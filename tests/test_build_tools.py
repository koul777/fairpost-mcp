from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET
import zipfile

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "tools" / f"{name}.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_tag_evidence_rejects_ambiguous_head_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_tool("build_release_report")
    responses = iter(
        [
            subprocess.CompletedProcess([], 0, stdout="true\n", stderr=""),
            subprocess.CompletedProcess(
                [],
                0,
                stdout="v1.1.0\nv1.0.0\n",
                stderr="",
            ),
        ]
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: next(responses))

    tag, note = module._release_tag_evidence()

    assert tag is None
    assert "multiple release tags" in note
    assert "v1.0.0" in note and "v1.1.0" in note


def test_release_tag_evidence_reports_missing_head_tag_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_tool("build_release_report")
    responses = iter(
        [
            subprocess.CompletedProcess([], 0, stdout="true\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        ]
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: next(responses))

    tag, note = module._release_tag_evidence()

    assert tag is None
    assert note == "The repository is available, but the current HEAD has no release tag."


def test_build_release_report_accepts_built_at_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = load_tool("build_release_report")
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    sdist_path = dist_dir / "fairpost-test-9.8.7.tar.gz"
    wheel_path = dist_dir / "fairpost_test-9.8.7.whl"
    sdist_path.write_bytes(b"s" * 10)
    wheel_path.write_bytes(b"w" * 20)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fairpost-test"\nversion = "9.8.7"\n',
        encoding="utf-8",
    )
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fairpost": {
                        "type": "http",
                        "url": "http://127.0.0.1:8000/mcp",
                    },
                    "fairpost-remote": {
                        "type": "http",
                        "url": "https://fairmcp.vercel.app/api/mcp",
                        "headers": {
                            "Authorization": "Bearer ${FAIRPOST_MCP_TOKEN}"
                        },
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    (reports_dir / "distribution_audit.json").write_text(
        json.dumps(
            {
                "schema_version": "fairpost-distribution-audit-v2",
                "passed": True,
                "distribution_source_fingerprint": "distribution-test",
                "runtime_source_fingerprint": "runtime-test",
                "sdist": {
                    "path": str(sdist_path),
                    "bytes": 10,
                    "sha256": hashlib.sha256(sdist_path.read_bytes()).hexdigest(),
                    "members": 3,
                },
                "wheel": {
                    "path": str(wheel_path),
                    "bytes": 20,
                    "sha256": hashlib.sha256(wheel_path.read_bytes()).hexdigest(),
                    "members": 4,
                },
            }
        ),
        encoding="utf-8",
    )
    (reports_dir / "web_engine_parity.json").write_text(
        json.dumps(
            {
                "schema_version": "fairpost-web-engine-parity-v1",
                "input": {"records": 7},
                "mismatched_records": 0,
            }
        ),
        encoding="utf-8",
    )
    (reports_dir / "mcp_client_audit.json").write_text(
        json.dumps(
            {
                "schema_version": "fairpost-mcp-client-audit-v2",
                "project_config": {"registered": True},
                "official_inspector": {
                    "tool_call_exit_code": 0,
                    "is_error": False,
                },
            }
        ),
        encoding="utf-8",
    )
    (reports_dir / "work24_access_audit.json").write_text(
        json.dumps(
            {
                "schema_version": "fairpost-work24-access-audit-v1",
                "result": "api_business_error",
            }
        ),
        encoding="utf-8",
    )
    (reports_dir / "vercel_deployment_audit.json").write_text(
        json.dumps(
            {
                "schema_version": "fairpost-vercel-deployment-audit-v3",
                "passed": True,
                "production_url": "https://example.test",
                "health": {
                    "status": "ok",
                    "transport": "streamable-http",
                    "ruleset_version": "ruleset-version",
                    "matching_version": "matching-version",
                    "runtime_source_fingerprint": "runtime-test",
                },
                "checks": {
                    "tool_call_succeeded": True,
                    "structured_tool_call_succeeded": True,
                    "structured_contract_is_traceable": True,
                    "claude_readonly_profile_verified": True,
                    "verification_context_complete": True,
                },
                "verification_context": {
                    "source_commit": "a" * 40,
                    "verified_by": "codex-agent",
                    "approval_ref": "user-approved-poc-2026-08-31",
                },
            }
        ),
        encoding="utf-8",
    )
    (reports_dir / "prd_corpus_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "fairpost-prd-corpus-summary-v1",
                "total": 600,
                "train": 420,
                "holdout": 180,
                "train_holdout_hash_overlap": 0,
            }
        ),
        encoding="utf-8",
    )
    (reports_dir / "human_labeling_handoff.json").write_text(
        json.dumps(
            {
                "schema_version": "fairpost-human-labeling-handoff-v1",
                "status": "awaiting_human_annotations",
                "holdout_records": 180,
            }
        ),
        encoding="utf-8",
    )

    (reports_dir / "evidence_version_audit.json").write_text(
        json.dumps(
            {
                "schema_version": "fairpost-evidence-version-audit-v1",
                "passed": True,
                "reports_checked": 21,
            }
        ),
        encoding="utf-8",
    )
    (reports_dir / "corpus_diversity_audit.json").write_text(
        json.dumps(
            {
                "schema_version": "private-corpus-diversity-audit-v1",
                "status": "alert",
                "release_policy": {"blocking": False},
            }
        ),
        encoding="utf-8",
    )

    class StubRuleset:
        version = "ruleset-version"
        matching_version = "matching-version"
        statutes = [1, 2]
        rules = [
            {"layer": "law"},
            {"layer": "question"},
            {"layer": "question"},
        ]

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "load_ruleset", lambda path: StubRuleset())
    monkeypatch.setattr(
        module,
        "runtime_source_fingerprint",
        lambda **_kwargs: "runtime-test",
    )
    monkeypatch.setattr(
        module,
        "distribution_source_fingerprint",
        lambda _root: "distribution-test",
    )
    monkeypatch.setattr(
        module,
        "_release_tag_evidence",
        lambda: (None, "no release tag"),
    )
    junitxml = tmp_path / "pytest.xml"
    validation_fingerprint = module.validation_source_fingerprint(tmp_path)
    junitxml.write_text(
        '<testsuites><testsuite tests="2" failures="0" errors="0" '
        'skipped="0"><properties><property '
        'name="fairpost_validation_source_fingerprint" '
        f'value="{validation_fingerprint}" /></properties>'
        '<testcase /><testcase /></testsuite></testsuites>',
        encoding="utf-8",
    )

    report = module.build_report(
        junitxml,
        built_at="2026-08-03T23:57:39+09:00",
    )

    assert report["built_at"] == "2026-08-03T23:57:39+09:00"
    assert report["schema_version"] == "fairpost-build-artifact-v2"
    assert report["package"] == "fairpost-test"
    assert report["version"] == "9.8.7"
    assert report["wheel"]["path"] == "dist/fairpost_test-9.8.7.whl"
    assert report["sdist"]["path"] == "dist/fairpost-test-9.8.7.tar.gz"
    assert report["verification"]["tests_passed"] == 2
    assert report["verification"]["test_evidence_bytes"] == junitxml.stat().st_size
    assert report["verification"]["test_evidence_sha256"] == hashlib.sha256(
        junitxml.read_bytes()
    ).hexdigest()
    assert report["verification"]["question_cards"] == 2
    assert report["verification"]["sdist_members"] == 3
    assert report["verification"]["wheel_members"] == 4
    assert report["verification"]["evidence_versions_passed"] is True
    assert report["verification"]["evidence_versions_reports_checked"] == 21
    assert report["verification"]["corpus_diversity_status"] == "alert"
    assert report["verification"]["corpus_diversity_release_blocking"] is False
    assert report["verification"]["claude_project_http_registered"] is True
    assert report["verification"]["mcp_project_config"]["valid"] is True
    assert report["verification"]["vercel_asgi_protocol_test_passed"] is True
    assert report["verification"]["vercel_deployed_ruleset_version"] == (
        "ruleset-version"
    )
    assert report["verification"]["vercel_deployed_matching_version"] == (
        "matching-version"
    )
    assert report["verification"]["vercel_deployment_matches_current_ruleset"] is True
    assert report["verification"][
        "vercel_runtime_source_fingerprint_matches_local"
    ] is True
    assert report["verification"]["vercel_verification_context_complete"] is True
    assert report["verification"]["vercel_contract_checks_passed"] is True
    assert report["verification"]["vercel_verification_context"] == {
        "source_commit": "a" * 40,
        "verified_by": "codex-agent",
        "approval_ref": "user-approved-poc-2026-08-31",
    }
    assert report["verification"]["distribution_matches_current_runtime"] is True
    assert report["verification"]["distribution_matches_current_source"] is True
    assert report["release_readiness"]["status"] == "blocked"
    assert {item["id"] for item in report["release_readiness"]["blockers"]} == {
        "human_holdout_labels",
        "private_corpus_diversity",
        "current_external_client_evidence",
        "work24_source_access",
        "release_tag",
    }


