from __future__ import annotations
from functools import lru_cache
import cv2
import numpy as np
from ..units import mm_to_px

MORPH_RADIUS_MM = 0.35
WHITE_INSET_MM = 0.15
WHITE_SMOOTH_EXPAND_MM = 0.10


@lru_cache(maxsize=32)
def _ellipse_kernel(radius_px: int) -> np.ndarray:
    """Cached circular structuring element.

    PPI is fixed (1440), so a given mm radius always maps to the same kernel
    size; caching avoids rebuilding the kernel on every mask call.
    """
    kernel_size = radius_px * 2 + 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))


def build_custom_mask(rgba_image: np.ndarray, threshold: int = 8) -> np.ndarray:
    """
    커스텀 파이프라인 방식의 마스크 생성.

    1. 알파 임계값(threshold - 1) 이상 픽셀을 유효 영역으로 추출.
    2. Opening(0.2mm): 표면 돌출 노이즈 및 점 노이즈 제거.
    3. Closing(0.35mm): 작은 구멍 메우기 및 외곽선 부드럽게.

    낮은 임계값(기본 8)으로 반투명 경계 픽셀까지 포함하며,
    Open→Close 순서로 정제하여 외곽선 품질을 높인다.
    """
    alpha = rgba_image[:, :, 3]
    _, binary_mask = cv2.threshold(alpha, threshold - 1, 255, cv2.THRESH_BINARY)

    open_px = max(1, int(mm_to_px(0.2)))
    close_px = max(1, int(mm_to_px(MORPH_RADIUS_MM)))

    kernel_open = _ellipse_kernel(open_px)
    kernel_close = _ellipse_kernel(close_px)

    mask = cv2.morphologyEx(binary_mask, cv2.MORPH_OPEN, kernel_open)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel_close)
    return mask


def build_high_res_binary_mask(rgba_image: np.ndarray, threshold: int = 128) -> np.ndarray:
    """
    Extracts a binary mask by simulating image stacking and high-res thresholding.
    Input rgba_image is a numpy array (H, W, 4).
    """
    alpha = rgba_image[:, :, 3].astype(np.float32) / 255.0
    
    # Simulate stacking 2 images (Alpha Stacking) to thicken edges
    # A_new = 1 - (1 - A) * (1 - A)
    stacked_alpha = 1.0 - (1.0 - alpha) * (1.0 - alpha)
    
    # Convert back to 0-255
    stacked_alpha_8bit = (stacked_alpha * 255.0).astype(np.uint8)
    
    # Threshold at 128 as requested
    _, binary_mask = cv2.threshold(stacked_alpha_8bit, threshold, 255, cv2.THRESH_BINARY)
    
    # 3. Apply initial closing (0.35mm) to fill gaps
    mask = close_mask(binary_mask)
    
    # 4. Fine-tuning with 0.2mm Closing & Opening for jitter removal
    jitter_px = mm_to_px(0.2)
    if jitter_px >= 1:
        kernel_size = jitter_px * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        
    return mask


def close_mask(mask: np.ndarray) -> np.ndarray:
    radius_px = mm_to_px(MORPH_RADIUS_MM)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _ellipse_kernel(radius_px))


def build_white_mask(mask: np.ndarray) -> np.ndarray:
    white_mask = morphology_offset_mask(mask, -(WHITE_INSET_MM + WHITE_SMOOTH_EXPAND_MM))
    if not np.any(white_mask):
        return white_mask
    return morphology_offset_mask(white_mask, WHITE_SMOOTH_EXPAND_MM)


def morphology_offset_mask(mask: np.ndarray, delta_mm: float) -> np.ndarray:
    if delta_mm == 0:
        return mask.copy()
    radius_px = mm_to_px(abs(delta_mm))
    kernel = _ellipse_kernel(radius_px)
    if delta_mm < 0:
        return cv2.erode(mask, kernel)
    return cv2.dilate(mask, kernel)
