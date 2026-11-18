from __future__ import annotations

import zlib

from PIL import Image
import pikepdf

from ..models import PreparedArtwork, SpotColorDefinition
from ..pdf.content import format_number, pdf_name_token


from functools import lru_cache


@lru_cache(maxsize=8)
def _levels_lut(output_black: int, output_white: int) -> tuple[int, ...]:
    return tuple(
        max(0, min(255, round(output_black + (value / 255.0) * (output_white - output_black))))
        for value in range(256)
    )


def apply_output_levels(gray_image: Image.Image, output_black: int = 0, output_white: int = 170) -> Image.Image:
    """Apply Photoshop-style Levels Output Levels to an 8-bit grayscale image."""
    out_black = max(0, min(255, int(output_black)))
    out_white = max(0, min(255, int(output_white)))
    lut = _levels_lut(out_black, out_white)
    return gray_image.convert("L").point(lut)


def build_white_spot_tint_with_mask(
    rgba_image: Image.Image,
    *,
    output_black: int = 0,
    output_white: int = 170,
    alpha_threshold: int = 1,
    inset_coverage_mask: bool = True,
    invert: bool = True,
) -> tuple[Image.Image, Image.Image]:
    """Build (spot tint image, coverage mask) for the white underbase.

    Returns the single-channel tint image (0..output_white) plus the binary
    coverage mask (0/255) that gates it. The coverage mask is the alpha
    silhouette inset by 0.15mm, so everything outside the artwork is masked
    out — this mask is embedded as the PDF image's SMask to keep the outside
    transparent instead of an opaque white rectangle.
    """
    from PIL import ImageOps
    rgba = rgba_image if rgba_image.mode == "RGBA" else rgba_image.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")

    if invert:
        inverted_rgb = ImageOps.invert(rgb)
        gray = inverted_rgb.convert("L")
    else:
        gray = rgb.convert("L")

    leveled = apply_output_levels(gray, output_black=output_black, output_white=output_white)

    threshold = max(0, min(255, int(alpha_threshold)))
    coverage = alpha.point([0 if value < threshold else 255 for value in range(256)])
    if inset_coverage_mask:
        from ..raster.masks import build_white_mask
        import numpy as np
        coverage = Image.fromarray(build_white_mask(np.array(coverage, dtype=np.uint8)), "L")

    composite = Image.composite(leveled, Image.new("L", leveled.size, 0), coverage)
    return composite, coverage


def build_white_spot_tint_image(
    rgba_image: Image.Image,
    *,
    output_black: int = 0,
    output_white: int = 170,
    alpha_threshold: int = 1,
    inset_coverage_mask: bool = True,
    invert: bool = True,
) -> Image.Image:
    """Build single-channel spot tint image for the white underbase.

    Flow:
      1. RGBA -> RGB (옵션: invert=True 면 색반전) -> Grayscale (L)
      2. apply_output_levels (그레이스케일 레벨 조정)
      3. Alpha channel silhouette (아웃라인 분기 전) -> 0.15mm Inset 수축 마스크 (build_white_mask)
      4. Composite level-adjusted raster with 0.15mm inset coverage mask.

    invert=False (기본): 밝은 원화 → 높은 값 → 흰 잉크↑ (원화 휘도 그대로).
    invert=True: 어두운 원화 → 높은 값 → 흰 잉크↑ (색반전).
    """
    return build_white_spot_tint_with_mask(
        rgba_image,
        output_black=output_black,
        output_white=output_white,
        alpha_threshold=alpha_threshold,
        inset_coverage_mask=inset_coverage_mask,
        invert=invert,
    )[0]


def extract_white_grayscale(rgba_image: Image.Image, invert: bool = True) -> Image.Image:
    """Backward-compatible wrapper for building the white spot tint image."""
    return build_white_spot_tint_image(rgba_image, output_black=0, output_white=170, invert=invert)


def build_spot1_flat_fill(mask: Image.Image, fill_ratio: float = 0.25) -> Image.Image:
    """Build the Spot_1 plate: a flat 25% fill inside the coverage mask.

    The coverage mask is the alpha silhouette shrunk by 0.15mm. Inside that
    region the image is a constant `fill_ratio` tint (byte = 255 * fill_ratio),
    outside it is 0. The plate is embedded as Separation[/Spot_1] with the
    same mask as its SMask so the outside stays transparent.
    """
    import numpy as np
    arr = np.array(mask, dtype=np.uint8)
    fill = int(round(255.0 * fill_ratio))
    out = np.where(arr > 0, fill, 0).astype(np.uint8)
    return Image.fromarray(out, "L")


