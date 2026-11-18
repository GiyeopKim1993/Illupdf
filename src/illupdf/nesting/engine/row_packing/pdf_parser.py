from __future__ import annotations
from pathlib import Path
import re
import pikepdf

from ....converting.engine.models import PathPoints
from ....converting.engine.geometry.polygons import path_bounds
from ....converting.engine.units import pt_to_mm
from .geometry import orient_paths, safety_paths, bounds_mm
from .models import SourcePdf, OrientedItem

PATH_TOKEN_RE = re.compile(r"/[^\s]+|[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?|[A-Za-z*]+")
FORM_NAMES = {"Raster": "/RasterForm", "White": "/WhiteForm", "Outline": "/OutlineForm"}


def _stream_text(obj) -> str:
    try:
        return bytes(obj.read_bytes()).decode("latin-1")
    except Exception:
        return ""


def _parse_paths_from_content(content: str) -> list[PathPoints]:
    tokens = PATH_TOKEN_RE.findall(content)
    stack: list[float] = []
    current: PathPoints = []
    paths: list[PathPoints] = []
    for tok in tokens:
        try:
            stack.append(float(tok))
            continue
        except ValueError:
            pass
        if tok == "m" and len(stack) >= 2:
            if current and len(current) >= 3:
                paths.append(current)
            y = stack.pop(); x = stack.pop(); stack.clear()
            current = [(x, y)]
        elif tok == "l" and len(stack) >= 2:
            y = stack.pop(); x = stack.pop(); stack.clear()
            current.append((x, y))
        elif tok == "h":
            stack.clear()
        elif tok in {"S", "s", "f", "F", "f*", "B", "B*"}:
            if current and len(current) >= 3:
                paths.append(current)
            current = []
            stack.clear()
        elif tok == "re" and len(stack) >= 4:
            h = stack.pop(); w = stack.pop(); y = stack.pop(); x = stack.pop(); stack.clear()
            paths.append([(x, y), (x+w, y), (x+w, y+h), (x, y+h)])
        else:
            # Ignore graphics operators like q/Q/RG/w/J/j/Do/BDC/EMC.
            stack.clear() if tok in {"q", "Q", "cm", "Do", "BDC", "EMC"} else None
    if current and len(current) >= 3:
        paths.append(current)
    return paths


def _page_content_text(page) -> str:
    if "/Contents" not in page:
        return ""
    contents = page.Contents
    if isinstance(contents, pikepdf.Array):
        return "\n".join(_stream_text(c) for c in contents)
    return _stream_text(contents)


def _outline_form(page):
    try:
        return page.Resources.XObject[pikepdf.Name("/OutlineForm")]
    except Exception:
        return None


def _has_art_marker(pdf) -> bool:
    """True when the PDF declares itself as orientation-sensitive artwork.

    Sources converted by ``illupdf.nesting.engine.row_packing.artwork`` carry
    ``/IllupdfArt true`` in the document catalogue, because character art must
    never be flipped upside down on the sheet.
    """
    try:
        return bool(pdf.Root.get("/IllupdfArt", False))
    except Exception:
        return False


def parse_source_pdf(path: Path) -> SourcePdf:
    with pikepdf.open(path) as pdf:
        page = pdf.pages[0]
        media = [float(x) for x in page.MediaBox]
        page_size = (media[2] - media[0], media[3] - media[1])
        form = _outline_form(page)
        if form is not None:
            outline_paths = _parse_paths_from_content(_stream_text(form))
        else:
            outline_paths = _parse_paths_from_content(_page_content_text(page))
        if not outline_paths:
            raise ValueError(f"could not parse outline paths from {path}")
        forms: dict[str, str] = {}
        try:
            xobjs = page.Resources.XObject
            for layer, name in FORM_NAMES.items():
                if pikepdf.Name(name) in xobjs:
                    forms[layer] = name
        except Exception:
            pass
        return SourcePdf(
            path=path,
            page_size_pt=page_size,
            outline_paths_pt=outline_paths,
            form_names=forms,
            allow_flip=not _has_art_marker(pdf),
        )


def oriented_items_for_source(source: SourcePdf, instance_id: str, copy_index: int) -> list[OrientedItem]:
    # Make long side vertical. If already portrait use 0/180; otherwise 90/270.
    b = path_bounds(source.outline_paths_pt)
    w, h = bounds_mm(b)
    if h >= w:
        rotations = [0, 180] if source.allow_flip else [0]
    else:
        rotations = [90, 270] if source.allow_flip else [90]
    items: list[OrientedItem] = []
    for rot in rotations:
        outline, ob = orient_paths(source.outline_paths_pt, rot)
        safety = safety_paths(outline)
        sb = path_bounds(safety)
        sw, sh = bounds_mm(sb)
        sub_sb = tuple(path_bounds([p]) for p in safety)
        items.append(OrientedItem(
            source=source,
            instance_id=instance_id,
            copy_index=copy_index,
            rotation_deg=rot,
            outline_paths_pt=outline,
            safety_paths_pt=safety,
            outline_bounds_pt=ob,
            safety_bounds_pt=sb,
            width_mm=sw,
            height_mm=sh,
            safety_sub_bounds_pt=sub_sb,
        ))
    return items
