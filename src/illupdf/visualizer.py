from __future__ import annotations
import sys
from typing import Any
from .cli import main, build_parser
from .converting.visualizer.server import HTML_PAGE
from .converting.visualizer.state import BALANCE_STAGES

def frame_is_balance_step(frame: Any) -> bool:
    # Works with both TraceFrame objects and dicts from NDJSON
    if hasattr(frame, "stage"):
        return frame.stage in BALANCE_STAGES
    if isinstance(frame, dict):
        return frame.get("stage") in BALANCE_STAGES
    return False


def normalize_command_tokens(cmd: str) -> list[str]:
    tokens = cmd.split()
    if not tokens:
        return []
    if tokens[0] == "python":
        tokens = tokens[1:]
    if tokens and tokens[0] == "-m":
        tokens = tokens[1:]
    if tokens and tokens[0] == "illupdf":
        tokens = tokens[1:]
    return tokens


def parse_visualizer_command(cmd: str) -> Any:
    parser = build_parser()
    tokens = normalize_command_tokens(cmd)
    return parser.parse_args(tokens)


if __name__ == "__main__":
    # The visualizer is now part of the main CLI as 'visualize' command
    # but we maintain this file for backward compatibility.
    sys.exit(main(["visualize"] + sys.argv[1:]))
