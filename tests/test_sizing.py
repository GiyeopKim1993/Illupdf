from __future__ import annotations
import sys
import unittest
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from illupdf.core import prepare_artwork, pt_to_mm, path_bounds

class TestSizing(unittest.TestCase):
    def setUp(self):
        self.img_path = Path("test_sizing_sample.png")
        # Create a tall sample: 100x200 px with 10px padding
        image = Image.new("RGBA", (100, 200), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 10, 90, 190), fill=(255, 0, 0, 255))
        image.save(self.img_path)

    def tearDown(self):
        if self.img_path.exists():
            self.img_path.unlink()

    def test_keyring_final_outline_long_side_matches_target(self):
        target_size_mm = 50.0
        prepared = prepare_artwork(self.img_path, "keyring", size_mm=target_size_mm)
        
        bounds = path_bounds(prepared.outline_outer_paths_pt)
        width_mm = pt_to_mm(bounds[2] - bounds[0])
        height_mm = pt_to_mm(bounds[3] - bounds[1])
        long_side_mm = max(width_mm, height_mm)
        
        # 5% tolerance for discrete rasterization and boss protrusion approximation
        self.assertAlmostEqual(long_side_mm, target_size_mm, delta=target_size_mm * 0.05)

    def test_stand_final_outline_long_side_matches_target(self):
        target_size_mm = 100.0
        prepared = prepare_artwork(self.img_path, "stand", size_mm=target_size_mm)
        
        bounds = path_bounds(prepared.outline_outer_paths_pt)
        long_side_mm = max(pt_to_mm(bounds[2] - bounds[0]), pt_to_mm(bounds[3] - bounds[1]))
        
        # Stand is simpler, 2% tolerance
        self.assertAlmostEqual(long_side_mm, target_size_mm, delta=target_size_mm * 0.02)

if __name__ == "__main__":
    unittest.main()
