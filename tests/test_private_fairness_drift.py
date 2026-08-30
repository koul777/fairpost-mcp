from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from tools import check_private_fairness_drift as drift


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "check_private_fairness_drift.py"


def _row(item_id: str, records: int, total: int) -> dict[str, Any]:
    return {"id": item_id, "records": records, "rate": round(records / total, 6)}


def audit(
    *,
    total: int = 10,
    law: int = 2,
    question: int = 3,
    slot: int = 4,
    sources: dict[str, int] | None = None,
    ruleset: str = "rules-v1",
    matching: str = "match-v1",
) -> dict[str, Any]:
    law_rows = [_row("AGE-001", law, total)]
    question_rows = [_row("Q-INFO-001", question, total)]
    slot_rows = [_row("schedule", slot, total)]
    missing_rows = [_row("schedule", total - slot, total)]
    high_frequency_threshold = 0.8
    return {
        "schema_version": "private-fairness-audit-v1",
        "input": {
            "records": total,
            "sha256": "a" * 64,
            "sector": "private",
            "sources": sources or {"work24": total},
            "split": "train_only",
        },
        "law_rules": law_rows,
        "questions": question_rows,
        "slots_found": slot_rows,
        "slots_missing": missing_rows,
        "summary": {
            "records_with_law_findings": law,
            "records_with_law_findings_rate": round(law / total, 6),
            "law_rules_never_observed": ["AGE-001"] if law == 0 else [],
            "questions_never_observed": ["Q-INFO-001"] if question == 0 else [],
            "high_frequency_questions": (
                question_rows if question / total >= high_frequency_threshold else []
            ),
            "high_frequency_missing_slots": (
                missing_rows
                if (total - slot) / total >= high_frequency_threshold
                else []
            ),
            "high_frequency_threshold": high_frequency_threshold,
        },
        "ruleset_version": ruleset,
        "matching_version": matching,
        "privacy_boundary": {
            "contains_posting_text": False,
            "contains_record_ids": False,
            "contains_source_ids": False,
            "contains_organization_identifiers": False,
            "contains_personal_identifiers": False,
        },
    }


