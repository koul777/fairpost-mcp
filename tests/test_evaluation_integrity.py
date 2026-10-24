from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_tool(name: str):
    path = ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"integrity_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_annotation_payload_binds_matching_version_and_metric_scope() -> None:
    module = load_tool("build_annotation_ui")

    payload = module.build_payload([])

    assert payload["evaluation_phase"] == "sealed_holdout_final"
    assert payload["matching_version"]
    assert payload["metric_scope"]["question_cards"] == "pilot_only_not_g1_g2"


def test_annotation_ui_rejects_output_alias_and_preserves_existing_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = load_tool("build_annotation_ui")
    holdout = tmp_path / "holdout"
    holdout.mkdir()
    records = holdout / "records.jsonl"
    manifest = holdout / "manifest.json"
    output = holdout / "labeler.html"
    records.write_text("records", encoding="utf-8")
    manifest.write_text("manifest", encoding="utf-8")
    output.write_text("existing", encoding="utf-8")

    with pytest.raises(ValueError, match="다른 파일"):
        module.validate_paths(records, manifest, records)

    hardlink = holdout / "records-alias.jsonl"
    hardlink.hardlink_to(records)
    with pytest.raises(ValueError, match="다른 파일"):
        module.validate_paths(records, manifest, hardlink)

    monkeypatch.setattr(
        os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )
    with pytest.raises(OSError, match="replace failed"):
        module.atomic_write_text(output, "new")
    assert output.read_text(encoding="utf-8") == "existing"


def test_handoff_detects_stale_labeler_version() -> None:
    module = load_tool("build_human_labeling_handoff")
    embedded = {
        "records": [{"id": "one", "content_hash": "a" * 64}],
        "ruleset_version": "old-rules",
        "matching_version": "old-matching",
        "evaluation_phase": "sealed_holdout_final",
        "metric_scope": {"question_cards": "pilot_only_not_g1_g2"},
    }
    encoded = base64.b64encode(json.dumps(embedded).encode()).decode()
    labeler = (
        '<script id="payload" type="application/octet-stream">'
        f"{encoded}</script>"
    )

    class CurrentRuleset:
        version = "current-rules"
        matching_version = "current-matching"

    with pytest.raises(ValueError, match="오래되었습니다"):
        module._verify_labeler_binding(
            labeler,
            {"ids": ["one"], "content_hashes": ["a" * 64]},
            CurrentRuleset(),
        )


def test_human_attestation_rejects_ai_generated_gold(tmp_path: Path) -> None:
    module = load_tool("evaluate")
    path = tmp_path / "attestation.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "attestation": "human_gold",
                "prediction_blinded": True,
                "ai_generated_labels": True,
                "reviewer_ids": ["reviewer-01"],
                "annotations_sha256": "a" * 64,
                "holdout_manifest_sha256": "b" * 64,
                "holdout_records_sha256": "c" * 64,
                "ruleset_version": "rules",
                "matching_version": "matching",
                "attested_at": "2026-08-30T00:00:00+09:00",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="ai_generated_labels"):
        module.load_human_attestation(
            path,
            annotations_sha256="a" * 64,
            holdout_manifest_sha256="b" * 64,
            holdout_records_sha256="c" * 64,
            ruleset_version="rules",
            matching_version="matching",
        )


def test_human_attestation_binds_holdout_record_metadata(tmp_path: Path) -> None:
    module = load_tool("evaluate")
    path = tmp_path / "attestation.json"
    payload = {
        "schema_version": 2,
        "attestation": "human_gold",
        "prediction_blinded": True,
        "ai_generated_labels": False,
        "reviewer_ids": ["reviewer-01"],
        "annotations_sha256": "a" * 64,
        "holdout_manifest_sha256": "b" * 64,
        "holdout_records_sha256": "c" * 64,
        "ruleset_version": "rules",
        "matching_version": "matching",
        "attested_at": "2026-08-30T00:00:00+09:00",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="holdout_records_sha256"):
        module.load_human_attestation(
            path,
            annotations_sha256="a" * 64,
            holdout_manifest_sha256="b" * 64,
            holdout_records_sha256="changed-records",
            ruleset_version="rules",
            matching_version="matching",
        )


