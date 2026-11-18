from __future__ import annotations
from typing import Sequence
from ..geometry.intersections import topmost_intersection_y, horizontal_line_intersections
from ..models import Point, PathPoints
from ..units import mm_to_pt

KEYRING_HOLE_RADIUS_MM = 1.5
KEYRING_BOSS_SLIDE_STEP_MM = 0.1
KEYRING_SIDE_CLEARANCE_THRESHOLD_MM = 0.75
INTERNAL_ITERATION_GUARD = 4096


def keyring_side_clearances(
    base_outer_path_pt: Sequence[Point],
    center_pt: Point,
) -> tuple[float | None, float | None]:
    from ..geometry.polygons import GEOMETRY_EPSILON_PT
    center_x_pt, center_y_pt = center_pt
    intersections = horizontal_line_intersections(base_outer_path_pt, center_y_pt)
    left_distances = [
        center_x_pt - intersection_x_pt
        for intersection_x_pt in intersections
        if intersection_x_pt < center_x_pt - GEOMETRY_EPSILON_PT
    ]
    right_distances = [
        intersection_x_pt - center_x_pt
        for intersection_x_pt in intersections
        if intersection_x_pt > center_x_pt + GEOMETRY_EPSILON_PT
    ]
    left_clearance_pt = min(left_distances) if left_distances else None
    right_clearance_pt = min(right_distances) if right_distances else None
    return left_clearance_pt, right_clearance_pt


def side_clearance_is_tight(
    base_outer_path_pt: Sequence[Point],
    center_pt: Point,
    boss_radius_pt: float,
) -> bool:
    threshold_pt = boss_radius_pt + mm_to_pt(KEYRING_SIDE_CLEARANCE_THRESHOLD_MM)
    left_clearance_pt, right_clearance_pt = keyring_side_clearances(base_outer_path_pt, center_pt)
    return (
        (left_clearance_pt is not None and left_clearance_pt <= threshold_pt)
        or (right_clearance_pt is not None and right_clearance_pt <= threshold_pt)
    )


def resolve_keyring_hole_center(
    base_outer_path_pt: PathPoints,
    rx_pt: float,
    hole_radius_pt: float,
    boss_radius_pt: float,
) -> Point:
    from ..errors import PdfGenerationError
    from ..geometry.polygons import geometry_bounds
    
    # 1. Local peak at rx
    top_y_pt = topmost_intersection_y(base_outer_path_pt, rx_pt)
    
    # 2. Global peak safety (Avoid interference from tall side elements)
    # Ensure hole is not significantly lower than the absolute highest point of the silhouette
    bounds = geometry_bounds(paths_groups=([base_outer_path_pt],))
    global_max_y_pt = bounds[3] # max_y
    
    # If the local head is more than 15mm lower than the global peak, 
    # we raise the floor to prevent chain interference.
    safety_margin_pt = mm_to_pt(15.0)
    effective_top_y_pt = max(top_y_pt, global_max_y_pt - safety_margin_pt)
    
    slide_step_pt = mm_to_pt(KEYRING_BOSS_SLIDE_STEP_MM)
    current_center_pt = (rx_pt, effective_top_y_pt + hole_radius_pt)
    
    if not side_clearance_is_tight(base_outer_path_pt, current_center_pt, boss_radius_pt):
        return current_center_pt

    for _ in range(INTERNAL_ITERATION_GUARD):
        current_center_pt = (rx_pt, current_center_pt[1] + slide_step_pt)
        if not side_clearance_is_tight(base_outer_path_pt, current_center_pt, boss_radius_pt):
            return current_center_pt
    raise PdfGenerationError("keyring boss side-clearance adjustment exceeded the internal safety guard")
