from __future__ import annotations

import json
from types import SimpleNamespace

import anyio
import httpx
import pytest

from core import FairpostEngine
from mcp_server.assisted_review import (
    AiApiConfig,
    AiReviewer,
    assisted_review_capability,
    prepare_assisted_review,
)
from mcp_server.review import LawMcpRequest, LawVerificationResult


AI_ENV = (
    "FAIRPOST_AI_PROVIDER",
    "FAIRPOST_AI_API_URL",
    "FAIRPOST_AI_API_KEY",
    "FAIRPOST_AI_MODEL",
    "FAIRPOST_AI_TIMEOUT_SECONDS",
    "FAIRPOST_AI_REASONING_EFFORT",
    "FAIRPOST_ANTHROPIC_API_URL",
    "FAIRPOST_ANTHROPIC_API_KEY",
    "FAIRPOST_ANTHROPIC_MODEL",
    "FAIRPOST_OPENAI_API_URL",
    "FAIRPOST_OPENAI_API_KEY",
    "FAIRPOST_OPENAI_MODEL",
    "FAIRPOST_GEMINI_API_URL",
    "FAIRPOST_GEMINI_API_KEY",
    "FAIRPOST_GEMINI_MODEL",
)
LAW_ENV = (
    "FAIRPOST_KOREAN_LAW_MCP_URL",
    "FAIRPOST_KOREAN_LAW_MCP_COMMAND",
)


def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*AI_ENV, *LAW_ENV):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("timeout", ["0", "301", "bad", "NaN"])
def test_ai_timeout_is_bounded(monkeypatch, timeout):
    _clear_environment(monkeypatch)
    monkeypatch.setenv("FAIRPOST_AI_TIMEOUT_SECONDS", timeout)
    with pytest.raises(ValueError, match="timeout"):
        AiApiConfig.from_environment()


def test_local_ai_options_are_passed_only_when_configured(monkeypatch):
    _clear_environment(monkeypatch)
    monkeypatch.setenv("FAIRPOST_AI_API_URL", "http://127.0.0.1:11434/v1/chat/completions")
    monkeypatch.setenv("FAIRPOST_AI_MODEL", "qwen3:8b")
    monkeypatch.setenv("FAIRPOST_AI_TIMEOUT_SECONDS", "180")
    captured = []
    async def respond(request):
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "검토 메모"}}]})
    async def exercise():
        reviewer = AiReviewer(AiApiConfig.from_environment(), transport=httpx.MockTransport(respond))
        await reviewer.review({})
        monkeypatch.setenv("FAIRPOST_AI_REASONING_EFFORT", "none")
        reviewer = AiReviewer(AiApiConfig.from_environment(), transport=httpx.MockTransport(respond))
        await reviewer.review({})
    anyio.run(exercise)
    assert "reasoning_effort" not in json.loads(captured[0].content)
    assert json.loads(captured[1].content)["reasoning_effort"] == "none"
    assert json.loads(captured[1].content)["messages"][-1]["content"].endswith("/no_think")
    assert captured[1].extensions["timeout"]["read"] == 180
    monkeypatch.setenv("FAIRPOST_AI_REASONING_EFFORT", "bad")
    with pytest.raises(ValueError, match="reasoning"):
        AiApiConfig.from_environment()


@pytest.mark.parametrize("content", [
    "<think>private reasoning</think>최종 메모",
    "private reasoning</think>최종 메모",
    [{"type": "text", "text": "<think>private reasoning</think>최종 메모"}],
])
def test_ai_response_excludes_reasoning_preamble(content):
    from mcp_server.assisted_review import _response_text
    assert _response_text({"choices": [{"message": {"content": content}}]}) == "최종 메모"


@pytest.mark.parametrize("content", ["<think>unfinished", "reasoning</think>  "])
def test_ai_response_requires_a_final_answer(content):
    from mcp_server.assisted_review import _response_text
    with pytest.raises(ValueError):
        _response_text({"choices": [{"message": {"content": content}}]})


