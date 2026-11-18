from __future__ import annotations
import argparse
from pathlib import Path


def build_convert_parser(subparsers: argparse._SubParsersAction) -> None:
    convert_parser = subparsers.add_parser(
        "convert",
        help="Convert a transparent PNG/WebP image into combined and split PDFs.",
    )
    convert_parser.add_argument("inputs", type=Path, nargs="+", help="Source PNG or WebP images.")
    convert_parser.add_argument(
        "--preset",
        required=True,
        choices=("keyring", "stand"),
        help="Output sizing preset.",
    )
    convert_parser.add_argument(
        "--output",
        type=Path,
        help="Optional combined PDF output path. Defaults to <input>_<preset>.pdf",
    )
    convert_parser.add_argument(
        "--size",
        type=float,
        help="Target size in mm (long side of the final outline). Defaults to preset's default (keyring=65, stand=148).",
    )
    convert_parser.add_argument(
        "--emit",
        choices=("text", "ndjson"),
        default="text",
        help="Output format for progress events.",
    )
    convert_parser.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Number of worker processes for parallel conversion. 0 = auto "
        "(min(cpu_count, input count)).",
    )

    visualize_parser = subparsers.add_parser(
        "visualize-converting",
        help="Run the visualizer to show real-time processing.",
    )
    visualize_parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read NDJSON events from stdin instead of running a command.",
    )
    visualize_parser.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        help="Command to run and visualize (e.g., 'convert art.png --preset keyring').",
    )
