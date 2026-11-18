from __future__ import annotations
import threading
from typing import Any


class VisualizerState:
    def __init__(self, command: str) -> None:
        self.command = command
        self.frames: list[dict[str, Any]] = []
        self.outputs_text = "pending..."
        self.error: str | None = None
        self.done = False
        self._lock = threading.Lock()

    def add_frame(self, frame_dict: dict[str, Any]) -> None:
        with self._lock:
            self.frames.append(frame_dict)

    def append_output_text(self, text: str) -> None:
        with self._lock:
            if self.outputs_text == "pending...":
                self.outputs_text = ""
            self.outputs_text += text

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

    def mark_done(self) -> None:
        with self._lock:
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
