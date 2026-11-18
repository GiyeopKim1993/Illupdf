from __future__ import annotations
import json
from typing import Any
from pathlib import Path
from dataclasses import asdict
from .events import PipelineEvent

class PipelineEventEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        return super().default(obj)


def serialize_event(event: PipelineEvent) -> str:
    return json.dumps(asdict(event), cls=PipelineEventEncoder)
