from __future__ import annotations

import anyio
import httpx
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from mcp_server.remote import (
    CLAUDE_MCP_PATH,
    MCP_PATH,
    RemoteSecurityMiddleware,
    _AnonymousRateLimiter,
    _public_claude_remote_mode,
    health,
)


ROOT = Path(__file__).resolve().parents[1]


def test_anonymous_rate_limit_keys_are_ephemeral_and_expire(
    monkeypatch,
) -> None:
    now = [0.0]
    monkeypatch.setattr("mcp_server.remote.time.monotonic", lambda: now[0])
    scope = {"client": ("192.0.2.10", 12345)}
    first = _AnonymousRateLimiter()
    second = _AnonymousRateLimiter()

    assert first.allow(scope, MCP_PATH, limit=1) is True
    assert first.allow(scope, MCP_PATH, limit=1) is False
    assert second.allow(scope, MCP_PATH, limit=1) is True
    assert set(first._entries) != set(second._entries)
    assert "192.0.2.10" not in repr(first._entries)

    now[0] = 61.0
    assert first.allow(scope, MCP_PATH, limit=1) is True
    assert len(first._entries) == 1


def test_remote_profiles_fail_closed_outside_vercel_without_opt_in(
    monkeypatch,
) -> None:
    called = False

    async def downstream(_scope, _receive, send) -> None:
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def exercise(path: str) -> int:
        transport = httpx.ASGITransport(app=RemoteSecurityMiddleware(downstream))
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            return (await client.post(path, content=b"{}")).status_code

    for name in (
        "VERCEL",
        "FAIRPOST_MCP_TOKEN",
        "FAIRPOST_ALLOW_PUBLIC_REMOTE",
        "FAIRPOST_ALLOW_PUBLIC_CLAUDE_REMOTE",
    ):
        monkeypatch.delenv(name, raising=False)

    assert anyio.run(exercise, MCP_PATH) == 503
    assert anyio.run(exercise, f"{MCP_PATH}/") == 503
    assert anyio.run(exercise, CLAUDE_MCP_PATH) == 503
    assert anyio.run(exercise, f"{CLAUDE_MCP_PATH}/future-route") == 503
    assert called is False


def test_public_claude_remote_requires_separate_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("FAIRPOST_MCP_TOKEN", raising=False)
    monkeypatch.setenv("FAIRPOST_ALLOW_PUBLIC_REMOTE", "1")
    monkeypatch.delenv("FAIRPOST_ALLOW_PUBLIC_CLAUDE_REMOTE", raising=False)
    assert _public_claude_remote_mode() is False

    monkeypatch.setenv("FAIRPOST_ALLOW_PUBLIC_CLAUDE_REMOTE", "1")
    assert _public_claude_remote_mode() is True


def test_health_reports_disabled_remote_profiles_without_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    for name in (
        "FAIRPOST_MCP_TOKEN",
        "FAIRPOST_ALLOW_PUBLIC_REMOTE",
        "FAIRPOST_ALLOW_PUBLIC_CLAUDE_REMOTE",
    ):
        monkeypatch.delenv(name, raising=False)

    response = anyio.run(health, None)
    payload = json.loads(response.body)

    assert payload["authentication"] == "disabled"
    assert payload["claude_readonly_authentication"] == "disabled"
    assert payload["anonymous_access_controls"] == {
        "enabled": False,
        "strategy": "per_instance_client_fixed_window",
        "requests_per_minute": 60,
        "stores_raw_client_address": False,
        "stores_request_content": False,
        "client_key": "ephemeral_hmac_sha256",
        "maximum_retention_seconds": 60,
        "distributed": False,
    }


def test_anonymous_remote_requests_are_rate_limited(monkeypatch) -> None:
    calls = 0

    async def downstream(_scope, _receive, send) -> None:
        nonlocal calls
        calls += 1
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def exercise() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=RemoteSecurityMiddleware(downstream))
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            return [
                await client.post(MCP_PATH, content=b"{}")
                for _index in range(3)
            ]

    monkeypatch.delenv("FAIRPOST_MCP_TOKEN", raising=False)
    monkeypatch.setenv("FAIRPOST_ALLOW_PUBLIC_REMOTE", "1")
    monkeypatch.setenv("FAIRPOST_PUBLIC_REQUESTS_PER_MINUTE", "2")
    responses = anyio.run(exercise)

    assert [response.status_code for response in responses] == [204, 204, 429]
    assert responses[-1].json() == {"error": "Public request rate limit exceeded"}
    assert responses[-1].headers["retry-after"] == "60"
    assert calls == 2


