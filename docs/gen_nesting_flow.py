#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a self-contained HTML flowchart of the illupdf nesting pipeline."""
import html

FONT = "Pretendard, -apple-system, 'Segoe UI', 'Noto Sans KR', 'Malgun Gothic', sans-serif"

STYLES = {
    "input":    {"fill": "#f0fdf4", "stroke": "#16a34a", "title": "#14532d", "text": "#3f6212"},
    "parse":    {"fill": "#eff6ff", "stroke": "#3b82f6", "title": "#1e3a8a", "text": "#334155"},
    "pack":     {"fill": "#fffbeb", "stroke": "#d97706", "title": "#92400e", "text": "#78350f"},
    "sheet":    {"fill": "#f5f3ff", "stroke": "#8b5cf6", "title": "#4c1d95", "text": "#5b21b6"},
    "render":   {"fill": "#fce7f3", "stroke": "#db2777", "title": "#831843", "text": "#9d174d"},
    "decision": {"fill": "#fff7ed", "stroke": "#ea580c", "title": "#9a3412", "text": "#7c2d12"},
    "legend":   {"fill": "#f8fafc", "stroke": "#64748b", "title": "#334155", "text": "#475569"},
}

# id -> (kind, title, lines)
NODES = {
    "n1":  ("input", "CLI — illupdf nest <입력>",
            ["--preset stand|keyring|mixed · --count · --width/--height",
             "--beam-width · --row-candidate-limit · --pair-precompute",
             "--pair-precompute-workers · --search-mode · --emit"]),
    "n2":  ("input", "입력 수집 (inputs.py)",
            ["디렉터리 재귀 스캔 · sheet_* / _raster/_white/_outline 제외",
             "counts.json/csv (illupdf-counts-v1) → PDF별 수량 확장"]),
    "n3":  ("parse", "소스 파싱 (pdf_parser.py)",
            ["pikepdf로 OutlineForm 경로 추출 (없으면 페이지 콘텐츠 파싱)",
             "회전 후보: long side 세로 기준 → [0,180] 또는 [90,270]",
             "safety 오프셋 0.125mm (geometry.py)"]),
    "d1":  ("decision", "preset == 'mixed' ?", []),
    "n4":  ("pack", "키링 2개 → 스탠드형 수직 조합 (packer.py)",
            ["_build_mixed_composites · 8케이스(2순서×2회전×2회전)",
             "폭 최소 우선 · ROW_GAP 0.25mm 이격"]),
    "n5":  ("pack", "행 생성 — build_best_row (beam search)",
            ["빔 상태 beam_width개 유지 · 후보 풀 row_candidate_limit",
             "pair-offset 사전 계산(hybrid 이진탐색 / linear, 캐시)",
             "병렬화: pair_precompute_workers>1 & jobs≥16 → ProcessPool"]),
    "n6":  ("pack", "배치/충돌 검사",
            ["candidate_x_from_pair_constraints → 쌍 오프셋 결합",
             "candidate_collides_with_row: Shapely prep + bbox 선필터"]),
    "n7":  ("sheet", "시트 조립 — build_sheets",
            ["행 적층: keyring 고정 Y그리드(65+0.25) / stand·mixed 2행",
             "keyring: 0.5, 65.75, 131.0, 196.25... (4행/시트)"]),
    "d2":  ("decision", "preset in (stand, mixed) ?", []),
    "n8":  ("sheet", "받침대(베이스) 최대 배치",
            ["9개 정렬 조합(left/center/right)² → base 개수 최대 선택",
             "fill_row/sheet_with_bases_shapely: 35×35mm 필링",
             "clearance 0.25mm · 크롭마크 회피 · step 5mm"]),
    "n9":  ("render", "렌더링 (renderer.py)",
            ["시트 PDF + White/Raster/Outline 분리 PDF 4종",
             "Form XObject 캐시 + 회전 행렬 + OCG 레이어",
             "4모서리 10×10mm 초록 크롭마크"]),
}

# 순서 (main spine): n1 n2 n3 d1 n5 n6 n7 d2 n9
MAIN_SEQ = ["n1", "n2", "n3", "d1", "n5", "n6", "n7", "d2", "n9"]
LEFT_SEQ = ["n4"]    # mixed branch
RIGHT_SEQ = ["n8"]   # stand/mixed bases branch

ARROWS = [
    ("n1", "n2", "", "b", "t", False),
    ("n2", "n3", "", "b", "t", False),
    ("n3", "d1", "", "b", "t", False),
    ("d1", "n4", "예 — mixed", "l", "r", False),
    ("d1", "n5", "아니오", "b", "t", False),
    ("n4", "n5", "조합된 인스턴스", "r", "l", False),
    ("n5", "n6", "", "b", "t", False),
    ("n6", "n7", "행 누적 → 시트", "b", "t", False),
    ("n7", "d2", "", "b", "t", False),
    ("d2", "n8", "예 — 받침대 평가", "r", "l", False),
    ("d2", "n9", "아니오 — keyring은 바로 렌더", "b", "t", False),
    ("n8", "n9", "베이스 포함 시트", "r", "l", False),
]

