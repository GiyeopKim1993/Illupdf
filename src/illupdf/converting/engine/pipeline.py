from __future__ import annotations
import numpy as np
from PIL import Image
from pathlib import Path

from .errors import PdfGenerationError
from .models import TraceFrame, TraceObserver, KeyringCandidate, PreparedArtwork, PathPoints
from .raster.loader import load_rgba_image, crop_to_alpha_bounds, add_transparent_padding
from .raster.normalize import compute_target_size_pixels, image_to_b64
from .raster.masks import build_custom_mask
from .vector.contours import extract_significant_outer_contours, simplify_contour, contour_to_pdf_path
from .geometry.polygons import translate_path, geometry_bounds, keep_largest_path_only, extend_line_to_bbox, signed_path_area, polygon_area_and_centroid
from .geometry.clipping import offset_paths, cleanup_tiny_protrusions
from .vector.layout import translate_to_page, translate_keyring_balance
from .vector.white import extract_white_grayscale, build_white_spot_tint_with_mask, build_spot1_flat_fill
from .units import mm_to_px, px_to_pt, pt_to_mm, WORKING_ALPHA_MARGIN_MM

CALIBRATION_TOLERANCE_MM = 1.0
CALIBRATION_MAX_ITERATIONS = 3


def emit_trace(observer: TraceObserver | None, frame: TraceFrame) -> None:
    if observer is not None:
        enrich_trace_frame(frame)
        observer(frame)


def enrich_trace_frame(frame: TraceFrame) -> None:
    if frame.bbox_pt:
        min_x, min_y, max_x, max_y = frame.bbox_pt
        width_pt = max_x - min_x
        height_pt = max_y - min_y
        frame.bbox_width_mm = pt_to_mm(width_pt)
        frame.bbox_height_mm = pt_to_mm(height_pt)
        frame.bbox_area_mm2 = frame.bbox_width_mm * frame.bbox_height_mm
    if frame.outer_paths_pt:
        total_area_pt = sum(abs(signed_path_area(p)) for p in frame.outer_paths_pt)
        frame.outline_area_mm2 = total_area_pt * (pt_to_mm(1.0) ** 2)


def make_keyring_trace_frame(*, preset_name: str, stage: str, message: str, candidate: KeyringCandidate, iteration: int, status: str) -> TraceFrame:
    bbox_pt = geometry_bounds(paths_groups=([candidate.outer_path_pt], [candidate.hole_path_pt], [candidate.boss_path_pt]), points=(candidate.hole_center_pt, candidate.centroid_pt))
    line_pt = extend_line_to_bbox(candidate.hole_center_pt, candidate.centroid_pt, bbox_pt)
    return TraceFrame(
        stage=stage,
        message=message,
        preset_name=preset_name,
        status=status,
        iteration=iteration,
        outer_paths_pt=[candidate.outer_path_pt],
        hole_paths_pt=[candidate.hole_path_pt],
        boss_paths_pt=[candidate.boss_path_pt],
        vertical_line_pt=((candidate.rx_pt, bbox_pt[1]), (candidate.rx_pt, bbox_pt[3])),
        boss_center_pt=candidate.hole_center_pt,
        centroid_pt=candidate.centroid_pt,
        outer_centroid_pt=candidate.outer_centroid_pt,
        centroid_line_pt=line_pt,
        theta_rad=candidate.theta_rad,
        bbox_pt=bbox_pt,
    )


def emit_raster_trace(observer: TraceObserver | None, stage: str, message: str, image: Image.Image, rect_pt: tuple[float, float, float, float], preset_name: str) -> None:
    # Fast path: without an observer (e.g. parallel CLI workers) there is
    # nobody consuming the trace, so skip the expensive resize + PNG base64
    # encoding entirely. This avoids ~40% of prepare_artwork runtime.
    if observer is None:
        return
    max_viz_size = 1024
    if max(image.width, image.height) > max_viz_size:
        scale = max_viz_size / max(image.width, image.height)
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), resample=Image.Resampling.LANCZOS)
    emit_trace(observer, TraceFrame(stage=stage, message=message, preset_name=preset_name, raster_b64=image_to_b64(image), raster_rect_pt=rect_pt, bbox_pt=(rect_pt[0], rect_pt[1], rect_pt[0] + rect_pt[2], rect_pt[1] + rect_pt[3])))


