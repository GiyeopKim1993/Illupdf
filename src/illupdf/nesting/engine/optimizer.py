from typing import List, Tuple, Optional
from .parser import NestingItem
from .grid import GridMap, PlacedItem, get_rotated_mask
import numpy as np
from pathlib import Path

def calculate_hierarchical_score(
    placed_items: List[PlacedItem], 
    target_w: int, 
    target_h: int, 
    orientation: str = 'horizontal'
) -> float:
    """
    Implements user-defined hierarchical scoring:
    1. Priority 1: Keep within fixed axis limits (Hard constraint).
    2. Priority 2: Flexible axis should be close to half of current target.
    3. Priority 3: Minimize total area.
    """
    if not placed_items: return 1e25
    max_x = max(p.x_pos + p.rotated_mask.shape[1] for p in placed_items)
    max_y = max(p.y_pos + p.rotated_mask.shape[0] for p in placed_items)
    area = float(max_x * max_y)
    
    # Priority 1: Keep within target boundaries (Hard constraint)
    penalty = 0.0
    if max_x > target_w: penalty += (max_x - target_w) * 1e18
    if max_y > target_h: penalty += (max_y - target_h) * 1e18
    
    # Determine flexible axis
    if orientation == 'horizontal':
        # Width is Fixed, Height is Flexible
        flex_val = max_y
        flex_target = target_h / 2
    else:
        # Height is Fixed, Width is Flexible
        flex_val = max_x
        flex_target = target_w / 2
        
    # Priority 2: Flexible axis close to half of current stage
    # Priority 3: Minimize total area
    if flex_val > flex_target:
        # Not yet at half-way point. Prioritize shrinking flex axis.
        # Use a high baseline (1e12) so any layout below half is better.
        score = 1e12 + (flex_val - flex_target) * 1e6 + area
    else:
        # Already at or below half-way point. Prioritize area.
        score = area
        
    return score + penalty


def bottom_left_fill_squeeze(
    items: List[NestingItem],
    initial_width_px: int,
    initial_height_px: int,
    max_width_px: int,
    max_height_px: int,
    rotations: List[int] = [0, 90, 180, 270],
    orientation: str = 'horizontal'
) -> Tuple[List[PlacedItem], int, int]:
    committed_w = initial_width_px
    committed_h = initial_height_px
    
    if orientation == 'horizontal':
        # 150x100 -> 150x200 -> 300x200 -> 300x400
        stage_limits = [
            (initial_width_px, initial_height_px),
            (initial_width_px, initial_height_px * 2),
            (initial_width_px * 2, initial_height_px * 2),
            (initial_width_px * 2, initial_height_px * 4),
        ]
    else:
        # 100x150 -> 200x150 -> 200x300 -> 400x300
        stage_limits = [
            (initial_width_px, initial_height_px),
            (initial_width_px * 2, initial_height_px),
            (initial_width_px * 2, initial_height_px * 2),
            (initial_width_px * 4, initial_height_px * 2),
        ]
    
    cur_stage_idx = 0
    sorted_items = sorted(items, key=lambda it: it.mask.shape[0] * it.mask.shape[1], reverse=True)
    grid = GridMap(committed_w, committed_h)
    placed_items: List[PlacedItem] = []
    
    crop_w, crop_h = 0, 0

    print(f"[BLF] Axis-Switching Start ({orientation}). Target: {committed_w}x{committed_h}px")

    for item in sorted_items:
        placed = False
        
        # 1. Try in cropped area (hole-filling) first
        if crop_w > 0 and crop_h > 0:
            found = _place_item_in_grid(item, grid, rotations, max_w=crop_w, max_h=crop_h)
            if found:
                grid.place_item_obj(found)
                placed_items.append(found)
                crop_w = max(crop_w, found.x_pos + found.rotated_mask.shape[1])
                crop_h = max(crop_h, found.y_pos + found.rotated_mask.shape[0])
                continue

        # 2. If failed in cropped area, try in current committed stage
        while not placed:
            found = _place_item_in_grid(item, grid, rotations, max_w=committed_w, max_h=committed_h)
            if found:
                grid.place_item_obj(found)
                placed_items.append(found)
                placed = True
                crop_w = max(crop_w if crop_w else 0, found.x_pos + found.rotated_mask.shape[1])
                crop_h = max(crop_h if crop_h else 0, found.y_pos + found.rotated_mask.shape[0])
            else:
                # 3. Failed. Try expanding
                if cur_stage_idx < len(stage_limits) - 1:
                    next_w, next_h = stage_limits[cur_stage_idx + 1]
                    temp_grid = GridMap(next_w, next_h)
                    temp_grid.grid[0:committed_h, 0:committed_w] = grid.grid
                    
                    grid = temp_grid
                    committed_w, committed_h = next_w, next_h
                    cur_stage_idx += 1
                    print(f"[BLF] Item {item.pdf_path.name[:10]} forced expansion to Stage {cur_stage_idx}")
                else:
                    print(f"[BLF] ERROR: Item {item.pdf_path.name[:10]} exceeds max stage.")
                    break

    if not placed_items: return [], 0, 0
    actual_w = max(p.x_pos + p.rotated_mask.shape[1] for p in placed_items)
    actual_h = max(p.y_pos + p.rotated_mask.shape[0] for p in placed_items)
    return placed_items, int(actual_w), int(actual_h)

def _place_item_in_grid(item: NestingItem, grid: GridMap, rotations: List[int], max_w: int = None, max_h: int = None) -> Optional[PlacedItem]:
    for rot in rotations:
        rot_mask = get_rotated_mask(item, rot)
        pos = grid.find_best_placement(rot_mask, max_w=max_w, max_h=max_h)
        if pos is not None:
            return PlacedItem(item=item, x_pos=pos[0], y_pos=pos[1], rotation_deg=rot, rotated_mask=rot_mask)
    return None
