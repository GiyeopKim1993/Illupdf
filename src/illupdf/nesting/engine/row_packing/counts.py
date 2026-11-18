from __future__ import annotations
import csv
import json
from pathlib import Path

SCHEMA = "illupdf-counts-v1"
COUNT_FILENAMES = ("counts.json", "counts.csv")


def find_counts_file(directory: Path) -> Path | None:
    found = [directory / name for name in COUNT_FILENAMES if (directory / name).exists()]
    if len(found) > 1:
        raise ValueError(f"multiple count files found in {directory}: {', '.join(p.name for p in found)}")
    return found[0] if found else None


def read_counts(path: Path) -> dict[str, int]:
    if path.suffix.lower() == ".json":
        return read_json_counts(path)
    if path.suffix.lower() == ".csv":
        return read_csv_counts(path)
    raise ValueError(f"unsupported counts file: {path}")


def read_json_counts(path: Path) -> dict[str, int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError(f"invalid counts json schema in {path}; expected schema={SCHEMA!r}")
    items = data.get("items", [])
    result: dict[str, int] = {}
    for item in items:
        key = item.get("path") or item.get("filename")
        count = int(item.get("count", 0))
        if key and count > 0:
            result[str(key)] = count
    return result


def read_csv_counts(path: Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    if not any(line.strip() == f"# {SCHEMA}" for line in lines[:5]):
        raise ValueError(f"invalid counts csv schema in {path}; first lines must include '# {SCHEMA}'")
    data_lines = [line for line in lines if not line.lstrip().startswith("#") and line.strip()]
    reader = csv.DictReader(data_lines)
    result: dict[str, int] = {}
    for row in reader:
        key = row.get("path") or row.get("filename")
        count_text = row.get("count")
        if key and count_text:
            count = int(count_text)
            if count > 0:
                result[key] = count
    return result


def count_for_pdf(pdf: Path, group_dir: Path, counts: dict[str, int], default_count: int) -> int:
    rel = pdf.relative_to(group_dir).as_posix()
    candidates = [rel, pdf.name, pdf.stem]
    matched = [counts[c] for c in candidates if c in counts]
    if matched:
        return matched[0]
    return default_count