def test_external_evidence_redacts_direct_identifiers() -> None:
    from mcp_server.assisted_review import _redact_external_text

    source = "담당자 test@example.com / 010-1234-5678 / 900101-1234567"
    redacted = _redact_external_text(source)

    assert "test@example.com" not in redacted
    assert "010-1234-5678" not in redacted
    assert "900101-1234567" not in redacted
    assert "[이메일 마스킹]" in redacted


def test_gemini_response_does_not_expose_thought_parts() -> None:
    from mcp_server.assisted_review import _response_text

    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"thought": True, "text": "비공개 추론"},
                        {"text": "최종 메모"},
                    ]
                }
            }
        ]
    }
    assert _response_text(body, provider="gemini") == "최종 메모"


def test_assisted_review_is_optional_and_disabled_without_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_environment(monkeypatch)

    capability = assisted_review_capability()

    assert capability["ready"] is False
    assert capability["ai_configured"] is False
    assert capability["law_mcp_configured"] is False
    assert "AI API" in capability["reason"]
    assert "Korean Law MCP" in capability["reason"]


def test_assisted_review_capability_requires_both_ai_and_law_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_environment(monkeypatch)
    monkeypatch.setenv(
        "FAIRPOST_AI_API_URL", "https://ai.example/v1/chat/completions"
    )
    monkeypatch.setenv("FAIRPOST_AI_API_KEY", "secret")
    monkeypatch.setenv("FAIRPOST_AI_MODEL", "review-model")
    assert assisted_review_capability()["ready"] is False

    monkeypatch.setenv(
        "FAIRPOST_KOREAN_LAW_MCP_URL", "https://law.example/mcp"
    )
    capability = assisted_review_capability()

    assert capability["ready"] is True
    assert capability["law_mcp_transport"] == "http"
    assert "공고문" in capability["privacy"]


def test_remote_ai_api_requires_https_and_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_environment(monkeypatch)
    monkeypatch.setenv(
        "FAIRPOST_AI_API_URL", "http://ai.example/v1/chat/completions"
    )
    with pytest.raises(ValueError, match="must use HTTPS"):
        AiApiConfig.from_environment()

    monkeypatch.setenv(
        "FAIRPOST_AI_API_URL", "https://ai.example/v1/chat/completions"
    )
    monkeypatch.setenv("FAIRPOST_AI_MODEL", "review-model")
    assert AiApiConfig.from_environment().configured is False


