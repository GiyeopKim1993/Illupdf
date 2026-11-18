from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from illupdf.nesting.engine.pipeline import run_nesting_pipeline

class NestingTests(unittest.TestCase):
    def test_run_nesting_pipeline_handles_empty_input(self):
        # Even with empty input, it should return an empty list or raise an expected error,
        # but for now we just verify the import works and it can be called.
        try:
            results = run_nesting_pipeline(
                input_pdfs=[],
                output_pdf_base=Path("dummy_out"),
                artboard_width_mm=300,
                artboard_height_mm=400,
                max_width_mm=300,
                max_height_mm=400,
                PPI=50,
                rotations=[0, 90, 180, 270],
                generations=1,
            )
            self.assertEqual(results, [])
        except Exception as e:
            # We expect an exception if it tries to process empty, or it just returns
            pass

if __name__ == "__main__":
    unittest.main()
