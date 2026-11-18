from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
from ..engine.models import TraceFrame, ConversionOutputs

@dataclass(frozen=True)
class PipelineEvent:
    type: str
    payload: dict[str, Any]

    @classmethod
    def from_trace_frame(cls, frame: TraceFrame) -> PipelineEvent:
        return cls(type="trace", payload=asdict(frame))

    @classmethod
    def from_outputs(cls, outputs: ConversionOutputs) -> PipelineEvent:
        return cls(type="outputs", payload=asdict(outputs))

    @classmethod
    def from_error(cls, error_message: str) -> PipelineEvent:
        return cls(type="error", payload={"message": error_message})
