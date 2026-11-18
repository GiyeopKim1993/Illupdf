from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence
from PIL import Image

Point = tuple[float, float]
PathPoints = list[Point]
TraceObserver = Callable[["TraceFrame"], None]


@dataclass(frozen=True)
class SpotColorDefinition:
    name: str
    cmyk: tuple[float, float, float, float]


@dataclass(frozen=True)
class ConversionOutputs:
    combined_pdf: Path
    raster_pdf: Path
    outline_pdf: Path
    white_pdf: Path

    def ordered_paths(self) -> tuple[Path, Path, Path, Path]:
        return (
            self.combined_pdf,
            self.raster_pdf,
            self.outline_pdf,
            self.white_pdf,
        )


@dataclass(frozen=True)
class KeyringBalanceResult:
    outer_path_pt: PathPoints
    hole_path_pt: PathPoints
    boss_path_pt: PathPoints
    hole_center_pt: Point
    centroid_pt: Point
    theta_rad: float
    iterations: int


@dataclass
class TraceFrame:
    stage: str
    message: str
    preset_name: str
    status: str = "running"
    iteration: int | None = None
    outer_paths_pt: list[PathPoints] | None = None
    hole_paths_pt: list[PathPoints] | None = None
    boss_paths_pt: list[PathPoints] | None = None
    vertical_line_pt: tuple[Point, Point] | None = None
    boss_center_pt: Point | None = None
    centroid_pt: Point | None = None           # 최종 형상(외곽 − 구멍)의 면적 가중 중심점 (hole 기준)
    outer_centroid_pt: Point | None = None     # 외곽선(outer) 도형 자체의 중심점 (hole 기준)
    centroid_line_pt: tuple[Point, Point] | None = None
    theta_rad: float | None = None
    bbox_pt: tuple[float, float, float, float] | None = None
    bbox_width_mm: float | None = None
    bbox_height_mm: float | None = None
    bbox_area_mm2: float | None = None
    outline_area_mm2: float | None = None
    raster_b64: str | None = None
    raster_rect_pt: tuple[float, float, float, float] | None = None  # (x, y, w, h) in pt


@dataclass(frozen=True)
class PreparedArtwork:
    preset_name: str
    rgba_image: Image.Image
    base_paths_pt: list[PathPoints]
    outline_paths_pt: list[PathPoints]
    outline_outer_paths_pt: list[PathPoints]
    outline_inner_paths_pt: list[PathPoints]
    white_source_paths_pt: list[PathPoints]
    white_paths_pt: list[PathPoints]
    raster_origin_pt: Point
    raster_size_pt: tuple[float, float]
    page_size_pt: tuple[float, float]
    keyring_balance: KeyringBalanceResult | None = field(default=None)
    white_raster_img: Image.Image | None = field(default=None)
    white_coverage_mask: Image.Image | None = field(default=None)
    white_spot1_img: Image.Image | None = field(default=None)


@dataclass(frozen=True)
class KeyringCandidate:
    rx_pt: float
    error_x_pt: float
    theta_rad: float
    outer_path_pt: PathPoints
    hole_path_pt: PathPoints
    boss_path_pt: PathPoints
    hole_center_pt: Point
    centroid_pt: Point           # 최종 형상(외곽 − 구멍)의 면적 가중 중심점 (hole 기준)
    outer_centroid_pt: Point     # 외곽선(outer) 도형 자체의 중심점 (hole 기준)
