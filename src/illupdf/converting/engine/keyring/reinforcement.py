from __future__ import annotations
from typing import Sequence, Iterable
from ..geometry.clipping import union_paths, cleanup_minimum_gap
from ..geometry.polygons import (
    approximate_circle_path,
    signed_path_area,
    closest_points_between_paths,
    closest_points_between_path_sets,
    run_polyline_length,
    keep_largest_path_only,
    point_distance
)
from ..geometry.intersections import topmost_intersection_y
from ..models import Point, PathPoints
from ..units import mm_to_pt

KEYRING_BOSS_RADIUS_MM = 3.5
KEYRING_NECK_WIDTH_MM = 8.0
KEYRING_BOSS_MINIMUM_GAP_MM = 5.0
KEYRING_BOSS_GAP_MIN_DEPTH_MM = 1.0
KEYRING_BOSS_GAP_MIN_SPAN_MM = 1.5
ISLAND_ATTACHMENT_WIDTH_MM = 3.5
MINIMUM_CUTTABLE_GAP_MM = 1.0
OUTLINE_EXPAND_MM = 5.0
OUTLINE_SHRINK_MM = 3.0


def build_capsule_bridge_shapes(
    start_pt: Point,
    end_pt: Point,
    width_pt: float,
) -> list[PathPoints]:
    from ..errors import PdfGenerationError
    from ..geometry.polygons import GEOMETRY_EPSILON_PT
    import math
    if width_pt <= 0:
        raise PdfGenerationError("bridge width must be positive")
    dx = end_pt[0] - start_pt[0]
    dy = end_pt[1] - start_pt[1]
    segment_length = math.hypot(dx, dy)
    half_width_pt = width_pt / 2.0
    if segment_length <= GEOMETRY_EPSILON_PT:
        return [approximate_circle_path(start_pt, half_width_pt)]

    ux = dx / segment_length
    uy = dy / segment_length
    nx = -uy
    ny = ux
    rectangle_path_pt = [
        (start_pt[0] + nx * half_width_pt, start_pt[1] + ny * half_width_pt),
        (end_pt[0] + nx * half_width_pt, end_pt[1] + ny * half_width_pt),
        (end_pt[0] - nx * half_width_pt, end_pt[1] - ny * half_width_pt),
        (start_pt[0] - nx * half_width_pt, start_pt[1] - ny * half_width_pt),
    ]
    return [
        rectangle_path_pt,
        approximate_circle_path(start_pt, half_width_pt),
        approximate_circle_path(end_pt, half_width_pt),
    ]


def build_boss_gap_bridge_shapes(
    base_outer_path_pt: PathPoints,
    boss_path_pt: PathPoints,
    hole_center_pt: Point,
) -> list[PathPoints]:
    threshold_pt = mm_to_pt(KEYRING_BOSS_MINIMUM_GAP_MM)
    minimum_depth_pt = mm_to_pt(KEYRING_BOSS_GAP_MIN_DEPTH_MM)
    minimum_span_pt = mm_to_pt(KEYRING_BOSS_GAP_MIN_SPAN_MM)
    bridge_shapes_pt: list[PathPoints] = []
    lower_boss_points_pt = [
        point_pt
        for point_pt in boss_path_pt
        if point_pt[1] <= hole_center_pt[1] - minimum_depth_pt
    ]
    if not lower_boss_points_pt:
        return bridge_shapes_pt

    for is_left_side in (True, False):
        side_points_pt = [
            point_pt
            for point_pt in lower_boss_points_pt
            if (point_pt[0] <= hole_center_pt[0] if is_left_side else point_pt[0] >= hole_center_pt[0])
        ]
        if not side_points_pt:
            continue
        runs = build_close_gap_runs(side_points_pt, base_outer_path_pt, threshold_pt)
        if not runs:
            continue
        longest_run = max(runs, key=lambda run: run_polyline_length([record[0] for record in run]))
        if run_polyline_length([record[0] for record in longest_run]) < minimum_span_pt:
            continue
        boss_side_pt, body_side_pt, gap_distance_pt = min(longest_run, key=lambda record: record[2])
        if body_side_pt[1] > hole_center_pt[1] - minimum_depth_pt:
            continue
        bridge_shapes_pt.extend(
            build_capsule_bridge_shapes(body_side_pt, boss_side_pt, threshold_pt)
        )
    return bridge_shapes_pt


