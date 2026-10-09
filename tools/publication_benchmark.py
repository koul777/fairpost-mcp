"""Private operational benchmark; never creates labels or accuracy claims.

Requires a coordinator-created split manifest binding the exact records bytes:
{"schema_version": 1, "phase": "development", "records_sha256": "...",
 "records": [{"id": "...", "content_hash": "..."}]}

Validation must use separately named validation files and --final-validation.
Existing sealed holdout paths remain forbidden even with that flag. This tool
never touches the human-gold final evaluator or its receipts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
import time
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = ROOT / ".assay" / "publication-20261009"
REQUIRED = ("id", "source_url", "snapshot", "content_hash", "text", "title",
            "sector", "occupation", "employer")
FORBIDDEN_PATH_MARKERS = ("holdout", "sealed-reserved", ".corpus")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def guard_path(path: Path, *, final_validation: bool = False) -> Path:
    """Check supplied and resolved names before opening any file."""
    resolved = path.resolve()
    names = (str(path).replace("\\", "/").casefold(),
             str(resolved).replace("\\", "/").casefold())
    if any(marker in name for name in names for marker in FORBIDDEN_PATH_MARKERS):
        raise ValueError("holdout, sealed-reserved, and existing corpus paths are forbidden")
    if not final_validation and any("validation" in name for name in names):
        raise ValueError("validation paths require --final-validation")
    if not final_validation and any("reserved" in name for name in names):
        raise ValueError("reserved paths cannot be development inputs")
    return resolved


def private_path(path: Path, *, final_validation: bool = False,
                 private_root: Path = PRIVATE_ROOT) -> Path:
    resolved = guard_path(path, final_validation=final_validation)
    if not resolved.is_relative_to(private_root.resolve()):
        raise ValueError("per-posting inputs and outputs must stay in the private publication run")
    return resolved


def _is_hash(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def load_inputs(records_path: Path, manifest_path: Path, *,
                final_validation: bool = False,
                private_root: Path = PRIVATE_ROOT) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # Both boundaries are checked before either body is opened.
    records_path = private_path(records_path, final_validation=final_validation,
                                private_root=private_root)
    manifest_path = private_path(manifest_path, final_validation=final_validation,
                                 private_root=private_root)
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    phase = "validation" if final_validation else "development"
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("split manifest schema_version must be 1")
    if manifest.get("phase") != phase:
        raise ValueError("split manifest phase must be " + phase)
    if final_validation and "validation" not in records_path.name.casefold() and "validation" not in records_path.parent.name.casefold():
        raise ValueError("final validation requires a separately named validation input")
    if not _is_hash(manifest.get("records_sha256")):
        raise ValueError("split manifest must bind records_sha256")
    bindings = manifest.get("records")
    if not isinstance(bindings, list) or not bindings:
        raise ValueError("split manifest must contain nonempty record bindings")
    expected: dict[str, str] = {}
    for item in bindings:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not _is_hash(item.get("content_hash")):
            raise ValueError("invalid split record binding")
        if item["id"] in expected:
            raise ValueError("duplicate split manifest id")
        expected[item["id"]] = item["content_hash"]
    records_bytes = records_path.read_bytes()
    if sha256(records_bytes) != manifest["records_sha256"]:
        raise ValueError("records bytes do not match split manifest")
    records: list[dict[str, Any]] = []
    ids: set[str] = set()
    hashes: set[str] = set()
    for number, line in enumerate(records_bytes.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict) or any(not isinstance(record.get(k), str) or not record[k].strip() for k in REQUIRED):
            raise ValueError(f"line {number}: required nonempty string fields missing")
        if not final_validation and record.get("reserved"):
            raise ValueError("reserved record cannot be a development input")
        if any(k.startswith("expected_") or k in {"human_gold", "attestation", "labels", "predictions", "annotations"} for k in record):
            raise ValueError("benchmark records must not contain labels or predictions")
        url = urlsplit(record["source_url"])
        if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password:
            raise ValueError("source_url must be a public HTTP(S) URL without credentials")
        if not _is_hash(record["content_hash"]) or sha256(record["text"].encode("utf-8")) != record["content_hash"]:
            raise ValueError("text content_hash mismatch")
        if record["id"] in ids or record["content_hash"] in hashes:
            raise ValueError("duplicate record id or body hash")
        ids.add(record["id"])
        hashes.add(record["content_hash"])
        if expected.get(record["id"]) != record["content_hash"]:
            raise ValueError("record does not match split binding")
        snapshot_name = Path(record["snapshot"])
        if snapshot_name.is_absolute() or ".." in snapshot_name.parts:
            raise ValueError("snapshot must be a relative contained path")
        snapshot = private_path(records_path.parent / snapshot_name,
                                final_validation=final_validation, private_root=private_root)
        if not snapshot.is_relative_to(records_path.parent):
            raise ValueError("snapshot escapes record directory")
        if snapshot.read_bytes() != record["text"].encode("utf-8"):
            raise ValueError("snapshot bytes do not match original text")
        records.append(record)
    if not records or ids != set(expected):
        raise ValueError("records must exactly cover split manifest")
    return records, {"phase": phase, "records_sha256": sha256(records_bytes),
                     "split_manifest_sha256": sha256(manifest_bytes),
                     "record_bindings": [{"id": r["id"], "content_hash": r["content_hash"]} for r in records]}


def fingerprint_sources(root: Path = ROOT) -> dict[str, str]:
    """Bind implementation and bundled rule bytes, without reading corpora."""
    files = sorted((root / "core").glob("*.py")) + sorted((root / "data").rglob("*.yaml"))
    files.append(root / "tools" / "publication_benchmark.py")
    return {p.relative_to(root).as_posix(): sha256(p.read_bytes()) for p in files}


def _latency(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"samples": 0, "min": None, "mean": None, "p50": None, "p95": None, "max": None}
    ordered = sorted(values)
    return {"samples": len(values), "min": ordered[0], "mean": sum(values) / len(values),
            "p50": ordered[max(0, math.ceil(len(values) * .5) - 1)],
            "p95": ordered[max(0, math.ceil(len(values) * .95) - 1)], "max": ordered[-1]}


def measure(records: list[dict[str, Any]], engine: Any, *,
            clock: Callable[[], float] = time.perf_counter) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    per_record: list[dict[str, Any]] = []
    findings: Counter[str] = Counter()
    questions: Counter[str] = Counter()
    slot_found: Counter[str] = Counter()
    slot_observed: Counter[str] = Counter()
    successes = 0
    for record in records:
        item = {"id": record["id"], "source_url": record["source_url"],
                "content_hash": record["content_hash"]}
        start = clock()
        try:
            # Preserve original text exactly; no title injection or normalization here.
            result = engine.check(record["text"]).to_dict()
            item["result"] = result
            item["status"] = "ok"
        except Exception as exc:
            item["status"] = "error"
            item["error"] = {"type": type(exc).__name__, "message": str(exc)}
        elapsed = (clock() - start) * 1000
        if elapsed < 0 or not math.isfinite(elapsed):
            raise RuntimeError("benchmark clock is not finite and monotonic")
        item["latency_ms"] = elapsed
        per_record.append(item)
        if item["status"] == "ok":
            successes += 1
            findings.update({f["id"] for f in result.get("findings", [])})
            questions.update({q["id"] for q in result.get("questions", [])})
            for slot in result.get("slots", []):
                slot_observed[slot["slot"]] += 1
                if slot["found"]:
                    slot_found[slot["slot"]] += 1
    aggregate = {"attempted": len(records), "succeeded": successes,
                 "errors": len(records) - successes,
                 "latency_ms": _latency([r["latency_ms"] for r in per_record]),
                 "coverage": {"denominator": "successfully processed postings; observed outputs, not correctness",
                    "successful_postings": successes,
                    "finding_posting_counts": dict(sorted(findings.items())),
                    "question_posting_counts": dict(sorted(questions.items())),
                    "slot_observation_counts": dict(sorted(slot_observed.items())),
                    "slot_found_posting_counts": dict(sorted(slot_found.items()))},
                 "sample_counts": {key: dict(sorted(Counter(r[key] for r in records).items()))
                                   for key in ("sector", "occupation", "employer")}}
    return per_record, aggregate


def _engine() -> Any:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from core import FairpostEngine
    return FairpostEngine()


def run_benchmark(records_path: Path, manifest_path: Path, output_dir: Path, *,
                  final_validation: bool = False, private_root: Path = PRIVATE_ROOT,
                  engine_factory: Callable[[], Any] = _engine,
                  fingerprint: Callable[[], dict[str, str]] = fingerprint_sources,
                  clock: Callable[[], float] = time.perf_counter) -> dict[str, Any]:
    output_dir = private_path(output_dir, final_validation=final_validation, private_root=private_root)
    if output_dir.exists():
        raise ValueError("output directory must be new; existing reports are never overwritten")
    records, integrity = load_inputs(records_path, manifest_path,
                                    final_validation=final_validation, private_root=private_root)
    before = fingerprint()
    engine = engine_factory()
    per_record, aggregate = measure(records, engine, clock=clock)
    after = fingerprint()
    if before != after:
        raise RuntimeError("engine or rule source changed during benchmark; no report saved")
    report = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
              "scope": "exploratory_validation_benchmark" if final_validation else "development_benchmark",
              "accuracy_claim": False, "human_gold_evaluation": False, "production_sla_claim": False,
              "interpretation": "Output frequencies and latency only; absent slots are not proof a procedure does not exist.",
              "ruleset_version": engine.ruleset.version,
              "matching_version": engine.ruleset.matching_version,
              "runtime": {"python": platform.python_version(), "implementation": platform.python_implementation(),
                          "platform": platform.platform()},
              "configuration": {"passes": 1, "engine_initialization_timed": False,
                                "timing_source": "monotonic perf_counter", "percentile_method": "nearest_rank"},
              "integrity": {**integrity, "source_files_sha256": before,
                            "source_fingerprint_sha256": sha256(json.dumps(before, sort_keys=True).encode("utf-8"))},
              "aggregate": aggregate, "privacy": "Local private run only; per-record results can contain original excerpts."}
    result_bytes = ("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in per_record)).encode("utf-8")
    report["integrity"]["results_sha256"] = sha256(result_bytes)
    # All validation and measurement precede writes; exclusive files prevent overwrite.
    output_dir.mkdir(parents=True, exist_ok=False)
    with (output_dir / "results.jsonl").open("xb") as handle:
        handle.write(result_bytes)
    with (output_dir / "report.json").open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explicit split-bound private benchmark; no accuracy claims")
    parser.add_argument("--records", required=True, type=Path)
    parser.add_argument("--split-manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--final-validation", action="store_true",
                        help="Allow a separately named validation split; never existing holdout")
    args = parser.parse_args(argv)
    try:
        report = run_benchmark(args.records, args.split_manifest, args.output_dir,
                               final_validation=args.final_validation)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    metrics = report["aggregate"]
    print(json.dumps({"attempted": metrics["attempted"], "succeeded": metrics["succeeded"],
                      "errors": metrics["errors"], "accuracy_claim": False}))
    return 1 if metrics["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
