"""Convert a layered artwork PDF into an illupdf nesting source.

The converter accepts PDFs that follow the layered-artwork convention produced
by ``illupdf converting`` (and by common RIP/prepress tools):

* ``/Artwork`` image XObject with an ``/SMask`` alpha channel
* ``/OC /ArtLayer``, ``/OC /WhiteLayer``, ``/OC /CutLayer`` marked-content
  sections in the page content stream, each preceded by a ``cm`` matrix

and rewrites them into the shape the row packer expects:

* ``/RasterForm`` — the raster clipped by the cutline, so the transparent
  margin disappears without any resampling or rescaling (no distortion)
* ``/WhiteForm`` — the white underbase, shifted to the page origin
* ``/OutlineForm`` — the cutline, shifted to the page origin

The page is cropped to the cutline bounding box, so the packer sees exactly the
cut size. ``/IllupdfArt true`` is written to the catalogue to mark the file as
orientation-sensitive (never nest it upside down).
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pikepdf

from ....converting.engine.units import pt_to_mm

_MM_PER_PT = 25.4 / 72.0
_CM_RE = re.compile(r"([\-\d.]+)\s+0\s+0\s+([\-\d.]+)\s+([\-\d.]+)\s+([\-\d.]+)\s+cm")
_PATH_RE = re.compile(r"([\-\d.]+)\s+([\-\d.]+)\s+([ml])\b")
_ALPHA_THRESHOLD = 8


@dataclass(frozen=True)
class ArtworkGeometry:
    """Measured geometry of one layered artwork page (all values in pt)."""

    cut_paths: list[list[tuple[float, float]]]
    white_paths: list[list[tuple[float, float]]]
    cut_bbox: tuple[float, float, float, float]
    image_rect: tuple[float, float, float, float]

    @property
    def cut_size_mm(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.cut_bbox
        return pt_to_mm(x1 - x0), pt_to_mm(y1 - y0)


def _layer_block(content: str, layer: str) -> str:
    start = content.find("/OC /%s BDC" % layer)
    if start < 0:
        raise ValueError("layer /%s not found" % layer)
    end = content.find("EMC", start)
    return content[start:end]


def _cm(block: str) -> tuple[float, float, float, float]:
    match = _CM_RE.search(block)
    if match is None:
        return 1.0, 1.0, 0.0, 0.0
    return tuple(float(match.group(i)) for i in (1, 2, 3, 4))  # type: ignore[return-value]


def _subpaths(block: str, cm: tuple[float, float, float, float]):
    sx, sy, tx, ty = cm
    paths: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for x_raw, y_raw, op in _PATH_RE.findall(block):
        point = (float(x_raw) * sx + tx, float(y_raw) * sy + ty)
        if op == "m":
            if len(current) >= 3:
                paths.append(current)
            current = [point]
        else:
            current.append(point)
    if len(current) >= 3:
        paths.append(current)
    return paths


def _path_cmds(paths, dx: float, dy: float) -> str:
    """Serialise paths as PDF path commands. Coordinates are already in pt."""
    parts = []
    for path in paths:
        parts.append(
            " ".join(
                "%.4f %.4f %s" % (x + dx, y + dy, "m" if i == 0 else "l")
                for i, (x, y) in enumerate(path)
            )
            + " h"
        )
    return "\n".join(parts)


def measure_artwork(page) -> ArtworkGeometry:
    """Measure cut/white outline and the raster placement of one page."""
    content = page.Contents.read_bytes().decode("latin-1")
    cut_paths = _subpaths(_layer_block(content, "CutLayer"), _cm(_layer_block(content, "CutLayer")))
    white_paths = _subpaths(_layer_block(content, "WhiteLayer"), _cm(_layer_block(content, "WhiteLayer")))
    points = [p for path in cut_paths for p in path]
    cut_bbox = (
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    )

    image = page.Resources.XObject["/Artwork"]
    width, height = int(image.Width), int(image.Height)
    ax, ay, atx, aty = _cm(_layer_block(content, "ArtLayer"))
    alpha = np.frombuffer(
        zlib.decompress(image.SMask.read_raw_bytes()), dtype=np.uint8
    ).reshape(height, width)
    rows, cols = np.nonzero(alpha > _ALPHA_THRESHOLD)
    r0, r1, c0, c1 = rows.min(), rows.max(), cols.min(), cols.max()
    image_rect = (
        c0 * ax / width,
        aty + (1 - (r1 + 1) / height) * ay,
        (c1 + 1) * ax / width,
        aty + (1 - r0 / height) * ay,
    )
    return ArtworkGeometry(
        cut_paths=cut_paths,
        white_paths=white_paths,
        cut_bbox=cut_bbox,
        image_rect=image_rect,
    )


def _crop_raster(image, rect, page_cut_bbox):
    width, height = int(image.Width), int(image.Height)
    rgb = np.frombuffer(zlib.decompress(image.read_raw_bytes()), dtype=np.uint8).reshape(height, width, 3)
    alpha = np.frombuffer(
        zlib.decompress(image.SMask.read_raw_bytes()), dtype=np.uint8
    ).reshape(height, width)
    rows, cols = np.nonzero(alpha > _ALPHA_THRESHOLD)
    r0, r1, c0, c1 = rows.min(), rows.max(), cols.min(), cols.max()
    return rgb[r0 : r1 + 1, c0 : c1 + 1].copy(), alpha[r0 : r1 + 1, c0 : c1 + 1].copy()


def convert_artwork_pdf(source_path: Path, output_path: Path) -> Path:
    """Rewrite a layered artwork PDF into an illupdf nesting source."""
    src = pikepdf.open(source_path)
    try:
        page = src.pages[0]
        geometry = measure_artwork(page)
        cut_x0, cut_y0, cut_x1, cut_y1 = geometry.cut_bbox
        cut_w, cut_h = cut_x1 - cut_x0, cut_y1 - cut_y0
        image = page.Resources.XObject["/Artwork"]
        rgb, alpha = _crop_raster(image, geometry.image_rect, geometry.cut_bbox)
        img_h, img_w = alpha.shape
        img_x0, img_y0, img_x1, img_y1 = geometry.image_rect

        out = pikepdf.Pdf.new()
        out_page = out.add_blank_page(page_size=(cut_w, cut_h))
        xobjects = pikepdf.Dictionary()

        raster_image = out.make_stream(
            zlib.compress(rgb.tobytes(), 9),
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=img_w,
            Height=img_h,
            ColorSpace=pikepdf.Name.DeviceRGB,
            BitsPerComponent=8,
            Filter=pikepdf.Name.FlateDecode,
        )
        mask_image = out.make_stream(
            zlib.compress(alpha.tobytes(), 9),
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=img_w,
            Height=img_h,
            ColorSpace=pikepdf.Name.DeviceGray,
            BitsPerComponent=8,
            Filter=pikepdf.Name.FlateDecode,
        )
        raster_image.SMask = mask_image

        # Raster: clipped by the cutline -> transparent margin gone, and the
        # image transform is untouched, so the artwork keeps its true scale.
        raster_body = (
            "q\n%s\nW* n\nq %.4f 0 0 %.4f %.4f %.4f cm /Im0 Do Q\nQ\n"
            % (
                _path_cmds(geometry.cut_paths, -cut_x0, -cut_y0),
                img_x1 - img_x0,
                img_y1 - img_y0,
                img_x0 - cut_x0,
                img_y0 - cut_y0,
            )
        )
        xobjects["/RasterForm"] = out.make_stream(
            raster_body.encode("latin-1"),
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Form,
            BBox=pikepdf.Array([0, 0, cut_w, cut_h]),
            Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=raster_image)),
        )

        white_space = pikepdf.Array(
            [
                pikepdf.Name.Separation,
                pikepdf.Name("/rdg_white"),
                pikepdf.Name.DeviceCMYK,
                out.make_indirect(
                    pikepdf.Dictionary(
                        FunctionType=2,
                        Domain=pikepdf.Array([0, 1]),
                        N=1,
                        C0=pikepdf.Array([0, 0, 0, 0]),
                        C1=pikepdf.Array([0, 0, 0, 0]),
                    )
                ),
            ]
        )
        white_body = "q\n/WhiteSpot cs\n1 scn\n%s\nf*\nQ\n" % _path_cmds(
            geometry.white_paths, -cut_x0, -cut_y0
        )
        xobjects["/WhiteForm"] = out.make_stream(
            white_body.encode("latin-1"),
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Form,
            BBox=pikepdf.Array([0, 0, cut_w, cut_h]),
            Resources=pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(WhiteSpot=white_space)
            ),
        )

        outline_body = "q\n1 0 0 RG\n0.5 w\n1 J\n1 j\n%s\nS\nQ\n" % _path_cmds(
            geometry.cut_paths, -cut_x0, -cut_y0
        )
        xobjects["/OutlineForm"] = out.make_stream(
            outline_body.encode("latin-1"),
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Form,
            BBox=pikepdf.Array([0, 0, cut_w, cut_h]),
            Resources=pikepdf.Dictionary(),
        )

        out_page.Resources = pikepdf.Dictionary(XObject=xobjects)
        out_page.Contents = out.make_stream(
            b"/RasterForm Do /WhiteForm Do /OutlineForm Do"
        )
        out.Root.IllupdfArt = True
        output_path.parent.mkdir(parents=True, exist_ok=True)
        out.save(output_path)
        out.close()
        return output_path
    finally:
        src.close()
