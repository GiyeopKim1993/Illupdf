from __future__ import annotations

from .converting.engine.errors import IlluPdfError, InputImageError, PdfGenerationError
from .converting.engine.units import PPI, MM_PER_INCH, PT_PER_INCH, mm_to_px, mm_to_pt, pt_to_mm, px_to_pt
from .converting.engine.presets import Preset, PRESETS
from .converting.engine.models import (
    Point,
    PathPoints,
    TraceObserver,
    SpotColorDefinition,
    ConversionOutputs,
    KeyringBalanceResult,
    TraceFrame,
    PreparedArtwork,
    KeyringCandidate,
)
from .converting.engine.vector.contours import WHITE_VECTOR_SIMPLIFY_EPSILON_MM, mask_to_union_paths_pt
from .converting.engine.geometry.polygons import (
    approximate_circle_path,
    keep_largest_path_only,
    path_bounds,
    polygon_area_and_centroid,
)
from .converting.engine.geometry.clipping import offset_paths, union_paths, cleanup_tiny_protrusions, intersect_paths
from .converting.engine.geometry.intersections import horizontal_line_intersections, vertical_line_intersections
from .converting.engine.keyring.balance import build_keyring_balance
from .converting.engine.keyring.reinforcement import build_boss_gap_bridge_shapes
from .converting.engine.keyring.hole import resolve_keyring_hole_center
from .converting.engine.raster.normalize import compute_target_size_pixels
from .converting.engine.pipeline import prepare_artwork
from .converting.app.outputs import default_output_path, derive_output_paths
from .converting.app.convert_usecase import convert_image_to_pdf_usecase as convert_image_to_pdf

__all__ = [
    "IlluPdfError",
    "InputImageError",
    "PdfGenerationError",
    "PPI",
    "MM_PER_INCH",
    "PT_PER_INCH",
    "mm_to_px",
    "mm_to_pt",
    "pt_to_mm",
    "px_to_pt",
    "Preset",
    "PRESETS",
    "Point",
    "PathPoints",
    "TraceObserver",
    "SpotColorDefinition",
    "ConversionOutputs",
    "KeyringBalanceResult",
    "TraceFrame",
    "PreparedArtwork",
    "KeyringCandidate",
    "WHITE_VECTOR_SIMPLIFY_EPSILON_MM",
    "approximate_circle_path",
    "mask_to_union_paths_pt",
    "keep_largest_path_only",
    "path_bounds",
    "polygon_area_and_centroid",
    "horizontal_line_intersections",
    "vertical_line_intersections",
    "build_keyring_balance",
    "build_boss_gap_bridge_shapes",
    "resolve_keyring_hole_center",
    "compute_target_size_pixels",
    "prepare_artwork",
    "default_output_path",
    "derive_output_paths",
    "convert_image_to_pdf",
    "offset_paths",
    "union_paths",
    "intersect_paths",
    "cleanup_tiny_protrusions",
]
