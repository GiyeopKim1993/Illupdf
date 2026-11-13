from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from shutil import rmtree
from unittest import mock
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
TMP_ROOT = ROOT / ".tmp_tests"
TMP_ROOT.mkdir(exist_ok=True)

import cv2
import pikepdf
import numpy as np
from PIL import Image, ImageDraw

from illupdf.cli import build_parser
from illupdf.core import (
    PPI,
    ConversionOutputs,
    InputImageError,
    PdfGenerationError,
    WHITE_VECTOR_SIMPLIFY_EPSILON_MM,
    approximate_circle_path,
    build_keyring_balance,
    build_boss_gap_bridge_shapes,
    cleanup_tiny_protrusions,
    compute_target_size_pixels,
    convert_image_to_pdf,
    horizontal_line_intersections,
    keep_largest_path_only,
    mask_to_union_paths_pt,
    mm_to_pt,
    path_bounds,
    polygon_area_and_centroid,
    px_to_pt,
    prepare_artwork,
    resolve_keyring_hole_center,
    vertical_line_intersections,
)
from illupdf.visualizer import HTML_PAGE, frame_is_balance_step, normalize_command_tokens, parse_visualizer_command


class IlluPdfTests(unittest.TestCase):
    def test_mm_and_px_conversions_are_consistent(self) -> None:
        self.assertAlmostEqual(mm_to_pt(25.4), 72.0, places=4)
        self.assertAlmostEqual(px_to_pt(PPI), 72.0, places=4)

    def test_keyring_preset_scales_wide_artwork_by_width(self) -> None:
        width_px, height_px = compute_target_size_pixels(200, 100, "keyring")
        self.assertAlmostEqual(width_px / PPI * 25.4, 65.0, places=1)
        self.assertLess(height_px / PPI * 25.4, 65.0)

    def test_keyring_preset_scales_tall_artwork_by_height(self) -> None:
        width_px, height_px = compute_target_size_pixels(100, 200, "keyring")
        self.assertAlmostEqual(height_px / PPI * 25.4, 65.0, places=1)
        self.assertLess(width_px / PPI * 25.4, 65.0)

    def test_raster_size_limit_applies_before_vector_offsets(self) -> None:
        with workspace_case_dir() as tmp_dir:
            image_path = tmp_dir / "wide.png"
            make_sample_artwork(image_path, size=(260, 140), rounded=True)
            prepared = prepare_artwork(image_path, "keyring")
            raster_width_mm = prepared.raster_size_pt[0] / 72.0 * 25.4
            raster_height_mm = prepared.raster_size_pt[1] / 72.0 * 25.4
            page_width_mm = prepared.page_size_pt[0] / 72.0 * 25.4
            self.assertAlmostEqual(raster_width_mm, 61.0, places=1)
            self.assertLess(raster_height_mm, 65.0)
            self.assertGreaterEqual(page_width_mm, raster_width_mm)

    def test_stand_preset_sets_long_side(self) -> None:
        width_px, height_px = compute_target_size_pixels(100, 250, "stand")
        self.assertAlmostEqual(max(width_px, height_px) / PPI * 25.4, 148.0, places=1)

    def test_rgb_input_is_rejected(self) -> None:
        with workspace_case_dir() as tmp_dir:
            image_path = tmp_dir / "opaque.png"
            Image.new("RGB", (100, 100), (255, 255, 255)).save(image_path)
            with self.assertRaises(InputImageError):
                prepare_artwork(image_path, "keyring")




    def test_small_valid_island_is_attached_into_base_outline(self) -> None:
        with workspace_case_dir() as tmp_dir:
            image_path = tmp_dir / "island.png"
            image = Image.new("RGBA", (280, 180), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((30, 40, 190, 150), radius=28, fill=(0, 0, 0, 255))
            draw.ellipse((205, 72, 235, 102), fill=(0, 0, 0, 255))
            image.save(image_path)

            prepared = prepare_artwork(image_path, "stand")
            outline_bounds = path_bounds(prepared.outline_outer_paths_pt)
            base_bounds = path_bounds(prepared.base_paths_pt)

            self.assertEqual(len(prepared.base_paths_pt), 1)
            self.assertGreater(base_bounds[2], base_bounds[0])
            self.assertGreater(outline_bounds[2], base_bounds[2] - 0.1)

    def test_mask_union_builds_pixel_square_geometry(self) -> None:
        mask = np.array(
            [
                [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0],
                [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0],
                [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0],
                [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0],
                [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0],
                [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0],
                [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0],
                [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0],
                [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0],
                [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0],
                [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0],
                [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0],
                [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0],
                [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0],
                [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            ],
            dtype=np.uint8,
        ) * 255
        paths = mask_to_union_paths_pt(mask)
        self.assertEqual(len(paths), 1)
        bounds = path_bounds(paths)
        self.assertAlmostEqual(bounds[2] - bounds[0], px_to_pt(13), places=4)
        self.assertAlmostEqual(bounds[3] - bounds[1], px_to_pt(13), places=4)

    def test_mask_union_discards_tiny_islands_and_keeps_main_shape(self) -> None:
        mask = np.zeros((32, 32), dtype=np.uint8)
        mask[4:28, 4:28] = 255
        mask[1, 1] = 255
        paths = mask_to_union_paths_pt(mask)
        self.assertEqual(len(paths), 1)

    def test_mask_union_preserves_holes_for_compound_white_paths(self) -> None:
        mask = np.zeros((80, 80), dtype=np.uint8)
        mask[8:72, 8:72] = 255
        mask[28:52, 28:52] = 0
        paths = mask_to_union_paths_pt(mask)
        self.assertGreaterEqual(len(paths), 2)

    def test_white_mask_vectorization_keeps_rounder_detail_than_base_outline(self) -> None:
        mask = np.zeros((120, 120), dtype=np.uint8)
        cv2_circle = np.zeros((120, 120), dtype=np.uint8)
        yy, xx = np.ogrid[:120, :120]
        cv2_circle[((xx - 60) ** 2 + (yy - 60) ** 2) <= 40 ** 2] = 255
        coarse_paths = mask_to_union_paths_pt(cv2_circle, simplify_epsilon_mm=0.08)
        fine_paths = mask_to_union_paths_pt(cv2_circle, simplify_epsilon_mm=WHITE_VECTOR_SIMPLIFY_EPSILON_MM)
        self.assertGreaterEqual(len(fine_paths[0]), len(coarse_paths[0]))

    def test_outline_expands_and_white_insets_relative_to_white_source(self) -> None:
        with workspace_case_dir() as tmp_dir:
            image_path = tmp_dir / "badge.png"
            make_sample_artwork(image_path, size=(220, 160), rounded=True)
            prepared = prepare_artwork(image_path, "stand")

            base_bounds = path_bounds(prepared.base_paths_pt)
            outline_bounds = path_bounds(prepared.outline_paths_pt)

            self.assertGreater(outline_bounds[2] - outline_bounds[0], base_bounds[2] - base_bounds[0])
            self.assertGreater(outline_bounds[3] - outline_bounds[1], base_bounds[3] - base_bounds[1])


    def test_tiny_protrusion_cleanup_opens_and_intersects_outline(self) -> None:
        outline_with_tiny_spike = [
            (0.0, 0.0),
            (100.0, 0.0),
            (100.0, 50.0),
            (46.0, 50.0),
            (46.0, 51.0),
            (45.9, 51.0),
            (45.9, 50.0),
            (0.0, 50.0),
        ]

        cleaned = cleanup_tiny_protrusions([outline_with_tiny_spike], opening_gap_mm=0.2)

        self.assertEqual(len(cleaned), 1)
        bounds = path_bounds(cleaned)
        self.assertLessEqual(bounds[3], 50.02)
        self.assertGreater(bounds[2] - bounds[0], 99.0)

    def test_keep_largest_path_only_discards_smaller_loops(self) -> None:
        large_path = [(0.0, 0.0), (12.0, 0.0), (12.0, 12.0), (0.0, 12.0)]
        small_path = [(4.0, 4.0), (5.0, 4.0), (5.0, 5.0), (4.0, 5.0)]

        filtered = keep_largest_path_only([small_path, large_path])

        self.assertEqual(filtered, [large_path])

    def test_prepare_artwork_keeps_only_largest_outline_path_after_each_offset(self) -> None:
        with workspace_case_dir() as tmp_dir:
            image_path = tmp_dir / "badge.png"
            make_sample_artwork(image_path, size=(220, 160), rounded=True)

            expanded_large = [(0.0, 0.0), (12.0, 0.0), (12.0, 12.0), (0.0, 12.0)]
            expanded_small = [(4.0, 4.0), (5.0, 4.0), (5.0, 5.0), (4.0, 5.0)]
            shrunk_large = [(0.5, 0.5), (11.5, 0.5), (11.5, 11.5), (0.5, 11.5)]
            shrunk_small = [(6.0, 6.0), (6.5, 6.0), (6.5, 6.5), (6.0, 6.5)]

            with mock.patch(
                "illupdf.converting.engine.pipeline.offset_paths",
                side_effect=[
                    [shrunk_small, shrunk_large],
                    [expanded_small, expanded_large],
                    [expanded_small, expanded_large],
                    [shrunk_small, shrunk_large],
                    [expanded_small, expanded_large],
                    [shrunk_small, shrunk_large],
                ],
            ) as mocked_offset_paths:
                prepared = prepare_artwork(image_path, "stand")

            self.assertEqual(mocked_offset_paths.call_count, 6)
            self.assertEqual(prepared.outline_outer_paths_pt, [shrunk_large])

    def test_polygon_area_and_centroid_match_shoelace_expectation(self) -> None:
        area, centroid = polygon_area_and_centroid(
            [(0.0, 0.0), (12.0, 0.0), (12.0, 8.0), (0.0, 8.0)]
        )

        self.assertAlmostEqual(area, 96.0, places=4)
        self.assertAlmostEqual(centroid[0], 6.0, places=4)
        self.assertAlmostEqual(centroid[1], 4.0, places=4)

    def test_vertical_line_intersections_return_expected_crossings(self) -> None:
        intersections = vertical_line_intersections(
            [(0.0, 0.0), (10.0, 0.0), (10.0, 12.0), (0.0, 12.0)],
            5.0,
        )

        self.assertEqual(intersections, [0.0, 12.0])

    def test_horizontal_line_intersections_return_expected_crossings(self) -> None:
        intersections = horizontal_line_intersections(
            [(0.0, 0.0), (10.0, 0.0), (10.0, 12.0), (0.0, 12.0)],
            5.0,
        )

        self.assertEqual(intersections, [0.0, 10.0])

    def test_keyring_hole_center_slides_up_when_side_clearance_is_tight(self) -> None:
        scale = 3.0
        base_path = [
            (0.0 * scale, 0.0 * scale),
            (0.0 * scale, 10.0 * scale),
            (48.0 * scale, 10.0 * scale),
            (44.0 * scale, 18.0 * scale),
            (40.0 * scale, 18.0 * scale),
            (40.0 * scale, 10.0 * scale),
            (60.0 * scale, 10.0 * scale),
            (60.0 * scale, 18.0 * scale),
            (56.0 * scale, 18.0 * scale),
            (52.0 * scale, 10.0 * scale),
            (100.0 * scale, 10.0 * scale),
            (100.0 * scale, 0.0 * scale),
        ]

        hole_center = resolve_keyring_hole_center(
            base_path,
            rx_pt=50.0 * scale,
            hole_radius_pt=mm_to_pt(1.5),
            boss_radius_pt=mm_to_pt(3.5),
        )

        self.assertGreater(hole_center[1], 10.0 * scale + mm_to_pt(1.5))

    def test_keyring_hole_center_stays_at_default_height_when_sides_are_clear(self) -> None:
        base_path = [(0.0, 0.0), (100.0, 0.0), (100.0, 10.0), (0.0, 10.0)]

        hole_center = resolve_keyring_hole_center(
            base_path,
            rx_pt=50.0,
            hole_radius_pt=mm_to_pt(1.5),
            boss_radius_pt=mm_to_pt(3.5),
        )

        self.assertAlmostEqual(hole_center[1], 10.0 + mm_to_pt(1.5), places=4)

    def test_keyring_balance_reconnects_slid_boss_with_reinforced_neck(self) -> None:
        scale = 3.0
        base_path = [
            (0.0 * scale, 0.0 * scale),
            (0.0 * scale, 10.0 * scale),
            (48.0 * scale, 10.0 * scale),
            (44.0 * scale, 18.0 * scale),
            (40.0 * scale, 18.0 * scale),
            (40.0 * scale, 10.0 * scale),
            (60.0 * scale, 10.0 * scale),
            (60.0 * scale, 18.0 * scale),
            (56.0 * scale, 18.0 * scale),
            (52.0 * scale, 10.0 * scale),
            (100.0 * scale, 10.0 * scale),
            (100.0 * scale, 0.0 * scale),
        ]

        balance = build_keyring_balance(base_path)
        base_area, _ = polygon_area_and_centroid(base_path)
        outer_area, _ = polygon_area_and_centroid(balance.outer_path_pt)

        self.assertGreater(balance.hole_center_pt[1], 10.0 * scale + mm_to_pt(1.5))
        self.assertGreater(outer_area, base_area)

    def test_boss_gap_bridge_shapes_are_added_for_nearby_side_gap(self) -> None:
        base_path = [
            (0.0, 0.0),
            (0.0, 80.0),
            (26.0, 80.0),
            (26.0, 60.0),
            (34.0, 60.0),
            (34.0, 80.0),
            (100.0, 80.0),
            (100.0, 0.0),
        ]
        hole_center = (30.0, 90.0)
        boss_radius = 12.0
        boss_path = approximate_circle_path(hole_center, boss_radius)

        bridge_shapes = build_boss_gap_bridge_shapes(base_path, boss_path, hole_center)

        self.assertTrue(bridge_shapes)

    def test_boss_gap_bridge_shapes_ignore_tiny_local_contact(self) -> None:
        base_path = [
            (0.0, 0.0),
            (0.0, 140.0),
            (36.8, 139.4),
            (32.0, 120.0),
            (0.0, 120.0),
        ]
        hole_center = (47.45, 139.75)
        boss_path = approximate_circle_path(hole_center, mm_to_pt(3.5))

        bridge_shapes = build_boss_gap_bridge_shapes(base_path, boss_path, hole_center)

        self.assertEqual(bridge_shapes, [])

    def test_keyring_balance_centroid_matches_outer_minus_hole_formula(self) -> None:
        base_path = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]

        balance = build_keyring_balance(base_path)
        outer_area, outer_centroid = polygon_area_and_centroid(balance.outer_path_pt)
        hole_radius = mm_to_pt(1.5)
        hole_area = np.pi * hole_radius * hole_radius
        final_area = outer_area - hole_area
        expected_centroid_x = (
            outer_area * outer_centroid[0] - hole_area * balance.hole_center_pt[0]
        ) / final_area
        expected_centroid_y = (
            outer_area * outer_centroid[1] - hole_area * balance.hole_center_pt[1]
        ) / final_area

        self.assertAlmostEqual(balance.centroid_pt[0], expected_centroid_x, places=4)
        self.assertAlmostEqual(balance.centroid_pt[1], expected_centroid_y, places=4)

    def test_keyring_prepare_artwork_adds_inner_hole_and_balance_result(self) -> None:
        with workspace_case_dir() as tmp_dir:
            image_path = tmp_dir / "badge.png"
            make_sample_artwork(image_path, size=(220, 160), rounded=True)

            prepared = prepare_artwork(image_path, "keyring")

            self.assertEqual(len(prepared.outline_outer_paths_pt), 1)
            self.assertEqual(len(prepared.outline_inner_paths_pt), 1)
            self.assertIsNotNone(prepared.keyring_balance)
            self.assertLessEqual(abs(prepared.keyring_balance.theta_rad), 0.05)

    def test_prepare_artwork_crops_transparent_padding_before_resizing(self) -> None:
        with workspace_case_dir() as tmp_dir:
            tight_path = tmp_dir / "tight.png"
            padded_path = tmp_dir / "padded.png"
            tight_image = Image.new("RGBA", (160, 120), (0, 0, 0, 0))
            tight_draw = ImageDraw.Draw(tight_image)
            tight_draw.rounded_rectangle((20, 20, 140, 100), radius=24, fill=(40, 90, 220, 255))
            tight_image.save(tight_path)

            padded_image = Image.new("RGBA", (360, 320), (0, 0, 0, 0))
            padded_image.paste(tight_image, (100, 100))
            padded_image.save(padded_path)

            tight_prepared = prepare_artwork(tight_path, "keyring")
            padded_prepared = prepare_artwork(padded_path, "keyring")

            self.assertEqual(tight_prepared.raster_size_pt, padded_prepared.raster_size_pt)

    def test_prepare_artwork_observer_emits_keyring_balance_frames(self) -> None:
        with workspace_case_dir() as tmp_dir:
            image_path = tmp_dir / "badge.png"
            make_sample_artwork(image_path, size=(220, 160), rounded=True)
            frames = []

            prepare_artwork(image_path, "keyring", observer=frames.append)

            stages = [frame.stage for frame in frames]
            self.assertIn("base_outline", stages)
            self.assertIn("outline_expand", stages)
            self.assertIn("outline_shrink", stages)
            self.assertIn("keyring_balance", stages)
            self.assertIn("keyring_result", stages)
            self.assertTrue(any(frame.vertical_line_pt is not None for frame in frames if frame.stage == "keyring_balance"))
            self.assertTrue(any(frame.centroid_line_pt is not None for frame in frames if frame.stage == "keyring_balance"))

    def test_visualizer_command_normalization_supports_expected_wrappers(self) -> None:
        self.assertEqual(
            normalize_command_tokens("illupdf convert .\\art.png --preset keyring"),
            ["convert", ".\\art.png", "--preset", "keyring"],
        )
        self.assertEqual(
            normalize_command_tokens("python -m illupdf convert .\\art.png --preset stand"),
            ["convert", ".\\art.png", "--preset", "stand"],
        )
        self.assertEqual(
            normalize_command_tokens("convert .\\art.png --preset keyring"),
            ["convert", ".\\art.png", "--preset", "keyring"],
        )

    def test_visualizer_command_parser_reuses_convert_cli(self) -> None:
        args = parse_visualizer_command("illupdf convert .\\art.png --preset keyring")

        self.assertEqual(args.command, "convert")
        self.assertEqual(args.preset, "keyring")

    def test_visualizer_marks_keyring_iteration_frames_as_balance_steps(self) -> None:
        with workspace_case_dir() as tmp_dir:
            image_path = tmp_dir / "badge.png"
            make_sample_artwork(image_path, size=(220, 160), rounded=True)
            frames = []

            prepare_artwork(image_path, "keyring", observer=frames.append)

            balance_frames = [frame for frame in frames if frame.stage in {"keyring_balance", "keyring_result"}]
            non_balance_frames = [frame for frame in frames if frame.stage not in {"keyring_balance", "keyring_result"}]

            self.assertTrue(balance_frames)
            self.assertTrue(all(frame_is_balance_step(frame) for frame in balance_frames))
            self.assertTrue(all(not frame_is_balance_step(frame) for frame in non_balance_frames))

    def test_visualizer_html_includes_balance_step_navigation_controls(self) -> None:
        self.assertIn('id="balance-steps"', HTML_PAGE)
        self.assertIn('id="first"', HTML_PAGE)
        self.assertIn('id="last"', HTML_PAGE)
        self.assertIn("Arrow keys move step-by-step.", HTML_PAGE)

    def test_thin_artwork_can_fail_white_inset(self) -> None:
        with workspace_case_dir() as tmp_dir:
            image_path = tmp_dir / "thin.png"
            image = Image.new("RGBA", (3000, 40), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.rectangle((10, 19, 2990, 20), fill=(20, 20, 20, 255))
            image.save(image_path)
            with self.assertRaises(PdfGenerationError):
                prepare_artwork(image_path, "keyring")

    def test_conversion_creates_combined_and_split_outputs(self) -> None:
        with workspace_case_dir() as tmp_dir:
            image_path = tmp_dir / "badge.png"
            combined_path = tmp_dir / "badge_keyring.pdf"
            make_sample_artwork(image_path, size=(180, 180), rounded=True)

            outputs = convert_image_to_pdf(image_path, combined_path, "keyring")

            self.assertIsInstance(outputs, ConversionOutputs)
            for path in outputs.ordered_paths():
                self.assertTrue(path.exists(), path)

    def test_stand_outline_pdf_stays_single_outer_stroke(self) -> None:
        with workspace_case_dir() as tmp_dir:
            image_path = tmp_dir / "stand.png"
            combined_path = tmp_dir / "stand.pdf"
            make_sample_artwork(image_path, size=(180, 240), rounded=True)

            outputs = convert_image_to_pdf(image_path, combined_path, "stand")

            with pikepdf.Pdf.open(outputs.outline_pdf) as outline_pdf:
                outline_content = bytes(outline_pdf.pages[0].Contents.read_bytes()).decode("ascii")
                self.assertEqual(sum(1 for line in outline_content.splitlines() if line.strip() == "S"), 1)

def extract_do_sequence(content: str) -> list[str]:
    sequence: list[str] = []
    for line in content.splitlines():
        line = line.strip()
        if line.endswith(" Do"):
            sequence.append(line.split()[0])
    return sequence


def make_sample_artwork(path: Path, size: tuple[int, int], rounded: bool = False) -> None:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    inset = max(14, min(size) // 7)
    bbox = (inset, inset, size[0] - inset, size[1] - inset)
    if rounded:
        draw.rounded_rectangle(bbox, radius=max(16, inset), fill=(40, 90, 220, 255))
    else:
        draw.ellipse(bbox, fill=(40, 90, 220, 255))
    image.save(path)


@contextmanager
def workspace_case_dir() -> Path:
    case_dir = TMP_ROOT / f"case_{uuid4().hex}"
    case_dir.mkdir(parents=True, exist_ok=False)
    try:
        yield case_dir
    finally:
        if case_dir.exists():
            rmtree(case_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