def make_white_separation_colorspace(
    pdf: pikepdf.Pdf,
    white_spot: SpotColorDefinition,
) -> pikepdf.Array:
    tint_transform = pdf.make_indirect(
        pikepdf.Dictionary(
            FunctionType=2,
            Domain=pikepdf.Array([0, 1]),
            Range=pikepdf.Array([0, 1, 0, 1, 0, 1, 0, 1]),
            C0=pikepdf.Array([0, 0, 0, 0]),
            C1=pikepdf.Array(list(white_spot.cmyk)),
            N=1,
        )
    )
    return pikepdf.Array(
        [
            pikepdf.Name.Separation,
            pikepdf.Name(f"/{white_spot.name}"),
            pikepdf.Name.DeviceCMYK,
            tint_transform,
        ]
    )


def make_separation_image_xobject(
    pdf: pikepdf.Pdf,
    gray_image: Image.Image,
    colorspace_name: pikepdf.Name,
) -> pikepdf.Stream:
    return pdf.make_stream(
        gray_image.tobytes(),
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=gray_image.width,
        Height=gray_image.height,
        ColorSpace=colorspace_name,
        BitsPerComponent=8,
    )


def make_white_raster_form_xobject(
    pdf: pikepdf.Pdf,
    prepared: PreparedArtwork,
    white_spot: SpotColorDefinition,
    layer_ocg: pikepdf.Object | None = None,
) -> pikepdf.Stream:
    white_colorspace = make_white_separation_colorspace(pdf, white_spot)
    white_image_name = pikepdf.Name("/ImWhite")
    white_resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary())
    white_img_stream = pdf.make_stream(
        prepared.white_raster_img.tobytes(),
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=prepared.white_raster_img.width,
        Height=prepared.white_raster_img.height,
        ColorSpace=white_colorspace,
        BitsPerComponent=8,
    )
    # Attach the coverage mask as an SMask so the area outside the artwork
    # silhouette stays transparent instead of rendering as an opaque white box.
    if prepared.white_coverage_mask is not None:
        white_img_stream.SMask = pdf.make_stream(
            zlib.compress(prepared.white_coverage_mask.tobytes(), level=9),
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=prepared.white_coverage_mask.width,
            Height=prepared.white_coverage_mask.height,
            ColorSpace=pikepdf.Name.DeviceGray,
            BitsPerComponent=8,
            Filter=pikepdf.Name.FlateDecode,
        )
    white_resources.XObject[white_image_name] = white_img_stream
    raster_width_pt, raster_height_pt = prepared.raster_size_pt
    raster_origin_x, raster_origin_y = prepared.raster_origin_pt

    # Spot_1 plate: flat 25% fill inside the same coverage mask (if present).
    draw_cmds = ""
    if prepared.white_spot1_img is not None:
        from ..models import SpotColorDefinition
        spot1 = SpotColorDefinition(name="Spot_1", cmyk=(0.0, 0.0, 0.0, 1.0))
        spot1_colorspace = make_white_separation_colorspace(pdf, spot1)
        spot1_img_stream = pdf.make_stream(
            zlib.compress(prepared.white_spot1_img.tobytes(), level=9),
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=prepared.white_spot1_img.width,
            Height=prepared.white_spot1_img.height,
            ColorSpace=spot1_colorspace,
            BitsPerComponent=8,
            Filter=pikepdf.Name.FlateDecode,
        )
        if prepared.white_coverage_mask is not None:
            spot1_img_stream.SMask = pdf.make_stream(
                zlib.compress(prepared.white_coverage_mask.tobytes(), level=9),
                Type=pikepdf.Name.XObject,
                Subtype=pikepdf.Name.Image,
                Width=prepared.white_coverage_mask.width,
                Height=prepared.white_coverage_mask.height,
                ColorSpace=pikepdf.Name.DeviceGray,
                BitsPerComponent=8,
                Filter=pikepdf.Name.FlateDecode,
            )
        white_resources.XObject[pikepdf.Name("/ImWhiteSpot1")] = spot1_img_stream
        draw_cmds = f"{pdf_name_token(pikepdf.Name('/ImWhiteSpot1'))} Do\n"

    from ..pdf.content import paths_to_pdf_commands
    clip_paths = []
    clip_cmds = ""

    content = (
        "q\n"
        f"{clip_cmds}"
        f"{format_number(raster_width_pt)} 0 0 {format_number(raster_height_pt)} "
        f"{format_number(raster_origin_x)} {format_number(raster_origin_y)} cm\n"
        f"{draw_cmds}"
        f"{pdf_name_token(white_image_name)} Do\n"
        "Q\n"
    )
    from ..pdf.renderer import make_form_xobject

    return make_form_xobject(
        pdf=pdf,
        page_size_pt=prepared.page_size_pt,
        resources=white_resources,
        content=content,
        layer_ocg=layer_ocg,
    )
