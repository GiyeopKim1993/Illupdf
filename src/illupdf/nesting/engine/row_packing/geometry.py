from __future__ import annotations
import math
from ....converting.engine.models import PathPoints
from ....converting.engine.geometry.polygons import path_bounds
from ....converting.engine.geometry.clipping import offset_paths, intersect_paths
from ....converting.engine.units import mm_to_pt, pt_to_mm

SAFETY_MM = 0.125


def transform_path(path: PathPoints, rotation_deg: int) -> PathPoints:
    rot = rotation_deg % 360
    if rot == 0:
        return list(path)
    if rot == 90:
        return [(-y, x) for x, y in path]
    if rot == 180:
        return [(-x, -y) for x, y in path]
    if rot == 270:
        return [(y, -x) for x, y in path]
    theta = math.radians(rot)
    c, s = math.cos(theta), math.sin(theta)
    return [(c*x - s*y, s*x + c*y) for x, y in path]


def translate_path(path: PathPoints, dx_pt: float, dy_pt: float) -> PathPoints:
    return [(x + dx_pt, y + dy_pt) for x, y in path]


def normalize_paths(paths: list[PathPoints]) -> tuple[list[PathPoints], tuple[float, float, float, float]]:
    b = path_bounds(paths)
    min_x, min_y, max_x, max_y = b
    return [translate_path(p, -min_x, -min_y) for p in paths], (0.0, 0.0, max_x - min_x, max_y - min_y)


def orient_paths(paths: list[PathPoints], rotation_deg: int) -> tuple[list[PathPoints], tuple[float, float, float, float]]:
    rotated = [transform_path(p, rotation_deg) for p in paths]
    return normalize_paths(rotated)


def safety_paths(paths: list[PathPoints], safety_mm: float = SAFETY_MM) -> list[PathPoints]:
    """Offset each path individually so small features like the dome boss are kept.

    Offsetting all paths at once union-merges them, which silently drops small
    interior features (e.g. the keyring dome/boss). Keeping them lets the
    collision checks protect those features too.
    """
    offset_pt = mm_to_pt(safety_mm)
    result: list[PathPoints] = []
    for path in paths:
        offset = offset_paths([path], offset_pt)
        if offset:
            result.extend(offset)
        else:
            result.append(path)
    return result or list(paths)


def _bounds_overlap(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def paths_collide(paths_a: list[PathPoints], paths_b: list[PathPoints], clearance_mm: float = 0.0) -> bool:
    if not paths_a or not paths_b:
        return False
    from shapely.geometry import Polygon
    from shapely.prepared import prep
    from shapely.validation import make_valid

    buf_pt = mm_to_pt(clearance_mm) if clearance_mm > 0 else 0.0

    for pa in paths_a:
        ba = path_bounds([pa])
        if buf_pt > 0:
            ba = (ba[0] - buf_pt, ba[1] - buf_pt, ba[2] + buf_pt, ba[3] + buf_pt)
        poly_a = None
        for pb in paths_b:
            bb = path_bounds([pb])
            if buf_pt > 0:
                bb = (bb[0] - buf_pt, bb[1] - buf_pt, bb[2] + buf_pt, bb[3] + buf_pt)
            if not _bounds_overlap(ba, bb):
                continue
            if poly_a is None:
                try:
                    pa_poly = _valid_polygon(Polygon(pa), make_valid)
                    if buf_pt > 0:
                        pa_poly = pa_poly.buffer(buf_pt)
                    poly_a = prep(pa_poly)
                except Exception:
                    poly_a = None
            try:
                pb_poly = _valid_polygon(Polygon(pb), make_valid)
                if buf_pt > 0:
                    pb_poly = pb_poly.buffer(buf_pt)
                if poly_a is not None and poly_a.intersects(pb_poly):
                    return True
            except Exception:
                if bool(intersect_paths([pa], [pb])):
                    return True
    return False


def _valid_polygon(poly, make_valid):
    """Return a valid polygon (offset paths can self-intersect on thin parts)."""
    if poly.is_valid:
        return poly
    try:
        return make_valid(poly)
    except Exception:
        return poly.buffer(0)


def place_paths(paths: list[PathPoints], x_mm: float, y_mm: float) -> list[PathPoints]:
    return [translate_path(p, mm_to_pt(x_mm), mm_to_pt(y_mm)) for p in paths]


def bounds_mm(bounds_pt: tuple[float, float, float, float]) -> tuple[float, float]:
    return pt_to_mm(bounds_pt[2] - bounds_pt[0]), pt_to_mm(bounds_pt[3] - bounds_pt[1])
