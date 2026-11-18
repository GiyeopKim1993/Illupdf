import numpy as np
from dataclasses import dataclass
from .parser import NestingItem

@dataclass
class PlacedItem:
    item: NestingItem
    x_pos: int            # X position on the grid
    y_pos: int            # Y position on the grid
    rotation_deg: int     # 0, 90, 180, 270
    rotated_mask: np.ndarray

class GridMap:
    def __init__(self, width_px: int, height_px: int):
        self.width = width_px
        self.height = height_px
        # False = empty, True = occupied
        self.grid = np.zeros((height_px, width_px), dtype=bool)
        
    def can_place(self, mask: np.ndarray, x: int, y: int) -> bool:
        mask_h, mask_w = mask.shape
        if x < 0 or y < 0 or x + mask_w > self.width or y + mask_h > self.height:
            return False
        grid_region = self.grid[y:y+mask_h, x:x+mask_w]
        if np.any(grid_region & mask):
            return False
        return True

    def find_best_placement(self, mask: np.ndarray, max_w: int = None, max_h: int = None) -> tuple[int, int] | None:
        """
        Top-Left Fill (TLF): Prefers smaller Y (top) and smaller X (left).
        In numpy, y=0 is TOP, so we prefer the SMALLEST valid Y.
        """
        mask_h, mask_w = mask.shape
        search_w = max_w if max_w is not None else self.width
        search_h = max_h if max_h is not None else self.height

        if mask_w > search_w or mask_h > search_h:
            return None
            
        grid_region = self.grid[0:search_h, 0:search_w]
        
        try:
            import cv2
            collision_map = cv2.matchTemplate(grid_region.astype(np.uint8), mask.astype(np.uint8), cv2.TM_CCORR)
        except ImportError:
            from scipy.signal import fftconvolve
            collision_map = fftconvolve(grid_region.astype(float), mask[::-1, ::-1].astype(float), mode='valid')
        
        valid_y, valid_x = np.where(collision_map < 0.5)
        if len(valid_y) == 0: return None
        
        # NumPy default is row-major (Smallest Y first).
        for i in range(len(valid_y)):
            tx, ty = int(valid_x[i]), int(valid_y[i])
            if self.can_place(mask, tx, ty):
                return tx, ty
        return None

    def find_best_placement_top_left(self, mask: np.ndarray) -> tuple[int, int] | None:
        return self.find_best_placement(mask)
        
    def place(self, item: NestingItem, rotated_mask: np.ndarray, x: int, y: int, rotation: int) -> PlacedItem:
        self.grid[y : y + rotated_mask.shape[0], x : x + rotated_mask.shape[1]] |= rotated_mask.astype(bool)
        return PlacedItem(item=item, x_pos=x, y_pos=y, rotation_deg=rotation, rotated_mask=rotated_mask)

    def place_item_obj(self, p: PlacedItem):
        mask = p.rotated_mask
        self.grid[p.y_pos : p.y_pos + mask.shape[0], p.x_pos : p.x_pos + mask.shape[1]] |= mask.astype(bool)

_MASK_CACHE = {}

def get_rotated_mask(item: NestingItem, rotation_deg: int) -> np.ndarray:
    cache_key = (str(item.pdf_path), rotation_deg)
    if cache_key in _MASK_CACHE:
        return _MASK_CACHE[cache_key]
        
    if rotation_deg % 90 == 0:
        k = (rotation_deg % 360) // 90
        res = np.rot90(item.mask, k=k)
    else:
        import scipy.ndimage
        res = scipy.ndimage.rotate(item.mask, rotation_deg, reshape=True, order=0).astype(bool)
    
    _MASK_CACHE[cache_key] = res
    return res
