/*
 * illupdf Client-Side Keyring Engine (Port of src/illupdf/converting/engine/keyring/)
 * Includes: Keyring Balance, Reinforcement, Hole Resolution, Bridge Shapes.
 * Depends on: IlluGeometry (js/geometry.js)
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    const IlluGeometry = require('./geometry.js');
    module.exports = factory(IlluGeometry);
  } else {
    root.IlluKeyringEngine = factory(root.IlluGeometry);
  }
})(typeof self !== 'undefined' ? self : this, function (IlluGeometry) {
  'use strict';

  const {
    GEOMETRY_EPSILON_PT,
    mm_to_pt,
    pt_to_mm,
    geometry_bounds,
    signed_path_area,
    polygon_area_and_centroid,
    topmost_intersection_y,
    horizontal_line_intersections,
    closest_points_between_paths,
    closest_points_between_path_sets,
    run_polyline_length,
    approximate_circle_path,
    union_paths,
    cleanup_minimum_gap,
    keep_largest_path_only,
    offset_paths,
    extend_line_to_bbox,
  } = IlluGeometry;

  // --- Keyring Constants ---
  const KEYRING_HOLE_RADIUS_MM = 1.5;
  const KEYRING_BOSS_SLIDE_STEP_MM = 0.1;
  const KEYRING_SIDE_CLEARANCE_THRESHOLD_MM = 0.75;
  const INTERNAL_ITERATION_GUARD = 4096;
  const KEYRING_BOSS_RADIUS_MM = 3.5;
  const KEYRING_NECK_WIDTH_MM = 8.0;
  const KEYRING_BOSS_MINIMUM_GAP_MM = 5.0;
  const KEYRING_BOSS_GAP_MIN_DEPTH_MM = 1.0;
  const KEYRING_BOSS_GAP_MIN_SPAN_MM = 1.5;
  const ISLAND_ATTACHMENT_WIDTH_MM = 3.5;
  const MINIMUM_CUTTABLE_GAP_MM = 1.0;
  const OUTLINE_EXPAND_MM = 5.0;
  const OUTLINE_SHRINK_MM = 3.0;

  // --- Models ---

  class KeyringCandidate {
    constructor({ rx_pt, error_x_pt, theta_rad, outer_path_pt, hole_path_pt, boss_path_pt, hole_center_pt, centroid_pt, outer_centroid_pt }) {
      this.rx_pt = rx_pt;
      this.error_x_pt = error_x_pt;
      this.theta_rad = theta_rad;
      this.outer_path_pt = outer_path_pt;
      this.hole_path_pt = hole_path_pt;
      this.boss_path_pt = boss_path_pt;
      this.hole_center_pt = hole_center_pt;
      this.centroid_pt = centroid_pt;
      this.outer_centroid_pt = outer_centroid_pt;
    }
  }

  class KeyringBalanceResult {
    constructor({ outer_path_pt, hole_path_pt, boss_path_pt, hole_center_pt, centroid_pt, theta_rad, iterations }) {
      this.outer_path_pt = outer_path_pt;
      this.hole_path_pt = hole_path_pt;
      this.boss_path_pt = boss_path_pt;
      this.hole_center_pt = hole_center_pt;
      this.centroid_pt = centroid_pt;
      this.theta_rad = theta_rad;
      this.iterations = iterations;
    }
  }

  // --- Keyring Balance & Reinforcement ---

  function keyring_side_clearances(base_outer_path_pt, center_pt) {
    const [center_x_pt, center_y_pt] = center_pt;
    const intersections = horizontal_line_intersections(base_outer_path_pt, center_y_pt);
    const left_distances = intersections.filter(ix => ix < center_x_pt - GEOMETRY_EPSILON_PT).map(ix => center_x_pt - ix);
    const right_distances = intersections.filter(ix => ix > center_x_pt + GEOMETRY_EPSILON_PT).map(ix => ix - center_x_pt);
    const left_clearance_pt = left_distances.length > 0 ? Math.min(...left_distances) : null;
    const right_clearance_pt = right_distances.length > 0 ? Math.min(...right_distances) : null;
    return [left_clearance_pt, right_clearance_pt];
  }

  function side_clearance_is_tight(base_outer_path_pt, center_pt, boss_radius_pt) {
    const threshold_pt = boss_radius_pt + mm_to_pt(KEYRING_SIDE_CLEARANCE_THRESHOLD_MM);
    const [left_clearance_pt, right_clearance_pt] = keyring_side_clearances(base_outer_path_pt, center_pt);
    return (
      (left_clearance_pt !== null && left_clearance_pt <= threshold_pt) ||
      (right_clearance_pt !== null && right_clearance_pt <= threshold_pt)
    );
  }

  function resolve_keyring_hole_center(base_outer_path_pt, rx_pt, hole_radius_pt, boss_radius_pt) {
    const top_y_pt = topmost_intersection_y(base_outer_path_pt, rx_pt);
    const bounds = geometry_bounds({ paths_groups: [[base_outer_path_pt]] });
    const global_max_y_pt = bounds[3];
    const safety_margin_pt = mm_to_pt(15.0);
    const effective_top_y_pt = Math.max(top_y_pt, global_max_y_pt - safety_margin_pt);
    const slide_step_pt = mm_to_pt(KEYRING_BOSS_SLIDE_STEP_MM);
    let current_center_pt = [rx_pt, effective_top_y_pt + hole_radius_pt];

    if (!side_clearance_is_tight(base_outer_path_pt, current_center_pt, boss_radius_pt)) {
      return current_center_pt;
    }

    for (let i = 0; i < INTERNAL_ITERATION_GUARD; i++) {
      current_center_pt = [rx_pt, current_center_pt[1] + slide_step_pt];
      if (!side_clearance_is_tight(base_outer_path_pt, current_center_pt, boss_radius_pt)) {
        return current_center_pt;
      }
    }
    throw new Error('keyring boss side-clearance adjustment exceeded the internal safety guard');
  }

  function build_capsule_bridge_shapes(start_pt, end_pt, width_pt) {
    if (width_pt <= 0) throw new Error('bridge width must be positive');
    const dx = end_pt[0] - start_pt[0];
    const dy = end_pt[1] - start_pt[1];
    const segment_length = Math.hypot(dx, dy);
    const half_width_pt = width_pt / 2.0;
    if (segment_length <= GEOMETRY_EPSILON_PT) {
      return [approximate_circle_path(start_pt, half_width_pt)];
    }
    const ux = dx / segment_length;
    const uy = dy / segment_length;
    const nx = -uy;
    const ny = ux;
    const rectangle_path_pt = [
      [start_pt[0] + nx * half_width_pt, start_pt[1] + ny * half_width_pt],
      [end_pt[0] + nx * half_width_pt, end_pt[1] + ny * half_width_pt],
      [end_pt[0] - nx * half_width_pt, end_pt[1] - ny * half_width_pt],
      [start_pt[0] - nx * half_width_pt, start_pt[1] - ny * half_width_pt],
    ];
    return [
      rectangle_path_pt,
      approximate_circle_path(start_pt, half_width_pt),
      approximate_circle_path(end_pt, half_width_pt),
    ];
  }

  function build_boss_gap_bridge_shapes(base_outer_path_pt, boss_path_pt, hole_center_pt) {
    const threshold_pt = mm_to_pt(KEYRING_BOSS_MINIMUM_GAP_MM);
    const minimum_depth_pt = mm_to_pt(KEYRING_BOSS_GAP_MIN_DEPTH_MM);
    const minimum_span_pt = mm_to_pt(KEYRING_BOSS_GAP_MIN_SPAN_MM);
    const bridge_shapes_pt = [];
    const lower_boss_points_pt = boss_path_pt.filter(pt => pt[1] <= hole_center_pt[1] - minimum_depth_pt);
    if (!lower_boss_points_pt.length) return bridge_shapes_pt;

    for (const is_left_side of [true, false]) {
      const side_points_pt = lower_boss_points_pt.filter(pt =>
        is_left_side ? (pt[0] <= hole_center_pt[0]) : (pt[0] >= hole_center_pt[0])
      );
      if (!side_points_pt.length) continue;
      const runs = build_close_gap_runs(side_points_pt, base_outer_path_pt, threshold_pt);
      if (!runs.length) continue;
      const longest_run = runs.reduce((a, b) => (run_polyline_length(b.map(r => r[0])) > run_polyline_length(a.map(r => r[0]))) ? b : a);
      if (run_polyline_length(longest_run.map(r => r[0])) < minimum_span_pt) continue;
      const [boss_side_pt, body_side_pt, gap_distance_pt] = longest_run.reduce((best, curr) => (curr[2] < best[2] ? curr : best));
      if (body_side_pt[1] > hole_center_pt[1] - minimum_depth_pt) continue;
      bridge_shapes_pt.push(...build_capsule_bridge_shapes(body_side_pt, boss_side_pt, threshold_pt));
    }
    return bridge_shapes_pt;
  }

  function build_close_gap_runs(source_points_pt, target_path_pt, gap_threshold_pt) {
    const records = source_points_pt.map(sp => {
      const result = closest_points_between_path_sets([sp], target_path_pt);
      const tp = result[1];
      const dist = result[2];
      return [sp, tp, dist];
    });
    const runs = [];
    let current_run = [];
    for (const record of records) {
      if (record[2] < gap_threshold_pt) {
        current_run.push(record);
      } else {
        if (current_run.length) {
          runs.push(current_run);
          current_run = [];
        }
      }
    }
    if (current_run.length) runs.push(current_run);
    return runs;
  }

  function attach_island_to_outline(main_path_pt, island_path_pt, bridge_width_pt) {
    const [main_anchor_pt, island_anchor_pt] = closest_points_between_paths(main_path_pt, island_path_pt);
    const bridge_shapes_pt = build_capsule_bridge_shapes(main_anchor_pt, island_anchor_pt, bridge_width_pt);
    const unified_paths_pt = union_paths([main_path_pt, island_path_pt, ...bridge_shapes_pt]);
    if (!unified_paths_pt.length) throw new Error('island attachment produced an empty outline');
    const cleaned_paths_pt = cleanup_minimum_gap(unified_paths_pt, MINIMUM_CUTTABLE_GAP_MM);
    if (!cleaned_paths_pt.length) throw new Error('island cleanup produced an empty outline');
    return cleaned_paths_pt[0];
  }

  function build_base_outline_path(contour_paths_pt) {
    if (!contour_paths_pt.length) throw new Error('base outline generation did not receive any contours');
    const ordered_paths_pt = [...contour_paths_pt].sort((a, b) => Math.abs(signed_path_area(b)) - Math.abs(signed_path_area(a)));
    let combined_path_pt = [...ordered_paths_pt[0]];
    const bridge_width_pt = mm_to_pt(ISLAND_ATTACHMENT_WIDTH_MM);
    for (let i = 1; i < ordered_paths_pt.length; i++) {
      combined_path_pt = attach_island_to_outline(combined_path_pt, ordered_paths_pt[i], bridge_width_pt);
    }
    return combined_path_pt;
  }

  function build_reinforced_keyring_outer_path(base_outer_path_pt, hole_center_pt, boss_radius_pt) {
    const boss_path_pt = approximate_circle_path(hole_center_pt, boss_radius_pt);
    const body_anchor_y_pt = topmost_intersection_y(base_outer_path_pt, hole_center_pt[0]);
    const neck_bridge_shapes_pt = build_capsule_bridge_shapes([hole_center_pt[0], body_anchor_y_pt], hole_center_pt, mm_to_pt(KEYRING_NECK_WIDTH_MM));
    const gap_bridge_shapes_pt = build_boss_gap_bridge_shapes(base_outer_path_pt, boss_path_pt, hole_center_pt);
    const candidate_outer_paths_pt = keep_largest_path_only(union_paths([base_outer_path_pt, boss_path_pt, ...neck_bridge_shapes_pt, ...gap_bridge_shapes_pt]));
    if (!candidate_outer_paths_pt.length) throw new Error('keyring boss reinforcement produced an empty outline');
    const cleaned_outer_paths_pt = cleanup_minimum_gap(candidate_outer_paths_pt, Math.max(MINIMUM_CUTTABLE_GAP_MM, KEYRING_BOSS_MINIMUM_GAP_MM));
    if (!cleaned_outer_paths_pt.length) throw new Error('keyring minimum-gap cleanup produced an empty outline');
    return [cleaned_outer_paths_pt[0], boss_path_pt];
  }

  function build_keyring_balance(base_outer_path_pt, observer = null) {
    const THETA_TOLERANCE_RAD = 0.01;
    const THETA_IMPROVEMENT_TOLERANCE_RAD = 1e-5;
    const BRACKET_WIDTH_TOLERANCE_MM = 0.01;
    const RX_RECURRING_STEP_MM = 0.001;

    const hole_radius_pt = mm_to_pt(KEYRING_HOLE_RADIUS_MM);
    const boss_radius_pt = mm_to_pt(KEYRING_BOSS_RADIUS_MM);
    const [, base_centroid_pt] = polygon_area_and_centroid(base_outer_path_pt);
    let current_rx_pt = base_centroid_pt[0];
    let previous_candidate = null;
    let bracket_low = null;
    let bracket_high = null;
    let best_candidate = null;
    const seen_states = {};
    let iteration = 0;

    while (true) {
      iteration++;
      if (iteration > INTERNAL_ITERATION_GUARD) throw new Error('keyring balancing did not converge before the internal safety guard');

      const hole_center_pt = resolve_keyring_hole_center(base_outer_path_pt, current_rx_pt, hole_radius_pt, boss_radius_pt);
      const hole_path_pt = approximate_circle_path(hole_center_pt, hole_radius_pt);
      const [outer_path_pt, boss_path_pt] = build_reinforced_keyring_outer_path(base_outer_path_pt, hole_center_pt, boss_radius_pt);

      const [outer_area_pt2, outer_centroid_pt] = polygon_area_and_centroid(outer_path_pt);
      const hole_area_pt2 = Math.PI * hole_radius_pt * hole_radius_pt;
      const final_area_pt2 = outer_area_pt2 - hole_area_pt2;
      if (final_area_pt2 <= GEOMETRY_EPSILON_PT) throw new Error('keyring balancing produced a non-positive final area');

      const cx_final = (outer_area_pt2 * outer_centroid_pt[0] - hole_area_pt2 * hole_center_pt[0]) / final_area_pt2;
      const cy_final = (outer_area_pt2 * outer_centroid_pt[1] - hole_area_pt2 * hole_center_pt[1]) / final_area_pt2;
      const centroid_pt = [cx_final, cy_final];
      const error_x_pt = cx_final - current_rx_pt;
      const theta_rad = Math.atan2(error_x_pt, Math.max(GEOMETRY_EPSILON_PT, hole_center_pt[1] - cy_final));
      const current_candidate = new KeyringCandidate({
        rx_pt: current_rx_pt,
        error_x_pt,
        theta_rad,
        outer_path_pt,
        hole_path_pt,
        boss_path_pt,
        hole_center_pt,
        centroid_pt,
        outer_centroid_pt,
      });

      if (!best_candidate || Math.abs(current_candidate.theta_rad) < Math.abs(best_candidate.theta_rad)) {
        best_candidate = current_candidate;
      }

      let status = 'running';
      if (previous_candidate && previous_candidate.error_x_pt * current_candidate.error_x_pt < 0) {
        const [low, high] = [previous_candidate, current_candidate].sort((a, b) => a.rx_pt - b.rx_pt);
        bracket_low = low;
        bracket_high = high;
        status = 'oscillating';
      } else if (bracket_low && bracket_high) {
        if (current_candidate.error_x_pt * bracket_low.error_x_pt > 0) bracket_low = current_candidate;
        else if (current_candidate.error_x_pt * bracket_high.error_x_pt > 0) bracket_high = current_candidate;
        status = 'oscillating';
      }

      if (observer) {
        observer({
          stage: 'keyring_balance',
          message: 'Keyring hole placement, reinforcement, and balance iteration.',
          candidate: current_candidate,
          iteration,
          status,
          preset_name: 'keyring',
        });
      }

      if (Math.abs(current_candidate.theta_rad) <= THETA_TOLERANCE_RAD) {
        return new KeyringBalanceResult({ ...best_candidate, iterations: iteration });
      }

      const quantized_rx_mm = Math.round(pt_to_mm(current_candidate.rx_pt) * 1000) / 1000;
      const previous_theta = seen_states[quantized_rx_mm];
      const current_abs_theta = Math.abs(current_candidate.theta_rad);
      if (previous_theta !== undefined && previous_theta - current_abs_theta <= THETA_IMPROVEMENT_TOLERANCE_RAD) {
        return new KeyringBalanceResult({ ...best_candidate, iterations: iteration });
      }
      seen_states[quantized_rx_mm] = previous_theta !== undefined ? Math.min(previous_theta, current_abs_theta) : current_abs_theta;

      let next_rx_pt = current_candidate.rx_pt;
      if (bracket_low && bracket_high) {
        const bracket_width_mm = Math.abs(pt_to_mm(bracket_high.rx_pt - bracket_low.rx_pt));
        if (bracket_width_mm <= BRACKET_WIDTH_TOLERANCE_MM) {
          return new KeyringBalanceResult({ ...best_candidate, iterations: iteration });
        }
        next_rx_pt = (bracket_low.rx_pt + bracket_high.rx_pt) / 2.0;
      } else {
        next_rx_pt = (current_candidate.rx_pt + current_candidate.centroid_pt[0]) / 2.0;
      }

      if (Math.abs(pt_to_mm(next_rx_pt - current_candidate.rx_pt)) <= RX_RECURRING_STEP_MM) {
        return new KeyringBalanceResult({ ...best_candidate, iterations: iteration });
      }

      previous_candidate = current_candidate;
      current_rx_pt = next_rx_pt;
    }
  }

  function make_keyring_balance_result(candidate, iterations) {
    return new KeyringBalanceResult({
      outer_path_pt: [...candidate.outer_path_pt],
      hole_path_pt: [...candidate.hole_path_pt],
      boss_path_pt: [...candidate.boss_path_pt],
      hole_center_pt: candidate.hole_center_pt,
      centroid_pt: candidate.centroid_pt,
      theta_rad: candidate.theta_rad,
      iterations,
    });
  }

  // --- Trace Frame Helper (for converting-engine integration) ---

  function create_keyring_trace_frame({ stage, message, candidate, iteration, status, preset_name = 'keyring', outer_paths_pt = null, hole_paths_pt = null, boss_paths_pt = null, bbox_pt = null }) {
    const bbox = bbox_pt || geometry_bounds({
      paths_groups: [[candidate.outer_path_pt], [candidate.hole_path_pt], [candidate.boss_path_pt]],
      points: [candidate.hole_center_pt, candidate.centroid_pt],
    });
    const line_pt = extend_line_to_bbox(candidate.hole_center_pt, candidate.centroid_pt, bbox);
    return {
      stage,
      message,
      preset_name,
      status,
      iteration,
      outer_paths_pt: outer_paths_pt || [candidate.outer_path_pt],
      hole_paths_pt: hole_paths_pt || [candidate.hole_path_pt],
      boss_paths_pt: boss_paths_pt || [candidate.boss_path_pt],
      vertical_line_pt: [[candidate.rx_pt, bbox[1]], [candidate.rx_pt, bbox[3]]],
      boss_center_pt: candidate.hole_center_pt,
      centroid_pt: candidate.centroid_pt,
      outer_centroid_pt: candidate.outer_centroid_pt,
      centroid_line_pt: line_pt,
      theta_rad: candidate.theta_rad,
      bbox_pt: bbox,
    };
  }

  return {
    // Constants
    KEYRING_HOLE_RADIUS_MM,
    KEYRING_BOSS_SLIDE_STEP_MM,
    KEYRING_SIDE_CLEARANCE_THRESHOLD_MM,
    INTERNAL_ITERATION_GUARD,
    KEYRING_BOSS_RADIUS_MM,
    KEYRING_NECK_WIDTH_MM,
    KEYRING_BOSS_MINIMUM_GAP_MM,
    KEYRING_BOSS_GAP_MIN_DEPTH_MM,
    KEYRING_BOSS_GAP_MIN_SPAN_MM,
    ISLAND_ATTACHMENT_WIDTH_MM,
    MINIMUM_CUTTABLE_GAP_MM,
    OUTLINE_EXPAND_MM,
    OUTLINE_SHRINK_MM,

    // Models
    KeyringCandidate,
    KeyringBalanceResult,

    // Core functions
    keyring_side_clearances,
    side_clearance_is_tight,
    resolve_keyring_hole_center,
    build_capsule_bridge_shapes,
    build_boss_gap_bridge_shapes,
    build_close_gap_runs,
    attach_island_to_outline,
    build_base_outline_path,
    build_reinforced_keyring_outer_path,
    build_keyring_balance,
    make_keyring_balance_result,
    create_keyring_trace_frame,
  };
});