def test_release_report_rejects_incomplete_test_evidence(tmp_path: Path) -> None:
    module = load_tool("build_release_report")
    junitxml = tmp_path / "pytest.xml"
    junitxml.write_text(
        '<testsuites><testsuite tests="10" failures="1" errors="0" '
        'skipped="0" /></testsuites>',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="complete passing test run"):
        module._load_passing_test_count(junitxml)


def test_release_report_rejects_xml_entities() -> None:
    module = load_tool("build_release_report")
    payload = (
        b'<!DOCTYPE testsuites [<!ENTITY injected "unsafe">]>'
        b'<testsuites><testsuite tests="1" failures="0" errors="0" '
        b'skipped="0"><testcase name="&injected;" /></testsuite></testsuites>'
    )

    with pytest.raises(ValueError, match="readable JUnit XML"):
        module._parse_passing_test_count(payload)


@pytest.mark.parametrize(
    ("payload", "expected", "label"),
    [
        ({}, "fairpost-distribution-audit-v2", "distribution audit"),
        (
            {"schema_version": "fairpost-vercel-deployment-audit-v1"},
            "fairpost-vercel-deployment-audit-v3",
            "Vercel deployment audit",
        ),
        ({"schema_version": 2}, 3, "evaluation report"),
        (
            {"schema_version": "fairpost-web-engine-parity-v0"},
            "fairpost-web-engine-parity-v1",
            "web engine parity audit",
        ),
        (
            {},
            "fairpost-work24-access-audit-v1",
            "Work24 access audit",
        ),
        (
            {},
            "fairpost-prd-corpus-summary-v1",
            "PRD corpus summary",
        ),
        (
            {},
            "fairpost-human-labeling-handoff-v1",
            "human labeling handoff",
        ),
    ],
)
def test_release_report_rejects_legacy_input_schemas(
    payload: dict[str, object],
    expected: object,
    label: str,
) -> None:
    module = load_tool("build_release_report")

    with pytest.raises(ValueError, match="unsupported schema_version"):
        module._require_report_schema(payload, expected, label)


def test_release_report_validation_enforces_operational_evidence() -> None:
    module = load_tool("build_release_report")
    verification = {
        "evidence_versions_passed": True,
        "vercel_production_deployed": True,
        "vercel_asgi_protocol_test_passed": True,
        "vercel_verification_context_complete": True,
        "vercel_contract_checks_passed": True,
        "vercel_deployment_matches_current_ruleset": True,
        "vercel_runtime_source_fingerprint_matches_local": True,
        "distribution_audit_passed": True,
        "distribution_matches_current_runtime": True,
        "distribution_matches_current_source": True,
    }
    report = {
        "verification": verification,
        "release_readiness": {
            "strict_ready": False,
            "blockers": [{"id": "human_holdout_labels"}],
        },
    }

    module.validate_release_report(report)
    verification["vercel_asgi_protocol_test_passed"] = False
    with pytest.raises(ValueError, match="protocol audit"):
        module.validate_release_report(report)
    verification["vercel_asgi_protocol_test_passed"] = True
    verification["vercel_verification_context_complete"] = False
    with pytest.raises(ValueError, match="verification context"):
        module.validate_release_report(report)
    verification["vercel_verification_context_complete"] = True
    verification["vercel_contract_checks_passed"] = False
    with pytest.raises(ValueError, match="structured and Claude"):
        module.validate_release_report(report)
    verification["vercel_contract_checks_passed"] = True
    verification["vercel_runtime_source_fingerprint_matches_local"] = False
    with pytest.raises(ValueError, match="runtime source fingerprint"):
        module.validate_release_report(report)
    module.validate_release_report(report, allow_stale_deployment=True)
    with pytest.raises(ValueError, match="human_holdout_labels"):
        module.validate_release_report(
            report,
            allow_stale_deployment=True,
            strict_release=True,
        )


def test_current_client_evidence_is_bound_to_deployment_audit() -> None:
    module = load_tool("build_release_report")
    client = {
        "schema_version": "fairpost-mcp-client-audit-v2",
        "evidence_status": "current",
        "passed": True,
        "ruleset_version": "rules-v1",
        "matching_version": "match-v1",
        "runtime_source_fingerprint": "runtime-v1",
        "deployment_id": "dpl-current",
        "vercel_deployment_audit_sha256": "a" * 64,
        "endpoint": "https://example.test/api/mcp",
        "authentication": "bearer",
        "contains_posting_text": False,
        "synthetic_input_only": True,
        "official_inspector": {
            "tools_list_exit_code": 0,
            "tool_call_exit_code": 0,
            "is_error": False,
            "tool_count": 3,
            "tools": [
                "check_job_posting",
                "check_job_posting_structured",
                "next_review_question",
            ],
            "all_tools_read_only": True,
            "endpoint": "https://example.test/api/mcp",
            "version": "2.4.0",
        },
    }
    arguments = {
        "ruleset_version": "rules-v1",
        "matching_version": "match-v1",
        "runtime_source_fingerprint_value": "runtime-v1",
        "deployment_id": "dpl-current",
        "vercel_audit_sha256": "a" * 64,
        "endpoint": "https://example.test/api/mcp",
        "authentication": "bearer",
    }

    assert module._current_client_evidence_matches(client, **arguments) is True
    client["passed"] = False
    assert module._current_client_evidence_matches(client, **arguments) is False
    client["passed"] = True
    client["deployment_id"] = "dpl-stale"
    assert module._current_client_evidence_matches(client, **arguments) is False
    client["deployment_id"] = "dpl-current"
    client["official_inspector"]["endpoint"] = "https://evil.example/mcp"
    assert module._current_client_evidence_matches(client, **arguments) is False
    client["official_inspector"]["endpoint"] = "https://example.test/api/mcp"
    client["official_inspector"]["version"] = "1.0.0"
    assert module._current_client_evidence_matches(client, **arguments) is False


def test_release_report_rejects_hidden_testcase_failure(tmp_path: Path) -> None:
    module = load_tool("build_release_report")
    junitxml = tmp_path / "pytest.xml"
    junitxml.write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="0">'
        "<testcase><failure /></testcase></testsuite>",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="complete passing test run"):
        module._load_passing_test_count(junitxml)


def test_release_report_rejects_output_evidence_alias(tmp_path: Path) -> None:
    module = load_tool("build_release_report")
    junitxml = tmp_path / "pytest.xml"
    junitxml.write_text("<testsuites />", encoding="utf-8")

    with pytest.raises(ValueError, match="must not overwrite"):
        module.validate_paths(junitxml, junitxml)


def test_release_report_atomic_failure_preserves_previous_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_tool("build_release_report")
    output = tmp_path / "build-artifact.json"
    output.write_text("previous\n", encoding="utf-8")
    monkeypatch.setattr(
        module.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("simulated")),
    )

    with pytest.raises(OSError, match="simulated"):
        module._atomic_write_text(output, "replacement\n")

    assert output.read_text(encoding="utf-8") == "previous\n"


def test_release_report_rejects_junit_for_different_validation_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_tool("build_release_report")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    junitxml = tmp_path / "pytest.xml"
    junitxml.write_text(
        '<testsuite tests="1"><properties><property '
        'name="fairpost_validation_source_fingerprint" value="stale" />'
        '</properties><testcase /></testsuite>',
        encoding="utf-8",
    )
    source = tmp_path / "core" / "engine.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not bound to current validation inputs"):
        module._verify_junit_freshness(junitxml)


