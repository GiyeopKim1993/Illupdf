from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class Preset:
    name: str
    default_size_mm: float
    has_boss: bool = False
    has_connection: bool = False
    has_support: bool = False
    has_base: bool = False


PRESETS: dict[str, Preset] = {
    "keyring": Preset(
        name="keyring",
        default_size_mm=65.0,
        has_boss=True,
    ),
    "stand": Preset(
        name="stand",
        default_size_mm=148.0,
        has_connection=True,
        has_support=True,
        has_base=True,
    ),
}
