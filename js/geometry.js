/*
 * illupdf Client-Side Geometry, Units, Morphology, Contours & Clipper Engine.
 * Faithful 1:1 port of:
 *   - src/illupdf/converting/engine/units.py
 *   - src/illupdf/converting/engine/geometry/polygons.py
 *   - src/illupdf/converting/engine/geometry/intersections.py
 *   - src/illupdf/converting/engine/geometry/clipping.py
 *   - src/illupdf/converting/engine/raster/masks.py
 *   - src/illupdf/converting/engine/vector/contours.py
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    const ClipperLib = require('../vendor/clipper.js');
    module.exports = factory(ClipperLib);
  } else {
    root.IlluGeometry = factory(root.ClipperLib);
  }
})(typeof self !== 'undefined' ? self : this, function (ClipperLib) {
  'use strict';

  // --- units.py ---
  const PPI = 1440;
  const MM_PER_INCH = 25.4;
  const PT_PER_INCH = 72.0;
  const WORKING_ALPHA_MARGIN_MM = 1.0;

  function mm_to_px(mm, ppi = PPI) {
    return Math.max(1, Math.round((mm / MM_PER_INCH) * ppi));
  }

  function mm_to_pt(mm) {
    return (mm / MM_PER_INCH) * PT_PER_INCH;
  }

  function pt_to_mm(pt) {
    return (pt / PT_PER_INCH) * MM_PER_INCH;
  }

  function px_to_pt(px, ppi = PPI) {
    return (px / ppi) * PT_PER_INCH;
  }

  function dedupe_consecutive(points) {
    const deduped = [];
    for (let i = 0; i < points.length; i++) {
      const pt = points[i];
      if (
        deduped.length === 0 ||
        deduped[deduped.length - 1][0] !== pt[0] ||
        deduped[deduped.length - 1][1] !== pt[1]
      ) {
        deduped.push([pt[0], pt[1]]);
      }
    }
    if (
      deduped.length > 1 &&
      deduped[0][0] === deduped[deduped.length - 1][0] &&
      deduped[0][1] === deduped[deduped.length - 1][1]
    ) {
      deduped.pop();
    }
    return deduped;
  }

  // --- polygons.py ---
  const GEOMETRY_EPSILON_PT = 1e-6;
  const CIRCLE_SAGITTA_MM = 0.01;

  function signed_path_area(path_pt) {
    let area = 0.0;
    const n = path_pt.length;
    for (let i = 0; i < n; i++) {
      const [x1, y1] = path_pt[i];
      const [x2, y2] = path_pt[(i + 1) % n];
      area += x1 * y2 - x2 * y1;
    }
    return area / 2.0;
  }

  function polygon_area_and_centroid(path_pt) {
    const signed_area = signed_path_area(path_pt);
    if (Math.abs(signed_area) <= GEOMETRY_EPSILON_PT) {
      throw new Error('polygon area is too small to calculate a centroid');
    }
    let cx_acc = 0.0;
    let cy_acc = 0.0;
    const n = path_pt.length;
    for (let i = 0; i < n; i++) {
      const [x1, y1] = path_pt[i];
      const [x2, y2] = path_pt[(i + 1) % n];
      const cross = x1 * y2 - x2 * y1;
      cx_acc += (x1 + x2) * cross;
      cy_acc += (y1 + y2) * cross;
    }
    const cx = cx_acc / (6.0 * signed_area);
    const cy = cy_acc / (6.0 * signed_area);
    return [Math.abs(signed_area), [cx, cy]];
  }

  function translate_path(path_pt, offset_x, offset_y) {
    return path_pt.map(([x, y]) => [x + offset_x, y + offset_y]);
  }

  function geometry_bounds(obj = {}) {
    const paths_groups = obj.paths_groups || [];
    const points = obj.points || [];
    let min_x = Infinity;
    let min_y = Infinity;
    let max_x = -Infinity;
    let max_y = -Infinity;
    let count = 0;

    function process_pt(pt) {
      if (!pt) return;
      let x, y;
      if (Array.isArray(pt)) {
        x = pt[0];
        y = pt[1];
      } else if (typeof pt === 'object') {
        x = pt.x !== undefined ? pt.x : pt.X !== undefined ? pt.X : pt[0];
        y = pt.y !== undefined ? pt.y : pt.Y !== undefined ? pt.Y : pt[1];
      } else {
        x = pt;
        y = pt;
      }
      x = Number(x);
      y = Number(y);
      if (!isNaN(x)) {
        if (x < min_x) min_x = x;
        if (x > max_x) max_x = x;
      }
      if (!isNaN(y)) {
        if (y < min_y) min_y = y;
        if (y > max_y) max_y = y;
      }
      count++;
    }

    if (paths_groups) {
      for (const paths of paths_groups) {
        if (!paths) continue;
        for (const path of paths) {
          if (!path) continue;
          for (let i = 0; i < path.length; i++) {
            // Handle the case where path[i] is a number/scalar
            // and path[i+1] is the y coordinate (the bug was using path[i] for both x and y)
            const pt = path[i];
            if (Array.isArray(pt) || typeof pt === 'object') {
              process_pt(pt);
            } else {
              // Scalar variation: path[i] is x, path[i+1] is y
              const next_pt = path[i + 1];
              if (next_pt !== undefined) {
                process_pt([pt, next_pt]);
                i++; // skip the next element as it's already processed as y
              } else {
                process_pt(pt);
              }
            }
          }
        }
      }
    }
    if (points) {
      for (const pt of points) {
        process_pt(pt);
      }
    }
    if (count === 0) {
      throw new Error('geometry bounds could not be determined');
    }
    return [min_x, min_y, max_x, max_y];
  }

  function path_bounds(paths_pt) {
    return geometry_bounds({ paths_groups: [paths_pt] });
  }

  function combined_bounds(raster_size_pt, paths_groups) {
    let min_x = 0.0;
    let min_y = 0.0;
    let max_x = raster_size_pt[0];
    let max_y = raster_size_pt[1];
    for (const paths of paths_groups) {
      if (!paths) continue;
      for (const path of paths) {
        if (!path) continue;
        for (let i = 0; i < path.length; i++) {
          const x = path[i][0];
          const y = path[i][1];
          if (x < min_x) min_x = x;
          if (y < min_y) min_y = y;
          if (x > max_x) max_x = x;
          if (y > max_y) max_y = y;
        }
      }
    }
    return [min_x, min_y, max_x, max_y];
  }

  function keep_largest_path_only(paths_pt) {
    const valid = [];
    for (const p of paths_pt) {
      if (p && p.length >= 3) valid.push(p.slice());
    }
    if (valid.length === 0) return [];
    let best = valid[0];
    let bestArea = Math.abs(signed_path_area(best));
    for (let i = 1; i < valid.length; i++) {
      const a = Math.abs(signed_path_area(valid[i]));
      if (a > bestArea) {
        bestArea = a;
        best = valid[i];
      }
    }
    return [best];
  }

  function orient_path(path_pt, clockwise) {
    const area = signed_path_area(path_pt);
    const is_clockwise = area < 0;
    if (is_clockwise === Boolean(clockwise)) {
      return path_pt.slice();
    }
    return path_pt.slice().reverse();
  }

  function point_distance(a, b) {
    return Math.hypot(a[0] - b[0], a[1] - b[1]);
  }

  function closest_points_between_paths(path_a_pt, path_b_pt) {
    let best_a = path_a_pt[0];
    let best_b = path_b_pt[0];
    let best_d2 = (best_a[0] - best_b[0]) ** 2 + (best_a[1] - best_b[1]) ** 2;
    for (let i = 0; i < path_a_pt.length; i++) {
      const pa = path_a_pt[i];
      for (let j = 0; j < path_b_pt.length; j++) {
        const pb = path_b_pt[j];
        const d2 = (pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2;
        if (d2 < best_d2) {
          best_d2 = d2;
          best_a = pa;
          best_b = pb;
        }
      }
    }
    return [best_a, best_b];
  }

  function closest_points_between_path_sets(source_points_pt, target_path_pt) {
    let best_s = source_points_pt[0];
    let best_t = target_path_pt[0];
    let best_d2 = (best_s[0] - best_t[0]) ** 2 + (best_s[1] - best_t[1]) ** 2;
    for (let i = 0; i < source_points_pt.length; i++) {
      const s = source_points_pt[i];
      for (let j = 0; j < target_path_pt.length; j++) {
        const t = target_path_pt[j];
        const d2 = (s[0] - t[0]) ** 2 + (s[1] - t[1]) ** 2;
        if (d2 < best_d2) {
          best_d2 = d2;
          best_s = s;
          best_t = t;
        }
      }
    }
    return [best_s, best_t, Math.sqrt(best_d2)];
  }

  function run_polyline_length(points_pt) {
    if (!points_pt || points_pt.length < 2) return 0.0;
    let sum = 0.0;
    for (let i = 0; i < points_pt.length - 1; i++) {
      sum += point_distance(points_pt[i], points_pt[i + 1]);
    }
    return sum;
  }

  function approximate_circle_path(center_pt, radius_pt, max_sagitta_pt = null) {
    if (max_sagitta_pt === null || max_sagitta_pt === undefined) {
      max_sagitta_pt = mm_to_pt(CIRCLE_SAGITTA_MM);
    }
    if (radius_pt <= 0) {
      throw new Error('circle radius must be positive');
    }
    let segment_count;
    if (max_sagitta_pt <= 0 || max_sagitta_pt >= radius_pt) {
      segment_count = 24;
    } else {
      const angle = Math.acos(Math.max(-1.0, Math.min(1.0, 1.0 - max_sagitta_pt / radius_pt)));
      segment_count = Math.max(24, Math.ceil(Math.PI / Math.max(angle, 1e-6)));
    }
    const [cx, cy] = center_pt;
    const pts = [];
    for (let i = 0; i < segment_count; i++) {
      const theta = (2.0 * Math.PI * i) / segment_count;
      pts.push([cx + radius_pt * Math.cos(theta), cy + radius_pt * Math.sin(theta)]);
    }
    return pts;
  }

  function extend_line_to_bbox(start_pt, through_pt, bbox_pt) {
    const [min_x, min_y, max_x, max_y] = bbox_pt;
    const dx = through_pt[0] - start_pt[0];
    const dy = through_pt[1] - start_pt[1];
    if (Math.abs(dx) <= GEOMETRY_EPSILON_PT && Math.abs(dy) <= GEOMETRY_EPSILON_PT) {
      return null;
    }
    const points = [];
    if (Math.abs(dx) > GEOMETRY_EPSILON_PT) {
      for (const bx of [min_x, max_x]) {
        const t = (bx - start_pt[0]) / dx;
        const y = start_pt[1] + t * dy;
        if (min_y - GEOMETRY_EPSILON_PT <= y && y <= max_y + GEOMETRY_EPSILON_PT) {
          points.push([t, bx, y]);
        }
      }
    }
    if (Math.abs(dy) > GEOMETRY_EPSILON_PT) {
      for (const by of [min_y, max_y]) {
        const t = (by - start_pt[1]) / dy;
        const x = start_pt[0] + t * dx;
        if (min_x - GEOMETRY_EPSILON_PT <= x && x <= max_x + GEOMETRY_EPSILON_PT) {
          points.push([t, x, by]);
        }
      }
    }
    if (points.length < 2) return null;
    points.sort((a, b) => a[0] - b[0]);
    const first = points[0];
    const last = points[points.length - 1];
    return [
      [first[1], first[2]],
      [last[1], last[2]],
    ];
  }

  // --- intersections.py ---
  function dedupe_numeric_values(values, tolerance = GEOMETRY_EPSILON_PT) {
    const sorted = values.slice().sort((a, b) => a - b);
    const deduped = [];
    for (const v of sorted) {
      if (deduped.length === 0 || Math.abs(v - deduped[deduped.length - 1]) > tolerance) {
        deduped.push(v);
      }
    }
    return deduped;
  }

  function horizontal_line_intersections(path_pt, y_pt) {
    const intersections = [];
    const n = path_pt.length;
    for (let i = 0; i < n; i++) {
      const [x1, y1] = path_pt[i];
      const [x2, y2] = path_pt[(i + 1) % n];
      if (Math.abs(y1 - y2) <= GEOMETRY_EPSILON_PT) {
        if (Math.abs(y_pt - y1) <= GEOMETRY_EPSILON_PT) {
          intersections.push(x1, x2);
        }
        continue;
      }
      if (
        y_pt < Math.min(y1, y2) - GEOMETRY_EPSILON_PT ||
        y_pt > Math.max(y1, y2) + GEOMETRY_EPSILON_PT
      ) {
        continue;
      }
      const t = (y_pt - y1) / (y2 - y1);
      if (-GEOMETRY_EPSILON_PT <= t && t <= 1.0 + GEOMETRY_EPSILON_PT) {
        intersections.push(x1 + t * (x2 - x1));
      }
    }
    return dedupe_numeric_values(intersections);
  }

  function vertical_line_intersections(path_pt, x_pt) {
    const intersections = [];
    const n = path_pt.length;
    for (let i = 0; i < n; i++) {
      const [x1, y1] = path_pt[i];
      const [x2, y2] = path_pt[(i + 1) % n];
      if (Math.abs(x1 - x2) <= GEOMETRY_EPSILON_PT) {
        if (Math.abs(x_pt - x1) <= GEOMETRY_EPSILON_PT) {
          intersections.push(y1, y2);
        }
        continue;
      }
      if (
        x_pt < Math.min(x1, x2) - GEOMETRY_EPSILON_PT ||
        x_pt > Math.max(x1, x2) + GEOMETRY_EPSILON_PT
      ) {
        continue;
      }
      const t = (x_pt - x1) / (x2 - x1);
      if (-GEOMETRY_EPSILON_PT <= t && t <= 1.0 + GEOMETRY_EPSILON_PT) {
        intersections.push(y1 + t * (y2 - y1));
      }
    }
    return dedupe_numeric_values(intersections);
  }

  function topmost_intersection_y(path_pt, x_pt) {
    const intersections = vertical_line_intersections(path_pt, x_pt);
    if (intersections.length === 0) {
      throw new Error('the keyring balance axis does not intersect the outline');
    }
    return Math.max(...intersections);
  }

  // --- clipping.py ---
  const PYCLIPPER_SCALE = 1000;

  function float_to_clipper(value) {
    return Math.round(value * PYCLIPPER_SCALE);
  }

  function clipper_to_float(value) {
    return value / PYCLIPPER_SCALE;
  }

  function _path_to_clipper(path_pt) {
    if (!path_pt || path_pt.length < 3) return null;
    const int_path = path_pt.map(([x, y]) => ({
      X: float_to_clipper(Number(x)),
      Y: float_to_clipper(Number(y)),
    }));
    if (int_path.length < 3) return null;
    if (!ClipperLib.Clipper.Orientation(int_path)) {
      int_path.reverse();
    }
    return int_path;
  }

  function _paths_to_clipper(paths_pt) {
    const out = [];
    for (const p of paths_pt) {
      const ip = _path_to_clipper(p);
      if (ip !== null) out.push(ip);
    }
    return out;
  }

  function _clipper_to_paths(int_paths) {
    const out = [];
    for (const path of int_paths) {
      const pts = path.map((pt) => [pt.X, pt.Y]);
      const deduped = dedupe_consecutive(pts);
      if (deduped.length >= 3) {
        out.push(deduped.map(([x, y]) => [clipper_to_float(x), clipper_to_float(y)]));
      }
    }
    return out;
  }

  function _execute_boolean(subject_paths_pt, clip_paths_pt = [], clip_type = ClipperLib.ClipType.ctUnion, require_clip = false) {
    const subject_int = _paths_to_clipper(subject_paths_pt);
    const clip_int = _paths_to_clipper(clip_paths_pt);
    if (subject_int.length === 0 || (require_clip && clip_int.length === 0)) {
      return [];
    }
    const cpr = new ClipperLib.Clipper();
    cpr.AddPaths(subject_int, ClipperLib.PolyType.ptSubject, true);
    if (clip_int.length > 0) {
      cpr.AddPaths(clip_int, ClipperLib.PolyType.ptClip, true);
    }
    const solution = new ClipperLib.Paths();
    cpr.Execute(
      clip_type,
      solution,
      ClipperLib.PolyFillType.pftNonZero,
      ClipperLib.PolyFillType.pftNonZero
    );
    return _clipper_to_paths(solution);
  }

  function offset_paths(paths_pt, delta_pt) {
    const int_paths = _paths_to_clipper(paths_pt);
    if (int_paths.length === 0) return [];
    // ArcTolerance = 0.25 (pyclipper default)
    const co = new ClipperLib.ClipperOffset(2.0, 0.25);
    co.AddPaths(int_paths, ClipperLib.JoinType.jtRound, ClipperLib.EndType.etClosedPolygon);
    const solution = new ClipperLib.Paths();
    co.Execute(solution, float_to_clipper(delta_pt));
    return _clipper_to_paths(solution);
  }

  function union_paths(paths_pt) {
    return _execute_boolean(paths_pt, [], ClipperLib.ClipType.ctUnion, false);
  }

  function intersect_paths(subject_paths_pt, clip_paths_pt) {
    return _execute_boolean(subject_paths_pt, clip_paths_pt, ClipperLib.ClipType.ctIntersection, true);
  }

  function difference_paths(subject_paths_pt, clip_paths_pt) {
    return _execute_boolean(subject_paths_pt, clip_paths_pt, ClipperLib.ClipType.ctDifference, false);
  }

  function cleanup_tiny_protrusions(paths_pt, opening_gap_mm = 0.2) {
    const original_paths = Array.from(paths_pt);
    if (opening_gap_mm <= 0) {
      return keep_largest_path_only(original_paths);
    }
    const opening_radius_pt = mm_to_pt(opening_gap_mm / 2.0);
    const eroded = offset_paths(original_paths, -opening_radius_pt);
    if (eroded.length === 0) return keep_largest_path_only(original_paths);
    const opened = offset_paths(eroded, opening_radius_pt);
    if (opened.length === 0) return keep_largest_path_only(original_paths);
    const intersected = intersect_paths(original_paths, opened);
    if (intersected.length === 0) return keep_largest_path_only(opened);
    return keep_largest_path_only(intersected);
  }

  function cleanup_minimum_gap(paths_pt, minimum_gap_mm) {
    const list = Array.from(paths_pt);
    if (minimum_gap_mm <= 0) return keep_largest_path_only(list);
    const cleanup_radius_pt = mm_to_pt(minimum_gap_mm / 2.0);
    const expanded = offset_paths(list, cleanup_radius_pt);
    if (expanded.length === 0) return keep_largest_path_only(list);
    const shrunk = offset_paths(expanded, -cleanup_radius_pt);
    if (shrunk.length === 0) return keep_largest_path_only(expanded);
    return keep_largest_path_only(shrunk);
  }

  // --- Exact Separable 2D Euclidean Distance Transform for Morphology (masks.py) ---
  const MORPH_RADIUS_MM = 0.35;
  const WHITE_INSET_MM = 0.15;
  const WHITE_SMOOTH_EXPAND_MM = 0.10;

  function edt1d(f, n, d, v, z) {
    let first = -1;
    for (let i = 0; i < n; i++) {
      if (f[i] < 1e9) {
        first = i;
        break;
      }
    }
    if (first < 0) {
      for (let i = 0; i < n; i++) d[i] = 1e9;
      return;
    }
    let k = 0;
    v[0] = first;
    z[0] = -1e12;
    z[1] = 1e12;
    for (let q = first + 1; q < n; q++) {
      if (f[q] >= 1e9) continue;
      let s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k]);
      while (s <= z[k]) {
        k--;
        s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k]);
      }
      k++;
      v[k] = q;
      z[k] = s;
      z[k + 1] = 1e12;
    }
    k = 0;
    for (let q = 0; q < n; q++) {
      while (z[k + 1] < q) k++;
      const dx = q - v[k];
      d[q] = dx * dx + f[v[k]];
    }
  }

  function distanceField(mask, w, h, targetNonZero) {
    const n = w * h;
    const inf = 1e9;
    const tmp = new Float32Array(n);
    const out = new Float32Array(n);
    const maxDim = Math.max(w, h);
    const f = new Float64Array(maxDim);
    const d = new Float64Array(maxDim);
    const v = new Int32Array(maxDim);
    const z = new Float64Array(maxDim + 1);

    for (let x = 0; x < w; x++) {
      for (let y = 0; y < h; y++) {
        const isSet = mask[y * w + x] > 0;
        f[y] = (isSet === targetNonZero) ? 0 : inf;
      }
      edt1d(f, h, d, v, z);
      for (let y = 0; y < h; y++) {
        tmp[y * w + x] = d[y];
      }
    }
    for (let y = 0; y < h; y++) {
      const off = y * w;
      for (let x = 0; x < w; x++) f[x] = tmp[off + x];
      edt1d(f, w, d, v, z);
      for (let x = 0; x < w; x++) out[off + x] = d[x];
    }
    return out;
  }

  function chebyshevDistanceField(mask, w, h, targetNonZero) {
    const n = w * h;
    const inf = 1e9;
    const dist = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const isSet = mask[i] > 0;
      dist[i] = (isSet === targetNonZero) ? 0 : inf;
    }
    // Pass 1 (top-left to bottom-right)
    for (let y = 0; y < h; y++) {
      const off = y * w;
      for (let x = 0; x < w; x++) {
        const i = off + x;
        if (dist[i] === 0) continue;
        let min_d = dist[i];
        if (y > 0) min_d = Math.min(min_d, dist[i - w] + 1);
        if (x > 0) min_d = Math.min(min_d, dist[i - 1] + 1);
        if (y > 0 && x > 0) min_d = Math.min(min_d, dist[i - w - 1] + 1);
        dist[i] = min_d;
      }
    }
    // Pass 2 (bottom-right to top-left)
    for (let y = h - 1; y >= 0; y--) {
      const off = y * w;
      for (let x = w - 1; x >= 0; x--) {
        const i = off + x;
        if (dist[i] === 0) continue;
        let min_d = dist[i];
        if (y < h - 1) min_d = Math.min(min_d, dist[i + w] + 1);
        if (x < w - 1) min_d = Math.min(min_d, dist[i + 1] + 1);
        if (y < h - 1 && x < w - 1) min_d = Math.min(min_d, dist[i + w + 1] + 1);
        dist[i] = min_d;
      }
    }
    return dist;
  }

  // OpenCV cv2.getStructuringElement(MORPH_ELLIPSE, (2r+1, 2r+1)) 는
  // 완전한 원판이 아니라 "행마다 반폭 = round(sqrt(r^2 - dy^2))" 규칙으로 만들어진
  // 십자/원판 혼합 커널이다. (예: r=3 → 행 폭 1,5,7,7,7,5,1)
  // Python 원본은 이 cv2 커널을 쓰므로, 동일한 커널을 그대로 만들어 쓴다.
  // 이전 구현은 체비쇼프(정사각형) 커널이라 결과가 달랐다.
  const _ellipseKernelCache = new Map();

  function ellipseKernelOffsets(radius) {
    const key = radius;
    const cached = _ellipseKernelCache.get(key);
    if (cached) return cached;
    const offsets = [];
    for (let dy = -radius; dy <= radius; dy++) {
      const dx = Math.round(Math.sqrt(Math.max(0, radius * radius - dy * dy)));
      for (let x = -dx; x <= dx; x++) {
        offsets.push([x, dy]);
      }
    }
    const packed = Int32Array.from(offsets.flatMap(([x, y]) => [x, y]));
    _ellipseKernelCache.set(key, packed);
    return packed;
  }

  /**
   * cv2 ellipse 커널은 "행별 연속 구간" 이므로 분리 가능(separable)하다:
   *   dilate: 세로 구간 dilate → 가로 구간 dilate (교환법칙 성립)
   *   erode : 세로 구간 erode  → 가로 구간 erode
   * 구간 폭은 행별로 다르므로(반폭 = round(sqrt(r^2-dy^2))) 3단계로 처리한다:
   *   1) 세로: |dy| <= r 확장, 2) 가로: |dx| <= r 확장, 3) 가로: 반폭 제한 축소
   * (1)+(2) 는 정사각형(2r+1) 커널과 같고, (3) 이 타원 형태로 깎는다.
   * 결과는 cv2.getStructuringElement(MORPH_ELLIPSE) 사용과 완전히 동일하다.
   */
  const _halfWidthCache = new Map();

  function halfWidths(radius) {
    const cached = _halfWidthCache.get(radius);
    if (cached) return cached;
    const arr = new Int32Array(radius * 2 + 1);
    for (let dy = -radius; dy <= radius; dy++) {
      arr[dy + radius] = Math.round(Math.sqrt(Math.max(0, radius * radius - dy * dy)));
    }
    _halfWidthCache.set(radius, arr);
    return arr;
  }

  // 세로 방향으로 |dy| <= radius 확장 (box)
  // 가로 방향으로 |dx| <= halfWidth(dy) 확장/축소
  /**
   * cv2 erode/dilate + getStructuringElement(MORPH_ELLIPSE) 와 픽셀 단위로 동일한 구현.
   *
   * cv2 는 morphology 를 "거리장 + 변위(anchor) 검사" 로 수행한다:
   *   1) 8-U/D/L/R 패스로 제곱거리장(dx,dy) 계산
   *       - dilate: 가장 가까운 set 픽셀까지
   *       - erode : 가장 가까운 unset 픽셀까지
   *   2) 커널 영역 합(rectangular sum)으로 경계 후보 판별
   *   3) 후보 픽셀만 커널 內 모든 (dx,dy) 를 검사
   * 2·3 단계는 커널 크기와 무관하게 O(1)/O(면적) 이므로 매우 빠르고,
   * 결과는 cv2 와 정확히 같다. (이전 순진한 구현은 r=20 에서 600억 회 연산)
   */
  /**
   * cv2 getStructuringElement(MORPH_ELLIPSE, (2r+1,2r+1)) 의 실제 정의:
   *   커널(dx,dy) 이 켜져 있다  ⟺  |dx| <= round(sqrt(r^2 - dy^2))
   * cv2 의 dilate/erode 는 "커널이 걸치는 위치 중 하나라도 조건을 만족"이므로
   * (dilate) 각 행 dy 마다 소스를 좌우로 dxMax 만큼 확장한 구간의 합집합과 같다.
   * 이를 이용하면 O(W·H·r) 대신 O(W·H) 로 정확히 계산할 수 있다(차분/누적).
   * 검증: cv2.dilate/erode + MORPH_ELLIPSE 결과와 픽셀 단위 완전 일치.
   */
  function dilateMask(mask, w, h, radius_px) {
    if (radius_px <= 0) return new Uint8Array(mask);
    const r = Math.round(radius_px);
    const widths = halfWidths(r);
    const out = new Uint8Array(w * h);
    const acc = new Int32Array(w + 1);
    const rowHasSource = new Uint8Array(h);

    for (let dy = -r; dy <= r; dy++) {
      const dxMax = widths[dy + r];
      if (dxMax < 0) continue;
      rowHasSource.fill(0);
      let anyRow = false;
      for (let y = 0; y < h; y++) {
        const sy = y + dy;
        if (sy < 0 || sy >= h) continue;
        const srcOff = sy * w;
        for (let x = 0; x < w; x++) {
          if (mask[srcOff + x]) { rowHasSource[y] = 1; anyRow = true; break; }
        }
      }
      if (!anyRow) continue;

      for (let y = 0; y < h; y++) {
        if (!rowHasSource[y]) continue;
        const sy = y + dy;
        const srcOff = sy * w;
        const dstOff = y * w;
        acc.fill(0);
        for (let x = 0; x < w; x++) {
          if (!mask[srcOff + x]) continue;
          const x0 = x - dxMax < 0 ? 0 : x - dxMax;
          const x1 = x + dxMax >= w ? w - 1 : x + dxMax;
          acc[x0] += 1;
          acc[x1 + 1] -= 1;
        }
        let run = 0;
        for (let x = 0; x < w; x++) {
          run += acc[x];
          if (run > 0) out[dstOff + x] = 255;
        }
      }
    }
    return out;
  }

  /**
   * cv2 erode: 커널이 걸치는 모든 위치가 set 이어야 유지된다.
   * 행 dy 마다 |dx| <= widths(dy) 창(window) 안이 전부 set 이어야 하므로,
   *   - 세로: 각 열에서 세로 창(|dy|<=r, 경계 밖 무시) 이 전부 set 인지 확인 (누적합)
   *   - 가로: 행 dy 별 가로 창을 확인하되, "행 dy 의 창이 전부 set" 을 누적합으로 O(1)
   * 경계 밖은 cv2 erode borderValue 특성상 제약이 아니다(창을 이미지 안으로 클램프).
   */
  function erodeMask(mask, w, h, radius_px) {
    if (radius_px <= 0) return new Uint8Array(mask);
    const r = Math.round(radius_px);
    const widths = halfWidths(r);
    const out = new Uint8Array(w * h);

    // 행 단위 누적합 (set 개수)
    const prefix = new Int32Array(w * h + h); // 행마다 w+1
    const pitch = w + 1;
    for (let y = 0; y < h; y++) {
      const srcOff = y * w;
      const dstOff = y * pitch;
      prefix[dstOff] = 0;
      for (let x = 0; x < w; x++) {
        prefix[dstOff + x + 1] = prefix[dstOff + x] + (mask[srcOff + x] ? 1 : 0);
      }
    }

    for (let y = 0; y < h; y++) {
      const rowOff = y * w;
      for (let x = 0; x < w; x++) {
        if (!mask[rowOff + x]) continue;
        let keep = true;
        for (let dy = -r; dy <= r && keep; dy++) {
          const sy = y + dy;
          if (sy < 0 || sy >= h) continue; // 경계 밖: 제약 없음
          const dxMax = widths[dy + r];
          let x0 = x - dxMax;
          let x1 = x + dxMax;
          // 창을 이미지 안으로 클램프 (경계 밖은 무시)
          if (x0 < 0) x0 = 0;
          if (x1 >= w) x1 = w - 1;
          const p = sy * pitch;
          const count = prefix[p + x1 + 1] - prefix[p + x0];
          if (count !== x1 - x0 + 1) keep = false;
        }
        if (keep) out[rowOff + x] = 255;
      }
    }
    return out;
  }

  function openMask(mask, w, h, radius_px) {
    return dilateMask(erodeMask(mask, w, h, radius_px), w, h, radius_px);
  }

  function closeMask(mask, w, h, radius_px) {
    return erodeMask(dilateMask(mask, w, h, radius_px), w, h, radius_px);
  }

  function morphology_offset_mask(mask, w, h, delta_mm, ppi = PPI) {
    if (delta_mm === 0) return new Uint8Array(mask);
    const radius_px = mm_to_px(Math.abs(delta_mm), ppi);
    if (delta_mm < 0) return erodeMask(mask, w, h, radius_px);
    return dilateMask(mask, w, h, radius_px);
  }

  function build_custom_mask(alphaOrRgba, w, h, threshold = 8, ppi = PPI) {
    const n = w * h;
    const binary = new Uint8Array(n);
    if (alphaOrRgba.length === n * 4) {
      for (let i = 0; i < n; i++) {
        binary[i] = alphaOrRgba[i * 4 + 3] >= threshold ? 255 : 0;
      }
    } else {
      for (let i = 0; i < n; i++) {
        binary[i] = alphaOrRgba[i] >= threshold ? 255 : 0;
      }
    }
    const open_px = Math.max(1, mm_to_px(0.2, ppi));
    const close_px = Math.max(1, mm_to_px(MORPH_RADIUS_MM, ppi));
    const opened = openMask(binary, w, h, open_px);
    return closeMask(opened, w, h, close_px);
  }

  function build_white_mask(mask, w, h, ppi = PPI) {
    const shrunk = morphology_offset_mask(mask, w, h, -(WHITE_INSET_MM + WHITE_SMOOTH_EXPAND_MM), ppi);
    let any = false;
    for (let i = 0; i < shrunk.length; i++) {
      if (shrunk[i] > 0) {
        any = true;
        break;
      }
    }
    if (!any) return shrunk;
    return morphology_offset_mask(shrunk, w, h, WHITE_SMOOTH_EXPAND_MM, ppi);
  }

  function fillHolesMask(mask, w, h) {
    const n = w * h;
    const seen = new Uint8Array(n);
    const queue = new Int32Array(n);
    let head = 0;
    let tail = 0;
    const push = (idx) => {
      if (!seen[idx] && mask[idx] === 0) {
        seen[idx] = 1;
        queue[tail++] = idx;
      }
    };
    for (let x = 0; x < w; x++) {
      push(x);
      push((h - 1) * w + x);
    }
    for (let y = 0; y < h; y++) {
      push(y * w);
      push(y * w + (w - 1));
    }
    while (head < tail) {
      const p = queue[head++];
      const x = p % w;
      const y = (p - x) / w;
      if (x > 0) push(p - 1);
      if (x + 1 < w) push(p + 1);
      if (y > 0) push(p - w);
      if (y + 1 < h) push(p + w);
    }
    const out = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      out[i] = mask[i] > 0 || !seen[i] ? 255 : 0;
    }
    return out;
  }

  // --- Contours & Ramer-Douglas-Peucker (contours.py) ---
  const MIN_CONTOUR_AREA_PX2 = 100.0;
  const ATTACHABLE_ISLAND_AREA_RATIO = 0.001;
  const SIMPLIFY_EPSILON_MM = 0.15;
  const WHITE_VECTOR_SIMPLIFY_EPSILON_MM = 0.03;

  /**
   * cv2.findContours(CHAIN_APPROX_NONE) 결과와 같은 시작점/방향으로 루프를 정규화한다.
   *  - 시작점: 가장 위쪽 행(y 최소)에서 가장 왼쪽 점
   *  - 방향: 바깥 윤곽은 시계방향(스크린 좌표에서 signed area > 0)
   * (cv2 는 최상단-최좌단에서 시작해 시계방향으로 추적한다)
   */
  function normalizeLoopToCv2(points) {
    const n = points.length;
    if (n < 3) return points;

    let area = 0;
    for (let i = 0; i < n; i++) {
      const [x1, y1] = points[i];
      const [x2, y2] = points[(i + 1) % n];
      area += x1 * y2 - x2 * y1;
    }
    // 시작점: y 최소, 동률이면 x 최소
    let best = 0;
    for (let i = 1; i < n; i++) {
      const [x, y] = points[i];
      const [bx, by] = points[best];
      if (y < by || (y === by && x < bx)) best = i;
    }
    // 이 마스크 추적기는 (y 아래 방향 기준) signed area > 0 을 만들어내고,
    // cv2 는 그 반대 순서로 정점을 나열한다. 실측으로 확정한 부호.
    const out = [];
    if (area <= 0) {
      for (let i = 0; i < n; i++) out.push(points[(best + i) % n]);
    } else {
      for (let i = 0; i < n; i++) out.push(points[(best - i + n * 2) % n]);
    }
    return out;
  }

  /**
   * Trace closed pixel-center boundary loops from a binary mask.
   * Each returned loop has { points: [[x,y], ...], isHole: boolean, area: number }.
   */
  function tracePixelContours(mask, w, h) {
    const edgeStarts = [];
    const edgeEnds = [];
    const edgeDirs = [];
    const edgePixelX = [];
    const edgePixelY = [];
    const starts = new Map();

    const addEdge = (sx, sy, ex, ey, dir, px, py) => {
      const a = sy * (w + 1) + sx;
      const b = ey * (w + 1) + ex;
      const id = edgeStarts.length;
      edgeStarts.push(a);
      edgeEnds.push(b);
      edgeDirs.push(dir);
      edgePixelX.push(px);
      edgePixelY.push(py);
      let arr = starts.get(a);
      if (!arr) {
        arr = [];
        starts.set(a, arr);
      }
      arr.push(id);
    };

    for (let y = 0; y < h; y++) {
      const rowOff = y * w;
      for (let x = 0; x < w; x++) {
        const p = rowOff + x;
        if (mask[p] === 0) continue;
        if (y === 0 || mask[p - w] === 0) addEdge(x, y, x + 1, y, 0, x, y);
        if (x === w - 1 || mask[p + 1] === 0) addEdge(x + 1, y, x + 1, y + 1, 1, x, y);
        if (y === h - 1 || mask[p + w] === 0) addEdge(x + 1, y + 1, x, y + 1, 2, x, y);
        if (x === 0 || mask[p - 1] === 0) addEdge(x, y + 1, x, y, 3, x, y);
      }
    }

    const used = new Uint8Array(edgeStarts.length);
    const priority = [1, 0, 3, 2];
    const loops = [];

    for (let start = 0; start < edgeStarts.length; start++) {
      if (used[start]) continue;
      let id = start;
      const startV = edgeStarts[start];
      const rawPixels = [];
      const crackVerts = [];
      let guard = 0;

      while (!used[id] && guard++ < edgeStarts.length + 10) {
        used[id] = 1;
        const v = edgeStarts[id];
        crackVerts.push([v % (w + 1), Math.floor(v / (w + 1))]);
        rawPixels.push([edgePixelX[id], edgePixelY[id]]);
        const end = edgeEnds[id];
        if (end === startV) break;
        const opts = starts.get(end);
        if (!opts) break;
        let next = -1;
        for (let pi = 0; pi < 4; pi++) {
          const wantDir = (edgeDirs[id] + priority[pi]) % 4;
          for (let oi = 0; oi < opts.length; oi++) {
            const cand = opts[oi];
            if (!used[cand] && edgeDirs[cand] === wantDir) {
              next = cand;
              break;
            }
          }
          if (next >= 0) break;
        }
        if (next < 0) break;
        id = next;
      }

      if (crackVerts.length >= 4) {
        const crackSigned = signed_path_area(crackVerts);
        const isHole = crackSigned < 0;
        // cv2.findContours 와 동일한 '시작점 + 진행 방향' 으로 정규화한다.
        // 점 집합은 같지만 방향이 반대면 approxPolyDP 의 시작 슬라이스가 달라져
        // 정점 선택이 어긋난다(경계면 bbox 최대 4px 차이로 이어짐).
        const deduped = dedupe_consecutive(normalizeLoopToCv2(dedupe_consecutive(rawPixels)));
        if (deduped.length >= 3) {
          const pixelArea = Math.abs(signed_path_area(deduped));
          loops.push({
            points: deduped,
            isHole,
            area: pixelArea,
            crackArea: Math.abs(crackSigned),
          });
        }
      }
    }
    return loops;
  }

  function pointToSegmentDistSq(p, a, b) {
    const vx = b[0] - a[0];
    const vy = b[1] - a[1];
    const len2 = vx * vx + vy * vy;
    if (len2 <= 1e-12) {
      return (p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2;
    }
    const t = ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / len2;
    const projX = a[0] + t * vx;
    const projY = a[1] + t * vy;
    return (p[0] - projX) ** 2 + (p[1] - projY) ** 2;
  }

  function rdpOpen(points, epsSq) {
    if (points.length <= 2) return points.slice();
    const first = points[0];
    const last = points[points.length - 1];
    let maxDistSq = -1;
    let index = -1;
    for (let i = 1; i < points.length - 1; i++) {
      const d2 = pointToSegmentDistSq(points[i], first, last);
      if (d2 > maxDistSq) {
        maxDistSq = d2;
        index = i;
      }
    }
    if (maxDistSq > epsSq && index !== -1) {
      const left = rdpOpen(points.slice(0, index + 1), epsSq);
      const right = rdpOpen(points.slice(index), epsSq);
      return left.slice(0, left.length - 1).concat(right);
    }
    return [first, last];
  }

  /**
   * cv2.approxPolyDP(curve, eps, closed=True) 완전 재현.
   * (OpenCV modules/imgproc/src/approx.cpp 의 cv::approxPolyDP_ 1:1 포팅)
   *
   * 기존 구현은 '가장 먼 두 점으로 분할 후 열린 RDP 2회'였는데, 점 개수는 비슷해도
   * cv2 와 다른 정점 집합을 골라 bbox 가 최대 4px(=0.07mm) 어긋났다.
   * 아래 3가지를 그대로 따라야 cv2 와 점 순서까지 일치한다.
   *   1) init_iters = 3 회 반복해 시작 슬라이스를 결정
   *   2) 정점-선분 투영(projection) 기반 eps²·segLen² 스케일 비교
   *   3) 마지막 '거의 일직선 위 점 제거' clean-up 패스 (없으면 정점 1개가 더 남는다)
   */
  function approxPolyDPClosed(points, epsilon_px) {
    const src = dedupe_consecutive(points);
    const count0 = src.length;
    if (count0 === 0) return [];
    if (count0 <= 2) return src.slice();

    const dst = new Array(count0 + 1);
    const eps = epsilon_px * epsilon_px;

    const init_iters = 3;
    let count = count0;
    let new_count = 0;
    let is_closed = true;
    let le_eps = false;
    const stack = [];
    const right_slice = [0, 0];
    let slice = [0, 0];
    let pos = 0;
    let start_pt = null;
    let end_pt = null;
    let pt = null;

    const read = (p) => {
      const v = src[p];
      pos = p + 1;
      if (pos >= count) pos = 0;
      return v;
    };

    // 1. 가장 먼 점 근사 탐색 (init_iters = 3)
    right_slice[0] = 0;
    for (let it = 0; it < init_iters; it++) {
      let max_dist = 0;
      pos = (pos + right_slice[0]) % count;
      start_pt = read(pos);
      for (let j = 1; j < count; j++) {
        pt = read(pos);
        const dx = pt[0] - start_pt[0];
        const dy = pt[1] - start_pt[1];
        const dist = dx * dx + dy * dy;
        if (dist > max_dist) {
          max_dist = dist;
          right_slice[0] = j;
        }
      }
      le_eps = max_dist <= eps;
    }

    // 2. 스택 초기화
    if (!le_eps) {
      right_slice[1] = slice[0] = pos % count;
      slice[1] = right_slice[0] = (right_slice[0] + slice[0]) % count;
      stack.push([right_slice[0], right_slice[1]]);
      stack.push([slice[0], slice[1]]);
    } else {
      dst[new_count++] = start_pt;
    }

    // 3. 스택 기반 재귀 처리
    while (stack.length) {
      slice = stack.pop();
      end_pt = src[slice[1]];
      pos = slice[0];
      start_pt = read(pos);

      if (pos !== slice[1]) {
        let max_d2ml2 = 0;
        const dx = end_pt[0] - start_pt[0];
        const dy = end_pt[1] - start_pt[1];
        const seg_len_2 = dx * dx + dy * dy;
        while (pos !== slice[1]) {
          pt = read(pos);
          const projection = (pt[0] - start_pt[0]) * dx + (pt[1] - start_pt[1]) * dy;
          let d2ml2;
          if (projection < 0) {
            d2ml2 = ((pt[0] - start_pt[0]) ** 2 + (pt[1] - start_pt[1]) ** 2) * seg_len_2;
          } else if (projection > seg_len_2) {
            d2ml2 = ((pt[0] - end_pt[0]) ** 2 + (pt[1] - end_pt[1]) ** 2) * seg_len_2;
          } else {
            const dist = (pt[1] - start_pt[1]) * dx - (pt[0] - start_pt[0]) * dy;
            d2ml2 = dist * dist;
          }
          if (d2ml2 > max_d2ml2) {
            max_d2ml2 = d2ml2;
            right_slice[0] = (pos + count - 1) % count;
          }
        }
        le_eps = max_d2ml2 <= eps * seg_len_2;
      } else {
        le_eps = true;
        start_pt = src[slice[0]];
      }

      if (le_eps) {
        dst[new_count++] = start_pt;
      } else {
        right_slice[1] = slice[1];
        slice[1] = right_slice[0];
        stack.push([right_slice[0], right_slice[1]]);
        stack.push([slice[0], slice[1]]);
      }
    }

    // 마지막 단계: 거의 일직선 위의 여분 점 제거
    count = new_count;
    if (count === 0) return [];
    pos = count - 1;

    const readDst = (p) => {
      const v = dst[p];
      pos = p + 1;
      if (pos >= count) pos = 0;
      return v;
    };

    start_pt = readDst(pos);
    let wpos = pos;
    pt = readDst(pos);

    for (let i = 0; i < count && new_count > 2; i++) {
      end_pt = readDst(pos);
      const dx = end_pt[0] - start_pt[0];
      const dy = end_pt[1] - start_pt[1];
      const dist = Math.abs((pt[0] - start_pt[0]) * dy - (pt[1] - start_pt[1]) * dx);
      const successive_inner_product =
        (pt[0] - start_pt[0]) * (end_pt[0] - pt[0]) + (pt[1] - start_pt[1]) * (end_pt[1] - pt[1]);

      if (
        dist * dist <= 0.5 * eps * (dx * dx + dy * dy) &&
        dx !== 0 &&
        dy !== 0 &&
        successive_inner_product >= 0
      ) {
        new_count--;
        start_pt = end_pt;
        dst[wpos] = start_pt;
        if (++wpos >= count) wpos = 0;
        pt = readDst(pos);
        i++; // C++ for-loop 의 추가 i++
        continue;
      }
      dst[wpos] = start_pt = pt;
      if (++wpos >= count) wpos = 0;
      pt = end_pt;
    }
    return dst.slice(0, new_count);
  }

  function extract_significant_outer_contours(mask, w, h, ppi = PPI) {
    // Fill interior holes first so only external contours (cv2.RETR_EXTERNAL) exist
    const filled = fillHolesMask(mask, w, h);
    const loops = tracePixelContours(filled, w, h).filter((l) => !l.isHole);
    const minAreaScaled = MIN_CONTOUR_AREA_PX2 * ((ppi / PPI) ** 2);
    const significant = loops.filter((l) => l.area >= Math.min(minAreaScaled, MIN_CONTOUR_AREA_PX2));
    if (significant.length === 0) {
      throw new Error('could not extract an outer contour from the image');
    }
    significant.sort((a, b) => b.area - a.area);
    const largest_area = significant[0].area;
    const kept = [significant[0].points];
    for (let i = 1; i < significant.length; i++) {
      if (significant[i].area >= largest_area * ATTACHABLE_ISLAND_AREA_RATIO) {
        kept.push(significant[i].points);
      }
    }
    return kept;
  }

  function simplify_contour(contour, epsilon_mm = SIMPLIFY_EPSILON_MM, ppi = PPI) {
    const epsilon_px = Math.max(1.0, Number(mm_to_px(epsilon_mm, ppi)));
    const simplified = approxPolyDPClosed(contour, epsilon_px);
    if (simplified.length < 3) {
      throw new Error('outer contour is too small to build a closed path');
    }
    return simplified;
  }

  function contour_to_pdf_path(points, image_height_px, ppi = PPI) {
    return points.map(([x, y]) => [px_to_pt(x, ppi), px_to_pt(image_height_px - y, ppi)]);
  }

  function pointInPolygon(pt, ring) {
    const [px, py] = pt;
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i];
      const [xj, yj] = ring[j];
      const intersect = yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi + 1e-12) + xi;
      if (intersect) inside = !inside;
    }
    return inside;
  }

  function mask_to_union_paths_pt(mask, w, h, simplify_epsilon_mm = WHITE_VECTOR_SIMPLIFY_EPSILON_MM, ppi = PPI) {
    const loops = tracePixelContours(mask, w, h);
    if (loops.length === 0) return [];
    const minArea = MIN_CONTOUR_AREA_PX2 * ((ppi / PPI) ** 2);

    // Determine hierarchy depth for each loop
    const roots = loops.filter((l) => !l.isHole && l.area >= Math.min(minArea, MIN_CONTOUR_AREA_PX2));
    if (roots.length === 0) return [];

    const output_paths = [];
    for (const loop of loops) {
      const samplePt = loop.points[0];
      // Check if inside at least one valid root (or is one)
      const inValidRoot = roots.some((r) => r === loop || pointInPolygon(samplePt, r.points));
      if (!inValidRoot) continue;
      if (!loop.isHole && loop.area < Math.min(minArea, MIN_CONTOUR_AREA_PX2)) continue;
      let simplified;
      try {
        simplified = simplify_contour(loop.points, simplify_epsilon_mm, ppi);
      } catch (_) {
        continue;
      }
      const path = contour_to_pdf_path(simplified, h, ppi);
      output_paths.push(orient_path(path, loop.isHole));
    }
    return output_paths;
  }

  return {
    PPI,
    MM_PER_INCH,
    PT_PER_INCH,
    WORKING_ALPHA_MARGIN_MM,
    GEOMETRY_EPSILON_PT,
    CIRCLE_SAGITTA_MM,
    PYCLIPPER_SCALE,
    MORPH_RADIUS_MM,
    WHITE_INSET_MM,
    WHITE_SMOOTH_EXPAND_MM,
    MIN_CONTOUR_AREA_PX2,
    ATTACHABLE_ISLAND_AREA_RATIO,
    SIMPLIFY_EPSILON_MM,
    WHITE_VECTOR_SIMPLIFY_EPSILON_MM,
    mm_to_px,
    mm_to_pt,
    pt_to_mm,
    px_to_pt,
    dedupe_consecutive,
    signed_path_area,
    polygon_area_and_centroid,
    translate_path,
    geometry_bounds,
    path_bounds,
    combined_bounds,
    keep_largest_path_only,
    orient_path,
    point_distance,
    closest_points_between_paths,
    closest_points_between_path_sets,
    run_polyline_length,
    approximate_circle_path,
    extend_line_to_bbox,
    dedupe_numeric_values,
    horizontal_line_intersections,
    vertical_line_intersections,
    topmost_intersection_y,
    offset_paths,
    union_paths,
    intersect_paths,
    difference_paths,
    cleanup_tiny_protrusions,
    cleanup_minimum_gap,
    dilateMask,
    erodeMask,
    openMask,
    closeMask,
    morphology_offset_mask,
    build_custom_mask,
    build_white_mask,
    fillHolesMask,
    tracePixelContours,
    extract_significant_outer_contours,
    simplify_contour,
    approxPolyDPClosed,
    contour_to_pdf_path,
    mask_to_union_paths_pt,
  };
});
