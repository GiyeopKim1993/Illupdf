from __future__ import annotations
import json
from typing import TextIO
from .state import VisualizerState


def consume_ndjson_stream(stream: TextIO, state: VisualizerState) -> None:
    try:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                state.append_output_text(line + "\n")
                continue
            if event.get("type") != "nesting":
                continue
            payload = event.get("payload", {})
            event_type = payload.get("event_type", "")
            message = payload.get("message") or event_type
            if event_type == "error":
                state.set_error(message)
            else:
                state.add_frame(payload)
                state.append_output_text(message + "\n")
                if payload.get("done"):
                    state.mark_done()
    except Exception as exc:
        state.set_error(f"Error reading output: {exc}")


def consume_text_stream(stream: TextIO, state: VisualizerState) -> None:
    # Backward-compatible alias; current visualizer expects NDJSON.
    consume_ndjson_stream(stream, state)