def test_capability_exposes_all_configured_native_providers_without_leaking_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_environment(monkeypatch)
    monkeypatch.setenv("FAIRPOST_ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("FAIRPOST_ANTHROPIC_MODEL", "claude-model")
    monkeypatch.setenv("FAIRPOST_OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("FAIRPOST_OPENAI_MODEL", "gpt-model")
    monkeypatch.setenv("FAIRPOST_GEMINI_API_KEY", "gemini-secret")
    monkeypatch.setenv("FAIRPOST_GEMINI_MODEL", "gemini-model")
    monkeypatch.setenv("FAIRPOST_KOREAN_LAW_MCP_URL", "https://law.example/mcp")

    capability = assisted_review_capability()

    assert capability["ready"] is True
    assert capability["default_provider"] == "anthropic"
    assert [item["id"] for item in capability["available_providers"]] == [
        "anthropic",
        "openai",
        "gemini",
    ]
    assert [item["label"] for item in capability["available_providers"]] == [
        "Claude",
        "GPT",
        "Gemini",
    ]
    serialized = json.dumps(capability, ensure_ascii=False)
    assert "anthropic-secret" not in serialized
    assert "openai-secret" not in serialized
    assert "gemini-secret" not in serialized


def test_native_provider_uses_review_ready_default_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_environment(monkeypatch)
    monkeypatch.setenv("FAIRPOST_OPENAI_API_KEY", "openai-secret")

    config = AiApiConfig.from_provider_environment("openai")

    assert config.configured is True
    assert config.model == "gpt-5.6-terra"


@pytest.mark.parametrize(
    ("provider", "url", "response_body"),
    [
        (
            "anthropic",
            "https://api.anthropic.com/v1/messages",
            {"content": [{"type": "text", "text": "Claude 검토 메모"}]},
        ),
        (
            "openai",
            "https://api.openai.com/v1/responses",
            {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "GPT 검토 메모"}],
                    }
                ]
            },
        ),
        (
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta",
            {"candidates": [{"content": {"parts": [{"text": "Gemini 검토 메모"}]}}]},
        ),
    ],
)
def test_native_ai_provider_request_contracts(provider, url, response_body):
    captured: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=response_body)

    async def exercise() -> str:
        reviewer = AiReviewer(
            AiApiConfig(
                url=url,
                api_key="provider-secret",
                model="provider-model",
                provider=provider,
            ),
            transport=httpx.MockTransport(respond),
        )
        result, truncated = await reviewer.review({"finding": "선별 근거"})
        assert truncated is False
        return result

    result = anyio.run(exercise)
    request = captured[0]
    payload = json.loads(request.content)
    assert "선별 근거" in json.dumps(payload, ensure_ascii=False)
    assert "provider-secret" not in request.content.decode("utf-8")

    if provider == "anthropic":
        assert result == "Claude 검토 메모"
        assert request.headers["x-api-key"] == "provider-secret"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert payload["system"]
        assert payload["messages"][0]["role"] == "user"
    elif provider == "openai":
        assert result == "GPT 검토 메모"
        assert request.headers["authorization"] == "Bearer provider-secret"
        assert payload["instructions"]
        assert payload["input"]
    else:
        assert result == "Gemini 검토 메모"
        assert request.headers["x-goog-api-key"] == "provider-secret"
        assert request.url.path.endswith("/models/provider-model:generateContent")
        assert payload["systemInstruction"]
        assert payload["contents"][0]["role"] == "user"


@pytest.mark.parametrize(
    ("provider", "prefix"),
    [
        ("anthropic", "FAIRPOST_ANTHROPIC"),
        ("openai", "FAIRPOST_OPENAI"),
        ("gemini", "FAIRPOST_GEMINI"),
    ],
)
def test_native_provider_key_cannot_be_sent_to_an_unofficial_host(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    prefix: str,
) -> None:
    _clear_environment(monkeypatch)
    monkeypatch.setenv(f"{prefix}_API_URL", "https://attacker.example/v1")
    monkeypatch.setenv(f"{prefix}_API_KEY", "secret")
    monkeypatch.setenv(f"{prefix}_MODEL", "model")
    with pytest.raises(ValueError, match="official host"):
        AiApiConfig.from_provider_environment(provider)


