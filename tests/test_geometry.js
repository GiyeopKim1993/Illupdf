const IlluGeometry = require('../js/geometry.js');
const g = IlluGeometry;

console.log('=== UNIT TESTS ===');
// 1. Units
console.assert(g.mm_to_px(10) === 567, 'mm_to_px(10) should be 567');
console.assert(Math.abs(g.mm_to_pt(10) - 28.346456) < 0.001, 'mm_to_pt(10)');
console.assert(Math.abs(g.pt_to_mm(28.346456) - 10) < 0.001, 'pt_to_mm(28.346456)');
console.assert(Math.abs(g.px_to_pt(567) - 28.35) < 0.01, 'px_to_pt(567)');

// 2. Polygons
const square = [[0,0],[10,0],[10,10],[0,10]];
console.assert(Math.abs(g.signed_path_area(square) - 100) < 0.001, 'signed_path_area');
const [area, centroid] = g.polygon_area_and_centroid(square);
console.assert(Math.abs(area - 100) < 0.001, 'area');
console.assert(Math.abs(centroid[0] - 5) < 0.001, 'centroid x');
console.assert(Math.abs(centroid[1] - 5) < 0.001, 'centroid y');

const bounds = g.geometry_bounds({ paths_groups: [square] });
console.assert(bounds[0] === 0 && bounds[1] === 0 && bounds[2] === 10 && bounds[3] === 10, 'bounds');

const translated = g.translate_path(square, 5, 5);
console.assert(translated[0][0] === 5 && translated[0][1] === 5, 'translate');

// 3. Intersections
const horiz = g.horizontal_line_intersections(square, 5);
console.assert(horiz.length === 2 && Math.abs(horiz[0] - 0) < 0.001 && Math.abs(horiz[1] - 10) < 0.001, 'horizontal_line_intersections');

// 4. Clipper
const union = g.union_paths([square, [[15,15],[25,15],[25,25],[15,25]]]);
console.assert(union.length === 2, 'union_paths');

const offset = g.offset_paths([square], 5);
console.assert(offset.length === 1, 'offset_paths');

// 5. Morphology
const mask = new Uint8Array(100 * 100);
for (let y = 40; y < 60; y++) {
  for (let x = 40; x < 60; x++) {
    mask[y * 100 + x] = 255;
  }
}
const dilated = g.dilateMask(mask, 100, 100, 5);
console.assert(dilated[35 * 100 + 35] === 255, 'dilateMask');

// 6. Contours
const loops = g.tracePixelContours(mask, 100, 100);
console.assert(loops.length > 0, 'tracePixelContours');
const extracted = g.extract_significant_outer_contours(mask, 100, 100);
console.assert(extracted.length > 0, 'extract_significant_outer_contours');

const simplified = g.simplify_contour(extracted[0], 0.15);
console.assert(simplified.length >= 3, 'simplify_contour');

const pdfPath = g.contour_to_pdf_path(simplified, 100);
console.assert(pdfPath.length === simplified.length, 'contour_to_pdf_path');

const unionPaths = g.mask_to_union_paths_pt(mask, 100, 100);
console.assert(unionPaths.length > 0, 'mask_to_union_paths_pt');

console.log('=== ALL TESTS PASSED ===');
process.exit(0);
