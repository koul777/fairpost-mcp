from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

from tools import build_private_monitoring_snapshot as snapshot_builder


ROOT = Path(__file__).resolve().parents[1]


def load_tool():
    path = ROOT / "tools" / "audit_private_fairness.py"
    spec = importlib.util.spec_from_file_location("audit_private_fairness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_historical_report_versions(
    report: dict[str, object], engine: object
) -> None:
    ruleset = engine.ruleset
    assert report["evidence_status"] == "historical"
    assert report["source_ruleset_version"] == report["ruleset_version"]
    assert report["source_matching_version"] == report["matching_version"]
    assert report["current_ruleset_version"] == ruleset.version
    assert report["current_matching_version"] == ruleset.matching_version
    assert report["ruleset_version"] != ruleset.version
    assert report["matching_version"] != ruleset.matching_version
    assert "were not rerun" in str(report["historical_reason"])


def write_records(path: Path, records: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def private_records() -> list[dict[str, str]]:
    return [
        {
            "id": "private:secret-1",
            "source": "licensed-feed",
            "source_id": "secret-source-id",
            "sector": "private",
            "organization": "비공개 주식회사",
            "text": "여성만 모집\n급여 월 300만원\n담당자 홍길동 010-1234-5678",
        },
        {
            "id": "private:secret-2",
            "source": "licensed-feed",
            "source_id": "secret-source-id-2",
            "sector": "private",
            "organization": "또다른 회사",
            "text": "성별 무관\n급여 협의\n전형 절차 서류전형 및 면접전형",
        },
    ]


def test_private_audit_is_deterministic_and_contains_no_sensitive_values(
    tmp_path: Path,
) -> None:
    module = load_tool()
    path = tmp_path / "train" / "records.jsonl"
    write_records(path, private_records())
    raw, records = module.load_private_training_records(path)

    first = module.build_report(
        raw, records, high_frequency_threshold=0.5
    )
    second = module.build_report(
        raw, records, high_frequency_threshold=0.5
    )

    assert first == second
    encoded = json.dumps(first, ensure_ascii=False, sort_keys=True)
    for secret in [
        "secret-1",
        "secret-source-id",
        "비공개 주식회사",
        "홍길동",
        "010-1234-5678",
        "여성만 모집",
    ]:
        assert secret not in encoded
    assert first["privacy_boundary"] == {
        "contains_organization_identifiers": False,
        "contains_personal_identifiers": False,
        "contains_posting_text": False,
        "contains_record_ids": False,
        "contains_source_ids": False,
    }
    sex = next(row for row in first["law_rules"] if row["id"] == "SEX-001")
    assert sex["records"] == 1
    assert sex["rate"] == 0.5


def test_private_audit_redacts_unapproved_source_labels(tmp_path: Path) -> None:
    module = load_tool()
    path = tmp_path / "train" / "records.jsonl"
    records = private_records()
    records[0]["source"] = "secret-company@example.com"
    write_records(path, records)

    raw, loaded = module.load_private_training_records(path)
    report = module.build_report(raw, loaded, high_frequency_threshold=0.8)
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)

    assert "secret-company@example.com" not in encoded
    assert report["input"]["sources"] == {"licensed-feed": 1, "other": 1}


def test_private_audit_rejects_holdout_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_tool()
    path = tmp_path / "holdout" / "records.jsonl"
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("must not read"))

    with pytest.raises(ValueError, match="train"):
        module.load_private_training_records(path)


@pytest.mark.parametrize("split", ["dev", "test", "evaluation", "hold-out"])
def test_private_audit_rejects_other_non_train_splits_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, split: str
) -> None:
    module = load_tool()
    path = tmp_path / split / "records.jsonl"
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("must not read"))

    with pytest.raises(ValueError, match="train"):
        module.load_private_training_records(path)


def test_private_audit_requires_explicit_train_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_tool()
    path = tmp_path / "records.jsonl"
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("must not read"))

    with pytest.raises(ValueError, match="train"):
        module.load_private_training_records(path)


def test_private_audit_rejects_public_or_mixed_records(tmp_path: Path) -> None:
    module = load_tool()
    path = tmp_path / "train" / "records.jsonl"
    records = private_records()
    records.append(
        {
            "id": "public:1",
            "source": "public-source",
            "sector": "public",
            "text": "공공 채용공고",
        }
    )
    write_records(path, records)

    with pytest.raises(ValueError, match="sector=private"):
        module.load_private_training_records(path)


