from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import FairpostEngine  # noqa: E402


DEFAULT_HIGH_FREQUENCY_THRESHOLD = 0.80
APPROVED_SOURCE_CATEGORIES = frozenset(
    {
        "jincheon-jobs",
        "work24",
        "senior-job",
        "company-career-page",
        "licensed-feed",
    }
)


def _write_report(path: Path, report: dict[str, Any]) -> None:
    staged_path = path.with_name(f".{path.name}.fairpost-stage-{uuid4().hex}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        staged_path.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(staged_path, path)
    except (OSError, UnicodeError) as exc:
        raise ValueError("audit report could not be written") from exc
    finally:
        try:
            staged_path.unlink(missing_ok=True)
        except OSError:
            pass


NON_TRAIN_PARTS = frozenset(
    {"holdout", "hold-out", "test", "tests", "dev", "evaluation", "eval"}
)
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def _validate_train_path(path: Path) -> None:
    for candidate in (path, path.resolve(strict=False)):
        parts = {part.casefold() for part in candidate.parts}
        if parts & NON_TRAIN_PARTS:
            raise ValueError("민간 감사 도구는 train 경로만 읽을 수 있습니다")
        if "train" not in parts:
            raise ValueError("민간 감사 입력 경로에 train 분할이 필요합니다")


def _validate_non_evaluation_path(path: Path, *, argument: str) -> None:
    for candidate in (path, path.resolve(strict=False)):
        parts = {part.casefold() for part in candidate.parts}
        if parts & NON_TRAIN_PARTS:
            raise ValueError(f"{argument} 경로에는 비학습 분할 이름을 사용할 수 없습니다")


def load_private_training_records(path: Path) -> tuple[bytes, list[dict[str, Any]]]:
    _validate_train_path(path)

    raw = path.read_bytes()
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: JSON 형식 오류") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: JSON 객체가 필요합니다")
        if not isinstance(value.get("text"), str) or not value["text"].strip():
            raise ValueError(f"{path}:{line_number}: 비어 있지 않은 text 문자열이 필요합니다")
        if value.get("sector") != "private":
            raise ValueError(
                f"{path}:{line_number}: sector=private 레코드만 허용합니다"
            )
        if "split" in value and value.get("split") != "train":
            raise ValueError(
                f"{path}:{line_number}: split이 있으면 train이어야 합니다"
            )
        source = value.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"{path}:{line_number}: source 문자열이 필요합니다")
        records.append(value)
    if not records:
        raise ValueError(f"{path}: 민간 학습 레코드가 없습니다")
    return raw, records


def validate_snapshot_manifest(
    path: Path,
    raw: bytes,
    records: list[dict[str, Any]],
) -> None:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: manifest JSON 형식 오류") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"{path}: manifest JSON 객체가 필요합니다")
    if manifest.get("schema_version") != 1:
        raise ValueError(f"{path}: manifest schema_version은 1이어야 합니다")
    if manifest.get("split") != "train" or manifest.get("sector") != "private":
        raise ValueError(f"{path}: private train manifest가 필요합니다")
    if manifest.get("count") != len(records):
        raise ValueError(f"{path}: manifest count가 records와 일치하지 않습니다")
    expected_sha256 = hashlib.sha256(raw).hexdigest()
    if manifest.get("records_sha256") != expected_sha256:
        raise ValueError(f"{path}: records_sha256가 입력과 일치하지 않습니다")

    ids = [record.get("id") for record in records]
    if not all(isinstance(item, str) and SHA256_RE.fullmatch(item) for item in ids):
        raise ValueError("manifest 검증 입력에는 SHA-256 record id가 필요합니다")
    if len(ids) != len(set(ids)) or manifest.get("ids") != ids:
        raise ValueError(f"{path}: manifest ids가 records와 일치하지 않습니다")

    content_hashes = [record.get("content_hash") for record in records]
    if not all(
        isinstance(item, str) and SHA256_RE.fullmatch(item)
        for item in content_hashes
    ):
        raise ValueError("manifest 검증 입력에는 SHA-256 content_hash가 필요합니다")
    expected_hashes = sorted(content_hashes)
    if len(expected_hashes) != len(set(expected_hashes)) or manifest.get(
        "content_hashes"
    ) != expected_hashes:
        raise ValueError(
            f"{path}: manifest content_hashes가 records와 일치하지 않습니다"
        )

    source_counts = Counter(str(record["source"]) for record in records)
    expected_sources = {key: source_counts[key] for key in sorted(source_counts)}
    if manifest.get("source_categories") != expected_sources:
        raise ValueError(
            f"{path}: manifest source_categories가 records와 일치하지 않습니다"
        )


def _rate(count: int, total: int) -> float:
    return round(count / total, 6)


