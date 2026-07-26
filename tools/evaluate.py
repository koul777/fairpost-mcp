from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import FairpostEngine  # noqa: E402


HASH_RE = re.compile(r"[0-9a-f]{64}")
TARGETS = {
    "expression_precision": 0.90,
    "absence_recall": 0.85,
    "absence_precision": 0.80,
}
MIN_HOLDOUT_BY_SECTOR = {"public": 90, "private": 90}


def load_jsonl(path: Path) -> list[dict]:
    records = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        record_id = value.get("id")
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError(f"{path}:{line_number}: id가 필요합니다")
        if record_id in seen_ids:
            raise ValueError(f"{path}:{line_number}: 중복 id '{record_id}'")
        seen_ids.add(record_id)
        if not isinstance(value.get("text"), str):
            raise ValueError(f"{path}:{line_number}: text가 필요합니다")
        content_hash = value.get("content_hash")
        if not isinstance(content_hash, str) or not HASH_RE.fullmatch(content_hash):
            raise ValueError(
                f"{path}:{line_number}: content_hash는 SHA-256 64자리여야 합니다"
            )
        actual_hash = hashlib.sha256(value["text"].encode("utf-8")).hexdigest()
        if content_hash != actual_hash:
            raise ValueError(f"{path}:{line_number}: text와 content_hash가 일치하지 않습니다")
        if content_hash in seen_hashes:
            raise ValueError(f"{path}:{line_number}: 중복 content_hash")
        seen_hashes.add(content_hash)
        if not isinstance(value.get("expected_findings"), list):
            raise ValueError(f"{path}:{line_number}: expected_findings 목록이 필요합니다")
        if not isinstance(value.get("expected_absent_slots"), list):
            raise ValueError(
                f"{path}:{line_number}: expected_absent_slots 목록이 필요합니다"
            )
        if not all(isinstance(item, str) for item in value["expected_findings"]):
            raise ValueError(
                f"{path}:{line_number}: expected_findings 값은 문자열이어야 합니다"
            )
        if not all(isinstance(item, str) for item in value["expected_absent_slots"]):
            raise ValueError(
                f"{path}:{line_number}: expected_absent_slots 값은 문자열이어야 합니다"
            )
        expressions = value.get("expected_expressions")
        if not isinstance(expressions, list):
            raise ValueError(f"{path}:{line_number}: expected_expressions는 목록이어야 합니다")
        expression_rule_ids: list[str] = []
        for expression in expressions:
            if not isinstance(expression, dict):
                raise ValueError(
                    f"{path}:{line_number}: expected_expressions 항목은 객체여야 합니다"
                )
            if expression.get("rule_id") is not None and not isinstance(
                expression.get("rule_id"), str
            ):
                raise ValueError(
                    f"{path}:{line_number}: expected_expressions.rule_id 형식 오류"
                )
            if expression.get("rule_id") is not None:
                expression_rule_ids.append(str(expression["rule_id"]))
        expected_findings = list(map(str, value["expected_findings"]))
        expected_absent_slots = list(map(str, value["expected_absent_slots"]))
        if len(expected_findings) != len(set(expected_findings)):
            raise ValueError(f"{path}:{line_number}: expected_findings 중복")
        if len(expected_absent_slots) != len(set(expected_absent_slots)):
            raise ValueError(f"{path}:{line_number}: expected_absent_slots 중복")
        if set(expected_findings) != set(expression_rule_ids):
            raise ValueError(
                f"{path}:{line_number}: expected_findings와 "
                "expected_expressions.rule_id가 일치해야 합니다"
            )
        records.append(value)
    return records


def load_hashes(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("content_hashes")
    if not isinstance(values, list) or not all(
        isinstance(value, str) and HASH_RE.fullmatch(value) for value in values
    ):
        raise ValueError(f"{path}: content_hashes SHA-256 목록이 필요합니다")
    if len(values) != len(set(values)):
        raise ValueError(f"{path}: content_hashes가 중복되었습니다")
    return set(values)


def load_holdout_records(
    path: Path,
    expected_hashes: set[str],
) -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    seen_ids: set[str] = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        record_id = value.get("id")
        content_hash = value.get("content_hash")
        text = value.get("text")
        sector = value.get("sector")
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError(f"{path}:{line_number}: id가 필요합니다")
        if record_id in seen_ids:
            raise ValueError(f"{path}:{line_number}: 중복 id '{record_id}'")
        seen_ids.add(record_id)
        if not isinstance(content_hash, str) or not HASH_RE.fullmatch(content_hash):
            raise ValueError(
                f"{path}:{line_number}: content_hash는 SHA-256 64자리여야 합니다"
            )
        if content_hash in records:
            raise ValueError(f"{path}:{line_number}: 중복 content_hash")
        if not isinstance(text, str):
            raise ValueError(f"{path}:{line_number}: text가 필요합니다")
        actual_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if content_hash != actual_hash:
            raise ValueError(f"{path}:{line_number}: text와 content_hash가 일치하지 않습니다")
        if sector not in MIN_HOLDOUT_BY_SECTOR:
            raise ValueError(
                f"{path}:{line_number}: sector는 public 또는 private이어야 합니다"
            )
        records[content_hash] = {
            "id": record_id,
            "sector": str(sector),
            "source": str(value.get("source") or record_id.partition(":")[0]),
        }
    actual_hashes = set(records)
    missing = expected_hashes - actual_hashes
    extra = actual_hashes - expected_hashes
    if missing or extra:
        raise ValueError(
            f"{path}: manifest와 records 해시가 일치하지 않습니다 "
            f"(누락 {len(missing)}, 초과 {len(extra)})"
        )
    return records


def metrics(tp: int, fp: int, fn: int) -> dict[str, float | int | None]:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": round(precision, 6) if precision is not None else None,
        "recall": round(recall, 6) if recall is not None else None,
    }