def build_close_gap_runs(
    source_points_pt: Sequence[Point],
    target_path_pt: Sequence[Point],
    gap_threshold_pt: float,
) -> list[list[tuple[Point, Point, float]]]:
    records: list[tuple[Point, Point, float]] = []
    for source_pt in source_points_pt:
        _, target_pt, distance_pt = closest_points_between_path_sets([source_pt], target_path_pt)
        records.append((source_pt, target_pt, distance_pt))

    runs: list[list[tuple[Point, Point, float]]] = []
    current_run: list[tuple[Point, Point, float]] = []
    for record in records:
        if record[2] < gap_threshold_pt:
            current_run.append(record)
            continue
        if current_run:
            runs.append(current_run)
            current_run = []
    if current_run:
        runs.append(current_run)
    return runs


def attach_island_to_outline(
    main_path_pt: PathPoints,
    island_path_pt: PathPoints,
    bridge_width_pt: float,
) -> PathPoints:
    from ..errors import PdfGenerationError
    main_anchor_pt, island_anchor_pt = closest_points_between_paths(main_path_pt, island_path_pt)
    bridge_shapes_pt = build_capsule_bridge_shapes(main_anchor_pt, island_anchor_pt, bridge_width_pt)
    unified_paths_pt = union_paths([main_path_pt, island_path_pt, *bridge_shapes_pt])
    if not unified_paths_pt:
        raise PdfGenerationError("island attachment produced an empty outline")
    cleaned_paths_pt = cleanup_minimum_gap(unified_paths_pt, MINIMUM_CUTTABLE_GAP_MM)
    if not cleaned_paths_pt:
        raise PdfGenerationError("island cleanup produced an empty outline")
    return cleaned_paths_pt[0]


def build_base_outline_path(
    contour_paths_pt: Sequence[PathPoints],
) -> PathPoints:
    from ..errors import PdfGenerationError
    if not contour_paths_pt:
        raise PdfGenerationError("base outline generation did not receive any contours")
    ordered_paths_pt = sorted(contour_paths_pt, key=lambda path: abs(signed_path_area(path)), reverse=True)
    combined_path_pt = list(ordered_paths_pt[0])
    bridge_width_pt = mm_to_pt(ISLAND_ATTACHMENT_WIDTH_MM)
    for island_path_pt in ordered_paths_pt[1:]:
        combined_path_pt = attach_island_to_outline(combined_path_pt, island_path_pt, bridge_width_pt)
    return combined_path_pt


def build_reinforced_keyring_outer_path(
    base_outer_path_pt: PathPoints,
    hole_center_pt: Point,
    boss_radius_pt: float,
) -> tuple[PathPoints, PathPoints]:
    from ..errors import PdfGenerationError
    boss_path_pt = approximate_circle_path(hole_center_pt, boss_radius_pt)
    body_anchor_y_pt = topmost_intersection_y(base_outer_path_pt, hole_center_pt[0])
    neck_bridge_shapes_pt = build_capsule_bridge_shapes(
        start_pt=(hole_center_pt[0], body_anchor_y_pt),
        end_pt=hole_center_pt,
        width_pt=mm_to_pt(KEYRING_NECK_WIDTH_MM),
    )
    gap_bridge_shapes_pt = build_boss_gap_bridge_shapes(
        base_outer_path_pt,
        boss_path_pt,
        hole_center_pt,
    )
    candidate_outer_paths_pt = keep_largest_path_only(
        union_paths([base_outer_path_pt, boss_path_pt, *neck_bridge_shapes_pt, *gap_bridge_shapes_pt])
    )
    if not candidate_outer_paths_pt:
        raise PdfGenerationError("keyring boss reinforcement produced an empty outline")
    cleaned_outer_paths_pt = cleanup_minimum_gap(
        candidate_outer_paths_pt,
        max(MINIMUM_CUTTABLE_GAP_MM, KEYRING_BOSS_MINIMUM_GAP_MM),
    )
    if not cleaned_outer_paths_pt:
        raise PdfGenerationError("keyring minimum-gap cleanup produced an empty outline")
    return cleaned_outer_paths_pt[0], boss_path_pt
