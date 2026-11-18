"""
Nesting pipeline event definitions.
These are emitted by the GA optimizer and consumed by visualizers or loggers.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Any


@dataclass
class NestingEvent:
    """A single event emitted by the nesting pipeline."""
    event_type: str          # "start" | "generation" | "expansion" | "result" | "error"
    generation: int = 0
    total_generations: int = 0
    population_size: int = 0
    best_fitness: float = 0.0
    num_pages: int = 0
    phase: str = ""          # "exploration" | "refinement"
    artboard_w_px: int = 0
    artboard_h_px: int = 0
    artboard_w_mm: float = 0.0
    artboard_h_mm: float = 0.0
    expansion_count: int = 0
    num_items: int = 0
    placed_items: list = field(default_factory=list)  # list of {name, x, y, w, h, rot}
    message: str = ""
    done: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
