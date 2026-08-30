from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import FairpostEngine  # noqa: E402


DEFAULT_INPUT = Path(".corpus-prd/train/records.jsonl")
DEFAULT_OUTPUT = Path("reports/question_relevance_audit.json")
REPEATED_QUESTION_THRESHOLD = 0.95
DEFAULT_REVIEW_SCOPE = "posting"
SLOT_DETAIL_QUESTION_SLOTS = {
    "Q-INFO-001": "appeal_channel",
    "Q-INFO-004": "document_return",
    "Q-PROC-002": "evaluation_criteria",
}
SLOT_DETAIL_QUESTION_IDS = frozenset(SLOT_DETAIL_QUESTION_SLOTS)


def _reject_holdout_path(path: Path) -> None:
    """Reject a holdout-like path before opening it."""

    supplied = str(path).casefold()
    resolved = str(path.resolve(strict=False)).casefold()
    if "holdout" in supplied or "holdout" in resolved:
        raise ValueError(
            "holdout paths are forbidden; question relevance audit accepts "
            "train-only input"
        )


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
        raise ValueError("question relevance output must not overwrite its input")
    if output_path.exists() and output_path.is_dir():
        raise ValueError("question relevance output must be a file path")


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def load_training_records(path: Path) -> list[str]:
    """Load only posting text from a train JSONL file.

    Record and source identifiers are deliberately discarded at this boundary so
    they cannot be copied into the aggregate report.
    """

    _reject_holdout_path(path)
    texts: list[str] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{line_number}: invalid JSON object"
                ) from exc
            if not isinstance(value, dict) or not isinstance(
                value.get("text"),
                str,
            ):
                raise ValueError(
                    f"{path}:{line_number}: a string text field is required"
                )
            texts.append(value["text"])
    return texts


def _review_scope(value: object) -> str:
    scope = getattr(value, "review_scope", DEFAULT_REVIEW_SCOPE)
    if not isinstance(scope, str) or not scope.strip():
        return DEFAULT_REVIEW_SCOPE
    return scope.strip()


def _question_catalog(engine: Any) -> dict[str, str]:
    catalog: dict[str, str] = {}
    ruleset = getattr(engine, "ruleset", None)
    rules = getattr(ruleset, "rules", ())
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("layer") != "question":
            continue
        question_id = rule.get("id")
        if not isinstance(question_id, str) or not question_id:
            raise ValueError("question rule is missing a string id")
        scope = rule.get("review_scope", DEFAULT_REVIEW_SCOPE)
        if not isinstance(scope, str) or not scope.strip():
            scope = DEFAULT_REVIEW_SCOPE
        catalog[question_id] = scope.strip()
    return catalog


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 6)


