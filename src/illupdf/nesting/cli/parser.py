from __future__ import annotations
import argparse
from pathlib import Path


def build_nest_parser(subparsers: argparse._SubParsersAction) -> None:
    nest_parser = subparsers.add_parser(
        "nest",
        help="Nest multiple PDFs onto one or more 400x300mm sheets.",
    )
    nest_parser.add_argument("inputs", type=Path, nargs="+", help="Source PDF files or directories.")
    nest_parser.add_argument("--output", "-o", type=Path, default=Path("nested_output"), help="Output directory")
    nest_parser.add_argument("--count", type=int, default=1, help="Default quantity for PDFs not listed in counts files")
    nest_parser.add_argument("--no-recursive", action="store_true", help="Do not recursively scan input directories")
    nest_parser.add_argument("--emit", choices=("text", "ndjson"), default="text", help="Output format for progress events")
    nest_parser.add_argument("--beam-width", type=int, default=8, help="Beam states to keep while building each row.")
    nest_parser.add_argument("--row-candidate-limit", type=int, default=60, help="Maximum source instances considered while building a row.")
    nest_parser.add_argument("--pair-precompute", action=argparse.BooleanOptionalAction, default=True, help="Precompute pair offsets for row candidate pool.")
    nest_parser.add_argument("--pair-precompute-workers", type=int, default=1, help="Worker process count for pair-offset precompute.")
    nest_parser.add_argument("--search-mode", choices=("hybrid", "linear"), default="hybrid", help="Pair-offset search mode.")
    # Legacy GA-engine options accepted but ignored by the row-packing engine.
    nest_parser.add_argument("--PPI", type=int, default=50, help=argparse.SUPPRESS)
    nest_parser.add_argument("--generations", type=int, default=60, help=argparse.SUPPRESS)
    nest_parser.add_argument("--mode", choices=["fast", "balanced", "best"], default="balanced", help=argparse.SUPPRESS)

    visualize_parser = subparsers.add_parser(
        "visualize-nesting",
        help="Run the visualizer to show real-time nesting processing.",
    )
    visualize_parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read NDJSON events from stdin instead of running a command.",
    )
    visualize_parser.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        help="Command to run and visualize.",
    )