def write_audit(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def run_cli(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *(str(arg) for arg in args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_rate_comparison_is_independent_of_record_count() -> None:
    baseline = audit(total=10, law=2, question=3, slot=4)
    current = audit(total=20, law=4, question=6, slot=8)

    report = drift.build_drift_report(current, baseline)

    assert report["status"] == "ok"
    assert report["records"] == {"baseline": 10, "current": 20, "delta": 10}
    assert report["activation_rate_deltas"]["law_rules"][0] == {
        "id": "AGE-001",
        "baseline_records": 2,
        "baseline_rate": 0.2,
        "current_records": 4,
        "current_rate": 0.2,
        "rate_delta": 0.0,
        "absolute_rate_delta": 0.0,
        "transition": "none",
        "threshold_exceeded": False,
    }


def test_activation_delta_above_threshold_alerts_but_boundary_does_not() -> None:
    baseline = audit(total=100, law=10)
    at_boundary = audit(total=100, law=20)
    above = audit(total=100, law=21)

    boundary_report = drift.build_drift_report(
        at_boundary, baseline, max_record_rate_delta=0.10
    )
    above_report = drift.build_drift_report(
        above, baseline, max_record_rate_delta=0.10
    )

    assert boundary_report["status"] == "ok"
    assert above_report["status"] == "alert"
    alert = above_report["alerts"][0]
    assert alert["type"] == "activation_drift"
    assert alert["rate_delta"] == 0.11
    assert alert["reasons"] == ["rate_delta"]


def test_zero_observation_transitions_alert_even_below_rate_threshold() -> None:
    baseline = audit(total=100, law=0, question=1, slot=20)
    current = audit(total=100, law=1, question=0, slot=20)

    report = drift.build_drift_report(
        current, baseline, max_record_rate_delta=0.50
    )

    assert report["status"] == "alert"
    assert report["transitions"]["zero_to_observed"]["law_rules"] == ["AGE-001"]
    assert report["transitions"]["observed_to_zero"]["questions"] == [
        "Q-INFO-001"
    ]
    assert report["summary"]["zero_to_observed_count"] == 1
    assert report["summary"]["observed_to_zero_count"] == 1


def test_source_share_delta_uses_share_and_strict_maximum() -> None:
    baseline = audit(
        total=10,
        sources={"licensed-feed": 5, "work24": 5},
    )
    at_boundary = audit(
        total=20,
        sources={"licensed-feed": 6, "work24": 14},
    )
    above = audit(
        total=20,
        sources={"licensed-feed": 5, "work24": 15},
    )

    boundary_report = drift.build_drift_report(
        at_boundary, baseline, max_source_share_delta=0.20
    )
    above_report = drift.build_drift_report(
        above, baseline, max_source_share_delta=0.20
    )

    assert boundary_report["summary"]["source_alert_count"] == 0
    assert above_report["summary"]["source_alert_count"] == 2
    assert [row["category"] for row in above_report["source_share_deltas"]] == [
        "licensed-feed",
        "work24",
    ]


def test_version_mismatch_is_reported_and_optionally_alerted() -> None:
    baseline = audit(ruleset="rules-v1", matching="match-v1")
    current = audit(ruleset="rules-v2", matching="match-v2")

    advisory = drift.build_drift_report(current, baseline)
    required = drift.build_drift_report(
        current, baseline, require_version_match=True
    )

    assert advisory["status"] == "ok"
    assert advisory["versions"]["compatibility"] == {
        "ruleset_version_equal": False,
        "matching_version_equal": False,
        "identifier_sets_equal": True,
    }
    assert required["status"] == "alert"
    assert required["summary"]["version_alert_count"] == 2
    assert [item["version"] for item in required["alerts"]] == [
        "ruleset",
        "matching",
    ]


def test_identifier_set_changes_are_deterministic_version_compatibility() -> None:
    baseline = audit()
    current = audit()
    current["questions"].append(_row("Q-PROC-001", 0, 10))
    current["summary"]["questions_never_observed"].append("Q-PROC-001")

    report = drift.build_drift_report(
        current, baseline, require_version_match=True
    )

    assert report["identifier_changes"]["questions"] == {
        "added": ["Q-PROC-001"],
        "removed": [],
    }
    assert report["alerts"] == [
        {"type": "version_mismatch", "version": "identifier_sets"}
    ]


def test_added_nonzero_identifier_alerts_without_version_match_gate() -> None:
    baseline = audit()
    current = audit()
    current["law_rules"].append(_row("PHOTO-001", 1, 10))

    report = drift.build_drift_report(current, baseline)

    assert report["status"] == "alert"
    assert report["identifier_changes"]["law_rules"] == {
        "added": ["PHOTO-001"],
        "removed": [],
    }
    photo = next(
        row
        for row in report["activation_rate_deltas"]["law_rules"]
        if row["id"] == "PHOTO-001"
    )
    assert photo["baseline_records"] == 0
    assert photo["current_records"] == 1
    assert photo["transition"] == "zero_to_observed"
    assert any(
        alert.get("id") == "PHOTO-001"
        and alert["reasons"] == ["zero_to_observed"]
        for alert in report["alerts"]
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(schema_version="wrong"), "schema_version"),
        (lambda value: value["input"].update(sector="public"), "sector"),
        (lambda value: value["input"].update(split="holdout"), "train_only"),
        (lambda value: value["input"].update(records=0), "positive integer"),
        (
            lambda value: value["privacy_boundary"].update(
                contains_personal_identifiers=True
            ),
            "anonymous",
        ),
    ],
)
def test_strict_audit_contract_rejects_wrong_schema_scope_or_privacy(
    mutation: Any, message: str
) -> None:
    value = audit()
    mutation(value)

    with pytest.raises(drift.DriftInputError, match=message):
        drift.validate_audit(value)


def test_manifest_verified_is_an_optional_boolean_not_a_drift_signal() -> None:
    baseline = audit()
    current = audit()
    baseline["input"]["manifest_verified"] = False
    current["input"]["manifest_verified"] = True

    report = drift.build_drift_report(current, baseline)

    assert report["status"] == "ok"
    invalid = audit()
    invalid["input"]["manifest_verified"] = "yes"
    with pytest.raises(drift.DriftInputError, match="must be boolean"):
        drift.validate_audit(invalid)


def test_duplicate_rule_identifier_is_rejected() -> None:
    value = audit()
    value["law_rules"].append(copy.deepcopy(value["law_rules"][0]))

    with pytest.raises(drift.DriftInputError, match="duplicate identifier"):
        drift.validate_audit(value)


@pytest.mark.parametrize(
    "rate",
    [-0.01, 1.01, float("inf"), float("nan")],
)
def test_rate_must_be_finite_and_between_zero_and_one(rate: float) -> None:
    value = audit()
    value["law_rules"][0]["rate"] = rate

    with pytest.raises(drift.DriftInputError, match="between 0 and 1"):
        drift.validate_audit(value)


def test_rate_and_record_count_must_be_consistent() -> None:
    value = audit()
    value["law_rules"][0]["rate"] = 0.3

    with pytest.raises(drift.DriftInputError, match="inconsistent with records"):
        drift.validate_audit(value)

    value = audit()
    value["law_rules"][0]["records"] = 11
    with pytest.raises(drift.DriftInputError, match="records is inconsistent"):
        drift.validate_audit(value)


def test_sources_must_be_approved_aggregates_and_sum_to_records() -> None:
    identifying_category = "https://private.example/organization/secret-id"
    value = audit(sources={identifying_category: 10})

    with pytest.raises(drift.DriftInputError) as caught:
        drift.validate_audit(value)
    assert identifying_category not in str(caught.value)

    value = audit(sources={"work24": 9})
    with pytest.raises(drift.DriftInputError, match="sum to input.records"):
        drift.validate_audit(value)


def test_slot_found_and_missing_counts_must_be_complements() -> None:
    value = audit()
    value["slots_missing"] = [_row("schedule", 5, 10)]

    with pytest.raises(drift.DriftInputError, match="slot aggregate"):
        drift.validate_audit(value)


def test_unapproved_field_is_rejected_without_echoing_its_value() -> None:
    value = audit()
    secret = "person@example.test / raw posting text / record-123"
    value["raw_posting"] = secret

    with pytest.raises(drift.DriftInputError) as caught:
        drift.validate_audit(value)
    assert secret not in str(caught.value)
    assert "unapproved audit field" in str(caught.value)


def test_duplicate_json_object_field_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "audit.json"
    path.write_text('{"schema_version":"a","schema_version":"b"}', encoding="utf-8")

    with pytest.raises(drift.DriftInputError, match="duplicate object field"):
        drift.load_audit(path, label="current")


@pytest.mark.parametrize(
    "part",
    ["holdout", "hold-out", "test", "tests", "dev", "evaluation", "eval"],
)
def test_non_train_path_is_rejected_before_file_read(
    tmp_path: Path, part: str
) -> None:
    forbidden = tmp_path / part / "audit.json"
    completed = run_cli(
        "--current",
        forbidden,
        "--baseline",
        tmp_path / "baseline.json",
        "--output",
        tmp_path / "out.json",
    )

    assert completed.returncode == 1
    assert "train-only path policy" in completed.stderr
    assert "could not be read" not in completed.stderr


def test_non_train_filename_token_is_also_rejected_before_read(tmp_path: Path) -> None:
    completed = run_cli(
        "--current",
        tmp_path / "current-test.json",
        "--baseline",
        tmp_path / "baseline.json",
        "--output",
        tmp_path / "out.json",
    )

    assert completed.returncode == 1
    assert "train-only path policy" in completed.stderr
    assert "could not be read" not in completed.stderr


@pytest.mark.parametrize("input_name", ["current", "baseline"])
def test_output_input_collision_is_rejected_before_read(
    tmp_path: Path, input_name: str
) -> None:
    current = tmp_path / "current.json"
    baseline = tmp_path / "baseline.json"
    output = current if input_name == "current" else baseline

    completed = run_cli(
        "--current",
        current,
        "--baseline",
        baseline,
        "--output",
        output,
    )

    assert completed.returncode == 1
    assert "must differ" in completed.stderr
    assert "could not be read" not in completed.stderr


def test_cli_no_alert_writes_report_and_exits_zero(tmp_path: Path) -> None:
    current_path = tmp_path / "current.json"
    baseline_path = tmp_path / "baseline.json"
    output_path = tmp_path / "drift.json"
    write_audit(current_path, audit(total=20, law=4, question=6, slot=8))
    write_audit(baseline_path, audit(total=10, law=2, question=3, slot=4))

    completed = run_cli(
        "--current",
        current_path,
        "--baseline",
        baseline_path,
        "--output",
        output_path,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"alerts": 0, "status": "ok"}
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["status"] == "ok"
    encoded = output_path.read_text(encoding="utf-8")
    assert "a" * 64 not in encoded
    assert str(current_path) not in encoded
    assert "privacy_boundary" not in encoded


def test_cli_alert_writes_report_and_exits_two(tmp_path: Path) -> None:
    current_path = tmp_path / "current.json"
    baseline_path = tmp_path / "baseline.json"
    output_path = tmp_path / "drift.json"
    write_audit(current_path, audit(total=100, law=30))
    write_audit(baseline_path, audit(total=100, law=10))

    completed = run_cli(
        "--current",
        current_path,
        "--baseline",
        baseline_path,
        "--output",
        output_path,
    )

    assert completed.returncode == 2, completed.stderr
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["status"] == "alert"
    assert report["summary"]["activation_alert_count"] == 1


def test_cli_version_flag_controls_exit_code(tmp_path: Path) -> None:
    current_path = tmp_path / "current.json"
    baseline_path = tmp_path / "baseline.json"
    output_path = tmp_path / "drift.json"
    write_audit(current_path, audit(ruleset="rules-v2"))
    write_audit(baseline_path, audit(ruleset="rules-v1"))

    completed = run_cli(
        "--current",
        current_path,
        "--baseline",
        baseline_path,
        "--output",
        output_path,
        "--require-version-match",
    )

    assert completed.returncode == 2
    assert json.loads(output_path.read_text(encoding="utf-8"))["summary"][
        "version_alert_count"
    ] == 1


def test_cli_malformed_input_exits_one_without_output(tmp_path: Path) -> None:
    current_path = tmp_path / "current.json"
    baseline_path = tmp_path / "baseline.json"
    output_path = tmp_path / "drift.json"
    current_path.write_text("{not json", encoding="utf-8")
    write_audit(baseline_path, audit())

    completed = run_cli(
        "--current",
        current_path,
        "--baseline",
        baseline_path,
        "--output",
        output_path,
    )

    assert completed.returncode == 1
    assert "malformed JSON" in completed.stderr
    assert not output_path.exists()


def test_report_publish_failure_preserves_previous_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "drift.json"
    output_path.write_text("previous-report", encoding="utf-8")

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("simulated publish failure")

    monkeypatch.setattr(drift.os, "replace", fail_replace)

    with pytest.raises(drift.DriftInputError, match="could not be written"):
        drift._write_report(output_path, {"status": "ok"})

    assert output_path.read_text(encoding="utf-8") == "previous-report"
    assert not list(tmp_path.glob(".drift.json.fairpost-stage-*"))


@pytest.mark.parametrize("threshold", ["-0.01", "1.01", "nan", "inf"])
def test_cli_invalid_threshold_is_rejected_before_reads(
    tmp_path: Path, threshold: str
) -> None:
    completed = run_cli(
        "--current",
        tmp_path / "missing-current.json",
        "--baseline",
        tmp_path / "missing-baseline.json",
        "--output",
        tmp_path / "drift.json",
        "--max-record-rate-delta",
        threshold,
    )

    assert completed.returncode == 1
    assert "between 0 and 1" in completed.stderr
    assert "could not be read" not in completed.stderr


def test_report_is_deterministic() -> None:
    baseline = audit(
        total=10,
        law=0,
        sources={"work24": 5, "licensed-feed": 5},
    )
    current = audit(
        total=20,
        law=1,
        sources={"work24": 12, "licensed-feed": 8},
    )

    first = drift.build_drift_report(current, baseline)
    second = drift.build_drift_report(copy.deepcopy(current), copy.deepcopy(baseline))

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_committed_private_audit_is_accepted() -> None:
    value = json.loads(
        (ROOT / "reports" / "private_fairness_audit.json").read_text(
            encoding="utf-8"
        )
    )

    report = drift.build_drift_report(value, value)

    assert report["status"] == "ok"
    assert report["summary"]["alert_count"] == 0