@pytest.mark.parametrize("split", ["holdout", "dev", "evaluation", "test"])
def test_private_audit_rejects_non_train_record_metadata(
    tmp_path: Path, split: str
) -> None:
    module = load_tool()
    path = tmp_path / "train" / "records.jsonl"
    records = private_records()
    records[0]["split"] = split
    write_records(path, records)

    with pytest.raises(ValueError, match="split.*train"):
        module.load_private_training_records(path)


def test_private_audit_accepts_explicit_train_record_metadata(tmp_path: Path) -> None:
    module = load_tool()
    path = tmp_path / "train" / "records.jsonl"
    records = private_records()
    records[0]["split"] = "train"
    write_records(path, records)

    _, loaded = module.load_private_training_records(path)
    assert loaded[0]["split"] == "train"


def test_private_audit_reports_baseline_deltas(tmp_path: Path) -> None:
    module = load_tool()
    path = tmp_path / "train" / "records.jsonl"
    write_records(path, private_records())
    raw, records = module.load_private_training_records(path)
    baseline = {
        "input": {
            "records": 1,
            "sha256": "old",
            "sector": "private",
            "split": "train_only",
        },
        "law_rule_posting_hits": {"SEX-001": 0},
        "question_posting_hits": {},
        "slot_found_posting_hits": {},
    }

    report = module.build_report(
        raw,
        records,
        high_frequency_threshold=0.8,
        baseline=baseline,
    )

    delta = report["change_from_baseline"]
    assert delta["record_delta"] == 1
    assert delta["baseline_input_sha256"] == "old"
    assert delta["posting_hit_deltas"]["law_rules"]["SEX-001"] == 1


def test_private_audit_rejects_duplicate_or_non_private_baseline(
    tmp_path: Path,
) -> None:
    module = load_tool()
    path = tmp_path / "train" / "records.jsonl"
    write_records(path, private_records())
    raw, records = module.load_private_training_records(path)
    duplicate = {
        "input": {"records": 2, "sector": "private", "split": "train_only"},
        "law_rules": [
            {"id": "SEX-001", "records": 1},
            {"id": "SEX-001", "records": 2},
        ],
        "questions": [],
        "slots_found": [],
    }

    with pytest.raises(ValueError, match="중복"):
        module.build_report(
            raw,
            records,
            high_frequency_threshold=0.8,
            baseline=duplicate,
        )

    public = {
        "input": {"records": 2, "sector": "public", "split": "train_only"},
        "law_rules": [],
        "questions": [],
        "slots_found": [],
    }
    with pytest.raises(ValueError, match="private"):
        module.build_report(
            raw,
            records,
            high_frequency_threshold=0.8,
            baseline=public,
        )


def test_private_audit_rejects_blank_text(tmp_path: Path) -> None:
    module = load_tool()
    path = tmp_path / "train" / "records.jsonl"
    write_records(
        path,
        [{"source": "licensed-feed", "sector": "private", "text": "  "}],
    )

    with pytest.raises(ValueError, match="비어 있지 않은 text"):
        module.load_private_training_records(path)