def _activation_rows(
    ids: list[str], counts: Counter[str], total: int
) -> list[dict[str, int | float | str]]:
    return [
        {
            "id": item_id,
            "records": counts[item_id],
            "rate": _rate(counts[item_id], total),
        }
        for item_id in ids
    ]


def _missing_slot_rows(
    ids: list[str], found_counts: Counter[str], total: int
) -> list[dict[str, int | float | str]]:
    return [
        {
            "id": item_id,
            "records": total - found_counts[item_id],
            "rate": _rate(total - found_counts[item_id], total),
        }
        for item_id in ids
    ]


def _counts_from_report(
    report: dict[str, Any],
    *,
    section: str,
    legacy_section: str,
) -> dict[str, int]:
    value = report.get(section)
    if isinstance(value, list):
        counts: dict[str, int] = {}
        for row in value:
            if not isinstance(row, dict):
                raise ValueError(f"baseline.{section} 항목 형식 오류")
            item_id = row.get("id")
            records = row.get("records")
            if (
                not isinstance(item_id, str)
                or not item_id
                or not isinstance(records, int)
                or isinstance(records, bool)
                or records < 0
            ):
                raise ValueError(f"baseline.{section} 항목 형식 오류")
            if item_id in counts:
                raise ValueError(f"baseline.{section} ID 중복: {item_id}")
            counts[item_id] = records
        return counts

    legacy = report.get(legacy_section)
    if isinstance(legacy, dict) and all(
        isinstance(key, str)
        and bool(key)
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 0
        for key, count in legacy.items()
    ):
        return dict(legacy)
    raise ValueError(
        f"baseline에 {section} 또는 {legacy_section} 집계가 필요합니다"
    )


def _baseline_delta(
    baseline: dict[str, Any],
    *,
    report: dict[str, Any],
) -> dict[str, Any]:
    current_input = report["input"]
    assert isinstance(current_input, dict)
    current_records = int(current_input["records"])
    baseline_input = baseline.get("input")
    if not isinstance(baseline_input, dict) or not isinstance(
        baseline_input.get("records"), int
    ):
        raise ValueError("baseline.input.records 정수가 필요합니다")
    if baseline_input.get("split") != "train_only":
        raise ValueError("baseline.input.split은 train_only여야 합니다")
    baseline_sector = baseline_input.get("sector")
    baseline_sectors = baseline_input.get("sectors")
    if baseline_sector != "private" and baseline_sectors != {
        "private": int(baseline_input["records"])
    }:
        raise ValueError("baseline은 private 전용 집계여야 합니다")

    mappings = (
        ("law_rules", "law_rule_posting_hits"),
        ("questions", "question_posting_hits"),
        ("slots_found", "slot_found_posting_hits"),
    )
    deltas: dict[str, dict[str, int]] = {}
    for section, legacy_section in mappings:
        current = _counts_from_report(
            report,
            section=section,
            legacy_section=legacy_section,
        )
        previous = _counts_from_report(
            baseline,
            section=section,
            legacy_section=legacy_section,
        )
        deltas[section] = {
            item_id: current.get(item_id, 0) - previous.get(item_id, 0)
            for item_id in sorted(set(current) | set(previous))
        }

    return {
        "baseline_input_sha256": baseline_input.get("sha256"),
        "record_delta": current_records - int(baseline_input["records"]),
        "version_compatibility": {
            "ruleset_version_equal": baseline.get("ruleset_version")
            == report.get("ruleset_version"),
            "matching_version_equal": baseline.get("matching_version")
            == report.get("matching_version"),
        },
        "posting_hit_deltas": deltas,
    }


