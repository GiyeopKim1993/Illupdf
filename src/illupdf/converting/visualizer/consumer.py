from __future__ import annotations
import json
import time
from typing import TextIO
from .state import VisualizerState

DEFAULT_DELAY_MS = 150

def consume_ndjson_stream(stream: TextIO, state: VisualizerState, delay_ms: int = DEFAULT_DELAY_MS) -> None:
    delay_s = max(0, delay_ms) / 1000.0
    for line in stream:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            event_type = event.get("type")
            payload = event.get("payload", {})
            
            if event_type == "trace":
                state.add_frame(payload)
                if delay_s > 0:
                    time.sleep(delay_s)
            elif event_type == "outputs":
                state.set_outputs(payload)
            elif event_type == "error":
                state.set_error(payload.get("message", "unknown error"))
        except json.JSONDecodeError:
            continue
