from __future__ import annotations
import zlib
from pathlib import Path
from PIL import Image
import pikepdf

from ..models import PreparedArtwork
from .colors import resolve_white_spot, DEFAULT_WHITE_SPOT
from ..vector.white import make_white_separation_colorspace, make_separation_image_xobject


def make_ocg(pdf: pikepdf.Pdf, name: str) -> pikepdf.Object:
    return pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.OCG,
        Name=pikepdf.String(name),
    ))


def assign_oc_properties(pdf: pikepdf.Pdf, ocgs: list[pikepdf.Object]) -> None:
    pdf.Root.OCProperties = pikepdf.Dictionary(
        OCGs=pikepdf.Array(ocgs),
        D=pikepdf.Dictionary(
            Order=pikepdf.Array(ocgs),
            ON=pikepdf.Array(ocgs),
            Off=pikepdf.Array([]),
        ),
    )


def ensure_page_resources(page: pikepdf.Page) -> pikepdf.Dictionary:
    if "/Resources" not in page:
        page.Resources = pikepdf.Dictionary()
    return page.Resources


def make_form_xobject(
    pdf: pikepdf.Pdf,
    page_size_pt: tuple[float, float],
    resources: pikepdf.Dictionary,
    content: str,
    layer_ocg: pikepdf.Object | None = None,
) -> pikepdf.Stream:
    form_dict = pikepdf.Dictionary(
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Form,
        BBox=pikepdf.Array([0.0, 0.0, page_size_pt[0], page_size_pt[1]]),
        Resources=resources,
    )
    if layer_ocg is not None:
        form_dict.OC = layer_ocg
    
    return pdf.make_stream(content.encode("latin-1"), form_dict)


def make_white_smask_xobject(pdf: pikepdf.Pdf, mask: Image.Image) -> pikepdf.Object:
    """Build a DeviceGray SMask stream from the white coverage mask (0/255)."""
    return pdf.make_stream(
        zlib.compress(mask.tobytes(), level=9),
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=mask.width,
        Height=mask.height,
        ColorSpace=pikepdf.Name.DeviceGray,
        BitsPerComponent=8,
        Filter=pikepdf.Name.FlateDecode,
    )


def make_raster_image_xobject(pdf: pikepdf.Pdf, image: Image.Image) -> pikepdf.Stream:
    w, h = image.size
    if "A" in image.getbands():
        rgb_image = image.convert("RGB")
        alpha = image.getchannel("A")
        
        # Create SMask XObject
        alpha_bytes = alpha.tobytes()
        smask = pdf.make_stream(
            zlib.compress(alpha_bytes, level=9),
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=w,
            Height=h,
            ColorSpace=pikepdf.Name.DeviceGray,
            BitsPerComponent=8,
            Filter=pikepdf.Name.FlateDecode,
        )
        
        # Create main Image XObject
        rgb_bytes = rgb_image.tobytes()
        img = pdf.make_stream(
            zlib.compress(rgb_bytes, level=9),
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=w,
            Height=h,
            ColorSpace=pikepdf.Name.DeviceRGB,
            BitsPerComponent=8,
            Filter=pikepdf.Name.FlateDecode,
            SMask=smask,
        )
    else:
        rgb_bytes = image.convert("RGB").tobytes()
        img = pdf.make_stream(
            zlib.compress(rgb_bytes, level=9),
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=w,
            Height=h,
            ColorSpace=pikepdf.Name.DeviceRGB,
            BitsPerComponent=8,
            Filter=pikepdf.Name.FlateDecode,
        )
    return img


