from __future__ import annotations
from pathlib import Path
import math
import pikepdf

from ....converting.engine.units import mm_to_pt
from ....converting.engine.pdf.renderer import make_ocg, assign_oc_properties, ensure_page_resources
from ....converting.engine.geometry.polygons import path_bounds
from .models import SheetLayout, PlacedItem

LAYER_ORDER = ["White", "Raster", "Outline"]
FORM_NAMES = {"Raster": "/RasterForm", "White": "/WhiteForm", "Outline": "/OutlineForm"}


def _rotation_matrix(deg: int) -> tuple[float, float, float, float]:
    deg = deg % 360
    if deg == 0:
        return 1.0, 0.0, 0.0, 1.0
    if deg == 90:
        return 0.0, 1.0, -1.0, 0.0
    if deg == 180:
        return -1.0, 0.0, 0.0, -1.0
    if deg == 270:
        return 0.0, -1.0, 1.0, 0.0
    t = math.radians(deg)
    return math.cos(t), math.sin(t), -math.sin(t), math.cos(t)


def _rotated_bounds(bounds: tuple[float, float, float, float], deg: int) -> tuple[float, float, float, float]:
    a,b,c,d = _rotation_matrix(deg)
    pts = [(bounds[0], bounds[1]), (bounds[2], bounds[1]), (bounds[2], bounds[3]), (bounds[0], bounds[3])]
    r = [(a*x+c*y, b*x+d*y) for x,y in pts]
    xs = [p[0] for p in r]; ys=[p[1] for p in r]
    return min(xs), min(ys), max(xs), max(ys)


def _rotated_min_corner(paths_pt, deg: int) -> tuple[float, float]:
    """Exact min corner of the ROTATED artwork outline (pt).

    ``_rotated_bounds`` on the raw AABB over-estimates for non-axis-aligned
    rotations (e.g. the 45° pedestal base: a rounded square rotated 45° does
    not reach the rotated AABB corners), which would shift the rendered item
    relative to where the packer validated it. Computing the min over the
    actual rotated points keeps renderer and packer coordinates identical.
    """
    a, b, c, d = _rotation_matrix(deg)
    min_x = min_y = None
    for path in paths_pt:
        for x, y in path:
            rx = a * x + c * y
            ry = b * x + d * y
            if min_x is None or rx < min_x:
                min_x = rx
            if min_y is None or ry < min_y:
                min_y = ry
    if min_x is None:
        return 0.0, 0.0
    return min_x, min_y


def _page_as_form(src_pdf: pikepdf.Pdf, page) -> pikepdf.Stream:
    content = b""
    if "/Contents" in page:
        if isinstance(page.Contents, pikepdf.Array):
            content = b"\n".join(bytes(c.read_bytes()) for c in page.Contents)
        else:
            content = bytes(page.Contents.read_bytes())
    resources = page.Resources if "/Resources" in page else pikepdf.Dictionary()
    media = [float(x) for x in page.MediaBox]
    return src_pdf.make_stream(
        content,
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Form,
        BBox=pikepdf.Array([0, 0, media[2]-media[0], media[3]-media[1]]),
        Resources=resources,
    )


def _copy_layer_form(dst: pikepdf.Pdf, source_path: Path, layer: str):
    src = pikepdf.Pdf.open(source_path)
    page = src.pages[0]
    wanted = pikepdf.Name(FORM_NAMES[layer])
    try:
        xobj = page.Resources.XObject[wanted]
        return dst.copy_foreign(xobj)
    except Exception:
        if layer == "Outline":
            return dst.copy_foreign(_page_as_form(src, page))
    return None


def _matrix_for(placed: PlacedItem) -> str:
    item = placed.item
    a,b,c,d = _rotation_matrix(item.rotation_deg)
    min_x, min_y = _rotated_min_corner(item.source.outline_paths_pt, item.rotation_deg)
    e = mm_to_pt(placed.x_mm) - min_x
    f = mm_to_pt(placed.y_mm) - min_y
    return f"{a:.8f} {b:.8f} {c:.8f} {d:.8f} {e:.8f} {f:.8f} cm"


