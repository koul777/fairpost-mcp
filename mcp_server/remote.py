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
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .build_identity import runtime_source_fingerprint, runtime_source_manifest
from .assisted_review import (
    assisted_review_capability,
    prepare_assisted_review,
)
from .server import (
    CLAUDE_MCP_PATH,
    MCP_PATH,
    _transport_security_from_environment,
    claude_mcp,
    engine,
    public_mcp,
)


DEFAULT_PUBLIC_REQUESTS_PER_MINUTE = 60
DEFAULT_ASSISTED_REVIEW_REQUESTS_PER_MINUTE = 5
MAX_RATE_LIMIT_CLIENTS = 2048
ASSISTED_REVIEW_PATH = "/api/assisted-review"
MAX_ASSISTED_POSTING_CHARS = 100_000
ASSISTED_REVIEW_AUTH_NOT_CONFIGURED = (
    "보강 실행 인증이 설정되지 않았습니다. 서버 운영자가 사내 클라이언트 토큰 "
    "또는 허용된 웹 출처를 설정해야 합니다."
)
ASSISTED_REVIEW_TOKEN_REQUIRED = (
    "이 배포의 보강 실행은 사내 클라이언트 인증이 필요해 웹 화면에서는 "
    "사용할 수 없습니다."
)
ASSISTED_REVIEW_BROWSER_ORIGIN_REQUIRED = (
    "보강 실행은 허용된 FairPost 웹 화면(동일 출처)에서만 요청할 수 있습니다."
)
ASSISTED_REVIEW_ORIGIN_OR_TOKEN_REQUIRED = (
    "보강 실행은 허용된 FairPost 웹 화면(동일 출처) 또는 사내 클라이언트 "
    "인증으로만 요청할 수 있습니다."
)


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


def _assisted_review_requests_per_minute() -> int:
    try:
        value = int(
            os.environ.get(
                "FAIRPOST_ASSISTED_REVIEW_REQUESTS_PER_MINUTE",
                str(DEFAULT_ASSISTED_REVIEW_REQUESTS_PER_MINUTE),
            )
        )
    except ValueError as exc:
        raise ValueError("invalid assisted review rate limit") from exc
    if not 1 <= value <= 1_000:
        raise ValueError("invalid assisted review rate limit")
    return value


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().casefold() in {"1", "true", "yes"}


def _assisted_review_token() -> str:
    return os.environ.get("FAIRPOST_ASSISTED_REVIEW_TOKEN", "").strip()


def _assisted_review_allowed_origins() -> tuple[str, ...]:
    """Exact browser origins allowed to spend the server's AI provider keys.

    Without an explicit list the MCP transport origin settings are reused.
    Wildcard entries (such as the loopback ``http://127.0.0.1:*`` defaults)
    can never match exactly, so they are dropped rather than expanded.
    """

    raw = os.environ.get("FAIRPOST_ASSISTED_REVIEW_ALLOWED_ORIGINS", "")
    values = [value.strip() for value in raw.split(",") if value.strip()]
    if not values:
        values = list(_transport_security_from_environment().allowed_origins)
    return tuple(
        dict.fromkeys(
            value.rstrip("/")
            for value in values
            if "*" not in value and value.casefold() != "null"
        )
    )


def _assisted_review_browser_access_enabled() -> bool:
    return _env_flag("FAIRPOST_ASSISTED_REVIEW_ALLOW_BROWSER") and bool(
        _assisted_review_allowed_origins()
    )


def _single_header(scope_headers: Any, name: bytes) -> bytes | None:
    values = [value for key, value in scope_headers if key.lower() == name]
    return values[0] if len(values) == 1 else None


def _assisted_review_bearer_matches(scope_headers: Any) -> bool:
    token = _assisted_review_token()
    if not token:
        return False
    supplied = _single_header(scope_headers, b"authorization") or b""
    expected = f"Bearer {token}".encode("utf-8")
    return hmac.compare_digest(supplied, expected)


def _assisted_review_browser_request_allowed(scope_headers: Any) -> bool:
    origin = _single_header(scope_headers, b"origin")
    fetch_site = _single_header(scope_headers, b"sec-fetch-site")
    if origin is None or fetch_site != b"same-origin":
        return False
    try:
        origin_text = origin.decode("ascii")
    except UnicodeDecodeError:
        return False
    return origin_text in _assisted_review_allowed_origins()


def _assisted_review_access_denial(scope_headers: Any) -> str | None:
    """Return a reason when a request may not spend AI provider keys.

    Fail closed: with neither control configured every request is denied.
    The reason never echoes supplied credentials or configured secrets.
    """

    token_configured = bool(_assisted_review_token())
    browser_enabled = _assisted_review_browser_access_enabled()
    if not token_configured and not browser_enabled:
        return ASSISTED_REVIEW_AUTH_NOT_CONFIGURED
    if _assisted_review_bearer_matches(scope_headers):
        return None
    if browser_enabled:
        if _assisted_review_browser_request_allowed(scope_headers):
            return None
        return (
            ASSISTED_REVIEW_ORIGIN_OR_TOKEN_REQUIRED
            if token_configured
            else ASSISTED_REVIEW_BROWSER_ORIGIN_REQUIRED
        )
    return ASSISTED_REVIEW_TOKEN_REQUIRED


