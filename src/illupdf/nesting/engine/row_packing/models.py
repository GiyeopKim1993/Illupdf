from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ....converting.engine.models import PathPoints


@dataclass(frozen=True)
class CountSpec:
    path: Path
    count: int


@dataclass(frozen=True)
class SourcePdf:
    path: Path
    page_size_pt: tuple[float, float]
    outline_paths_pt: list[PathPoints]
    form_names: dict[str, str]
    # IllupdfArt sources are character artwork with a fixed top/bottom
    # (head up, feet down). Rotating them 180° would print upside down, so
    # only 0° / 90° / 270° are offered for them.
    allow_flip: bool = True


@dataclass(frozen=True)
class OrientedItem:
    source: SourcePdf
    instance_id: str
    copy_index: int
    rotation_deg: int
    outline_paths_pt: list[PathPoints]
    safety_paths_pt: list[PathPoints]
    outline_bounds_pt: tuple[float, float, float, float]
    safety_bounds_pt: tuple[float, float, float, float]
    width_mm: float
    height_mm: float
    safety_sub_bounds_pt: tuple[tuple[float, float, float, float], ...] = field(default_factory=tuple)
    # For composite items (e.g., 2 keyrings stacked vertically as one stand-like item)
    # Each component is (OrientedItem, x_offset_mm, y_offset_mm) relative to composite origin
    composite_components: tuple = field(default_factory=tuple, compare=False, hash=False)
    is_composite: bool = field(default=False, compare=False, hash=False)


@dataclass(frozen=True)
class PlacedItem:
    item: OrientedItem
    x_mm: float
    y_mm: float


@dataclass
class RowLayout:
    placed: list[PlacedItem] = field(default_factory=list)
    width_mm: float = 0.0
    height_mm: float = 0.0


@dataclass
class SheetLayout:
    rows: list[RowLayout] = field(default_factory=list)
    placed: list[PlacedItem] = field(default_factory=list)
    width_mm: float = 0.0
    height_mm: float = 0.0
    source_dir: Path | None = None
    index: int = 1
