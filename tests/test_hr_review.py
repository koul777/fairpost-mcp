from __future__ import annotations

from types import SimpleNamespace

import anyio
import pytest

from core import FairpostEngine
from core.guidance import GUIDANCE_SCHEMA_VERSION, load_guidance_catalog
from mcp_server.review import (
    LawMcpConfig,
    LiveLawVerifier,
    prepare_hr_review_packet,
)


LAW_ENV = (
    "FAIRPOST_KOREAN_LAW_MCP_URL",
    "FAIRPOST_KOREAN_LAW_MCP_TOKEN",
    "FAIRPOST_KOREAN_LAW_MCP_COMMAND",
    "FAIRPOST_KOREAN_LAW_MCP_ARGS",
    "FAIRPOST_KOREAN_LAW_MCP_CWD",
    "FAIRPOST_KOREAN_LAW_MCP_ENV_ALLOWLIST",
    "FAIRPOST_KOREAN_LAW_SEARCH_TOOL",
    "FAIRPOST_KOREAN_LAW_TEXT_TOOL",
)


def _clear_law_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in LAW_ENV:
        monkeypatch.delenv(name, raising=False)


def test_ncs_guidance_catalog_is_preprocessed_and_rule_linked() -> None:
    engine = FairpostEngine()
    question_ids = {
        rule["id"] for rule in engine.ruleset.rules if rule["layer"] == "question"
    }

    catalog = load_guidance_catalog(question_ids=question_ids)

    assert catalog.schema_version == GUIDANCE_SCHEMA_VERSION
    assert catalog.catalog_version.startswith("guidance-")
    assert catalog.as_of == "2026-09-06"
    assert catalog.authority == "guidance_not_law"
    assert len(catalog.sources) >= 7
    assert len(catalog.controls) >= 9
    assert all(
        source.source_url.startswith("https://www.ncs.go.kr/")
        for source in catalog.sources
    )


def test_hr_review_packet_keeps_posting_local_when_law_mcp_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_law_environment(monkeypatch)
    private_posting = "여성만 지원 가능 private-posting-marker"

    async def exercise() -> None:
        packet = await prepare_hr_review_packet(FairpostEngine(), private_posting)

        assert packet.schema_version == "fairpost-hr-review-v1"
        assert packet.law_mcp_transport == "disabled"
        assert packet.check.findings[0].id == "SEX-001"
        assert packet.law_verifications[0].status == "not_configured"
        assert packet.law_verifications[0].request.posting_text_sent is False
        serialized_requests = repr(
            [item.request for item in packet.law_verifications]
        )
        assert "private-posting-marker" not in serialized_requests
        assert packet.ncs_guidance.authority == "guidance_not_law"
        assert any(
            control.id == "ncs-job-relevance"
            for control in packet.ncs_guidance.controls
        )

    anyio.run(exercise)


def test_live_law_verifier_resolves_current_mst_and_article_without_posting() -> None:
    engine = FairpostEngine()
    findings = engine.check("여성만 지원 가능").findings
    calls: list[tuple[str, dict[str, str]]] = []

    class FakeSession:
        async def list_tools(self):
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(name="search_law"),
                    SimpleNamespace(name="get_law_text"),
                ]
            )

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            if name == "search_law":
                return SimpleNamespace(
                    isError=False,
                    content=[
                        SimpleNamespace(
                            type="text",
                            text=(
                                "1. 남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률 [현행]\n"
                                "- 법령ID: 000130\n- MST: 283455\n"
                            ),
                        )
                    ],
                )
            return SimpleNamespace(
                isError=False,
                content=[
                    SimpleNamespace(
                        type="text",
                        text=(
                            "법령명: 남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률\n"
                            "제7조 모집과 채용\n사업주는 모집ㆍ채용에서 차별하여서는 아니 된다."
                        ),
                    )
                ],
            )

    async def exercise() -> None:
        verifier = LiveLawVerifier(
            LawMcpConfig(transport="http", url="https://law.example/mcp")
        )
        # Use the session-level method to avoid opening a real endpoint while
        # still exercising tool discovery, exact-law parsing, and article lookup.
        from mcp_server.review import _references

        references = await verifier._verify_with_session(
            FakeSession(), _references(findings, engine.ruleset.statutes)
        )

        assert len(references) == 1
        result = references[0]
        assert result.status == "current_article_retrieved"
        assert result.current_law_id == "000130"
        assert result.current_mst == "283455"
        assert result.request.posting_text_sent is False
        assert calls == [
            (
                "search_law",
                {"query": "남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률"},
            ),
            ("get_law_text", {"mst": "283455", "jo": "제7조"}),
        ]

    anyio.run(exercise)