def test_final_receipt_allows_reproduction_but_rejects_tuning(
    tmp_path: Path,
) -> None:
    module = load_tool("evaluate")
    receipt = tmp_path / "final-evaluation-receipt.json"
    binding = module._receipt_binding(
        dataset_fingerprint="d" * 64,
        annotations_sha256="a" * 64,
        attestation_sha256="h" * 64,
        ruleset_version="rules-v1",
        matching_version="matching-v1",
        evaluator_source_fingerprint="evaluator-v1",
        complete_holdout=True,
    )

    assert module.register_final_evaluation(receipt, binding) == "registered"
    assert module.register_final_evaluation(receipt, binding) == "reproduced"

    tuned = {**binding, "matching_version": "matching-v2"}
    with pytest.raises(ValueError, match="새 독립 홀드아웃"):
        module.register_final_evaluation(receipt, tuned)

    changed_evaluator = {
        **binding,
        "evaluator_source_fingerprint": "evaluator-v2",
    }
    with pytest.raises(ValueError, match="새 독립 홀드아웃"):
        module.register_final_evaluation(receipt, changed_evaluator)


def test_final_receipt_is_pending_until_report_is_persisted(tmp_path: Path) -> None:
    module = load_tool("evaluate")
    receipt = tmp_path / "final-evaluation-receipt.json"
    binding = module._receipt_binding(
        dataset_fingerprint="d" * 64,
        annotations_sha256="a" * 64,
        attestation_sha256="h" * 64,
        ruleset_version="rules-v1",
        matching_version="matching-v1",
        evaluator_source_fingerprint="evaluator-v1",
        complete_holdout=True,
    )

    assert module.prepare_final_evaluation(receipt, binding) == "prepared"
    assert json.loads(receipt.read_text(encoding="utf-8"))["state"] == "pending"
    assert module.prepare_final_evaluation(receipt, binding) == "pending_recovery"
    assert (
        module.finalize_final_evaluation(
            receipt, binding, report_sha256="r" * 64
        )
        == "registered"
    )
    finalized = json.loads(receipt.read_text(encoding="utf-8"))
    assert finalized["state"] == "finalized"
    assert finalized["report_sha256"] == "r" * 64
    with pytest.raises(ValueError, match="보고서 해시"):
        module.finalize_final_evaluation(
            receipt,
            binding,
            report_sha256="changed-report",
        )


def test_evaluator_fingerprint_ignores_formatting_but_binds_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_tool("evaluate")
    source = tmp_path / "evaluate.py"
    source.write_text("value=1\n", encoding="utf-8")
    monkeypatch.setattr(module, "__file__", str(source))
    first = module.evaluator_fingerprint()

    source.write_text("value = 1\n\n", encoding="utf-8")
    assert module.evaluator_fingerprint() == first

    source.write_text("value = 2\n", encoding="utf-8")
    assert module.evaluator_fingerprint() != first


def test_evaluator_fingerprint_binds_imported_engine_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_tool("evaluate")
    for relative in module.EVALUATOR_SOURCE_FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "__file__", str(tmp_path / "tools" / "evaluate.py"))

    first = module.evaluator_fingerprint()
    (tmp_path / "core" / "engine.py").write_text("value = 2\n", encoding="utf-8")

    assert module.evaluator_fingerprint() != first


