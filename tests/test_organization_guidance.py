from __future__ import annotations

import pytest

from core.loader import RuleLoadError
from core.organization_guidance import (
    ORGANIZATION_GUIDANCE_SCHEMA_VERSION,
    load_organization_guidance_catalog,
)


def test_public_organization_guidance_has_distinct_applicability_profiles() -> None:
    catalog = load_organization_guidance_catalog()

    assert catalog.schema_version == ORGANIZATION_GUIDANCE_SCHEMA_VERSION
    assert catalog.as_of == "2026-09-06"
    assert len(catalog.profiles) == 6
    public_corporation = catalog.context_for("public_corporation")
    other_public = catalog.context_for("other_public")
    assert public_corporation is not None
    assert other_public is not None
    assert "경영·혁신 지침 직접 검토" == public_corporation["profile"][
        "applicability_label"
    ]
    assert "자동 단정하지 않는다" in other_public["profile"]["statement"]
    assert any(
        source["title"].startswith("공기업·준정부기관의 경영에 관한 지침")
        for source in public_corporation["sources"]
    )
    assert not any(
        source["id"] == "public-management-guideline"
        for source in other_public["sources"]
    )


def test_public_organization_guidance_rejects_unofficial_source(tmp_path) -> None:
    guidance = tmp_path / "guidance"
    guidance.mkdir()
    source = (
        "schema_version: fairpost-organization-applicability-v1\n"
        "as_of: '2026-09-06'\n"
        "authority: applicability_aid_not_legal_advice\n"
        "sources:\n"
        "- id: bad\n"
        "  title: 비공식\n"
        "  publisher: 비공식\n"
        "  source_url: https://example.com/guide\n"
        "  published_or_updated_at: '2026-09-06'\n"
        "  source_type: guidance\n"
        "profiles:\n"
        "- id: public_corporation\n"
        "  sector: public\n"
        "  label: 공기업\n"
        "  applicability_label: 확인\n"
        "  statement: 적용 범위를 확인한다.\n"
        "  source_ids: [bad]\n"
        "limitations: [법률 자문이 아니다.]\n"
    )
    (guidance / "organization-applicability.yaml").write_text(
        source, encoding="utf-8"
    )

    with pytest.raises(RuleLoadError, match="공식 HTTPS URL"):
        load_organization_guidance_catalog(tmp_path)