def test_validation_source_fingerprint_covers_inputs_and_normalizes_newlines(
    tmp_path: Path,
) -> None:
    module = load_tool("release_inputs")
    source = tmp_path / "core" / "engine.py"
    source.parent.mkdir()
    source.write_bytes(b"value = 1\r\n")
    test_source = tmp_path / "tests" / "test_engine.py"
    test_source.parent.mkdir()
    test_source.write_text("def test_value(): pass\n", encoding="utf-8")
    ignored_doc = tmp_path / "docs" / "note.md"
    ignored_doc.parent.mkdir()
    ignored_doc.write_text("first\n", encoding="utf-8")

    first = module.validation_source_fingerprint(tmp_path)
    assert source in module.validation_inputs(tmp_path)
    assert test_source in module.validation_inputs(tmp_path)
    assert ignored_doc not in module.validation_inputs(tmp_path)

    source.write_bytes(b"value = 1\n")
    ignored_doc.write_text("second\n", encoding="utf-8")
    assert module.validation_source_fingerprint(tmp_path) == first

    test_source.write_text("def test_value(): assert False\n", encoding="utf-8")
    assert module.validation_source_fingerprint(tmp_path) != first


def test_release_report_rechecks_audited_artifact_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_tool("build_release_report")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "package.whl"
    wheel.write_bytes(b"current")
    details = {
        "path": "dist/package.whl",
        "bytes": len(b"current"),
        "sha256": hashlib.sha256(b"audited-old-bytes").hexdigest(),
    }

    with pytest.raises(ValueError, match="no longer matches"):
        module._audited_artifact(details, "wheel")


def test_release_report_rejects_audited_artifact_outside_dist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_tool("build_release_report")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "dist").mkdir()
    outside = tmp_path / "outside.whl"
    payload = b"not-a-distribution-artifact"
    outside.write_bytes(payload)
    details = {
        "path": "outside.whl",
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }

    with pytest.raises(ValueError, match="inside the project dist directory"):
        module._audited_artifact(details, "wheel")


def _write_handoff_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    records = tmp_path / "corpus" / "holdout" / "records.jsonl"
    manifest = tmp_path / "corpus" / "holdout" / "manifest.json"
    labeler = tmp_path / "corpus" / "holdout" / "labeler.html"
    annotations = tmp_path / "corpus" / "holdout" / "annotations.jsonl"
    records.parent.mkdir(parents=True)
    text = "채용 공고 예시"
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    records.write_text(
        json.dumps(
            {"id": "record-1", "content_hash": content_hash, "text": text},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            {"count": 1, "ids": ["record-1"], "content_hashes": [content_hash]}
        ),
        encoding="utf-8",
    )
    labeler_payload = base64.b64encode(
        json.dumps(
            {
                "records": [
                    {"id": "record-1", "content_hash": content_hash, "text": text}
                ],
                "ruleset_version": "rules-v1",
                "matching_version": "matching-v1",
                "evaluation_phase": "sealed_holdout_final",
                "metric_scope": {"question_cards": "pilot_only_not_g1_g2"},
            },
            ensure_ascii=False,
        ).encode("utf-8")
    ).decode("ascii")
    labeler.write_text(
        "<meta http-equiv='Content-Security-Policy' content=\""
        "default-src 'none'; connect-src 'none'; object-src 'none'; "
        "base-uri 'none'; form-action 'none'\">"
        f'<script id="payload" type="application/octet-stream">'
        f"{labeler_payload}</script>",
        encoding="utf-8",
    )
    return records, manifest, labeler, annotations


def test_human_labeling_handoff_verifies_records_and_emits_readable_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_tool("build_human_labeling_handoff")
    records, manifest, labeler, annotations = _write_handoff_fixture(tmp_path)

    class StubRuleset:
        version = "rules-v1"
        matching_version = "matching-v1"

    monkeypatch.setattr(module, "load_ruleset", lambda _path: StubRuleset())
    report = module.build_report(records, manifest, labeler, annotations)

    assert report["schema_version"] == "fairpost-human-labeling-handoff-v1"
    assert report["holdout_records"] == 1
    assert report["ruleset_version"] == "rules-v1"
    assert report["release_claim"] == (
        "G1/G2 성능 목표는 사람이 1건 전체를 라벨링하고 평가 명령이 "
        "통과하기 전까지 미충족입니다."
    )


def test_human_labeling_handoff_rejects_manifest_record_mismatch(
    tmp_path: Path,
) -> None:
    module = load_tool("build_human_labeling_handoff")
    records, manifest, labeler, annotations = _write_handoff_fixture(tmp_path)
    value = json.loads(records.read_text(encoding="utf-8"))
    value["text"] = "변조된 공고"
    records.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="ID·본문 해시"):
        module.build_report(records, manifest, labeler, annotations)


def test_human_labeling_handoff_rejects_output_input_alias(tmp_path: Path) -> None:
    module = load_tool("build_human_labeling_handoff")
    records, manifest, labeler, annotations = _write_handoff_fixture(tmp_path)

    with pytest.raises(ValueError, match="--records"):
        module.validate_paths(records, manifest, labeler, annotations, records)


def test_human_labeling_handoff_atomic_failure_preserves_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_tool("build_human_labeling_handoff")
    output = tmp_path / "handoff.json"
    output.write_text("previous\n", encoding="utf-8")
    monkeypatch.setattr(
        module.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("simulated")),
    )

    with pytest.raises(OSError, match="simulated"):
        module._atomic_write_text(output, "replacement\n")

    assert output.read_text(encoding="utf-8") == "previous\n"


def test_web_bundle_is_current() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/export_web_bundle.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_statute_snapshot_hashes_are_current() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/build_statutes.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_recruitment_procedure_snapshot_contains_scope_and_notice_articles() -> None:
    payload = yaml.safe_load(
        (ROOT / "data" / "statutes" / "recruitment-procedure-act.yaml").read_text(
            encoding="utf-8"
        )
    )
    articles = payload["articles"]

    assert "상시 30명 이상" in articles["제3조"]["text"]
    assert "구직자에게 채용 여부를 알려야" in articles["제10조"]["text"]


def test_official_statute_article_parser_preserves_order_and_effective_date() -> None:
    module = load_tool("build_statutes")
    root = ET.fromstring(
        """
<법령>
  <기본정보><법령ID>123456</법령ID></기본정보>
  <조문>
    <조문단위>
      <조문번호>4</조문번호>
      <조문가지번호>3</조문가지번호>
      <조문여부>조문</조문여부>
      <조문제목>테스트 조문</조문제목>
      <조문시행일자>20260701</조문시행일자>
      <조문내용>제4조의3(테스트 조문) 본문</조문내용>
      <항><항내용>① 첫째 항</항내용><호><호내용>1. 첫째 호</호내용></호></항>
      <조문참고자료>[본조신설 2026.1.1]</조문참고자료>
    </조문단위>
  </조문>
</법령>
""".strip()
    )
    articles, official_id = module.official_articles(root, {"제4조의3"})
    assert official_id == "123456"
    assert articles["제4조의3"]["effective_date"] == "2026-07-01"
    assert articles["제4조의3"]["text"] == (
        "제4조의3(테스트 조문) 본문\n\n"
        "① 첫째 항\n\n"
        "1. 첫째 호\n\n"
        "[본조신설 2026.1.1]"
    )
    assert articles["제4조의3"]["hash"] == module.article_hash(
        articles["제4조의3"]["text"]
    )


def test_statute_audit_maps_articles_to_affected_rule_ids(tmp_path: Path) -> None:
    module = load_tool("build_statutes")
    rules_path = tmp_path / "law.yaml"
    rules_path.write_text(
        """
- id: TEST-002
  basis: {type: statute, statute_id: test-act, article: 제2조}
- id: TEST-001
  basis: {type: statute, statute_id: test-act, article: 제2조}
- id: QUESTION-001
  basis: {type: consensus}
""".strip(),
        encoding="utf-8",
    )

    assert module._rule_impact(rules_path) == {
        ("test-act", "제2조"): ["TEST-001", "TEST-002"]
    }


def test_evaluation_rejects_text_hash_mismatch(tmp_path: Path) -> None:
    module = load_tool("evaluate")
    path = tmp_path / "annotations.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "test:1",
                "content_hash": "0" * 64,
                "text": "공고문",
                "expected_findings": [],
                "expected_absent_slots": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="일치하지 않습니다"):
        module.load_jsonl(path)


