from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import FairpostEngine  # noqa: E402
from tools import build_private_review_queue as review_queue  # noqa: E402


INPUT_FIELDS = frozenset(
    {
        "review_id",
        "rule_id",
        "layer",
        "dimension",
        "context",
        "matched_text",
        "section",
        "label",
        "allowed_labels",
    }
)
ALLOWED_LABELS = ("true_positive", "false_positive", "uncertain")
ALL_LABELS = (*ALLOWED_LABELS, "unreviewed")
ALLOWED_LAYERS = frozenset({"law", "question"})
FORBIDDEN_PATH_PARTS = frozenset(
    {"holdout", "hold-out", "test", "tests", "dev", "evaluation", "eval"}
)
REVIEW_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
QUEUE_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "ruleset_version",
        "matching_version",
        "input_sha256",
        "selected_rule_ids",
        "per_rule",
        "context_chars",
        "row_count",
        "rule_sampling",
        "immutable_rows_sha256",
    }
)


class ReviewSummaryError(ValueError):
    """A privacy-safe validation or report-writing failure."""


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().resolve(strict=False))).casefold()


def _path_variants(path: Path) -> tuple[Path, ...]:
    expanded = path.expanduser()
    try:
        return expanded, expanded.resolve(strict=False)
    except OSError:
        return (expanded,)


def _reject_forbidden_path(path: Path, *, argument: str) -> None:
    for candidate in _path_variants(path):
        for part in candidate.parts:
            lowered = part.casefold().rstrip(". ")
            if lowered in FORBIDDEN_PATH_PARTS:
                raise ReviewSummaryError(
                    f"{argument} is rejected by the train-only path policy"
                )
            if Path(part).stem.casefold().rstrip(". ") in FORBIDDEN_PATH_PARTS:
                raise ReviewSummaryError(
                    f"{argument} is rejected by the train-only path policy"
                )

        # Reject an explicitly partition-labelled filename such as
        # ``private-holdout-review.jsonl`` without rejecting pytest's temporary
        # parent directories (for example ``pytest-of-user``).
        filename_tokens = set(
            filter(None, re.split(r"[^a-z0-9]+", candidate.name.casefold()))
        )
        if filename_tokens & FORBIDDEN_PATH_PARTS:
            raise ReviewSummaryError(
                f"{argument} is rejected by the train-only path policy"
            )


def validate_paths(
    input_path: Path,
    output_path: Path,
    manifest_path: Path | None = None,
    source_input_path: Path | None = None,
) -> None:
    """Reject unsafe paths and collisions before the input is opened."""
    labeled_paths = [("--input", input_path), ("--output", output_path)]
    if manifest_path is not None:
        labeled_paths.append(("--manifest", manifest_path))
    if source_input_path is not None:
        labeled_paths.append(("--source-input", source_input_path))
    for argument, path in labeled_paths:
        _reject_forbidden_path(path, argument=argument)

    for index, (left_name, left_path) in enumerate(labeled_paths):
        for right_name, right_path in labeled_paths[index + 1 :]:
            same = _path_key(left_path) == _path_key(right_path)
            if not same and left_path.exists() and right_path.exists():
                try:
                    same = os.path.samefile(left_path, right_path)
                except OSError:
                    pass
            if same:
                raise ReviewSummaryError(
                    f"{left_name} and {right_name} must be different files"
                )
    if output_path.exists() and output_path.is_dir():
        raise ReviewSummaryError("--output must be a file path")
    if manifest_path is not None and manifest_path.exists() and manifest_path.is_dir():
        raise ReviewSummaryError("--manifest must be a file path")
    if source_input_path is not None:
        if source_input_path.exists() and source_input_path.is_dir():
            raise ReviewSummaryError("--source-input must be a file path")
        if not any(
            part.casefold().rstrip(". ") == "train"
            or Path(part).stem.casefold().rstrip(". ") == "train"
            for candidate in _path_variants(source_input_path)
            for part in candidate.parts
        ):
            raise ReviewSummaryError("--source-input must name a train split path")


