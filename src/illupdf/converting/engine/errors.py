from __future__ import annotations

class IlluPdfError(Exception):
    exit_code = 1


class InputImageError(IlluPdfError):
    exit_code = 2


class PdfGenerationError(IlluPdfError):
    exit_code = 3
