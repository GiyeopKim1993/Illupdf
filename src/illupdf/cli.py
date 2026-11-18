from __future__ import annotations
import sys
import argparse
from .converting.cli.parser import build_convert_parser
from .nesting.cli.parser import build_nest_parser
from .converting.engine.errors import IlluPdfError

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="illupdf",
        description="Convert and Nest transparent artwork for printing.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    build_convert_parser(subparsers)
    build_nest_parser(subparsers)
    return parser

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    
    if args.command in ["convert", "visualize-converting"]:
        from .converting.cli.app import handle_command
        handler = handle_command
    elif args.command in ["nest", "visualize-nesting"]:
        from .nesting.cli.app import handle_command
        handler = handle_command
    else:
        parser.print_help()
        return 1

    try:
        return handler(args)
    except IlluPdfError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        print(f"error: unexpected failure: {exc}", file=sys.stderr)
        return 3

if __name__ == "__main__":
    sys.exit(main())
