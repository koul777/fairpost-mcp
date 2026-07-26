from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import FairpostEngine  # noqa: E402


def load_training_records(path: Path) -> list[dict[str, object]]:
    if "holdout" in {part.casefold() for part in path.parts}:
        raise ValueError("코퍼스 분석기는 홀드아웃 파일을 읽을 수 없습니다")

    records: list[dict[str, object]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict) or not isinstance(value.get("text"), str):
            raise ValueError(f"{path}:{line_number}: text 문자열이 필요합니다")
        records.append(value)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "사전 구축용 학습 코퍼스에서 규칙·슬롯 관찰 빈도를 익명 집계합니다. "
            "공고 원문이나 기관 식별자는 보고서에 기록하지 않습니다."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(".corpus/train/records.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/corpus_rule_coverage.json"),
    )
    args = parser.parse_args()

    try:
        records = load_training_records(args.input)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc

    engine = FairpostEngine()
    rule_hits: Counter[str] = Counter()
    question_hits: Counter[str] = Counter()
    slot_hits: Counter[str] = Counter()
    law_dimension_hits: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    sector_counts: Counter[str] = Counter()
    any_law_rule = 0
    multiple_law_rules = 0

    for record in records:
        result = engine.check(str(record["text"]))
        rule_hits.update(finding.id for finding in result.findings)
        dimensions = {finding.dimension for finding in result.findings}
        law_dimension_hits.update(dimensions)
        any_law_rule += bool(result.findings)
        multiple_law_rules += len(result.findings) > 1
        question_hits.update(question.id for question in result.questions)
        slot_hits.update(slot.slot for slot in result.slots if slot.found)
        source_counts.update([str(record.get("source", "unknown"))])
        sector_counts.update([str(record.get("sector", "unknown"))])

    law_ids = sorted(
        rule["id"] for rule in engine.ruleset.rules if rule["layer"] == "law"
    )
    question_ids = sorted(
        rule["id"] for rule in engine.ruleset.rules if rule["layer"] == "question"
    )
    slot_ids = sorted(engine.ruleset.slots)
    report = {
        "input": {
            "records": len(records),
            "sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
            "sources": dict(sorted(source_counts.items())),
            "sectors": dict(sorted(sector_counts.items())),
            "split": "train_only",
        },
        "law_rule_posting_hits": {rule_id: rule_hits[rule_id] for rule_id in law_ids},
        "candidate_summary": {
            "any_law_rule": any_law_rule,
            "multiple_law_rules": multiple_law_rules,
            "by_dimension": dict(sorted(law_dimension_hits.items())),
        },
        "question_posting_hits": {
            rule_id: question_hits[rule_id] for rule_id in question_ids
        },
        "slot_found_posting_hits": {
            slot_id: slot_hits[slot_id] for slot_id in slot_ids
        },
        "ruleset_version": engine.ruleset.version,
        "matching_version": engine.ruleset.matching_version,
        "contains_posting_text": False,
        "contains_organization_identifiers": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"{len(records)}건 집계 완료: 규칙 {len(law_ids)}개, "
        f"슬롯 {len(slot_ids)}개, {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
