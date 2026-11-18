import fitz
import numpy as np
from pathlib import Path
from dataclasses import dataclass

@dataclass
class NestingItem:
    pdf_path: Path
    mask: np.ndarray          # 2D boolean array (True where occupied)
    width_pt: float           # Original PDF page width in pt
    height_pt: float          # Original PDF page height in pt
    PPI: int                  # Resolution used for masking
    
    # Bounding box of the actual content inside the PDF
    # This allows us to ignore empty margins when packing
    mask_offset_x: int        # Left offset in pixels
    mask_offset_y: int        # Top offset in pixels
    
    def get_mask_dimensions(self) -> tuple[int, int]:
        return self.mask.shape[1], self.mask.shape[0]

def parse_pdf_to_item(pdf_path: Path, PPI: int = 50) -> NestingItem:
    """
    Renders the first page of the PDF to extract a true-shape binary mask.
    PPI=50 means roughly 2 pixels per mm.
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    
    # Get original dimensions in pt
    rect = page.rect
    width_pt = rect.width
    height_pt = rect.height
    
    # Render page to pixmap with alpha channel (pymupdf uses 'dpi' as the API parameter)
    pix = page.get_pixmap(alpha=True, dpi=PPI)
    
    # Convert to numpy array
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    
    # Extract alpha channel (last channel)
    alpha = img[:, :, -1]
    
    # Create boolean mask (True where alpha > 0)
    full_mask = alpha > 0
    
    # Find bounding box of the true shape to crop the mask and save space
    y_indices, x_indices = np.where(full_mask)
    if len(y_indices) == 0:
        # Empty page
        return NestingItem(
            pdf_path=pdf_path,
            mask=np.zeros((1, 1), dtype=bool),
            width_pt=width_pt,
            height_pt=height_pt,
            PPI=PPI,
            mask_offset_x=0,
            mask_offset_y=0
        )
        
    min_x, max_x = x_indices.min(), x_indices.max()
    min_y, max_y = y_indices.min(), y_indices.max()
    
    # Crop the mask
    cropped_mask = full_mask[min_y:max_y+1, min_x:max_x+1]
    
    import scipy.ndimage
    margin_px = 2 # roughly 1mm gap on each side at 50 PPI
    padded_mask = np.pad(cropped_mask, margin_px, mode='constant', constant_values=False)
    dilated_mask = scipy.ndimage.binary_dilation(padded_mask, iterations=margin_px)
    
    return NestingItem(
        pdf_path=pdf_path,
        mask=dilated_mask,
        width_pt=width_pt,
        height_pt=height_pt,
        PPI=PPI,
        mask_offset_x=int(min_x) - margin_px,
        mask_offset_y=int(min_y) - margin_px
    )