def final_outline_long_side_mm(prepared: PreparedArtwork) -> float:
    bounds = geometry_bounds(paths_groups=(prepared.outline_outer_paths_pt,))
    return max(pt_to_mm(bounds[2] - bounds[0]), pt_to_mm(bounds[3] - bounds[1]))


def estimate_initial_raster_long_side_mm(*, cropped: Image.Image, preset_name: str, target_final_size_mm: float) -> float:
    from .keyring.reinforcement import OUTLINE_EXPAND_MM, OUTLINE_SHRINK_MM

    net_growth_one_side = OUTLINE_EXPAND_MM - OUTLINE_SHRINK_MM
    predicted_growth = net_growth_one_side * 2
    if preset_name == "keyring":
        predicted_growth = net_growth_one_side + 5.0 if cropped.height >= cropped.width else net_growth_one_side * 2
    return max(1.0, target_final_size_mm - predicted_growth)


def prepare_artwork_for_raster_target(
    *,
    cropped: Image.Image,
    preset_name: str,
    target_raster_long_side_mm: float,
    observer: TraceObserver | None = None,
    mask_alpha_threshold: int = 8,
    skip_white: bool = False,
) -> PreparedArtwork:
    from .keyring.reinforcement import build_base_outline_path, OUTLINE_EXPAND_MM, OUTLINE_SHRINK_MM
    from .keyring.balance import build_keyring_balance
    from .units import mm_to_pt
    from .presets import PRESETS

    preset = PRESETS[preset_name]
    target_size = compute_target_size_pixels(cropped.width, cropped.height, target_raster_long_side_mm)
    resized = cropped.resize(target_size, Image.Resampling.LANCZOS)
    emit_raster_trace(observer, "normalized", f"Resized to target raster size ({target_raster_long_side_mm:.2f}mm).", resized, (0, 0, px_to_pt(resized.width), px_to_pt(resized.height)), preset_name)

    working_padding_px = mm_to_px(WORKING_ALPHA_MARGIN_MM)
    working_image = add_transparent_padding(resized, working_padding_px)
    working_padding_pt = px_to_pt(working_padding_px)

    rgba_np = np.array(working_image, dtype=np.uint8)
    mask = build_custom_mask(rgba_np, threshold=mask_alpha_threshold)
    contours = extract_significant_outer_contours(mask)
    contour_paths_pt: list[PathPoints] = []
    for contour in contours:
        contour_paths_pt.append(translate_path(contour_to_pdf_path(simplify_contour(contour), working_image.height), -working_padding_pt, -working_padding_pt))

    filtered_paths_pt: list[PathPoints] = []
    for path in contour_paths_pt:
        area_mm2 = abs(signed_path_area(path)) / (mm_to_pt(1.0) ** 2)
        if area_mm2 >= 2.0:
            filtered_paths_pt.append(path)
    if not filtered_paths_pt:
        raise PdfGenerationError("no significant outline islands after area filtering")

    base_path_pt = build_base_outline_path(filtered_paths_pt)
    radius_pt = mm_to_pt(0.1)
    opened_shrunk = keep_largest_path_only(offset_paths([base_path_pt], -radius_pt))
    if opened_shrunk:
        opened_expanded = keep_largest_path_only(offset_paths(opened_shrunk, radius_pt))
        if opened_expanded:
            base_path_pt = opened_expanded[0]
    closed_expanded = keep_largest_path_only(offset_paths([base_path_pt], radius_pt))
    if closed_expanded:
        closed_shrunk = keep_largest_path_only(offset_paths(closed_expanded, -radius_pt))
        if closed_shrunk:
            base_path_pt = closed_shrunk[0]
    emit_trace(observer, TraceFrame(stage="base_outline", message="Base outline extracted from the alpha silhouette and attached islands.", preset_name=preset_name, outer_paths_pt=[base_path_pt], bbox_pt=geometry_bounds(paths_groups=([base_path_pt],))))

    expanded_outline_pt = keep_largest_path_only(offset_paths([base_path_pt], mm_to_pt(OUTLINE_EXPAND_MM)))
    emit_trace(observer, TraceFrame(stage="outline_expand", message="Round +5mm offset applied to the base contour.", preset_name=preset_name, outer_paths_pt=expanded_outline_pt, bbox_pt=geometry_bounds(paths_groups=(expanded_outline_pt,))))
    outline_outer_paths_pt = keep_largest_path_only(offset_paths(expanded_outline_pt, -mm_to_pt(OUTLINE_SHRINK_MM)))
    if not outline_outer_paths_pt:
        raise PdfGenerationError("outline layer generation produced an empty result")
    emit_trace(observer, TraceFrame(stage="outline_shrink", message="Round -3mm offset applied to build the cutline.", preset_name=preset_name, outer_paths_pt=outline_outer_paths_pt, bbox_pt=geometry_bounds(paths_groups=(outline_outer_paths_pt,))))

    outline_inner_paths_pt: list[PathPoints] = []
    keyring_balance = None
    if preset_name == "keyring" and preset.has_boss:
        keyring_balance = build_keyring_balance(outline_outer_paths_pt[0], observer=observer)
        outline_outer_paths_pt = [keyring_balance.outer_path_pt]
        outline_inner_paths_pt = [keyring_balance.hole_path_pt]
        _kr_outer_area, _kr_outer_centroid = polygon_area_and_centroid(keyring_balance.outer_path_pt)
        emit_trace(observer, make_keyring_trace_frame(preset_name=preset_name, stage="keyring_result", message="Keyring boss and hole placement finalized.", candidate=KeyringCandidate(rx_pt=keyring_balance.hole_center_pt[0], error_x_pt=keyring_balance.centroid_pt[0] - keyring_balance.hole_center_pt[0], theta_rad=keyring_balance.theta_rad, outer_path_pt=keyring_balance.outer_path_pt, hole_path_pt=keyring_balance.hole_path_pt, boss_path_pt=keyring_balance.boss_path_pt, hole_center_pt=keyring_balance.hole_center_pt, centroid_pt=keyring_balance.centroid_pt, outer_centroid_pt=_kr_outer_centroid), iteration=keyring_balance.iterations, status="final"))

    outline_bounds = geometry_bounds(paths_groups=(outline_outer_paths_pt,))
    outline_long_side_mm = max(pt_to_mm(outline_bounds[2] - outline_bounds[0]), pt_to_mm(outline_bounds[3] - outline_bounds[1]))
    if outline_long_side_mm >= 20.0:
        cleaned_outline_outer_paths_pt = cleanup_tiny_protrusions(outline_outer_paths_pt, opening_gap_mm=0.2)
        if cleaned_outline_outer_paths_pt:
            outline_outer_paths_pt = cleaned_outline_outer_paths_pt
            emit_trace(observer, TraceFrame(stage="outline_cleanup", message="Tiny final cutline protrusions removed with open/intersect cleanup.", preset_name=preset_name, outer_paths_pt=outline_outer_paths_pt, bbox_pt=geometry_bounds(paths_groups=(outline_outer_paths_pt,))))

    # Release large temporary raster/mask buffers before building and storing the white raster.
    del rgba_np, mask, working_image, contours, contour_paths_pt, filtered_paths_pt

    if skip_white:
        white_raster_img = Image.new("L", resized.size, 255)
        white_coverage_mask = Image.new("L", resized.size, 0)
    else:
        # invert=True(기본): 색반전 → 어두운 원화일수록 높은 값(흰 잉크↑).
        # 이 값들이 Spot_0(기본 화이트 틴트) 분판으로 들어간다.
        white_raster_img, white_coverage_mask = build_white_spot_tint_with_mask(resized)
    emit_raster_trace(observer, "white_mask", "White layer: grayscale spot tint with 85/255 output levels and inset alpha coverage mask.", white_raster_img, (0, 0, px_to_pt(white_raster_img.width), px_to_pt(white_raster_img.height)), preset_name)

    # Spot_1 플레이트: 기본 화이트 스팟이 'spot_0'일 때만 생성한다.
    # 알파 아웃라인을 0.15mm 수축한 커버리지 마스크 내부를 25% 고정으로 채운다.
    # (CMYK 등가 0,0,0,1 / Spot_1)
    # rdg_white / sswhite / white 등 다른 스팟을 쓸 때는 Spot_1 플레이트를 만들지 않는다.
    from .pdf.colors import DEFAULT_WHITE_SPOT
    white_spot1_img = None
    if (not skip_white) and DEFAULT_WHITE_SPOT == "spot_0":
        white_spot1_img = build_spot1_flat_fill(white_coverage_mask, fill_ratio=0.25)
    white_source_paths_pt: list[PathPoints] = []
    white_paths_pt: list[PathPoints] = []

    translated_base, translated_outline_outer, translated_outline_inner, translated_white_source, translated_white, raster_origin_pt, page_size_pt = translate_to_page(
        base_paths_pt=[base_path_pt],
        outline_outer_paths_pt=outline_outer_paths_pt,
        outline_inner_paths_pt=outline_inner_paths_pt,
        white_source_paths_pt=white_source_paths_pt,
        white_paths_pt=white_paths_pt,
        raster_size_pt=(px_to_pt(resized.width), px_to_pt(resized.height)),
    )

    translated_balance = translate_keyring_balance(keyring_balance, *raster_origin_pt) if keyring_balance is not None else None
    emit_trace(observer, TraceFrame(stage="final", message="Final layered composition on output page.", preset_name=preset_name, status="done", outer_paths_pt=translated_outline_outer, hole_paths_pt=translated_outline_inner, raster_b64=image_to_b64(resized) if observer is not None else None, raster_rect_pt=(*raster_origin_pt, px_to_pt(resized.width), px_to_pt(resized.height)), bbox_pt=(0, 0, *page_size_pt)))

    return PreparedArtwork(
        preset_name=preset_name,
        rgba_image=resized,
        base_paths_pt=translated_base,
        outline_paths_pt=translated_outline_outer + translated_outline_inner,
        outline_outer_paths_pt=translated_outline_outer,
        outline_inner_paths_pt=translated_outline_inner,
        white_source_paths_pt=translated_white_source,
        white_paths_pt=translated_white,
        raster_origin_pt=raster_origin_pt,
        raster_size_pt=(px_to_pt(resized.width), px_to_pt(resized.height)),
        page_size_pt=page_size_pt,
        keyring_balance=translated_balance,
        white_raster_img=white_raster_img,
        white_coverage_mask=white_coverage_mask,
        white_spot1_img=white_spot1_img,
    )


