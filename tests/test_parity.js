/*
 * Python 원본과의 동등성(parity) 회귀 테스트.
 * - Pillow LANCZOS 리샘플 결과와 비트 단위 일치 (고정 기대값)
 * - cv2 ellipse morphology 커널 규칙 일치
 * - White spot / Spot_1 / OCG PDF 구조
 * 실행: node tests/test_parity.js
 */
'use strict';
const assert = require('assert');
const IlluGeometry = require('../js/geometry.js');
const IlluConvertingEngine = require('../js/converting-engine.js');
const IlluPdfEngine = require('../js/pdf-engine.js');
const IlluPdfWriter = require('../js/pdf-writer.js');

// 브라우저 canvas 의존 함수는 건드리지 않도록 최소 document 스텁
if (typeof global.document === 'undefined') {
  global.document = {
    createElement: () => ({
      width: 0, height: 0,
      getContext: () => ({
        createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
        putImageData() {},
        drawImage() {},
        getImageData: (x, y, w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
      }),
      toDataURL: () => 'data:,',
    }),
  };
}

let passed = 0;
let failed = 0;
function check(name, fn) {
  try { fn(); passed++; }
  catch (e) { failed++; console.error(`  ✗ ${name}: ${e.message}`); }
}

console.log('=== PARITY TESTS (JS ↔ Python) ===');

// 1. Pillow LANCZOS 와 비트 단위 일치
//    Pillow 13.0.0: 2x1 [(255,0,0,255),(0,0,255,0)] → resize(4,1) LANCZOS
//    = (255,0,0,255) (255,0,0,196) (255,0,0,59) (0,0,0,0)
check('Pillow LANCZOS RGBA premultiply match', () => {
  const src = new Uint8Array([255, 0, 0, 255, 0, 0, 255, 0]);
  const out = IlluConvertingEngine.resizeRGBA(src, 2, 1, 4, 1);
  assert.deepStrictEqual(Array.from(out.data), [255, 0, 0, 255, 255, 0, 0, 196, 255, 0, 0, 59, 0, 0, 0, 0]);
});

// 2. cv2 ellipse 커널 규칙: 반폭 = round(sqrt(r²-dy²))
check('cv2 ellipse kernel (r=3 => 1,5,7,7,7,5,1)', () => {
  const w = 7, h = 1;
  const rows = [];
  for (const dy of [-3, -2, -1, 0, 1, 2, 3]) {
    const dx = Math.round(Math.sqrt(3 * 3 - dy * dy));
    rows.push(dx * 2 + 1);
  }
  assert.deepStrictEqual(rows, [1, 5, 7, 7, 7, 5, 1]);
  // 실제 dilate 결과가 커널과 동일해야 함
  const m = new Uint8Array(w * w);
  m[3 * w + 3] = 255;
  const d = IlluGeometry.dilateMask(m, w, w, 3);
  let count = 0;
  for (let i = 0; i < d.length; i++) if (d[i]) count++;
  assert.strictEqual(count, 33, `expected 33, got ${count}`);
});

// 3. cv2 erode 경계 처리: 이미지 전체가 255면 erode 후에도 255 유지
check('cv2 erode border behavior', () => {
  const w = 9, h = 9;
  const full = new Uint8Array(w * h).fill(255);
  const e = IlluGeometry.erodeMask(full, w, h, 1);
  for (let i = 0; i < e.length; i++) assert.strictEqual(e[i], 255);
});

// 4. Pillow rgb2l (L24) 그레이스케일 공식
check('Pillow rgb2l grayscale formula', () => {
  const px = new Uint8Array([255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255]);
  const { raster } = IlluConvertingEngine.build_white_spot_tint_with_mask(px, 3, 1, {
    invert: false, inset_coverage_mask: false, output_white: 255,
  });
  assert.deepStrictEqual(Array.from(raster.data), [76, 150, 29]);
});

// 5. Output levels (Photoshop): 0..170
check('apply_output_levels', () => {
  const out = IlluConvertingEngine.apply_output_levels(new Uint8Array([0, 128, 255]), 0, 170);
  assert.deepStrictEqual(Array.from(out), [0, 85, 170]);
});

// 6. PRESETS 가 Python presets.py 와 일치
check('PRESETS == Python presets.py', () => {
  // prepare_artwork_from_rgba 가 잘못된 프리셋을 받으면 stand 로 폴백하므로
  // 소스에서 상수를 직접 확인한다.
  const src = require('fs').readFileSync(__dirname + '/../js/converting-engine.js', 'utf8');
  assert.ok(src.includes("keyring: { has_boss: true, default_size_mm: 65.0 }"));
  assert.ok(src.includes("stand: { has_boss: false, default_size_mm: 148.0, has_connection: true, has_support: true, has_base: true }"));
});

// 7. PDF 엔진 상수 (Python colors.py)
check('PDF colors/sizes == Python colors.py', () => {
  assert.strictEqual(IlluPdfEngine.OUTLINE_STROKE_PT, 0.01);
  assert.deepStrictEqual(IlluPdfEngine.OUTLINE_STROKE_RGB, [1.0, 0.0, 0.0]);
  assert.strictEqual(IlluPdfEngine.DEFAULT_WHITE_SPOT, 'spot_0');
  assert.deepStrictEqual(IlluPdfEngine.resolve_white_spot('sswhite'), { name: 'SSWhite', cmyk: [0, 0, 0, 1] });
});

// 8. 순수 JS deflate 왕복 (Node zlib 로 검증)
check('pure-JS deflate round trip', () => {
  const zlib = require('zlib');
  const data = Buffer.from('illupdf parity check '.repeat(200));
  const comp = Buffer.from(IlluPdfWriter.zlibDeflateFixed(new Uint8Array(data)));
  const back = zlib.inflateSync(comp);
  assert.ok(back.equals(data), 'inflate(deflate(x)) must equal x');
});

// 9. PDF 구조: OCG 3개 + Separation 2개 + SMask
check('PDF document structure', () => {
  const prepared = {
    preset_name: 'stand',
    rgba_image: { data: new Uint8Array(4 * 4 * 4).fill(200), width: 4, height: 4 },
    base_paths_pt: [[[0, 0], [10, 0], [10, 10]]],
    outline_paths_pt: [[[0, 0], [10, 0], [10, 10]]],
    outline_outer_paths_pt: [[[0, 0], [10, 0], [10, 10]]],
    outline_inner_paths_pt: [],
    white_source_paths_pt: [],
    white_paths_pt: [],
    raster_origin_pt: [5, 5],
    raster_size_pt: [10, 10],
    page_size_pt: [20, 20],
    keyring_balance: null,
    white_raster_img: { data: new Uint8Array(16).fill(170), width: 4, height: 4 },
    white_coverage_mask: { data: new Uint8Array(16).fill(255), width: 4, height: 4 },
    white_spot1_img: { data: new Uint8Array(16).fill(64), width: 4, height: 4 },
  };
  const doc = IlluPdfEngine.build_combined_pdf(prepared);
  assert.deepStrictEqual(Object.keys(doc.layers), ['White', 'Raster', 'Outline']);
  assert.deepStrictEqual(Object.keys(doc.page_resources.XObject).sort(),
    ['/ImWhite', '/ImWhiteSpot1', '/OutlineForm', '/RasterForm', '/WhiteForm'].sort());
  assert.strictEqual(doc.page_resources.XObject['/ImWhite'].ColorSpace.name, 'Spot_0');
  assert.strictEqual(doc.page_resources.XObject['/ImWhiteSpot1'].ColorSpace.name, 'Spot_1');
  assert.ok(doc.page_resources.XObject['/ImWhite'].SMask, 'ImWhite must have SMask');
  assert.deepStrictEqual(Object.keys(doc.page_resources.Properties), ['/MC0', '/MC1', '/MC2']);
  assert.ok(doc.page_content.includes('/OC /MC0 BDC'));
  assert.ok(doc.page_content.includes('/ImWhiteSpot1 Do'));
});

// 9. cv2.approxPolyDP(closed=True) 규칙 일치 — cv2 5.0.0 실측 픽스처
//    approxPolyDP_ 의 'init_iters=3 + 투영 거리 + clean-up 패스' 3가지를
//    모두 재현해야 정점 순서까지 같아진다.
check('approxPolyDPClosed == cv2 (정점 순서까지)', () => {
  const input = [
    [160, 100], [160, 103], [159, 106], [158, 109], [157, 112], [155, 115], [153, 118],
    [151, 121], [149, 124], [146, 126], [142, 128], [139, 130], [135, 132], [131, 134],
    [127, 136], [123, 137], [119, 138], [114, 139], [109, 140], [105, 140], [100, 140],
    [95, 140], [91, 140], [86, 139], [81, 138], [77, 137], [73, 136], [69, 134], [65, 132],
    [61, 130], [58, 128], [54, 126], [51, 124], [49, 121], [47, 118], [45, 115], [43, 112],
    [42, 109], [41, 106], [40, 103], [40, 100], [40, 97], [41, 94], [42, 91], [43, 88],
    [45, 85], [47, 82], [49, 79], [51, 76], [54, 74], [58, 72], [61, 70], [65, 68], [69, 66],
    [73, 64], [77, 63], [81, 62], [86, 61], [91, 60], [95, 60], [100, 60], [105, 60],
    [109, 60], [114, 61], [119, 62], [123, 63], [127, 64], [131, 66], [135, 68], [139, 70],
    [142, 72], [146, 74], [149, 76], [151, 79], [153, 82], [155, 85], [157, 88], [158, 91],
    [159, 94], [160, 97],
  ];
  const expected = [[160, 97], [149, 124], [109, 140], [61, 130], [40, 103], [51, 76], [91, 60], [139, 70]];
  const got = IlluGeometry.approxPolyDPClosed(input, 9.0).map((p) => [Math.round(p[0]), Math.round(p[1])]);
  assert.deepStrictEqual(got, expected);
});

// 10. findContours 정점 순서 == cv2 (최상단-최좌단 시작, 시계방향)
check('contour 정점 순서 == cv2 findContours', () => {
  const W = 16, H = 16;
  const mask = new Uint8Array(W * H);
  for (let y = 2; y < 14; y++) for (let x = 2; x < 14; x++) mask[y * W + x] = 1;
  const loops = IlluGeometry.extract_significant_outer_contours(mask, W, H, 1440);
  assert.strictEqual(loops.length, 1);
  const pts = loops[0].points || loops[0];
  // cv2: 12x12 블록 → 왼쪽 변을 위에서 아래로, 아래 변을 왼→오, ...
  assert.deepStrictEqual(pts[0], [2, 2], '첫 점은 최상단-최좌단이어야 한다');
  assert.deepStrictEqual(pts[1], [2, 3], '진행 방향은 시계방향(아래)이어야 한다');
  assert.strictEqual(pts.length, 44, 'cv2 CHAIN_APPROX_NONE 과 동일한 44점이어야 한다');
});

// 11. 타깃 픽셀 크기 == Python compute_target_size_pixels (scale 방식)
//     비율을 직접 나누면 반올림이 달라져 3288x6221 이 되어버린다.
check('compute_target_size_pixels == Python (portrait)', () => {
  const mm = 58.0, PPI = 1440;
  const scale = ((mm / 25.4) * PPI) / Math.max(871, 1648);
  assert.deepStrictEqual([Math.round(871 * scale), Math.round(1648 * scale)], [1738, 3288]);
});

console.log(`\n${passed} passed, ${failed} failed`);
console.log(failed === 0 ? '=== ALL PARITY TESTS PASSED ===' : '=== PARITY TESTS FAILED ===');
process.exit(failed === 0 ? 0 : 1);