def _validate_thresholds(
    min_reviewed_per_rule: object, min_precision: object
) -> tuple[int, float]:
    if (
        isinstance(min_reviewed_per_rule, bool)
        or not isinstance(min_reviewed_per_rule, int)
        or min_reviewed_per_rule < 0
    ):
        raise ReviewSummaryError(
            "--min-reviewed-per-rule must be a non-negative integer"
        )
    if (
        isinstance(min_precision, bool)
        or not isinstance(min_precision, (int, float))
        or not math.isfinite(float(min_precision))
        or not 0 <= float(min_precision) <= 1
    ):
        raise ReviewSummaryError("--min-precision must be between 0 and 1")
    return min_reviewed_per_rule, float(min_precision)


def _rule_metadata() -> dict[str, tuple[str, str]]:
    """Return exactly the presence rules eligible for the private review queue."""
    result: dict[str, tuple[str, str]] = {}
    for rule in FairpostEngine().ruleset.rules:
        layer = str(rule.get("layer", ""))
        trigger = rule.get("trigger")
        if (
            layer in ALLOWED_LAYERS
            and isinstance(trigger, Mapping)
            and trigger.get("type") == "presence"
        ):
            result[str(rule["id"])] = (layer, str(rule["dimension"]))
    return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReviewSummaryError("JSON contains a duplicate object field")
        value[key] = item
    return value


def _invalid(line_number: int, detail: str) -> ReviewSummaryError:
    # Deliberately include only the physical line number and schema field name;
    # no input value, review identifier, context, or path is ever reflected.
    return ReviewSummaryError(f"line {line_number}: {detail}")