def calibrate_raster_long_side_mm(
    *,
    cropped: Image.Image,
    preset_name: str,
    target_final_size_mm: float,
    initial_raster_long_side_mm: float,
    mask_alpha_threshold: int,
    tolerance_mm: float = CALIBRATION_TOLERANCE_MM,
    max_iterations: int = CALIBRATION_MAX_ITERATIONS,
) -> tuple[float, float]:
    import gc

    def measure(raster_mm: float) -> float:
        prepared = prepare_artwork_for_raster_target(
            cropped=cropped,
            preset_name=preset_name,
            target_raster_long_side_mm=max(1.0, raster_mm),
            observer=None,
            mask_alpha_threshold=mask_alpha_threshold,
            skip_white=True,
        )
        actual = final_outline_long_side_mm(prepared)
        del prepared
        gc.collect()
        return actual

    initial = max(1.0, initial_raster_long_side_mm)
    best_raster = initial
    best_actual = measure(initial)
    best_error = abs(best_actual - target_final_size_mm)
    if best_error <= tolerance_mm:
        return best_raster, best_actual

    # Direct ratio scaling (converges in 1 step)
    scaled_raster = max(1.0, initial * (target_final_size_mm / best_actual))
    scaled_actual = measure(scaled_raster)
    scaled_error = abs(scaled_actual - target_final_size_mm)

    if scaled_error <= tolerance_mm or scaled_error < best_error:
        best_raster, best_actual, best_error = scaled_raster, scaled_actual, scaled_error

    if best_error <= tolerance_mm:
        return best_raster, best_actual

    low_raster = min(initial, scaled_raster)
    high_raster = max(initial, scaled_raster)
    for _ in range(max_iterations):
        mid_raster = (low_raster + high_raster) / 2.0
        mid_actual = measure(mid_raster)
        err = abs(mid_actual - target_final_size_mm)
        if err < best_error:
            best_raster, best_actual, best_error = mid_raster, mid_actual, err
        if err <= tolerance_mm:
            break
        if mid_actual > target_final_size_mm:
            high_raster = mid_raster
        else:
            low_raster = mid_raster

    return best_raster, best_actual


