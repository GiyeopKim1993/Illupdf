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

console.log(`\n${passed} passed, ${failed} failed`);
console.log(failed === 0 ? '=== ALL PARITY TESTS PASSED ===' : '=== PARITY TESTS FAILED ===');
process.exit(failed === 0 ? 0 : 1);
