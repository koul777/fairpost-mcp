from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sys
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import RuleLoadError, load_ruleset  # noqa: E402


OCCUPATION_CLASSES = {"office", "tech", "research", "field"}


def _required_text(item: dict[str, Any], field: str, context: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}: {field}가 필요합니다")
    return value.strip()


def validate_rejected(path: Path, question_ids: set[str]) -> int:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path}: 최상위 값은 목록이어야 합니다")

    candidates: set[str] = set()
    for index, item in enumerate(payload, start=1):
        context = f"{path.name}[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{context}: 항목은 객체여야 합니다")
        candidate = _required_text(item, "candidate", context)
        if candidate in candidates:
            raise ValueError(f"{context}: 중복 candidate '{candidate}'")
        candidates.add(candidate)
        if item.get("decision") not in {"rejected", "deferred"}:
            raise ValueError(f"{context}: decision은 rejected/deferred여야 합니다")
        hits = item.get("corpus_hits")
        if not isinstance(hits, int) or isinstance(hits, bool) or hits < 0:
            raise ValueError(f"{context}: corpus_hits 오류")
        if item.get("corpus_split") not in {"public", "private", "both"}:
            raise ValueError(f"{context}: corpus_split 오류")
        _required_text(item, "reason", context)
        _required_text(item, "reviewed_by", context)
        reviewed_at = _required_text(item, "reviewed_at", context)
        try:
            date.fromisoformat(reviewed_at)
        except ValueError as exc:
            raise ValueError(f"{context}: reviewed_at 날짜 형식 오류") from exc
        if item["decision"] == "deferred":
            converted_to = _required_text(item, "converted_to", context)
            if converted_to not in question_ids:
                raise ValueError(
                    f"{context}: 존재하지 않는 질문 카드 '{converted_to}'"
                )
    return len(payload)


def load_corpus_hit_report(
    path: Path,
    matching_version: str,
) -> dict[str, int]:
    if not path.exists():
        raise ValueError(f"{path}: 코퍼스 집계 보고서가 없습니다")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("matching_version") != matching_version:
        raise ValueError(f"{path}: 현재 표현 매칭 버전과 일치하지 않습니다")
    hits = report.get("law_rule_posting_hits")
    if not isinstance(hits, dict):
        raise ValueError(f"{path}: law_rule_posting_hits가 필요합니다")
    if any(
        not isinstance(rule_id, str)
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        for rule_id, count in hits.items()
    ):
        raise ValueError(f"{path}: law_rule_posting_hits 형식 오류")
    return hits


def validate_corpus_hits(
    rules: tuple[dict[str, Any], ...],
    matching_version: str,
    public_path: Path,
    private_path: Path,
) -> None:
    reports = {
        "public": load_corpus_hit_report(public_path, matching_version),
        "private": load_corpus_hit_report(private_path, matching_version),
    }
    for rule in rules:
        provenance = rule["provenance"]
        if rule["layer"] != "law":
            continue
        split = provenance.get("corpus_split")
        if split == "both":
            observed = sum(reports[name].get(rule["id"], 0) for name in reports)
        else:
            observed = reports[str(split)].get(rule["id"])
        if observed != provenance.get("corpus_hits"):
            raise ValueError(
                f"{rule['id']}: provenance.corpus_hits와 {split} 코퍼스 집계가 다릅니다"
            )


def validate_corpus_summary(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    occupations = payload.get("occupations")
    if not isinstance(occupations, dict):
        raise ValueError(f"{path}: occupations 집계가 필요합니다")
    observed = set(occupations)
    if observed != OCCUPATION_CLASSES:
        missing = sorted(OCCUPATION_CLASSES - observed)
        unexpected = sorted(observed - OCCUPATION_CLASSES)
        raise ValueError(
            f"{path}: 직군은 office/tech/research/field만 모두 포함해야 합니다 "
            f"(누락={missing}, 허용 외={unexpected})"
        )
    if any(
        not isinstance(count, int)
        or isinstance(count, bool)
        or count < 1
        for count in occupations.values()
    ):
        raise ValueError(f"{path}: 모든 직군 집계는 1 이상이어야 합니다")
    total = payload.get("total")
    if not isinstance(total, int) or sum(occupations.values()) != total:
        raise ValueError(f"{path}: 직군 집계 합계가 total과 다릅니다")


def main() -> int:
    try:
        ruleset = load_ruleset(ROOT / "data")
        question_ids = {
            rule["id"] for rule in ruleset.rules if rule["layer"] == "question"
        }
        rejected_count = validate_rejected(
            ROOT / "data" / "rules" / "rejected.yaml",
            question_ids,
        )
        load_ruleset(
            ROOT / "data",
            ROOT / "data" / "local_rules.example.yaml",
        )
        validate_corpus_hits(
            ruleset.rules,
            ruleset.matching_version,
            ROOT / "reports" / "corpus_rule_coverage.json",
            ROOT / "reports" / "private_open_training_analysis.json",
        )
        for summary_name in (
            "corpus_summary.json",
            "private_open_corpus_summary.json",
            "final_corpus_summary.json",
            "prd_corpus_summary.json",
            "youth_job_corpus_summary.json",
        ):
            validate_corpus_summary(ROOT / "reports" / summary_name)
    except (OSError, ValueError, RuleLoadError, yaml.YAMLError) as exc:
        print(f"데이터 검증 실패: {exc}", file=sys.stderr)
        return 1
    print(
        f"데이터 검증 완료: 규칙 {len(ruleset.rules)}개, "
        f"질문 {len(question_ids)}개, 기각·유보 {rejected_count}개"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
