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
