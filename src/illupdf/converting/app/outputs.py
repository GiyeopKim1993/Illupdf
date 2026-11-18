from __future__ import annotations
from pathlib import Path
from ..engine.models import ConversionOutputs

def default_output_path(input_path: Path, preset_name: str) -> Path:
    return input_path.with_name(f"{input_path.stem}_{preset_name}.pdf")


def derive_output_paths(combined_pdf: Path) -> ConversionOutputs:
    return ConversionOutputs(
        combined_pdf=combined_pdf,
        raster_pdf=combined_pdf.with_name(f"{combined_pdf.stem}_raster.pdf"),
        outline_pdf=combined_pdf.with_name(f"{combined_pdf.stem}_outline.pdf"),
        white_pdf=combined_pdf.with_name(f"{combined_pdf.stem}_white.pdf"),
    )
