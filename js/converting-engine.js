/*
 * illupdf Client-Side Converting Engine (Port of src/illupdf/converting/engine/)
 * Includes: Pipeline, Keyring Balance, Reinforcement, Vector Layout, White Spot, and Models.
 * Keyring logic is in js/keyring-engine.js.
 * Depends on: IlluGeometry (js/geometry.js), IlluZip (js/zip.js), IlluKeyringEngine (js/keyring-engine.js)
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    const IlluGeometry = require('./geometry.js');
    const IlluZip = require('./zip.js');
    const IlluKeyringEngine = require('./keyring-engine.js');
    module.exports = factory(IlluGeometry, IlluZip, IlluKeyringEngine);
  } else {
    root.IlluConvertingEngine = factory(root.IlluGeometry, root.IlluZip, root.IlluKeyringEngine);
  }
})(typeof self !== 'undefined' ? self : this, function (IlluGeometry, IlluZip, IlluKeyringEngine) {
  'use strict';

  const {
    PPI,
    MM_PER_INCH,
    PT_PER_INCH,
    WORKING_ALPHA_MARGIN_MM,
    GEOMETRY_EPSILON_PT,
    mm_to_px,
    mm_to_pt,
    pt_to_mm,
    px_to_pt,
    geometry_bounds,
    signed_path_area,
    polygon_area_and_centroid,
    translate_path,
    offset_paths,
    cleanup_tiny_protrusions,
    cleanup_minimum_gap,
    union_paths,
    approximate_circle_path,
    extend_line_to_bbox,
    build_custom_mask,
    build_white_mask,
    extract_significant_outer_contours,
    contour_to_pdf_path,
    simplify_contour,
    keep_largest_path_only,
  } = IlluGeometry;

  // --- Keyring Engine (separate module) ---
  const {
    KeyringCandidate,
    KeyringBalanceResult,
    KEYRING_BOSS_RADIUS_MM,
    KEYRING_NECK_WIDTH_MM,
    KEYRING_BOSS_MINIMUM_GAP_MM,
    KEYRING_BOSS_GAP_MIN_DEPTH_MM,
    KEYRING_BOSS_GAP_MIN_SPAN_MM,
    ISLAND_ATTACHMENT_WIDTH_MM,
    MINIMUM_CUTTABLE_GAP_MM,
    OUTLINE_EXPAND_MM,
    OUTLINE_SHRINK_MM,
    build_base_outline_path,
    build_reinforced_keyring_outer_path,
    build_keyring_balance,
    create_keyring_trace_frame,
  } = IlluKeyringEngine;

  // --- Models ---

  class TraceFrame {
    constructor({
      stage = '',
      message = '',
      preset_name = '',
      status = 'running',
      iteration = 0,
      outer_paths_pt = [],
      hole_paths_pt = [],
      boss_paths_pt = [],
      vertical_line_pt = null,
      boss_center_pt = null,
      centroid_pt = null,
      outer_centroid_pt = null,
      centroid_line_pt = null,
      theta_rad = 0,
      bbox_pt = null,
      raster_b64 = null,
      raster_rect_pt = null,
      bbox_width_mm = 0,
      bbox_height_mm = 0,
      bbox_area_mm2 = 0,
      outline_area_mm2 = 0,
    }) {
      this.stage = stage;
      this.message = message;
      this.preset_name = preset_name;
      this.status = status;
      this.iteration = iteration;
      this.outer_paths_pt = outer_paths_pt;
      this.hole_paths_pt = hole_paths_pt;
      this.boss_paths_pt = boss_paths_pt;
      this.vertical_line_pt = vertical_line_pt;
      this.boss_center_pt = boss_center_pt;
      this.centroid_pt = centroid_pt;
      this.outer_centroid_pt = outer_centroid_pt;
      this.centroid_line_pt = centroid_line_pt;
      this.theta_rad = theta_rad;
      this.bbox_pt = bbox_pt;
      this.raster_b64 = raster_b64;
      this.raster_rect_pt = raster_rect_pt;
      this.bbox_width_mm = bbox_width_mm;
      this.bbox_height_mm = bbox_height_mm;
      this.bbox_area_mm2 = bbox_area_mm2;
      this.outline_area_mm2 = outline_area_mm2;
    }
  }

  class PreparedArtwork {
    constructor({
      preset_name,
      rgba_image,
      base_paths_pt,
      outline_paths_pt,
      outline_outer_paths_pt,
      outline_inner_paths_pt,
      white_source_paths_pt,
      white_paths_pt,
      raster_origin_pt,
      raster_size_pt,
      page_size_pt,
      keyring_balance,
      white_raster_img,
      white_coverage_mask,
      white_spot1_img,
    }) {
      this.preset_name = preset_name;
      this.rgba_image = rgba_image;
      this.base_paths_pt = base_paths_pt;
      this.outline_paths_pt = outline_paths_pt;
      this.outline_outer_paths_pt = outline_outer_paths_pt;
      this.outline_inner_paths_pt = outline_inner_paths_pt;
      this.white_source_paths_pt = white_source_paths_pt;
      this.white_paths_pt = white_paths_pt;
      this.raster_origin_pt = raster_origin_pt;
      this.raster_size_pt = raster_size_pt;
      this.page_size_pt = page_size_pt;
      this.keyring_balance = keyring_balance;
      this.white_raster_img = white_raster_img;
      this.white_coverage_mask = white_coverage_mask;
      this.white_spot1_img = white_spot1_img;
    }
  }

  class SpotColorDefinition {
    constructor({ name, cmyk }) {
      this.name = name;
      this.cmyk = cmyk;
    }
  }

  // --- Constants ---
  const CALIBRATION_TOLERANCE_MM = 1.0;
  const CALIBRATION_MAX_ITERATIONS = 3;

  // --- Browser Image Utilities ---

  /**
   * Load an image file and return a Promise<Image>
   */
  function loadImageFromFile(file) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = (err) => reject(err);
      img.src = URL.createObjectURL(file);
    });
  }

  /**
   * Extract RGBA Uint8Array from an Image using offscreen canvas
   * Returns { data: Uint8Array, width, height }
   */
  function imageToRGBA(image) {
    const canvas = document.createElement('canvas');
    canvas.width = image.width;
    canvas.height = image.height;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(image, 0, 0);
    const imageData = ctx.getImageData(0, 0, image.width, image.height);
    return {
      data: new Uint8Array(imageData.data),
      width: image.width,
      height: image.height,
    };
  }

  /**
   * Crop RGBA image to alpha bounds (non-transparent region)
   * Returns { data: Uint8Array, width, height }
   */
  function cropToAlphaBounds(rgbaData, width, height) {
    let minX = width, minY = height, maxX = 0, maxY = 0;
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const alpha = rgbaData[(y * width + x) * 4 + 3];
        if (alpha > 0) {
          if (x < minX) minX = x;
          if (y < minY) minY = y;
          if (x > maxX) maxX = x;
          if (y > maxY) maxY = y;
        }
      }
    }
    if (minX > maxX) {
      return { data: new Uint8Array(4), width: 1, height: 1 };
    }

    const cropW = maxX - minX + 1;
    const cropH = maxY - minY + 1;
    const cropped = new Uint8Array(cropW * cropH * 4);
    for (let y = 0; y < cropH; y++) {
      for (let x = 0; x < cropW; x++) {
        const srcIdx = ((minY + y) * width + (minX + x)) * 4;
        const dstIdx = (y * cropW + x) * 4;
        cropped[dstIdx] = rgbaData[srcIdx];
        cropped[dstIdx + 1] = rgbaData[srcIdx + 1];
        cropped[dstIdx + 2] = rgbaData[srcIdx + 2];
        cropped[dstIdx + 3] = rgbaData[srcIdx + 3];
      }
    }
    return { data: cropped, width: cropW, height: cropH };
  }

  /**
   * Resize RGBA image using nearest-neighbor (fast, good enough for mask generation)
   * Returns { data: Uint8Array, width, height }
   */
  function resizeRGBA(rgbaData, width, height, targetWidth, targetHeight) {
    const resized = new Uint8Array(targetWidth * targetHeight * 4);
    const xRatio = width / targetWidth;
    const yRatio = height / targetHeight;
    for (let ty = 0; ty < targetHeight; ty++) {
      for (let tx = 0; tx < targetWidth; tx++) {
        const sx = Math.min(Math.floor(tx * xRatio), width - 1);
        const sy = Math.min(Math.floor(ty * yRatio), height - 1);
        const srcIdx = (sy * width + sx) * 4;
        const dstIdx = (ty * targetWidth + tx) * 4;
        resized[dstIdx] = rgbaData[srcIdx];
        resized[dstIdx + 1] = rgbaData[srcIdx + 1];
        resized[dstIdx + 2] = rgbaData[srcIdx + 2];
        resized[dstIdx + 3] = rgbaData[srcIdx + 3];
      }
    }
    return { data: resized, width: targetWidth, height: targetHeight };
  }

  /**
   * Add transparent padding around RGBA image
   * Returns { data: Uint8Array, width, height }
   */
  function addTransparentPadding(rgbaData, width, height, padding) {
    const newW = width + padding * 2;
    const newH = height + padding * 2;
    const padded = new Uint8Array(newW * newH * 4); // All zeros = transparent
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const srcIdx = (y * width + x) * 4;
        const dstIdx = ((y + padding) * newW + (x + padding)) * 4;
        padded[dstIdx] = rgbaData[srcIdx];
        padded[dstIdx + 1] = rgbaData[srcIdx + 1];
        padded[dstIdx + 2] = rgbaData[srcIdx + 2];
        padded[dstIdx + 3] = rgbaData[srcIdx + 3];
      }
    }
    return { data: padded, width: newW, height: newH };
  }

  /**
   * Convert RGBA Uint8Array to base64 data URL
   */
  function rgbaToB64(rgbaData, width, height) {
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    const imageData = ctx.createImageData(width, height);
    imageData.data.set(rgbaData);
    ctx.putImageData(imageData, 0, 0);
    return canvas.toDataURL();
  }

  // --- Converting Engine Logic ---

  function emit_trace(observer, frame) {
    if (observer) {
      enrich_trace_frame(frame);
      observer(frame);
    }
  }

  function enrich_trace_frame(frame) {
    if (frame.bbox_pt) {
      const [min_x, min_y, max_x, max_y] = frame.bbox_pt;
      const width_pt = max_x - min_x;
      const height_pt = max_y - min_y;
      frame.bbox_width_mm = pt_to_mm(width_pt);
      frame.bbox_height_mm = pt_to_mm(height_pt);
      frame.bbox_area_mm2 = frame.bbox_width_mm * frame.bbox_height_mm;
    }
    if (frame.outer_paths_pt && frame.outer_paths_pt.length > 0) {
      const total_area_pt = frame.outer_paths_pt.reduce((sum, p) => sum + Math.abs(signed_path_area(p)), 0);
      frame.outline_area_mm2 = total_area_pt * (pt_to_mm(1.0) ** 2);
    }
  }

  function make_keyring_trace_frame({ preset_name, stage, message, candidate, iteration, status }) {
    return new TraceFrame({
      ...create_keyring_trace_frame({ stage, message, candidate, iteration, status, preset_name }),
      outer_paths_pt: [candidate.outer_path_pt],
      hole_paths_pt: [candidate.hole_path_pt],
      boss_paths_pt: [candidate.boss_path_pt],
    });
  }

  function emit_raster_trace(observer, stage, message, image, rect_pt, preset_name) {
    if (!observer) return;
    const raster_b64 = rgbaToB64(image.data, image.width, image.height);
    const frame = new TraceFrame({
      stage,
      message,
      preset_name,
      raster_b64,
      raster_rect_pt,
      bbox_pt: [rect_pt[0], rect_pt[1], rect_pt[0] + rect_pt[2], rect_pt[1] + rect_pt[3]],
    });
    emit_trace(observer, frame);
  }

  function final_outline_long_side_mm(prepared) {
    const bounds = geometry_bounds({ paths_groups: [prepared.outline_outer_paths_pt] });
    return Math.max(pt_to_mm(bounds[2] - bounds[0]), pt_to_mm(bounds[3] - bounds[1]));
  }

  function estimate_initial_raster_long_side_mm({ cropped, preset_name, target_final_size_mm }) {
    const net_growth_one_side = OUTLINE_EXPAND_MM - OUTLINE_SHRINK_MM;
    let predicted_growth = net_growth_one_side * 2;
    if (preset_name === 'keyring') {
      predicted_growth = (cropped.height >= cropped.width) ? (net_growth_one_side + 5.0) : (net_growth_one_side * 2);
    }
    return Math.max(1.0, target_final_size_mm - predicted_growth);
  }

  // --- Vector Layout ---

  function translate_to_page(base_paths_pt, outline_outer_paths_pt, outline_inner_paths_pt, white_source_paths_pt, white_paths_pt, raster_size_pt) {
    const [min_x, min_y, max_x, max_y] = geometry_bounds({
      raster_size_pt,
      paths_groups: [base_paths_pt, outline_outer_paths_pt, outline_inner_paths_pt, white_source_paths_pt, white_paths_pt]
    });
    const offset_x = -min_x;
    const offset_y = -min_y;
    const translated_base = base_paths_pt.map(p => translate_path(p, offset_x, offset_y));
    const translated_outline_outer = outline_outer_paths_pt.map(p => translate_path(p, offset_x, offset_y));
    const translated_outline_inner = outline_inner_paths_pt.map(p => translate_path(p, offset_x, offset_y));
    const translated_white_source = white_source_paths_pt.map(p => translate_path(p, offset_x, offset_y));
    const translated_white = white_paths_pt.map(p => translate_path(p, offset_x, offset_y));
    const page_size_pt = [max_x - min_x, max_y - min_y];
    const raster_origin_pt = [offset_x, offset_y];
    return [translated_base, translated_outline_outer, translated_outline_inner, translated_white_source, translated_white, raster_origin_pt, page_size_pt];
  }

  function translate_keyring_balance(balance, offset_x, offset_y) {
    return new KeyringBalanceResult({
      outer_path_pt: translate_path(balance.outer_path_pt, offset_x, offset_y),
      hole_path_pt: translate_path(balance.hole_path_pt, offset_x, offset_y),
      boss_path_pt: translate_path(balance.boss_path_pt, offset_x, offset_y),
      hole_center_pt: [balance.hole_center_pt[0] + offset_x, balance.hole_center_pt[1] + offset_y],
      centroid_pt: [balance.centroid_pt[0] + offset_x, balance.centroid_pt[1] + offset_y],
      theta_rad: balance.theta_rad,
      iterations: balance.iterations,
    });
  }

  // --- Pipeline: process RGBA data directly (browser-friendly) ---

  /**
   * Prepare artwork from raw RGBA Uint8Array data.
   * This is the main entry point for browser-based image processing.
   *
   * @param {Object} params
   * @param {Uint8Array} params.rgbaData - RGBA pixel data (width * height * 4 bytes)
   * @param {number} params.width - Image width in pixels
   * @param {number} params.height - Image height in pixels
   * @param {string} params.preset_name - 'keyring', 'stand', or 'mixed'
   * @param {number} [params.size_mm] - Target final size in mm
   * @param {Function} [params.observer] - Trace callback
   * @param {number} [params.mask_alpha_threshold=8] - Alpha threshold for mask
   * @returns {PreparedArtwork}
   */
  function prepare_artwork_from_rgba({
    rgbaData,
    width,
    height,
    preset_name,
    size_mm = null,
    observer = null,
    mask_alpha_threshold = 8,
  }) {
    const PRESETS = {
      keyring: { has_boss: true, default_size_mm: 65.0 },
      stand: { has_boss: false, default_size_mm: 148.0, has_connection: true, has_support: true, has_base: true },
      mixed: { has_boss: false, default_size_mm: 40.0 },
    };
    const preset = PRESETS[preset_name] || PRESETS.stand;
    const target_final_size_mm = size_mm !== null ? size_mm : preset.default_size_mm;

    // Work with RGBA data directly
    let cropped = { data: rgbaData, width, height };

    emit_raster_trace(observer, 'input', 'Raw input image loaded.', cropped, [0, 0, px_to_pt(cropped.width), px_to_pt(cropped.height)], preset_name);

    // Crop to alpha bounds
    cropped = cropToAlphaBounds(cropped.data, cropped.width, cropped.height);
    emit_raster_trace(observer, 'crop', 'Image cropped to alpha bounds.', cropped, [0, 0, px_to_pt(cropped.width), px_to_pt(cropped.height)], preset_name);

    // Estimate initial raster size
    const initial_raster_long_side_mm = estimate_initial_raster_long_side_mm({
      cropped,
      preset_name,
      target_final_size_mm,
    });

    // Initial prepare (no observer for performance)
    const initial_prepared = prepare_artwork_for_raster_target({
      cropped,
      preset_name,
      target_raster_long_side_mm: initial_raster_long_side_mm,
      observer: null,
      mask_alpha_threshold,
    });
    const initial_actual_final_size_mm = final_outline_long_side_mm(initial_prepared);
    const initial_error_mm = Math.abs(initial_actual_final_size_mm - target_final_size_mm);

    if (initial_error_mm <= CALIBRATION_TOLERANCE_MM || initial_actual_final_size_mm < target_final_size_mm * 0.5 || initial_actual_final_size_mm > target_final_size_mm * 2.0) {
      emit_trace(observer, new TraceFrame({
        stage: 'size_calibration',
        message: `Initial raster long side ${initial_raster_long_side_mm.toFixed(2)}mm produced final outline ${initial_actual_final_size_mm.toFixed(2)}mm (target ${target_final_size_mm.toFixed(2)}mm).`,
        preset_name,
        status: 'done',
      }));
      return prepare_artwork_for_raster_target({
        cropped,
        preset_name,
        target_raster_long_side_mm: initial_raster_long_side_mm,
        observer,
        mask_alpha_threshold,
      });
    }

    // Calibrate
    const [raster_long_side_mm, actual_final_size_mm] = calibrate_raster_long_side_mm({
      cropped,
      preset_name,
      target_final_size_mm,
      initial_raster_long_side_mm,
      mask_alpha_threshold,
    });

    emit_trace(observer, new TraceFrame({
      stage: 'size_calibration',
      message: `Binary-search calibration selected raster long side ${raster_long_side_mm.toFixed(2)}mm for final outline ${actual_final_size_mm.toFixed(2)}mm (target ${target_final_size_mm.toFixed(2)}mm).`,
      preset_name,
      status: 'done',
    }));

    const prepared = prepare_artwork_for_raster_target({
      cropped,
      preset_name,
      target_raster_long_side_mm: raster_long_side_mm,
      observer,
      mask_alpha_threshold,
    });

    const final_actual_size_mm = final_outline_long_side_mm(prepared);
    const final_error_mm = Math.abs(final_actual_size_mm - target_final_size_mm);

    if (final_error_mm > CALIBRATION_TOLERANCE_MM) {
      const corrected_raster_long_side_mm = Math.max(1.0, raster_long_side_mm * target_final_size_mm / final_actual_size_mm);
      emit_trace(observer, new TraceFrame({
        stage: 'size_calibration_refine',
        message: `Final verification adjusted raster long side ${raster_long_side_mm.toFixed(2)}mm -> ${corrected_raster_long_side_mm.toFixed(2)}mm after final outline ${final_actual_size_mm.toFixed(2)}mm.`,
        preset_name,
        status: 'running',
      }));
      return prepare_artwork_for_raster_target({
        cropped,
        preset_name,
        target_raster_long_side_mm: corrected_raster_long_side_mm,
        observer,
        mask_alpha_threshold,
      });
    }
    return prepared;
  }

  function prepare_artwork_for_raster_target({ cropped, preset_name, target_raster_long_side_mm, observer = null, mask_alpha_threshold = 8, skip_white = false }) {
    const PRESETS = {
      keyring: { has_boss: true, default_size_mm: 65.0 },
      stand: { has_boss: false, default_size_mm: 148.0, has_connection: true, has_support: true, has_base: true },
      mixed: { has_boss: false, default_size_mm: 40.0 },
    };
    const preset = PRESETS[preset_name] || PRESETS.stand;

    // 1. Normalize / Resize
    const target_size = compute_target_size_pixels(cropped, target_raster_long_side_mm);
    const resized = resizeRGBA(cropped.data, cropped.width, cropped.height, target_size[0], target_size[1]);
    emit_raster_trace(observer, 'normalized', `Resized to target raster size (${target_raster_long_side_mm.toFixed(2)}mm).`, resized, [0, 0, px_to_pt(resized.width), px_to_pt(resized.height)], preset_name);

    // 2. Padding & Mask
    const working_padding_px = mm_to_px(WORKING_ALPHA_MARGIN_MM);
    const working_image = addTransparentPadding(resized.data, resized.width, resized.height, working_padding_px);
    const working_padding_pt = px_to_pt(working_padding_px);

    // Build custom mask from RGBA
    const mask = build_custom_mask(working_image.data, working_image.width, working_image.height, mask_alpha_threshold);
    const contours = extract_significant_outer_contours(mask, working_image.width, working_image.height);
    const contour_paths_pt = contours.map(c =>
      translate_path(contour_to_pdf_path(simplify_contour(c), working_image.height), -working_padding_pt, -working_padding_pt)
    );

    const filtered_paths_pt = contour_paths_pt.filter(p => {
      const area_mm2 = Math.abs(signed_path_area(p)) / (mm_to_pt(1.0) ** 2);
      return area_mm2 >= 2.0;
    });
    if (!filtered_paths_pt.length) throw new Error('no significant outline islands after area filtering');

    // 3. Base Outline
    let base_path_pt = build_base_outline_path(filtered_paths_pt);
    const radius_pt = mm_to_pt(0.1);
    const opened_shrunk = keep_largest_path_only(offset_paths([base_path_pt], -radius_pt));
    if (opened_shrunk.length) {
      const opened_expanded = keep_largest_path_only(offset_paths(opened_shrunk, radius_pt));
      if (opened_expanded.length) base_path_pt = opened_expanded[0];
    }
    const closed_expanded = keep_largest_path_only(offset_paths([base_path_pt], radius_pt));
    if (closed_expanded.length) {
      const closed_shrunk = keep_largest_path_only(offset_paths(closed_expanded, -radius_pt));
      if (closed_shrunk.length) base_path_pt = closed_shrunk[0];
    }
    emit_trace(observer, new TraceFrame({
      stage: 'base_outline',
      message: 'Base outline extracted from the alpha silhouette and attached islands.',
      preset_name,
      outer_paths_pt: [base_path_pt],
      bbox_pt: geometry_bounds({ paths_groups: [[base_path_pt]] }),
    }));

    // 4. Expand & Shrink Outline
    const expanded_outline_pt = keep_largest_path_only(offset_paths([base_path_pt], mm_to_pt(OUTLINE_EXPAND_MM)));
    emit_trace(observer, new TraceFrame({
      stage: 'outline_expand',
      message: 'Round +5mm offset applied to the base contour.',
      preset_name,
      outer_paths_pt: expanded_outline_pt,
      bbox_pt: geometry_bounds({ paths_groups: [expanded_outline_pt] }),
    }));
    const outline_outer_paths_pt = keep_largest_path_only(offset_paths(expanded_outline_pt, -mm_to_pt(OUTLINE_SHRINK_MM)));
    if (!outline_outer_paths_pt.length) throw new Error('outline layer generation produced an empty result');
    emit_trace(observer, new TraceFrame({
      stage: 'outline_shrink',
      message: 'Round -3mm offset applied to build the cutline.',
      preset_name,
      outer_paths_pt: outline_outer_paths_pt,
      bbox_pt: geometry_bounds({ paths_groups: [outline_outer_paths_pt] }),
    }));

    // 5. Keyring Balance
    let outline_inner_paths_pt = [];
    let keyring_balance = null;
    if (preset_name === 'keyring' && preset.has_boss) {
      keyring_balance = build_keyring_balance(outline_outer_paths_pt[0], (traceEvent) => {
        emit_trace(observer, make_keyring_trace_frame({
          preset_name,
          stage: traceEvent.stage,
          message: traceEvent.message,
          candidate: traceEvent.candidate,
          iteration: traceEvent.iteration,
          status: traceEvent.status,
        }));
      });
      outline_outer_paths_pt = [keyring_balance.outer_path_pt];
      outline_inner_paths_pt = [keyring_balance.hole_path_pt];
      const [_kr_outer_area, _kr_outer_centroid] = polygon_area_and_centroid(keyring_balance.outer_path_pt);
      emit_trace(observer, make_keyring_trace_frame({
        preset_name,
        stage: 'keyring_result',
        message: 'Keyring boss and hole placement finalized.',
        candidate: keyring_balance,
        iteration: keyring_balance.iterations,
        status: 'final',
      }));
    }

    // 6. Cleanup
    const outline_bounds = geometry_bounds({ paths_groups: [outline_outer_paths_pt] });
    const outline_long_side_mm = Math.max(pt_to_mm(outline_bounds[2] - outline_bounds[0]), pt_to_mm(outline_bounds[3] - outline_bounds[1]));
    if (outline_long_side_mm >= 20.0) {
      const cleaned_outline_outer_paths_pt = cleanup_tiny_protrusions(outline_outer_paths_pt, 0.2);
      if (cleaned_outline_outer_paths_pt.length) {
        outline_outer_paths_pt = cleaned_outline_outer_paths_pt;
        emit_trace(observer, new TraceFrame({
          stage: 'outline_cleanup',
          message: 'Tiny final cutline protrusions removed with open/intersect cleanup.',
          preset_name,
          outer_paths_pt: outline_outer_paths_pt,
          bbox_pt: geometry_bounds({ paths_groups: [outline_outer_paths_pt] }),
        }));
      }
    }

    // 7. White Mask & Spot
    let white_raster_img = null, white_coverage_mask = null, white_spot1_img = null;
    if (skip_white) {
      white_raster_img = create_mock_image(resized.width, resized.height, 255);
      white_coverage_mask = create_mock_image(resized.width, resized.height, 0);
    } else {
      const white_mask = build_white_mask(mask, working_image.width, working_image.height);
      white_raster_img = { data: white_mask, width: resized.width, height: resized.height };
      white_coverage_mask = { data: white_mask, width: working_image.width, height: working_image.height };
      white_spot1_img = create_spot1_from_mask(white_coverage_mask);
    }

    // 8. Translate to Page
    const [translated_base, translated_outline_outer, translated_outline_inner, translated_white_source, translated_white, raster_origin_pt, page_size_pt] = translate_to_page(
      [base_path_pt], outline_outer_paths_pt, outline_inner_paths_pt, [], [], [px_to_pt(resized.width), px_to_pt(resized.height)]
    );

    const translated_balance = keyring_balance ? translate_keyring_balance(keyring_balance, ...raster_origin_pt) : null;
    emit_trace(observer, new TraceFrame({
      stage: 'final',
      message: 'Final layered composition on output page.',
      preset_name,
      status: 'done',
      outer_paths_pt: translated_outline_outer,
      hole_paths_pt: translated_outline_inner,
      raster_b64: rgbaToB64(resized.data, resized.width, resized.height),
      raster_rect_pt: [...raster_origin_pt, px_to_pt(resized.width), px_to_pt(resized.height)],
      bbox_pt: [0, 0, ...page_size_pt],
    }));

    return new PreparedArtwork({
      preset_name,
      rgba_image: resized,
      base_paths_pt: translated_base,
      outline_paths_pt: [...translated_outline_outer, ...translated_outline_inner],
      outline_outer_paths_pt: translated_outline_outer,
      outline_inner_paths_pt: translated_outline_inner,
      white_source_paths_pt: [],
      white_paths_pt: [],
      raster_origin_pt,
      raster_size_pt: [px_to_pt(resized.width), px_to_pt(resized.height)],
      page_size_pt,
      keyring_balance: translated_balance,
      white_raster_img,
      white_coverage_mask,
      white_spot1_img,
    });
  }

  // --- Helper: compute target size in pixels ---

  function compute_target_size_pixels(cropped, target_mm) {
    const ratio = cropped.width / cropped.height;
    const ppi = PPI;
    if (cropped.width >= cropped.height) {
      return [Math.round((target_mm / MM_PER_INCH) * ppi), Math.round((target_mm / MM_PER_INCH) * ppi / ratio)];
    } else {
      return [Math.round((target_mm / MM_PER_INCH) * ppi * ratio), Math.round((target_mm / MM_PER_INCH) * ppi)];
    }
  }

  // --- Mock/Helper image creation (for white mask outputs) ---

  function create_mock_image(w, h, val) {
    const data = new Uint8Array(w * h).fill(val);
    return { width: w, height: h, data };
  }

  function create_spot1_from_mask(mask) {
    // Spot_1: flat 25% fill inside coverage mask
    const w = mask.width, h = mask.height;
    const data = new Uint8Array(w * h);
    for (let i = 0; i < w * h; i++) {
      data[i] = mask.data[i] > 0 ? Math.round(255 * 0.25) : 0;
    }
    return { width: w, height: h, data };
  }

  // --- Calibration ---

  function calibrate_raster_long_side_mm({ cropped, preset_name, target_final_size_mm, initial_raster_long_side_mm, mask_alpha_threshold = 8, tolerance_mm = CALIBRATION_TOLERANCE_MM, max_iterations = CALIBRATION_MAX_ITERATIONS }) {
    const measure = (raster_mm) => {
      const prepared = prepare_artwork_for_raster_target({
        cropped, preset_name, target_raster_long_side_mm: Math.max(1.0, raster_mm), mask_alpha_threshold,
      });
      const actual = final_outline_long_side_mm(prepared);
      return actual;
    };

    let best_raster = Math.max(1.0, initial_raster_long_side_mm);
    let best_actual = measure(best_raster);
    let best_error = Math.abs(best_actual - target_final_size_mm);
    if (best_error <= tolerance_mm) return [best_raster, best_actual];

    const scaled_raster = Math.max(1.0, initial_raster_long_side_mm * (target_final_size_mm / best_actual));
    const scaled_actual = measure(scaled_raster);
    const scaled_error = Math.abs(scaled_actual - target_final_size_mm);

    if (scaled_error <= tolerance_mm || scaled_error < best_error) {
      best_raster = scaled_raster;
      best_actual = scaled_actual;
      best_error = scaled_error;
    }

    if (best_error <= tolerance_mm) return [best_raster, best_actual];

    let low_raster = Math.min(initial_raster_long_side_mm, scaled_raster);
    let high_raster = Math.max(initial_raster_long_side_mm, scaled_raster);
    for (let i = 0; i < max_iterations; i++) {
      const mid_raster = (low_raster + high_raster) / 2.0;
      const mid_actual = measure(mid_raster);
      const err = Math.abs(mid_actual - target_final_size_mm);
      if (err < best_error) {
        best_raster = mid_raster;
        best_actual = mid_actual;
        best_error = err;
      }
      if (err <= tolerance_mm) break;
      if (mid_actual > target_final_size_mm) high_raster = mid_raster;
      else low_raster = mid_raster;
    }
    return [best_raster, best_actual];
  }

  // --- Legacy file-path based prepare_artwork (Node.js compatibility) ---

  function prepare_artwork(input_path, preset_name, size_mm = null, observer = null, mask_alpha_threshold = 8) {
    throw new Error('prepare_artwork(input_path) is not supported in browser. Use prepare_artwork_from_rgba() instead.');
  }

  return {
    TraceFrame,
    PreparedArtwork,
    SpotColorDefinition,
    prepare_artwork,
    prepare_artwork_from_rgba,
    prepare_artwork_for_raster_target,
    calibrate_raster_long_side_mm,
    emit_trace,
    enrich_trace_frame,
    make_keyring_trace_frame,
    emit_raster_trace,
    final_outline_long_side_mm,
    estimate_initial_raster_long_side_mm,
    translate_to_page,
    translate_keyring_balance,
    build_base_outline_path,
    build_reinforced_keyring_outer_path,
    build_keyring_balance,
    // Browser utilities
    loadImageFromFile,
    imageToRGBA,
    cropToAlphaBounds,
    resizeRGBA,
    addTransparentPadding,
    rgbaToB64,
  };

});