def _matrix_for_component(comp_item, abs_x_mm: float, abs_y_mm: float) -> str:
    """Rotation + translation matrix for a (possibly composite) component."""
    a,b,c,d = _rotation_matrix(comp_item.rotation_deg)
    min_x, min_y = _rotated_min_corner(comp_item.source.outline_paths_pt, comp_item.rotation_deg)
    e = mm_to_pt(abs_x_mm) - min_x
    f = mm_to_pt(abs_y_mm) - min_y
    return f"{a:.8f} {b:.8f} {c:.8f} {d:.8f} {e:.8f} {f:.8f} cm"


def _merge_form_colorspaces(resources, form) -> None:
    """Hoist named ColorSpace entries from a Form's resources to the page.

    Guards against duplicate blocks and missing resources.
    """
    fres = form.get("/Resources")
    if not fres or "/ColorSpace" not in fres:
        return
    if "/ColorSpace" not in resources:
        resources.ColorSpace = pikepdf.Dictionary()
    for cs_key, cs_val in fres.ColorSpace.items():
        resources.ColorSpace[cs_key] = cs_val


def _iter_placed_components(placed: PlacedItem):
    """Yield (OrientedItem, abs_x, abs_y) for each renderable component."""
    item = placed.item
    if getattr(item, "is_composite", False) and getattr(item, "composite_components", None):
        for comp_item, dx, dy in item.composite_components:
            yield comp_item, placed.x_mm + dx, placed.y_mm + dy
    else:
        yield item, placed.x_mm, placed.y_mm