def _target_gate(
    report: dict[str, object],
    sector_counts: Counter[str],
) -> dict[str, object]:
    expression = report["expression_detection"]
    absence = report["absence_detection"]
    assert isinstance(expression, dict) and isinstance(absence, dict)
    checks = {
        "complete_holdout": bool(report["complete_holdout"]),
        "public_holdout_at_least_90": (
            sector_counts["public"] >= MIN_HOLDOUT_BY_SECTOR["public"]
        ),
        "private_holdout_at_least_90": (
            sector_counts["private"] >= MIN_HOLDOUT_BY_SECTOR["private"]
        ),
        "expression_precision_defined": expression["precision"] is not None,
        "expression_precision_at_least_0_90": (
            expression["precision"] is not None
            and float(expression["precision"]) >= TARGETS["expression_precision"]
        ),
        "absence_recall_defined": absence["recall"] is not None,
        "absence_recall_at_least_0_85": (
            absence["recall"] is not None
            and float(absence["recall"]) >= TARGETS["absence_recall"]
        ),
        "absence_precision_defined": absence["precision"] is not None,
        "absence_precision_at_least_0_80": (
            absence["precision"] is not None
            and float(absence["precision"]) >= TARGETS["absence_precision"]
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "thresholds": TARGETS,
        "minimum_holdout_by_sector": MIN_HOLDOUT_BY_SECTOR,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="사람이 라벨링한 봉인 홀드아웃에서 표현·부재 탐지 성능을 측정합니다."
    )
    parser.add_argument("annotations", type=Path)
    parser.add_argument(
        "--train-manifest",
        type=Path,
        default=Path(".corpus-prd/train/manifest.json"),
    )
    parser.add_argument(
        "--holdout-manifest",
        type=Path,
        default=Path(".corpus-prd/holdout/manifest.json"),
    )
    parser.add_argument(
        "--holdout-records",
        type=Path,
        help="생략하면 --holdout-manifest와 같은 폴더의 records.jsonl을 사용합니다.",
    )
    parser.add_argument("--output", type=Path, default=Path("reports/evaluation.json"))
    parser.add_argument("--enforce-targets", action="store_true")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="개발 중 일부 홀드아웃만 측정합니다. 목표 통과 판정에는 사용할 수 없습니다.",
    )
    args = parser.parse_args()
    holdout_records_path = (
        args.holdout_records or args.holdout_manifest.with_name("records.jsonl")
    )

    try:
        train_hashes = load_hashes(args.train_manifest)
        holdout_hashes = load_hashes(args.holdout_manifest)
        overlap = train_hashes & holdout_hashes
        if overlap:
            raise ValueError(f"훈련/홀드아웃 해시가 {len(overlap)}개 겹칩니다")
        holdout_records = load_holdout_records(
            holdout_records_path,
            holdout_hashes,
        )
        records = load_jsonl(args.annotations)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc

    annotation_hashes = {str(record["content_hash"]) for record in records}
    if not annotation_hashes <= holdout_hashes:
        raise SystemExit("라벨 데이터에 홀드아웃 manifest 밖의 content_hash가 있습니다")
    if args.enforce_targets and args.allow_partial:
        raise SystemExit("--enforce-targets와 --allow-partial은 함께 사용할 수 없습니다")
    missing_annotations = holdout_hashes - annotation_hashes
    if missing_annotations and not args.allow_partial:
        raise SystemExit(
            "홀드아웃 전체 라벨이 필요합니다: "
            f"{len(missing_annotations)}건의 content_hash가 누락되었습니다"
        )

    engine = FairpostEngine()
    all_finding_rules = {
        rule["id"] for rule in engine.ruleset.rules if rule["layer"] == "law"
    }
    all_slot_ids = set(engine.ruleset.slots)
    for record in records:
        metadata = holdout_records[str(record["content_hash"])]
        if record["id"] != metadata["id"]:
            raise SystemExit(
                f"{record['id']}: content_hash에 연결된 홀드아웃 id "
                f"'{metadata['id']}'와 일치하지 않습니다"
            )
        unknown_findings = set(map(str, record["expected_findings"])) - all_finding_rules
        if unknown_findings:
            raise SystemExit(
                f"{record['id']}: 알 수 없는 expected_findings: "
                f"{', '.join(sorted(unknown_findings))}"
            )
        unknown_slots = set(map(str, record["expected_absent_slots"])) - all_slot_ids
        if unknown_slots:
            raise SystemExit(
                f"{record['id']}: 알 수 없는 expected_absent_slots: "
                f"{', '.join(sorted(unknown_slots))}"
            )
        for expression in record.get("expected_expressions", []):
            rule_id = expression.get("rule_id")
            if rule_id is not None and rule_id not in all_finding_rules:
                raise SystemExit(
                    f"{record['id']}: 알 수 없는 expected_expressions.rule_id '{rule_id}'"
                )

    finding_tp = finding_fp = finding_fn = 0
    absence_tp = absence_fp = absence_fn = 0
    expression_total = expression_covered = 0
    sector_counters: dict[str, dict[str, int]] = {
        sector: {
            "finding_tp": 0,
            "finding_fp": 0,
            "finding_fn": 0,
            "absence_tp": 0,
            "absence_fp": 0,
            "absence_fn": 0,
        }
        for sector in MIN_HOLDOUT_BY_SECTOR
    }
    details = []
    for record in records:
        result = engine.check(record["text"])
        predicted_findings = {item.id for item in result.findings}
        expected_findings = set(map(str, record["expected_findings"]))
        predicted_absence = {item.slot for item in result.slots if not item.found}
        expected_absence = set(map(str, record["expected_absent_slots"]))
        expected_expressions = record.get("expected_expressions", [])
        expression_total += len(expected_expressions)
        expression_covered += sum(
            expression.get("rule_id") in all_finding_rules
            for expression in expected_expressions
        )

        finding_tp += len(predicted_findings & expected_findings)
        finding_fp += len(predicted_findings - expected_findings)
        finding_fn += len(expected_findings - predicted_findings)
        absence_tp += len(predicted_absence & expected_absence)
        absence_fp += len(predicted_absence - expected_absence)
        absence_fn += len(expected_absence - predicted_absence)
        sector = holdout_records[str(record["content_hash"])]["sector"]
        counters = sector_counters[sector]
        counters["finding_tp"] += len(predicted_findings & expected_findings)
        counters["finding_fp"] += len(predicted_findings - expected_findings)
        counters["finding_fn"] += len(expected_findings - predicted_findings)
        counters["absence_tp"] += len(predicted_absence & expected_absence)
        counters["absence_fp"] += len(predicted_absence - expected_absence)
        counters["absence_fn"] += len(expected_absence - predicted_absence)
        details.append(
            {
                "id": record.get("id"),
                "finding_extra": sorted(predicted_findings - expected_findings),
                "finding_missed": sorted(expected_findings - predicted_findings),
                "absence_extra": sorted(predicted_absence - expected_absence),
                "absence_missed": sorted(expected_absence - predicted_absence),
            }
        )

    holdout_sector_counts = Counter(
        metadata["sector"] for metadata in holdout_records.values()
    )
    annotation_sector_counts = Counter(
        holdout_records[str(record["content_hash"])]["sector"] for record in records
    )
    report = {
        "records": len(records),
        "holdout_records": len(holdout_hashes),
        "annotation_coverage": round(
            len(annotation_hashes) / len(holdout_hashes), 6
        )
        if holdout_hashes
        else 1.0,
        "complete_holdout": annotation_hashes == holdout_hashes,
        "holdout_hash_overlap": 0,
        "measurement_units": {
            "expression_detection": "posting_rule_pair",
            "absence_detection": "posting_slot_pair",
            "dictionary_coverage": "human_labeled_expression_occurrence",
        },
        "expression_detection": metrics(finding_tp, finding_fp, finding_fn),
        "absence_detection": metrics(absence_tp, absence_fp, absence_fn),
        "dictionary_coverage": round(
            expression_covered / expression_total, 6
        )
        if expression_total
        else None,
        "dictionary_coverage_expressions": expression_total,
        "holdout_by_sector": dict(sorted(holdout_sector_counts.items())),
        "annotations_by_sector": dict(sorted(annotation_sector_counts.items())),
        "holdout_by_source": dict(
            sorted(
                Counter(
                    metadata["source"] for metadata in holdout_records.values()
                ).items()
            )
        ),
        "sector_metrics": {
            sector: {
                "records": annotation_sector_counts[sector],
                "expression_detection": metrics(
                    counters["finding_tp"],
                    counters["finding_fp"],
                    counters["finding_fn"],
                ),
                "absence_detection": metrics(
                    counters["absence_tp"],
                    counters["absence_fp"],
                    counters["absence_fn"],
                ),
            }
            for sector, counters in sorted(sector_counters.items())
        },
        "details": details,
        "ruleset_version": engine.ruleset.version,
        "matching_version": engine.ruleset.matching_version,
    }
    report["target_gate"] = _target_gate(report, holdout_sector_counts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))

    if args.enforce_targets:
        if not report["target_gate"]["passed"]:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
