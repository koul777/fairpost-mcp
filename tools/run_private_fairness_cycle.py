from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import FairpostEngine  # noqa: E402
from tools import build_private_review_queue as review_queue  # noqa: E402
from tools import build_private_review_sampling_audit as sampling_audit  # noqa: E402
from tools import check_private_fairness_drift as drift_checker  # noqa: E402
from tools import run_private_monitoring as monitoring  # noqa: E402


class PrivateFairnessCycleError(ValueError):
    """Privacy-safe failure raised by one stage of the combined cycle."""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        super().__init__(f"private fairness cycle failed during {stage}")


class _SafeArgumentParser(argparse.ArgumentParser):
    """Keep attacker-controlled argument values out of argparse errors."""

    def error(self, message: str) -> None:
        del message
        raise PrivateFairnessCycleError("argument validation")


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False))).casefold()


def _same_existing_file(left: Path, right: Path) -> bool:
    if not left.exists() or not right.exists():
        return False
    try:
        return os.path.samefile(left, right)
    except OSError:
        # The owning stage will later turn an inaccessible path into a safe
        # execution error.  Preflight still catches every resolvable alias.
        return False


def validate_cycle_paths(
    input_path: Path,
    output_dir: Path,
    snapshot_summary_path: Path,
    audit_output_path: Path,
    baseline_audit_path: Path,
    drift_output_path: Path,
    review_queue_output_path: Path,
    sampling_audit_output_path: Path | None = None,
    *,
    exclude_manifests: Sequence[Path] = (),
) -> Path:
    """Preflight the complete read/write graph before any file is opened."""
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    snapshot_summary_path = Path(snapshot_summary_path)
    audit_output_path = Path(audit_output_path)
    baseline_audit_path = Path(baseline_audit_path)
    drift_output_path = Path(drift_output_path)
    review_queue_output_path = Path(review_queue_output_path)
    sampling_audit_output_path = (
        Path(sampling_audit_output_path)
        if sampling_audit_output_path is not None
        else None
    )
    exclude_paths = tuple(Path(path) for path in exclude_manifests)

    records_path = output_dir / "train" / "records.jsonl"
    manifest_path = output_dir / "train" / "manifest.json"
    queue_manifest_output_path = review_queue.queue_manifest_path(
        review_queue_output_path
    )
    user_paths = [
        ("--input", input_path),
        ("--output-dir", output_dir),
        ("--snapshot-summary", snapshot_summary_path),
        ("--audit-output", audit_output_path),
        ("--baseline-audit", baseline_audit_path),
        ("--drift-output", drift_output_path),
        ("--review-queue-output", review_queue_output_path),
        *(
            (("--sampling-audit-output", sampling_audit_output_path),)
            if sampling_audit_output_path is not None
            else ()
        ),
        *(("--exclude-manifest", path) for path in exclude_paths),
    ]
    for argument, path in user_paths:
        monitoring.snapshot_builder.reject_non_train_path(path, argument=argument)
        # Drift's shared check additionally catches an exact forbidden token in
        # a compound filename (for example ``current-test-audit.json``).
        drift_checker.reject_disallowed_path(path, argument=argument)

    # Reuse each stage's own boundary checks while all paths are still unread.
    monitoring.validate_run_paths(
        input_path,
        output_dir,
        snapshot_summary_path,
        audit_output_path,
        exclude_manifests=exclude_paths,
        baseline_path=baseline_audit_path,
    )
    drift_checker.validate_paths(
        audit_output_path, baseline_audit_path, drift_output_path
    )
    review_queue.validate_paths(records_path, review_queue_output_path)

    reads = [
        ("--input", input_path),
        ("--baseline-audit", baseline_audit_path),
        *(("--exclude-manifest", path) for path in exclude_paths),
    ]
    writes = [
        ("snapshot records", records_path),
        ("snapshot manifest", manifest_path),
        ("--snapshot-summary", snapshot_summary_path),
        ("--audit-output", audit_output_path),
        ("--drift-output", drift_output_path),
        ("--review-queue-output", review_queue_output_path),
        ("review queue manifest", queue_manifest_output_path),
        *(
            (("--sampling-audit-output", sampling_audit_output_path),)
            if sampling_audit_output_path is not None
            else ()
        ),
    ]

    output_dir_key = _path_key(output_dir)
    for _label, path in writes:
        if _path_key(path) == output_dir_key or _same_existing_file(path, output_dir):
            raise ValueError("an output file collides with --output-dir")
        if path.exists() and path.is_dir():
            raise ValueError("every report output must be a file path")

    for index, (_left_label, left) in enumerate(writes):
        for _right_label, right in writes[index + 1 :]:
            if _path_key(left) == _path_key(right) or _same_existing_file(left, right):
                raise ValueError("output paths must be distinct")

    for _read_label, read_path in reads:
        if _path_key(read_path) == output_dir_key or _same_existing_file(
            read_path, output_dir
        ):
            raise ValueError("input and output paths must be distinct")
        for _write_label, write_path in writes:
            if _path_key(read_path) == _path_key(write_path) or _same_existing_file(
                read_path, write_path
            ):
                raise ValueError("input and output paths must be distinct")
    return records_path


