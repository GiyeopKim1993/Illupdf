"""Public package exports for illupdf."""

from .core import (
    ConversionOutputs,
    IlluPdfError,
    InputImageError,
    PdfGenerationError,
    SpotColorDefinition,
    convert_image_to_pdf,
    default_output_path,
)

__all__ = [
    "ConversionOutputs",
    "IlluPdfError",
    "InputImageError",
    "PdfGenerationError",
    "SpotColorDefinition",
    "convert_image_to_pdf",
    "default_output_path",
]
