from __future__ import annotations

import anyio
import httpx
import json
import os
from pathlib import Path
import subprocess
import sys

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