def _finite_float(value: object, *, lower_open: bool, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} has an invalid value")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} has an invalid value") from exc
    lower_valid = parsed > 0 if lower_open else parsed >= 0
    if not math.isfinite(parsed) or not lower_valid or parsed > 1:
        raise ValueError(f"{name} has an invalid value")
    return parsed


def _nonnegative_int(value: object, *, minimum: int, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} has an invalid value")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} has an invalid value") from exc
    if parsed < minimum:
        raise ValueError(f"{name} has an invalid value")
    return parsed


def run_cycle(
    input_path: Path,
    output_dir: Path,
    snapshot_summary_path: Path,
    audit_output_path: Path,
    baseline_audit_path: Path,
    drift_output_path: Path,
    review_queue_output_path: Path,
    *,
    exclude_manifests: Sequence[Path] = (),
    rule_ids: Sequence[str] = (),
    high_frequency_threshold: float = monitoring.fairness_audit.DEFAULT_HIGH_FREQUENCY_THRESHOLD,
    max_record_rate_delta: float = drift_checker.DEFAULT_MAX_RECORD_RATE_DELTA,
    max_source_share_delta: float = drift_checker.DEFAULT_MAX_SOURCE_SHARE_DELTA,
    require_version_match: bool = False,
    per_rule: int = 20,
    context_chars: int = 120,
    sampling_audit_output_path: Path | None = None,
) -> dict[str, int | str]:
    """Run one private snapshot, audit, drift, and local review-queue cycle."""
    paths = tuple(
        Path(path)
        for path in (
            input_path,
            output_dir,
            snapshot_summary_path,
            audit_output_path,
            baseline_audit_path,
            drift_output_path,
            review_queue_output_path,
        )
    )
    (
        input_path,
        output_dir,
        snapshot_summary_path,
        audit_output_path,
        baseline_audit_path,
        drift_output_path,
        review_queue_output_path,
    ) = paths
    exclude_paths = tuple(Path(path) for path in exclude_manifests)
    sampling_audit_output_path = (
        Path(sampling_audit_output_path)
        if sampling_audit_output_path is not None
        else None
    )

    try:
        records_path = validate_cycle_paths(
            input_path,
            output_dir,
            snapshot_summary_path,
            audit_output_path,
            baseline_audit_path,
            drift_output_path,
            review_queue_output_path,
            sampling_audit_output_path,
            exclude_manifests=exclude_paths,
        )
        audit_threshold = _finite_float(
            high_frequency_threshold,
            lower_open=True,
            name="--high-frequency-threshold",
        )
        record_threshold = _finite_float(
            max_record_rate_delta,
            lower_open=False,
            name="--max-record-rate-delta",
        )
        source_threshold = _finite_float(
            max_source_share_delta,
            lower_open=False,
            name="--max-source-share-delta",
        )
        queue_limit = _nonnegative_int(per_rule, minimum=1, name="--per-rule")
        context_limit = _nonnegative_int(
            context_chars, minimum=0, name="--context-chars"
        )
        if not isinstance(require_version_match, bool):
            raise ValueError("--require-version-match has an invalid value")
        # Rules are validated after every path, but before either private input.
        review_queue.select_rules(FairpostEngine(), tuple(rule_ids))
        baseline = drift_checker.load_audit(baseline_audit_path, label="baseline")
    except (OSError, UnicodeError, ValueError) as exc:
        raise PrivateFairnessCycleError("preflight") from exc

    # Complete every stage under one temporary root.  Only after snapshot,
    # audit, drift, and review queue all succeed are the seven required artifacts
    # and optional sampling audit published as one recoverable transaction.
    # This prevents a failed late stage from leaving mixed cycle generations.
    with TemporaryDirectory(prefix="fairpost-private-cycle-") as temp_value:
        temp_root = Path(temp_value)
        staged_output_dir = temp_root / "snapshot"
        staged_summary_path = temp_root / "snapshot-summary.json"
        staged_audit_path = temp_root / "audit.json"
        staged_drift_path = temp_root / "drift.json"
        staged_queue_path = temp_root / "review-queue.jsonl"
        staged_queue_manifest_path = review_queue.queue_manifest_path(staged_queue_path)
        staged_sampling_audit_path = temp_root / "review-sampling.json"
        pinned_baseline_path = temp_root / "baseline.json"
        staged_records_path = staged_output_dir / "train" / "records.jsonl"
        staged_manifest_path = staged_output_dir / "train" / "manifest.json"

        # The baseline was validated and loaded once during preflight.  Pin
        # that exact payload for the audit stage so an external replacement of
        # the source file cannot make audit and drift compare different
        # baselines within one cycle.
        try:
            drift_checker._write_report(pinned_baseline_path, baseline)
        except (OSError, UnicodeError, ValueError) as exc:
            raise PrivateFairnessCycleError("baseline pin") from exc

        try:
            monitoring_summary = monitoring.run_monitoring(
                input_path,
                staged_output_dir,
                staged_summary_path,
                staged_audit_path,
                exclude_manifests=exclude_paths,
                baseline_path=pinned_baseline_path,
                high_frequency_threshold=audit_threshold,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            raise PrivateFairnessCycleError("snapshot and audit") from exc

        try:
            current = drift_checker.load_audit(staged_audit_path, label="current")
            drift_report = drift_checker.build_drift_report(
                current,
                baseline,
                max_record_rate_delta=record_threshold,
                max_source_share_delta=source_threshold,
                require_version_match=require_version_match,
            )
            drift_checker._write_report(staged_drift_path, drift_report)
        except (OSError, UnicodeError, ValueError) as exc:
            raise PrivateFairnessCycleError("drift") from exc

        try:
            queue_counts = review_queue.build_review_queue(
                staged_records_path,
                staged_queue_path,
                rule_ids=tuple(rule_ids),
                per_rule=queue_limit,
                context_chars=context_limit,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            raise PrivateFairnessCycleError("review queue") from exc

        if sampling_audit_output_path is not None:
            try:
                sampling_report = sampling_audit.build_sampling_audit(
                    staged_queue_path,
                    staged_queue_manifest_path,
                    staged_records_path,
                )
                drift_checker._write_report(
                    staged_sampling_audit_path,
                    sampling_report,
                )
            except (OSError, UnicodeError, ValueError) as exc:
                raise PrivateFairnessCycleError("review sampling audit") from exc

        try:
            publish_pairs = [
                (staged_records_path, records_path),
                (
                    staged_manifest_path,
                    output_dir / "train" / "manifest.json",
                ),
                (staged_summary_path, snapshot_summary_path),
                (staged_audit_path, audit_output_path),
                (staged_drift_path, drift_output_path),
                (staged_queue_path, review_queue_output_path),
                (
                    staged_queue_manifest_path,
                    review_queue.queue_manifest_path(review_queue_output_path),
                ),
            ]
            if sampling_audit_output_path is not None:
                publish_pairs.append(
                    (staged_sampling_audit_path, sampling_audit_output_path)
                )
            monitoring._publish_files(
                tuple(publish_pairs)
            )
        except (OSError, UnicodeError, ValueError) as exc:
            raise PrivateFairnessCycleError("publish") from exc

    # Do not include paths, hashes, source text, record IDs, or rule IDs.
    return {
        "alerts": int(drift_report["summary"]["alert_count"]),
        "audit_records": int(monitoring_summary["audit_records"]),
        "queued_reviews": sum(queue_counts.values()),
        "snapshot_records": int(monitoring_summary["snapshot_records"]),
        "status": str(drift_report["status"]),
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = _SafeArgumentParser(
        description=(
            "Run a private train-only snapshot, anonymous fairness audit, "
            "baseline drift check, and selected-rule review queue in one command."
        ),
        epilog=(
            "로컬 위험 경고 (LOCAL-RISK WARNING):\n"
            "Review queue context is de-identified but remains re-identification-sensitive.\n"
            "Keep it on an approved local system; do not transmit or broadly share it."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--snapshot-summary", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    parser.add_argument("--baseline-audit", type=Path, required=True)
    parser.add_argument("--drift-output", type=Path, required=True)
    parser.add_argument("--review-queue-output", type=Path, required=True)
    parser.add_argument("--sampling-audit-output", type=Path)
    parser.add_argument("--exclude-manifest", type=Path, action="append", default=[])
    parser.add_argument("--rule-id", action="append", default=[])
    parser.add_argument(
        "--high-frequency-threshold",
        default=str(monitoring.fairness_audit.DEFAULT_HIGH_FREQUENCY_THRESHOLD),
    )
    parser.add_argument(
        "--max-record-rate-delta",
        default=str(drift_checker.DEFAULT_MAX_RECORD_RATE_DELTA),
    )
    parser.add_argument(
        "--max-source-share-delta",
        default=str(drift_checker.DEFAULT_MAX_SOURCE_SHARE_DELTA),
    )
    parser.add_argument("--require-version-match", action="store_true")
    parser.add_argument("--per-rule", default="20")
    parser.add_argument("--context-chars", default="120")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
        summary = run_cycle(
            args.input,
            args.output_dir,
            args.snapshot_summary,
            args.audit_output,
            args.baseline_audit,
            args.drift_output,
            args.review_queue_output,
            exclude_manifests=args.exclude_manifest,
            rule_ids=args.rule_id,
            high_frequency_threshold=args.high_frequency_threshold,
            max_record_rate_delta=args.max_record_rate_delta,
            max_source_share_delta=args.max_source_share_delta,
            require_version_match=args.require_version_match,
            per_rule=args.per_rule,
            context_chars=args.context_chars,
            sampling_audit_output_path=args.sampling_audit_output,
        )
    except PrivateFairnessCycleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # Last-resort CLI privacy boundary: never stringify an unexpected error.
        print("error: private fairness cycle failed during execution", file=sys.stderr)
        return 1

    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 2 if summary["status"] == "alert" else 0


if __name__ == "__main__":
    raise SystemExit(main())
