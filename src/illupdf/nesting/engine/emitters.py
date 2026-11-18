"""
Nesting event emitters ??mirror the pattern in app/emitters.py.
The GA core calls observer(event). Where the event goes depends on which emitter is wired in.
"""
from __future__ import annotations
import json
import sys
from typing import Callable, TextIO

from .events import NestingEvent

NestingObserver = Callable[[NestingEvent], None]


class NdjsonNestingEmitter:
    """Serializes NestingEvents as newline-delimited JSON to a stream (default: stdout)."""

    def __init__(self, stream: TextIO = sys.stdout) -> None:
        self._stream = stream

    def __call__(self, event: NestingEvent) -> None:
        payload = {"type": "nesting", "payload": event.to_dict()}
        self._stream.write(json.dumps(payload) + "\n")
        self._stream.flush()


class PrintNestingEmitter:
    """Prints a human-readable summary of each event to stderr."""

    def __call__(self, event: NestingEvent) -> None:
        if event.event_type == "start":
            print(
                f"[Nesting] Start: {event.num_items} items | "
                f"Artboard {event.artboard_w_mm:.0f}x{event.artboard_h_mm:.0f}mm | "
                f"Pop={event.population_size} Gen={event.total_generations}",
                file=sys.stderr,
            )
        elif event.event_type == "generation":
            bar_len = 20
            filled = int(bar_len * event.generation / max(event.total_generations, 1))
            bar = "#" * filled + "-" * (bar_len - filled)
            print(
                f"\r[{bar}] Gen {event.generation:3d}/{event.total_generations} "
                f"| {event.phase:11s} | Fitness={event.best_fitness:.0f} | Pages={event.num_pages}",
                end="",
                file=sys.stderr,
            )
        elif event.event_type == "expansion":
            print(
                f"\n[Nesting] Expanding artboard to "
                f"{event.artboard_w_mm:.0f}x{event.artboard_h_mm:.0f}mm "
                f"(attempt #{event.expansion_count})",
                file=sys.stderr,
            )
        elif event.event_type in ("result", "error"):
            print(f"\n[Nesting] {event.message}", file=sys.stderr)