def build_combined_pdf(prepared: PreparedArtwork, combined_pdf_path: Path) -> None:
    from .content import build_raster_layer_content, build_outline_layer_content
    from ..vector.white import make_white_raster_form_xobject
    
    pdf = pikepdf.Pdf.new()
    
    # 1. Create OCGs
    raster_ocg = make_ocg(pdf, "Raster")
    outline_ocg = make_ocg(pdf, "Outline")
    white_ocg = make_ocg(pdf, "White")
    assign_oc_properties(pdf, [raster_ocg, outline_ocg, white_ocg])
    
    # 2. Build form XObjects for the layers
    # Raster layer
    raster_img_xobj = make_raster_image_xobject(pdf, prepared.rgba_image)
    raster_res = pikepdf.Dictionary(XObject=pikepdf.Dictionary())
    raster_res.XObject["/ImRaster"] = raster_img_xobj
    
    raster_content = build_raster_layer_content(prepared, pikepdf.Name("/ImRaster"))
    raster_form = make_form_xobject(
        pdf=pdf,
        page_size_pt=prepared.page_size_pt,
        resources=raster_res,
        content=raster_content,
        layer_ocg=raster_ocg
    )
    
    # Outline layer
    outline_content = build_outline_layer_content(prepared)
    outline_form = make_form_xobject(
        pdf=pdf,
        page_size_pt=prepared.page_size_pt,
        resources=pikepdf.Dictionary(),
        content=outline_content,
        layer_ocg=outline_ocg
    )
    
    # White layer: Form XObject kept for nesting renderer compatibility (layer_ocg=None avoids
    # double-OCG conflicts since page content already wraps drawing in BDC/EMC blocks)
    white_spot = resolve_white_spot(DEFAULT_WHITE_SPOT)
    white_form = make_white_raster_form_xobject(
        pdf=pdf,
        prepared=prepared,
        white_spot=white_spot,
        layer_ocg=None,
    )
    
    # White image for DIRECT page drawing.
    # Key fix: embed Separation colorspace as an inline Array directly in the image stream,
    # NOT as a named /CSWhite reference looked up from Form resources.
    # Many viewers (Illustrator, pdf.js) fail to resolve named colorspaces from Form XObject
    # Resources when the Form is nested inside a page. This matches how alpha_to_white_pdf.py works.
    white_colorspace_inline = make_white_separation_colorspace(pdf, white_spot)
    w, h = prepared.white_raster_img.size
    raw_white_bytes = prepared.white_raster_img.tobytes()
    white_img_direct = pdf.make_stream(
        zlib.compress(raw_white_bytes, level=9),
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=w,
        Height=h,
        ColorSpace=white_colorspace_inline,
        BitsPerComponent=8,
        Filter=pikepdf.Name.FlateDecode,
    )
    if prepared.white_coverage_mask is not None:
        white_img_direct.SMask = make_white_smask_xobject(pdf, prepared.white_coverage_mask)
    
    # Spot_1 plate: flat 25% fill inside the coverage mask (Separation[/Spot_1])
    spot1_img_direct = None
    if prepared.white_spot1_img is not None:
        from ..models import SpotColorDefinition
        spot1 = SpotColorDefinition(name="Spot_1", cmyk=(0.0, 0.0, 0.0, 1.0))
        spot1_colorspace_inline = make_white_separation_colorspace(pdf, spot1)
        w1, h1 = prepared.white_spot1_img.size
        spot1_img_direct = pdf.make_stream(
            zlib.compress(prepared.white_spot1_img.tobytes(), level=9),
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=w1,
            Height=h1,
            ColorSpace=spot1_colorspace_inline,
            BitsPerComponent=8,
            Filter=pikepdf.Name.FlateDecode,
        )
        if prepared.white_coverage_mask is not None:
            spot1_img_direct.SMask = make_white_smask_xobject(pdf, prepared.white_coverage_mask)
    
    # 3. Create the page
    page = pdf.add_blank_page(page_size=prepared.page_size_pt)
    page_resources = ensure_page_resources(page)
    
    # Form XObjects in page resources (needed by nesting renderer)
    page_resources.XObject = pikepdf.Dictionary()
    page_resources.XObject["/RasterForm"] = raster_form
    page_resources.XObject["/OutlineForm"] = outline_form
    page_resources.XObject["/WhiteForm"] = white_form
    # Direct white image (used by page content stream for display)
    page_resources.XObject["/ImWhite"] = white_img_direct
    if spot1_img_direct is not None:
        page_resources.XObject["/ImWhiteSpot1"] = spot1_img_direct
    
    # OCG Properties
    page_resources.Properties = pikepdf.Dictionary()
    page_resources.Properties["/MC0"] = white_ocg
    page_resources.Properties["/MC1"] = raster_ocg
    page_resources.Properties["/MC2"] = outline_ocg
    
    # Page content stream:
    # White drawn directly with matrix + inline colorspace (identical to standalone white PDF).
    # Raster/Outline drawn via Form XObjects.
    raster_width_pt, raster_height_pt = prepared.raster_size_pt
    raster_origin_x, raster_origin_y = prepared.raster_origin_pt
    from .content import format_number, paths_to_pdf_commands
    
    clip_paths = prepared.outline_outer_paths_pt or prepared.base_paths_pt
    clip_cmds = paths_to_pdf_commands(clip_paths, paint_operator="W n") if clip_paths else ""
    white_clip_prefix = f"{clip_cmds}\n" if clip_cmds else ""

    spot1_draw = "/ImWhiteSpot1 Do\n" if spot1_img_direct is not None else ""

    content = (
        "/OC /MC0 BDC\n"
        "q\n"
        f"{white_clip_prefix}"
        f"{format_number(raster_width_pt)} 0 0 {format_number(raster_height_pt)} "
        f"{format_number(raster_origin_x)} {format_number(raster_origin_y)} cm\n"
        f"{spot1_draw}"
        "/ImWhite Do\n"
        "Q\n"
        "EMC\n"
        "/OC /MC1 BDC\n"
        "q\n"
        "/RasterForm Do\n"
        "Q\n"
        "EMC\n"
        "/OC /MC2 BDC\n"
        "q\n"
        "/OutlineForm Do\n"
        "Q\n"
        "EMC\n"
    )
    page.Contents = pdf.make_stream(content.encode("latin-1"))
    
    pdf.Root.PageMode = pikepdf.Name.UseOC
    pdf.save(combined_pdf_path)