def _validate_row(
    value: object,
    *,
    line_number: int,
    rules: Mapping[str, tuple[str, str]],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid(line_number, "a JSON object is required")
    if set(value) != INPUT_FIELDS:
        raise _invalid(line_number, "fields do not match the anonymous queue schema")

    review_id = value["review_id"]
    if not isinstance(review_id, str) or REVIEW_ID_RE.fullmatch(review_id) is None:
        raise _invalid(line_number, "review_id is invalid")

    rule_id = value["rule_id"]
    if not isinstance(rule_id, str) or rule_id not in rules:
        raise _invalid(line_number, "rule_id is invalid")
    expected_layer, expected_dimension = rules[rule_id]

    layer = value["layer"]
    if (
        not isinstance(layer, str)
        or layer not in ALLOWED_LAYERS
        or layer != expected_layer
    ):
        raise _invalid(line_number, "layer does not match rule_id")

    dimension = value["dimension"]
    if not isinstance(dimension, str) or dimension != expected_dimension:
        raise _invalid(line_number, "dimension does not match rule_id")

    context = value["context"]
    matched_text = value["matched_text"]
    if not isinstance(context, str) or not context:
        raise _invalid(line_number, "context must be a non-empty string")
    if not isinstance(matched_text, str) or not matched_text:
        raise _invalid(line_number, "matched_text must be a non-empty string")

    section = value["section"]
    if section is not None and not isinstance(section, str):
        raise _invalid(line_number, "section must be a string or null")

    allowed_labels = value["allowed_labels"]
    if allowed_labels != list(ALLOWED_LABELS):
        raise _invalid(line_number, "allowed_labels is invalid")
    label = value["label"]
    if not isinstance(label, str) or label not in ALL_LABELS:
        raise _invalid(line_number, "label is invalid")

    # Keep validated fields in memory for provenance verification. Sensitive
    # fields are never copied into the aggregate report or stdout.
    return {field: value[field] for field in INPUT_FIELDS}


def load_review_rows(path: Path) -> list[dict[str, Any]]:
    rules = _rule_metadata()
    rows: list[dict[str, Any]] = []
    seen_review_ids: set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                if not raw_line.strip():
                    continue
                try:
                    value = json.loads(raw_line, object_pairs_hook=_unique_object)
                except ReviewSummaryError as exc:
                    raise _invalid(line_number, str(exc)) from exc
                except json.JSONDecodeError as exc:
                    raise _invalid(line_number, "malformed JSON") from exc
                row = _validate_row(value, line_number=line_number, rules=rules)
                review_id = row["review_id"]
                if review_id in seen_review_ids:
                    raise _invalid(line_number, "duplicate review_id")
                seen_review_ids.add(review_id)
                rows.append(row)
    except ReviewSummaryError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ReviewSummaryError("--input could not be read") from exc
    return rows


def _load_queue_manifest(
    path: Path,
    rows: Sequence[dict[str, Any]],
    *,
    source_input_sha256: str | None = None,
) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except ReviewSummaryError:
        raise
    except json.JSONDecodeError as exc:
        raise ReviewSummaryError("--manifest contains malformed JSON") from exc
    except (OSError, UnicodeError) as exc:
        raise ReviewSummaryError("--manifest could not be read") from exc
    if not isinstance(value, dict) or set(value) != QUEUE_MANIFEST_FIELDS:
        raise ReviewSummaryError("--manifest fields are invalid")
    if value["schema_version"] != "private-review-queue-manifest-v1":
        raise ReviewSummaryError("--manifest schema_version is invalid")

    engine = FairpostEngine()
    if value["ruleset_version"] != engine.ruleset.version:
        raise ReviewSummaryError("--manifest ruleset_version is stale")
    if value["matching_version"] != engine.ruleset.matching_version:
        raise ReviewSummaryError("--manifest matching_version is stale")
    for field in ("input_sha256", "immutable_rows_sha256"):
        item = value[field]
        if not isinstance(item, str) or REVIEW_ID_RE.fullmatch(item) is None:
            raise ReviewSummaryError(f"--manifest {field} is invalid")
    if (
        source_input_sha256 is not None
        and value["input_sha256"] != source_input_sha256
    ):
        raise ReviewSummaryError("--source-input does not match --manifest input_sha256")

    selected = value["selected_rule_ids"]
    metadata = _rule_metadata()
    if (
        not isinstance(selected, list)
        or not selected
        or any(not isinstance(rule_id, str) for rule_id in selected)
        or selected != sorted(set(selected))
        or any(rule_id not in metadata for rule_id in selected)
    ):
        raise ReviewSummaryError("--manifest selected_rule_ids is invalid")
    for field, minimum in (("per_rule", 1), ("context_chars", 0)):
        item = value[field]
        if isinstance(item, bool) or not isinstance(item, int) or item < minimum:
            raise ReviewSummaryError(f"--manifest {field} is invalid")
    row_count = value["row_count"]
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
        raise ReviewSummaryError("--manifest row_count is invalid")
    if row_count != len(rows):
        raise ReviewSummaryError("--manifest row_count does not match the queue")
    rule_sampling = value["rule_sampling"]
    sampling_fields = {
        "candidate_matches",
        "unique_contexts",
        "selected_rows",
        "collapsed_duplicate_contexts",
        "truncated_unique_contexts",
    }
    if not isinstance(rule_sampling, dict) or set(rule_sampling) != set(selected):
        raise ReviewSummaryError("--manifest rule_sampling is invalid")
    selected_total = 0
    actual_rows_by_rule = Counter(str(row["rule_id"]) for row in rows)
    for rule_id, sampling in rule_sampling.items():
        if not isinstance(sampling, dict) or set(sampling) != sampling_fields:
            raise ReviewSummaryError("--manifest rule_sampling is invalid")
        if any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in sampling.values()
        ):
            raise ReviewSummaryError("--manifest rule_sampling is invalid")
        candidates = sampling["candidate_matches"]
        unique = sampling["unique_contexts"]
        selected_rows = sampling["selected_rows"]
        if (
            selected_rows > unique
            or unique > candidates
            or sampling["collapsed_duplicate_contexts"] != candidates - unique
            or sampling["truncated_unique_contexts"] != unique - selected_rows
        ):
            raise ReviewSummaryError("--manifest rule_sampling is inconsistent")
        if selected_rows != actual_rows_by_rule[rule_id]:
            raise ReviewSummaryError(
                "--manifest rule_sampling does not match per-rule queue rows"
            )
        selected_total += selected_rows
    if selected_total != row_count:
        raise ReviewSummaryError("--manifest rule_sampling row count is inconsistent")
    if any(row["rule_id"] not in selected for row in rows):
        raise ReviewSummaryError("--manifest selected rules do not match the queue")
    if value["immutable_rows_sha256"] != review_queue.immutable_rows_sha256(rows):
        raise ReviewSummaryError("--manifest immutable row digest does not match")
    return value


