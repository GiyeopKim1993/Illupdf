"""
Nesting Pipeline (Refactored: Fast, Balanced, Best with Artboard Stages)
"""

import time
from pathlib import Path
from typing import List, Any

from .parser import parse_pdf_to_item
from .renderer import render_nested_pdf
from .optimizer import bottom_left_fill_squeeze
from .ga_optimizer_x2 import refine_with_x2
from .ga_optimizer_x5 import refine_with_x5
from .axis_blf_refiner import refine_with_axis_blf_pass
from .events import NestingEvent


def run_nesting_pipeline(
    input_pdfs: List[Path],
    output_pdf_base: Path,
    artboard_width_mm: float = 150.0,
    artboard_height_mm: float = 100.0,
    max_width_mm: float = 300.0,
    max_height_mm: float = 400.0,
    PPI: int = 50,
    generations: int = 40,
    mode: str = 'balanced', # 'fast', 'balanced', 'best'
    observer=None,
):
    def emit(event: NestingEvent) -> None:
        if observer is not None:
            observer(event)

    def mm_to_px(mm): return int((mm / 25.4) * PPI)
    def px_to_mm(px): return (px * 25.4) / PPI

    # Artboard Stage Limits (Fixed Increments)
    # stage_limits = [
    #     (150, 100), (150, 200), (300, 200), (300, 400)
    # ]
    # Converting to PX
    stages_px = [
        (mm_to_px(150), mm_to_px(100)),
        (mm_to_px(150), mm_to_px(200)),
        (mm_to_px(300), mm_to_px(200)),
        (mm_to_px(300), mm_to_px(400))
    ]

    emit(NestingEvent(event_type="start", num_items=len(input_pdfs), artboard_w_mm=artboard_width_mm, artboard_h_mm=artboard_height_mm, total_generations=generations, message=f"Nesting pipeline started: {mode}"))
    print(f"[Nesting] Pipeline Started | Mode: {mode.upper()}")
    print(f"[Nesting] Initial Artboard: {artboard_width_mm}x{artboard_height_mm}mm | PPI: {PPI}")

    # --- Step 1: Parse ---
    items = [parse_pdf_to_item(p, PPI=PPI) for p in input_pdfs]
    
    # --- Helper for Rendering ---
    results = {}
    def save_res(name: str, placed_list: List[Any], w_px: int, h_px: int):
        out_path = output_pdf_base.parent / f"{output_pdf_base.stem}_{name}.pdf"
        w_mm, h_mm = px_to_mm(w_px), px_to_mm(h_px)
        
        # Select the smallest artboard stage that fits the result
        final_art_w, final_art_h = w_mm, h_mm
        for sw_px, sh_px in stages_px:
            if w_px <= sw_px and h_px <= sh_px:
                final_art_w, final_art_h = px_to_mm(sw_px), px_to_mm(sh_px)
                break
        
        render_nested_pdf([placed_list], final_art_w, final_art_h, out_path)
        message = f"Saved [{name}]: Result({w_mm:.1f}x{h_mm:.1f}mm) -> Artboard({final_art_w:.1f}x{final_art_h:.1f}mm)"
        print(f" >> {message}")
        results[name] = (final_art_w, final_art_h, final_art_w * final_art_h)
        emit(NestingEvent(event_type="result", artboard_w_mm=final_art_w, artboard_h_mm=final_art_h, num_items=len(placed_list), message=message, done=True))

    def save_final(name: str, items: List[Any], w: int, h: int):
        final_items, final_w, final_h = refine_with_axis_blf_pass(
            items, PPI, px_to_mm(stages_px[-1][0]), px_to_mm(stages_px[-1][1]), 'horizontal'
        )
        save_res(name, final_items, final_w, final_h)

    # --- Stage 1: Fast (BLF Baseline with Auto-Expansion) ---
    v0_items, v0_w, v0_h = bottom_left_fill_squeeze(
        items=items, 
        initial_width_px=stages_px[0][0], 
        initial_height_px=stages_px[0][1],
        max_width_px=stages_px[-1][0], 
        max_height_px=stages_px[-1][1], 
        rotations=[0, 90, 180, 270]
    )
    
    if mode == 'fast':
        save_final("FAST", v0_items, v0_w, v0_h)
        return results

    # Find which stage V0 ended up in
    target_w_px, target_h_px = stages_px[-1]
    for sw, sh in stages_px:
        if v0_w <= sw and v0_h <= sh:
            target_w_px, target_h_px = sw, sh
            break

    # --- Stage 2: Balanced (Global Explorer X2) ---
    if mode == 'balanced':
        print(f"\n[PIPELINE] Running Balanced Mode (Global Explorer)... Target: {px_to_mm(target_w_px)}x{px_to_mm(target_h_px)}mm")
        v_items, v_w, v_h = refine_with_x2(
            v0_items, target_w_px, target_h_px, generations, PPI, target_w_px, target_h_px, orientation='horizontal'
        )
        save_final("BALANCED", v_items, v_w, v_h)
        return results

    # --- Stage 3: Best (Micro Finisher X5) ---
    if mode == 'best':
        print(f"\n[PIPELINE] Running Best Mode (Micro Finisher)... Target: {px_to_mm(target_w_px)}x{px_to_mm(target_h_px)}mm")
        v_bal_items, v_bal_w, v_bal_h = refine_with_x2(
            v0_items, target_w_px, target_h_px, generations // 2, PPI, target_w_px, target_h_px, orientation='horizontal'
        )
        v_best_items, v_best_w, v_best_h = refine_with_x5(
            v_bal_items, target_w_px, target_h_px, generations, PPI, target_w_px, target_h_px, orientation='horizontal'
        )
        save_final("BEST", v_best_items, v_best_w, v_best_h)
        return results

    return results
