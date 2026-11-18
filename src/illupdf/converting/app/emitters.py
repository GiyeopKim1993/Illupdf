from __future__ import annotations
import sys
from typing import Callable
from .events import PipelineEvent
from .serializers import serialize_event
from ..engine.models import TraceFrame

EventEmitter = Callable[[PipelineEvent], None]

class NdjsonEmitter:
    def __init__(self, stream=sys.stdout) -> None:
        self._stream = stream

    def __call__(self, event: PipelineEvent) -> None:
        self._stream.write(serialize_event(event) + "\n")
        self._stream.flush()


class TraceToEventEmitter:
    def __init__(self, emitter: EventEmitter) -> None:
        self._emitter = emitter

    def __call__(self, frame: TraceFrame) -> None:
        event = PipelineEvent.from_trace_frame(frame)
        self._emitter(event)