def _verify_queue_derivation(
    source_payload: bytes,
    source_path: Path,
    rows: Sequence[dict[str, Any]],
    manifest: Mapping[str, Any],
) -> None:
    """Rebuild the canonical queue so a self-consistent forgery cannot pass."""
    try:
        source_records = review_queue.load_private_train_records_bytes(
            source_payload,
            source_path=source_path,
        )
        expected_rows, _counts, expected_sampling = review_queue.build_queue_rows(
            source_records,
            FairpostEngine(),
            rule_ids=tuple(manifest["selected_rule_ids"]),
            per_rule=int(manifest["per_rule"]),
            context_chars=int(manifest["context_chars"]),
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReviewSummaryError(
            "--source-input could not reproduce the review queue"
        ) from exc
    if expected_sampling != manifest["rule_sampling"]:
        raise ReviewSummaryError(
            "--source-input does not reproduce --manifest rule_sampling"
        )
    if review_queue.immutable_rows_sha256(
        expected_rows
    ) != review_queue.immutable_rows_sha256(rows):
        raise ReviewSummaryError(
            "--source-input does not reproduce immutable queue rows"
        )


def _precision(counts: Mapping[str, int]) -> float | None:
    decided = counts["true_positive"] + counts["false_positive"]
    if decided == 0:
        return None
    rounded = round(counts["true_positive"] / decided, 6)
    return 0.0 if rounded == 0 else rounded


def _metrics(counts: Mapping[str, int]) -> dict[str, int | float | None]:
    true_positive = counts["true_positive"]
    false_positive = counts["false_positive"]
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "uncertain": counts["uncertain"],
        "unreviewed": counts["unreviewed"],
        "decided": true_positive + false_positive,
        "precision": _precision(counts),
    }


