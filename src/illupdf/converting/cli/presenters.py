from __future__ import annotations
from ..engine.models import ConversionOutputs

def print_outputs(outputs: ConversionOutputs) -> None:
    print(f"combined: {outputs.combined_pdf}")