def test_private_audit_cli_writes_anonymous_report(tmp_path: Path) -> None:
    input_path = tmp_path / "train" / "records.jsonl"
    output_path = tmp_path / "audit.json"
    write_records(input_path, private_records())

    completed = subprocess.run(
        [
            sys.executable,
            "tools/audit_private_fairness.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["input"]["records"] == 2
    assert report["input"]["sector"] == "private"
    assert report["input"]["manifest_verified"] is False


@pytest.mark.parametrize("argument", ["--input", "--baseline", "--manifest"])
def test_private_audit_cli_read_failures_do_not_echo_sensitive_paths(
    tmp_path: Path, argument: str
) -> None:
    safe_input = tmp_path / "train" / "records.jsonl"
    write_records(safe_input, private_records())
    secret = "customer-secret-company"
    missing = tmp_path / secret / "train" / "missing.json"
    args = [
        sys.executable,
        "tools/audit_private_fairness.py",
        "--input",
        str(safe_input),
        "--output",
        str(tmp_path / "audit.json"),
    ]
    if argument == "--input":
        args[args.index("--input") + 1] = str(missing)
    else:
        args.extend([argument, str(missing)])

    completed = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert completed.returncode != 0
    assert secret not in completed.stderr
    assert str(missing) not in completed.stderr
    assert "Traceback" not in completed.stderr
    assert not (tmp_path / "audit.json").exists()


def test_private_audit_validates_new_snapshot_manifest(tmp_path: Path) -> None:
    source_path = tmp_path / "source.jsonl"
    source_path.write_text(
        json.dumps(
            {
                "source_category": "company-career-page",
                "source_url": "https://careers.example.com/jobs/1",
                "published_at": "2026-08-03",
                "organization": "예시회사",
                "text": "예시회사 채용 공고",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "snapshot"
    snapshot_builder.build_snapshot(
        source_path,
        output_dir,
        tmp_path / "summary.json",
    )
    records_path = output_dir / "train" / "records.jsonl"
    manifest_path = output_dir / "train" / "manifest.json"
    module = load_tool()
    raw, records = module.load_private_training_records(records_path)

    module.validate_snapshot_manifest(manifest_path, raw, records)
    report = module.build_report(
        raw,
        records,
        high_frequency_threshold=0.8,
        manifest_verified=True,
    )
    assert report["input"]["manifest_verified"] is True


def test_private_audit_rejects_tampered_snapshot_manifest(tmp_path: Path) -> None:
    source_path = tmp_path / "source.jsonl"
    source_path.write_text(
        json.dumps(
            {
                "source_category": "licensed-feed",
                "source_url": "https://feed.example.com/jobs/1",
                "published_at": "2026-08-03",
                "organization": "예시회사",
                "text": "예시회사 채용 공고",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "snapshot"
    snapshot_builder.build_snapshot(
        source_path,
        output_dir,
        tmp_path / "summary.json",
    )
    records_path = output_dir / "train" / "records.jsonl"
    manifest_path = output_dir / "train" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["records_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    module = load_tool()
    raw, records = module.load_private_training_records(records_path)

    with pytest.raises(ValueError, match="records_sha256"):
        module.validate_snapshot_manifest(manifest_path, raw, records)


def test_committed_private_audit_matches_current_dictionary() -> None:
    module = load_tool()
    report = json.loads(
        (ROOT / "reports" / "private_fairness_audit.json").read_text(
            encoding="utf-8"
        )
    )
    engine = module.FairpostEngine()
    law_ids = {
        rule["id"] for rule in engine.ruleset.rules if rule["layer"] == "law"
    }
    question_ids = {
        rule["id"]
        for rule in engine.ruleset.rules
        if rule["layer"] == "question"
    }

    assert {row["id"] for row in report["law_rules"]} == law_ids
    assert {row["id"] for row in report["questions"]} == question_ids
    assert {row["id"] for row in report["slots_found"]} == set(
        engine.ruleset.slots
    )
    assert report["ruleset_version"] == engine.ruleset.version
    assert report["matching_version"] == engine.ruleset.matching_version
    assert report["input"]["manifest_verified"] is False


def test_private_research_bundle_is_traceable_and_sanitized() -> None:
    bundle = json.loads(
        (ROOT / "docs" / "private-fairness-research-bundle.json").read_text(
            encoding="utf-8"
        )
    )
    source_ids = [source["source_id"] for source in bundle["sources"]]
    encoded = json.dumps(bundle, ensure_ascii=False, sort_keys=True)

    assert bundle["schema_version"] == "1.0"
    assert bundle["jurisdiction"] == "대한민국"
    assert len(source_ids) == len(set(source_ids))
    assert all(
        source.get("url", "https://local.invalid").startswith("https://")
        for source in bundle["sources"]
    )
    assert all(
        set(claim["source_ids"]) <= set(source_ids)
        and claim["relation"]
        in {"supports", "contradicts", "context_only", "no_evidence"}
        for claim in bundle["claims"]
    )
    assert not re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", encoded, re.I)
    assert not re.search(r"01[016789][- ]?\d{3,4}[- ]?\d{4}", encoded)
    private_sources = [
        source for source in bundle["sources"] if source["source_id"].startswith("src_1")
    ]
    assert private_sources
    assert all(
        source["title"].startswith("공개 민간 채용공고 사례")
        for source in private_sources
    )

    matrix = json.loads(
        (ROOT / "reports" / "private_fairness_case_matrix.json").read_text(
            encoding="utf-8"
        )
    )
    matrix_text = json.dumps(matrix, ensure_ascii=False, sort_keys=True)
    assert matrix["evidence_status"] == "historical"
    assert matrix["source_ruleset_version"] == "not_recorded"
    assert matrix["summary"]["case_family_count"] == len(matrix["case_families"])
    assert all(
        set(family["research_source_ids"]) <= set(source_ids)
        for family in matrix["case_families"]
    )
    for family in matrix["case_families"]:
        evidence_path = family.get("internal_evidence_report")
        if evidence_path is not None:
            assert (ROOT / evidence_path).is_file()
    assert not re.search(
        r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", matrix_text, re.I
    )
    assert not re.search(r"01[016789][- ]?\d{3,4}[- ]?\d{4}", matrix_text)


def test_model_assisted_triage_report_is_aggregate_private_and_not_performance() -> None:
    report = json.loads(
        (ROOT / "reports" / "private_review_model_triage.json").read_text(
            encoding="utf-8"
        )
    )
    module = load_tool()
    engine = module.FairpostEngine()
    presence_ids = {
        rule["id"]
        for rule in engine.ruleset.rules
        if rule["layer"] in {"law", "question"}
        and rule.get("trigger", {}).get("mode", "presence") == "presence"
    }
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)

    assert report["schema_version"] == "private-review-model-triage-v1"
    assert_historical_report_versions(report, engine)
    assert report["status"] == "model_assisted_triage_only"
    assert report["not_human_labels"] is True
    assert report["not_performance_measurement"] is True
    assert report["scope"]["sector"] == "private"
    assert report["scope"]["split"] == "train_only"
    assert all(value is False for value in report["privacy_boundary"].values())
    assert {row["rule_id"] for row in report["rules"]} <= presence_ids
    assert all(
        row["reviewed"]
        == row["true_positive"] + row["false_positive"] + row["uncertain"]
        for row in report["rules"]
    )
    assert report["aggregate"] == {
        key: sum(row[key] for row in report["rules"])
        for key in ("reviewed", "true_positive", "false_positive", "uncertain")
    }
    assert report["scope"]["records"] == report["aggregate"]["reviewed"]
    assert not re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", encoded, re.I)
    assert not re.search(r"01[016789][- ]?\d{3,4}[- ]?\d{4}", encoded)


def test_standalone_audit_publish_failure_preserves_previous_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_tool()
    output_path = tmp_path / "audit.json"
    output_path.write_text("previous-report", encoding="utf-8")

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("simulated publish failure")

    monkeypatch.setattr(module.os, "replace", fail_replace)

    with pytest.raises(ValueError, match="could not be written"):
        module._write_report(output_path, {"status": "ok"})

    assert output_path.read_text(encoding="utf-8") == "previous-report"
    assert not list(tmp_path.glob(".audit.json.fairpost-stage-*"))


def test_age_context_audit_is_current_aggregate_and_not_performance() -> None:
    report = json.loads(
        (ROOT / "reports" / "private_age_context_audit.json").read_text(
            encoding="utf-8"
        )
    )
    module = load_tool()
    engine = module.FairpostEngine()
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)

    assert report["schema_version"] == "private-age-context-audit-v1"
    assert_historical_report_versions(report, engine)
    assert report["not_human_labels"] is True
    assert report["not_performance_measurement"] is True
    assert report["scope"] == {
        "records": 2100,
        "sector": "private",
        "split": "train_only",
        "source_category_count": 1,
        "rule_id": "AGE-002",
    }
    assert all(value is False for value in report["privacy_boundary"].values())
    assert sum(
        row["records"] for row in report["pre_fix_analysis"]["clusters"]
    ) == report["pre_fix_analysis"]["activated_records"]
    assert sum(report["pre_fix_analysis"]["explicit_numeric_subtypes"].values()) == 118
    assert report["post_fix_verification"]["activated_records"] == 192
    assert report["post_fix_verification"]["record_delta"] == -1
    assert report["post_fix_verification"]["web_parity_mismatches"] == 0
    assert not re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", encoded, re.I)
    assert not re.search(r"01[016789][- ]?\d{3,4}[- ]?\d{4}", encoded)


def test_result_notice_context_audit_is_current_aggregate_and_not_performance() -> None:
    report = json.loads(
        (ROOT / "reports" / "private_result_notice_context_audit.json").read_text(
            encoding="utf-8"
        )
    )
    module = load_tool()
    engine = module.FairpostEngine()
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)

    assert report["schema_version"] == "private-result-notice-context-audit-v1"
    assert_historical_report_versions(report, engine)
    assert report["not_human_labels"] is True
    assert report["not_performance_measurement"] is True
    assert report["scope"] == {
        "records": 2100,
        "sector": "private",
        "split": "train_only",
        "source_category_count": 1,
        "rule_id": "Q-INFO-014",
        "activated_records": 161,
    }
    assert all(value is False for value in report["privacy_boundary"].values())
    assert sum(
        row["records"] for row in report["classification"]["clusters"]
    ) == report["scope"]["activated_records"]
    assert report["classification"]["protective_exclusion_candidates"] == 0
    current = report["current_context_audit"]
    assert sum(current["exclusive_primary_context"].values()) == 161
    assert current["safe_downgrade_candidates"] == 0
    assert current["decision"] == "hold_rule_change_pending_human_labels"
    assert report["decision"]["action"] == (
        "retain_review_question_without_new_exclusion"
    )
    assert not re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", encoded, re.I)
    assert not re.search(r"01[016789][- ]?\d{3,4}[- ]?\d{4}", encoded)


def test_return_context_audit_is_current_aggregate_and_not_performance() -> None:
    report = json.loads(
        (ROOT / "reports" / "private_return_context_audit.json").read_text(
            encoding="utf-8"
        )
    )
    module = load_tool()
    engine = module.FairpostEngine()
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)

    assert report["schema_version"] == "private-return-context-audit-v1"
    assert_historical_report_versions(report, engine)
    assert report["not_human_labels"] is True
    assert report["not_performance_measurement"] is True
    assert report["scope"] == {
        "records": 2100,
        "sector": "private",
        "split": "train_only",
        "source_category_count": 1,
        "phrase_family": "nonreturn_conjunctive_variant",
    }
    assert all(value is False for value in report["privacy_boundary"].values())
    assert sum(
        row["records"] for row in report["classification"]["clusters"]
    ) == report["classification"]["variant_records"] == 69
    assert report["post_change_activation"] == {
        "RETURN-001": 68,
        "Q-INFO-013": 69,
    }
    assert report["decision"]["action"] == (
        "protect_email_only_keep_mixed_submission_finding_candidate"
    )
    assert not re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", encoded, re.I)
    assert not re.search(r"01[016789][- ]?\d{3,4}[- ]?\d{4}", encoded)


def test_nationality_context_audit_is_current_aggregate_and_not_performance() -> None:
    report = json.loads(
        (ROOT / "reports" / "private_nationality_context_audit.json").read_text(
            encoding="utf-8"
        )
    )
    module = load_tool()
    engine = module.FairpostEngine()
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)

    assert report["schema_version"] == "private-nationality-context-audit-v1"
    assert_historical_report_versions(report, engine)
    assert report["not_human_labels"] is True
    assert report["not_performance_measurement"] is True
    assert report["scope"] == {
        "records": 2100,
        "sector": "private",
        "split": "train_only",
        "source_category_count": 1,
        "rule_id": "Q-DIST-016",
    }
    assert all(value is False for value in report["privacy_boundary"].values())
    assert report["phrase_analysis"]["direct_exclusion_candidate_records"] == 1
    assert report["phrase_analysis"]["inclusive_or_preference_records"] == 11
    assert report["phrase_analysis"]["work_authorization_context_records"] == 30
    assert report["post_change_verification"]["Q-DIST-016"] == 1
    assert report["decision"]["action"] == "add_review_question_not_law_finding"
    assert not re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", encoded, re.I)
    assert not re.search(r"01[016789][- ]?\d{3,4}[- ]?\d{4}", encoded)


def test_religion_context_audit_is_current_aggregate_and_not_performance() -> None:
    report = json.loads(
        (ROOT / "reports" / "private_religion_context_audit.json").read_text(
            encoding="utf-8"
        )
    )
    module = load_tool()
    engine = module.FairpostEngine()
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)

    assert report["schema_version"] == "private-religion-context-audit-v1"
    assert_historical_report_versions(report, engine)
    assert report["not_human_labels"] is True
    assert report["not_performance_measurement"] is True
    assert report["scope"] == {
        "records": 2100,
        "sector": "private",
        "split": "train_only",
        "source_category_count": 1,
        "rule_id": "Q-DIST-012",
    }
    assert all(value is False for value in report["privacy_boundary"].values())
    pre = report["pre_change_analysis"]
    assert sum(row["records"] for row in pre["broad_activation_clusters"]) == 9
    assert pre["religion_keyword_records"] == 148
    assert report["post_change_verification"]["Q-DIST-012"] == 1
    assert report["decision"]["action"] == (
        "replace_standalone_keywords_with_explicit_qualification_or_question_patterns"
    )
    assert not re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", encoded, re.I)
    assert not re.search(r"01[016789][- ]?\d{3,4}[- ]?\d{4}", encoded)


def test_marital_context_audit_is_current_aggregate_and_not_performance() -> None:
    report = json.loads(
        (ROOT / "reports" / "private_marital_context_audit.json").read_text(
            encoding="utf-8"
        )
    )
    module = load_tool()
    engine = module.FairpostEngine()
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)

    assert report["schema_version"] == "private-marital-context-audit-v1"
    assert_historical_report_versions(report, engine)
    assert report["not_human_labels"] is True
    assert report["not_performance_measurement"] is True
    assert report["scope"] == {
        "records": 2100,
        "sector": "private",
        "split": "train_only",
        "source_category_count": 1,
        "rule_id": "Q-DIST-017",
    }
    assert all(value is False for value in report["privacy_boundary"].values())
    phrases = report["phrase_analysis"]
    assert phrases["protective_noncollection_records"] + phrases[
        "review_candidate_records"
    ] == phrases["marital_status_records"] == 53
    assert report["post_change_verification"]["Q-DIST-017"] == 2
    assert report["decision"]["action"] == (
        "add_review_question_with_blind_notice_protection"
    )
    assert not re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", encoded, re.I)
    assert not re.search(r"01[016789][- ]?\d{3,4}[- ]?\d{4}", encoded)


def test_family_evidence_context_audit_is_current_aggregate_and_not_performance() -> None:
    report = json.loads(
        (ROOT / "reports" / "private_family_evidence_context_audit.json").read_text(
            encoding="utf-8"
        )
    )
    module = load_tool()
    engine = module.FairpostEngine()
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)

    assert report["schema_version"] == "private-family-evidence-context-audit-v1"
    assert_historical_report_versions(report, engine)
    assert report["not_human_labels"] is True
    assert report["not_performance_measurement"] is True
    assert report["scope"] == {
        "records": 2100,
        "sector": "private",
        "split": "train_only",
        "source_category_count": 1,
        "rule_ids": ["FAMILY-001", "SCHOOL-001", "Q-INFO-011"],
    }
    assert all(value is False for value in report["privacy_boundary"].values())
    pre = report["pre_change_analysis"]
    assert sum(row["records"] for row in pre["protective_phrase_clusters"]) == (
        pre["sibling_education_variant_records"]
    )
    assert pre["sibling_education_unprotected_records"] == 0
    assert report["post_change_verification"]["SCHOOL-001"] == 0
    assert report["post_change_verification"]["Q-INFO-011"] == 162
    assert report["post_change_verification"]["Q-INFO-011_delta"] == 4
    assert report["post_change_verification"]["web_parity_mismatches"] == 0
    assert not re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", encoded, re.I)
    assert not re.search(r"01[016789][- ]?\d{3,4}[- ]?\d{4}", encoded)


def test_gender_context_audit_is_current_aggregate_and_not_performance() -> None:
    report = json.loads(
        (ROOT / "reports" / "private_gender_context_audit.json").read_text(
            encoding="utf-8"
        )
    )
    module = load_tool()
    engine = module.FairpostEngine()
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)

    assert report["schema_version"] == "private-gender-context-audit-v1"
    assert_historical_report_versions(report, engine)
    assert report["not_human_labels"] is True
    assert report["not_performance_measurement"] is True
    assert report["scope"] == {
        "records": 2100,
        "sector": "private",
        "split": "train_only",
        "source_category_count": 1,
        "law_rule_id": "SEX-001",
        "review_question_id": "Q-DIST-015",
    }
    assert all(value is False for value in report["privacy_boundary"].values())
    structured = report["structured_gender_field_analysis"]
    assert sum(row["records"] for row in structured["SEX-001_surface_clusters"]) == (
        structured["SEX-001_records"]
    )
    assert structured["union_records"] == 2100
    expansion = report["review_question_expansion"]
    assert sum(row["records"] for row in expansion["new_candidate_clusters"]) == (
        expansion["record_delta"]
    )
    assert expansion["post_change_records"] == 50
    assert expansion["web_parity_mismatches"] == 0
    current = report["current_review_context_audit"]
    assert sum(current["exclusive_clusters"].values()) == 50
    assert sum(current["review_priority"].values()) == 50
    assert current["pure_inclusive_or_protective_false_positive"] == 0
    assert current["decision"] == "hold_rule_change_pending_human_labels"
    assert not re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", encoded, re.I)
    assert not re.search(r"01[016789][- ]?\d{3,4}[- ]?\d{4}", encoded)