def build_summary(
    rows: Iterable[Mapping[str, Any]],
    *,
    min_reviewed_per_rule: int = 0,
    min_precision: float = 0.0,
    expected_rule_ids: Sequence[str] = (),
    provenance_verified: bool = False,
    manifest_verified: bool = False,
    source_input_verified: bool = False,
    expected_rules_explicit: bool = False,
) -> dict[str, Any]:
    minimum_reviewed, minimum_precision = _validate_thresholds(
        min_reviewed_per_rule, min_precision
    )
    grouped: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    aggregate_counts: Counter[str] = Counter()
    layer_counts: dict[str, Counter[str]] = {
        layer: Counter() for layer in sorted(ALLOWED_LAYERS)
    }
    row_count = 0
    for row in rows:
        row_count += 1
        label = row["label"]
        key = (row["rule_id"], row["layer"])
        grouped[key][label] += 1
        aggregate_counts[label] += 1
        layer_counts[row["layer"]][label] += 1

    rule_metadata = _rule_metadata()
    seen_expected: set[str] = set()
    for rule_id in expected_rule_ids:
        if rule_id in seen_expected:
            raise ReviewSummaryError("duplicate --expect-rule-id")
        seen_expected.add(rule_id)
        metadata = rule_metadata.get(rule_id)
        if metadata is None:
            raise ReviewSummaryError(
                "--expect-rule-id must name a presence law/question rule"
            )
        layer, _dimension = metadata
        grouped[(rule_id, layer)] += Counter()

    rule_summaries: list[dict[str, Any]] = []
    insufficient_reviews: list[dict[str, Any]] = []
    precision_below_threshold: list[dict[str, Any]] = []
    for (rule_id, layer), counts in sorted(grouped.items()):
        metrics = _metrics(counts)
        rule_summary = {"rule_id": rule_id, "layer": layer, **metrics}
        rule_summaries.append(rule_summary)

        # A precision gate needs an effective denominator. ``uncertain`` rows
        # document useful review effort but cannot count toward the minimum
        # sample used to judge precision.
        reviewed = int(metrics["decided"])
        if reviewed < minimum_reviewed:
            insufficient_reviews.append(
                {
                    "rule_id": rule_id,
                    "reviewed": reviewed,
                    "minimum": minimum_reviewed,
                }
            )
        precision = metrics["precision"]
        if (
            precision is not None
            and precision < minimum_precision
        ) or (precision is None and minimum_precision > 0):
            precision_below_threshold.append(
                {
                    "rule_id": rule_id,
                    "precision": precision,
                    "minimum": minimum_precision,
                }
            )

    empty_queue = []
    if row_count == 0 and not expected_rule_ids and (
        minimum_reviewed > 0 or minimum_precision > 0
    ):
        empty_queue.append(
            {
                "reviewed": 0,
                "minimum_per_rule": minimum_reviewed,
                "minimum_precision": minimum_precision,
            }
        )
    alerts = {
        "empty_queue": empty_queue,
        "insufficient_reviews": insufficient_reviews,
        "precision_below_threshold": precision_below_threshold,
        "unverified_provenance": (
            [{"required_for_quality_gate": True}]
            if not provenance_verified
            and (minimum_reviewed > 0 or minimum_precision > 0)
            else []
        ),
    }
    status = "alert" if any(alerts.values()) else "ok"
    return {
        "schema_version": "private-review-summary-v2",
        "evaluation_phase": "train_review",
        "release_claim_eligible": False,
        "status": status,
        "thresholds": {
            "min_reviewed_per_rule": minimum_reviewed,
            "min_precision": minimum_precision,
        },
        "rules": rule_summaries,
        "aggregate": _metrics(aggregate_counts),
        "aggregates_by_layer": {
            layer: _metrics(counts) for layer, counts in layer_counts.items()
        },
        "interpretation": {
            "aggregate_is_row_weighted": True,
            "aggregate_is_not_posting_level_performance": True,
            "rule_metrics_are_primary": True,
            "labels_are_rule_relevance_not_discrimination_or_illegality_determinations": True,
        },
        "provenance": {
            "verified": provenance_verified,
            "manifest_verified": manifest_verified,
            "source_input_verified": source_input_verified,
            "expected_rules_explicit": expected_rules_explicit,
            "selected_rules_from_manifest": manifest_verified,
        },
        "privacy_boundary": {
            "contains_posting_text": False,
            "contains_review_ids": False,
            "contains_record_ids": False,
            "contains_source_ids": False,
            "contains_organization_identifiers": False,
            "contains_personal_identifiers": False,
        },
        "alerts": alerts,
    }


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    staged_path = path.with_name(f".{path.name}.fairpost-temp-{uuid4().hex}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        staged_path.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(staged_path, path)
    except (OSError, UnicodeError) as exc:
        raise ReviewSummaryError("--output report could not be written") from exc
    finally:
        try:
            staged_path.unlink(missing_ok=True)
        except OSError:
            pass


def summarize_private_review(
    input_path: Path,
    output_path: Path,
    *,
    manifest_path: Path | None = None,
    source_input_path: Path | None = None,
    min_reviewed_per_rule: int = 0,
    min_precision: float = 0.0,
    expected_rule_ids: Sequence[str] = (),
) -> dict[str, Any]:
    input_path = Path(input_path)
    output_path = Path(output_path)
    manifest_path = Path(manifest_path) if manifest_path is not None else None
    source_input_path = (
        Path(source_input_path) if source_input_path is not None else None
    )
    minimum_reviewed, minimum_precision = _validate_thresholds(
        min_reviewed_per_rule, min_precision
    )
    validate_paths(input_path, output_path, manifest_path, source_input_path)
    # Validate expected rules before opening the queue.
    metadata = _rule_metadata()
    if len(expected_rule_ids) != len(set(expected_rule_ids)):
        raise ReviewSummaryError("duplicate --expect-rule-id")
    if any(rule_id not in metadata for rule_id in expected_rule_ids):
        raise ReviewSummaryError(
            "--expect-rule-id must name a presence law/question rule"
        )
    rows = load_review_rows(input_path)
    manifest_verified = manifest_path is not None
    source_input_verified = source_input_path is not None
    expected_rules_explicit = bool(expected_rule_ids)
    if source_input_path is not None and manifest_path is None:
        raise ReviewSummaryError("--source-input requires --manifest")
    source_input_payload: bytes | None = None
    source_input_sha256 = None
    if source_input_path is not None:
        try:
            source_input_payload = source_input_path.read_bytes()
            source_input_sha256 = hashlib.sha256(source_input_payload).hexdigest()
        except OSError as exc:
            raise ReviewSummaryError("--source-input could not be read") from exc
    effective_expected_rule_ids = tuple(expected_rule_ids)
    if manifest_path is not None:
        manifest = _load_queue_manifest(
            manifest_path,
            rows,
            source_input_sha256=source_input_sha256,
        )
        manifest_rule_ids = tuple(manifest["selected_rule_ids"])
        if source_input_payload is not None and source_input_path is not None:
            _verify_queue_derivation(
                source_input_payload,
                source_input_path,
                rows,
                manifest,
            )
        if effective_expected_rule_ids and set(effective_expected_rule_ids) != set(
            manifest_rule_ids
        ):
            raise ReviewSummaryError(
                "--expect-rule-id values must match --manifest selected rules"
            )
        effective_expected_rule_ids = manifest_rule_ids
    quality_gate_requested = minimum_reviewed > 0 or minimum_precision > 0
    provenance_verified = manifest_verified and (
        not quality_gate_requested
        or (source_input_verified and expected_rules_explicit)
    )
    report = build_summary(
        rows,
        min_reviewed_per_rule=minimum_reviewed,
        min_precision=minimum_precision,
        expected_rule_ids=effective_expected_rule_ids,
        provenance_verified=provenance_verified,
        manifest_verified=manifest_verified,
        source_input_verified=source_input_verified,
        expected_rules_explicit=expected_rules_explicit,
    )
    _write_report(output_path, report)
    return report


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize a de-identified private review queue into deterministic "
            "per-rule and aggregate label metrics. No row text or review ID is "
            "written to the report or stdout."
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        help=(
            "Queue sidecar manifest. Quality thresholds cannot pass without "
            "verified provenance."
        ),
    )
    parser.add_argument(
        "--source-input",
        type=Path,
        help=(
            "Original train snapshot records. Its SHA-256 must match the queue "
            "manifest for a positive quality gate to pass."
        ),
    )
    parser.add_argument("--min-reviewed-per-rule", type=int, default=0)
    parser.add_argument("--min-precision", type=float, default=0.0)
    parser.add_argument(
        "--expect-rule-id",
        action="append",
        default=[],
        help="Rule that must be represented even when the queue has zero rows",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        report = summarize_private_review(
            args.input,
            args.output,
            manifest_path=args.manifest,
            source_input_path=args.source_input,
            min_reviewed_per_rule=args.min_reviewed_per_rule,
            min_precision=args.min_precision,
            expected_rule_ids=args.expect_rule_id,
        )
    except ReviewSummaryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Stdout is exactly the anonymous aggregate, even when the gate fails.
    print(
        json.dumps(
            report["aggregate"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 2 if report["status"] == "alert" else 0


if __name__ == "__main__":
    raise SystemExit(main())