def test_live_law_verifier_rejects_search_without_current_marker() -> None:
    engine = FairpostEngine()
    findings = engine.check("여성만 지원 가능").findings

    class CurrentServerSession:
        async def list_tools(self):
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(name="search_law"),
                    SimpleNamespace(name="get_law_text"),
                ]
            )

        async def call_tool(self, name, _arguments):
            if name == "search_law":
                return SimpleNamespace(
                    isError=False,
                    content=[
                        SimpleNamespace(
                            type="text",
                            text=(
                                "검색 결과 (총 1건):\n\n"
                                "1. 남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률\n"
                                "   - 법령ID: 000130\n"
                                "   - MST: 283455\n"
                            ),
                        )
                    ],
                )
            return SimpleNamespace(
                isError=False,
                content=[
                    SimpleNamespace(
                        type="text",
                        text=(
                            "법령명: 남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률\n"
                            "제7조 모집과 채용\n사업주는 모집ㆍ채용에서 차별하여서는 아니 된다."
                        ),
                    )
                ],
            )

    async def exercise() -> None:
        verifier = LiveLawVerifier(
            LawMcpConfig(transport="http", url="https://law.example/mcp")
        )
        from mcp_server.review import _references

        results = await verifier._verify_with_session(
            CurrentServerSession(),
            _references(findings, engine.ruleset.statutes),
        )
        assert results[0].status == "current_law_not_resolved"
        assert results[0].current_law_id is None
        assert results[0].current_mst is None

    anyio.run(exercise)


@pytest.mark.parametrize("search", [
    "1. 테스트법 시행령 [현행]\n- 법령ID: 001\n- MST: 002\n",
    "1. 테스트법 [현행]\n2. 다른법 [현행]\n- 법령ID: 001\n- MST: 002\n",
    "1. 테스트법 [시행예정]\n- 법령ID: 001\n- MST: 002\n",
])
def test_current_identifiers_do_not_mix_laws_or_versions(search):
    from mcp_server.review import _current_identifiers
    assert _current_identifiers(search, "테스트법") is None


def test_law_mcp_configuration_is_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_law_environment(monkeypatch)

    assert LawMcpConfig.from_environment().transport == "disabled"


def test_law_mcp_configuration_requires_https_for_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_law_environment(monkeypatch)
    monkeypatch.setenv("FAIRPOST_KOREAN_LAW_MCP_URL", "http://law.example/mcp")

    with pytest.raises(ValueError, match="must use HTTPS"):
        LawMcpConfig.from_environment()


def test_law_mcp_configuration_accepts_bounded_stdio_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_law_environment(monkeypatch)
    monkeypatch.setenv("FAIRPOST_KOREAN_LAW_MCP_COMMAND", "law-mcp")
    monkeypatch.setenv("FAIRPOST_KOREAN_LAW_MCP_ARGS", '["serve", "--stdio"]')
    monkeypatch.setenv("FAIRPOST_KOREAN_LAW_MCP_ENV_ALLOWLIST", "LAW_OPEN_API_OC")

    config = LawMcpConfig.from_environment()

    assert config.transport == "stdio"
    assert config.command == "law-mcp"
    assert config.args == ("serve", "--stdio")
    assert config.env_names == ("LAW_OPEN_API_OC",)