BANDS = [
    ("band1", "① CLI · 입력 수집", "파일/디렉터리 → counts 수량 확장", "#16a34a", ["n1", "n2"]),
    ("band2", "② 소스 파싱", "OutlineForm 경로 + 회전/안전오프셋", "#3b82f6", ["n3"]),
    ("band3", "③ 행 배치 (빔서치 + pair-offset)", "혼합 조합 / 행 생성 / 충돌 검사", "#d97706", ["d1", "n4", "n5", "n6"]),
    ("band4", "④ 시트 조립", "행 적층 + 받침대 최대 배치", "#8b5cf6", ["n7", "d2", "n8"]),
    ("band5", "⑤ 렌더링", "시트 + 분리 레이어 PDF 4종", "#db2777", ["n9"]),
]

# columns
COL_X = {"main": 560, "left": 60, "right": 1060}
COL_W = {"main": 420, "left": 380, "right": 380}
GAP = 26
TOP = 110

def box_height(kind, lines):
    if kind == "decision":
        return 52
    return 44 + 17 * len(lines)

def compute_rects():
    rects = {}
    for col, seq in (("main", MAIN_SEQ), ("left", LEFT_SEQ), ("right", RIGHT_SEQ)):
        y = TOP if col == "main" else 360
        for nid in seq:
            kind = NODES[nid][0]
            lines = NODES[nid][2]
            h = box_height(kind, lines)
            rects[nid] = (COL_X[col], y, COL_W[col], h)
            y += h + GAP
    return rects

def est_width(text, size):
    w = 0.0
    for ch in str(text):
        if ord(ch) > 0x2E7F:
            w += size * 1.02
        elif ch == " ":
            w += size * 0.32
        elif ch.isdigit():
            w += size * 0.56
        else:
            w += size * 0.55
    return w

def esc(s):
    return html.escape(str(s))

def node_svg(rects):
    out = []
    for nid, (kind, title, lines) in NODES.items():
        x, y, w, h = rects[nid]
        st = STYLES[kind]
        if kind == "decision":
            inset = 20
            pts = f"{x+inset},{y} {x+w-inset},{y} {x+w},{y+h//2} {x+w-inset},{y+h} {x+inset},{y+h} {x},{y+h//2}"
            out.append(f'<polygon points="{pts}" fill="{st["fill"]}" stroke="{st["stroke"]}" stroke-width="2"/>')
            out.append(f'<text x="{x+w/2:.1f}" y="{y+h/2+5:.1f}" text-anchor="middle" font-size="15" font-weight="700" fill="{st["title"]}" font-family="{FONT}">{esc(title)}</text>')
        else:
            out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{st["fill"]}" stroke="{st["stroke"]}" stroke-width="2"/>')
            out.append(f'<text x="{x+w/2:.1f}" y="{y+22:.1f}" text-anchor="middle" font-size="15.5" font-weight="700" fill="{st["title"]}" font-family="{FONT}">{esc(title)}</text>')
            yc = y + 44
            for line in lines:
                out.append(f'<text x="{x+w/2:.1f}" y="{yc:.1f}" text-anchor="middle" font-size="12.5" fill="{st["text"]}" font-family="{FONT}">{esc(line)}</text>')
                yc += 17
    return "".join(out)

def anchor(rects, a, fs, b, ts):
    ax, ay, aw, ah = rects[a]
    bx, by, bw, bh = rects[b]
    if fs == "b":  x1, y1 = ax + aw / 2, ay + ah
    elif fs == "t": x1, y1 = ax + aw / 2, ay
    elif fs == "l": x1, y1 = ax, ay + ah / 2
    else:           x1, y1 = ax + aw, ay + ah / 2
    if ts == "t":  x2, y2 = bx + bw / 2, by
    elif ts == "b": x2, y2 = bx + bw / 2, by + bh
    elif ts == "l": x2, y2 = bx, by + bh / 2
    else:           x2, y2 = bx + bw, by + bh / 2
    col = STYLES[NODES[a][0]]["stroke"]
    return x1, y1, x2, y2, col