def test_invalid_anonymous_rate_limit_fails_closed(monkeypatch) -> None:
    called = False

    async def downstream(_scope, _receive, send) -> None:
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def exercise() -> httpx.Response:
        transport = httpx.ASGITransport(app=RemoteSecurityMiddleware(downstream))
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            return await client.post(MCP_PATH, content=b"{}")

    monkeypatch.delenv("FAIRPOST_MCP_TOKEN", raising=False)
    monkeypatch.setenv("FAIRPOST_ALLOW_PUBLIC_REMOTE", "1")
    monkeypatch.setenv("FAIRPOST_PUBLIC_REQUESTS_PER_MINUTE", "0")
    response = anyio.run(exercise)

    assert response.status_code == 500
    assert response.json() == {"error": "Invalid public request rate limit"}
    assert called is False


def test_chunked_request_body_is_limited_without_content_length(
    monkeypatch,
) -> None:
    called = False

    async def downstream(_scope, receive, send) -> None:
        nonlocal called
        called = True
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    async def chunks():
        yield b"1234"
        yield b"5678"

    async def exercise() -> None:
        transport = httpx.ASGITransport(
            app=RemoteSecurityMiddleware(downstream)
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post("/upload", content=chunks())

        assert response.status_code == 413
        assert response.json() == {"error": "Request body too large"}
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-frame-options"] == "DENY"

    monkeypatch.setenv("FAIRPOST_MAX_REQUEST_BYTES", "5")
    anyio.run(exercise)
    assert called is False


@pytest.mark.parametrize(
    "headers",
    [
        [(b"content-length", b"2"), (b"content-length", b"3")],
        [(b"content-length", b"2"), (b"transfer-encoding", b"chunked")],
    ],
)
def test_ambiguous_request_lengths_are_rejected_before_downstream(
    monkeypatch,
    headers: list[tuple[bytes, bytes]],
) -> None:
    called = False
    sent: list[dict[str, object]] = []

    async def downstream(_scope, _receive, _send) -> None:
        nonlocal called
        called = True

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    async def exercise() -> None:
        await RemoteSecurityMiddleware(downstream)(
            {
                "type": "http",
                "method": "POST",
                "path": "/upload",
                "headers": headers,
                "client": ("192.0.2.10", 12345),
            },
            receive,
            send,
        )

    monkeypatch.setenv("FAIRPOST_MAX_REQUEST_BYTES", "1024")
    anyio.run(exercise)

    assert called is False
    assert sent[0]["status"] == 400
    assert json.loads(sent[1]["body"]) == {"error": "Ambiguous request length"}


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("FAIRPOST_MCP_PATH", "api/mcp"),
        ("FAIRPOST_MCP_PATH", "/api/mcp/"),
        ("FAIRPOST_CLAUDE_MCP_PATH", "/api//claude-mcp"),
    ],
)
def test_invalid_mcp_paths_fail_during_import(variable: str, value: str) -> None:
    env = os.environ.copy()
    env.pop("FAIRPOST_MCP_PATH", None)
    env.pop("FAIRPOST_CLAUDE_MCP_PATH", None)
    env[variable] = value

    completed = subprocess.run(
        [sys.executable, "-c", "import mcp_server.server"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode != 0
    assert variable in completed.stderr


def test_security_headers_are_deduplicated_and_body_is_replayed(
    monkeypatch,
) -> None:
    received = b""

    async def downstream(_scope, receive, send) -> None:
        nonlocal received
        while True:
            message = await receive()
            received += message.get("body", b"")
            if not message.get("more_body", False):
                break
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"cache-control", b"private")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    async def chunks():
        yield b"1234"
        yield b"5678"

    async def exercise() -> None:
        transport = httpx.ASGITransport(
            app=RemoteSecurityMiddleware(downstream)
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post("/upload", content=chunks())

        assert response.status_code == 200
        assert response.headers.get_list("cache-control") == ["no-store"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"

    monkeypatch.setenv("FAIRPOST_MAX_REQUEST_BYTES", "8")
    anyio.run(exercise)
    assert received == b"12345678"


def test_duplicate_authorization_headers_are_rejected(monkeypatch) -> None:
    called = False

    async def downstream(_scope, _receive, send) -> None:
        nonlocal called
        called = True
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    async def exercise() -> None:
        transport = httpx.ASGITransport(
            app=RemoteSecurityMiddleware(downstream)
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post(
                MCP_PATH,
                content=b"{}",
                headers=[
                    ("Authorization", "Bearer test-token"),
                    ("Authorization", "Bearer test-token"),
                ],
            )

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers["cache-control"] == "no-store"

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("FAIRPOST_MCP_TOKEN", "test-token")
    anyio.run(exercise)
    assert called is False


def test_non_ascii_bearer_token_is_compared_as_raw_utf8_bytes(monkeypatch) -> None:
    token = "검토-토큰-2026"
    called = False

    async def downstream(_scope, _receive, send) -> None:
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def exercise() -> None:
        middleware = RemoteSecurityMiddleware(downstream)
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message) -> None:
            messages.append(message)

        await middleware(
            {
                "type": "http",
                "path": MCP_PATH,
                "headers": [
                    (b"authorization", f"Bearer {token}".encode("utf-8"))
                ],
            },
            receive,
            send,
        )

        assert messages[0]["status"] == 204

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("FAIRPOST_MCP_TOKEN", token)
    anyio.run(exercise)
    assert called is True


# --- /api/assisted-review access gate -------------------------------------
# Fake values only; real tokens never belong in tests, argv, or logs.
FAKE_ASSIST_TOKEN = "fake-assist-token-0000"
ASSIST_ORIGIN = "https://fairmcp.vercel.app"
ASSIST_GATE_ENV = (
    "FAIRPOST_ASSISTED_REVIEW_TOKEN",
    "FAIRPOST_ASSISTED_REVIEW_ALLOW_BROWSER",
    "FAIRPOST_ASSISTED_REVIEW_ALLOWED_ORIGINS",
    "FAIRPOST_MCP_ALLOWED_ORIGINS",
    "FAIRPOST_MCP_TOKEN",
)


def _assist_gate_setup(monkeypatch, *, ai_configured: bool = True) -> list[str]:
    from mcp_server import remote

    for name in ASSIST_GATE_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("FAIRPOST_ASSISTED_REVIEW_REQUESTS_PER_MINUTE", "1000")
    monkeypatch.setattr(
        remote,
        "assisted_review_capability",
        lambda: {
            "schema_version": "fairpost-assisted-review-capability-v1",
            "ready": ai_configured,
            "ai_configured": ai_configured,
            "available_providers": (
                [{"id": "openai", "label": "GPT", "model": "m"}]
                if ai_configured
                else []
            ),
            "law_mcp_configured": True,
            "law_mcp_transport": "http",
            "reason": "사용할 수 있습니다." if ai_configured else "설정 필요: AI API",
        },
    )
    calls: list[str] = []

    async def fake_prepare(_engine, text, **_kwargs):
        calls.append(text)
        return SimpleNamespace(
            to_dict=lambda: {"status": "completed", "summary": "메모"}
        )

    monkeypatch.setattr(remote, "prepare_assisted_review", fake_prepare)
    return calls


def _assist_request(method: str, headers: dict[str, str] | None = None):
    from mcp_server import remote

    async def exercise():
        transport = httpx.ASGITransport(app=remote.app)
        async with httpx.AsyncClient(
            transport=transport, base_url=ASSIST_ORIGIN
        ) as client:
            if method == "GET":
                return await client.get(
                    remote.ASSISTED_REVIEW_PATH, headers=headers or {}
                )
            return await client.post(
                remote.ASSISTED_REVIEW_PATH,
                headers=headers or {},
                json={"assist_enabled": True, "text": "여성만 지원 가능"},
            )

    return anyio.run(exercise)


def _assert_no_token_echo(response) -> None:
    assert FAKE_ASSIST_TOKEN not in response.text
    assert FAKE_ASSIST_TOKEN not in repr(dict(response.headers))


def test_assisted_review_fails_closed_without_access_control(monkeypatch) -> None:
    calls = _assist_gate_setup(monkeypatch)

    capability = _assist_request("GET")
    rejected = _assist_request(
        "POST",
        {"Origin": ASSIST_ORIGIN, "Sec-Fetch-Site": "same-origin"},
    )

    assert capability.status_code == 200
    assert capability.json()["ready"] is False
    assert "인증이 설정되지 않았습니다" in capability.json()["reason"]
    assert capability.json()["access_control"] == {
        "bearer_token": False,
        "browser_same_origin": False,
    }
    assert rejected.status_code == 403
    assert rejected.json()["ready"] is False
    assert "인증이 설정되지 않았습니다" in rejected.json()["reason"]
    assert calls == []


def test_health_reports_assisted_review_not_ready_without_access_control(
    monkeypatch,
) -> None:
    _assist_gate_setup(monkeypatch)

    payload = json.loads(anyio.run(health, None).body)

    assert payload["assisted_review"]["ready"] is False
    assert "인증이 설정되지 않았습니다" in payload["assisted_review"]["reason"]


def test_assisted_review_without_ai_keeps_existing_not_configured_response(
    monkeypatch,
) -> None:
    calls = _assist_gate_setup(monkeypatch, ai_configured=False)

    capability = _assist_request("GET")
    response = _assist_request("POST")

    assert capability.json()["reason"] == "설정 필요: AI API"
    assert response.status_code == 503
    assert calls == []


@pytest.mark.parametrize(
    ("authorization", "expected_status"),
    [
        (f"Bearer {FAKE_ASSIST_TOKEN}", 200),
        ("Bearer wrong-token", 403),
        (FAKE_ASSIST_TOKEN, 403),
        (None, 403),
    ],
    ids=["match", "mismatch", "missing-scheme", "missing"],
)
def test_assisted_review_bearer_token(
    monkeypatch, authorization, expected_status
) -> None:
    calls = _assist_gate_setup(monkeypatch)
    monkeypatch.setenv("FAIRPOST_ASSISTED_REVIEW_TOKEN", FAKE_ASSIST_TOKEN)
    headers = {"Authorization": authorization} if authorization else {}

    response = _assist_request("POST", headers)

    assert response.status_code == expected_status
    _assert_no_token_echo(response)
    if expected_status == 200:
        assert calls == ["여성만 지원 가능"]
    else:
        assert calls == []
        assert response.json()["ready"] is False
        assert "사내 클라이언트 인증" in response.json()["reason"]


def test_assisted_review_rejects_duplicate_bearer_headers(monkeypatch) -> None:
    from mcp_server import remote

    calls = _assist_gate_setup(monkeypatch)
    monkeypatch.setenv("FAIRPOST_ASSISTED_REVIEW_TOKEN", FAKE_ASSIST_TOKEN)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=remote.app),
            base_url=ASSIST_ORIGIN,
        ) as client:
            return await client.post(
                remote.ASSISTED_REVIEW_PATH,
                headers=[
                    ("Authorization", f"Bearer {FAKE_ASSIST_TOKEN}"),
                    ("Authorization", f"Bearer {FAKE_ASSIST_TOKEN}"),
                ],
                json={"assist_enabled": True, "text": "x"},
            )

    assert anyio.run(exercise).status_code == 403
    assert calls == []


def test_token_only_deployment_reports_not_ready_to_browsers(monkeypatch) -> None:
    _assist_gate_setup(monkeypatch)
    monkeypatch.setenv("FAIRPOST_ASSISTED_REVIEW_TOKEN", FAKE_ASSIST_TOKEN)

    browser = _assist_request("GET")
    internal = _assist_request(
        "GET", {"Authorization": f"Bearer {FAKE_ASSIST_TOKEN}"}
    )

    assert browser.json()["ready"] is False
    assert "사내 클라이언트 인증" in browser.json()["reason"]
    assert internal.json()["ready"] is True
    for response in (browser, internal):
        _assert_no_token_echo(response)
        assert response.json()["access_control"] == {
            "bearer_token": True,
            "browser_same_origin": False,
        }


@pytest.mark.parametrize(
    ("allow_browser", "headers", "expected_status"),
    [
        ("1", {"Origin": ASSIST_ORIGIN, "Sec-Fetch-Site": "same-origin"}, 200),
        ("", {"Origin": ASSIST_ORIGIN, "Sec-Fetch-Site": "same-origin"}, 403),
        ("1", {"Origin": "https://evil.example", "Sec-Fetch-Site": "same-origin"}, 403),
        ("1", {"Origin": f"{ASSIST_ORIGIN}.evil.example", "Sec-Fetch-Site": "same-origin"}, 403),
        ("1", {"Origin": ASSIST_ORIGIN, "Sec-Fetch-Site": "cross-site"}, 403),
        ("1", {"Origin": ASSIST_ORIGIN, "Sec-Fetch-Site": "same-site"}, 403),
        ("1", {"Origin": ASSIST_ORIGIN}, 403),
        ("1", {"Sec-Fetch-Site": "same-origin"}, 403),
    ],
    ids=[
        "allowed",
        "browser-off",
        "origin-mismatch",
        "origin-suffix",
        "cross-site",
        "same-site",
        "no-fetch-metadata",
        "no-origin",
    ],
)
def test_assisted_review_browser_same_origin_access(
    monkeypatch, allow_browser, headers, expected_status
) -> None:
    calls = _assist_gate_setup(monkeypatch)
    monkeypatch.setenv("FAIRPOST_ASSISTED_REVIEW_ALLOW_BROWSER", allow_browser)

    response = _assist_request("POST", headers)

    assert response.status_code == expected_status
    assert calls == (["여성만 지원 가능"] if expected_status == 200 else [])
    if expected_status == 403:
        assert response.json()["ready"] is False
        assert response.json()["reason"]


def test_browser_access_reports_ready_when_enabled(monkeypatch) -> None:
    _assist_gate_setup(monkeypatch)
    monkeypatch.setenv("FAIRPOST_ASSISTED_REVIEW_ALLOW_BROWSER", "true")

    capability = _assist_request("GET").json()

    assert capability["ready"] is True
    assert capability["access_control"] == {
        "bearer_token": False,
        "browser_same_origin": True,
    }


def test_explicit_assisted_origins_replace_mcp_origins(monkeypatch) -> None:
    from mcp_server import remote

    _assist_gate_setup(monkeypatch)
    monkeypatch.setenv("FAIRPOST_MCP_ALLOWED_ORIGINS", "https://mcp-only.example")
    assert "https://mcp-only.example" in remote._assisted_review_allowed_origins()
    assert all("*" not in value for value in remote._assisted_review_allowed_origins())

    monkeypatch.setenv(
        "FAIRPOST_ASSISTED_REVIEW_ALLOWED_ORIGINS",
        "https://review.example, https://*.example",
    )
    assert remote._assisted_review_allowed_origins() == ("https://review.example",)

    monkeypatch.setenv("FAIRPOST_ASSISTED_REVIEW_ALLOW_BROWSER", "1")
    assert _assist_request(
        "POST", {"Origin": ASSIST_ORIGIN, "Sec-Fetch-Site": "same-origin"}
    ).status_code == 403


def test_browser_access_without_exact_origins_stays_closed(monkeypatch) -> None:
    calls = _assist_gate_setup(monkeypatch)
    monkeypatch.delenv("VERCEL")
    monkeypatch.setenv("FAIRPOST_ASSISTED_REVIEW_ALLOW_BROWSER", "1")

    capability = _assist_request("GET").json()
    response = _assist_request(
        "POST",
        {"Origin": "http://127.0.0.1:8000", "Sec-Fetch-Site": "same-origin"},
    )

    assert capability["ready"] is False
    assert capability["access_control"]["browser_same_origin"] is False
    assert response.status_code == 403
    assert calls == []


def test_token_with_browser_access_accepts_either_path(monkeypatch) -> None:
    calls = _assist_gate_setup(monkeypatch)
    monkeypatch.setenv("FAIRPOST_ASSISTED_REVIEW_TOKEN", FAKE_ASSIST_TOKEN)
    monkeypatch.setenv("FAIRPOST_ASSISTED_REVIEW_ALLOW_BROWSER", "yes")

    via_token = _assist_request(
        "POST",
        {"Authorization": f"Bearer {FAKE_ASSIST_TOKEN}", "Sec-Fetch-Site": "cross-site"},
    )
    via_browser = _assist_request(
        "POST", {"Origin": ASSIST_ORIGIN, "Sec-Fetch-Site": "same-origin"}
    )
    denied = _assist_request(
        "POST", {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
    )

    assert (via_token.status_code, via_browser.status_code) == (200, 200)
    assert denied.status_code == 403
    assert "사내 클라이언트 인증" in denied.json()["reason"]
    _assert_no_token_echo(denied)
    assert len(calls) == 2


def test_local_runtime_assisted_review_is_not_gated(monkeypatch) -> None:
    from mcp_server.local_runtime import create_local_app

    calls = _assist_gate_setup(monkeypatch)
    monkeypatch.delenv("VERCEL")

    async def exercise():
        # No lifespan: the MCP session manager may run only once per process,
        # and the assisted-review route does not depend on it.
        app = create_local_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            capability = await client.get("/api/assisted-review")
            accepted = await client.post(
                "/api/assisted-review",
                headers={
                    "Origin": "http://127.0.0.1:8000",
                    "Sec-Fetch-Site": "same-origin",
                },
                json={"assist_enabled": True, "text": "여성만 지원 가능"},
            )
            cross = await client.post(
                "/api/assisted-review",
                headers={"Sec-Fetch-Site": "cross-site"},
                json={"assist_enabled": True, "text": "x"},
            )
        return capability, accepted, cross

    capability, accepted, cross = anyio.run(exercise)

    assert capability.json()["ready"] is True
    assert "access_control" not in capability.json()
    assert accepted.status_code == 200
    assert cross.status_code == 403
    assert calls == ["여성만 지원 가능"]
