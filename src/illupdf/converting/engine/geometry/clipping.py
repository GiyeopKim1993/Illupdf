from __future__ import annotations
from typing import Iterable, Sequence
import pyclipper
from .polygons import keep_largest_path_only
from ..models import Point, PathPoints
from ..units import mm_to_pt, dedupe_consecutive

PYCLIPPER_SCALE = 1000


def float_to_clipper(value: float) -> int:
    return int(round(value * PYCLIPPER_SCALE))


def clipper_to_float(value: int) -> float:
    return value / PYCLIPPER_SCALE


def _path_to_clipper(path_pt: Sequence[Point]) -> list[tuple[int, int]] | None:
    int_path = [(float_to_clipper(float(x)), float_to_clipper(float(y))) for x, y in path_pt]
    if len(int_path) < 3:
        return None
    if not pyclipper.Orientation(int_path):
        int_path = list(reversed(int_path))
    return int_path


def _paths_to_clipper(paths_pt: Iterable[Sequence[Point]]) -> list[list[tuple[int, int]]]:
    int_paths: list[list[tuple[int, int]]] = []
    for path_pt in paths_pt:
        int_path = _path_to_clipper(path_pt)
        if int_path is not None:
            int_paths.append(int_path)
    return int_paths


def _clipper_to_paths(int_paths: Iterable[Sequence[tuple[int, int]]]) -> list[PathPoints]:
    output_paths: list[PathPoints] = []
    for path in int_paths:
        deduped = dedupe_consecutive(path)
        if len(deduped) >= 3:
            output_paths.append([(clipper_to_float(x), clipper_to_float(y)) for x, y in deduped])
    return output_paths


def _execute_boolean(
    subject_paths_pt: Iterable[Sequence[Point]],
    clip_paths_pt: Iterable[Sequence[Point]] = (),
    clip_type: int = pyclipper.CT_UNION,
    *,
    require_clip: bool = False,
) -> list[PathPoints]:
    subject_int_paths = _paths_to_clipper(subject_paths_pt)
    clip_int_paths = _paths_to_clipper(clip_paths_pt)
    if not subject_int_paths or (require_clip and not clip_int_paths):
        return []

    clipper = pyclipper.Pyclipper()
    clipper.AddPaths(subject_int_paths, pyclipper.PT_SUBJECT, True)
    if clip_int_paths:
        clipper.AddPaths(clip_int_paths, pyclipper.PT_CLIP, True)
    result = clipper.Execute(clip_type, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    return _clipper_to_paths(result)


def offset_paths(
    paths_pt: Iterable[Sequence[float | int] | Sequence[tuple[float, float]]],
    delta_pt: float,
) -> list[PathPoints]:
    offsetter = pyclipper.PyclipperOffset()
    int_paths: list[list[tuple[int, int]]] = []
    for path_pt in paths_pt:
        int_path = _path_to_clipper(path_pt)  # type: ignore[arg-type]
        if int_path is not None:
            int_paths.append(int_path)

    if not int_paths:
        return []

    offsetter.AddPaths(int_paths, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    return _clipper_to_paths(offsetter.Execute(float_to_clipper(delta_pt)))


def union_paths(paths_pt: Iterable[Sequence[Point]]) -> list[PathPoints]:
    return _execute_boolean(paths_pt, clip_type=pyclipper.CT_UNION)


def intersect_paths(
    subject_paths_pt: Iterable[Sequence[Point]],
    clip_paths_pt: Iterable[Sequence[Point]],
) -> list[PathPoints]:
    return _execute_boolean(
        subject_paths_pt,
        clip_paths_pt,
        pyclipper.CT_INTERSECTION,
        require_clip=True,
    )


def difference_paths(
    subject_paths_pt: Iterable[Sequence[Point]],
    clip_paths_pt: Iterable[Sequence[Point]],
) -> list[PathPoints]:
    return _execute_boolean(subject_paths_pt, clip_paths_pt, pyclipper.CT_DIFFERENCE)


def cleanup_tiny_protrusions(
    paths_pt: Iterable[Sequence[Point]],
    opening_gap_mm: float = 0.2,
) -> list[PathPoints]:
    original_paths = list(paths_pt)
    if opening_gap_mm <= 0:
        return keep_largest_path_only(original_paths)

    opening_radius_pt = mm_to_pt(opening_gap_mm / 2.0)
    eroded_paths = offset_paths(original_paths, -opening_radius_pt)
    if not eroded_paths:
        return keep_largest_path_only(original_paths)
    opened_paths = offset_paths(eroded_paths, opening_radius_pt)
    if not opened_paths:
        return keep_largest_path_only(original_paths)

    # Intersect opened and original outlines so cleanup only removes tiny protrusions
    # and never grows outside the existing final cutline.
    intersected_paths = intersect_paths(original_paths, opened_paths)
    if not intersected_paths:
        return keep_largest_path_only(opened_paths)
    return keep_largest_path_only(intersected_paths)


def cleanup_minimum_gap(
    paths_pt: Iterable[Sequence[Point]],
    minimum_gap_mm: float,
) -> list[PathPoints]:
    if minimum_gap_mm <= 0:
        return keep_largest_path_only(paths_pt)
    cleanup_radius_pt = mm_to_pt(minimum_gap_mm / 2.0)
    expanded_paths_pt = offset_paths(paths_pt, cleanup_radius_pt)
    if not expanded_paths_pt:
        return keep_largest_path_only(paths_pt)
    shrunk_paths_pt = offset_paths(expanded_paths_pt, -cleanup_radius_pt)
    if not shrunk_paths_pt:
        return keep_largest_path_only(expanded_paths_pt)
    return keep_largest_path_only(shrunk_paths_pt)
