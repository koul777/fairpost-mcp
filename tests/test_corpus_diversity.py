from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_tool():
    path = ROOT / "tools" / "audit_corpus_diversity.py"
    spec = importlib.util.spec_from_file_location("audit_corpus_diversity", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summary():
    return {
        "total": 100,
        "train_holdout_hash_overlap": 0,
        "raw_postings_committed": False,
        "deidentification": ["organization", "email", "phone"],
        "sectors": {"private": 100},
        "sources": {"source-a": 50, "source-b": 50},
        "occupations": {"field": 50, "office": 30, "research": 10, "tech": 10},
    }


def test_diversity_gate_passes_balanced_private_summary() -> None:
    module = load_tool()
    report = module.audit(
        summary(),
        min_sources=2,
        max_dominant_source_share=0.7,
        min_non_field_share=0.35,
        min_research_tech_share=0.1,
    )

    assert report["status"] == "pass"
    assert all(report["checks"].values())
    assert all(value is False for value in report["privacy_boundary"].values())


def test_diversity_gate_surfaces_single_source_field_bias() -> None:
    module = load_tool()
    value = summary()
    value["sources"] = {"source-a": 100}
    value["occupations"] = {"field": 85, "office": 10, "research": 2, "tech": 3}
    report = module.audit(
        value,
        min_sources=2,
        max_dominant_source_share=0.7,
        min_non_field_share=0.35,
        min_research_tech_share=0.1,
    )

    assert report["status"] == "alert"
    assert report["checks"]["minimum_sources"] is False
    assert report["checks"]["dominant_source_share"] is False
    assert report["checks"]["non_field_share"] is False
    assert report["checks"]["research_tech_share"] is False


def test_diversity_gate_rejects_mixed_sector_summary() -> None:
    module = load_tool()
    value = summary()
    value["sectors"] = {"private": 50, "public": 50}

    with pytest.raises(ValueError, match="민간 전용"):
        module.audit(
            value,
            min_sources=2,
            max_dominant_source_share=0.7,
            min_non_field_share=0.35,
            min_research_tech_share=0.1,
        )


def test_diversity_output_must_not_alias_input(tmp_path: Path) -> None:
    module = load_tool()
    path = tmp_path / "summary.json"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="덮어쓸 수 없습니다"):
        module.validate_output_path(path, path)
