from __future__ import annotations
from ..models import SpotColorDefinition

WHITE_SPOT_CHOICES: dict[str, SpotColorDefinition] = {
    "spot_0": SpotColorDefinition(name="Spot_0", cmyk=(0.0, 0.0, 0.0, 1.0)),
    "spot_1": SpotColorDefinition(name="Spot_1", cmyk=(0.0, 0.0, 0.0, 1.0)),
    "white": SpotColorDefinition(name="White", cmyk=(0.0, 0.0, 0.0, 1.0)),
    "rdg_white": SpotColorDefinition(name="RDG_WHITE", cmyk=(0.0, 0.0, 0.0, 1.0)),
    "sswhite": SpotColorDefinition(name="SSWhite", cmyk=(0.0, 0.0, 0.0, 1.0)),
}

DEFAULT_WHITE_SPOT = "spot_0"
OUTLINE_STROKE_RGB = (1.0, 0.0, 0.0)
OUTLINE_STROKE_PT = 0.01


def resolve_white_spot(white_spot_name: str) -> SpotColorDefinition:
    from ..errors import InputImageError
    key = white_spot_name.lower()
    try:
        return WHITE_SPOT_CHOICES[key]
    except KeyError as exc:
        valid_names = ", ".join(sorted(WHITE_SPOT_CHOICES))
        raise InputImageError(f"white spot must be one of: {valid_names}") from exc