def build_report(
    raw: bytes,
    records: list[dict[str, Any]],
    *,
    high_frequency_threshold: float,
    baseline: dict[str, Any] | None = None,
    manifest_verified: bool = False,
) -> dict[str, Any]:
    if not 0 < high_frequency_threshold <= 1:
        raise ValueError("high_frequency_threshold는 0 초과 1 이하여야 합니다")

    engine = FairpostEngine()
    law_ids = sorted(
        rule["id"] for rule in engine.ruleset.rules if rule["layer"] == "law"
    )
    question_ids = sorted(
        rule["id"] for rule in engine.ruleset.rules if rule["layer"] == "question"
    )
    slot_ids = sorted(engine.ruleset.slots)
    law_counts: Counter[str] = Counter()
    question_counts: Counter[str] = Counter()
    slot_found_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    records_with_law_findings = 0

    for record in records:
        result = engine.check(str(record["text"]))
        law_counts.update(finding.id for finding in result.findings)
        question_counts.update(question.id for question in result.questions)
        slot_found_counts.update(slot.slot for slot in result.slots if slot.found)
        source = str(record["source"])
        source_counts.update(
            [source if source in APPROVED_SOURCE_CATEGORIES else "other"]
        )
        records_with_law_findings += bool(result.findings)

    total = len(records)
    law_rows = _activation_rows(law_ids, law_counts, total)
    question_rows = _activation_rows(question_ids, question_counts, total)
    slot_rows = _activation_rows(slot_ids, slot_found_counts, total)
    missing_slot_rows = _missing_slot_rows(slot_ids, slot_found_counts, total)

    report: dict[str, Any] = {
        "schema_version": "private-fairness-audit-v1",
        "input": {
            "records": total,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "sector": "private",
            "sources": dict(sorted(source_counts.items())),
            "split": "train_only",
            "manifest_verified": manifest_verified,
        },
        "law_rules": law_rows,
        "questions": question_rows,
        "slots_found": slot_rows,
        "slots_missing": missing_slot_rows,
        "summary": {
            "records_with_law_findings": records_with_law_findings,
            "records_with_law_findings_rate": _rate(
                records_with_law_findings, total
            ),
            "law_rules_never_observed": [
                row["id"] for row in law_rows if row["records"] == 0
            ],
            "questions_never_observed": [
                row["id"] for row in question_rows if row["records"] == 0
            ],
            "high_frequency_questions": [
                row
                for row in question_rows
                if float(row["rate"]) >= high_frequency_threshold
            ],
            "high_frequency_missing_slots": [
                row
                for row in missing_slot_rows
                if float(row["rate"]) >= high_frequency_threshold
            ],
            "high_frequency_threshold": high_frequency_threshold,
        },
        "ruleset_version": engine.ruleset.version,
        "matching_version": engine.ruleset.matching_version,
        "privacy_boundary": {
            "contains_posting_text": False,
            "contains_record_ids": False,
            "contains_source_ids": False,
            "contains_organization_identifiers": False,
            "contains_personal_identifiers": False,
        },
    }
    if baseline is not None:
        report["change_from_baseline"] = _baseline_delta(
            baseline,
            report=report,
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "민간 train 코퍼스의 공정성 규칙·질문·슬롯 발동률을 원문 없이 "
            "반복 감사합니다."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(".corpus-private-open/train/records.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/private_fairness_audit.json"),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        help="이전 감사 JSON 또는 analyze_corpus.py 익명 집계 JSON",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="신규 private monitoring snapshot manifest 무결성 검증",
    )
    parser.add_argument(
        "--high-frequency-threshold",
        type=float,
        default=DEFAULT_HIGH_FREQUENCY_THRESHOLD,
    )
    args = parser.parse_args()

    try:
        _validate_non_evaluation_path(args.output, argument="--output")
        if args.baseline is not None:
            _validate_non_evaluation_path(args.baseline, argument="--baseline")
        if args.manifest is not None:
            _validate_non_evaluation_path(args.manifest, argument="--manifest")
        resolved_input = str(args.input.resolve(strict=False)).casefold()
        resolved_output = str(args.output.resolve(strict=False)).casefold()
        if resolved_input == resolved_output:
            raise ValueError("--input과 --output은 달라야 합니다")
        if args.baseline is not None and (
            str(args.baseline.resolve(strict=False)).casefold() == resolved_output
        ):
            raise ValueError("--baseline과 --output은 달라야 합니다")
        if args.manifest is not None and (
            str(args.manifest.resolve(strict=False)).casefold() == resolved_output
        ):
            raise ValueError("--manifest와 --output은 달라야 합니다")
    except (OSError, UnicodeError, ValueError) as exc:
        raise SystemExit("경로 사전 검사 실패") from exc

    try:
        raw, records = load_private_training_records(args.input)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(
            "민간 train 입력을 읽거나 검증할 수 없습니다"
        ) from exc

    if args.manifest is not None:
        try:
            validate_snapshot_manifest(args.manifest, raw, records)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise SystemExit(
                "snapshot manifest를 읽거나 검증할 수 없습니다"
            ) from exc

    baseline = None
    if args.baseline is not None:
        try:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            if not isinstance(baseline, dict):
                raise ValueError("baseline object required")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise SystemExit(
                "기준선을 읽거나 검증할 수 없습니다"
            ) from exc

    try:
        report = build_report(
            raw,
            records,
            high_frequency_threshold=args.high_frequency_threshold,
            baseline=baseline,
            manifest_verified=args.manifest is not None,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit("공정성 감사 집계에 실패했습니다") from exc

    try:
        _write_report(args.output, report)
    except (OSError, UnicodeError, ValueError) as exc:
        raise SystemExit("감사 보고서를 쓸 수 없습니다") from exc
    print(f"민간 train {report['input']['records']}건 감사 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
