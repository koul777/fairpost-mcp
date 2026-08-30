from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import load_ruleset  # noqa: E402


ALLOWED_ROW_KEYS = {
    "case_id",
    "team_id",
    "ruleset_version",
    "matching_version",
    "review_minutes",
    "question_feedback",
    "outcome",
    "disclaimer_understood",
    "local_only_confirmed",
}
ALLOWED_FEEDBACK_KEYS = {"question_id", "result"}
QUESTION_RESULTS = {"actionable", "relevant_no_action", "irrelevant"}
OUTCOMES = {"edited", "confirmed", "escalated", "no_action"}


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
        raise ValueError("파일럿 집계 출력은 피드백 입력을 덮어쓸 수 없습니다")
    if output_path.exists() and output_path.is_dir():
        raise ValueError("파일럿 집계 출력은 파일 경로여야 합니다")


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: JSON 형식 오류") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number}: JSON 객체가 필요합니다")
        unknown = set(row) - ALLOWED_ROW_KEYS
        missing = ALLOWED_ROW_KEYS - set(row)
        if unknown or missing:
            raise ValueError(
                f"{path}:{line_number}: 허용되지 않거나 누락된 필드 "
                f"(unknown={sorted(unknown)}, missing={sorted(missing)})"
            )
        case_id = row["case_id"]
        team_id = row["team_id"]
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"{path}:{line_number}: case_id가 필요합니다")
        if case_id in seen_case_ids:
            raise ValueError(f"{path}:{line_number}: 중복 case_id '{case_id}'")
        seen_case_ids.add(case_id)
        if not isinstance(team_id, str) or not team_id.strip():
            raise ValueError(f"{path}:{line_number}: team_id가 필요합니다")
        minutes = row["review_minutes"]
        if (
            isinstance(minutes, bool)
            or not isinstance(minutes, (int, float))
            or not 0 < float(minutes) <= 240
        ):
            raise ValueError(
                f"{path}:{line_number}: review_minutes는 0 초과 240 이하여야 합니다"
            )
        if row["outcome"] not in OUTCOMES:
            raise ValueError(f"{path}:{line_number}: 알 수 없는 outcome")
        for field in ("disclaimer_understood", "local_only_confirmed"):
            if not isinstance(row[field], bool):
                raise ValueError(f"{path}:{line_number}: {field}는 bool이어야 합니다")
        feedback = row["question_feedback"]
        if not isinstance(feedback, list):
            raise ValueError(f"{path}:{line_number}: question_feedback 목록이 필요합니다")
        seen_question_ids: set[str] = set()
        for item in feedback:
            if not isinstance(item, dict) or set(item) != ALLOWED_FEEDBACK_KEYS:
                raise ValueError(
                    f"{path}:{line_number}: question_feedback 필드 형식 오류"
                )
            question_id = item["question_id"]
            if not isinstance(question_id, str) or not question_id:
                raise ValueError(
                    f"{path}:{line_number}: question_feedback.question_id 형식 오류"
                )
            if question_id in seen_question_ids:
                raise ValueError(
                    f"{path}:{line_number}: 중복 question_id '{question_id}'"
                )
            seen_question_ids.add(question_id)
            if item["result"] not in QUESTION_RESULTS:
                raise ValueError(
                    f"{path}:{line_number}: question_feedback.result 형식 오류"
                )
        rows.append(row)
    if not rows:
        raise ValueError(f"{path}: 파일럿 피드백이 비어 있습니다")
    return rows