def render_sheet(sheet: SheetLayout, output_path: Path) -> None:
    pdf = pikepdf.Pdf.new()
    ocgs = {layer: make_ocg(pdf, layer) for layer in LAYER_ORDER}
    assign_oc_properties(pdf, [ocgs[layer] for layer in ["Raster", "Outline", "White"]])
    page = pdf.add_blank_page(page_size=(mm_to_pt(sheet.width_mm), mm_to_pt(sheet.height_mm)))
    resources = ensure_page_resources(page)
    resources.XObject = pikepdf.Dictionary()
    resources.Properties = pikepdf.Dictionary({f"/OC{idx}": ocgs[layer] for idx, layer in enumerate(LAYER_ORDER)})

    cache: dict[tuple[str, str], str] = {}
    counter = 1
    layer_contents = {layer: [] for layer in LAYER_ORDER}
    for placed in sheet.placed:
        for comp_item, abs_x, abs_y in _iter_placed_components(placed):
            matrix = _matrix_for_component(comp_item, abs_x, abs_y)
            for layer in LAYER_ORDER:
                key = (str(comp_item.source.path), layer)
                if key not in cache:
                    form = _copy_layer_form(pdf, comp_item.source.path, layer)
                    if form is None:
                        continue
                    name = f"/Fm{counter}"; counter += 1
                    resources.XObject[name] = form
                    cache[key] = name
                    _merge_form_colorspaces(resources, form)
                name = cache.get(key)
                if name is None:
                    continue
                oc_name = f"/OC{LAYER_ORDER.index(layer)}"
                layer_contents[layer].append(f"/OC {oc_name} BDC q {matrix} {name} Do Q EMC")

    content = "\n".join("\n".join(layer_contents[layer]) for layer in LAYER_ORDER)
    
    # Draw Crop Marks at 4 corners of sheet (Top-Left, Top-Right, Bottom-Left, Bottom-Right)
    corners_mm = [(5.0, 5.0), (sheet.width_mm - 5.0, 5.0), (5.0, sheet.height_mm - 5.0), (sheet.width_mm - 5.0, sheet.height_mm - 5.0)]
    
    # Outline crop marks
    outline_cmds = ["q", "0 1 0 RG", "0.01 w"]
    for cx_mm, cy_mm in corners_mm:
        cx = mm_to_pt(cx_mm)
        cy = mm_to_pt(cy_mm)
        half = mm_to_pt(5.0)
        outline_cmds.append(f"{cx - half:.4f} {cy:.4f} m {cx + half:.4f} {cy:.4f} l S")
        outline_cmds.append(f"{cx:.4f} {cy - half:.4f} m {cx:.4f} {cy + half:.4f} l S")
    outline_cmds.append("Q")
    outline_str = "\n".join(outline_cmds)
    
    # Raster crop marks
    raster_cmds = ["q", "0 1 0 RG", f"{mm_to_pt(0.25):.4f} w"]
    for cx_mm, cy_mm in corners_mm:
        cx = mm_to_pt(cx_mm)
        cy = mm_to_pt(cy_mm)
        half = mm_to_pt(5.0)
        raster_cmds.append(f"{cx - half:.4f} {cy:.4f} m {cx + half:.4f} {cy:.4f} l S")
        raster_cmds.append(f"{cx:.4f} {cy - half:.4f} m {cx:.4f} {cy + half:.4f} l S")
    raster_cmds.append("Q")
    raster_str = "\n".join(raster_cmds)
    
    # Prepend OCG markers and append to layer contents
    layer_contents["Outline"].append(f"/OC /OC2 BDC\n{outline_str}\nEMC")
    layer_contents["Raster"].append(f"/OC /OC1 BDC\n{raster_str}\nEMC")

    content = "\n".join("\n".join(layer_contents[layer]) for layer in LAYER_ORDER)
    page.Contents = pdf.make_stream(content.encode("ascii"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.Root.PageMode = pikepdf.Name.UseOC
    pdf.save(output_path)


def render_sheet_layers(sheet: SheetLayout, output_path: Path) -> None:
    render_sheet(sheet, output_path)
    for layer in LAYER_ORDER:
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page(page_size=(mm_to_pt(sheet.width_mm), mm_to_pt(sheet.height_mm)))
        resources = ensure_page_resources(page)
        resources.XObject = pikepdf.Dictionary()
        cache: dict[str, str] = {}
        counter = 1
        lines = []
        for placed in sheet.placed:
            for comp_item, abs_x, abs_y in _iter_placed_components(placed):
                key = str(comp_item.source.path)
                if key not in cache:
                    form = _copy_layer_form(pdf, comp_item.source.path, layer)
                    if form is None:
                        continue
                    name = f"/Fm{counter}"; counter += 1
                    resources.XObject[name] = form
                    cache[key] = name
                    _merge_form_colorspaces(resources, form)
                matrix = _matrix_for_component(comp_item, abs_x, abs_y)
                lines.append(f"q {matrix} {cache[key]} Do Q")
        
        # Draw Crop Marks on split layers
        corners_mm = [(5.0, 5.0), (sheet.width_mm - 5.0, 5.0), (5.0, sheet.height_mm - 5.0), (sheet.width_mm - 5.0, sheet.height_mm - 5.0)]
        if layer == "Outline":
            outline_cmds = ["q", "0 1 0 RG", "0.01 w"]
            for cx_mm, cy_mm in corners_mm:
                cx = mm_to_pt(cx_mm)
                cy = mm_to_pt(cy_mm)
                half = mm_to_pt(5.0)
                outline_cmds.append(f"{cx - half:.4f} {cy:.4f} m {cx + half:.4f} {cy:.4f} l S")
                outline_cmds.append(f"{cx:.4f} {cy - half:.4f} m {cx:.4f} {cy + half:.4f} l S")
            outline_cmds.append("Q")
            lines.append("\n".join(outline_cmds))
        elif layer == "Raster":
            raster_cmds = ["q", "0 1 0 RG", f"{mm_to_pt(0.25):.4f} w"]
            for cx_mm, cy_mm in corners_mm:
                cx = mm_to_pt(cx_mm)
                cy = mm_to_pt(cy_mm)
                half = mm_to_pt(5.0)
                raster_cmds.append(f"{cx - half:.4f} {cy:.4f} m {cx + half:.4f} {cy:.4f} l S")
                raster_cmds.append(f"{cx:.4f} {cy - half:.4f} m {cx:.4f} {cy + half:.4f} l S")
            raster_cmds.append("Q")
            lines.append("\n".join(raster_cmds))
            
        page.Contents = pdf.make_stream("\n".join(lines).encode("ascii"))
        pdf.save(output_path.with_name(f"{output_path.stem}_{layer.lower()}.pdf"))
