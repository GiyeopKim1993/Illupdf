from __future__ import annotations
import threading
from dataclasses import asdict
from typing import Any
from ..engine.models import TraceFrame, ConversionOutputs

BALANCE_STAGES = {"keyring_balance", "keyring_result"}

class VisualizerState:
    def __init__(self, command: str) -> None:
        self.command = command
        self.frames: list[dict[str, Any]] = []
        self.outputs_text = "pending..."
        self.error: str | None = None
        self.done = False
        self._lock = threading.Lock()

    def add_frame(self, frame_dict: dict[str, Any]) -> None:
        frame_dict["is_balance_step"] = frame_dict.get("stage") in BALANCE_STAGES
        with self._lock:
            self.frames.append(frame_dict)

    def set_outputs(self, outputs_dict: dict[str, Any]) -> None:
        outputs_text = "\n".join(
            [
                f"combined: {outputs_dict.get('combined_pdf')}",
                f"raster: {outputs_dict.get('raster_pdf')}",
                f"outline: {outputs_dict.get('outline_pdf')}",
            ]
        )
        with self._lock:
            self.outputs_text = outputs_text
            self.done = True

    def set_error(self, error: str) -> None:
        with self._lock:
            self.error = error
            self.done = True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "command": self.command,
                "frames": list(self.frames),
                "outputs_text": self.outputs_text,
                "error": self.error,
                "done": self.done,
            }
