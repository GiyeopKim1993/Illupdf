from __future__ import annotations

PPI = 1440
MM_PER_INCH = 25.4
PT_PER_INCH = 72.0
WORKING_ALPHA_MARGIN_MM = 1.0


def mm_to_px(mm: float) -> int:
    return max(1, int(round(mm / MM_PER_INCH * PPI)))


def mm_to_pt(mm: float) -> float:
    return mm / MM_PER_INCH * PT_PER_INCH


def pt_to_mm(pt: float) -> float:
    return pt / PT_PER_INCH * MM_PER_INCH


def px_to_pt(px: float) -> float:
    return px / PPI * PT_PER_INCH


def dedupe_consecutive(points: Sequence[tuple[int, int]] | Sequence[tuple[float, float]]) -> list[tuple[int, int]] | list[tuple[float, float]]:
    deduped: list[tuple[int, int]] | list[tuple[float, float]] = []
    for point in points:
        if not deduped or deduped[-1] != point:
            deduped.append(tuple(point))
    if deduped and deduped[0] == deduped[-1]:
        deduped.pop()
    return deduped
