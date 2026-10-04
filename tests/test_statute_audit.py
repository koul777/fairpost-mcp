from __future__ import annotations

import copy
from datetime import date
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any
import xml.etree.ElementTree as ET

import pytest
import yaml

from core import load_ruleset
from core.loader import RuleLoadError, _validate_optional_statute_metadata, _validate_statutes


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DATE = "2026-10-03"


def load_build_statutes():
    spec = importlib.util.spec_from_file_location(
        "build_statutes_under_test", ROOT / "tools" / "build_statutes.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool():
    return load_build_statutes()


# ---------------------------------------------------------------------------
# Fixtures: stored snapshots and official XML responses (no network)
# ---------------------------------------------------------------------------


def official_xml(
    *,
    name: str,
    law_id: str,
    articles: dict[str, tuple[str, str, str]],
    law_number: str | None = None,
    promulgated: str | None = None,
    mst: str | None = None,
) -> ET.Element:
    """articles: {"2": (title, text, "YYYYMMDD" effective date)}."""
    basic = [f"<법령ID>{law_id}</법령ID>", f"<법령명_한글>{name}</법령명_한글>"]
    if law_number is not None:
        basic.append(f"<공포번호>{law_number}</공포번호>")
    if promulgated is not None:
        basic.append(f"<공포일자>{promulgated}</공포일자>")
    if mst is not None:
        basic.append(f"<법령일련번호>{mst}</법령일련번호>")
    units = []
    for number, (title, text, effective) in articles.items():
        units.append(
            "<조문단위>"
            f"<조문번호>{number}</조문번호><조문여부>조문</조문여부>"
            f"<조문제목>{title}</조문제목><조문시행일자>{effective}</조문시행일자>"
            f"<조문내용>{text}</조문내용>"
            "</조문단위>"
        )
    return ET.fromstring(
        f"<법령><기본정보>{''.join(basic)}</기본정보><조문>{''.join(units)}</조문></법령>"
    )


def stored_snapshot(
    tool,
    *,
    statute_id: str,
    name: str,
    law_id: str,
    articles: dict[str, tuple[str, str, str]],
    snapshot_date: str = "2026-07-26",
) -> dict[str, Any]:
    """articles: {"제2조": (title, text, "YYYY-MM-DD" effective date)}."""
    return {
        "id": statute_id,
        "name": name,
        "articles": {
            key: {
                "title": title,
                "text": text,
                "effective_date": effective,
                "hash": tool.article_hash(text),
            }
            for key, (title, text, effective) in articles.items()
        },
        "snapshot_date": snapshot_date,
        "source": "국가법령정보센터",
        "source_url": f"https://www.law.go.kr/법령/{name}",
        "retrieved_via": "national-law-open-api",
        "official_id": law_id,
    }


def refreshed_from(stored: dict[str, Any], tool, **official_changes: Any) -> dict[str, Any]:
    """Copy of `stored` after replacing article fields: {"제2조": {"text": ...}}."""
    refreshed = copy.deepcopy(stored)
    for article, changes in official_changes.items():
        refreshed["articles"][article].update(changes)
        if "text" in changes:
            refreshed["articles"][article]["hash"] = tool.article_hash(changes["text"])
    return refreshed


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_text_hash_difference_is_content_changed(tool) -> None:
    stored = stored_snapshot(
        tool,
        statute_id="alpha-act",
        name="알파법",
        law_id="000001",
        articles={"제2조": ("정의", "제2조(정의) 옛 본문", "2024-01-01")},
    )
    refreshed = refreshed_from(stored, tool, **{"제2조": {"text": "제2조(정의) 새 본문"}})

    changes = tool.diff_snapshots(stored, refreshed)

    assert {change["classification"] for change in changes} == {tool.CONTENT_CHANGED}
    assert {change["field"] for change in changes} == {"text", "hash"}


def test_effective_date_only_is_metadata_only(tool) -> None:
    stored = stored_snapshot(
        tool,
        statute_id="alpha-act",
        name="알파법",
        law_id="000001",
        articles={"제2조": ("정의", "제2조(정의) 본문", "2024-01-01")},
    )
    refreshed = refreshed_from(stored, tool, **{"제2조": {"effective_date": "2026-09-01"}})

    changes = tool.diff_snapshots(stored, refreshed)

    assert changes == [
        {
            "statute_id": "alpha-act",
            "article": "제2조",
            "field": "effective_date",
            "classification": tool.METADATA_ONLY,
        }
    ]


def test_identical_text_with_stale_stored_hash_is_not_content_changed(tool) -> None:
    stored = stored_snapshot(
        tool,
        statute_id="alpha-act",
        name="알파법",
        law_id="000001",
        articles={"제2조": ("정의", "제2조(정의) 본문", "2024-01-01")},
    )
    stored["articles"]["제2조"]["hash"] = "sha256:" + "0" * 64
    refreshed = refreshed_from(stored, tool)
    refreshed["articles"]["제2조"]["hash"] = tool.article_hash("제2조(정의) 본문")

    changes = tool.diff_snapshots(stored, refreshed)

    assert [(c["field"], c["classification"]) for c in changes] == [
        ("hash", tool.METADATA_ONLY)
    ]


def test_hash_field_cannot_hide_a_text_change(tool) -> None:
    stored = stored_snapshot(
        tool,
        statute_id="alpha-act",
        name="알파법",
        law_id="000001",
        articles={"제2조": ("정의", "제2조(정의) 옛 본문", "2024-01-01")},
    )
    # The stored hash was tampered to equal the hash of the new official text.
    stored["articles"]["제2조"]["hash"] = tool.article_hash("제2조(정의) 새 본문")
    refreshed = refreshed_from(stored, tool, **{"제2조": {"text": "제2조(정의) 새 본문"}})

    changes = tool.diff_snapshots(stored, refreshed)

    assert changes
    assert {change["classification"] for change in changes} == {tool.CONTENT_CHANGED}


def test_statute_level_changes_are_metadata_only(tool) -> None:
    stored = stored_snapshot(
        tool,
        statute_id="alpha-act",
        name="알파법",
        law_id="000001",
        articles={"제2조": ("정의", "제2조(정의) 본문", "2024-01-01")},
    )
    refreshed = copy.deepcopy(stored)
    refreshed["law_number"] = "19234"
    refreshed["promulgation_date"] = "2026-03-14"

    changes = tool.diff_snapshots(stored, refreshed)

    assert [(c["article"], c["field"], c["classification"]) for c in changes] == [
        ("", "law_number", tool.METADATA_ONLY),
        ("", "promulgation_date", tool.METADATA_ONLY),
    ]


def test_unchanged_snapshot_has_no_changes(tool) -> None:
    stored = stored_snapshot(
        tool,
        statute_id="alpha-act",
        name="알파법",
        law_id="000001",
        articles={"제2조": ("정의", "제2조(정의) 본문", "2024-01-01")},
    )
    assert tool.diff_snapshots(stored, copy.deepcopy(stored)) == []


# ---------------------------------------------------------------------------
# Report: only content changes list rules that need review
# ---------------------------------------------------------------------------


def test_report_lists_rule_ids_only_for_content_changes(tool) -> None:
    changes = [
        {
            "statute_id": "alpha-act",
            "article": "제2조",
            "field": "text",
            "classification": tool.CONTENT_CHANGED,
        },
        {
            "statute_id": "alpha-act",
            "article": "제2조",
            "field": "hash",
            "classification": tool.CONTENT_CHANGED,
        },
        {
            "statute_id": "alpha-act",
            "article": "제3조",
            "field": "effective_date",
            "classification": tool.METADATA_ONLY,
        },
    ]
    impact = {
        ("alpha-act", "제2조"): ["LAW-A1", "LAW-A2"],
        ("alpha-act", "제3조"): ["LAW-A3"],
    }

    report = tool.build_audit_report(
        changes, statutes=6, snapshot_date=SNAPSHOT_DATE, rule_impact=impact
    )

    assert report["content_changed"] == [
        {
            "statute_id": "alpha-act",
            "article": "제2조",
            "fields": ["text", "hash"],
            "rule_ids": ["LAW-A1", "LAW-A2"],
        }
    ]
    assert report["metadata_only"] == [
        {"statute_id": "alpha-act", "article": "제3조", "fields": ["effective_date"]}
    ]
    assert report["affected_rule_ids"] == ["LAW-A1", "LAW-A2"]
    assert "LAW-A3" not in json.dumps(report)
    assert report["review_required"] is True
    assert report["snapshot_changed"] is True
    assert report["contains_statute_text"] is False


def test_metadata_only_report_needs_no_rule_review(tool) -> None:
    changes = [
        {
            "statute_id": "alpha-act",
            "article": "제3조",
            "field": "effective_date",
            "classification": tool.METADATA_ONLY,
        }
    ]
    report = tool.build_audit_report(
        changes,
        statutes=6,
        snapshot_date=SNAPSHOT_DATE,
        rule_impact={("alpha-act", "제3조"): ["LAW-A3"]},
    )

    assert report["content_changed"] == []
    assert report["affected_rule_ids"] == []
    assert report["review_required"] is False
    assert report["snapshot_changed"] is True


def test_empty_report_matches_committed_shape(tool) -> None:
    report = tool.build_audit_report(
        [], statutes=6, snapshot_date=SNAPSHOT_DATE, rule_impact={}
    )
    committed = json.loads((ROOT / "reports" / "statute_audit.json").read_text("utf-8"))

    assert committed.keys() <= report.keys()
    assert report["changed_statutes"] == []
    assert report["review_required"] is False
    assert report["snapshot_changed"] is False


def test_pr_body_shows_both_groups_and_matching_checklists(tool) -> None:
    report = tool.build_audit_report(
        [
            {
                "statute_id": "alpha-act",
                "article": "제2조",
                "field": "text",
                "classification": tool.CONTENT_CHANGED,
            },
            {
                "statute_id": "alpha-act",
                "article": "제3조",
                "field": "effective_date",
                "classification": tool.METADATA_ONLY,
            },
            {
                "statute_id": "beta-act",
                "article": "",
                "field": "law_number",
                "classification": tool.METADATA_ONLY,
            },
        ],
        statutes=6,
        snapshot_date=SNAPSHOT_DATE,
        rule_impact={("alpha-act", "제2조"): ["LAW-A1"]},
    )

    body = tool.render_pr_body(report)

    assert "## Content changed: rule review required (1)" in body
    assert "`alpha-act` 제2조: text | rules: `LAW-A1`" in body
    assert "## Metadata only: article text identical (2)" in body
    assert "`alpha-act` 제3조: effective_date" in body
    assert "`beta-act` (statute level): law_number" in body
    assert body.index("Content changed") < body.index("Metadata only")
    assert "Run the holdout regression evaluation" in body
    assert "Spot-check effective dates" in body
    assert "LAW-A3" not in body


def test_pr_body_for_metadata_only_skips_rule_review_checklist(tool) -> None:
    report = tool.build_audit_report(
        [
            {
                "statute_id": "alpha-act",
                "article": "제3조",
                "field": "effective_date",
                "classification": tool.METADATA_ONLY,
            }
        ],
        statutes=6,
        snapshot_date=SNAPSHOT_DATE,
        rule_impact={},
    )

    body = tool.render_pr_body(report)

    assert "## Content changed: rule review required (0)" in body
    assert "None. No article text changed." in body
    assert "rule review is not required" in body
    assert "Run the holdout regression evaluation" not in body


# ---------------------------------------------------------------------------
# Official metadata extraction (law number, promulgation date, MST)
# ---------------------------------------------------------------------------


def test_official_law_metadata_reads_present_fields(tool) -> None:
    root = official_xml(
        name="알파법",
        law_id="000001",
        articles={"2": ("정의", "제2조(정의) 본문", "20240101")},
        law_number="19234",
        promulgated="20260314",
    )
    assert tool.official_law_metadata(root) == {
        "law_number": "19234",
        "promulgation_date": "2026-03-14",
    }


def test_official_law_metadata_pins_source_url_when_mst_is_returned(tool) -> None:
    root = official_xml(
        name="알파법",
        law_id="000001",
        articles={"2": ("정의", "제2조(정의) 본문", "20240101")},
        law_number="19234",
        promulgated="20260314",
        mst="283455",
    )
    metadata = tool.official_law_metadata(root)
    assert metadata["mst"] == "283455"
    assert metadata["versioned_source_url"] == "https://www.law.go.kr/lsInfoP.do?lsiSeq=283455"


def test_official_law_metadata_skips_absent_and_malformed_values(tool) -> None:
    root = official_xml(
        name="알파법",
        law_id="000001",
        articles={"2": ("정의", "제2조(정의) 본문", "20240101")},
        law_number="제19234호",
        promulgated="2026-03-14",
        mst="abc",
    )
    assert tool.official_law_metadata(root) == {}


def test_optional_field_names_match_loader_validation(tool) -> None:
    source = (ROOT / "core" / "loader.py").read_text(encoding="utf-8")
    for field in tool.OPTIONAL_STATUTE_FIELDS:
        assert f'"{field}"' in source, field


# ---------------------------------------------------------------------------
# End to end: compare, classify, refresh with mocked official responses
# ---------------------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path: Path, tool) -> Path:
    statutes = tmp_path / "data" / "statutes"
    rules = tmp_path / "data" / "rules"
    statutes.mkdir(parents=True)
    rules.mkdir(parents=True)
    alpha = stored_snapshot(
        tool,
        statute_id="alpha-act",
        name="알파법",
        law_id="000001",
        articles={
            "제2조": ("정의", "제2조(정의) 옛 본문", "2024-01-01"),
            "제3조": ("범위", "제3조(범위) 본문", "2024-01-01"),
        },
    )
    beta = stored_snapshot(
        tool,
        statute_id="beta-act",
        name="베타법",
        law_id="000002",
        articles={"제5조": ("금지", "제5조(금지) 본문", "2025-06-01")},
    )
    for payload in (alpha, beta):
        (statutes / f"{payload['id']}.yaml").write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=120),
            encoding="utf-8",
        )
    law_rules = [
        {"id": "LAW-A1", "basis": {"type": "statute", "statute_id": "alpha-act",
                                    "article": "제2조", "snapshot_date": "2026-07-26"}},
        {"id": "LAW-A2", "basis": {"type": "statute", "statute_id": "alpha-act",
                                    "article": "제2조", "snapshot_date": "2026-07-26"}},
        {"id": "LAW-A3", "basis": {"type": "statute", "statute_id": "alpha-act",
                                    "article": "제3조", "snapshot_date": "2026-07-26"}},
        {"id": "LAW-B1", "basis": {"type": "statute", "statute_id": "beta-act",
                                    "article": "제5조", "snapshot_date": "2026-07-26"}},
    ]
    (rules / "law.yaml").write_text(
        yaml.safe_dump(law_rules, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )
    return tmp_path / "data"


@pytest.fixture
def official_roots() -> dict[str, ET.Element]:
    return {
        "알파법": official_xml(
            name="알파법",
            law_id="000001",
            articles={
                # Text changed.
                "2": ("정의", "제2조(정의) 새 본문", "20260901"),
                # Text identical, only the effective date moved.
                "3": ("범위", "제3조(범위) 본문", "20260901"),
            },
            law_number="19234",
            promulgated="20260314",
        ),
        "베타법": official_xml(
            name="베타법",
            law_id="000002",
            articles={"5": ("금지", "제5조(금지) 본문", "20250601")},
            law_number="20001",
            promulgated="20250601",
        ),
    }


def run_audit(tool, monkeypatch, data_dir, roots, *, refresh, tmp_path):
    monkeypatch.setattr(tool, "fetch_official_law", lambda name, oc: roots[name])
    report_path = tmp_path / "out" / "statute_audit.json"
    body_path = tmp_path / "out" / "pr-body.md"
    report = tool.run_official_audit(
        data_dir / "statutes",
        oc="fixture",
        refresh=refresh,
        snapshot_date=SNAPSHOT_DATE,
        report_path=report_path,
        pr_body_path=body_path,
    )
    return report, report_path, body_path


def test_audit_separates_content_and_metadata_changes(
    tool, monkeypatch, data_dir, official_roots, tmp_path
) -> None:
    report, report_path, body_path = run_audit(
        tool, monkeypatch, data_dir, official_roots, refresh=False, tmp_path=tmp_path
    )

    assert [(e["statute_id"], e["article"]) for e in report["content_changed"]] == [
        ("alpha-act", "제2조")
    ]
    assert report["content_changed"][0]["rule_ids"] == ["LAW-A1", "LAW-A2"]
    metadata = {(e["statute_id"], e["article"]): e["fields"] for e in report["metadata_only"]}
    assert metadata[("alpha-act", "제3조")] == ["effective_date"]
    # New optional identifiers on statutes: statute level, article "".
    assert set(metadata[("alpha-act", "")]) == {"law_number", "promulgation_date"}
    assert set(metadata[("beta-act", "")]) == {"law_number", "promulgation_date"}
    # Only the content-changed article contributes rule IDs.
    assert report["affected_rule_ids"] == ["LAW-A1", "LAW-A2"]
    assert report["review_required"] is True
    assert report["snapshot_changed"] is True
    assert report["changed_statutes"] == ["alpha-act", "beta-act"]

    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert written == json.loads(json.dumps(report))
    assert "옛 본문" not in report_path.read_text(encoding="utf-8")
    body = body_path.read_text(encoding="utf-8")
    assert "`alpha-act` 제2조: text, effective_date, hash | rules: `LAW-A1`, `LAW-A2`" in body
    assert "`alpha-act` 제3조: effective_date" in body

    # Compare-only mode must not touch the snapshots.
    stored = yaml.safe_load((data_dir / "statutes" / "alpha-act.yaml").read_text("utf-8"))
    assert stored["snapshot_date"] == "2026-07-26"
    assert "law_number" not in stored


def test_metadata_only_audit_does_not_require_rule_review(
    tool, monkeypatch, data_dir, tmp_path
) -> None:
    roots = {
        "알파법": official_xml(
            name="알파법",
            law_id="000001",
            articles={
                "2": ("정의", "제2조(정의) 옛 본문", "20260901"),
                "3": ("범위", "제3조(범위) 본문", "20240101"),
            },
        ),
        "베타법": official_xml(
            name="베타법",
            law_id="000002",
            articles={"5": ("금지", "제5조(금지) 본문", "20250601")},
        ),
    }
    report, _, _ = run_audit(tool, monkeypatch, data_dir, roots, refresh=False, tmp_path=tmp_path)

    assert report["content_changed"] == []
    assert report["affected_rule_ids"] == []
    assert report["review_required"] is False
    assert report["snapshot_changed"] is True
    assert report["metadata_only"] == [
        {"statute_id": "alpha-act", "article": "제2조", "fields": ["effective_date"]}
    ]


def test_refresh_fills_optional_fields_and_is_then_stable(
    tool, monkeypatch, data_dir, official_roots, tmp_path
) -> None:
    run_audit(tool, monkeypatch, data_dir, official_roots, refresh=True, tmp_path=tmp_path)

    alpha = yaml.safe_load((data_dir / "statutes" / "alpha-act.yaml").read_text("utf-8"))
    assert alpha["law_number"] == "19234"
    assert alpha["promulgation_date"] == "2026-03-14"
    assert "mst" not in alpha and "versioned_source_url" not in alpha
    assert alpha["snapshot_date"] == SNAPSHOT_DATE
    assert alpha["articles"]["제2조"]["text"] == "제2조(정의) 새 본문"
    # The refreshed output satisfies the loader's optional-field validation.
    _validate_optional_statute_metadata(
        "alpha-act", alpha, date.fromisoformat(alpha["snapshot_date"])
    )
    rules = yaml.safe_load((data_dir / "rules" / "law.yaml").read_text("utf-8"))
    assert {rule["basis"]["snapshot_date"] for rule in rules} == {SNAPSHOT_DATE}

    # A second run against the same official data finds nothing to do.
    report, _, body_path = run_audit(
        tool, monkeypatch, data_dir, official_roots, refresh=True, tmp_path=tmp_path
    )
    assert report["snapshot_changed"] is False
    assert report["content_changed"] == [] and report["metadata_only"] == []
    assert "None. No article text changed." in body_path.read_text(encoding="utf-8")


def test_absent_official_metadata_keeps_stored_values(
    tool, monkeypatch, data_dir, official_roots, tmp_path
) -> None:
    run_audit(tool, monkeypatch, data_dir, official_roots, refresh=True, tmp_path=tmp_path)
    bare_roots = {
        "알파법": official_xml(
            name="알파법",
            law_id="000001",
            articles={
                "2": ("정의", "제2조(정의) 새 본문", "20260901"),
                "3": ("범위", "제3조(범위) 본문", "20260901"),
            },
        ),
        "베타법": official_xml(
            name="베타법",
            law_id="000002",
            articles={"5": ("금지", "제5조(금지) 본문", "20250601")},
        ),
    }
    report, _, _ = run_audit(
        tool, monkeypatch, data_dir, bare_roots, refresh=True, tmp_path=tmp_path
    )

    assert report["snapshot_changed"] is False
    alpha = yaml.safe_load((data_dir / "statutes" / "alpha-act.yaml").read_text("utf-8"))
    assert alpha["law_number"] == "19234"


def test_cli_check_official_reports_counts_and_exit_code(
    tool, monkeypatch, data_dir, official_roots, tmp_path, capsys
) -> None:
    monkeypatch.setattr(tool, "fetch_official_law", lambda name, oc: official_roots[name])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_statutes.py",
            "--statutes-dir",
            str(data_dir / "statutes"),
            "--check-official",
            "--oc",
            "fixture",
            "--snapshot-date",
            SNAPSHOT_DATE,
            "--report",
            str(tmp_path / "report.json"),
            "--pr-body",
            str(tmp_path / "body.md"),
        ],
    )

    assert tool.main() == 1
    assert "조문 본문 변경 1건, 메타데이터만 변경 3건" in capsys.readouterr().err
    assert (tmp_path / "body.md").is_file()


