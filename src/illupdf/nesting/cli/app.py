from __future__ import annotations
import argparse
import json
import sys


def handle_nest(args: argparse.Namespace) -> int:
    from ..engine.row_packing.pipeline import run_row_packing_nesting
    try:
        outputs = run_row_packing_nesting(
            inputs=args.inputs,
            output=args.output,
            default_count=max(1, args.count),
            recursive=not args.no_recursive,
            beam_width=max(1, args.beam_width),
            row_candidate_limit=max(1, args.row_candidate_limit),
            pair_precompute=args.pair_precompute,
            pair_precompute_workers=max(1, args.pair_precompute_workers),
            use_hybrid_search=(args.search_mode == "hybrid"),
        )
        if args.emit == "ndjson":
            print(json.dumps({"type": "nesting", "payload": {"event_type": "result", "outputs": [str(p) for p in outputs], "done": True}}))
        else:
            print(f"[+] Generated {len(outputs)} nested sheet PDF(s) under {args.output} "
                  f"(preset=auto: keyring/stand/mixed selected from item sizes; sheet 400x300mm fixed)")
            for p in outputs:
                print(f"    {p}")
        return 0
    except Exception as exc:
        if args.emit == "ndjson":
            print(json.dumps({"type": "nesting", "payload": {"event_type": "error", "message": str(exc), "done": True}}))
        else:
            print(f" [!] Error: {exc}", file=sys.stderr)
            import traceback
            traceback.print_exc()
        return 1


def handle_visualize_nesting(args: argparse.Namespace) -> int:
    from ..visualizer.app import run_visualizer_adapter
    return run_visualizer_adapter(args)


def handle_command(args: argparse.Namespace) -> int:
    if args.command == "nest":
        return handle_nest(args)
    if args.command == "visualize-nesting":
        return handle_visualize_nesting(args)
    return 1
