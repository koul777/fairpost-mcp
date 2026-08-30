from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence
from uuid import uuid4


AUDIT_SCHEMA_VERSION = "private-fairness-audit-v1"
DRIFT_SCHEMA_VERSION = "private-fairness-drift-v1"
DEFAULT_MAX_RECORD_RATE_DELTA = 0.10
DEFAULT_MAX_SOURCE_SHARE_DELTA = 0.20
DISALLOWED_PATH_PARTS = frozenset(
    {"holdout", "hold-out", "test", "tests", "dev", "evaluation", "eval"}
)
APPROVED_SOURCE_CATEGORIES = frozenset(
    {
        "jincheon-jobs",
        "work24",
        "senior-job",
        "company-career-page",
        "licensed-feed",
        "other",
    }
)

_LAW_ID = re.compile(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+\Z")
_QUESTION_ID = re.compile(r"Q-[A-Z0-9]+(?:-[A-Z0-9]+)*\Z")
_SLOT_ID = re.compile(r"[a-z][a-z0-9_]*\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PRIVACY_KEYS = frozenset(
    {
        "contains_posting_text",
        "contains_record_ids",
        "contains_source_ids",
        "contains_organization_identifiers",
        "contains_personal_identifiers",
    }
)
_TOP_LEVEL_REQUIRED = frozenset(
    {
        "schema_version",
        "input",
        "law_rules",
        "questions",
        "slots_found",
        "slots_missing",
        "summary",
        "ruleset_version",
        "matching_version",
        "privacy_boundary",
    }
)
_SUMMARY_KEYS = frozenset(
    {
        "records_with_law_findings",
        "records_with_law_findings_rate",
        "law_rules_never_observed",
        "questions_never_observed",
        "high_frequency_questions",
        "high_frequency_missing_slots",
        "high_frequency_threshold",
    }
)
_SECTIONS = ("law_rules", "questions", "slots_found")


class DriftInputError(ValueError):
    """A privacy-safe, user-facing drift-check input error."""


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _expect_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DriftInputError(f"{label} must be a JSON object")
    return value


def _expect_exact_keys(
    value: Mapping[str, Any],
    required: frozenset[str],
    label: str,
    *,
    optional: frozenset[str] = frozenset(),
) -> None:
    keys = frozenset(value)
    if not required <= keys:
        raise DriftInputError(f"{label} is missing required audit fields")
    if not keys <= required | optional:
        # Do not echo an unknown key: it may itself contain identifying data.
        raise DriftInputError(f"{label} contains an unapproved audit field")


def _rate_is_consistent(rate: float, records: int, total: int) -> bool:
    expected = round(records / total, 6)
    return math.isclose(rate, expected, rel_tol=0.0, abs_tol=5.000001e-7)


def _validate_identifier(item_id: object, section: str) -> str:
    if not isinstance(item_id, str) or len(item_id) > 64:
        raise DriftInputError(f"{section} contains an invalid identifier")
    if section == "law_rules":
        valid = bool(_LAW_ID.fullmatch(item_id)) and not item_id.startswith("Q-")
    elif section == "questions":
        valid = bool(_QUESTION_ID.fullmatch(item_id))
    else:
        valid = bool(_SLOT_ID.fullmatch(item_id))
    if not valid:
        raise DriftInputError(f"{section} contains an invalid identifier")
    return item_id


def _validate_rows(value: object, section: str, total: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise DriftInputError(f"{section} must be an array")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_row in value:
        row = _expect_object(raw_row, f"{section} row")
        _expect_exact_keys(row, frozenset({"id", "records", "rate"}), f"{section} row")
        item_id = _validate_identifier(row.get("id"), section)
        if item_id in seen:
            raise DriftInputError(f"{section} contains a duplicate identifier: {item_id}")
        seen.add(item_id)
        records = row.get("records")
        rate = row.get("rate")
        if not _is_int(records) or not 0 <= records <= total:
            raise DriftInputError(f"{section}.{item_id}.records is inconsistent")
        if not _is_number(rate) or not 0.0 <= float(rate) <= 1.0:
            raise DriftInputError(f"{section}.{item_id}.rate must be between 0 and 1")
        if not _rate_is_consistent(float(rate), records, total):
            raise DriftInputError(
                f"{section}.{item_id}.rate is inconsistent with records"
            )
        rows.append({"id": item_id, "records": records, "rate": float(rate)})
    identifiers = [row["id"] for row in rows]
    if identifiers != sorted(identifiers):
        raise DriftInputError(f"{section} identifiers must be sorted")
    return rows


def _validate_id_list(
    value: object,
    *,
    label: str,
    section: str,
    expected: list[str],
) -> None:
    if not isinstance(value, list):
        raise DriftInputError(f"{label} must be an array")
    actual = [_validate_identifier(item, section) for item in value]
    if len(actual) != len(set(actual)):
        raise DriftInputError(f"{label} contains a duplicate identifier")
    if actual != expected:
        raise DriftInputError(f"{label} is inconsistent with activation records")


def _validate_summary(
    value: object,
    *,
    total: int,
    law_rows: list[dict[str, Any]],
    question_rows: list[dict[str, Any]],
    missing_rows: list[dict[str, Any]],
) -> None:
    summary = _expect_object(value, "summary")
    _expect_exact_keys(summary, _SUMMARY_KEYS, "summary")

    findings = summary.get("records_with_law_findings")
    findings_rate = summary.get("records_with_law_findings_rate")
    if not _is_int(findings) or not 0 <= findings <= total:
        raise DriftInputError("summary law finding records are inconsistent")
    if not _is_number(findings_rate) or not 0 <= float(findings_rate) <= 1:
        raise DriftInputError("summary law finding rate must be between 0 and 1")
    if not _rate_is_consistent(float(findings_rate), findings, total):
        raise DriftInputError("summary law finding rate is inconsistent with records")
    if law_rows:
        lower_bound = max(int(row["records"]) for row in law_rows)
        upper_bound = min(total, sum(int(row["records"]) for row in law_rows))
        if not lower_bound <= findings <= upper_bound:
            raise DriftInputError("summary law finding records are inconsistent")
    elif findings != 0:
        raise DriftInputError("summary law finding records are inconsistent")

    _validate_id_list(
        summary.get("law_rules_never_observed"),
        label="summary.law_rules_never_observed",
        section="law_rules",
        expected=[row["id"] for row in law_rows if row["records"] == 0],
    )
    _validate_id_list(
        summary.get("questions_never_observed"),
        label="summary.questions_never_observed",
        section="questions",
        expected=[row["id"] for row in question_rows if row["records"] == 0],
    )

    threshold = summary.get("high_frequency_threshold")
    if not _is_number(threshold) or not 0 < float(threshold) <= 1:
        raise DriftInputError("summary.high_frequency_threshold must be in (0, 1]")
    high_questions = _validate_rows(
        summary.get("high_frequency_questions"), "questions", total
    )
    expected_questions = [
        row for row in question_rows if float(row["rate"]) >= float(threshold)
    ]
    if high_questions != expected_questions:
        raise DriftInputError("summary.high_frequency_questions is inconsistent")
    high_missing = _validate_rows(
        summary.get("high_frequency_missing_slots"), "slots_found", total
    )
    expected_missing = [
        row for row in missing_rows if float(row["rate"]) >= float(threshold)
    ]
    if high_missing != expected_missing:
        raise DriftInputError("summary.high_frequency_missing_slots is inconsistent")


def _validate_change_from_baseline(value: object, report: Mapping[str, Any]) -> None:
    change = _expect_object(value, "change_from_baseline")
    _expect_exact_keys(
        change,
        frozenset(
            {
                "baseline_input_sha256",
                "record_delta",
                "version_compatibility",
                "posting_hit_deltas",
            }
        ),
        "change_from_baseline",
    )
    baseline_hash = change.get("baseline_input_sha256")
    if baseline_hash is not None and (
        not isinstance(baseline_hash, str) or not _SHA256.fullmatch(baseline_hash)
    ):
        raise DriftInputError("change_from_baseline contains an invalid digest")
    if not _is_int(change.get("record_delta")):
        raise DriftInputError("change_from_baseline.record_delta must be an integer")

    compatibility = _expect_object(
        change.get("version_compatibility"), "change_from_baseline.version_compatibility"
    )
    _expect_exact_keys(
        compatibility,
        frozenset({"ruleset_version_equal", "matching_version_equal"}),
        "change_from_baseline.version_compatibility",
    )
    if any(not isinstance(value, bool) for value in compatibility.values()):
        raise DriftInputError("change_from_baseline compatibility values must be boolean")

    deltas = _expect_object(
        change.get("posting_hit_deltas"), "change_from_baseline.posting_hit_deltas"
    )
    _expect_exact_keys(deltas, frozenset(_SECTIONS), "change_from_baseline.posting_hit_deltas")
    for section in _SECTIONS:
        section_deltas = _expect_object(
            deltas.get(section), f"change_from_baseline.posting_hit_deltas.{section}"
        )
        allowed_ids = {row["id"] for row in report[section]}
        for item_id, delta in section_deltas.items():
            _validate_identifier(item_id, section)
            if item_id not in allowed_ids or not _is_int(delta):
                raise DriftInputError(
                    f"change_from_baseline.{section} contains inconsistent aggregates"
                )


def validate_audit(value: object, *, label: str = "audit") -> dict[str, Any]:
    """Validate and return a normalized anonymous private fairness audit.

    Unknown fields and source categories are rejected because they are not part
    of the anonymous aggregate contract and could carry posting or organization
    data.  Error messages never include unknown values.
    """
    audit = _expect_object(value, label)
    _expect_exact_keys(
        audit,
        _TOP_LEVEL_REQUIRED,
        label,
        optional=frozenset({"change_from_baseline"}),
    )
    if audit.get("schema_version") != AUDIT_SCHEMA_VERSION:
        raise DriftInputError(f"{label} has an unsupported audit schema_version")

    input_summary = _expect_object(audit.get("input"), f"{label}.input")
    _expect_exact_keys(
        input_summary,
        frozenset({"records", "sha256", "sector", "sources", "split"}),
        f"{label}.input",
        optional=frozenset({"manifest_verified"}),
    )
    records = input_summary.get("records")
    if not _is_int(records) or records <= 0:
        raise DriftInputError(f"{label}.input.records must be a positive integer")
    if input_summary.get("sector") != "private":
        raise DriftInputError(f"{label}.input.sector must be private")
    if input_summary.get("split") != "train_only":
        raise DriftInputError(f"{label}.input.split must be train_only")
    if "manifest_verified" in input_summary and not isinstance(
        input_summary["manifest_verified"], bool
    ):
        raise DriftInputError(f"{label}.input.manifest_verified must be boolean")
    digest = input_summary.get("sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise DriftInputError(f"{label}.input.sha256 must be a lowercase SHA-256 digest")

    sources = _expect_object(input_summary.get("sources"), f"{label}.input.sources")
    if not sources:
        raise DriftInputError(f"{label}.input.sources must not be empty")
    source_total = 0
    for category, count in sources.items():
        if category not in APPROVED_SOURCE_CATEGORIES:
            # Deliberately do not echo a potentially identifying source label.
            raise DriftInputError(f"{label}.input.sources contains an unapproved category")
        if not _is_int(count) or count < 0:
            raise DriftInputError(f"{label}.input.sources contains an invalid count")
        source_total += count
    if source_total != records:
        raise DriftInputError(f"{label}.input.sources counts must sum to input.records")

    law_rows = _validate_rows(audit.get("law_rules"), "law_rules", records)
    question_rows = _validate_rows(audit.get("questions"), "questions", records)
    slot_rows = _validate_rows(audit.get("slots_found"), "slots_found", records)
    missing_rows = _validate_rows(audit.get("slots_missing"), "slots_found", records)
    if [row["id"] for row in slot_rows] != [row["id"] for row in missing_rows]:
        raise DriftInputError("slots_found and slots_missing identifiers must match")
    for found, missing in zip(slot_rows, missing_rows, strict=True):
        if found["records"] + missing["records"] != records:
            raise DriftInputError(
                f"slot aggregate {found['id']} is inconsistent with input.records"
            )

    ruleset_version = audit.get("ruleset_version")
    matching_version = audit.get("matching_version")
    if not isinstance(ruleset_version, str) or not _VERSION.fullmatch(ruleset_version):
        raise DriftInputError(f"{label}.ruleset_version is invalid")
    if not isinstance(matching_version, str) or not _VERSION.fullmatch(matching_version):
        raise DriftInputError(f"{label}.matching_version is invalid")

    privacy = _expect_object(audit.get("privacy_boundary"), f"{label}.privacy_boundary")
    _expect_exact_keys(privacy, _PRIVACY_KEYS, f"{label}.privacy_boundary")
    if any(privacy[key] is not False for key in _PRIVACY_KEYS):
        raise DriftInputError(f"{label} is not an anonymous aggregate audit")

    _validate_summary(
        audit.get("summary"),
        total=records,
        law_rows=law_rows,
        question_rows=question_rows,
        missing_rows=missing_rows,
    )

    normalized: dict[str, Any] = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "input": {
            "records": records,
            "sha256": digest,
            "sector": "private",
            "sources": dict(sorted(sources.items())),
            "split": "train_only",
        },
        "law_rules": law_rows,
        "questions": question_rows,
        "slots_found": slot_rows,
        "slots_missing": missing_rows,
        "summary": audit["summary"],
        "ruleset_version": ruleset_version,
        "matching_version": matching_version,
        "privacy_boundary": dict(privacy),
    }
    if "change_from_baseline" in audit:
        _validate_change_from_baseline(audit["change_from_baseline"], normalized)
    return normalized


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            # The key is intentionally omitted from the message.
            raise DriftInputError("JSON contains a duplicate object field")
        result[key] = value
    return result


def load_audit(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DriftInputError(f"{label} audit could not be read") from exc
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except DriftInputError:
        raise
    except json.JSONDecodeError as exc:
        raise DriftInputError(
            f"{label} audit is malformed JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    return validate_audit(value, label=label)


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False))).casefold()


def reject_disallowed_path(path: Path, *, argument: str) -> None:
    for candidate in (path, path.resolve(strict=False)):
        parts = {part.casefold().rstrip(". ") for part in candidate.parts}
        filename_tokens = set(
            filter(None, re.split(r"[^a-z0-9]+", candidate.name.casefold()))
        )
        if parts & DISALLOWED_PATH_PARTS or filename_tokens & DISALLOWED_PATH_PARTS:
            raise DriftInputError(
                f"{argument} is rejected by the train-only path policy"
            )


def validate_paths(current: Path, baseline: Path, output: Path) -> None:
    """Preflight all paths before either input is opened."""
    for argument, path in (
        ("--current", current),
        ("--baseline", baseline),
        ("--output", output),
    ):
        reject_disallowed_path(path, argument=argument)
    output_key = _path_key(output)
    if output_key in {_path_key(current), _path_key(baseline)}:
        raise DriftInputError("--output must differ from both input paths")
    if output.exists():
        for input_path in (current, baseline):
            if input_path.exists():
                try:
                    if os.path.samefile(output, input_path):
                        raise DriftInputError(
                            "--output must differ from both input paths"
                        )
                except OSError:
                    # A later read/write produces the privacy-safe I/O error.
                    pass
    if output.exists() and output.is_dir():
        raise DriftInputError("--output must be a file path")


def _validate_threshold(value: object, name: str) -> float:
    if not _is_number(value) or not 0 <= float(value) <= 1:
        raise DriftInputError(f"{name} must be between 0 and 1")
    return float(value)


def _row_map(report: Mapping[str, Any], section: str) -> dict[str, dict[str, Any]]:
    return {str(row["id"]): row for row in report[section]}


def _round_rate(value: float) -> float:
    rounded = round(value, 6)
    return 0.0 if rounded == 0 else rounded


def build_drift_report(
    current: object,
    baseline: object,
    *,
    max_record_rate_delta: float = DEFAULT_MAX_RECORD_RATE_DELTA,
    max_source_share_delta: float = DEFAULT_MAX_SOURCE_SHARE_DELTA,
    require_version_match: bool = False,
) -> dict[str, Any]:
    current_report = validate_audit(current, label="current")
    baseline_report = validate_audit(baseline, label="baseline")
    record_threshold = _validate_threshold(
        max_record_rate_delta, "--max-record-rate-delta"
    )
    source_threshold = _validate_threshold(
        max_source_share_delta, "--max-source-share-delta"
    )
    if not isinstance(require_version_match, bool):
        raise DriftInputError("require_version_match must be boolean")

    current_total = int(current_report["input"]["records"])
    baseline_total = int(baseline_report["input"]["records"])
    metric_deltas: dict[str, list[dict[str, Any]]] = {}
    identifier_changes: dict[str, dict[str, list[str]]] = {}
    transitions: dict[str, dict[str, list[str]]] = {
        "zero_to_observed": {section: [] for section in _SECTIONS},
        "observed_to_zero": {section: [] for section in _SECTIONS},
    }
    alerts: list[dict[str, Any]] = []

    for section in _SECTIONS:
        current_rows = _row_map(current_report, section)
        baseline_rows = _row_map(baseline_report, section)
        all_ids = sorted(current_rows.keys() | baseline_rows.keys())
        identifier_changes[section] = {
            "added": sorted(current_rows.keys() - baseline_rows.keys()),
            "removed": sorted(baseline_rows.keys() - current_rows.keys()),
        }
        output_rows: list[dict[str, Any]] = []
        for item_id in all_ids:
            current_row = current_rows.get(
                item_id, {"id": item_id, "records": 0, "rate": 0.0}
            )
            baseline_row = baseline_rows.get(
                item_id, {"id": item_id, "records": 0, "rate": 0.0}
            )
            current_rate_exact = int(current_row["records"]) / current_total
            baseline_rate_exact = int(baseline_row["records"]) / baseline_total
            delta_exact = current_rate_exact - baseline_rate_exact
            absolute_delta_exact = abs(delta_exact)
            if baseline_row["records"] == 0 and current_row["records"] > 0:
                transition = "zero_to_observed"
                transitions[transition][section].append(item_id)
            elif baseline_row["records"] > 0 and current_row["records"] == 0:
                transition = "observed_to_zero"
                transitions[transition][section].append(item_id)
            else:
                transition = "none"
            threshold_exceeded = absolute_delta_exact > record_threshold
            row = {
                "id": item_id,
                "baseline_records": int(baseline_row["records"]),
                "baseline_rate": _round_rate(baseline_rate_exact),
                "current_records": int(current_row["records"]),
                "current_rate": _round_rate(current_rate_exact),
                "rate_delta": _round_rate(delta_exact),
                "absolute_rate_delta": _round_rate(absolute_delta_exact),
                "transition": transition,
                "threshold_exceeded": threshold_exceeded,
            }
            output_rows.append(row)
            reasons: list[str] = []
            if threshold_exceeded:
                reasons.append("rate_delta")
            if transition != "none":
                reasons.append(transition)
            if reasons:
                alerts.append(
                    {
                        "type": "activation_drift",
                        "section": section,
                        "id": item_id,
                        "baseline_rate": row["baseline_rate"],
                        "current_rate": row["current_rate"],
                        "rate_delta": row["rate_delta"],
                        "absolute_rate_delta": row["absolute_rate_delta"],
                        "threshold": record_threshold,
                        "reasons": reasons,
                    }
                )
        metric_deltas[section] = output_rows

    source_rows: list[dict[str, Any]] = []
    current_sources = current_report["input"]["sources"]
    baseline_sources = baseline_report["input"]["sources"]
    for category in sorted(set(current_sources) | set(baseline_sources)):
        current_count = int(current_sources.get(category, 0))
        baseline_count = int(baseline_sources.get(category, 0))
        current_share = current_count / current_total
        baseline_share = baseline_count / baseline_total
        delta = current_share - baseline_share
        exceeded = abs(delta) > source_threshold
        row = {
            "category": category,
            "baseline_records": baseline_count,
            "baseline_share": _round_rate(baseline_share),
            "current_records": current_count,
            "current_share": _round_rate(current_share),
            "share_delta": _round_rate(delta),
            "absolute_share_delta": _round_rate(abs(delta)),
            "threshold_exceeded": exceeded,
        }
        source_rows.append(row)
        if exceeded:
            alerts.append(
                {
                    "type": "source_share_drift",
                    "category": category,
                    "baseline_share": row["baseline_share"],
                    "current_share": row["current_share"],
                    "share_delta": row["share_delta"],
                    "absolute_share_delta": row["absolute_share_delta"],
                    "threshold": source_threshold,
                }
            )

    ruleset_equal = (
        current_report["ruleset_version"] == baseline_report["ruleset_version"]
    )
    matching_equal = (
        current_report["matching_version"] == baseline_report["matching_version"]
    )
    identifier_sets_equal = all(
        not changes["added"] and not changes["removed"]
        for changes in identifier_changes.values()
    )
    if require_version_match:
        if not ruleset_equal:
            alerts.append({"type": "version_mismatch", "version": "ruleset"})
        if not matching_equal:
            alerts.append({"type": "version_mismatch", "version": "matching"})
        if not identifier_sets_equal:
            alerts.append({"type": "version_mismatch", "version": "identifier_sets"})

    metric_alert_count = sum(alert["type"] == "activation_drift" for alert in alerts)
    source_alert_count = sum(alert["type"] == "source_share_drift" for alert in alerts)
    version_alert_count = sum(alert["type"] == "version_mismatch" for alert in alerts)
    status = "alert" if alerts else "ok"
    return {
        "schema_version": DRIFT_SCHEMA_VERSION,
        "status": status,
        "thresholds": {
            "max_record_rate_delta": record_threshold,
            "max_source_share_delta": source_threshold,
            "require_version_match": require_version_match,
        },
        "records": {
            "baseline": baseline_total,
            "current": current_total,
            "delta": current_total - baseline_total,
        },
        "versions": {
            "baseline": {
                "ruleset": baseline_report["ruleset_version"],
                "matching": baseline_report["matching_version"],
            },
            "current": {
                "ruleset": current_report["ruleset_version"],
                "matching": current_report["matching_version"],
            },
            "compatibility": {
                "ruleset_version_equal": ruleset_equal,
                "matching_version_equal": matching_equal,
                "identifier_sets_equal": identifier_sets_equal,
            },
        },
        "identifier_changes": identifier_changes,
        "activation_rate_deltas": metric_deltas,
        "source_share_deltas": source_rows,
        "transitions": transitions,
        "alerts": alerts,
        "summary": {
            "alert_count": len(alerts),
            "activation_alert_count": metric_alert_count,
            "source_alert_count": source_alert_count,
            "version_alert_count": version_alert_count,
            "zero_to_observed_count": sum(
                len(items) for items in transitions["zero_to_observed"].values()
            ),
            "observed_to_zero_count": sum(
                len(items) for items in transitions["observed_to_zero"].values()
            ),
        },
    }


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    staged_path = path.with_name(
        f".{path.name}.fairpost-stage-{uuid4().hex}"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        staged_path.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(staged_path, path)
    except (OSError, UnicodeError) as exc:
        raise DriftInputError("--output report could not be written") from exc
    finally:
        try:
            staged_path.unlink(missing_ok=True)
        except OSError:
            pass


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare two anonymous private train-only fairness audits and emit "
            "a deterministic aggregate drift report."
        )
    )
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--max-record-rate-delta",
        type=float,
        default=DEFAULT_MAX_RECORD_RATE_DELTA,
    )
    parser.add_argument(
        "--max-source-share-delta",
        type=float,
        default=DEFAULT_MAX_SOURCE_SHARE_DELTA,
    )
    parser.add_argument("--require-version-match", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        # Threshold and path checks intentionally precede both reads.
        record_threshold = _validate_threshold(
            args.max_record_rate_delta, "--max-record-rate-delta"
        )
        source_threshold = _validate_threshold(
            args.max_source_share_delta, "--max-source-share-delta"
        )
        validate_paths(args.current, args.baseline, args.output)
        current = load_audit(args.current, label="current")
        baseline = load_audit(args.baseline, label="baseline")
        report = build_drift_report(
            current,
            baseline,
            max_record_rate_delta=record_threshold,
            max_source_share_delta=source_threshold,
            require_version_match=args.require_version_match,
        )
        _write_report(args.output, report)
    except DriftInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # stdout contains aggregates only; paths, hashes and input identifiers are omitted.
    print(
        json.dumps(
            {"alerts": report["summary"]["alert_count"], "status": report["status"]},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 2 if report["status"] == "alert" else 0


if __name__ == "__main__":
    raise SystemExit(main())