def arrow_svg(rects):
    out = []
    for (a, b, label, fs, ts, dashed) in ARROWS:
        x1, y1, x2, y2, col = anchor(rects, a, fs, b, ts)
        dash = ' stroke-dasharray="7,5"' if dashed else ""
        out.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{col}" stroke-width="2"{dash} marker-end="url(#arr-{col.replace("#","")})"/>')
        if label:
            if x1 == x2:
                out.append(f'<text x="{x1-12:.1f}" y="{(y1+y2)/2:.1f}" text-anchor="end" font-size="12" font-weight="600" fill="{col}" font-family="{FONT}">{esc(label)}</text>')
            else:
                out.append(f'<text x="{(x1+x2)/2:.1f}" y="{min(y1,y2)-9:.1f}" text-anchor="middle" font-size="12" font-weight="600" fill="{col}" font-family="{FONT}">{esc(label)}</text>')
    return "".join(out)

def band_svg(rects):
    out = []
    for key, title, sub, color, members in BANDS:
        ys = [rects[m][1] for m in members]
        ye = [rects[m][1] + rects[m][3] for m in members]
        y0 = min(ys) - 14
        y1 = max(ye) + 14
        out.append(f'<rect x="24" y="{y0}" width="4" height="{y1-y0}" rx="2" fill="{color}"/>')
        out.append(f'<text x="36" y="{y0+20}" font-size="13.5" font-weight="700" fill="{color}" font-family="{FONT}">{esc(title)}</text>')
        out.append(f'<text x="36" y="{y0+38}" font-size="11.5" fill="#64748b" font-family="{FONT}">{esc(sub)}</text>')
    return "".join(out)

def build_svg():
    rects = compute_rects()
    ymax = max(r[1] + r[3] for r in rects.values())
    W, H = 1460, max(1100, ymax + 80)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
    out.append("<defs>")
    seen = set()
    for st in STYLES.values():
        col = st["stroke"].replace("#", "")
        if col in seen:
            continue
        seen.add(col)
        out.append(f'<marker id="arr-{col}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                   f'<path d="M 0 1 L 10 5 L 0 9 z" fill="{st["stroke"]}"/></marker>')
    out.append("</defs>")
    out.append(band_svg(rects))
    out.append(node_svg(rects))
    out.append(arrow_svg(rects))
    out.append("</svg>")
    return "".join(out), W, H

def check_widths():
    node_col = {nid: col for col, seq in (("main", MAIN_SEQ), ("left", LEFT_SEQ), ("right", RIGHT_SEQ)) for nid in seq}
    issues = []
    for nid, (kind, title, lines) in NODES.items():
        w = COL_W[node_col[nid]]
        if kind == "decision":
            if est_width(title, 15) > w - 60:
                issues.append(f"{nid}: {est_width(title,15):.0f}px")
        else:
            if est_width(title, 15.5) > w - 24:
                issues.append(f"{nid}: title {est_width(title,15.5):.0f}px > {w-24}")
            for line in lines:
                if est_width(line, 12.5) > w - 18:
                    issues.append(f"{nid}: line {est_width(line,12.5):.0f}px > {w-18}")
    return issues

def build_html():
    svg, W, H = build_svg()
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>illupdf — 네스팅 파이프라인 플로우 차트</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #f1f5f9; font-family: {FONT}; color: #0f172a; padding: 24px; }}
  .wrap {{ max-width: 1500px; margin: 0 auto; }}
  header {{ display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; margin-bottom: 14px; }}
  h1 {{ font-size: 22px; font-weight: 800; color: #0f172a; }}
  header .sub {{ font-size: 13.5px; color: #64748b; }}
  .legend {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 16px; }}
  .legend span {{ font-size: 12.5px; padding: 5px 12px; border-radius: 999px; border: 1.5px solid; font-weight: 600; }}
  .note {{ font-size: 13px; color: #475569; line-height: 1.8; margin-bottom: 16px; padding: 12px 16px; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; }}
  .note code {{ background: #f1f5f9; padding: 1px 6px; border-radius: 5px; font-size: 12px; }}
  .flowcard {{ background: #ffffff; border: 1px solid #e2e8f0; border-radius: 16px; padding: 12px; overflow-x: auto; }}
  .flowcard svg {{ display: block; width: 100%; height: auto; min-width: 1100px; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>illupdf — 네스팅(nest) 파이프라인 플로우 차트</h1>
    <div class="sub">src/illupdf/nesting/engine/row_packing · master 기준</div>
  </header>
  <div class="legend">
    <span style="background:#f0fdf4;border-color:#16a34a;color:#14532d">입력/수집</span>
    <span style="background:#eff6ff;border-color:#3b82f6;color:#1e3a8a">소스 파싱</span>
    <span style="background:#fffbeb;border-color:#d97706;color:#92400e">행 배치</span>
    <span style="background:#f5f3ff;border-color:#8b5cf6;color:#4c1d95">시트 조립</span>
    <span style="background:#fce7f3;border-color:#db2777;color:#831843">렌더링</span>
  </div>
  <div class="note">
    <b>핵심 파라미터:</b>
    <code>시트 400×300mm</code> · <code>마진 0.5mm</code> · <code>행간 0.25mm</code> ·
    <code>safety 0.125mm</code> · <code>keyring 65mm / stand 148mm</code> ·
    <code>beam-width 8</code> · <code>row-candidate-limit 60</code> ·
    <code>pair-offset hybrid(이진탐색)</code> · <code>받침대 35×35mm, clearance 0.25mm</code>
  </div>
  <div class="flowcard">
    {svg}
  </div>
</div>
</body>
</html>
"""

if __name__ == "__main__":
    issues = check_widths()
    if issues:
        print("OVERFLOW:")
        for i in issues:
            print("  -", i)
        raise SystemExit(1)
    with open("/home/user/repo/nesting_flow.html", "w", encoding="utf-8") as f:
        f.write(build_html())
    print("written: /home/user/repo/nesting_flow.html")