def test_evaluation_requires_complete_holdout_by_default(tmp_path: Path) -> None:
    text_one = "첫 번째 공고"
    text_two = "두 번째 공고"
    hash_one = hashlib.sha256(text_one.encode("utf-8")).hexdigest()
    hash_two = hashlib.sha256(text_two.encode("utf-8")).hexdigest()
    train_manifest = tmp_path / "train.json"
    holdout_manifest = tmp_path / "holdout.json"
    holdout_records = tmp_path / "records.jsonl"
    annotations = tmp_path / "annotations.jsonl"
    train_manifest.write_text('{"content_hashes":[]}', encoding="utf-8")
    holdout_manifest.write_text(
        json.dumps({"content_hashes": [hash_one, hash_two]}),
        encoding="utf-8",
    )
    holdout_records.write_text(
        "\n".join(
            json.dumps(record, ensure_ascii=False)
            for record in (
                {
                    "id": "test:1",
                    "content_hash": hash_one,
                    "text": text_one,
                    "sector": "public",
                    "source": "test-public",
                },
                {
                    "id": "test:2",
                    "content_hash": hash_two,
                    "text": text_two,
                    "sector": "private",
                    "source": "test-private",
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    annotations.write_text(
        json.dumps(
            {
                "id": "test:1",
                "content_hash": hash_one,
                "text": text_one,
                "expected_findings": [],
                "expected_absent_slots": [],
                "expected_expressions": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "tools/evaluate.py",
            str(annotations),
            "--train-manifest",
            str(train_manifest),
            "--holdout-manifest",
            str(holdout_manifest),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert completed.returncode != 0
    assert "홀드아웃 전체 라벨" in completed.stderr


def test_evaluation_metrics_are_undefined_without_denominators() -> None:
    module = load_tool("evaluate")

    result = module.metrics(0, 0, 0)

    assert result["precision"] is None
    assert result["recall"] is None


def test_evaluation_requires_expression_labels_to_match_findings(
    tmp_path: Path,
) -> None:
    module = load_tool("evaluate")
    text = "라벨 일관성 테스트"
    path = tmp_path / "annotations.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "test:1",
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text": text,
                "expected_findings": ["SEX-001"],
                "expected_absent_slots": [],
                "expected_expressions": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected_findings와"):
        module.load_jsonl(path)


def test_evaluation_target_gate_requires_90_records_per_sector() -> None:
    module = load_tool("evaluate")
    report = {
        "complete_holdout": True,
        "expression_detection": {"precision": 0.95, "recall": 0.50},
        "absence_detection": {"precision": 0.90, "recall": 0.90},
    }

    gate = module._target_gate(
        report,
        module.Counter({"public": 89, "private": 90}),
    )

    assert gate["passed"] is False
    assert gate["checks"]["public_holdout_at_least_90"] is False
    assert gate["checks"]["private_holdout_at_least_90"] is True


def test_holdout_records_must_exactly_match_manifest(tmp_path: Path) -> None:
    module = load_tool("evaluate")
    text = "홀드아웃 원문"
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    missing_hash = "f" * 64
    path = tmp_path / "records.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "test:1",
                "content_hash": content_hash,
                "text": text,
                "sector": "public",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="누락 1"):
        module.load_holdout_records(path, {content_hash, missing_hash})


def test_evaluation_enforces_targets_on_complete_two_sector_holdout(
    tmp_path: Path,
) -> None:
    from core import FairpostEngine

    engine = FairpostEngine()
    train_manifest = tmp_path / "train.json"
    holdout_manifest = tmp_path / "manifest.json"
    holdout_records = tmp_path / "records.jsonl"
    annotations = tmp_path / "annotations.jsonl"
    human_attestation = tmp_path / "human-attestation.json"
    output = tmp_path / "evaluation.json"
    records = []
    labels = []
    for index in range(180):
        sector = "public" if index < 90 else "private"
        text = (
            "지원자격: 남성만 지원 가능"
            if index == 0
            else f"일반 채용공고 {index}"
        )
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        record_id = f"{sector}:{index}"
        result = engine.check(text)
        finding_ids = [finding.id for finding in result.findings]
        records.append(
            {
                "id": record_id,
                "content_hash": content_hash,
                "text": text,
                "sector": sector,
                "source": f"synthetic-{sector}",
            }
        )
        labels.append(
            {
                "id": record_id,
                "content_hash": content_hash,
                "text": text,
                "expected_findings": finding_ids,
                "expected_absent_slots": [
                    slot.slot for slot in result.slots if not slot.found
                ],
                "expected_expressions": [
                    {"rule_id": rule_id} for rule_id in finding_ids
                ],
            }
        )
    hashes = [record["content_hash"] for record in records]
    train_manifest.write_text('{"content_hashes":[]}', encoding="utf-8")
    holdout_manifest.write_text(
        json.dumps({"content_hashes": hashes}),
        encoding="utf-8",
    )
    holdout_records.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False) + "\n" for record in records
        ),
        encoding="utf-8",
    )
    annotations.write_text(
        "".join(
            json.dumps(label, ensure_ascii=False) + "\n" for label in labels
        ),
        encoding="utf-8",
    )
    human_attestation.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "attestation": "human_gold",
                "prediction_blinded": True,
                "ai_generated_labels": False,
                "reviewer_ids": ["test-human-reviewer"],
                "annotations_sha256": hashlib.sha256(
                    annotations.read_bytes()
                ).hexdigest(),
                "holdout_manifest_sha256": hashlib.sha256(
                    holdout_manifest.read_bytes()
                ).hexdigest(),
                "holdout_records_sha256": hashlib.sha256(
                    holdout_records.read_bytes()
                ).hexdigest(),
                "ruleset_version": engine.ruleset.version,
                "matching_version": engine.ruleset.matching_version,
                "attested_at": "2026-08-30T00:00:00+09:00",
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "tools/evaluate.py",
            str(annotations),
            "--train-manifest",
            str(train_manifest),
            "--holdout-manifest",
            str(holdout_manifest),
            "--holdout-records",
            str(holdout_records),
            "--human-attestation",
            str(human_attestation),
            "--enforce-targets",
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
    assert report["target_gate"]["passed"] is True
    assert report["holdout_by_sector"] == {"private": 90, "public": 90}
    assert report["measurement_units"]["expression_detection"] == (
        "posting_rule_pair"
    )


def test_annotation_ui_is_local_and_embeds_exact_holdout(tmp_path: Path) -> None:
    module = load_tool("build_annotation_ui")
    holdout = tmp_path / "holdout"
    holdout.mkdir()
    text = "비식별화된 봉인 공고문"
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    records_path = holdout / "records.jsonl"
    manifest_path = holdout / "manifest.json"
    records_path.write_text(
        json.dumps(
            {
                "id": "test:holdout:1",
                "content_hash": content_hash,
                "text": text,
                "sector": "public",
                "occupation": "office",
                "employment_type": "regular",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps({"content_hashes": [content_hash]}),
        encoding="utf-8",
    )

    records = module.load_records(records_path, manifest_path)
    html = module.build_html(module.build_payload(records))
    assert "connect-src 'none'" in html
    assert "http://" not in html
    assert "https://" not in html
    assert text not in html

    encoded = re.search(
        r'<script id="payload" type="application/octet-stream">([^<]+)</script>',
        html,
    )
    assert encoded
    payload = json.loads(base64.b64decode(encoded.group(1)).decode("utf-8"))
    assert payload["records"] == records
    assert len(payload["law_rules"]) >= 15
    assert len(payload["slots"]) == 11


def test_annotation_ui_rejects_non_holdout_input(tmp_path: Path) -> None:
    module = load_tool("build_annotation_ui")
    train = tmp_path / "train"
    train.mkdir()
    with pytest.raises(ValueError, match="holdout 경로"):
        module.load_records(train / "records.jsonl", train / "manifest.json")


def test_combiner_preserves_fixed_public_and_private_partitions(
    tmp_path: Path,
) -> None:
    module = load_tool("combine_corpora")

    def record(record_id: str, text: str, sector: str) -> dict[str, str]:
        return {
            "id": record_id,
            "text": text,
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "source": f"{sector}-source",
            "sector": sector,
            "occupation": "office",
            "employment_type": "regular",
        }

    def write_corpus(
        root: Path,
        train: list[dict[str, str]],
        holdout: list[dict[str, str]],
    ) -> None:
        for split, records in (("train", train), ("holdout", holdout)):
            directory = root / split
            directory.mkdir(parents=True)
            module._write_jsonl(directory / "records.jsonl", records)
            module._write_manifest(directory / "manifest.json", records)

    public_dir = tmp_path / "public"
    private_dir = tmp_path / "private"
    public_train = record("public:train", "공공 학습", "public")
    public_holdout = record("public:holdout", "공공 홀드아웃", "public")
    private_train = record("private:train", "민간 학습", "private")
    private_holdout = record("private:holdout", "민간 홀드아웃", "private")
    write_corpus(public_dir, [public_train], [public_holdout])
    write_corpus(private_dir, [private_train], [private_holdout])

    train, holdout, summary = module.combine(
        public_dir,
        private_dir,
        expected_public=2,
        expected_private=2,
        train_ratio=0.5,
    )
    assert {item["id"] for item in train} == {
        "public:train",
        "private:train",
    }
    assert {item["id"] for item in holdout} == {
        "public:holdout",
        "private:holdout",
    }
    assert summary["sectors"] == {"private": 2, "public": 2}
    assert summary["combined_from_fixed_partitions"] is True


def test_combiner_rejects_occupation_outside_prd_four_classes(
    tmp_path: Path,
) -> None:
    module = load_tool("combine_corpora")
    root = tmp_path / "corpus"
    text = "직군 미분류"
    record = {
        "id": "test:1",
        "text": text,
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "source": "test",
        "sector": "public",
        "occupation": "other",
        "employment_type": "regular",
    }
    directory = root / "train"
    directory.mkdir(parents=True)
    module._write_jsonl(directory / "records.jsonl", [record])
    module._write_manifest(directory / "manifest.json", [record])
    with pytest.raises(ValueError, match="허용되지 않은 직군"):
        module.read_partition(root, "train")


def test_prd_corpus_selection_is_exact_stratified_and_text_independent() -> None:
    module = load_tool("build_prd_corpus")
    records = []
    occupations = ("office", "tech", "research", "field")
    employment_types = ("regular", "temporary")
    for index in range(80):
        records.append(
            {
                "id": f"private:{index}",
                "occupation": occupations[index % len(occupations)],
                "employment_type": employment_types[
                    (index // len(occupations)) % len(employment_types)
                ],
                "text": f"선택에 사용하면 안 되는 원문 {index}",
            }
        )

    first = module.select_stratified(records, 24)
    modified = [
        {**record, "text": f"완전히 다른 원문 {record['id']}"}
        for record in records
    ]
    second = module.select_stratified(modified, 24)

    assert len(first) == 24
    assert [record["id"] for record in first] == [
        record["id"] for record in second
    ]
    assert {
        (record["occupation"], record["employment_type"])
        for record in first
    } == {
        (record["occupation"], record["employment_type"])
        for record in records
    }


def test_prd_corpus_selection_rejects_invalid_target() -> None:
    module = load_tool("build_prd_corpus")
    records = [
        {
            "id": "private:1",
            "occupation": "office",
            "employment_type": "regular",
        }
    ]
    with pytest.raises(ValueError, match="선택 목표"):
        module.select_stratified(records, 0)
    with pytest.raises(ValueError, match="선택 목표"):
        module.select_stratified(records, 2)


def test_reclassifier_preserves_ids_hashes_and_fixed_membership(
    tmp_path: Path,
) -> None:
    combine = load_tool("combine_corpora")
    reclassify = load_tool("reclassify_corpus")
    root = tmp_path / "corpus"
    summary_path = tmp_path / "summary.json"

    def record(record_id: str, text: str, split: str) -> dict[str, str]:
        return {
            "id": record_id,
            "text": text,
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "source": "test",
            "sector": "public" if split == "train" else "private",
            "occupation": "other",
            "employment_type": "regular",
        }

    original = {
        "train": [
            record("train:office", "행정 사무 담당", "train"),
            record("train:tech", "소프트웨어 개발 담당", "train"),
            record("train:research", "연구 실험 담당", "train"),
            record("train:field", "생산 현장 담당", "train"),
        ],
        "holdout": [
            record("holdout:1", "직무 상세 미기재", "holdout"),
        ],
    }
    for split, records in original.items():
        directory = root / split
        directory.mkdir(parents=True)
        combine._write_jsonl(directory / "records.jsonl", records)
        combine._write_manifest(directory / "manifest.json", records)

    result = reclassify.migrate(root, summary_path)
    migrated_train = combine.read_partition(root, "train")
    migrated_holdout = combine.read_partition(root, "holdout")

    assert [item["id"] for item in migrated_train] == [
        item["id"] for item in original["train"]
    ]
    assert [item["id"] for item in migrated_holdout] == [
        item["id"] for item in original["holdout"]
    ]
    assert [item["content_hash"] for item in migrated_train] == [
        item["content_hash"] for item in original["train"]
    ]
    assert result["occupations"] == {
        "field": 2,
        "office": 1,
        "research": 1,
        "tech": 1,
    }
    assert json.loads(summary_path.read_text(encoding="utf-8"))[
        "reclassified_preserving_fixed_partitions"
    ] is True


def test_vercel_configuration_excludes_private_inputs() -> None:
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert config["functions"]["api/index.py"]["maxDuration"] == 30
    rewrites = {
        item["source"]: item["destination"] for item in config["rewrites"]
    }
    assert rewrites == {
        "/api/claude-mcp": "/api",
        "/api/health": "/api",
        "/api/mcp": "/api",
    }
    assert (ROOT / "index.html").is_file()
    landing = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "내 Vercel에 이 MCP 배포" in landing
    assert "https://vercel.com/new/clone?repository-url=" in landing
    assert "FAIRPOST_MCP_TOKEN" in landing
    assert "공개 저장소를 복제" in landing
    assert "운영 기본값은 두 MCP 경로 모두 Bearer 필수" in landing
    assert "공정성ㆍ법률ㆍ합격 여부를 판정하지 않습니다." in landing
    assert "답변 저장과 이어서 검토하기는 루프백 로컬 MCP" in landing
    assert "읽기 전용 점검에는 토큰이 필요하지 않습니다" not in landing
    ignored = (ROOT / ".vercelignore").read_text(encoding="utf-8")
    for private_path in (
        ".env",
        ".agents/",
        ".claude/",
        ".corpus*/",
        ".corpus*",
        ".git/",
        ".mcp.json",
        ".mypy_cache/",
        ".private-review/",
        ".ruff_cache/",
        ".vercel/",
        ".coverage",
        "*.log",
        "answers.json",
        "data/local_rules.yaml",
    ):
        assert private_path in ignored


def test_data_validator_accepts_committed_dictionaries() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/validate_data.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert completed.returncode == 0, completed.stderr


def test_data_validator_checks_private_corpus_provenance(tmp_path: Path) -> None:
    module = load_tool("validate_data")
    public = tmp_path / "public.json"
    private = tmp_path / "private.json"
    public.write_text(
        json.dumps(
            {
                "matching_version": "test-matching-version",
                "law_rule_posting_hits": {"TEST-001": 0},
            }
        ),
        encoding="utf-8",
    )
    private.write_text(
        json.dumps(
            {
                "matching_version": "test-matching-version",
                "law_rule_posting_hits": {"TEST-001": 3},
            }
        ),
        encoding="utf-8",
    )
    rule = {
        "id": "TEST-001",
        "layer": "law",
        "provenance": {"corpus_split": "private", "corpus_hits": 2},
    }

    with pytest.raises(ValueError, match="private 코퍼스 집계"):
        module.validate_corpus_hits(
            (rule,),
            "test-matching-version",
            public,
            private,
        )


def test_corpus_reports_survive_statute_only_ruleset_change(tmp_path: Path) -> None:
    module = load_tool("validate_data")
    public = tmp_path / "public.json"
    private = tmp_path / "private.json"
    for path in (public, private):
        path.write_text(
            json.dumps(
                {
                    "ruleset_version": "older-full-ruleset-version",
                    "matching_version": "stable-matching-version",
                    "law_rule_posting_hits": {"TEST-001": 0},
                }
            ),
            encoding="utf-8",
        )
    rule = {
        "id": "TEST-001",
        "layer": "law",
        "provenance": {"corpus_split": "public", "corpus_hits": 0},
    }

    module.validate_corpus_hits(
        (rule,),
        "stable-matching-version",
        public,
        private,
    )


def test_public_reports_directory_contains_no_raw_jsonl() -> None:
    assert not list((ROOT / "reports").glob("*.jsonl"))


def test_project_mcp_config_uses_local_default_and_remote_opt_in() -> None:
    config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))

    assert config == {
        "mcpServers": {
            "fairpost": {
                "type": "http",
                "url": "http://127.0.0.1:8000/mcp",
            },
            "fairpost-remote": {
                "type": "http",
                "url": "https://fairmcp.vercel.app/api/mcp",
                "headers": {
                    "Authorization": "Bearer ${FAIRPOST_MCP_TOKEN}",
                },
            },
        }
    }


def test_release_report_mcp_config_preserves_local_and_remote_evidence() -> None:
    module = load_tool("build_release_report")

    assert module._project_mcp_config() == {
        "valid": True,
        "default_server": "fairpost",
        "remote_server": "fairpost-remote",
        "local_first": True,
        "remote_url": "https://fairmcp.vercel.app/api/mcp",
        "local_url": "http://127.0.0.1:8000/mcp",
        "authorization_uses_environment_reference": True,
    }


def test_release_report_mcp_config_rejects_remote_as_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_tool("build_release_report")
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fairpost": {
                        "type": "http",
                        "url": "https://fairmcp.vercel.app/api/mcp",
                        "headers": {
                            "Authorization": "Bearer ${FAIRPOST_MCP_TOKEN}"
                        },
                    },
                    "fairpost-local": {
                        "type": "http",
                        "url": "http://127.0.0.1:8000/mcp",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "ROOT", tmp_path)

    evidence = module._project_mcp_config()

    assert evidence["valid"] is False
    assert evidence["local_first"] is False
    assert evidence["local_url"] == "https://fairmcp.vercel.app/api/mcp"
    assert evidence["remote_url"] is None
    assert evidence["authorization_uses_environment_reference"] is False


def test_distribution_audit_rejects_private_build_artifacts() -> None:
    module = load_tool("verify_distribution")
    assert {
        "docs/question-relevance-audit.md",
        "docs/private-fairness-monitoring.md",
        "docs/private-fairness-research-bundle.json",
        "reports/question_relevance_audit.json",
        "reports/question_relevance_manual_review.json",
        "reports/private_fairness_audit.json",
        "reports/private_age_context_audit.json",
        "reports/private_family_evidence_context_audit.json",
        "reports/private_result_notice_context_audit.json",
        "reports/private_gender_context_audit.json",
        "reports/private_return_context_audit.json",
        "reports/private_nationality_context_audit.json",
        "reports/private_religion_context_audit.json",
        "reports/private_marital_context_audit.json",
        "reports/private_criminal_record_context_audit.json",
        "reports/private_residence_vehicle_context_audit.json",
        "reports/private_military_proxy_context_audit.json",
        "reports/private_preference_process_context_audit.json",
        "reports/private_health_job_relevance_context_audit.json",
        "reports/private_review_sampling_audit.json",
        "tools/run_private_fairness_cycle.py",
        "tools/build_private_review_ui.py",
        "tools/summarize_private_review.py",
        "tests/fixtures/private_fairness_cases.json",
    } <= module.SDIST_REQUIRED
    names = {
        "README.md",
        "reports/summary.json",
        "reports/private_open_candidate_batches.jsonl",
        "reports/build_artifact.json",
        "reports/distribution_audit.json",
        ".corpus-final/train/records.jsonl",
        ".private-review/queue.jsonl",
        ".env",
    }

    violations = module._forbidden(names)

    assert ".env" in violations
    assert ".corpus-final/train/records.jsonl" in violations
    assert ".private-review/queue.jsonl" in violations
    assert "reports/build_artifact.json" in violations
    assert "reports/distribution_audit.json" in violations
    assert "reports/private_open_candidate_batches.jsonl" in violations


def test_distribution_source_fingerprint_binds_docs_and_normalizes_line_endings(
    tmp_path: Path,
) -> None:
    module = load_tool("verify_distribution")
    roadmap = tmp_path / "docs" / "roadmap.md"
    roadmap.parent.mkdir()
    roadmap.write_bytes(b"first\r\nsecond\r\n")
    first = module.distribution_source_fingerprint(tmp_path)

    roadmap.write_bytes(b"first\nsecond\n")
    assert module.distribution_source_fingerprint(tmp_path) == first

    roadmap.write_bytes(b"first\nchanged\n")
    assert module.distribution_source_fingerprint(tmp_path) != first

    for relative in (
        ".vercelignore",
        "LICENSE",
        "LICENSE-DATA",
        "MANIFEST.in",
        "examples/pilot_feedback.example.jsonl",
        "tests/js_runner.cjs",
    ):
        text_source = tmp_path / relative
        text_source.parent.mkdir(parents=True, exist_ok=True)
        text_source.write_bytes(b"first\r\nsecond\r\n")
        crlf_fingerprint = module.distribution_source_fingerprint(tmp_path)
        text_source.write_bytes(b"first\nsecond\n")
        assert module.distribution_source_fingerprint(tmp_path) == crlf_fingerprint

    before_test = module.distribution_source_fingerprint(tmp_path)
    test_source = tmp_path / "tests" / "test_release.py"
    test_source.parent.mkdir(exist_ok=True)
    test_source.write_text("def test_release(): pass\n", encoding="utf-8")
    with_test = module.distribution_source_fingerprint(tmp_path)
    assert with_test != before_test
    test_source.write_text("# reviewed\ndef test_release(): pass\n", encoding="utf-8")
    with_comment = module.distribution_source_fingerprint(tmp_path)
    assert with_comment != with_test
    test_source.write_text("def test_release(): assert False\n", encoding="utf-8")
    assert module.distribution_source_fingerprint(tmp_path) != with_comment


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (("AKIA" + "A" * 16).encode(), "aws_access_key"),
        (("gh" + "p_" + "a" * 24).encode(), "github_token"),
        (("sk-" + "a" * 24).encode(), "api_key"),
        (("Bearer " + "a" * 24).encode(), "bearer_token"),
        (("-----BEGIN " + "PRIVATE KEY-----").encode(), "pem_private_key"),
        (
            ("eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12).encode(),
            "jwt",
        ),
        (
            ("postgresql://" + "user:secret@db.example/database").encode(),
            "credentialed_dsn",
        ),
    ],
)
def test_distribution_credential_scan_detects_secret_shapes_without_echoing_values(
    payload: bytes,
    expected: str,
) -> None:
    module = load_tool("verify_distribution")

    assert module._credential_kinds(payload) == [expected]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"person@company.example", "email"),
        (b"010-1234-5678", "korean_phone"),
        (b"900101-1234567", "resident_registration_number"),
        (b"jincheon-jobs:private-record-1", "private_record_id"),
    ],
)
def test_distribution_privacy_scan_detects_sensitive_asset_values(
    payload: bytes,
    expected: str,
) -> None:
    module = load_tool("verify_distribution")

    assert module._privacy_kinds(payload) == [expected]


