import math
from pathlib import Path
from typing import List
import pikepdf
from .grid import PlacedItem

def draw_registration_crosses(contents: List[str], x: float, y: float, w: float, h: float):
    """Draws 0.5pt green registration crosses at the 4 corners of the specified box."""
    size = 15.0 # 15pt cross size
    # PDF color: 0 1 0 RG (Green), 0.5 w (Line weight)
    cross_code = f"q 0.5 w 0 1 0 RG "
    # Points: (x,y), (x,y+h), (x+w,y), (x+w,y+h)
    pts = [(x, y), (x, y + h), (x + w, y), (x + w, y + h)]
    for px, py in pts:
        cross_code += f"{px-size} {py} m {px+size} {py} l S {px} {py-size} m {px} {py+size} l S "
    cross_code += "Q"
    contents.append(cross_code)

def render_nested_pdf(
    pages_items: List[List[PlacedItem]],
    artboard_width_mm: float,
    artboard_height_mm: float,
    output_path: Path
):
    from ...converting.engine.units import mm_to_pt
    from ...converting.engine.pdf.renderer import make_ocg, assign_oc_properties, ensure_page_resources
    
    pdf = pikepdf.Pdf.new()
    raster_ocg = make_ocg(pdf, "Raster")
    outline_ocg = make_ocg(pdf, "Outline")
    white_ocg = make_ocg(pdf, "White")
    assign_oc_properties(pdf, [raster_ocg, outline_ocg, white_ocg])
    
    xobj_cache = {}
    xobj_counter = 1
    margin_pt = mm_to_pt(10.0) # 10mm margin for registration marks

    for page_idx, placed_items in enumerate(pages_items):
        if not placed_items: continue
        
        min_x_pt, min_y_pt = float('inf'), float('inf')
        max_x_pt, max_y_pt = float('-inf'), float('-inf')
        
        page_data = []
        for placed in placed_items:
            item = placed.item; scale = 72.0 / item.PPI
            rot_w_pt = placed.rotated_mask.shape[1] * scale
            rot_h_pt = placed.rotated_mask.shape[0] * scale
            x_left_pt = placed.x_pos * scale
            y_top_pt = mm_to_pt(artboard_height_mm) - (placed.y_pos * scale)
            y_bottom_pt = y_top_pt - rot_h_pt
            x_right_pt = x_left_pt + rot_w_pt
            
            min_x_pt = min(min_x_pt, x_left_pt)
            max_x_pt = max(max_x_pt, x_right_pt)
            min_y_pt = min(min_y_pt, y_bottom_pt)
            max_y_pt = max(max_y_pt, y_top_pt)
            page_data.append({'placed': placed, 'scale': scale})

        item_w_pt = max_x_pt - min_x_pt
        item_h_pt = max_y_pt - min_y_pt
        page_w_pt = item_w_pt + 2 * margin_pt
        page_h_pt = item_h_pt + 2 * margin_pt
        
        page = pdf.add_blank_page(page_size=(page_w_pt, page_h_pt))
        resources = ensure_page_resources(page)
        if "/XObject" not in resources:
            resources.XObject = pikepdf.Dictionary()
        layer_contents = {"Raster": ["q"], "Outline": ["q"], "White": ["q"]}
        
        for data in page_data:
            placed = data['placed']; item = placed.item; scale = data['scale']
            for layer_name in ["Raster", "Outline", "White"]:
                form_name_in_src = pikepdf.Name(f"/{layer_name}Form")
                src_pdf = pikepdf.Pdf.open(item.pdf_path)
                src_page = src_pdf.pages[0]
                if "/Resources" not in src_page or "/XObject" not in src_page.Resources: continue
                if form_name_in_src not in src_page.Resources.XObject: continue
                
                cache_key = (str(item.pdf_path), layer_name)
                if cache_key not in xobj_cache:
                    xobj = pdf.copy_foreign(src_page.Resources.XObject[form_name_in_src])
                    xobj.OC = {"Raster": raster_ocg, "Outline": outline_ocg, "White": white_ocg}[layer_name]
                    fm_name = f"/Fm{xobj_counter}"; xobj_counter += 1
                    xobj_cache[cache_key] = (fm_name, xobj)
                
                fm_name, xobj = xobj_cache[cache_key]
                resources.XObject[fm_name] = xobj
                theta_rad = math.radians(placed.rotation_deg)
                cos_t, sin_t = math.cos(theta_rad), math.sin(theta_rad)
                cx_orig_pt = (item.mask_offset_x + item.mask.shape[1]/2.0) * scale
                cy_orig_pt = item.height_pt - (item.mask_offset_y + item.mask.shape[0]/2.0) * scale
                cx_rel_pt = (placed.x_pos + placed.rotated_mask.shape[1]/2.0) * scale - min_x_pt + margin_pt
                cy_rel_pt = mm_to_pt(artboard_height_mm) - (placed.y_pos + placed.rotated_mask.shape[0]/2.0) * scale - min_y_pt + margin_pt
                tx, ty = cx_rel_pt - (cx_orig_pt * cos_t - cy_orig_pt * sin_t), cy_rel_pt - (cx_orig_pt * sin_t + cy_orig_pt * cos_t)
                matrix = f"{cos_t:.6f} {sin_t:.6f} {-sin_t:.6f} {cos_t:.6f} {tx:.6f} {ty:.6f} cm"
                layer_contents[layer_name].append(f"q {matrix} {fm_name} Do Q")
        
        final_content = []
        for layer_name in ["White", "Raster", "Outline"]:
            layer_contents[layer_name].append("Q")
            final_content.append("\n".join(layer_contents[layer_name]))
        
        # Draw registration marks on the page
        reg_marks = []
        draw_registration_crosses(reg_marks, margin_pt, margin_pt, item_w_pt, item_h_pt)
        final_content.append("\n".join(reg_marks))
        
        page.Contents = pdf.make_stream("\n".join(final_content).encode("ascii"))
        print(f"Rendered cropped page {page_idx + 1} ({page_w_pt/72*25.4:.1f}x{page_h_pt/72*25.4:.1f}mm)")

    pdf.Root.PageMode = pikepdf.Name.UseOC
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.save(output_path)
    print(f"Saved nested & cropped PDF to {output_path}")

    # --- Save individual layer PDFs ---
    layer_configs = [("White", "_white.pdf"), ("Raster", "_raster.pdf"), ("Outline", "_outline.pdf")]
    for layer_name, suffix in layer_configs:
        layer_path = output_path.with_name(f"{output_path.stem}{suffix}")
        single_layer_pdf = pikepdf.Pdf.new()
        form_name_in_src = pikepdf.Name(f"/{layer_name}Form")
        
        for page_idx, placed_items in enumerate(pages_items):
            if not placed_items: continue
            min_x_pt, min_y_pt = float('inf'), float('inf')
            max_x_pt, max_y_pt = float('-inf'), float('-inf')
            for placed in placed_items:
                scale = 72.0 / placed.item.PPI
                x_left_pt = placed.x_pos * scale
                y_top_pt = mm_to_pt(artboard_height_mm) - (placed.y_pos * scale)
                min_x_pt = min(min_x_pt, x_left_pt)
                max_x_pt = max(max_x_pt, x_left_pt + placed.rotated_mask.shape[1] * scale)
                min_y_pt = min(min_y_pt, y_top_pt - placed.rotated_mask.shape[0] * scale)
                max_y_pt = max(max_y_pt, y_top_pt)
            
            item_w_pt, item_h_pt = max_x_pt - min_x_pt, max_y_pt - min_y_pt
            page_w_pt, page_h_pt = item_w_pt + 2 * margin_pt, item_h_pt + 2 * margin_pt
            
            new_page = single_layer_pdf.add_blank_page(page_size=(page_w_pt, page_h_pt))
            res = ensure_page_resources(new_page)
            if "/XObject" not in res:
                res.XObject = pikepdf.Dictionary()
            content = ["q"]
            layer_xobj_counter = 1
            for placed in placed_items:
                item = placed.item; scale = 72.0 / item.PPI
                src_pdf = pikepdf.Pdf.open(item.pdf_path); src_page = src_pdf.pages[0]
                if "/Resources" not in src_page or "/XObject" not in src_page.Resources: continue
                if form_name_in_src not in src_page.Resources.XObject: continue
                xobj = single_layer_pdf.copy_foreign(src_page.Resources.XObject[form_name_in_src])
                fm_name = f"/Fm{layer_xobj_counter}"; layer_xobj_counter += 1
                res.XObject[fm_name] = xobj
                theta_rad = math.radians(placed.rotation_deg)
                cos_t, sin_t = math.cos(theta_rad), math.sin(theta_rad)
                cx_orig_pt = (item.mask_offset_x + item.mask.shape[1]/2.0) * scale
                cy_orig_pt = item.height_pt - (item.mask_offset_y + item.mask.shape[0]/2.0) * scale
                cx_rel_pt = (placed.x_pos + placed.rotated_mask.shape[1]/2.0) * scale - min_x_pt + margin_pt
                cy_rel_pt = mm_to_pt(artboard_height_mm) - (placed.y_pos + placed.rotated_mask.shape[0]/2.0) * scale - min_y_pt + margin_pt
                tx, ty = cx_rel_pt - (cx_orig_pt * cos_t - cy_orig_pt * sin_t), cy_rel_pt - (cx_orig_pt * sin_t + cy_orig_pt * cos_t)
                content.append(f"q {cos_t:.6f} {sin_t:.6f} {-sin_t:.6f} {cos_t:.6f} {tx:.6f} {ty:.6f} cm {fm_name} Do Q")
            
            content.append("Q")
            # Registration marks for each layer
            draw_registration_crosses(content, margin_pt, margin_pt, item_w_pt, item_h_pt)
            new_page.Contents = single_layer_pdf.make_stream("\n".join(content).encode("ascii"))
            
        single_layer_pdf.save(layer_path)
        print(f"Saved split layer PDF to {layer_path}")