def summarize(
    rows: list[dict[str, Any]],
    *,
    min_cases: int,
    min_teams: int,
    max_median_minutes: float,
    min_actionable_case_rate: float,
    max_irrelevant_question_rate: float,
) -> dict[str, Any]:
    ruleset = load_ruleset(ROOT / "data")
    question_ids = {
        rule["id"] for rule in ruleset.rules if rule["layer"] == "question"
    }
    for row in rows:
        if row["ruleset_version"] != ruleset.version:
            raise ValueError(
                f"{row['case_id']}: 현재 규칙셋과 다른 ruleset_version"
            )
        if row["matching_version"] != ruleset.matching_version:
            raise ValueError(
                f"{row['case_id']}: 현재 엔진과 다른 matching_version"
            )
        unknown_questions = {
            item["question_id"] for item in row["question_feedback"]
        } - question_ids
        if unknown_questions:
            raise ValueError(
                f"{row['case_id']}: 알 수 없는 질문 ID: "
                + ", ".join(sorted(unknown_questions))
            )

    team_count = len({str(row["team_id"]) for row in rows})
    review_minutes = [float(row["review_minutes"]) for row in rows]
    outcomes = Counter(str(row["outcome"]) for row in rows)
    per_question: dict[str, Counter[str]] = {}
    total_question_feedback = 0
    irrelevant_question_feedback = 0
    actionable_cases = 0
    non_no_action_cases = 0
    for row in rows:
        results = [str(item["result"]) for item in row["question_feedback"]]
        if "actionable" in results:
            actionable_cases += 1
        if row["outcome"] != "no_action":
            non_no_action_cases += 1
        for item in row["question_feedback"]:
            question_id = str(item["question_id"])
            result = str(item["result"])
            per_question.setdefault(question_id, Counter())[result] += 1
            total_question_feedback += 1
            irrelevant_question_feedback += result == "irrelevant"

    case_count = len(rows)
    actionable_case_rate = actionable_cases / case_count
    irrelevant_question_rate = (
        irrelevant_question_feedback / total_question_feedback
        if total_question_feedback
        else 0.0
    )
    median_minutes = statistics.median(review_minutes)
    checks = {
        "minimum_cases": case_count >= min_cases,
        "minimum_teams": team_count >= min_teams,
        "median_review_minutes": median_minutes <= max_median_minutes,
        "actionable_case_rate": actionable_case_rate >= min_actionable_case_rate,
        "irrelevant_question_rate": (
            irrelevant_question_rate <= max_irrelevant_question_rate
        ),
        "disclaimer_understood": all(
            bool(row["disclaimer_understood"]) for row in rows
        ),
        "local_only_confirmed": all(
            bool(row["local_only_confirmed"]) for row in rows
        ),
    }
    return {
        "schema_version": "fairpost-pilot-summary-v1",
        "status": "pass" if all(checks.values()) else "alert",
        "ruleset_version": ruleset.version,
        "matching_version": ruleset.matching_version,
        "input": {
            "cases": case_count,
            "teams": team_count,
            "question_feedback": total_question_feedback,
        },
        "metrics": {
            "median_review_minutes": round(float(median_minutes), 3),
            "actionable_cases": actionable_cases,
            "actionable_case_rate": round(actionable_case_rate, 6),
            "non_no_action_cases": non_no_action_cases,
            "non_no_action_outcome_rate": round(
                non_no_action_cases / case_count, 6
            ),
            "irrelevant_question_feedback": irrelevant_question_feedback,
            "irrelevant_question_rate": round(irrelevant_question_rate, 6),
            "outcomes": dict(sorted(outcomes.items())),
        },
        "thresholds": {
            "min_cases": min_cases,
            "min_teams": min_teams,
            "max_median_minutes": max_median_minutes,
            "min_actionable_case_rate": min_actionable_case_rate,
            "max_irrelevant_question_rate": max_irrelevant_question_rate,
        },
        "checks": checks,
        "questions": {
            question_id: {
                "shown": sum(counts.values()),
                "actionable": counts["actionable"],
                "relevant_no_action": counts["relevant_no_action"],
                "irrelevant": counts["irrelevant"],
            }
            for question_id, counts in sorted(per_question.items())
        },
        "privacy_boundary": {
            "contains_case_ids": False,
            "contains_team_ids": False,
            "contains_posting_text": False,
            "contains_free_text": False,
            "contains_organization_identifiers": False,
        },
        "limitations": [
            "파일럿 결과는 질문의 사용자 유용성을 측정하며 법령 표현 정밀도나 부재 탐지 재현율이 아닙니다.",
            "actionable_case_rate는 질문 피드백이 actionable인 사례만 세며 전체 outcome은 별도 지표입니다.",
            "입력의 team_id와 case_id는 로컬 가명이어야 하며 집계 보고서에는 기록하지 않습니다.",
            "공고 원문과 자유서술 피드백은 이 입력 계약에 포함할 수 없습니다.",
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
        description="원문 없는 FairPost HR 파일럿 피드백을 익명 집계합니다."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("reports/pilot_summary.json")
    )
    parser.add_argument("--min-cases", type=int, default=20)
    parser.add_argument("--min-teams", type=int, default=3)
    parser.add_argument("--max-median-minutes", type=float, default=10.0)
    parser.add_argument("--min-actionable-case-rate", type=float, default=0.70)
    parser.add_argument("--max-irrelevant-question-rate", type=float, default=0.20)
    args = parser.parse_args(argv)
    if args.min_cases < 1 or args.min_teams < 1:
        parser.error("--min-cases와 --min-teams는 1 이상이어야 합니다")
    if args.max_median_minutes <= 0:
        parser.error("--max-median-minutes는 0보다 커야 합니다")
    for name, value in (
        ("--min-actionable-case-rate", args.min_actionable_case_rate),
        ("--max-irrelevant-question-rate", args.max_irrelevant_question_rate),
    ):
        if not 0 <= value <= 1:
            parser.error(f"{name}는 0 이상 1 이하여야 합니다")
    try:
        validate_output_path(args.input, args.output)
        rows = _load_rows(args.input)
        report = summarize(
            rows,
            min_cases=args.min_cases,
            min_teams=args.min_teams,
            max_median_minutes=args.max_median_minutes,
            min_actionable_case_rate=args.min_actionable_case_rate,
            max_irrelevant_question_rate=args.max_irrelevant_question_rate,
        )
        _atomic_write(
            args.output,
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(report["metrics"], ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
