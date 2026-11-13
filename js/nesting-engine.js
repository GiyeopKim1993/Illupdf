/*
 * illupdf Client-Side Nesting Engine (Port of src/illupdf/nesting/engine/)
 * Includes: Row Packing, Geometry, Counts, and Visualizer logic.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    const IlluGeometry = require('./geometry.js');
    const IlluConvertingEngine = require('./converting-engine.js');
    module.exports = factory(IlluGeometry, IlluConvertingEngine);
  } else {
    root.IlluNestingEngine = factory(root.IlluGeometry, root.IlluConvertingEngine);
  }
})(typeof self !== 'undefined' ? self : this, function (IlluGeometry, IlluConvertingEngine) {
  'use strict';

  const {
    PPI,
    MM_PER_INCH,
    PT_PER_INCH,
    mm_to_px,
    mm_to_pt,
    pt_to_mm,
    px_to_pt,
    geometry_bounds,
    signed_path_area,
    polygon_area_and_centroid,
    translate_path,
    offset_paths,
    union_paths,
    keep_largest_path_only,
    point_distance,
    closest_points_between_paths,
    closest_points_between_path_sets,
  } = IlluGeometry;

  // --- Constants ---
  const SHEET_SIZE_MM = { width: 400.0, height: 300.0 };
  const PEDESTAL_BASE_MM = { width: 35.0, height: 35.0, radius: 17.5 };
  const BEAM_SEARCH_WIDTH = 5;
  const AUTO_DETECTION_THRESHOLDS = { keyring: 25.0, stand: 40.0, mixed: 30.0 };

  // --- Models ---
  class NestingResult {
    constructor({ sheet_paths, pedestal_paths, auto_preset, beam_search_score }) {
      this.sheet_paths = sheet_paths; // Array of PathPoints arrays
      this.pedestal_paths = pedestal_paths; // Array of PathPoints arrays
      this.auto_preset = auto_preset;
      this.beam_search_score = beam_search_score;
    }
  }

  // --- Row Packing Engine ---
  function count_items(items) {
    const counts = {};
    for (const item of items) {
      const key = `${item.preset_name}_${item.size_mm}`;
      counts[key] = (counts[key] || 0) + 1;
    }
    return counts;
  }

  function geometry_fit(item_bounds, sheet_bounds) {
    return item_bounds[2] - item_bounds[0] <= sheet_bounds[2] - sheet_bounds[0] &&
           item_bounds[3] - item_bounds[1] <= sheet_bounds[3] - sheet_bounds[1];
  }

  function pack_rows(items, sheet_size_pt) {
    // Simplified row packing algorithm (Port of packer.py)
    // In a real port, this would include beam search and pedestal filling.
    const sheet_w = sheet_size_pt[0];
    const sheet_h = sheet_size_pt[1];
    const packed_sheets = [];
    let current_sheet_items = [];
    let current_y = 0;
    let row_height = 0;

    for (const item of items) {
      const bounds = geometry_bounds({ paths_groups: [item.paths_pt] });
      const w = bounds[2] - bounds[0];
      const h = bounds[3] - bounds[1];

      if (current_y + h > sheet_h || current_y === 0 && w > sheet_w) {
        if (current_sheet_items.length) {
          packed_sheets.push({ sheet_size_pt, items: current_sheet_items });
          current_sheet_items = [];
          current_y = 0;
          row_height = 0;
        } else {
          throw new Error('Item too large for sheet');
        }
      }

      // Simple placement: translate to current_y
      const translated = translate_path(item.paths_pt[0], 0, current_y);
      current_sheet_items.push({ ...item, paths_pt: [translated] });
      
      row_height = Math.max(row_height, h);
      // In actual row packing, we'd advance x and wrap to next row.
      // Simplified here for structure.
      current_y += row_height + mm_to_pt(1.0); // 1mm gap
    }

    if (current_sheet_items.length) {
      packed_sheets.push({ sheet_size_pt, items: current_sheet_items });
    }
    return packed_sheets;
  }

  // --- Auto Detection ---
  function detect_preset(artwork) {
    const long_side = Math.max(...artwork.raster_size_pt);
    const mm = pt_to_mm(long_side);
    if (mm >= AUTO_DETECTION_THRESHOLDS.keyring) return 'keyring';
    if (mm >= AUTO_DETECTION_THRESHOLDS.mixed) return 'mixed';
    return 'stand';
  }

  // --- Pedestal Base Filling ---
  function fill_pedestal_base(sheet_paths, pedestal_mm) {
    // Port of pedestal base void filling logic
    const pedestal_pt = [mm_to_pt(pedestal_mm.width), mm_to_pt(pedestal_mm.height)];
    const radius_pt = mm_to_pt(pedestal_mm.radius);
    const filled_paths = [];
    
    for (const path_group of sheet_paths) {
      const bounds = geometry_bounds({ paths_groups: [path_group] });
      const center = [(bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2];
      const circle = approximate_circle_path(center, radius_pt);
      filled_paths.push(circle);
    }
    return filled_paths;
  }

  // --- Main Engine ---
  function run_nesting(artworks, options = {}) {
    const sheet_pt = [mm_to_pt(SHEET_SIZE_MM.width), mm_to_pt(SHEET_SIZE_MM.height)];
    const preset = options.preset || detect_preset(artworks[0]);
    
    // 1. Prepare items
    const items = artworks.map(art => ({
      preset_name: preset,
      size_mm: art.size_mm,
      paths_pt: art.outline_paths_pt,
      bounds: geometry_bounds({ paths_groups: [art.outline_paths_pt] }),
    }));

    // 2. Pack rows (Beam search logic would be here)
    const packed_sheets = pack_rows(items, sheet_pt);

    // 3. Pedestal base filling
    const pedestal_paths = fill_pedestal_base(packed_sheets.flatMap(s => s.items.map(i => i.paths_pt)), PEDESTAL_BASE_MM);

    return new NestingResult({
      sheet_paths: packed_sheets,
      pedestal_paths,
      auto_preset: preset,
      beam_search_score: 0, // Placeholder for beam search score
    });
  }

  // --- Visualizer Logic ---
  function generate_nesting_visualization(nesting_result) {
    // Port of Nesting visualizer canvas drawing
    return {
      type: 'visualization',
      sheets: nesting_result.sheet_paths.map(sheet => ({
        size: sheet.sheet_size_pt,
        items: sheet.items.map(item => ({
          paths: item.paths_pt,
          preset: item.preset_name,
        })),
      })),
      pedestals: nesting_result.pedestal_paths,
    };
  }

  return {
    NestingResult,
    SHEET_SIZE_MM,
    PEDESTAL_BASE_MM,
    BEAM_SEARCH_WIDTH,
    AUTO_DETECTION_THRESHOLDS,
    count_items,
    geometry_fit,
    pack_rows,
    detect_preset,
    fill_pedestal_base,
    run_nesting,
    generate_nesting_visualization,
  };
});
