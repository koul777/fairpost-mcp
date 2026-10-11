from __future__ import annotations

from types import SimpleNamespace

import anyio
import httpx
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
    assert catalog.as_of == "2026-09-12"
    assert catalog.authority == "guidance_not_law"
    assert len(catalog.sources) >= 8
    assert len(catalog.controls) >= 9
    assert all(
        source.source_url.startswith("https://www.ncs.go.kr/")
        for source in catalog.sources
    )
    assert all(
        "ncs-2026-guide" not in control.source_ids
        for control in catalog.controls
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


# --- time budget, per-reference isolation and privacy-safe logging ---------
TWO_LAW_POSTING = "여성만 지원 가능\n만 35세 이하"
AGE_LAW = "고용상 연령차별금지 및 고령자고용촉진에 관한 법률"
SEX_LAW = "남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률"


class _ScriptedLawSession:
    """Answers search/text calls; ``behavior`` maps a law name to a failure."""

    def __init__(self, behavior: dict[str, str] | None = None) -> None:
        self.behavior = behavior or {}

    async def list_tools(self):
        return SimpleNamespace(
            tools=[SimpleNamespace(name="search_law"), SimpleNamespace(name="get_law_text")]
        )

    async def call_tool(self, name, arguments):
        law = arguments.get("query") or self._law_for_mst(arguments.get("mst"))
        action = self.behavior.get(law)
        if action == "hang":
            await anyio.sleep_forever()
        if action == "raise":
            raise RuntimeError("private upstream detail")
        if action == "http429":
            request = httpx.Request("POST", "https://law.example/mcp")
            raise httpx.HTTPStatusError(
                "rate limited",
                request=request,
                response=httpx.Response(429, request=request),
            )
        if name == "search_law":
            mst = "111" if law == AGE_LAW else "222"
            return SimpleNamespace(
                isError=False,
                content=[
                    SimpleNamespace(
                        type="text",
                        text=f"1. {law} [현행]\n- 법령ID: 000{mst}\n- MST: {mst}\n",
                    )
                ],
            )
        return SimpleNamespace(
            isError=False,
            content=[
                SimpleNamespace(
                    type="text",
                    text=f"법령명: {law}\n{arguments['jo']} 조문 원문",
                )
            ],
        )

    @staticmethod
    def _law_for_mst(mst):
        return AGE_LAW if mst == "111" else SEX_LAW


def _scripted_verifier(session, *, budget_seconds: float = 5.0) -> LiveLawVerifier:
    from contextlib import asynccontextmanager

    verifier = LiveLawVerifier(
        LawMcpConfig(transport="http", url="https://law.example/mcp"),
        budget_seconds=budget_seconds,
    )

    @asynccontextmanager
    async def fake_session():
        yield session

    verifier._session = fake_session  # type: ignore[method-assign]
    return verifier


def _verify(verifier: LiveLawVerifier):
    engine = FairpostEngine()
    findings = engine.check(TWO_LAW_POSTING).findings

    async def exercise():
        return await verifier.verify(findings, engine.ruleset.statutes)

    return anyio.run(exercise)


def test_two_law_posting_has_two_references() -> None:
    from mcp_server.review import _references

    engine = FairpostEngine()
    references = _references(
        engine.check(TWO_LAW_POSTING).findings, engine.ruleset.statutes
    )
    assert [item.law for item in references] == [AGE_LAW, SEX_LAW]


def test_hanging_law_session_returns_upstream_unavailable_within_budget() -> None:
    import time

    verifier = _scripted_verifier(
        _ScriptedLawSession({AGE_LAW: "hang", SEX_LAW: "hang"}), budget_seconds=0.3
    )
    started = time.monotonic()
    results = _verify(verifier)

    assert time.monotonic() - started < 3
    assert [item.status for item in results] == ["upstream_unavailable"] * 2
    assert all("제한 시간(0.3초)" in item.note for item in results)


def test_budget_keeps_articles_retrieved_before_it_expired() -> None:
    verifier = _scripted_verifier(
        _ScriptedLawSession({AGE_LAW: "hang"}), budget_seconds=0.3
    )

    results = _verify(verifier)

    assert [item.law for item in results] == [AGE_LAW, SEX_LAW]
    assert results[0].status == "upstream_unavailable"
    assert results[1].status == "current_article_retrieved"
    assert results[1].current_mst == "222"


def test_one_failed_reference_does_not_discard_the_others() -> None:
    results = _verify(_scripted_verifier(_ScriptedLawSession({SEX_LAW: "raise"})))

    assert results[0].status == "current_article_retrieved"
    assert results[1].status == "upstream_unavailable"
    assert results[1].note == "Korean Law MCP 조회를 완료하지 못했습니다(RuntimeError)."
    assert "private upstream detail" not in results[1].note


def test_law_lookup_failure_logs_status_without_private_values(caplog) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="fairpost")
    verifier = _scripted_verifier(_ScriptedLawSession({AGE_LAW: "http429"}))
    verifier.config = LawMcpConfig(
        transport="http",
        url="https://law.example/mcp",
        bearer_token="law-secret-token",
    )

    results = _verify(verifier)

    assert results[0].status == "upstream_unavailable"
    assert "HTTP 429" in results[0].note
    failures = [r for r in caplog.records if "law_mcp_lookup_failed" in r.getMessage()]
    assert len(failures) == 1
    line = failures[0].getMessage()
    assert "upstream_status=429" in line
    assert "exception=HTTPStatusError" in line
    assert "provider=korean_law_mcp" in line
    assert "elapsed_ms=" in line
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for private in ("law-secret-token", "여성만", "35세", "law.example", AGE_LAW):
        assert private not in logged
    assert any("law_mcp_verify_completed" in r.getMessage() for r in caplog.records)


def test_session_failure_marks_every_reference_with_status() -> None:
    from contextlib import asynccontextmanager

    verifier = LiveLawVerifier(
        LawMcpConfig(transport="http", url="https://law.example/mcp")
    )

    @asynccontextmanager
    async def failing_session():
        request = httpx.Request("POST", "https://law.example/mcp")
        raise httpx.HTTPStatusError(
            "unavailable", request=request, response=httpx.Response(503, request=request)
        )
        yield  # pragma: no cover

    verifier._session = failing_session  # type: ignore[method-assign]
    results = _verify(verifier)

    assert [item.status for item in results] == ["upstream_unavailable"] * 2
    assert all("HTTPStatusError, HTTP 503" in item.note for item in results)


def test_law_budget_must_be_positive() -> None:
    with pytest.raises(ValueError, match="시간 예산"):
        LiveLawVerifier(LawMcpConfig(transport="disabled"), budget_seconds=0)