def build_report(
    posting_texts: Iterable[str],
    *,
    engine: Any | None = None,
    input_sha256: str | None = None,
) -> dict[str, object]:
    checker = engine if engine is not None else FairpostEngine()
    texts = list(posting_texts)
    record_count = len(texts)
    question_scopes = _question_catalog(checker)
    activated_records: Counter[str] = Counter()
    instances_by_scope: Counter[str] = Counter()
    slot_detail_checked_records: Counter[str] = Counter()
    slot_detail_missing_records: Counter[str] = Counter()
    slot_detail_mismatched_records: Counter[str] = Counter()
    question_instances_total = 0

    for text in texts:
        result = checker.check(text)
        seen_in_record: set[str] = set()
        for question in result.questions:
            question_id = getattr(question, "id", None)
            if not isinstance(question_id, str) or not question_id:
                raise ValueError("engine question is missing a string id")
            scope = _review_scope(question)
            # The result is authoritative for emitted questions. This also makes
            # an older Question object without review_scope safely default to
            # posting, as required.
            question_scopes[question_id] = scope
            instances_by_scope[scope] += 1
            question_instances_total += 1
            seen_in_record.add(question_id)
        activated_records.update(seen_in_record)
        slots = getattr(result, "slots", None)
        if slots is not None:
            slots_by_id = {
                getattr(slot, "slot", None): slot
                for slot in slots
                if isinstance(getattr(slot, "slot", None), str)
            }
            for question_id, slot_id in SLOT_DETAIL_QUESTION_SLOTS.items():
                slot = slots_by_id.get(slot_id)
                if slot is None:
                    continue
                slot_detail_checked_records[question_id] += 1
                slot_missing = not bool(getattr(slot, "found", False))
                if slot_missing:
                    slot_detail_missing_records[question_id] += 1
                if slot_missing != (question_id in seen_in_record):
                    slot_detail_mismatched_records[question_id] += 1

    instances_by_scope.setdefault(DEFAULT_REVIEW_SCOPE, 0)
    for scope in question_scopes.values():
        instances_by_scope.setdefault(scope, 0)

    activation_rates = [
        {
            "question_id": question_id,
            "review_scope": question_scopes[question_id],
            "activated_records": activated_records[question_id],
            "activation_rate": _rate(
                activated_records[question_id],
                record_count,
            ),
        }
        for question_id in sorted(question_scopes)
    ]
    repeated_questions = [
        entry
        for entry in activation_rates
        if record_count
        and (
            int(entry["activated_records"]) / record_count
            >= REPEATED_QUESTION_THRESHOLD
        )
    ]
    repeated_by_scope = Counter(
        str(entry["review_scope"]) for entry in repeated_questions
    )

    posting_instances = instances_by_scope[DEFAULT_REVIEW_SCOPE]
    collapsed_instances = question_instances_total - posting_instances
    slot_detail_instances = sum(
        activated_records[question_id]
        for question_id in SLOT_DETAIL_QUESTION_IDS
        if question_scopes.get(question_id, DEFAULT_REVIEW_SCOPE)
        == DEFAULT_REVIEW_SCOPE
    )
    primary_posting_instances = posting_instances - slot_detail_instances
    default_collapsed_instances = (
        question_instances_total - primary_posting_instances
    )
    report: dict[str, object] = {
        "split": "train_only",
        "ruleset_version": checker.ruleset.version,
        "matching_version": checker.ruleset.matching_version,
        "input": {
            "split": "train_only",
            "records": record_count,
            "sha256": input_sha256,
        },
        "record_count": record_count,
        "question_definition_count": len(question_scopes),
        "question_instances_total": question_instances_total,
        "question_instances_by_review_scope": dict(
            sorted(instances_by_scope.items())
        ),
        "question_activation_rates": activation_rates,
        "repeated_questions_at_or_above_95_percent": {
            "threshold": REPEATED_QUESTION_THRESHOLD,
            "count": len(repeated_questions),
            "by_review_scope": dict(sorted(repeated_by_scope.items())),
            "questions": repeated_questions,
        },
        "default_expanded_posting_question_reduction": {
            "baseline_question_instances": question_instances_total,
            "default_expanded_question_instances": posting_instances,
            "collapsed_question_instances": collapsed_instances,
            "reduction_rate": _rate(
                collapsed_instances,
                question_instances_total,
            ),
        },
        "slot_detail_questions": {
            "question_ids": sorted(SLOT_DETAIL_QUESTION_IDS),
            "question_instances": slot_detail_instances,
        },
        "slot_detail_pair_invariants": {
            "checked_pairs": sum(slot_detail_checked_records.values()),
            "mismatched_pairs": sum(
                slot_detail_mismatched_records.values()
            ),
            "pairs": [
                {
                    "question_id": question_id,
                    "slot_id": SLOT_DETAIL_QUESTION_SLOTS[question_id],
                    "checked_records": slot_detail_checked_records[
                        question_id
                    ],
                    "slot_missing_records": slot_detail_missing_records[
                        question_id
                    ],
                    "question_activated_records": activated_records[
                        question_id
                    ],
                    "mismatched_records": slot_detail_mismatched_records[
                        question_id
                    ],
                }
                for question_id in sorted(SLOT_DETAIL_QUESTION_IDS)
            ],
        },
        "question_instances_by_presentation_group": {
            "common": collapsed_instances,
            "posting_primary": primary_posting_instances,
            "posting_slot_detail": slot_detail_instances,
        },
        "default_expanded_primary_question_reduction": {
            "baseline_question_instances": question_instances_total,
            "default_expanded_question_instances": primary_posting_instances,
            "collapsed_question_instances": default_collapsed_instances,
            "reduction_rate": _rate(
                default_collapsed_instances,
                question_instances_total,
            ),
        },
        "contains_posting_text": False,
        "contains_record_ids": False,
        "contains_source_ids": False,
        "contains_organization_identifiers": False,
        "contains_personal_identifiers": False,
    }
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit question relevance on train-only JSONL without retaining "
            "posting text or record/source identifiers."
        )
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        validate_output_path(args.input, args.output)
        records = load_training_records(args.input)
        report = build_report(
            records,
            input_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
        )
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))

    _atomic_write(
        args.output,
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    print(
        "Question relevance audit created: "
        f"{report['record_count']} train records, {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
