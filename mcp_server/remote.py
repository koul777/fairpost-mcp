from __future__ import annotations

from collections import OrderedDict
from contextlib import asynccontextmanager
import hmac
import json
import os
from threading import Lock
import time
from typing import Any

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .build_identity import runtime_source_fingerprint, runtime_source_manifest
from .server import (
    CLAUDE_MCP_PATH,
    MCP_PATH,
    claude_mcp,
    engine,
    public_mcp,
)


DEFAULT_PUBLIC_REQUESTS_PER_MINUTE = 60
MAX_RATE_LIMIT_CLIENTS = 2048


def _public_requests_per_minute() -> int:
    try:
        value = int(
            os.environ.get(
                "FAIRPOST_PUBLIC_REQUESTS_PER_MINUTE",
                str(DEFAULT_PUBLIC_REQUESTS_PER_MINUTE),
            )
        )
    except ValueError as exc:
        raise ValueError("invalid public request rate limit") from exc
    if not 1 <= value <= 10_000:
        raise ValueError("invalid public request rate limit")
    return value


def _is_endpoint_path(path: str, endpoint: str) -> bool:
    return path == endpoint or path.startswith(f"{endpoint}/")


class _AnonymousRateLimiter:
    """Bound anonymous requests without retaining client addresses or content."""

    def __init__(self) -> None:
        self._entries: OrderedDict[bytes, int] = OrderedDict()
        self._lock = Lock()
        self._key = os.urandom(32)
        self._window: int | None = None

    def allow(self, scope: Scope, path: str, *, limit: int) -> bool:
        client = scope.get("client")
        client_host = (
            str(client[0])
            if isinstance(client, (list, tuple)) and client
            else "unknown"
        )
        key = hmac.digest(
            self._key,
            f"{client_host}\0{path}".encode("utf-8", errors="replace"),
            "sha256",
        )
        window = int(time.monotonic() // 60)
        with self._lock:
            if self._window != window:
                self._entries.clear()
                self._window = window
            count = self._entries.pop(key, 0)
            count += 1
            self._entries[key] = count
            while len(self._entries) > MAX_RATE_LIMIT_CLIENTS:
                self._entries.popitem(last=False)
            return count <= limit


def _health_path() -> str:
    return "/api/health" if os.environ.get("VERCEL") else "/health"


def _root_path() -> str:
    return "/api" if os.environ.get("VERCEL") else "/"


def _public_remote_mode() -> bool:
    return not os.environ.get("FAIRPOST_MCP_TOKEN") and os.environ.get(
        "FAIRPOST_ALLOW_PUBLIC_REMOTE", ""
    ).casefold() in {
        "1",
        "true",
        "yes",
    }


def _public_claude_remote_mode() -> bool:
    return not os.environ.get("FAIRPOST_MCP_TOKEN") and os.environ.get(
        "FAIRPOST_ALLOW_PUBLIC_CLAUDE_REMOTE", ""
    ).casefold() in {"1", "true", "yes"}


async def health(_request: Any) -> JSONResponse:
    token_required = bool(os.environ.get("FAIRPOST_MCP_TOKEN"))
    public = _public_remote_mode()
    claude_public = _public_claude_remote_mode()
    source_fingerprint = runtime_source_fingerprint(
        ruleset_version=engine.ruleset.version,
        matching_version=engine.ruleset.matching_version,
    )
    try:
        public_requests_per_minute = _public_requests_per_minute()
    except ValueError:
        public_requests_per_minute = None
    return JSONResponse(
        {
            "name": "fairpost",
            "status": "ok",
            "transport": "streamable-http",
            "stateless": True,
            "mcp_endpoint": MCP_PATH,
            "claude_readonly_mcp_endpoint": CLAUDE_MCP_PATH,
            "authentication": (
                "bearer" if token_required else "none" if public else "disabled"
            ),
            "claude_readonly_authentication": (
                "bearer"
                if token_required
                else "none" if claude_public else "disabled"
            ),
            "answer_store": "disabled_on_remote_endpoint",
            "remote_tool_profile": "read_only",
            "anonymous_access_controls": {
                "enabled": public or claude_public,
                "strategy": "per_instance_client_fixed_window",
                "requests_per_minute": public_requests_per_minute,
                "stores_raw_client_address": False,
                "stores_request_content": False,
                "client_key": "ephemeral_hmac_sha256",
                "maximum_retention_seconds": 60,
                "distributed": False,
            },
            "ruleset_version": engine.ruleset.version,
            "matching_version": engine.ruleset.matching_version,
            "runtime_source_fingerprint": source_fingerprint,
            "runtime_source_manifest": runtime_source_manifest(),
            "processing_notice": (
                "공고문은 이 Vercel 배포의 서버 함수에서 처리되며 "
                "FairPost는 공고문 원문을 영속 저장하지 않습니다."
            ),
        },
        headers={"Cache-Control": "no-store"},
    )


class RemoteSecurityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._anonymous_rate_limiter = _AnonymousRateLimiter()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        mcp_request = _is_endpoint_path(path, MCP_PATH)
        claude_mcp_request = _is_endpoint_path(path, CLAUDE_MCP_PATH)
        token = os.environ.get("FAIRPOST_MCP_TOKEN", "")
        public_remote = _public_remote_mode()
        public_claude_remote = _public_claude_remote_mode()
        if (
            mcp_request
            and not token
            and not public_remote
        ):
            await self._json(
                send,
                503,
                {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32002,
                        "message": (
                            "Remote MCP authentication is not configured"
                        ),
                    },
                    "id": None,
                },
            )
            return
        if (
            claude_mcp_request
            and not token
            and not public_claude_remote
        ):
            await self._json(
                send,
                503,
                {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32002,
                        "message": (
                            "Public Claude MCP is not enabled"
                        ),
                    },
                    "id": None,
                },
            )
            return
        if token and (mcp_request or claude_mcp_request):
            authorization_values = [
                value
                for key, value in scope.get("headers", [])
                if key.lower() == b"authorization"
            ]
            supplied = (
                authorization_values[0]
                if len(authorization_values) == 1
                else b""
            )
            expected = f"Bearer {token}".encode("utf-8")
            if not hmac.compare_digest(supplied, expected):
                await self._json(
                    send,
                    401,
                    {
                        "jsonrpc": "2.0",
                        "error": {
                            "code": -32001,
                            "message": "Unauthorized",
                        },
                        "id": None,
                    },
                    extra_headers=[(b"www-authenticate", b"Bearer")],
                )
                return

        anonymous_endpoint = (
            (mcp_request and public_remote)
            or (claude_mcp_request and public_claude_remote)
        )
        if anonymous_endpoint:
            try:
                requests_per_minute = _public_requests_per_minute()
            except ValueError:
                await self._json(
                    send,
                    500,
                    {"error": "Invalid public request rate limit"},
                )
                return
            if not self._anonymous_rate_limiter.allow(
                scope,
                CLAUDE_MCP_PATH if claude_mcp_request else MCP_PATH,
                limit=requests_per_minute,
            ):
                await self._json(
                    send,
                    429,
                    {"error": "Public request rate limit exceeded"},
                    extra_headers=[(b"retry-after", b"60")],
                )
                return

        try:
            max_bytes = int(
                os.environ.get("FAIRPOST_MAX_REQUEST_BYTES", "1048576")
            )
            if max_bytes < 1:
                raise ValueError
        except ValueError:
            await self._json(send, 500, {"error": "Invalid request size limit"})
            return

        content_lengths = [
            value
            for key, value in scope.get("headers", [])
            if key.lower() == b"content-length"
        ]
        transfer_encoding_present = any(
            key.lower() == b"transfer-encoding"
            for key, _value in scope.get("headers", [])
        )
        if len(content_lengths) > 1 or (
            content_lengths and transfer_encoding_present
        ):
            await self._json(send, 400, {"error": "Ambiguous request length"})
            return
        if content_lengths:
            try:
                declared_length = content_lengths[0].decode("ascii")
                if not declared_length.isdecimal():
                    raise ValueError
                if int(declared_length) > max_bytes:
                    await self._json(send, 413, {"error": "Request body too large"})
                    return
            except (UnicodeDecodeError, ValueError):
                await self._json(send, 400, {"error": "Invalid Content-Length"})
                return

        bounded_receive = await self._buffer_request(receive, max_bytes=max_bytes)
        if bounded_receive is None:
            await self._json(send, 413, {"error": "Request body too large"})
            return

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                security_header_names = {
                    b"cache-control",
                    b"x-content-type-options",
                    b"x-frame-options",
                    b"referrer-policy",
                }
                response_headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() not in security_header_names
                ]
                response_headers.extend(
                    [
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                    ]
                )
                message["headers"] = response_headers
            await send(message)

        await self.app(scope, bounded_receive, secure_send)

    @staticmethod
    async def _buffer_request(
        receive: Receive,
        *,
        max_bytes: int,
    ) -> Receive | None:
        messages: list[Message] = []
        total = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            total += len(message.get("body", b""))
            if total > max_bytes:
                return None
            if not message.get("more_body", False):
                break

        index = 0

        async def replay() -> Message:
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return await receive()

        return replay

    @staticmethod
    async def _json(
        send: Send,
        status: int,
        payload: dict[str, Any],
        *,
        extra_headers: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"no-referrer"),
        ]
        headers.extend(extra_headers or [])
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": body})


# A shared Bearer token is authentication, not tenant authorization. Remote
# deployments therefore expose only the stateless analysis profile regardless
# of whether access is public or token-protected. The four-tool MCP, including
# answer persistence, remains available only through the local five-tool entrypoint.
_mcp_app = public_mcp.streamable_http_app()
_claude_mcp_app = claude_mcp.streamable_http_app()
_routes = [
    Route(_root_path(), health, methods=["GET"]),
    Route(_health_path(), health, methods=["GET"]),
    *_mcp_app.routes,
    *_claude_mcp_app.routes,
]


@asynccontextmanager
async def _lifespan(app: Starlette):
    async with _mcp_app.router.lifespan_context(app):
        async with _claude_mcp_app.router.lifespan_context(app):
            yield


app = Starlette(routes=_routes, lifespan=_lifespan)
app.add_middleware(RemoteSecurityMiddleware)
