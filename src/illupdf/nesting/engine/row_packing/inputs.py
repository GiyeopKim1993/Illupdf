from __future__ import annotations
from collections import defaultdict
from pathlib import Path
from .counts import find_counts_file, read_counts, count_for_pdf


def _is_source_pdf(path: Path) -> bool:
    stem = path.stem.lower()
    if path.name.startswith("sheet_"):
        return False
    return not stem.endswith(("_raster", "_white", "_outline"))


def collect_pdf_groups(inputs: list[Path], *, recursive: bool = True, default_count: int = 1) -> dict[Path, list[Path]]:
    pdfs: list[Path] = []
    for inp in inputs:
        if inp.is_dir():
            iterator = inp.rglob("*.pdf") if recursive else inp.glob("*.pdf")
            pdfs.extend(sorted(p for p in iterator if _is_source_pdf(p)))
        elif inp.exists() and inp.suffix.lower() == ".pdf" and _is_source_pdf(inp):
            pdfs.append(inp)
    groups: dict[Path, list[Path]] = defaultdict(list)
    for pdf in pdfs:
        groups[pdf.parent].append(pdf)
    return {k: sorted(v) for k, v in sorted(groups.items(), key=lambda item: str(item[0]))}


def expand_group_counts(group_dir: Path, pdfs: list[Path], default_count: int) -> list[Path]:
    counts_file = find_counts_file(group_dir)
    counts = read_counts(counts_file) if counts_file else {}
    expanded: list[Path] = []
    for pdf in pdfs:
        if pdf.name in {"counts.csv", "counts.json"}:
            continue
        count = count_for_pdf(pdf, group_dir, counts, default_count)
        expanded.extend([pdf] * count)
    return expanded
