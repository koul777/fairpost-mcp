from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_tool():
    path = ROOT / "tools" / "check_evidence_versions.py"
    spec = importlib.util.spec_from_file_location("check_evidence_versions", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StubRuleset:
    version = "rules-current"
    matching_version = "match-current"


def test_local_evidence_audit_detects_stale_and_skips_operational(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_tool()
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "current.json").write_text(
        json.dumps(
            {
                "ruleset_version": "rules-current",
                "matching_version": "match-current",
            }
        ),
        encoding="utf-8",
    )
    (reports / "stale.json").write_text(
        json.dumps({"ruleset_version": "rules-old"}), encoding="utf-8"
    )
    (reports / "vercel_deployment_audit.json").write_text(
        json.dumps(
            {
                "schema_version": "fairpost-vercel-deployment-audit-v3",
                "health": {
                    "ruleset_version": "rules-old",
                    "matching_version": "match-old",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "load_ruleset", lambda path: StubRuleset())

    report = module.audit(
        reports, scope="local", output=reports / "version-audit.json"
    )

    assert report["passed"] is False
    assert report["reports_checked"] == 2
    assert report["stale_paths"] == ["reports/stale.json"]


def test_all_evidence_audit_checks_live_health_and_historical_marker(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_tool()
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "current.json").write_text(
        json.dumps({"ruleset_version": "rules-current"}), encoding="utf-8"
    )
    (reports / "old-study.json").write_text(
        json.dumps(
            {"ruleset_version": "rules-old", "evidence_status": "historical"}
        ),
        encoding="utf-8",
    )
    (reports / "old-client.json").write_text(
        json.dumps({"evidence_status": "historical"}),
        encoding="utf-8",
    )
    (reports / "vercel_deployment_audit.json").write_text(
        json.dumps(
            {
                "schema_version": "fairpost-vercel-deployment-audit-v3",
                "health": {
                    "ruleset_version": "rules-current",
                    "matching_version": "match-current",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "load_ruleset", lambda path: StubRuleset())

    report = module.audit(
        reports, scope="all", output=reports / "version-audit.json"
    )

    assert report["passed"] is True
    assert report["reports_checked"] == 2
    assert report["historical_paths_skipped"] == [
        "reports/old-client.json",
        "reports/old-study.json",
    ]
    assert {row["scope"] for row in report["reports"]} == {
        "local",
        "operational",
    }


def test_evidence_output_must_not_alias_other_report(tmp_path: Path) -> None:
    module = load_tool()
    reports = tmp_path / "reports"
    reports.mkdir()
    source = reports / "web_engine_parity.json"
    source.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="덮어쓸 수 없습니다"):
        module.validate_output_path(reports, source)


@pytest.mark.parametrize(
    ("filename", "payload", "scope"),
    [
        (
            "corpus_diversity_audit.json",
            {"schema_version": "private-corpus-diversity-audit-v0"},
            "local",
        ),
        (
            "distribution_audit.json",
            {"schema_version": "fairpost-distribution-audit-v1"},
            "local",
        ),
        ("evaluation.json", {"schema_version": 2}, "local"),
        (
            "human_labeling_handoff.json",
            {"schema_version": "fairpost-human-labeling-handoff-v0"},
            "local",
        ),
        (
            "mcp_client_audit.json",
            {"schema_version": "fairpost-mcp-client-audit-v1"},
            "local",
        ),
        (
            "prd_corpus_summary.json",
            {"schema_version": "fairpost-prd-corpus-summary-v0"},
            "local",
        ),
        (
            "web_engine_parity.json",
            {"schema_version": "fairpost-web-engine-parity-v0"},
            "local",
        ),
        (
            "work24_access_audit.json",
            {"schema_version": "fairpost-work24-access-audit-v0"},
            "local",
        ),
        (
            "vercel_deployment_audit.json",
            {
                "schema_version": "fairpost-vercel-deployment-audit-v1",
                "health": {
                    "ruleset_version": "rules-current",
                    "matching_version": "match-current",
                },
            },
            "all",
        ),
    ],
)
def test_evidence_audit_detects_generated_report_schema_drift(
    monkeypatch,
    tmp_path: Path,
    filename: str,
    payload: dict[str, object],
    scope: str,
) -> None:
    module = load_tool()
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / filename).write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "load_ruleset", lambda path: StubRuleset())

    report = module.audit(
        reports,
        scope=scope,
        output=reports / "version-audit.json",
    )

    assert report["passed"] is False
    assert report["stale_paths"] == [f"reports/{filename}"]
    assert report["reports"][0]["schema_version_matches"] is False
    assert report["reports"][0]["stale_reasons"] == ["schema_version_mismatch"]


def _stub_fingerprints(monkeypatch, module, tmp_path: Path) -> None:
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "load_ruleset", lambda path: StubRuleset())
    monkeypatch.setattr(
        module, "current_runtime_fingerprint", lambda ruleset: "runtime-current"
    )
    monkeypatch.setattr(
        module, "current_validation_fingerprint", lambda: "validation-current"
    )


def _write(reports: Path, name: str, payload: object) -> None:
    (reports / name).write_text(json.dumps(payload), encoding="utf-8")


def test_evidence_audit_detects_runtime_drift_when_rules_are_unchanged(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_tool()
    reports = tmp_path / "reports"
    reports.mkdir()
    _write(
        reports,
        "distribution_audit.json",
        {
            "schema_version": "fairpost-distribution-audit-v2",
            "runtime_source_fingerprint": "runtime-old",
        },
    )
    _write(
        reports,
        "mcp_client_audit.json",
        {
            "schema_version": "fairpost-mcp-client-audit-v2",
            "evidence_status": "current",
            "ruleset_version": "rules-current",
            "matching_version": "match-current",
            "runtime_source_fingerprint": "runtime-current",
        },
    )
    _stub_fingerprints(monkeypatch, module, tmp_path)

    report = module.audit(
        reports, scope="local", output=reports / "version-audit.json"
    )

    assert report["passed"] is False
    assert report["runtime_source_fingerprint"] == "runtime-current"
    assert report["stale_paths"] == ["reports/distribution_audit.json"]
    rows = {row["path"]: row for row in report["reports"]}
    stale = rows["reports/distribution_audit.json"]
    assert stale["status"] == "stale"
    assert stale["stale_reasons"] == ["runtime_source_fingerprint_mismatch"]
    assert stale["schema_version_matches"] is True
    assert stale["runtime_source_fingerprint_matches"] is False
    assert stale["runtime_source_fingerprints"] == {
        "runtime_source_fingerprint": "runtime-old"
    }
    assert stale["expected_runtime_source_fingerprint"] == "runtime-current"
    current = rows["reports/mcp_client_audit.json"]
    assert current["status"] == "current"
    assert current["stale_reasons"] == []
    assert current["runtime_source_fingerprint_matches"] is True
    assert current["validation_source_fingerprint_matches"] is None


def test_evidence_audit_detects_validation_drift_with_distinct_reason(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_tool()
    reports = tmp_path / "reports"
    reports.mkdir()
    _write(
        reports,
        "build_artifact.json",
        {
            "schema_version": "fairpost-build-artifact-v2",
            "ruleset_version": "rules-current",
            "matching_version": "match-current",
            "verification": {
                "tests_passed": 893,
                "test_validation_source_fingerprint": "validation-old",
                "vercel_runtime_source_fingerprint": "runtime-current",
            },
        },
    )
    _stub_fingerprints(monkeypatch, module, tmp_path)

    local = module.audit(reports, scope="local", output=reports / "out.json")
    report = module.audit(reports, scope="all", output=reports / "out.json")

    assert local["reports_checked"] == 0
    assert local["passed"] is False
    assert report["passed"] is False
    assert report["stale_paths"] == ["reports/build_artifact.json"]
    row = report["reports"][0]
    assert row["scope"] == "operational"
    assert row["stale_reasons"] == ["validation_source_fingerprint_mismatch"]
    assert row["ruleset_version_matches"] is True
    assert row["runtime_source_fingerprint_matches"] is True
    assert row["validation_source_fingerprints"] == {
        "verification.test_validation_source_fingerprint": "validation-old"
    }
    assert row["expected_validation_source_fingerprint"] == "validation-current"


def test_vercel_audit_binds_deployed_and_local_runtime_fingerprints(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_tool()
    reports = tmp_path / "reports"
    reports.mkdir()
    _write(
        reports,
        "vercel_deployment_audit.json",
        {
            "schema_version": "fairpost-vercel-deployment-audit-v3",
            "health": {
                "ruleset_version": "rules-current",
                "matching_version": "match-current",
                "runtime_source_fingerprint": "runtime-deployed-old",
            },
            "local_ruleset": {"runtime_source_fingerprint": "runtime-current"},
        },
    )
    _stub_fingerprints(monkeypatch, module, tmp_path)

    report = module.audit(reports, scope="all", output=reports / "out.json")

    assert report["stale_paths"] == ["reports/vercel_deployment_audit.json"]
    row = report["reports"][0]
    assert row["stale_reasons"] == ["runtime_source_fingerprint_mismatch"]
    assert row["runtime_source_fingerprints"] == {
        "health.runtime_source_fingerprint": "runtime-deployed-old",
        "local_ruleset.runtime_source_fingerprint": "runtime-current",
    }


def test_fingerprint_only_report_is_checked_and_historical_is_still_skipped(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_tool()
    reports = tmp_path / "reports"
    reports.mkdir()
    _write(reports, "runtime-only.json", {"runtime_source_fingerprint": "runtime-old"})
    _write(
        reports,
        "old-client.json",
        {
            "evidence_status": "historical",
            "ruleset_version": "rules-old",
            "runtime_source_fingerprint": "runtime-old",
            "verification": {"test_validation_source_fingerprint": "validation-old"},
        },
    )
    _stub_fingerprints(monkeypatch, module, tmp_path)

    report = module.audit(reports, scope="local", output=reports / "out.json")

    assert report["historical_paths_skipped"] == ["reports/old-client.json"]
    assert report["stale_paths"] == ["reports/runtime-only.json"]
    assert report["reports"][0]["stale_reasons"] == [
        "runtime_source_fingerprint_mismatch"
    ]


def test_stale_snapshots_are_reported_without_failing_the_gate(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_tool()
    reports = tmp_path / "reports"
    reports.mkdir()
    _write(
        reports,
        "build_artifact-8h-candidate-2026-09-12.json",
        {
            "schema_version": "fairpost-build-artifact-v2",
            "ruleset_version": "rules-old",
            "verification": {"test_validation_source_fingerprint": "validation-old"},
        },
    )
    _write(
        reports,
        "distribution_audit-8h-final.json",
        {"runtime_source_fingerprint": "runtime-old"},
    )
    _write(
        reports,
        "corpus_rule_coverage.json",
        {"ruleset_version": "rules-current", "matching_version": "match-current"},
    )
    _stub_fingerprints(monkeypatch, module, tmp_path)

    report = module.audit(reports, scope="local", output=reports / "out.json")

    assert report["passed"] is True
    assert report["stale_paths"] == []
    assert report["reports_stale"] == 0
    assert report["reports_snapshot_stale"] == 2
    assert report["reports_current"] == 1
    assert report["stale_snapshot_paths"] == [
        "reports/build_artifact-8h-candidate-2026-09-12.json",
        "reports/distribution_audit-8h-final.json",
    ]
    rows = {row["path"]: row for row in report["reports"]}
    snapshot = rows["reports/build_artifact-8h-candidate-2026-09-12.json"]
    assert snapshot["claims_current"] is False
    assert snapshot["snapshot_of"] == "build_artifact.json"
    assert snapshot["stale_reasons"] == [
        "ruleset_version_mismatch",
        "validation_source_fingerprint_mismatch",
    ]
    assert rows["reports/corpus_rule_coverage.json"]["claims_current"] is True


def test_snapshot_that_claims_current_status_still_fails_the_gate(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_tool()
    reports = tmp_path / "reports"
    reports.mkdir()
    _write(
        reports,
        "distribution_audit-candidate.json",
        {"evidence_status": "current", "runtime_source_fingerprint": "runtime-old"},
    )
    _stub_fingerprints(monkeypatch, module, tmp_path)

    report = module.audit(reports, scope="local", output=reports / "out.json")

    assert report["passed"] is False
    assert report["stale_paths"] == ["reports/distribution_audit-candidate.json"]
    assert report["stale_snapshot_paths"] == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("build_artifact.json", None),
        ("build_artifact-8h-candidate-2026-09-12.json", "build_artifact.json"),
        ("distribution_audit-8h-final.json", "distribution_audit.json"),
        ("evidence_version_audit-8h-final.json", "evidence_version_audit.json"),
        ("role-review-audit-2026-09-12.json", None),
        ("distribution_audit_extra.json", None),
        ("build_artifact-8h.md", None),
    ],
)
def test_snapshot_names_follow_canonical_report_prefixes(
    name: str, expected: str | None
) -> None:
    assert load_tool().snapshot_of(name) == expected


def test_snapshot_prefixes_cover_distribution_ephemeral_reports() -> None:
    module = load_tool()
    from tools.verify_distribution import EPHEMERAL_REPORT_PREFIXES

    for prefix in EPHEMERAL_REPORT_PREFIXES:
        assert module.snapshot_of(f"{prefix}example.json") is not None


def test_evidence_audit_uses_real_runtime_and_validation_fingerprints(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_tool()
    from mcp_server.build_identity import runtime_source_fingerprint
    from tools.release_inputs import validation_source_fingerprint

    ruleset = module.load_ruleset(ROOT / "data")
    reports = tmp_path / "reports"
    reports.mkdir()
    runtime = runtime_source_fingerprint(
        ruleset_version=ruleset.version,
        matching_version=ruleset.matching_version,
        root=ROOT,
    )
    validation = validation_source_fingerprint(ROOT)
    _write(
        reports,
        "build_artifact.json",
        {
            "schema_version": "fairpost-build-artifact-v2",
            "ruleset_version": ruleset.version,
            "matching_version": ruleset.matching_version,
            "verification": {
                "test_validation_source_fingerprint": validation,
                "vercel_runtime_source_fingerprint": runtime,
            },
        },
    )
    # Report paths are relative to ROOT, so ROOT moves to tmp_path while the
    # unpatched fingerprint helpers keep hashing the repository via ROOT below.
    real_runtime = module.current_runtime_fingerprint(ruleset)
    real_validation = module.current_validation_fingerprint()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "load_ruleset", lambda path: ruleset)
    monkeypatch.setattr(
        module, "current_runtime_fingerprint", lambda rules: real_runtime
    )
    monkeypatch.setattr(
        module, "current_validation_fingerprint", lambda: real_validation
    )

    report = module.audit(reports, scope="all", output=tmp_path / "out.json")

    assert real_runtime == runtime
    assert real_validation == validation
    assert report["runtime_source_fingerprint"] == runtime
    assert report["passed"] is True
    assert report["reports"][0]["validation_source_fingerprint_matches"] is True
    assert report["reports"][0]["runtime_source_fingerprint_matches"] is True


def test_cli_lists_stale_reasons_and_exit_code(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    module = load_tool()
    reports = tmp_path / "reports"
    reports.mkdir()
    _write(
        reports,
        "distribution_audit.json",
        {
            "schema_version": "fairpost-distribution-audit-v2",
            "runtime_source_fingerprint": "runtime-old",
        },
    )
    _stub_fingerprints(monkeypatch, module, tmp_path)
    output = tmp_path / "audit.json"

    exit_code = module.main(
        ["--reports-dir", str(reports), "--output", str(output)]
    )

    assert exit_code == 1
    stdout = capsys.readouterr().out
    assert (
        "stale: reports/distribution_audit.json "
        "(runtime_source_fingerprint_mismatch)"
    ) in stdout
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["stale_paths"] == ["reports/distribution_audit.json"]
