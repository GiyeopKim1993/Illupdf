from __future__ import annotations
import argparse
import concurrent.futures
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .presenters import print_outputs
from ..app.convert_usecase import convert_image_to_pdf_usecase
from ..app.emitters import NdjsonEmitter, TraceToEventEmitter
from ..app.events import PipelineEvent

MAX_WORKERS_CAP = 32


@dataclass(frozen=True)
class ConvertJob:
    """Multiprocessing instruction set: one self-contained convert task.

    Only picklable primitives — this is the exact unit sent to worker
    processes via the ProcessPoolExecutor.
    """

    job_id: int
    input_path: str
    output_path: str | None
    preset_name: str
    size_mm: float | None


def _collect_image_inputs(raw_inputs: list[Path]) -> list[Path]:
    inputs: list[Path] = []
    for path in raw_inputs:
        if path.is_dir():
            inputs.extend(sorted(list(path.glob("*.png")) + list(path.glob("*.webp"))))
        elif path.exists():
            inputs.append(path)
        else:
            print(f" [!] Warning: Path does not exist: {path}", file=sys.stderr)
    return inputs


def _resolve_output_path(input_path: Path, output_base: Path | None, preset: str, multi: bool) -> Path | None:
    if output_base is None:
        return None
    if output_base.is_dir():
        return output_base / f"{input_path.stem}_{preset}.pdf"
    if not multi:
        return output_base
    return None


def _worker_init() -> None:
    """Runs once per worker process (ProcessPoolExecutor initializer).

    Pre-imports the heavy native modules so the first job in each worker does
    not pay the one-time import cost.
    """
    import numpy  # noqa: F401
    import cv2  # noqa: F401
    import pikepdf  # noqa: F401


def _resolve_workers(requested: int, n_inputs: int) -> int:
    """Decide the worker process count.

    ``requested <= 0`` means auto: min(CPU count, input count) capped at
    MAX_WORKERS_CAP. An explicit ``--workers`` value is clamped to
    [1, n_inputs].
    """
    if requested > 0:
        return max(1, min(requested, n_inputs))
    cpu = os.cpu_count() or 1
    return max(1, min(cpu, n_inputs, MAX_WORKERS_CAP))


def _run_convert_job(job: ConvertJob) -> tuple[int, str, bool, Any]:
    """Execute one ConvertJob in a worker process.

    Returns (job_id, input_name, success, result) so the caller can restore
    input ordering.
    """
    try:
        outputs = convert_image_to_pdf_usecase(
            input_path=Path(job.input_path),
            output_path=Path(job.output_path) if job.output_path else None,
            preset_name=job.preset_name,
            size_mm=job.size_mm,
            observer=None,
            event_emitter=None,
        )
        return job.job_id, Path(job.input_path).name, True, outputs
    except Exception as exc:
        return job.job_id, Path(job.input_path).name, False, str(exc)


def _submit_jobs(
    executor: concurrent.futures.Executor,
    inputs: list[Path],
    output_base: Path | None,
    preset: str,
    size_mm: float | None,
) -> dict[int, concurrent.futures.Future]:
    """Submit one ConvertJob per input in input order.

    The mapping job_id -> future lets us collect results and emit them in the
    original input order regardless of completion order.
    """
    multi = len(inputs) > 1
    future_by_id: dict[int, concurrent.futures.Future] = {}
    for job_id, input_path in enumerate(inputs):
        target_output = _resolve_output_path(input_path, output_base, preset, multi)
        job = ConvertJob(
            job_id=job_id,
            input_path=str(input_path),
            output_path=str(target_output) if target_output else None,
            preset_name=preset,
            size_mm=size_mm,
        )
        future_by_id[executor.submit(_run_convert_job, job)] = job_id
    return future_by_id


def _collect_ordered(
    future_by_id: dict[int, concurrent.futures.Future],
    n_inputs: int,
) -> dict[int, tuple[str, bool, Any]]:
    """Wait for all jobs and collect results keyed by job_id (input order)."""
    results: dict[int, tuple[str, bool, Any]] = {}
    for future, job_id in future_by_id.items():
        _job_id, name, success, data = future.result()
        results[job_id] = (name, success, data)
    return results


def _handle_convert_ndjson(args: argparse.Namespace, inputs: list[Path], output_base: Path | None) -> int:
    emitter = NdjsonEmitter()
    workers = _resolve_workers(getattr(args, "workers", 0), len(inputs))

    # Single worker: keep the original serial path with full per-frame trace events.
    if workers == 1:
        ok = True
        multi = len(inputs) > 1
        for input_path in inputs:
            target_output = _resolve_output_path(input_path, output_base, args.preset, multi)
            try:
                convert_image_to_pdf_usecase(
                    input_path=input_path,
                    output_path=target_output,
                    preset_name=args.preset,
                    size_mm=args.size,
                    observer=TraceToEventEmitter(emitter),
                    event_emitter=emitter,
                )
            except Exception as exc:
                ok = False
                emitter(PipelineEvent.from_error(f"{input_path}: {exc}"))
        return 0 if ok else 1

    # Parallel: worker processes convert; events are emitted in input order.
    ok = True
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers, initializer=_worker_init) as executor:
        future_by_id = _submit_jobs(executor, inputs, output_base, args.preset, args.size)
        results = _collect_ordered(future_by_id, len(inputs))

    for job_id in range(len(inputs)):
        name, success, data = results[job_id]
        if success:
            emitter(PipelineEvent.from_outputs(data))
        else:
            ok = False
            emitter(PipelineEvent.from_error(f"{name}: {data}"))
    return 0 if ok else 1


def handle_convert(args: argparse.Namespace) -> int:
    inputs = _collect_image_inputs(args.inputs)
    if not inputs:
        print(" [!] Error: No input images found.", file=sys.stderr)
        return 1

    multi = len(inputs) > 1
    output_base = args.output
    if output_base and multi:
        if not output_base.exists() and not output_base.suffix:
            output_base.mkdir(parents=True, exist_ok=True)
        if not output_base.is_dir():
            print(
                f" [!] Warning: Multiple inputs detected but output '{output_base}' is a file. "
                "Default naming will be used to avoid overwriting.",
                file=sys.stderr,
            )

    if args.emit == "ndjson":
        return _handle_convert_ndjson(args, inputs, output_base)

    workers = _resolve_workers(getattr(args, "workers", 0), len(inputs))
    print(f"[*] Converting {len(inputs)} file(s) with {workers} worker process(es) "
          f"(multiprocessing + multithreaded PDF builds)...")

    ok = True
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers, initializer=_worker_init) as executor:
        future_by_id = _submit_jobs(executor, inputs, output_base, args.preset, args.size)
        results = _collect_ordered(future_by_id, len(inputs))

    # Ordered output: always report in the original input order.
    for job_id in range(len(inputs)):
        name, success, data = results[job_id]
        if success:
            print(f" [+] Success: {name}")
            print_outputs(data)
        else:
            ok = False
            print(f" [!] Failed: {name} - {data}", file=sys.stderr)
    return 0 if ok else 1


def handle_visualize_converting(args: argparse.Namespace) -> int:
    from ..visualizer.app import run_visualizer_adapter
    return run_visualizer_adapter(args)


def handle_command(args: argparse.Namespace) -> int:
    if args.command == "convert":
        return handle_convert(args)
    if args.command == "visualize-converting":
        return handle_visualize_converting(args)
    return 1
