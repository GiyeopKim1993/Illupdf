from __future__ import annotations
from typing import Sequence
import cv2
import numpy as np
from ..geometry.polygons import dedupe_consecutive, orient_path
from ..models import PathPoints
from ..units import mm_to_px, px_to_pt

MIN_CONTOUR_AREA_PX2 = 100.0
DISCONNECTED_SILHOUETTE_REJECT_RATIO = 0.2
MASK_ALPHA_THRESHOLD = 2  # Lowered for sensitive edge detection
ATTACHABLE_ISLAND_AREA_RATIO = 0.001
SIMPLIFY_EPSILON_MM = 0.15
WHITE_VECTOR_SIMPLIFY_EPSILON_MM = 0.03


def extract_significant_outer_contours(mask: np.ndarray) -> list[np.ndarray]:
    from ..errors import PdfGenerationError
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    
    # 1. Filter out very small noise (absolute area < 64px)
    significant = [contour for contour in contours if cv2.contourArea(contour) >= MIN_CONTOUR_AREA_PX2]
    if not significant:
        raise PdfGenerationError("could not extract an outer contour from the image")

    # 2. Sort by area descending
    significant.sort(key=cv2.contourArea, reverse=True)
    largest_area = cv2.contourArea(significant[0])
    
    # 3. Automatic largest silhouette selection
    # We keep the largest one as the primary silhouette.
    kept_contours: list[np.ndarray] = [significant[0]]
    
    # 4. Optional: Keep 'Islands' only if they are close/relevant? 
    # For now, let's strictly follow the 'Largest Only' strategy to solve the error, 
    # but still filter out tiny crumbs even among other significant ones.
    for contour in significant[1:]:
        contour_area = cv2.contourArea(contour)
        # Only keep additional silhouettes if they are reasonably large (e.g. > 1% of largest)
        # and don't throw an error anymore.
        if contour_area >= largest_area * ATTACHABLE_ISLAND_AREA_RATIO:
            kept_contours.append(contour)
            
    return kept_contours


def extract_single_outer_contour(mask: np.ndarray) -> np.ndarray:
    return extract_significant_outer_contours(mask)[0]


def simplify_contour(
    contour: np.ndarray,
    epsilon_mm: float = SIMPLIFY_EPSILON_MM,
) -> list[tuple[int, int]]:
    from ..errors import PdfGenerationError
    epsilon_px = max(1.0, float(mm_to_px(epsilon_mm)))
    simplified = cv2.approxPolyDP(contour, epsilon_px, True)
    points = [(int(point[0][0]), int(point[0][1])) for point in simplified]
    points = dedupe_consecutive(points)
    if len(points) < 3:
        raise PdfGenerationError("outer contour is too small to build a closed path")
    return points


def contour_to_pdf_path(points: Sequence[tuple[int, int]], image_height_px: int) -> PathPoints:
    return [(px_to_pt(x), px_to_pt(image_height_px - y)) for x, y in points]


def mask_to_union_paths_pt(
    mask: np.ndarray,
    simplify_epsilon_mm: float = WHITE_VECTOR_SIMPLIFY_EPSILON_MM,
) -> list[PathPoints]:
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE)
    if not contours or hierarchy is None:
        return []

    image_height_px = mask.shape[0]
    hierarchy_rows = hierarchy[0]
    kept_root_indexes = {
        index
        for index, contour in enumerate(contours)
        if hierarchy_rows[index][3] == -1 and cv2.contourArea(contour) >= MIN_CONTOUR_AREA_PX2
    }
    if not kept_root_indexes:
        return []

    output_paths: list[PathPoints] = []
    for index, contour in enumerate(contours):
        if contour_root_index(index, hierarchy_rows) not in kept_root_indexes:
            continue
        try:
            points = simplify_contour(contour, epsilon_mm=simplify_epsilon_mm)
        except Exception:
            continue
        path = contour_to_pdf_path(points, image_height_px)
        depth = contour_depth(index, hierarchy_rows)
        output_paths.append(orient_path(path, clockwise=(depth % 2 == 1)))
    return output_paths


def contour_root_index(index: int, hierarchy_rows: np.ndarray) -> int:
    parent_index = hierarchy_rows[index][3]
    while parent_index != -1:
        index = parent_index
        parent_index = hierarchy_rows[index][3]
    return index


def contour_depth(index: int, hierarchy_rows: np.ndarray) -> int:
    depth = 0
    parent_index = hierarchy_rows[index][3]
    while parent_index != -1:
        depth += 1
        parent_index = hierarchy_rows[parent_index][3]
    return depth