def prepare_artwork(input_path: str | Path, preset_name: str, size_mm: float | None = None, observer: TraceObserver | None = None, mask_alpha_threshold: int = 8) -> PreparedArtwork:
    from .presets import PRESETS

    preset = PRESETS[preset_name]
    target_final_size_mm = size_mm if size_mm is not None else preset.default_size_mm
    input_path = Path(input_path)
    rgba_image = load_rgba_image(input_path)
    emit_raster_trace(observer, "input", "Raw input image loaded.", rgba_image, (0, 0, px_to_pt(rgba_image.width), px_to_pt(rgba_image.height)), preset_name)
    cropped = crop_to_alpha_bounds(rgba_image)
    emit_raster_trace(observer, "crop", "Image cropped to alpha bounds.", cropped, (0, 0, px_to_pt(cropped.width), px_to_pt(cropped.height)), preset_name)

    initial_raster_long_side_mm = estimate_initial_raster_long_side_mm(
        cropped=cropped,
        preset_name=preset_name,
        target_final_size_mm=target_final_size_mm,
    )

    initial_prepared = prepare_artwork_for_raster_target(
        cropped=cropped,
        preset_name=preset_name,
        target_raster_long_side_mm=initial_raster_long_side_mm,
        observer=None,
        mask_alpha_threshold=mask_alpha_threshold,
    )
    initial_actual_final_size_mm = final_outline_long_side_mm(initial_prepared)
    initial_error_mm = abs(initial_actual_final_size_mm - target_final_size_mm)

    if initial_error_mm <= CALIBRATION_TOLERANCE_MM or initial_actual_final_size_mm < target_final_size_mm * 0.5 or initial_actual_final_size_mm > target_final_size_mm * 2.0:
        emit_trace(observer, TraceFrame(stage="size_calibration", message=f"Initial raster long side {initial_raster_long_side_mm:.2f}mm produced final outline {initial_actual_final_size_mm:.2f}mm (target {target_final_size_mm:.2f}mm).", preset_name=preset_name, status="done"))
        if observer is None:
            return initial_prepared
        return prepare_artwork_for_raster_target(
            cropped=cropped,
            preset_name=preset_name,
            target_raster_long_side_mm=initial_raster_long_side_mm,
            observer=observer,
            mask_alpha_threshold=mask_alpha_threshold,
        )

    del initial_prepared
    raster_long_side_mm, actual_final_size_mm = calibrate_raster_long_side_mm(
        cropped=cropped,
        preset_name=preset_name,
        target_final_size_mm=target_final_size_mm,
        initial_raster_long_side_mm=initial_raster_long_side_mm,
        mask_alpha_threshold=mask_alpha_threshold,
    )

    emit_trace(observer, TraceFrame(stage="size_calibration", message=f"Binary-search calibration selected raster long side {raster_long_side_mm:.2f}mm for final outline {actual_final_size_mm:.2f}mm (target {target_final_size_mm:.2f}mm).", preset_name=preset_name, status="done"))
    prepared = prepare_artwork_for_raster_target(
        cropped=cropped,
        preset_name=preset_name,
        target_raster_long_side_mm=raster_long_side_mm,
        observer=observer,
        mask_alpha_threshold=mask_alpha_threshold,
    )
    final_actual_size_mm = final_outline_long_side_mm(prepared)
    final_error_mm = abs(final_actual_size_mm - target_final_size_mm)
    if final_error_mm > CALIBRATION_TOLERANCE_MM:
        corrected_raster_long_side_mm = max(1.0, raster_long_side_mm * target_final_size_mm / final_actual_size_mm)
        emit_trace(observer, TraceFrame(stage="size_calibration_refine", message=f"Final verification adjusted raster long side {raster_long_side_mm:.2f}mm -> {corrected_raster_long_side_mm:.2f}mm after final outline {final_actual_size_mm:.2f}mm.", preset_name=preset_name, status="running"))
        prepared = prepare_artwork_for_raster_target(
            cropped=cropped,
            preset_name=preset_name,
            target_raster_long_side_mm=corrected_raster_long_side_mm,
            observer=observer,
            mask_alpha_threshold=mask_alpha_threshold,
        )
    return prepared
