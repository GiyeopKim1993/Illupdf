from __future__ import annotations
from pathlib import Path
import numpy as np
from PIL import Image
from ..errors import InputImageError

VALID_SUFFIXES = {".png", ".webp"}
MASK_ALPHA_THRESHOLD = 1


def load_rgba_image(input_path: Path) -> Image.Image:
    if input_path.suffix.lower() not in VALID_SUFFIXES:
        raise InputImageError("input must be a PNG or WebP image")
    if not input_path.exists():
        raise InputImageError(f"input file not found: {input_path}")

    with Image.open(str(input_path)) as image:
        if not has_alpha_channel(image):
            raise InputImageError("input image must include an alpha channel")
        rgba_image = image.convert("RGBA")

    alpha = np.array(rgba_image.getchannel("A"), dtype=np.uint8)
    if np.all(alpha == 255):
        raise InputImageError("input image must contain transparent pixels around the artwork")
    if np.all(alpha < MASK_ALPHA_THRESHOLD):
        raise InputImageError("input image does not contain any opaque artwork pixels")
    return rgba_image


def crop_to_alpha_bounds(rgba_image: Image.Image) -> Image.Image:
    alpha = np.array(rgba_image.getchannel("A"), dtype=np.uint8)
    ys, xs = np.where(alpha >= MASK_ALPHA_THRESHOLD)
    if len(xs) == 0 or len(ys) == 0:
        raise InputImageError("input image does not contain any opaque artwork pixels")
    left = int(xs.min())
    top = int(ys.min())
    right = int(xs.max()) + 1
    bottom = int(ys.max()) + 1
    return rgba_image.crop((left, top, right, bottom))


def add_transparent_padding(rgba_image: Image.Image, padding_px: int) -> Image.Image:
    if padding_px <= 0:
        return rgba_image.copy()
    padded_image = Image.new("RGBA", (rgba_image.width + padding_px * 2, rgba_image.height + padding_px * 2), (0, 0, 0, 0))
    padded_image.paste(rgba_image, (padding_px, padding_px))
    return padded_image


def has_alpha_channel(image: Image.Image) -> bool:
    return image.mode in {"RGBA", "LA"} or "transparency" in image.info
