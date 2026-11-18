from __future__ import annotations
from typing import Iterable, Sequence
from .polygons import GEOMETRY_EPSILON_PT
from ..models import Point

def horizontal_line_intersections(path_pt: Sequence[Point], y_pt: float) -> list[float]:
    intersections: list[float] = []
    for index, (x1, y1) in enumerate(path_pt):
        x2, y2 = path_pt[(index + 1) % len(path_pt)]
        if abs(y1 - y2) <= GEOMETRY_EPSILON_PT:
            if abs(y_pt - y1) <= GEOMETRY_EPSILON_PT:
                intersections.extend([x1, x2])
            continue
        if y_pt < min(y1, y2) - GEOMETRY_EPSILON_PT or y_pt > max(y1, y2) + GEOMETRY_EPSILON_PT:
            continue
        t = (y_pt - y1) / (y2 - y1)
        if -GEOMETRY_EPSILON_PT <= t <= 1.0 + GEOMETRY_EPSILON_PT:
            intersections.append(x1 + t * (x2 - x1))
    return dedupe_numeric_values(intersections)


def vertical_line_intersections(path_pt: Sequence[Point], x_pt: float) -> list[float]:
    intersections: list[float] = []
    for index, (x1, y1) in enumerate(path_pt):
        x2, y2 = path_pt[(index + 1) % len(path_pt)]
        if abs(x1 - x2) <= GEOMETRY_EPSILON_PT:
            if abs(x_pt - x1) <= GEOMETRY_EPSILON_PT:
                intersections.extend([y1, y2])
            continue
        if x_pt < min(x1, x2) - GEOMETRY_EPSILON_PT or x_pt > max(x1, x2) + GEOMETRY_EPSILON_PT:
            continue
        t = (x_pt - x1) / (x2 - x1)
        if -GEOMETRY_EPSILON_PT <= t <= 1.0 + GEOMETRY_EPSILON_PT:
            intersections.append(y1 + t * (y2 - y1))
    return dedupe_numeric_values(intersections)


def topmost_intersection_y(path_pt: Sequence[Point], x_pt: float) -> float:
    from ..errors import PdfGenerationError
    intersections = vertical_line_intersections(path_pt, x_pt)
    if not intersections:
        raise PdfGenerationError("the keyring balance axis does not intersect the outline")
    return max(intersections)


def dedupe_numeric_values(values: Iterable[float], tolerance: float = GEOMETRY_EPSILON_PT) -> list[float]:
    deduped: list[float] = []
    for value in sorted(values):
        if not deduped or abs(value - deduped[-1]) > tolerance:
            deduped.append(value)
    return deduped
