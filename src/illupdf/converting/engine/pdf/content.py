from __future__ import annotations
from typing import Iterable, Sequence
import pikepdf
from ..models import Point, PreparedArtwork
from .colors import OUTLINE_STROKE_RGB, OUTLINE_STROKE_PT

def pdf_name_token(name: str | pikepdf.Name) -> str:
    name_str = str(name)
    if name_str.startswith("/"):
        return name_str
    return f"/{name_str}"


def format_number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def paths_to_pdf_commands(
    paths_pt: Iterable[Sequence[Point]],
    paint_operator: str,
    compound: bool = False,
) -> str:
    all_commands = []
    for path in paths_pt:
        if not path:
            continue
        commands = [f"{format_number(path[0][0])} {format_number(path[0][1])} m"]
        for x, y in path[1:]:
            commands.append(f"{format_number(x)} {format_number(y)} l")
        commands.append("h")
        all_commands.append(" ".join(commands))

    if not all_commands:
        return ""

    if compound:
        return " ".join(all_commands) + f"\n{paint_operator}"
    return "\n".join(f"{cmd}\n{paint_operator}" for cmd in all_commands)


def build_combined_page_content(
    raster_form_name: pikepdf.Name,
    outline_form_name: pikepdf.Name,
) -> str:
    commands = ["q"]
    commands.extend(f"{pdf_name_token(form_name)} Do" for form_name in (raster_form_name, outline_form_name))
    commands.append("Q")
    return "\n".join(commands) + "\n"


def build_raster_layer_content(
    prepared: PreparedArtwork,
    image_name: pikepdf.Name,
) -> str:
    raster_width_pt, raster_height_pt = prepared.raster_size_pt
    raster_origin_x, raster_origin_y = prepared.raster_origin_pt
    clip_paths = []
    clip_cmds = ""
    if clip_cmds:
        return (
            "q\n"
            f"{clip_cmds}\n"
            f"{format_number(raster_width_pt)} 0 0 {format_number(raster_height_pt)} "
            f"{format_number(raster_origin_x)} {format_number(raster_origin_y)} cm\n"
            f"{pdf_name_token(image_name)} Do\n"
            "Q\n"
        )
    return (
        "q\n"
        f"{format_number(raster_width_pt)} 0 0 {format_number(raster_height_pt)} "
        f"{format_number(raster_origin_x)} {format_number(raster_origin_y)} cm\n"
        f"{pdf_name_token(image_name)} Do\n"
        "Q\n"
    )


def build_outline_layer_content(
    prepared: PreparedArtwork,
) -> str:
    return (
        "q\n"
        f"{OUTLINE_STROKE_RGB[0]:.0f} {OUTLINE_STROKE_RGB[1]:.0f} {OUTLINE_STROKE_RGB[2]:.0f} RG\n"
        f"{format_number(OUTLINE_STROKE_PT)} w\n"
        "1 J\n"
        "1 j\n"
        f"{paths_to_pdf_commands(prepared.outline_paths_pt, paint_operator='S')}\n"
        "Q\n"
    )
