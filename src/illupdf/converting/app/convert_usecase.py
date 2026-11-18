from __future__ import annotations
import concurrent.futures
import os
from pathlib import Path
from typing import Optional

from ..engine.pipeline import prepare_artwork, TraceObserver
from ..engine.pdf.renderer import build_combined_pdf, build_split_layer_pdf
from ..engine.models import ConversionOutputs
from .outputs import derive_output_paths, default_output_path
from .events import PipelineEvent
from .emitters import EventEmitter

def _default_pdf_threads() -> int:
    """Pick the PDF-build thread count from available system memory.

    Each concurrent PDF build keeps a working copy of the artwork image data
    in memory, so on small-RAM machines the parallel build can push the
    process over the limit. Scale by total RAM (Linux), or fall back to 1.
    The ILLUPDF_PDF_THREADS env var overrides everything.
    """
    env = os.environ.get("ILLUPDF_PDF_THREADS")
    if env:
        return max(1, int(env))
    try:
        total_bytes = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        total_gb = total_bytes / (1024 ** 3)
    except (ValueError, OSError, AttributeError):
        total_gb = 0.0
    if total_gb < 4:
        return 1
    if total_gb < 8:
        return 2
    return 4


# PDF 저장 4종(combined + raster/outline/white)을 병렬로 저장하기 위한 스레드 수.
# prepared(PreparedArtwork)는 불변(frozen)이며 각 빌더는 독립 pikepdf 객체를 만든다.
# pikepdf 저장 / zlib 압축은 네이티브 호출이라 GIL이 해제되어 실제 병렬 이득이 있다.
_PDF_BUILD_THREADS = _default_pdf_threads()


def _build_all_pdfs(prepared, outputs: ConversionOutputs) -> None:
    """Render the combined + 3 split PDFs concurrently (multithreading)."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=_PDF_BUILD_THREADS) as pool:
        futures = [
            pool.submit(build_combined_pdf, prepared, outputs.combined_pdf),
            pool.submit(build_split_layer_pdf, prepared, outputs.raster_pdf, "Raster"),
            pool.submit(build_split_layer_pdf, prepared, outputs.outline_pdf, "Outline"),
            pool.submit(build_split_layer_pdf, prepared, outputs.white_pdf, "White"),
        ]
        for future in futures:
            future.result()  # propagate the first exception, if any


def convert_image_to_pdf_usecase(
    input_path: str | Path,
    output_path: Optional[str | Path],
    preset_name: str,
    size_mm: Optional[float] = None,
    observer: Optional[TraceObserver] = None,
    event_emitter: Optional[EventEmitter] = None,
) -> ConversionOutputs:
    from ..engine.errors import PdfGenerationError

    input_path = Path(input_path)
    output_path = Path(output_path) if output_path else default_output_path(input_path, preset_name)
    
    prepared = prepare_artwork(input_path, preset_name, size_mm=size_mm, observer=observer)
    outputs = derive_output_paths(output_path)

    try:
        _build_all_pdfs(prepared, outputs)
    except Exception as exc:
        if event_emitter:
            event_emitter(PipelineEvent.from_error(str(exc)))
        raise PdfGenerationError(f"failed to write PDF: {exc}") from exc

    if event_emitter:
        event_emitter(PipelineEvent.from_outputs(outputs))

    return outputs
