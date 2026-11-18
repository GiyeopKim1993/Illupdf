from typing import List, Tuple
from .grid import GridMap, PlacedItem, get_rotated_mask

def refine_with_axis_blf_pass(
    placed_items: List[PlacedItem],
    PPI: int,
    init_w_mm: float,
    init_h_mm: float,
    orientation: str = 'horizontal'
) -> Tuple[List[PlacedItem], int, int]:
    """
    1-Pass Axis-BLF Refiner.
    Takes an existing layout and for each item, attempts to pack it into the tightest 
    possible cropped area, simulating the Axis-BLF hole-filling behavior, without rotating.
    """
    if not placed_items: return [], 0, 0
    
    if orientation == 'horizontal':
        stage_limits = [
            (init_w_mm, init_h_mm), (init_w_mm, init_h_mm * 2),
            (init_w_mm * 2, init_h_mm * 2), (init_w_mm * 2, init_h_mm * 4)
        ]
    else:
        stage_limits = [
            (init_w_mm, init_h_mm), (init_w_mm * 2, init_h_mm),
            (init_w_mm * 2, init_h_mm * 2), (init_w_mm * 4, init_h_mm * 2)
        ]
    stage_limits_px = [(int((w/25.4)*PPI), int((h/25.4)*PPI)) for w, h in stage_limits]
    
    max_w_px = stage_limits_px[-1][0]
    max_h_px = stage_limits_px[-1][1]
    
    layout = list(placed_items)
    
    for idx in range(len(layout)):
        target_p = layout[idx]
        new_layout = [p for j, p in enumerate(layout) if j != idx]
        
        crop_w = max([p.x_pos + p.rotated_mask.shape[1] for p in new_layout]) if new_layout else 0
        crop_h = max([p.y_pos + p.rotated_mask.shape[0] for p in new_layout]) if new_layout else 0
        
        cur_stage_w, cur_stage_h = stage_limits_px[-1]
        for sw, sh in stage_limits_px:
            if crop_w <= sw and crop_h <= sh:
                cur_stage_w, cur_stage_h = sw, sh
                break
                
        grid = GridMap(max_w_px, max_h_px)
        for p in new_layout:
            grid.grid[p.y_pos : p.y_pos + p.rotated_mask.shape[0],
                      p.x_pos : p.x_pos + p.rotated_mask.shape[1]] |= p.rotated_mask.astype(bool)
                      
        mask = get_rotated_mask(target_p.item, target_p.rotation_deg)
        pos = None
        
        if crop_w > 0 and crop_h > 0:
            pos = grid.find_best_placement(mask, max_w=crop_w, max_h=crop_h)
            
        if pos is None:
            pos = grid.find_best_placement(mask, max_w=cur_stage_w, max_h=cur_stage_h)
            
        if pos is None:
            for sw, sh in stage_limits_px:
                if sw >= cur_stage_w and sh >= cur_stage_h:
                    pos = grid.find_best_placement(mask, max_w=sw, max_h=sh)
                    if pos: break
                    
        if pos:
            nx, ny = pos
            layout[idx] = PlacedItem(item=target_p.item, x_pos=nx, y_pos=ny, rotation_deg=target_p.rotation_deg, rotated_mask=mask)
            
    final_w = max([p.x_pos + p.rotated_mask.shape[1] for p in layout])
    final_h = max([p.y_pos + p.rotated_mask.shape[0] for p in layout])
    return layout, int(final_w), int(final_h)