def test_assisted_review_sends_only_selected_evidence_to_ai() -> None:
    engine = FairpostEngine()
    captured_body: dict = {}

    class FakeVerifier:
        config = SimpleNamespace(transport="http")

        async def verify(self, findings, _statutes):
            finding = findings[0]
            return [
                LawVerificationResult(
                    finding_ids=[finding.id],
                    law=finding.basis.law or "",
                    article=finding.basis.article or "",
                    statute_id=finding.basis.statute_id or "",
                    snapshot_date=finding.basis.snapshot_date or "",
                    snapshot_effective_date=finding.basis.effective_date or "",
                    official_source_url="https://www.law.go.kr/법령/테스트",
                    official_id="000130",
                    request=LawMcpRequest(
                        search_tool="search_law",
                        search_arguments={"query": finding.basis.law or ""},
                        text_tool="get_law_text",
                        text_arguments_after_search={"mst": "283455", "jo": "제7조"},
                    ),
                    status="current_article_retrieved",
                    checked_at="2026-09-06T00:00:00+00:00",
                    current_law_id="000130",
                    current_mst="283455",
                    current_text=(
                        "남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률 제7조 "
                        "사업주는 모집과 채용에서 차별하여서는 아니 된다."
                    ),
                    response_truncated=False,
                    note="현행 조문을 조회했습니다.",
                )
            ]

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_body
        captured_body = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "사람이 적용 범위와 예외를 확인하세요."}}
                ]
            },
        )

    reviewer = AiReviewer(
        AiApiConfig(
            url="https://ai.example/v1/chat/completions",
            api_key="secret",
            model="review-model",
        ),
        transport=httpx.MockTransport(handler),
    )

    async def exercise() -> None:
        result = await prepare_assisted_review(
            engine,
            "여성만 지원 가능 private-posting-marker",
            verifier=FakeVerifier(),  # type: ignore[arg-type]
            reviewer=reviewer,
            organization_profile={
                "sector": "public",
                "public_entity_type": "public_corporation",
                "size": "300_plus",
            },
        )

        assert result.status == "completed"
        assert result.ai_called is True
        assert result.current_articles_retrieved == 1
        assert result.summary == "사람이 적용 범위와 예외를 확인하세요."
        assert result.organization_profile["sector_label"] == "공공기관"
        assert result.organization_profile["public_entity_type_label"] == "공기업"

    anyio.run(exercise)
    serialized = json.dumps(captured_body, ensure_ascii=False)
    assert "여성만" in serialized
    assert "제7조" in serialized
    assert "private-posting-marker" not in serialized
    assert "공기업·준정부기관의 경영에 관한 지침" in serialized
    assert "공공기관의 혁신에 관한 지침" in serialized
    assert "temperature" in captured_body


def test_assisted_review_does_not_call_ai_without_findings() -> None:
    class FakeVerifier:
        config = SimpleNamespace(transport="http")

        async def verify(self, _findings, _statutes):
            return []

    async def fail_if_called(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("AI API must not be called")

    reviewer = AiReviewer(
        AiApiConfig(
            url="https://ai.example/v1/chat/completions",
            api_key="secret",
            model="review-model",
        ),
        transport=httpx.MockTransport(fail_if_called),
    )

    async def exercise() -> None:
        result = await prepare_assisted_review(
            FairpostEngine(),
            "직무 관련 경력자를 채용합니다.",
            verifier=FakeVerifier(),  # type: ignore[arg-type]
            reviewer=reviewer,
        )
        assert result.status == "no_findings"
        assert result.ai_called is False

    anyio.run(exercise)


def test_assisted_review_http_endpoint_requires_explicit_toggle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_server import remote

    monkeypatch.setattr(
        remote,
        "assisted_review_capability",
        lambda: {
            "schema_version": "fairpost-assisted-review-capability-v1",
            "ready": True,
            "ai_configured": True,
            "law_mcp_configured": True,
            "law_mcp_transport": "http",
            "reason": "사용할 수 있습니다.",
        },
    )
    calls: list[tuple[str, str | None]] = []

    async def fake_prepare(_engine, text, **_kwargs):
        calls.append((text, _kwargs.get("ai_provider")))
        return SimpleNamespace(
            to_dict=lambda: {
                "schema_version": "fairpost-assisted-review-v1",
                "status": "completed",
                "summary": "검토 메모",
            }
        )

    monkeypatch.setattr(remote, "prepare_assisted_review", fake_prepare)

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=remote.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            rejected = await client.post(
                remote.ASSISTED_REVIEW_PATH,
                json={"text": "여성만 지원 가능"},
            )
            accepted = await client.post(
                remote.ASSISTED_REVIEW_PATH,
                json={
                    "assist_enabled": True,
                    "ai_provider": "gemini",
                    "text": "여성만 지원 가능",
                },
            )

        assert rejected.status_code == 400
        assert accepted.status_code == 200
        assert accepted.json()["summary"] == "검토 메모"

    anyio.run(exercise)
    assert calls == [("여성만 지원 가능", "gemini")]