def test_distribution_privacy_asset_scope_covers_packaged_data() -> None:
    module = load_tool("verify_distribution")

    assert module._is_privacy_asset("docs/evidence.json") is True
    assert module._is_privacy_asset("examples/input.jsonl") is True
    assert module._is_privacy_asset("data/rules/law.yaml") is True
    assert module._is_privacy_asset("web/data.js") is True
    assert module._is_privacy_asset("tools/evaluate.py") is False


def test_distribution_privacy_allowlist_is_exact_and_asset_scoped() -> None:
    module = load_tool("verify_distribution")

    reviewed = b"02-1234-5678 / recruit@example.com"
    assert module._privacy_kinds(
        module._privacy_scan_payload("web/app.js", reviewed)
    ) == []
    assert module._privacy_kinds(
        module._privacy_scan_payload("docs/contact.md", reviewed)
    ) == ["email", "korean_phone"]
    assert module._privacy_kinds(
        module._privacy_scan_payload("web/app.js", b"010-9876-5432")
    ) == ["korean_phone"]


def test_sdist_privacy_scan_fails_closed_for_oversized_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_tool("verify_distribution")
    monkeypatch.setattr(module, "MAX_SECURITY_SCAN_BYTES", 8)
    archive_path = tmp_path / "package.tar.gz"
    payload = b"123456789"
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo("fairpost-0.3.0/docs/evidence.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    with tarfile.open(archive_path, "r:gz") as archive:
        findings = module._tar_asset_privacy_findings(archive)

    assert findings == [
        {
            "member": "fairpost-0.3.0/docs/evidence.json",
            "kinds": ["oversized_unscanned_member"],
        }
    ]


def test_wheel_privacy_scan_fails_closed_for_oversized_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_tool("verify_distribution")
    monkeypatch.setattr(module, "MAX_SECURITY_SCAN_BYTES", 8)
    archive_path = tmp_path / "package.whl"
    member = "fairpost-0.3.0.data/data/share/fairpost/docs/evidence.json"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(member, b"123456789")

    with zipfile.ZipFile(archive_path) as archive:
        findings = module._zip_asset_privacy_findings(archive)

    assert findings == [
        {"member": member, "kinds": ["oversized_unscanned_member"]}
    ]


@pytest.mark.parametrize(
    "payload",
    [
        b"Bearer ${FAIRPOST_MCP_TOKEN}",
        b"Bearer <token>",
        b"sk-short-example",
    ],
)
def test_distribution_credential_scan_allows_placeholders(payload: bytes) -> None:
    module = load_tool("verify_distribution")

    assert module._credential_kinds(payload) == []


def test_distribution_report_privacy_scan_reports_paths_without_values() -> None:
    module = load_tool("verify_distribution")
    secret_text = "sensitive posting body"
    payload = {
        "nested": {
            "text": secret_text,
            "contains_posting_text": False,
        }
    }

    pointers = module._sensitive_json_pointers(payload)

    assert pointers == ["/nested/text"]
    assert secret_text not in json.dumps(pointers)


@pytest.mark.parametrize(("left_name", "right_name"), [("sdist", "output"), ("wheel", "output"), ("sdist", "wheel")])
def test_distribution_audit_rejects_identical_paths(
    tmp_path: Path,
    left_name: str,
    right_name: str,
) -> None:
    module = load_tool("verify_distribution")
    paths = {
        "sdist": tmp_path / "package.tar.gz",
        "wheel": tmp_path / "package.whl",
        "output": tmp_path / "audit.json",
    }
    paths[left_name] = paths[right_name]

    with pytest.raises(ValueError, match="같거나 같은 파일"):
        module._validate_paths(paths["sdist"], paths["wheel"], paths["output"])


def test_distribution_audit_rejects_hardlink_alias(tmp_path: Path) -> None:
    module = load_tool("verify_distribution")
    sdist = tmp_path / "package.tar.gz"
    sdist.write_bytes(b"archive")
    output = tmp_path / "audit.json"
    try:
        output.hardlink_to(sdist)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    with pytest.raises(ValueError, match="sdist와 output"):
        module._validate_paths(sdist, tmp_path / "package.whl", output)

    assert sdist.read_bytes() == b"archive"


def test_distribution_audit_cli_rejects_output_archive_before_inspection(
    tmp_path: Path,
) -> None:
    module = load_tool("verify_distribution")
    sdist = tmp_path / "package.tar.gz"
    sdist.write_bytes(b"archive must survive")

    with pytest.raises(SystemExit) as exc_info:
        module.main(
            [
                "--sdist",
                str(sdist),
                "--wheel",
                str(tmp_path / "package.whl"),
                "--output",
                str(sdist),
            ]
        )

    assert exc_info.value.code == 2
    assert sdist.read_bytes() == b"archive must survive"


def test_distribution_audit_atomic_write_preserves_existing_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_tool("verify_distribution")
    output = tmp_path / "audit.json"
    output.write_text("previous\n", encoding="utf-8")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated publish failure")

    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated publish failure"):
        module._atomic_write_text(output, "replacement\n")

    assert output.read_text(encoding="utf-8") == "previous\n"
    assert list(tmp_path.glob(".audit.json.*.tmp")) == []


def test_private_fairness_research_bundle_has_traceable_references() -> None:
    bundle = json.loads(
        (ROOT / "docs" / "private-fairness-research-bundle.json").read_text(
            encoding="utf-8"
        )
    )
    matrix = json.loads(
        (ROOT / "reports" / "private_fairness_case_matrix.json").read_text(
            encoding="utf-8"
        )
    )
    source_ids = [source["source_id"] for source in bundle["sources"]]
    claim_ids = [claim["claim_id"] for claim in bundle["claims"]]

    assert len(source_ids) == len(set(source_ids))
    assert len(claim_ids) == len(set(claim_ids))
    assert bundle["privacy_boundary"]["contains_posting_text"] is False
    assert bundle["privacy_boundary"]["contains_corpus_record_ids"] is False
    assert bundle["privacy_boundary"]["contains_public_evidence_urls"] is True
    assert all(
        source["url"].startswith("https://")
        for source in bundle["sources"]
        if "url" in source
    )
    for source in bundle["sources"]:
        local_reference = source.get("local_reference")
        if local_reference is not None:
            assert not local_reference.startswith((".corpus", ".private-review"))
            assert (ROOT / local_reference).is_file()
    known_sources = set(source_ids)
    for claim in bundle["claims"]:
        assert claim["source_ids"]
        assert set(claim["source_ids"]) <= known_sources
        assert claim["relation"] in {"supports", "context_only"}
        assert claim["confidence"] in {"high", "medium"}
    for family in matrix["case_families"]:
        assert set(family["research_source_ids"]) <= known_sources
    implemented = sum(
        family["decision"].startswith("implemented")
        for family in matrix["case_families"]
    )
    assert matrix["summary"]["case_family_count"] == len(matrix["case_families"])
    assert matrix["summary"]["implemented"] == implemented
    assert matrix["summary"]["covered_by_existing_review"] == (
        len(matrix["case_families"])
        - implemented
        - matrix["summary"]["backlog"]
    )
    assert matrix["privacy_boundary"]["contains_source_ids"] is False
    assert matrix["privacy_boundary"]["contains_research_source_references"] is True
    for family in matrix["case_families"]:
        evidence_path = family.get("internal_evidence_report")
        if evidence_path is not None:
            assert (ROOT / evidence_path).is_file()


def test_private_review_sampling_report_is_internally_consistent() -> None:
    report = json.loads(
        (ROOT / "reports" / "private_review_sampling_audit.json").read_text(
            encoding="utf-8"
        )
    )
    audit = json.loads(
        (ROOT / "reports" / "private_fairness_audit.json").read_text(
            encoding="utf-8"
        )
    )
    sampling = report["rule_sampling"]
    selection = report["selection"]

    assert report["ruleset_version"] == audit["ruleset_version"]
    assert report["matching_version"] == audit["matching_version"]
    assert report["input"]["records"] == audit["input"]["records"]
    assert selection["selected_rule_count"] == len(sampling)
    for field in (
        "candidate_matches",
        "unique_contexts",
        "selected_rows",
        "collapsed_duplicate_contexts",
        "truncated_unique_contexts",
    ):
        assert selection[field] == sum(value[field] for value in sampling.values())
    for value in sampling.values():
        assert value["selected_rows"] <= value["unique_contexts"]
        assert value["unique_contexts"] <= value["candidate_matches"]
        assert value["collapsed_duplicate_contexts"] == (
            value["candidate_matches"] - value["unique_contexts"]
        )
        assert value["truncated_unique_contexts"] == (
            value["unique_contexts"] - value["selected_rows"]
        )
    assert all(value is False for value in report["privacy_boundary"].values())


def test_private_criminal_record_context_audit_is_anonymous_and_consistent() -> None:
    report = json.loads(
        (ROOT / "reports" / "private_criminal_record_context_audit.json").read_text(
            encoding="utf-8"
        )
    )
    audit = json.loads(
        (ROOT / "reports" / "private_fairness_audit.json").read_text(
            encoding="utf-8"
        )
    )
    question_counts = {item["id"]: item["records"] for item in audit["questions"]}
    candidate_count = report["scope"]["candidate_count"]

    assert question_counts[report["scope"]["rule_id"]] == candidate_count
    assert sum(item["count"] for item in report["exclusive_clusters"]) == (
        candidate_count
    )
    assert sum(report["trigger_families"].values()) == candidate_count
    assert sum(report["recommendation_counts"].values()) == candidate_count
    assert all(value is False for value in report["privacy_boundary"].values())
    assert all(value is True for value in report["limitations"].values())


def test_private_residence_vehicle_context_audit_is_anonymous_and_consistent() -> None:
    report = json.loads(
        (ROOT / "reports" / "private_residence_vehicle_context_audit.json").read_text(
            encoding="utf-8"
        )
    )
    audit = json.loads(
        (ROOT / "reports" / "private_fairness_audit.json").read_text(
            encoding="utf-8"
        )
    )
    question_counts = {item["id"]: item["records"] for item in audit["questions"]}

    for rule_id, candidate_count in report["scope"]["rules"].items():
        assert question_counts[rule_id] == candidate_count
        assert sum(report["trigger_composition"][rule_id].values()) == candidate_count
        assert (
            sum(report["exclusive_primary_context"][rule_id].values())
            == candidate_count
        )
        assert sum(report["review_priority"][rule_id].values()) == candidate_count
    assert all(value is False for value in report["privacy_boundary"].values())
    assert all(value is True for value in report["limitations"].values())


def test_private_military_proxy_context_audit_is_anonymous_and_consistent() -> None:
    report = json.loads(
        (ROOT / "reports" / "private_military_proxy_context_audit.json").read_text(
            encoding="utf-8"
        )
    )
    audit = json.loads(
        (ROOT / "reports" / "private_fairness_audit.json").read_text(
            encoding="utf-8"
        )
    )
    question_counts = {item["id"]: item["records"] for item in audit["questions"]}
    current_count = report["current_scope"]["candidate_count"]

    assert question_counts[report["current_scope"]["rule_id"]] == current_count
    assert sum(report["current_trigger_families"].values()) == current_count
    assert sum(item["count"] for item in report["current_exclusive_clusters"]) == (
        current_count
    )
    assert sum(report["current_review_tiers"].values()) == current_count
    assert all(value is False for value in report["privacy_boundary"].values())
    assert all(value is True for value in report["limitations"].values())


def test_private_preference_process_context_audit_is_anonymous_and_consistent() -> None:
    report = json.loads(
        (
            ROOT / "reports" / "private_preference_process_context_audit.json"
        ).read_text(encoding="utf-8")
    )
    audit = json.loads(
        (ROOT / "reports" / "private_fairness_audit.json").read_text(
            encoding="utf-8"
        )
    )
    question_counts = {item["id"]: item["records"] for item in audit["questions"]}
    candidate_count = report["scope"]["candidate_count"]

    assert question_counts[report["scope"]["rule_id"]] == candidate_count
    assert sum(report["exclusive_clusters"].values()) == candidate_count
    assert sum(report["sectioning"].values()) == candidate_count
    assert sum(report["review_priority"].values()) == candidate_count
    assert all(value is False for value in report["privacy_boundary"].values())
    assert all(value is True for value in report["limitations"].values())


def test_private_health_context_audit_is_anonymous_and_consistent() -> None:
    report = json.loads(
        (
            ROOT / "reports" / "private_health_job_relevance_context_audit.json"
        ).read_text(encoding="utf-8")
    )
    audit = json.loads(
        (ROOT / "reports" / "private_fairness_audit.json").read_text(
            encoding="utf-8"
        )
    )
    question_counts = {item["id"]: item["records"] for item in audit["questions"]}
    candidate_count = report["scope"]["candidate_count"]

    assert question_counts[report["scope"]["rule_id"]] == candidate_count
    assert sum(report["trigger_composition"].values()) == candidate_count
    assert sum(report["exclusive_clusters"].values()) == candidate_count
    assert sum(report["review_priority"].values()) == candidate_count
    assert all(value is False for value in report["privacy_boundary"].values())
    assert all(value is True for value in report["limitations"].values())


def test_new_private_context_audits_contain_no_contact_or_source_artifacts() -> None:
    report_names = (
        "private_criminal_record_context_audit.json",
        "private_residence_vehicle_context_audit.json",
        "private_military_proxy_context_audit.json",
        "private_preference_process_context_audit.json",
        "private_health_job_relevance_context_audit.json",
    )
    current_audit = json.loads(
        (ROOT / "reports" / "private_fairness_audit.json").read_text(
            encoding="utf-8"
        )
    )
    for report_name in report_names:
        encoded = (ROOT / "reports" / report_name).read_text(encoding="utf-8")
        report = json.loads(encoded)
        assert report["evidence_status"] == "historical"
        assert report["source_ruleset_version"] == report["ruleset_version"]
        assert report["source_matching_version"] == report["matching_version"]
        assert report["current_ruleset_version"] == current_audit["ruleset_version"]
        assert (
            report["current_matching_version"]
            == current_audit["matching_version"]
        )
        assert report["ruleset_version"] != current_audit["ruleset_version"]
        assert report["matching_version"] != current_audit["matching_version"]
        assert "were not rerun" in report["historical_reason"]
        assert not re.search(r"https?://", encoded, re.I)
        assert not re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", encoded, re.I)
        assert not re.search(r"01[016789][- ]?\d{3,4}[- ]?\d{4}", encoded)
        assert not re.search(r"\b\d{2,3}-\d{3,4}-\d{4}\b", encoded)


def test_private_review_sampling_audit_rebuilds_from_bound_queue(
    tmp_path: Path,
) -> None:
    queue_builder = load_tool("build_private_review_queue")
    audit_builder = load_tool("build_private_review_sampling_audit")
    source_path = tmp_path / "snapshot" / "train" / "records.jsonl"
    queue_path = tmp_path / "private" / "queue.jsonl"
    source_path.parent.mkdir(parents=True)
    text = "여성만 모집합니다."
    source_path.write_text(
        json.dumps(
            {
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "sector": "private",
                "source": "synthetic",
                "split": "train",
                "text": text,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    queue_builder.build_review_queue(
        source_path,
        queue_path,
        rule_ids=("SEX-001", "DISABILITY-001"),
        per_rule=1,
    )

    report = audit_builder.build_sampling_audit(
        queue_path,
        queue_builder.queue_manifest_path(queue_path),
        source_path,
    )

    assert report["input"] == {
        "records": 1,
        "sector": "private",
        "split": "train_only",
        "source_categories": 1,
    }
    assert report["selection"]["selected_rule_count"] == 2
    assert report["selection"]["selected_rows"] == 1
    assert report["rule_sampling"]["DISABILITY-001"]["selected_rows"] == 0
    assert all(value is False for value in report["privacy_boundary"].values())


def test_private_review_sampling_audit_cli_does_not_echo_private_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    audit_builder = load_tool("build_private_review_sampling_audit")
    queue_path = tmp_path / "private" / "queue.jsonl"
    manifest_path = tmp_path / "private" / "queue.jsonl.manifest.json"
    source_path = tmp_path / "snapshot" / "train" / "records.jsonl"
    output_path = tmp_path / "reports" / "sampling.json"
    queue_path.parent.mkdir(parents=True)
    source_path.parent.mkdir(parents=True)
    secret = "private-person@example.test"
    queue_path.write_text(secret + "\n", encoding="utf-8")
    manifest_path.write_text("{}", encoding="utf-8")
    source_path.write_text("{}\n", encoding="utf-8")

    exit_code = audit_builder.main(
        [
            "--queue",
            str(queue_path),
            "--manifest",
            str(manifest_path),
            "--source-input",
            str(source_path),
            "--output",
            str(output_path),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert secret not in captured.out + captured.err
    assert captured.err == (
        "error: private review sampling audit could not be built\n"
    )


def test_web_parity_auditor_rejects_holdout_path(tmp_path: Path) -> None:
    module = load_tool("verify_web_parity")
    path = tmp_path / "holdout" / "records.jsonl"
    path.parent.mkdir()
    path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="봉인 홀드아웃"):
        module.audit(path)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js가 필요합니다")
def test_web_parity_auditor_compares_training_records(tmp_path: Path) -> None:
    module = load_tool("verify_web_parity")
    path = tmp_path / "train" / "records.jsonl"
    path.parent.mkdir()
    records = [
        {"id": "train:1", "text": "청년인턴 채용"},
        {"id": "train:2", "text": "자격요건\n남성만 지원 가능"},
    ]
    path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False) + "\n" for record in records
        ),
        encoding="utf-8",
    )

    report = module.audit(path)

    assert report["schema_version"] == "fairpost-web-engine-parity-v1"
    assert report["passed"] is True
    assert report["matched_records"] == 2
    assert report["mismatched_records"] == 0
    assert report["contains_posting_text"] is False
