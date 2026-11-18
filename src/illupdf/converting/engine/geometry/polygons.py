from __future__ import annotations
import math
from typing import Sequence, Iterable
from ..models import Point, PathPoints, TraceFrame, KeyringCandidate
from ..units import mm_to_pt, px_to_pt, dedupe_consecutive

GEOMETRY_EPSILON_PT = 1e-6
CIRCLE_SAGITTA_MM = 0.01


def signed_path_area(path_pt: Sequence[Point]) -> float:
    area = 0.0
    for index, (x1, y1) in enumerate(path_pt):
        x2, y2 = path_pt[(index + 1) % len(path_pt)]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def polygon_area_and_centroid(path_pt: Sequence[Point]) -> tuple[float, Point]:
    from ..errors import PdfGenerationError
    signed_area = signed_path_area(path_pt)
    if abs(signed_area) <= GEOMETRY_EPSILON_PT:
        raise PdfGenerationError("polygon area is too small to calculate a centroid")

    cx_acc = 0.0
    cy_acc = 0.0
    for index, (x1, y1) in enumerate(path_pt):
        x2, y2 = path_pt[(index + 1) % len(path_pt)]
        cross = x1 * y2 - x2 * y1
        cx_acc += (x1 + x2) * cross
        cy_acc += (y1 + y2) * cross
    cx = cx_acc / (6.0 * signed_area)
    cy = cy_acc / (6.0 * signed_area)
    return abs(signed_area), (cx, cy)


def translate_path(
    path_pt: Sequence[Point],
    offset_x: float,
    offset_y: float,
) -> PathPoints:
    return [(x + offset_x, y + offset_y) for x, y in path_pt]


def geometry_bounds(
    *,
    paths_groups: Iterable[Iterable[Sequence[Point]]] = (),
    points: Iterable[Point] = (),
) -> tuple[float, float, float, float]:
    from ..errors import PdfGenerationError
    xs: list[float] = []
    ys: list[float] = []
    for paths in paths_groups:
        for path in paths:
            for x, y in path:
                xs.append(x)
                ys.append(y)
    for x, y in points:
        xs.append(x)
        ys.append(y)
    if not xs or not ys:
        raise PdfGenerationError("geometry bounds could not be determined")
    return min(xs), min(ys), max(xs), max(ys)


def path_bounds(paths_pt: Iterable[Sequence[Point]]) -> tuple[float, float, float, float]:
    return geometry_bounds(paths_groups=(paths_pt,))


def combined_bounds(
    raster_size_pt: tuple[float, float],
    paths_groups: Iterable[Iterable[Sequence[Point]]],
) -> tuple[float, float, float, float]:
    raster_width_pt, raster_height_pt = raster_size_pt
    min_x = 0.0
    min_y = 0.0
    max_x = raster_width_pt
    max_y = raster_height_pt

    for paths in paths_groups:
        for path in paths:
            for x, y in path:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)

    return min_x, min_y, max_x, max_y


def keep_largest_path_only(
    paths_pt: Iterable[Sequence[Point]],
) -> list[PathPoints]:
    materialized_paths = [list(path) for path in paths_pt if len(path) >= 3]
    if not materialized_paths:
        return []
    largest_path = max(materialized_paths, key=lambda path: abs(signed_path_area(path)))
    return [largest_path]


def orient_path(
    path_pt: Sequence[Point],
    clockwise: bool,
) -> PathPoints:
    area = signed_path_area(path_pt)
    is_clockwise = area < 0
    if is_clockwise == clockwise:
        return list(path_pt)
    return list(reversed(path_pt))


def point_distance(point_a: Point, point_b: Point) -> float:
    return math.hypot(point_a[0] - point_b[0], point_a[1] - point_b[1])


def closest_points_between_paths(path_a_pt: Sequence[Point], path_b_pt: Sequence[Point]) -> tuple[Point, Point]:
    best_pair = (path_a_pt[0], path_b_pt[0])
    best_distance = point_distance(best_pair[0], best_pair[1])
    for point_a_pt in path_a_pt:
        for point_b_pt in path_b_pt:
            distance = point_distance(point_a_pt, point_b_pt)
            if distance < best_distance:
                best_pair = (point_a_pt, point_b_pt)
                best_distance = distance
    return best_pair


def closest_points_between_path_sets(
    source_points_pt: Sequence[Point],
    target_path_pt: Sequence[Point],
) -> tuple[Point, Point, float]:
    best_source_pt = source_points_pt[0]
    best_target_pt = target_path_pt[0]
    best_distance = point_distance(best_source_pt, best_target_pt)
    for source_pt in source_points_pt:
        for target_pt in target_path_pt:
            distance = point_distance(source_pt, target_pt)
            if distance < best_distance:
                best_source_pt = source_pt
                best_target_pt = target_pt
                best_distance = distance
    return best_source_pt, best_target_pt, best_distance


def run_polyline_length(points_pt: Sequence[Point]) -> float:
    if len(points_pt) < 2:
        return 0.0
    return sum(
        point_distance(points_pt[index], points_pt[index + 1])
        for index in range(len(points_pt) - 1)
    )


def approximate_circle_path(
    center_pt: Point,
    radius_pt: float,
    max_sagitta_pt: float | None = None,
) -> PathPoints:
    from ..errors import PdfGenerationError
    if max_sagitta_pt is None:
        max_sagitta_pt = mm_to_pt(CIRCLE_SAGITTA_MM)
    if radius_pt <= 0:
        raise PdfGenerationError("circle radius must be positive")
    if max_sagitta_pt <= 0 or max_sagitta_pt >= radius_pt:
        segment_count = 24
    else:
        angle = math.acos(max(-1.0, min(1.0, 1.0 - max_sagitta_pt / radius_pt)))
        segment_count = max(24, int(math.ceil(math.pi / max(angle, 1e-6))))
    cx_pt, cy_pt = center_pt
    return [
        (
            cx_pt + radius_pt * math.cos(2.0 * math.pi * index / segment_count),
            cy_pt + radius_pt * math.sin(2.0 * math.pi * index / segment_count),
        )
        for index in range(segment_count)
    ]


def extend_line_to_bbox(
    start_pt: Point,
    through_pt: Point,
    bbox_pt: tuple[float, float, float, float],
) -> tuple[Point, Point] | None:
    min_x, min_y, max_x, max_y = bbox_pt
    dx = through_pt[0] - start_pt[0]
    dy = through_pt[1] - start_pt[1]
    if abs(dx) <= GEOMETRY_EPSILON_PT and abs(dy) <= GEOMETRY_EPSILON_PT:
        return None

    points: list[tuple[float, float, float]] = []
    if abs(dx) > GEOMETRY_EPSILON_PT:
        for boundary_x in (min_x, max_x):
            t = (boundary_x - start_pt[0]) / dx
            y_value = start_pt[1] + t * dy
            if min_y - GEOMETRY_EPSILON_PT <= y_value <= max_y + GEOMETRY_EPSILON_PT:
                points.append((t, boundary_x, y_value))
    if abs(dy) > GEOMETRY_EPSILON_PT:
        for boundary_y in (min_y, max_y):
            t = (boundary_y - start_pt[1]) / dy
            x_value = start_pt[0] + t * dx
            if min_x - GEOMETRY_EPSILON_PT <= x_value <= max_x + GEOMETRY_EPSILON_PT:
                points.append((t, x_value, boundary_y))

    if len(points) < 2:
        return None

    points.sort(key=lambda item: item[0])
    first = points[0]
    last = points[-1]
    return ((first[1], first[2]), (last[1], last[2]))
