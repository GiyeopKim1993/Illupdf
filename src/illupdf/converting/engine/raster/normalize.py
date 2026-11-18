from __future__ import annotations

from ..units import PPI

def compute_target_size_pixels(width_px: int, height_px: int, target_size: float | str, PPI: int = PPI) -> tuple[int, int]:
    """
    Computes the target pixel dimensions so that the long side of the image
    matches the target size in mm at the given PPI.
    
    If target_size is a string, it is treated as a preset name and the default size is used.
    """
    if isinstance(target_size, str):
        from ..presets import PRESETS
        target_long_side_mm = PRESETS[target_size].default_size_mm
    else:
        target_long_side_mm = target_size
        
    target_long_side_px = (target_long_side_mm / 25.4) * PPI
    current_long_side_px = max(width_px, height_px)
    scale = target_long_side_px / current_long_side_px
    
    return int(round(width_px * scale)), int(round(height_px * scale))


def image_to_b64(image: "PIL.Image.Image") -> str:
    import io
    import base64
    buffered = io.BytesIO()
    # Save as PNG to preserve transparency
    image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")
