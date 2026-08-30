from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Sequence


REQUIRED_OCCUPATIONS = {"field", "office", "research", "tech"}


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
        raise ValueError("다양성 감사 출력은 입력 요약을 덮어쓸 수 없습니다")
    if output_path.exists() and output_path.is_dir():
        raise ValueError("다양성 감사 출력은 파일 경로여야 합니다")


def audit(
    summary: dict[str, Any],
    *,
    min_sources: int,
    max_dominant_source_share: float,
    min_non_field_share: float,
    min_research_tech_share: float,
) -> dict[str, Any]:
    sources = summary.get("sources")
    occupations = summary.get("occupations")
    sectors = summary.get("sectors")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("sources 비어 있지 않은 객체가 필요합니다")
    if not isinstance(occupations, dict) or not REQUIRED_OCCUPATIONS <= set(
        occupations
    ):
        raise ValueError("field, office, research, tech occupations가 필요합니다")
    if not isinstance(sectors, dict) or set(sectors) != {"private"}:
        raise ValueError("민간 전용 코퍼스 요약(sectors.private)이 필요합니다")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in sources.values()):
        raise ValueError("sources 건수는 0 이상의 정수여야 합니다")
    if any(
        isinstance(occupations[name], bool)
        or not isinstance(occupations[name], int)
        or occupations[name] < 0
        for name in REQUIRED_OCCUPATIONS
    ):
        raise ValueError("occupations 건수는 0 이상의 정수여야 합니다")
    total = summary.get("total")
    if isinstance(total, bool) or not isinstance(total, int) or total < 1:
        raise ValueError("total은 1 이상의 정수여야 합니다")
    if sum(int(value) for value in sources.values()) != total:
        raise ValueError("sources 합계와 total이 일치해야 합니다")
    if sum(int(occupations[name]) for name in REQUIRED_OCCUPATIONS) != total:
        raise ValueError("occupations 합계와 total이 일치해야 합니다")
    if int(sectors["private"]) != total:
        raise ValueError("sectors.private과 total이 일치해야 합니다")

    positive_sources = {name: count for name, count in sources.items() if count > 0}
    dominant_source_count = max(positive_sources.values())
    dominant_source_share = dominant_source_count / total
    non_field = total - int(occupations["field"])
    non_field_share = non_field / total
    research_tech = int(occupations["research"]) + int(occupations["tech"])
    research_tech_share = research_tech / total
    checks = {
        "minimum_sources": len(positive_sources) >= min_sources,
        "dominant_source_share": (
            dominant_source_share <= max_dominant_source_share
        ),
        "non_field_share": non_field_share >= min_non_field_share,
        "research_tech_share": research_tech_share >= min_research_tech_share,
        "train_holdout_hash_overlap_zero": (
            summary.get("train_holdout_hash_overlap") == 0
        ),
        "raw_postings_not_committed": summary.get("raw_postings_committed") is False,
        "deidentification_declared": bool(summary.get("deidentification")),
    }
    return {
        "schema_version": "private-corpus-diversity-audit-v1",
        "status": "pass" if all(checks.values()) else "alert",
        "release_policy": {
            "blocking": False,
            "applies_to": "v0.3 evidence snapshot",
            "reason": (
                "다양성 경보는 v0.3에서 정보성이다. v1.0 strict release에서는 "
                "별도 준비도 차단 사유로 처리한다."
            ),
        },
        "input": {
            "total": total,
            "source_count": len(positive_sources),
            "sources": dict(sorted(positive_sources.items())),
            "occupations": {
                name: int(occupations[name]) for name in sorted(REQUIRED_OCCUPATIONS)
            },
        },
        "metrics": {
            "dominant_source_share": round(dominant_source_share, 6),
            "non_field_share": round(non_field_share, 6),
            "research_tech_share": round(research_tech_share, 6),
        },
        "thresholds": {
            "min_sources": min_sources,
            "max_dominant_source_share": max_dominant_source_share,
            "min_non_field_share": min_non_field_share,
            "min_research_tech_share": min_research_tech_share,
        },
        "checks": checks,
        "privacy_boundary": {
            "contains_posting_text": False,
            "contains_record_ids": False,
            "contains_organization_identifiers": False,
            "contains_personal_identifiers": False,
        },
        "limitations": [
            "출처 수와 직군 비율은 대표성의 대리 지표이며 시장 전체 일반화를 증명하지 않습니다.",
            "새 출처는 이용약관, API 승인과 비식별화 검토를 통과한 뒤에만 결합해야 합니다.",
            "출처 다양성은 사람 라벨 기반 정밀도ㆍ재현율 평가를 대신하지 않습니다.",
        ],
    }


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="민간 코퍼스의 출처ㆍ직군 다양성 게이트를 익명 집계합니다."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("reports/private_open_corpus_summary.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/corpus_diversity_audit.json"),
    )
    parser.add_argument("--min-sources", type=int, default=2)
    parser.add_argument("--max-dominant-source-share", type=float, default=0.70)
    parser.add_argument("--min-non-field-share", type=float, default=0.35)
    parser.add_argument("--min-research-tech-share", type=float, default=0.10)
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="alert이면 종료 코드 2를 반환합니다.",
    )
    args = parser.parse_args(argv)
    if args.min_sources < 1:
        parser.error("--min-sources는 1 이상이어야 합니다")
    for name, value in (
        ("--max-dominant-source-share", args.max_dominant_source_share),
        ("--min-non-field-share", args.min_non_field_share),
        ("--min-research-tech-share", args.min_research_tech_share),
    ):
        if not 0 <= value <= 1:
            parser.error(f"{name}는 0 이상 1 이하여야 합니다")
    try:
        validate_output_path(args.input, args.output)
        summary = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(summary, dict):
            raise ValueError("입력은 JSON 객체여야 합니다")
        report = audit(
            summary,
            min_sources=args.min_sources,
            max_dominant_source_share=args.max_dominant_source_share,
            min_non_field_share=args.min_non_field_share,
            min_research_tech_share=args.min_research_tech_share,
        )
        _atomic_write(
            args.output,
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(report["metrics"], ensure_ascii=False, sort_keys=True))
    return 2 if args.enforce and report["status"] != "pass" else 0


if __name__ == "__main__":
    raise SystemExit(main())