def _gated_assisted_review_capability(scope_headers: Any) -> dict[str, Any]:
    """Assisted review capability as seen by a network caller.

    With an AI provider configured the deployment reports ``ready: false``
    until an access control is configured. A token-only deployment also
    reports ``ready: false`` to callers without the token, because a browser
    page cannot hold that secret. Browsers omit ``Origin`` on same-origin GET,
    so the origin itself is enforced on POST.
    """

    capability = dict(assisted_review_capability())
    token_configured = bool(_assisted_review_token())
    browser_enabled = _assisted_review_browser_access_enabled()
    capability["access_control"] = {
        "bearer_token": token_configured,
        "browser_same_origin": browser_enabled,
    }
    if not capability.get("ai_configured"):
        return capability
    reason: str | None = None
    if not token_configured and not browser_enabled:
        reason = ASSISTED_REVIEW_AUTH_NOT_CONFIGURED
    elif not browser_enabled and not _assisted_review_bearer_matches(
        scope_headers
    ):
        reason = ASSISTED_REVIEW_TOKEN_REQUIRED
    if reason is not None:
        capability["ready"] = False
        capability["reason"] = reason
    return capability


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
            "assisted_review": _gated_assisted_review_capability(
                getattr(_request, "scope", {}).get("headers", [])
            ),
        },
        headers={"Cache-Control": "no-store"},
    )


async def assisted_review(request: Request) -> JSONResponse:
    """Network endpoint: AI provider keys are spent only by authorized callers."""

    headers = request.scope.get("headers", [])
    if request.method == "GET":
        return JSONResponse(
            _gated_assisted_review_capability(headers),
            headers={"Cache-Control": "no-store"},
        )
    capability = assisted_review_capability()
    if capability.get("ai_configured"):
        denial = _assisted_review_access_denial(headers)
        if denial is not None:
            return JSONResponse(
                {
                    "error": "Assisted review access denied",
                    "ready": False,
                    "reason": denial,
                },
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
    return await _run_assisted_review(request, capability)


async def local_assisted_review(request: Request) -> JSONResponse:
    """Loopback launcher endpoint; its same-origin guard lives in local_runtime."""

    capability = assisted_review_capability()
    if request.method == "GET":
        return JSONResponse(capability, headers={"Cache-Control": "no-store"})
    return await _run_assisted_review(request, capability)


async def _run_assisted_review(
    request: Request,
    capability: dict[str, Any],
) -> JSONResponse:
    if not capability.get("ready"):
        return JSONResponse(
            {"error": "Assisted review is not configured", **capability},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )
    content_type = request.headers.get("content-type", "").split(";", 1)[0]
    if content_type.casefold() != "application/json":
        return JSONResponse(
            {"error": "Content-Type must be application/json"},
            status_code=415,
            headers={"Cache-Control": "no-store"},
        )
    try:
        payload = await request.json()
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JSONResponse(
            {"error": "Invalid JSON body"},
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )
    if not isinstance(payload, dict) or payload.get("assist_enabled") is not True:
        return JSONResponse(
            {"error": "Assisted review must be explicitly enabled"},
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return JSONResponse(
            {"error": "Posting text is required"},
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )
    if len(text) > MAX_ASSISTED_POSTING_CHARS:
        return JSONResponse(
            {"error": "Posting text is too large"},
            status_code=413,
            headers={"Cache-Control": "no-store"},
        )
    ai_provider = payload.get("ai_provider")
    if ai_provider is not None and not isinstance(ai_provider, str):
        return JSONResponse(
            {"error": "AI provider must be a string"},
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )
    try:
        result = await prepare_assisted_review(
            engine,
            text,
            organization_profile=payload.get("organization_profile"),
            ai_provider=ai_provider or None,
        )
    except ValueError as exc:
        return JSONResponse(
            {"error": str(exc)},
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(result.to_dict(), headers={"Cache-Control": "no-store"})


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

        if path == ASSISTED_REVIEW_PATH:
            try:
                assisted_limit = _assisted_review_requests_per_minute()
            except ValueError:
                await self._json(
                    send, 500, {"error": "Invalid assisted review rate limit"}
                )
                return
            if not self._anonymous_rate_limiter.allow(
                scope,
                ASSISTED_REVIEW_PATH,
                limit=assisted_limit,
            ):
                await self._json(
                    send,
                    429,
                    {"error": "Assisted review rate limit exceeded"},
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
# of whether access is public or token-protected. HR review with optional upstream
# law lookup and answer persistence remain available only through the local entrypoint.
_mcp_app = public_mcp.streamable_http_app()
_claude_mcp_app = claude_mcp.streamable_http_app()
_routes = [
    Route(_root_path(), health, methods=["GET"]),
    Route(_health_path(), health, methods=["GET"]),
    Route(ASSISTED_REVIEW_PATH, assisted_review, methods=["GET", "POST"]),
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
