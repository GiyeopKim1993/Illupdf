# JS ↔ Python 변환 대조 결과

브라우저 포팅(`js/`)이 Python 원본(`src/illupdf/`)과 얼마나 일치하는지, 실제 캐릭터 이미지
8장으로 측정한 기록입니다. **Python 결과를 항상 정답으로 둡니다.**

- 측정 대상: `final_outline_long_side_mm` (완성 아웃라인의 긴 변, mm)
- 아티팩트: 원신/블루아카 캐릭터 8장 (RGBA, 투명 배경 51~73%)
- 프리셋: `keyring`(아크릴 키링, 기본 65mm) / `stand`(아크릴 스탠드, 기본 148mm)

## 결과

| 아티팩트 | 프리셋 | JS (mm) | Python (mm) | \|차이\| (mm) |
|---|---|---:|---:|---:|
| art1 | keyring | 65.845972 | 65.845972 | 0.000000000 |
| art1 | stand | 147.792369 | 147.792369 | 0.000000000 |
| art2 | keyring | 65.898536 | 65.898536 | 0.000000111 |
| art2 | stand | 147.853047 | 147.853047 | 0.000000222 |
| art3 | keyring | 65.165817 | 65.165817 | 0.000000000 |
| art3 | stand | 147.884797 | 147.884797 | 0.000000000 |
| art4 | keyring | 65.261419 | 65.261419 | 0.000000000 |
| art4 | stand | 147.973344 | 147.973344 | 0.000000000 |
| art5 | keyring | 65.304811 | 65.304811 | 0.000000000 |
| art5 | stand | 147.314003 | 147.314003 | 0.000000000 |
| art6 | keyring | 65.281175 | 65.281175 | 0.000000000 |
| art6 | stand | 147.535547 | 147.535547 | 0.000000000 |
| art7 | keyring | 65.778592 | 65.778239 | **0.000352778** |
| art7 | stand | 147.898556 | 147.898556 | 0.000000000 |
| art8 | keyring | 65.218028 | 65.218028 | 0.000000000 |
| art8 | stand | 147.984986 | 147.984986 | 0.000000000 |

**최대 오차 0.000352778 mm · 16조합 중 13조합 완전 일치 · 15조합 1µm 이내**

PDF 페이지 높이도 16개 중 15개가 Python 과 소수점 6자리까지 일치합니다
(art7-keyring 만 0.001mm 차이).

## 중간 단계 일치도 (art7 / keyring 기준)

| 단계 | JS | Python | 판정 |
|---|---|---|---|
| 리샘플(Pillow LANCZOS) | — | — | **비트 단위 일치** |
| `build_custom_mask` (모폴로지) | — | — | **픽셀 단위 일치** (40.2M px, diff 0) |
| `findContours` 정점 | 34,870점 | 34,870점 | **원소 단위 일치** (순서 포함) |
| `approxPolyDP` (eps=9px) | 154·4·4·4·4 | 154·4·4·4·4 | **정점 순서까지 일치** |
| `build_base_outline_path` | 5,314점 | 5,317점 | 3점 차이 (ymin 0.804 vs 0.805) |
| 최종 결과 | 65.778592 | 65.778239 | 0.000353mm |

## 남은 차이의 원인

`build_base_outline_path` 의 아일랜드 부착(union) 단계에서 Clipper 가
**공선점(collinear point) 1개를 남기거나 지우는지**가 미세하게 다릅니다.
양쪽 모두 Clipper 6.4.2(pyclipper 6.4.2 / vendor/clipper.js 6.4.2.2)를 쓰지만
JS 포팅본과 C++ 원본의 부동소수 처리 순서가 달라 경계에서 갈립니다.

- 이 1 unit(Clipper 스케일 1000 기준 0.001pt)이 아웃라인 offset 을 거치며
  최종 0.000353mm(0.35µm)로 나타납니다.
- 인쇄·재단 공차(수십 µm)보다 **2자리 이상 작은 값**이라 실사용 영향은 없습니다.

## 검증 방법

```bash
# JS 단위 테스트
node tests/test_geometry.js
# JS ↔ Python parity (12개, cv2 approxPolyDP/컨투어 순서 포함)
node tests/test_parity.js
```

Python 쪽 재현:

```bash
PYTHONPATH=/tmp/pylibs:src python3 - <<'EOF'
from illupdf.converting.engine.pipeline import prepare_artwork, final_outline_long_side_mm
p = prepare_artwork(input_path='art1.png', preset_name='keyring', size_mm=None, observer=None)
print(final_outline_long_side_mm(p))
EOF
```

## 미리보기

`preview/` 폴더에 16개 PDF 를 120dpi 로 렌더한 PNG 가 있습니다
(`art{1..8}_{keyring,stand}.png`). PDF 원본은 용량(스탠드 1개 18~24MB) 때문에
저장소에 넣지 않았습니다.
