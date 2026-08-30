from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_tool():
    path = ROOT / "tools" / "summarize_pilot_feedback.py"
    spec = importlib.util.spec_from_file_location("summarize_pilot_feedback", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_row(module, *, case_id: str = "case-1", team_id: str = "team-a"):
    ruleset = module.load_ruleset(ROOT / "data")
    return {
        "case_id": case_id,
        "team_id": team_id,
        "ruleset_version": ruleset.version,
        "matching_version": ruleset.matching_version,
        "review_minutes": 8,
        "question_feedback": [
            {"question_id": "Q-INFO-001", "result": "actionable"}
        ],
        "outcome": "edited",
        "disclaimer_understood": True,
        "local_only_confirmed": True,
    }


def test_pilot_summary_passes_without_identifiers() -> None:
    module = load_tool()
    rows = [
        valid_row(module, case_id=f"case-{index}", team_id=f"team-{index % 3}")
        for index in range(20)
    ]

    report = module.summarize(
        rows,
        min_cases=20,
        min_teams=3,
        max_median_minutes=10,
        min_actionable_case_rate=0.7,
        max_irrelevant_question_rate=0.2,
    )

    assert report["status"] == "pass"
    assert report["input"] == {"cases": 20, "teams": 3, "question_feedback": 20}
    assert report["metrics"]["actionable_case_rate"] == 1.0
    assert report["metrics"]["irrelevant_question_rate"] == 0.0
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert "case-" not in encoded
    assert "team-" not in encoded
    assert all(value is False for value in report["privacy_boundary"].values())


def test_pilot_summary_alerts_on_quality_and_privacy_attestation() -> None:
    module = load_tool()
    row = valid_row(module)
    row["review_minutes"] = 20
    row["question_feedback"][0]["result"] = "irrelevant"
    row["outcome"] = "no_action"
    row["local_only_confirmed"] = False

    report = module.summarize(
        [row],
        min_cases=1,
        min_teams=1,
        max_median_minutes=10,
        min_actionable_case_rate=0.7,
        max_irrelevant_question_rate=0.2,
    )

    assert report["status"] == "alert"
    assert report["checks"]["median_review_minutes"] is False
    assert report["checks"]["actionable_case_rate"] is False
    assert report["checks"]["irrelevant_question_rate"] is False
    assert report["checks"]["local_only_confirmed"] is False


def test_pilot_input_rejects_free_text_and_unknown_question(tmp_path: Path) -> None:
    module = load_tool()
    row = valid_row(module)
    row["notes"] = "원문이나 조직정보가 들어갈 수 있는 자유서술"
    input_path = tmp_path / "pilot.jsonl"
    input_path.write_text(
        json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="허용되지 않거나 누락된 필드"):
        module._load_rows(input_path)

    row.pop("notes")
    row["question_feedback"][0]["question_id"] = "Q-UNKNOWN"
    with pytest.raises(ValueError, match="알 수 없는 질문 ID"):
        module.summarize(
            [row],
            min_cases=1,
            min_teams=1,
            max_median_minutes=10,
            min_actionable_case_rate=0,
            max_irrelevant_question_rate=1,
        )


def test_pilot_input_rejects_stale_version() -> None:
    module = load_tool()
    row = valid_row(module)
    row["ruleset_version"] = "stale"

    with pytest.raises(ValueError, match="현재 규칙셋과 다른"):
        module.summarize(
            [row],
            min_cases=1,
            min_teams=1,
            max_median_minutes=10,
            min_actionable_case_rate=0,
            max_irrelevant_question_rate=1,
        )


def test_edit_without_question_does_not_inflate_actionable_rate() -> None:
    module = load_tool()
    row = valid_row(module)
    row["question_feedback"] = []
    row["outcome"] = "edited"

    report = module.summarize(
        [row],
        min_cases=1,
        min_teams=1,
        max_median_minutes=10,
        min_actionable_case_rate=0.7,
        max_irrelevant_question_rate=1,
    )

    assert report["metrics"]["actionable_case_rate"] == 0.0
    assert report["metrics"]["non_no_action_outcome_rate"] == 1.0
    assert report["checks"]["actionable_case_rate"] is False


def test_pilot_output_must_not_alias_input(tmp_path: Path) -> None:
    module = load_tool()
    path = tmp_path / "pilot.jsonl"
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="덮어쓸 수 없습니다"):
        module.validate_output_path(path, path)