def build_split_layer_pdf(prepared: PreparedArtwork, layer_pdf_path: Path, layer_name: str) -> None:
    from .content import build_raster_layer_content, build_outline_layer_content
    
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=prepared.page_size_pt)
    page_resources = ensure_page_resources(page)
    
    if layer_name == "Raster":
        # Main raster image
        raster_img_xobj = make_raster_image_xobject(pdf, prepared.rgba_image)
        page_resources.XObject = pikepdf.Dictionary()
        page_resources.XObject["/ImRaster"] = raster_img_xobj
        
        content = build_raster_layer_content(prepared, pikepdf.Name("/ImRaster"))
        page.Contents = pdf.make_stream(content.encode("latin-1"))
        
    elif layer_name == "Outline":
        content = build_outline_layer_content(prepared)
        page.Contents = pdf.make_stream(content.encode("latin-1"))
        
    elif layer_name == "White":
        from .content import format_number, pdf_name_token, paths_to_pdf_commands
        # White raster image
        white_spot = resolve_white_spot(DEFAULT_WHITE_SPOT)
        
        # Create OCG "White"
        ocg = make_ocg(pdf, "White")
        assign_oc_properties(pdf, [ocg])
        
        # Create Separation Colorspace Array
        white_colorspace = make_white_separation_colorspace(pdf, white_spot)
        
        # Create image XObject with inline colorspace
        w, h = prepared.white_raster_img.size
        raw_bytes = prepared.white_raster_img.tobytes()
        white_img_xobj = pdf.make_stream(
            zlib.compress(raw_bytes, level=9),
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=w,
            Height=h,
            ColorSpace=white_colorspace,
            BitsPerComponent=8,
            Filter=pikepdf.Name.FlateDecode,
        )
        if prepared.white_coverage_mask is not None:
            white_img_xobj.SMask = make_white_smask_xobject(pdf, prepared.white_coverage_mask)
        
        page_resources.XObject = pikepdf.Dictionary()
        
        # Spot_1 plate (flat 25% fill inside the coverage mask)
        spot1_draw = ""
        if prepared.white_spot1_img is not None:
            from ..models import SpotColorDefinition
            spot1 = SpotColorDefinition(name="Spot_1", cmyk=(0.0, 0.0, 0.0, 1.0))
            spot1_colorspace = make_white_separation_colorspace(pdf, spot1)
            w1, h1 = prepared.white_spot1_img.size
            spot1_img_xobj = pdf.make_stream(
                zlib.compress(prepared.white_spot1_img.tobytes(), level=9),
                Type=pikepdf.Name.XObject,
                Subtype=pikepdf.Name.Image,
                Width=w1,
                Height=h1,
                ColorSpace=spot1_colorspace,
                BitsPerComponent=8,
                Filter=pikepdf.Name.FlateDecode,
            )
            if prepared.white_coverage_mask is not None:
                spot1_img_xobj.SMask = make_white_smask_xobject(pdf, prepared.white_coverage_mask)
            page_resources.XObject["/ImWhiteSpot1"] = spot1_img_xobj
            spot1_draw = f"{pdf_name_token(pikepdf.Name('/ImWhiteSpot1'))} Do\n"
        
        # Register resources
        page_resources.XObject["/ImWhite"] = white_img_xobj
        
        page_resources.Properties = pikepdf.Dictionary()
        page_resources.Properties["/MC0"] = ocg
        
        raster_width_pt, raster_height_pt = prepared.raster_size_pt
        raster_origin_x, raster_origin_y = prepared.raster_origin_pt
        
        clip_paths = []
        clip_cmds = ""
        white_clip_prefix = ""

        # Wrapped inside OCG marked content
        content = (
            "/OC /MC0 BDC\n"
            "q\n"
            f"{white_clip_prefix}"
            f"{format_number(raster_width_pt)} 0 0 {format_number(raster_height_pt)} "
            f"{format_number(raster_origin_x)} {format_number(raster_origin_y)} cm\n"
            f"{spot1_draw}"
            f"{pdf_name_token(pikepdf.Name('/ImWhite'))} Do\n"
            "Q\n"
            "EMC\n"
        )
        page.Contents = pdf.make_stream(content.encode("latin-1"))
        
    else:
        raise ValueError(f"Unknown layer name: {layer_name}")
        
    pdf.save(layer_pdf_path)
