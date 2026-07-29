from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_tool():
    spec = importlib.util.spec_from_file_location(
        "audit_question_relevance",
        ROOT / "tools" / "audit_question_relevance.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MISSING = object()


def question(question_id: str, scope: object = MISSING) -> SimpleNamespace:
    payload: dict[str, object] = {"id": question_id}
    if scope is not MISSING:
        payload["review_scope"] = scope
    return SimpleNamespace(**payload)


class FakeEngine:
    def __init__(
        self,
        rules: list[dict[str, object]],
        responses: dict[str, list[SimpleNamespace]],
    ) -> None:
        self.ruleset = SimpleNamespace(rules=rules)
        self.responses = responses

    def check(self, text: str) -> SimpleNamespace:
        return SimpleNamespace(questions=self.responses.get(text, []))


def test_report_counts_scopes_rates_repetition_and_default_reduction() -> None:
    module = load_tool()
    rules = [
        {
            "id": "Q-COMMON",
            "layer": "question",
            "review_scope": "common",
        },
        {
            "id": "Q-LEGACY",
            "layer": "question",
            "review_scope": "common",
        },
        {
            "id": "Q-POST",
            "layer": "question",
            "review_scope": "posting",
        },
        {
            "id": "Q-ZERO",
            "layer": "question",
            "review_scope": "common",
        },
    ]
    texts = [f"private posting text {index}" for index in range(20)]
    responses: dict[str, list[SimpleNamespace]] = {}
    for index, text in enumerate(texts):
        responses[text] = [question("Q-COMMON", "common")]
        if index < 19:
            # A result from an older core object has no review_scope and must
            # therefore be counted as posting even if the catalog says otherwise.
            responses[text].append(question("Q-LEGACY"))
        if index < 2:
            responses[text].append(question("Q-POST", "posting"))

    report = module.build_report(
        texts,
        engine=FakeEngine(rules, responses),
    )

    assert report["split"] == "train_only"
    assert report["record_count"] == 20
    assert report["question_definition_count"] == 4
    assert report["question_instances_total"] == 41
    assert report["question_instances_by_review_scope"] == {
        "common": 20,
        "posting": 21,
    }
    rates = {
        item["question_id"]: item
        for item in report["question_activation_rates"]
    }
    assert rates["Q-COMMON"] == {
        "question_id": "Q-COMMON",
        "review_scope": "common",
        "activated_records": 20,
        "activation_rate": 1.0,
    }
    assert rates["Q-LEGACY"]["review_scope"] == "posting"
    assert rates["Q-LEGACY"]["activation_rate"] == 0.95
    assert rates["Q-POST"]["activation_rate"] == 0.1
    assert rates["Q-ZERO"]["activation_rate"] == 0.0

    repeated = report["repeated_questions_at_or_above_95_percent"]
    assert repeated["count"] == 2
    assert repeated["by_review_scope"] == {"common": 1, "posting": 1}
    assert [item["question_id"] for item in repeated["questions"]] == [
        "Q-COMMON",
        "Q-LEGACY",
    ]
    assert report["default_expanded_posting_question_reduction"] == {
        "baseline_question_instances": 41,
        "default_expanded_question_instances": 21,
        "collapsed_question_instances": 20,
        "reduction_rate": 0.487805,
    }
    assert report["slot_detail_questions"] == {
        "question_ids": [
            "Q-INFO-001",
            "Q-INFO-004",
            "Q-PROC-002",
        ],
        "question_instances": 0,
    }
    assert report["slot_detail_pair_invariants"]["checked_pairs"] == 0
    assert report["slot_detail_pair_invariants"]["mismatched_pairs"] == 0
    assert report["question_instances_by_presentation_group"] == {
        "common": 20,
        "posting_primary": 21,
        "posting_slot_detail": 0,
    }
    assert report["default_expanded_primary_question_reduction"] == {
        "baseline_question_instances": 41,
        "default_expanded_question_instances": 21,
        "collapsed_question_instances": 20,
        "reduction_rate": 0.487805,
    }
    assert "private posting text" not in json.dumps(report)


def test_empty_training_set_has_defined_zero_rates() -> None:
    module = load_tool()
    engine = FakeEngine(
        [
            {"id": "Q-POST", "layer": "question"},
            {
                "id": "Q-COMMON",
                "layer": "question",
                "review_scope": "common",
            },
        ],
        {},
    )

    report = module.build_report([], engine=engine)

    assert report["question_instances_by_review_scope"] == {
        "common": 0,
        "posting": 0,
    }
    assert [
        item["activation_rate"] for item in report["question_activation_rates"]
    ] == [0.0, 0.0]
    assert report["repeated_questions_at_or_above_95_percent"]["questions"] == []
    assert report["default_expanded_posting_question_reduction"][
        "reduction_rate"
    ] == 0.0
    assert report["default_expanded_primary_question_reduction"][
        "reduction_rate"
    ] == 0.0


def test_train_loader_rejects_any_holdout_substring_before_opening(
    tmp_path: Path,
) -> None:
    module = load_tool()
    path = tmp_path / "sealed-HoldOut-records.jsonl"

    with pytest.raises(ValueError, match="train-only"):
        module.load_training_records(path)


def test_cli_output_is_deterministic_and_excludes_posting_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_tool()
    input_path = tmp_path / "train-records.jsonl"
    first_output = tmp_path / "first" / "audit.json"
    second_output = tmp_path / "second" / "audit.json"
    secret_text = "SECRET POSTING BODY"
    secret_record_id = "SECRET-RECORD-ID"
    secret_source_id = "SECRET-SOURCE-ID"
    secret_organization = "SECRET-ORGANIZATION"
    input_path.write_text(
        json.dumps(
            {
                "id": secret_record_id,
                "source_id": secret_source_id,
                "source": "SECRET-SOURCE",
                "organization": secret_organization,
                "text": secret_text,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    engine = FakeEngine(
        [
            {
                "id": "Q-COMMON",
                "layer": "question",
                "review_scope": "common",
            }
        ],
        {secret_text: [question("Q-COMMON", "common")]},
    )
    monkeypatch.setattr(module, "FairpostEngine", lambda: engine)

    assert module.main(
        ["--input", str(input_path), "--output", str(first_output)]
    ) == 0
    assert module.main(
        ["--input", str(input_path), "--output", str(second_output)]
    ) == 0

    assert first_output.read_bytes() == second_output.read_bytes()
    serialized = first_output.read_text(encoding="utf-8")
    for secret in (
        secret_text,
        secret_record_id,
        secret_source_id,
        secret_organization,
        "SECRET-SOURCE",
    ):
        assert secret not in serialized
    report = json.loads(serialized)
    assert report["contains_posting_text"] is False
    assert report["contains_record_ids"] is False
    assert report["contains_source_ids"] is False
    assert report["contains_organization_identifiers"] is False
    assert report["contains_personal_identifiers"] is False
    assert report["question_activation_rates"][0]["question_id"] == "Q-COMMON"


def test_committed_manual_review_evidence_is_aggregate_and_consistent() -> None:
    manual = json.loads(
        (
            ROOT / "reports" / "question_relevance_manual_review.json"
        ).read_text(encoding="utf-8")
    )
    current = json.loads(
        (ROOT / "reports" / "question_relevance_audit.json").read_text(
            encoding="utf-8"
        )
    )

    for privacy_flag in (
        "contains_organization_identifiers",
        "contains_personal_identifiers",
        "contains_posting_text",
        "contains_record_ids",
        "contains_source_ids",
    ):
        assert manual[privacy_flag] is False
    assert manual["not_human_labels"] is True
    assert manual["not_performance_measurement"] is True

    sample = manual["manual_sample"]
    assert sum(sample["classification_counts"].values()) == sample[
        "question_instances_reviewed"
    ]
    posting = sample["posting_scope_false_or_contaminated"]
    assert posting["count"] == (
        sample["classification_counts"]["false_trigger"]
        + sample["classification_counts"]["portal_or_non_posting_contamination"]
    )
    assert posting["total"] == (
        sample["question_instances_reviewed"]
        - sample["classification_counts"]["common_or_organization_wide"]
    )

    before = manual["pre_change_train_snapshot"]
    assert before["record_count"] == current["record_count"]
    assert (
        before["question_instances_total"] - current["question_instances_total"]
        == 104
    )
    current_by_id = {
        row["question_id"]: row["activated_records"]
        for row in current["question_activation_rates"]
    }
    assert sum(
        before_count - current_by_id[question_id]
        for question_id, before_count in before[
            "question_activation_records"
        ].items()
    ) == 104
    groups = current["question_instances_by_presentation_group"]
    assert sum(groups.values()) == current["question_instances_total"]
    assert current["slot_detail_questions"]["question_ids"] == [
        "Q-INFO-001",
        "Q-INFO-004",
        "Q-PROC-002",
    ]
    assert (
        current["slot_detail_questions"]["question_instances"]
        == groups["posting_slot_detail"]
    )
    pair_invariants = current["slot_detail_pair_invariants"]
    assert pair_invariants["checked_pairs"] == current["record_count"] * 3
    assert pair_invariants["mismatched_pairs"] == 0
    for pair in pair_invariants["pairs"]:
        assert pair["checked_records"] == current["record_count"]
        assert pair["slot_missing_records"] == pair[
            "question_activated_records"
        ]
        assert pair["mismatched_records"] == 0