def test_captured_input_bytes_are_used_after_source_file_changes(tmp_path: Path) -> None:
    module = load_tool("evaluate")
    path = tmp_path / "manifest.json"
    original = json.dumps({"content_hashes": ["a" * 64]}).encode()
    path.write_bytes(original)
    captured = path.read_bytes()
    path.write_text(json.dumps({"content_hashes": ["b" * 64]}), encoding="utf-8")

    assert module.load_hashes(path, captured) == {"a" * 64}
    assert hashlib.sha256(captured).hexdigest() != hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def test_final_evaluation_reproduction_keeps_report_and_receipt_hashes(
    tmp_path: Path,
) -> None:
    train_dir = tmp_path / "train"
    holdout_dir = tmp_path / "holdout"
    train_dir.mkdir()
    holdout_dir.mkdir()
    (train_dir / "manifest.json").write_text(
        json.dumps({"content_hashes": []}),
        encoding="utf-8",
    )
    text = "일반 사무직 채용"
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    (holdout_dir / "manifest.json").write_text(
        json.dumps({"content_hashes": [content_hash]}),
        encoding="utf-8",
    )
    (holdout_dir / "records.jsonl").write_text(
        json.dumps(
            {
                "id": "holdout:one",
                "content_hash": content_hash,
                "text": text,
                "sector": "public",
                "source": "synthetic-holdout",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    annotations = holdout_dir / "annotations.jsonl"
    annotations.write_text(
        json.dumps(
            {
                "id": "holdout:one",
                "content_hash": content_hash,
                "text": text,
                "expected_findings": [],
                "expected_absent_slots": [],
                "expected_expressions": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    receipt = holdout_dir / "final-evaluation-receipt.json"
    output = tmp_path / "evaluation.json"
    command = [
        sys.executable,
        "tools/evaluate.py",
        str(annotations),
        "--phase",
        "final",
        "--train-manifest",
        str(train_dir / "manifest.json"),
        "--holdout-manifest",
        str(holdout_dir / "manifest.json"),
        "--holdout-records",
        str(holdout_dir / "records.jsonl"),
        "--evaluation-receipt",
        str(receipt),
        "--output",
        str(output),
    ]

    first = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert first.returncode == 0, first.stderr
    first_report = output.read_bytes()
    first_receipt = receipt.read_bytes()

    reproduced = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert reproduced.returncode == 0, reproduced.stderr
    assert output.read_bytes() == first_report
    assert receipt.read_bytes() == first_receipt
    report = json.loads(first_report)
    receipt_payload = json.loads(first_receipt)
    assert report["schema_version"] == 4
    assert report["final_evaluation_receipt"] == {
        "file": receipt.name,
        "status": "bound",
    }
    # The new per-rule fields are part of the hashed report, so the receipt
    # and the byte-identical reproduction above cover them. No labels or
    # findings exist in this holdout, so every law rule is a coverage gap.
    assert report["rule_coverage"]["observed_rule_ids"] == []
    assert report["rule_coverage"]["coverage_gap_rule_ids"] == sorted(
        report["expression_detection_by_rule"]
    )
    assert report["rule_coverage"][
        "coverage_gap_excluded_from_performance_claims"
    ] is True
    assert receipt_payload["report_sha256"] == hashlib.sha256(
        first_report
    ).hexdigest()


def test_rule_breakdown_uses_the_same_sets_as_the_aggregate() -> None:
    module = load_tool("evaluate")
    postings = [
        ({"SEX-001", "LOOK-001"}, {"SEX-001"}),
        ({"LOOK-001"}, {"LOOK-001", "AGE-001"}),
        (set(), set()),
    ]
    rule_counts = {
        rule_id: module.Counter()
        for rule_id in ("AGE-001", "LOOK-001", "SEX-001", "PHOTO-001")
    }
    aggregate = {"true_positive": 0, "false_positive": 0, "false_negative": 0}
    for predicted, expected in postings:
        module.tally_rule_findings(rule_counts, predicted, expected)
        aggregate["true_positive"] += len(predicted & expected)
        aggregate["false_positive"] += len(predicted - expected)
        aggregate["false_negative"] += len(expected - predicted)

    by_rule = module.expression_detection_by_rule(rule_counts)

    assert list(by_rule) == ["AGE-001", "LOOK-001", "PHOTO-001", "SEX-001"]
    assert by_rule["SEX-001"] == module.metrics(1, 0, 0)
    assert by_rule["LOOK-001"] == module.metrics(1, 1, 0)
    assert by_rule["AGE-001"] == module.metrics(0, 0, 1)
    assert by_rule["PHOTO-001"] == module.metrics(0, 0, 0)
    assert by_rule["PHOTO-001"]["precision"] is None
    for key in aggregate:
        assert sum(item[key] for item in by_rule.values()) == aggregate[key]


def test_rule_coverage_separates_unobserved_rules_from_observed_ones() -> None:
    module = load_tool("evaluate")
    by_rule = {
        "SEX-001": module.metrics(2, 0, 0),
        "LOOK-001": module.metrics(0, 1, 0),
        "AGE-001": module.metrics(0, 0, 3),
        "PHOTO-001": module.metrics(0, 0, 0),
        "BODY-001": module.metrics(0, 0, 0),
    }

    coverage = module.build_rule_coverage(by_rule)

    assert coverage == {
        "observed_rule_ids": ["AGE-001", "LOOK-001", "SEX-001"],
        "coverage_gap_rule_ids": ["BODY-001", "PHOTO-001"],
        "coverage_gap_excluded_from_performance_claims": True,
    }


def test_calibration_report_has_per_rule_breakdown_and_rule_coverage(
    tmp_path: Path,
) -> None:
    train_dir = tmp_path / "train"
    holdout_dir = tmp_path / "holdout"
    train_dir.mkdir()
    holdout_dir.mkdir()
    # Synthetic postings only. The engine fires SEX-001 on the first text and
    # LOOK-001 on the second. The third text fires nothing but is labeled with
    # AGE-001, so AGE-001 is a false negative.
    cases = [
        ("지원자격: 남성만 지원 가능", ["SEX-001"]),
        ("용모 단정한 여성 지원자 우대", []),
        ("일반 사무직 채용", ["AGE-001"]),
    ]
    records = []
    labels = []
    for index, (text, expected) in enumerate(cases):
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        records.append(
            {
                "id": f"train:{index}",
                "content_hash": content_hash,
                "text": text,
                "sector": "public" if index % 2 == 0 else "private",
                "source": "synthetic-train",
            }
        )
        labels.append(
            {
                "id": f"train:{index}",
                "content_hash": content_hash,
                "text": text,
                "expected_findings": expected,
                "expected_absent_slots": [],
                "expected_expressions": [{"rule_id": rule_id} for rule_id in expected],
            }
        )
    hashes = [record["content_hash"] for record in records]
    (train_dir / "manifest.json").write_text(
        json.dumps({"content_hashes": hashes}), encoding="utf-8"
    )
    (train_dir / "records.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
        encoding="utf-8",
    )
    (holdout_dir / "manifest.json").write_text(
        json.dumps({"content_hashes": []}), encoding="utf-8"
    )
    annotations = train_dir / "annotations.jsonl"
    annotations.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in labels),
        encoding="utf-8",
    )
    outputs = []
    for name in ("first.json", "second.json"):
        output = tmp_path / name
        completed = subprocess.run(
            [
                sys.executable,
                "tools/evaluate.py",
                str(annotations),
                "--phase",
                "calibration",
                "--train-manifest",
                str(train_dir / "manifest.json"),
                "--calibration-records",
                str(train_dir / "records.jsonl"),
                "--holdout-manifest",
                str(holdout_dir / "manifest.json"),
                "--output",
                str(output),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert completed.returncode == 0, completed.stderr
        outputs.append(output.read_bytes())
    assert outputs[0] == outputs[1]
    report = json.loads(outputs[0])

    assert report["schema_version"] == 4
    assert report["expression_detection"]["true_positive"] == 1
    assert report["expression_detection"]["false_positive"] == 1
    assert report["expression_detection"]["false_negative"] == 1
    by_rule = report["expression_detection_by_rule"]
    law_rules = set(by_rule)
    assert {"AGE-001", "LOOK-001", "SEX-001", "PHOTO-001"} <= law_rules
    assert by_rule["SEX-001"]["true_positive"] == 1
    assert by_rule["LOOK-001"]["false_positive"] == 1
    assert by_rule["AGE-001"]["false_negative"] == 1
    assert by_rule["PHOTO-001"] == {
        "true_positive": 0,
        "false_positive": 0,
        "false_negative": 0,
        "precision": None,
        "recall": None,
    }
    for key in ("true_positive", "false_positive", "false_negative"):
        assert sum(item[key] for item in by_rule.values()) == report[
            "expression_detection"
        ][key]
    coverage = report["rule_coverage"]
    assert coverage["observed_rule_ids"] == ["AGE-001", "LOOK-001", "SEX-001"]
    assert coverage["coverage_gap_rule_ids"] == sorted(
        law_rules - {"AGE-001", "LOOK-001", "SEX-001"}
    )
    assert "PHOTO-001" in coverage["coverage_gap_rule_ids"]
    assert coverage["coverage_gap_excluded_from_performance_claims"] is True
    # The calibration phase still makes no G1/G2 claim.
    assert report["target_gate"]["applicable"] is False


def test_evaluation_rejects_output_alias_and_writes_atomically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = load_tool("evaluate")
    annotations = tmp_path / "annotations.jsonl"
    output = tmp_path / "report.json"
    annotations.write_text("annotations", encoding="utf-8")
    output.write_text("existing", encoding="utf-8")

    with pytest.raises(ValueError, match="다른 파일"):
        module.validate_output_paths(
            output=annotations,
            receipt=None,
            inputs={"annotations": annotations},
        )

    monkeypatch.setattr(
        os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )
    with pytest.raises(OSError, match="replace failed"):
        module.atomic_write_text(output, "new")
    assert output.read_text(encoding="utf-8") == "existing"


def test_train_calibration_accepts_partial_labels_without_final_receipt(
    tmp_path: Path,
) -> None:
    train_dir = tmp_path / "train"
    holdout_dir = tmp_path / "holdout"
    train_dir.mkdir()
    holdout_dir.mkdir()
    texts = ["일반 사무직 채용", "기술직 채용"]
    hashes = [hashlib.sha256(text.encode()).hexdigest() for text in texts]
    records = [
        {
            "id": f"train:{index}",
            "content_hash": content_hash,
            "text": text,
            "sector": "public" if index == 0 else "private",
            "source": "synthetic-train",
        }
        for index, (text, content_hash) in enumerate(zip(texts, hashes))
    ]
    (train_dir / "manifest.json").write_text(
        json.dumps({"content_hashes": hashes}), encoding="utf-8"
    )
    (train_dir / "records.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
        encoding="utf-8",
    )
    (holdout_dir / "manifest.json").write_text(
        json.dumps({"content_hashes": []}), encoding="utf-8"
    )
    annotations = train_dir / "annotations.jsonl"
    annotations.write_text(
        json.dumps(
            {
                "id": records[0]["id"],
                "content_hash": hashes[0],
                "text": texts[0],
                "expected_findings": [],
                "expected_absent_slots": [],
                "expected_expressions": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "calibration.json"

    completed = subprocess.run(
        [
            sys.executable,
            "tools/evaluate.py",
            str(annotations),
            "--phase",
            "calibration",
            "--train-manifest",
            str(train_dir / "manifest.json"),
            "--calibration-records",
            str(train_dir / "records.jsonl"),
            "--holdout-manifest",
            str(holdout_dir / "manifest.json"),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["evaluation_phase"] == "train_calibration"
    assert report["annotation_coverage"] == 0.5
    assert report["target_gate"]["applicable"] is False
    assert not (holdout_dir / "final-evaluation-receipt.json").exists()


def test_final_evaluation_rejects_partial_holdout_without_writing_evidence(
    tmp_path: Path,
) -> None:
    train_dir = tmp_path / "train"
    holdout_dir = tmp_path / "holdout"
    train_dir.mkdir()
    holdout_dir.mkdir()
    texts = ["채용 공고 하나", "채용 공고 둘"]
    hashes = [hashlib.sha256(text.encode()).hexdigest() for text in texts]
    records = [
        {
            "id": f"holdout:{index}",
            "content_hash": content_hash,
            "text": text,
            "sector": "public",
            "source": "synthetic-holdout",
        }
        for index, (text, content_hash) in enumerate(zip(texts, hashes))
    ]
    (train_dir / "manifest.json").write_text(
        json.dumps({"content_hashes": []}), encoding="utf-8"
    )
    (holdout_dir / "manifest.json").write_text(
        json.dumps({"content_hashes": hashes}), encoding="utf-8"
    )
    (holdout_dir / "records.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
        encoding="utf-8",
    )
    annotations = holdout_dir / "annotations.jsonl"
    annotations.write_text(
        json.dumps(
            {
                "id": records[0]["id"],
                "content_hash": hashes[0],
                "text": texts[0],
                "expected_findings": [],
                "expected_absent_slots": [],
                "expected_expressions": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "evaluation.json"
    receipt = holdout_dir / "final-evaluation-receipt.json"

    completed = subprocess.run(
        [
            sys.executable,
            "tools/evaluate.py",
            str(annotations),
            "--phase",
            "final",
            "--train-manifest",
            str(train_dir / "manifest.json"),
            "--holdout-manifest",
            str(holdout_dir / "manifest.json"),
            "--holdout-records",
            str(holdout_dir / "records.jsonl"),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode != 0
    assert "1" in completed.stderr
    assert "content_hash" in completed.stderr
    assert not output.exists()
    assert not receipt.exists()
