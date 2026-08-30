from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sys
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import FairpostEngine  # noqa: E402


SCHEMA_VERSION = 1
_FORBIDDEN_SPLIT_PARTS = {
    "dev",
    "development",
    "eval",
    "evaluation",
    "holdout",
    "test",
    "tests",
}


def _path_parts(path: Path) -> set[str]:
    return {part.casefold().lstrip(".") for part in path.parts}


def validate_train_input(path: Path) -> Path:
    """Return the resolved input path after enforcing the train-only boundary."""
    supplied_parts = _path_parts(path)
    resolved = path.resolve(strict=False)
    all_parts = supplied_parts | _path_parts(resolved)

    forbidden = sorted(all_parts & _FORBIDDEN_SPLIT_PARTS)
    if forbidden:
        raise ValueError(
            "engine benchmarks reject holdout/test/dev/evaluation paths: "
            + ", ".join(forbidden)
        )
    if "train" not in all_parts:
        raise ValueError("engine benchmarks require an explicit train path component")
    if not resolved.is_file():
        raise ValueError(f"benchmark input is not a file: {path}")
    return resolved


def load_train_texts(path: Path, *, max_records: int | None = None) -> list[str]:
    resolved = validate_train_input(path)
    if max_records is not None and max_records <= 0:
        raise ValueError("max_records must be a positive integer")

    texts: list[str] = []
    with resolved.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{line_number}: invalid JSON object"
                ) from exc
            if not isinstance(value, dict) or not isinstance(value.get("text"), str):
                raise ValueError(f"{path}:{line_number}: string text is required")
            texts.append(value["text"])
            if max_records is not None and len(texts) >= max_records:
                break

    if not texts:
        raise ValueError(f"{path}: no training records found")
    return texts


def input_sha256(texts: Sequence[str]) -> str:
    """Hash the ordered, selected benchmark payload without retaining its text."""
    digest = hashlib.sha256(b"fairpost-engine-benchmark-input-v1\0")
    for text in texts:
        encoded = text.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, byteorder="big", signed=False))
        digest.update(encoded)
    return digest.hexdigest()


def _nearest_rank(sorted_values: Sequence[float], quantile: float) -> float:
    index = max(0, math.ceil(quantile * len(sorted_values)) - 1)
    return sorted_values[index]


def summarize_timings(timings_seconds: Sequence[float]) -> dict[str, Any]:
    if not timings_seconds:
        raise ValueError("at least one measured timing is required")
    if any(value < 0 for value in timings_seconds):
        raise ValueError("monotonic timings cannot be negative")

    elapsed_seconds = sum(timings_seconds)
    if elapsed_seconds <= 0:
        raise ValueError("measured elapsed time must be greater than zero")

    values_ms = sorted(value * 1000 for value in timings_seconds)
    sample_count = len(values_ms)
    return {
        "elapsed_seconds": round(elapsed_seconds, 6),
        "postings_per_second": round(sample_count / elapsed_seconds, 3),
        "latency_ms": {
            "samples": sample_count,
            "min": round(values_ms[0], 6),
            "mean": round(sum(values_ms) / sample_count, 6),
            "p50": round(_nearest_rank(values_ms, 0.50), 6),
            "p95": round(_nearest_rank(values_ms, 0.95), 6),
            "p99": round(_nearest_rank(values_ms, 0.99), 6),
            "max": round(values_ms[-1], 6),
        },
    }


def measure_engine(
    engine: Any,
    texts: Sequence[str],
    *,
    warmup: int,
    repeats: int,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    if warmup < 0:
        raise ValueError("warmup must be zero or greater")
    if repeats <= 0:
        raise ValueError("repeats must be a positive integer")
    if not texts:
        raise ValueError("at least one training record is required")

    for _ in range(warmup):
        for text in texts:
            engine.check(text)

    timings: list[float] = []
    for _ in range(repeats):
        for text in texts:
            started = clock()
            engine.check(text)
            finished = clock()
            duration = finished - started
            if duration < 0:
                raise RuntimeError("benchmark clock moved backwards")
            timings.append(duration)

    return summarize_timings(timings)


def build_report(
    input_path: Path,
    *,
    warmup: int = 1,
    repeats: int = 3,
    max_records: int | None = None,
    clock: Callable[[], float] = time.perf_counter,
    generated_at: datetime | None = None,
    engine: Any | None = None,
) -> dict[str, Any]:
    texts = load_train_texts(input_path, max_records=max_records)
    active_engine = engine if engine is not None else FairpostEngine()
    metrics = measure_engine(
        active_engine,
        texts,
        warmup=warmup,
        repeats=repeats,
        clock=clock,
    )
    timestamp = generated_at or datetime.now(timezone.utc)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": timestamp.astimezone(timezone.utc).isoformat(),
        "scope": "development_train_benchmark",
        "ruleset_version": active_engine.ruleset.version,
        "matching_version": active_engine.ruleset.matching_version,
        "input": {
            "split": "train_only",
            "records": len(texts),
            "sha256": input_sha256(texts),
        },
        "configuration": {
            "warmup_passes": warmup,
            "repeats": repeats,
            "max_records": max_records,
            "measured_postings": len(texts) * repeats,
            "engine_initialization_timed": False,
            "timing_source": "time.perf_counter (monotonic)",
            "percentile_method": "nearest_rank",
        },
        "runtime": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "operating_system": platform.system(),
            "machine": platform.machine(),
        },
        "metrics": metrics,
        "privacy": {
            "contains_posting_text": False,
            "contains_record_ids": False,
            "contains_organization_data": False,
            "contains_per_record_timings": False,
        },
        "production_sla_claim": False,
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    descriptor, staged_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _paths_alias(left: Path, right: Path) -> bool:
    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
    except OSError:
        pass
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def validate_output_path(input_path: Path, output_path: Path) -> None:
    if _paths_alias(input_path, output_path):
        raise ValueError("benchmark output must not overwrite its training input")
    if output_path.exists() and output_path.is_dir():
        raise ValueError("benchmark output must be a file path")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark the FairPost engine on training records only and emit a "
            "privacy-safe aggregate report. Results are not a production SLA."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / ".corpus-prd" / "train" / "records.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "engine_performance.json",
    )
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-records", type=int)
    args = parser.parse_args()

    try:
        validate_output_path(args.input, args.output)
        report = build_report(
            args.input,
            warmup=args.warmup,
            repeats=args.repeats,
            max_records=args.max_records,
        )
        _write_report(args.output, report)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))

    metrics = report["metrics"]
    print(
        f"benchmarked {report['input']['records']} training records: "
        f"{metrics['postings_per_second']} postings/s, "
        f"p95 {metrics['latency_ms']['p95']} ms -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
