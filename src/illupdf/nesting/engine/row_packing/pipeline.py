from __future__ import annotations
from collections import defaultdict
from pathlib import Path

from .inputs import collect_pdf_groups, expand_group_counts
from .models import SourcePdf
from .pdf_parser import parse_source_pdf, oriented_items_for_source
from .packer import PackingOptions, build_sheets
from .renderer import render_sheet_layers


def output_dir_for_group(output_root: Path, input_root: Path, group_dir: Path) -> Path:
    try:
        rel = group_dir.relative_to(input_root)
    except ValueError:
        rel = Path(group_dir.name)
    return output_root / rel


def run_row_packing_nesting(
    inputs: list[Path],
    output: Path,
    *,
    default_count: int = 1,
    recursive: bool = True,
    beam_width: int = 8,
    row_candidate_limit: int = 60,
    pair_precompute: bool = True,
    pair_precompute_workers: int = 1,
    use_hybrid_search: bool = True,
    observer=None,
) -> list[Path]:
    input_dirs = [p for p in inputs if p.is_dir()]
    input_root = input_dirs[0] if len(input_dirs) == 1 else Path.cwd()
    groups = collect_pdf_groups(inputs, recursive=recursive, default_count=default_count)
    outputs: list[Path] = []
    source_cache: dict[Path, SourcePdf] = {}
    for group_dir, pdfs in groups.items():
        expanded = expand_group_counts(group_dir, pdfs, default_count)
        if not expanded:
            continue
        options_by_instance = {}
        per_pdf_counter = defaultdict(int)
        for pdf_path in expanded:
            source = source_cache.get(pdf_path)
            if source is None:
                source = parse_source_pdf(pdf_path)
                source_cache[pdf_path] = source
            per_pdf_counter[pdf_path] += 1
            copy_index = per_pdf_counter[pdf_path]
            iid = f"{pdf_path}#{copy_index}"
            options_by_instance[iid] = oriented_items_for_source(source, iid, copy_index)
        packing_options = PackingOptions(
            beam_width=max(1, beam_width),
            row_candidate_limit=max(1, row_candidate_limit),
            pair_precompute=pair_precompute,
            pair_precompute_workers=max(1, pair_precompute_workers),
            use_hybrid_search=use_hybrid_search,
            preset="auto",
        )
        sheets = build_sheets(options_by_instance, group_dir, packing_options=packing_options)
        out_dir = output_dir_for_group(output, input_root, group_dir)
        for sheet in sheets:
            out_path = out_dir / f"sheet_{sheet.index:03d}.pdf"
            render_sheet_layers(sheet, out_path)
            outputs.append(out_path)
    return outputs