# ---------------------------------------------------------------------------
# Loader validation of optional snapshot metadata
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_statutes() -> dict[str, Any]:
    return load_ruleset().statutes


def test_committed_snapshots_stay_valid_without_optional_fields(real_statutes) -> None:
    _validate_statutes(copy.deepcopy(real_statutes))


def test_valid_optional_fields_are_accepted(real_statutes) -> None:
    statutes = copy.deepcopy(real_statutes)
    statutes["equal-employment-act"].update(
        {
            "law_number": "19234",
            "promulgation_date": "2026-03-14",
            "mst": "283455",
            "versioned_source_url": "https://www.law.go.kr/lsInfoP.do?lsiSeq=283455",
        }
    )
    _validate_statutes(statutes)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("law_number", "제19234호", "law_number"),
        ("law_number", 19234, "law_number"),
        ("mst", "28x455", "mst"),
        ("promulgation_date", "2026/03/14", "promulgation_date"),
        ("promulgation_date", "2099-01-01", "수집 기준일보다 늦을 수 없습니다"),
        ("versioned_source_url", "http://www.law.go.kr/lsInfoP.do?lsiSeq=1", "versioned_source_url"),
        ("versioned_source_url", "https://example.com/lsInfoP.do?lsiSeq=1", "versioned_source_url"),
    ],
)
def test_invalid_optional_fields_are_rejected(real_statutes, field, value, message) -> None:
    statutes = copy.deepcopy(real_statutes)
    statutes["equal-employment-act"][field] = value
    with pytest.raises(RuleLoadError, match=re.escape(message)):
        _validate_statutes(statutes)


def test_versioned_url_must_match_mst(real_statutes) -> None:
    statutes = copy.deepcopy(real_statutes)
    statutes["equal-employment-act"].update(
        {"mst": "1234", "versioned_source_url": "https://www.law.go.kr/lsInfoP.do?lsiSeq=12345"}
    )
    with pytest.raises(RuleLoadError, match="mst와 일치하지 않습니다"):
        _validate_statutes(statutes)
