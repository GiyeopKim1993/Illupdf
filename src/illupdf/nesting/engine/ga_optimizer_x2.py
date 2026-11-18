import random
import math
from typing import List, Tuple
import numpy as np
import gc
import sys

from .parser import NestingItem
from .grid import GridMap, PlacedItem, get_rotated_mask

# Track previous line count for non-flickering updates
_PREV_LINE_COUNT = 0

def _print_dashboard(placed_items: List[PlacedItem], prev_items: List[PlacedItem], gen: int, total_gen: int, area_mm2: float, PPI: int, attempts: List[str]):
    """Prints a non-flickering MUD-style dashboard by overwriting previous output."""
    global _PREV_LINE_COUNT
    
    # Move cursor up to overwrite previous dashboard
    if _PREV_LINE_COUNT > 0:
        sys.stdout.write(f"\033[{_PREV_LINE_COUNT}A")
        sys.stdout.flush()

    lines = []
    lines.append("+" + "="*78 + "+")
    lines.append(f"| [X2 DASHBOARD] Gen: {gen:03d}/{total_gen} | Mode: GLOBAL     | Area: {area_mm2:,.0f} mm2 |")
    lines.append("+" + "-"*4 + "+" + "-"*32 + "+" + "----------" + "+" + "----------" + "+" + "---------" + "+")
    lines.append(f"| {'ID':<2} | {'File Name':<30} | {'dX (pt)':>8} | {'dY (pt)':>8} | {'dAngle':>7} |")
    lines.append("+" + "-"*4 + "+" + "-"*32 + "+" + "----------" + "+" + "----------" + "+" + "---------" + "+")
    
    scale = 72.0 / PPI
    for i, (p, prev) in enumerate(zip(placed_items, prev_items)):
        name = p.item.pdf_path.name[:30]
        dx_pt = (p.x_pos - prev.x_pos) * scale
        dy_pt = (p.y_pos - prev.y_pos) * scale
        da = (p.rotation_deg - prev.rotation_deg)
        if da > 180: da -= 360
        if da < -180: da += 360
        lines.append(f"| {i+1:02d} | {name:<30} | {dx_pt:+8.1f} | {dy_pt:+8.1f} | {da:+7.1f} |")
        
    lines.append("+" + "="*4 + "+" + "="*32 + "+" + "==========" + "+" + "==========" + "+" + "=========" + "+")
    
    # Ensure attempt logs have fixed height to avoid flickering
    lines.append("\n [RECENT ATTEMPTS (GLOBAL)]")
    logs = attempts[-10:]
    while len(logs) < 10:
        logs.append("")
        
    for log in logs:
        clean_log = log.replace("deg", "deg")[:75]  # Standardize logging
        lines.append(f"  > {clean_log:<75}")
        
    lines.append("-" * 80)
    
    final_output = "\n".join(lines)
    sys.stdout.write(final_output + "\n")
    sys.stdout.flush()
    
    # Calculate line count (including newlines)
    _PREV_LINE_COUNT = final_output.count("\n") + 1

def _calculate_score(placed_items: List[PlacedItem], t_w: int, t_h: int, orientation: str = 'horizontal') -> float:
    from .optimizer import calculate_hierarchical_score
    return calculate_hierarchical_score(placed_items, t_w, t_h, orientation)

def _rebuild_grid(placed_items: List[PlacedItem], width: int, height: int, exclude_idx: int = -1) -> GridMap:
    grid = GridMap(width, height)
    for i, p in enumerate(placed_items):
        if i == exclude_idx: continue
        grid.grid[p.y_pos : p.y_pos + p.rotated_mask.shape[0],
                  p.x_pos : p.x_pos + p.rotated_mask.shape[1]] |= p.rotated_mask.astype(bool)
    return grid

def refine_with_x2(
    initial_placed: List[PlacedItem],
    grid_width_px: int,
    grid_height_px: int,
    generations: int = 60,
    PPI: int = 50,
    target_w_px: int = None,
    target_h_px: int = None,
    orientation: str = 'horizontal'
) -> Tuple[List[PlacedItem], int, int]:
    global _PREV_LINE_COUNT
    _PREV_LINE_COUNT = 0

    t_w = target_w_px if target_w_px is not None else grid_width_px
    t_h = target_h_px if target_h_px is not None else grid_height_px

    n = len(initial_placed)
    steps_per_item = n * 4
    max_gens = generations if generations is not None else 12 * n * n
    min_gens = int(12 * (n * 1.5))

    placed = list(initial_placed)
    best_score = _calculate_score(placed, t_w, t_h, orientation)
    attempt_logs = []
    angle_start, angle_end = 90, 1
    no_change_count = 0

    for gen in range(max_gens):
        from copy import copy
        prev_state = [copy(p) for p in placed]
        t = gen / max(generations - 1, 1)
        max_angle = int(angle_start + t * (angle_end - angle_start))
        order = list(range(len(placed)))
        random.shuffle(order)
        any_item_changed = False

        for idx in order:
            grid_without = _rebuild_grid(placed, grid_width_px, grid_height_px, exclude_idx=idx)
            current = placed[idx]
            item_name = current.item.pdf_path.name[:15]
            best_for_item = None
            best_score_for_item = best_score

            for _ in range(steps_per_item):
                delta = random.randint(-max_angle, max_angle)
                new_rot = (current.rotation_deg + delta) % 360
                new_mask = get_rotated_mask(current.item, new_rot)
                pos = grid_without.find_best_placement(new_mask)
                if pos:
                    nx, ny = pos
                    temp_placed = [p for i, p in enumerate(placed) if i != idx]
                    from .grid import PlacedItem as PI
                    temp_placed.append(PI(item=current.item, x_pos=nx, y_pos=ny, rotation_deg=new_rot, rotated_mask=new_mask))
                    trial_score = _calculate_score(temp_placed, t_w, t_h, orientation)
                    if trial_score < best_score_for_item:
                        best_score_for_item = trial_score
                        best_for_item = (nx, ny, new_rot, new_mask)
                        attempt_logs.append(f"[OK] {item_name}: Rotate {new_rot} -> Score {trial_score:,.0f}")
                    else:
                        attempt_logs.append(f"[SKIP] {item_name}: No score improvement")
                else:
                    attempt_logs.append(f"[FAIL] {item_name}: No Space")

            if best_for_item is not None:
                nx, ny, new_rot, new_mask = best_for_item
                from .grid import PlacedItem as PI
                placed[idx] = PI(item=current.item, x_pos=nx, y_pos=ny, rotation_deg=new_rot, rotated_mask=new_mask)
                best_score = best_score_for_item
                any_item_changed = True

        max_x = max(p.x_pos + p.rotated_mask.shape[1] for p in placed)
        max_y = max(p.y_pos + p.rotated_mask.shape[0] for p in placed)
        area_mm2 = (max_x * max_y) * (25.4 / PPI) ** 2
        _print_dashboard(placed, prev_state, gen + 1, max_gens, area_mm2, PPI, attempt_logs)
        attempt_logs = []

        if any_item_changed:
            no_change_count = 0
        else:
            no_change_count += 1

        if no_change_count >= 50 and (gen + 1) >= min_gens:
            print(f"\n[GA v1] Optimization converged after {gen+1} generations (min {min_gens} reached).")
            break
        gc.collect()

    final_w = max(p.x_pos + p.rotated_mask.shape[1] for p in placed)
    final_h = max(p.y_pos + p.rotated_mask.shape[0] for p in placed)
    return placed, int(final_w), int(final_h)
