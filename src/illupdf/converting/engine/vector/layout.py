from __future__ import annotations
from ..geometry.polygons import combined_bounds, translate_path
from ..models import Point, PathPoints, KeyringBalanceResult

def translate_to_page(
    base_paths_pt: list[PathPoints],
    outline_outer_paths_pt: list[PathPoints],
    outline_inner_paths_pt: list[PathPoints],
    white_source_paths_pt: list[PathPoints],
    white_paths_pt: list[PathPoints],
    raster_size_pt: tuple[float, float],
) -> tuple[
    list[PathPoints],
    list[PathPoints],
    list[PathPoints],
    list[PathPoints],
    list[PathPoints],
    Point,
    tuple[float, float],
]:
    min_x, min_y, max_x, max_y = combined_bounds(
        raster_size_pt=raster_size_pt,
        paths_groups=(
            base_paths_pt,
            outline_outer_paths_pt,
            outline_inner_paths_pt,
            white_source_paths_pt,
            white_paths_pt,
        ),
    )
    offset_x = -min_x
    offset_y = -min_y
    translated_base = [translate_path(path, offset_x, offset_y) for path in base_paths_pt]
    translated_outline_outer = [translate_path(path, offset_x, offset_y) for path in outline_outer_paths_pt]
    translated_outline_inner = [translate_path(path, offset_x, offset_y) for path in outline_inner_paths_pt]
    translated_white_source = [translate_path(path, offset_x, offset_y) for path in white_source_paths_pt]
    translated_white = [translate_path(path, offset_x, offset_y) for path in white_paths_pt]
    page_size_pt = (max_x - min_x, max_y - min_y)
    raster_origin_pt = (offset_x, offset_y)
    return (
        translated_base,
        translated_outline_outer,
        translated_outline_inner,
        translated_white_source,
        translated_white,
        raster_origin_pt,
        page_size_pt,
    )


def translate_keyring_balance(
    balance: KeyringBalanceResult,
    offset_x: float,
    offset_y: float,
) -> KeyringBalanceResult:
    return KeyringBalanceResult(
        outer_path_pt=translate_path(balance.outer_path_pt, offset_x, offset_y),
        hole_path_pt=translate_path(balance.hole_path_pt, offset_x, offset_y),
        boss_path_pt=translate_path(balance.boss_path_pt, offset_x, offset_y),
        hole_center_pt=(balance.hole_center_pt[0] + offset_x, balance.hole_center_pt[1] + offset_y),
        centroid_pt=(balance.centroid_pt[0] + offset_x, balance.centroid_pt[1] + offset_y),
        theta_rad=balance.theta_rad,
        iterations=balance.iterations,
    )
