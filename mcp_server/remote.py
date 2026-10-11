from __future__ import annotations

from collections import OrderedDict
from contextlib import asynccontextmanager
import functools
import hmac
import ipaddress
import json
import logging
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
PUBLIC_RATE_LIMIT_REASON = "요청이 너무 많습니다. 1분 뒤 다시 시도하세요."
ASSISTED_RATE_LIMIT_REASON = (
    "AI·현행 법령 보강 요청이 너무 많습니다. 1분 뒤 다시 시도하세요. "
    "로컬 검토 결과는 그대로 사용할 수 있습니다."
)
UNAUTHORIZED_REASON = (
    "인증되지 않은 요청입니다. Authorization: Bearer 토큰을 확인하세요."
)
REMOTE_MCP_NOT_CONFIGURED_REASON = (
    "원격 MCP 인증이 설정되지 않아 요청을 받지 않습니다."
)
PUBLIC_CLAUDE_MCP_DISABLED_REASON = "공개 Claude 호환 MCP가 활성화되지 않았습니다."
AMBIGUOUS_LENGTH_REASON = "요청 길이 헤더가 모호해 처리할 수 없습니다."
INVALID_CONTENT_LENGTH_REASON = "Content-Length 헤더가 올바르지 않습니다."
SERVER_CONFIGURATION_REASON = "서버 설정 오류로 요청을 처리할 수 없습니다."

# JSON-RPC server error codes for middleware rejections on MCP paths. The
# HTTP status carries the same meaning for clients that ignore the body.
JSONRPC_UNAUTHORIZED = -32001
JSONRPC_NOT_CONFIGURED = -32002
JSONRPC_RATE_LIMITED = -32003
JSONRPC_REQUEST_TOO_LARGE = -32004
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_INTERNAL_ERROR = -32603

# Vercel overwrites X-Forwarded-For with the connecting client and does not
# forward client-supplied values ("to prevent IP spoofing"); it sends the same
# address as x-vercel-forwarded-for and x-real-ip
# (https://vercel.com/docs/headers/request-headers, checked 2026-10-11).
# Those headers are trusted only when the VERCEL runtime variable is set.
# Anywhere else the ASGI peer address is used, because an unproxied client
# could put any value in them.
VERCEL_CLIENT_IP_HEADERS = (
    b"x-vercel-forwarded-for",
    b"x-real-ip",
    b"x-forwarded-for",
)


def _body_too_large_reason(max_bytes: int) -> str:
    return f"요청 본문이 너무 큽니다. 최대 {max_bytes:,}바이트까지 보낼 수 있습니다."


def _configure_vercel_logging() -> None:
    """Send FairPost's privacy-safe event lines to the Vercel runtime log."""

    fairpost_logger = logging.getLogger("fairpost")
    if os.environ.get("VERCEL") and not fairpost_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        fairpost_logger.addHandler(handler)
        fairpost_logger.setLevel(logging.INFO)


_configure_vercel_logging()


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


def _public_requests_per_minute_or_default() -> int:
    try:
        return _public_requests_per_minute()
    except ValueError:
        return DEFAULT_PUBLIC_REQUESTS_PER_MINUTE


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


def _client_identity(scope: Scope) -> str:
    """Client address used only as an ephemeral rate-limit key, never stored."""

    if os.environ.get("VERCEL"):
        headers = scope.get("headers", [])
        for name in VERCEL_CLIENT_IP_HEADERS:
            value = _single_header(headers, name)
            if not value:
                continue
            try:
                candidate = value.decode("ascii").split(",", 1)[0].strip()
                return str(ipaddress.ip_address(candidate))
            except (UnicodeDecodeError, ValueError):
                continue
    client = scope.get("client")
    return (
        str(client[0])
        if isinstance(client, (list, tuple)) and client
        else "unknown"
    )


class _AnonymousRateLimiter:
    """Bound anonymous requests without retaining client addresses or content."""

    def __init__(self) -> None:
        self._entries: OrderedDict[bytes, int] = OrderedDict()
        self._lock = Lock()
        self._key = os.urandom(32)
        self._window: int | None = None

    def allow(self, scope: Scope, path: str, *, limit: int) -> bool:
        """Count one request against the (client, bucket) fixed window.

        ``path`` names the bucket. Callers use separate buckets for
        authorized and unauthorized attempts so junk traffic can never drain
        an authorized caller's budget.
        """

        key = hmac.digest(
            self._key,
            f"{_client_identity(scope)}\0{path}".encode("utf-8", errors="replace"),
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


@functools.lru_cache(maxsize=1)
def _runtime_identity() -> tuple[str, dict[str, str]]:
    """Hash the deployed sources once per process.

    A Vercel deployment is immutable, so recomputing the fingerprint and
    manifest on every health request only spent about 200 ms of CPU.
    """

    fingerprint = runtime_source_fingerprint(
        ruleset_version=engine.ruleset.version,
        matching_version=engine.ruleset.matching_version,
    )
    return fingerprint, runtime_source_manifest()


def _tool_names(server: Any) -> list[str]:
    return sorted(tool.name for tool in server._tool_manager.list_tools())


@functools.lru_cache(maxsize=1)
def _remote_tools() -> dict[str, list[str]]:
    """Tool names each network MCP endpoint exposes (names only, no secrets)."""

    return {
        MCP_PATH: _tool_names(public_mcp),
        CLAUDE_MCP_PATH: _tool_names(claude_mcp),
    }


async def health(_request: Any) -> JSONResponse:
    token_required = bool(os.environ.get("FAIRPOST_MCP_TOKEN"))
    public = _public_remote_mode()
    claude_public = _public_claude_remote_mode()
    source_fingerprint, source_manifest = _runtime_identity()
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
            "remote_tools": {
                endpoint: list(names) for endpoint, names in _remote_tools().items()
            },
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
            "runtime_source_manifest": dict(source_manifest),
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
        return _assisted_error(
            415,
            "Content-Type must be application/json",
            "요청 형식은 application/json이어야 합니다.",
        )
    try:
        payload = await request.json()
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _assisted_error(
            400, "Invalid JSON body", "요청 본문이 올바른 JSON이 아닙니다."
        )
    if not isinstance(payload, dict) or payload.get("assist_enabled") is not True:
        return _assisted_error(
            400,
            "Assisted review must be explicitly enabled",
            "보강 실행은 사용자가 명시적으로 켠 경우에만 처리합니다.",
        )
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return _assisted_error(
            400, "Posting text is required", "검토할 공고문을 입력하세요."
        )
    if len(text) > MAX_ASSISTED_POSTING_CHARS:
        return _assisted_error(
            413,
            "Posting text is too large",
            f"공고문은 최대 {MAX_ASSISTED_POSTING_CHARS:,}자까지 보강 검토할 수 있습니다.",
        )
    ai_provider = payload.get("ai_provider")
    if ai_provider is not None and not isinstance(ai_provider, str):
        return _assisted_error(
            400, "AI provider must be a string", "AI 제공자 값이 올바르지 않습니다."
        )
    try:
        result = await prepare_assisted_review(
            engine,
            text,
            organization_profile=payload.get("organization_profile"),
            ai_provider=ai_provider or None,
        )
    except ValueError as exc:
        # Validation messages are fixed Korean strings without request content.
        return _assisted_error(400, str(exc), str(exc))
    return JSONResponse(result.to_dict(), headers={"Cache-Control": "no-store"})


def _assisted_error(status: int, error: str, reason: str) -> JSONResponse:
    """``error`` stays a stable code; the web screen shows the Korean ``reason``."""

    return JSONResponse(
        {"error": error, "reason": reason},
        status_code=status,
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
        any_mcp_request = mcp_request or claude_mcp_request
        token = os.environ.get("FAIRPOST_MCP_TOKEN", "")
        public_remote = _public_remote_mode()
        public_claude_remote = _public_claude_remote_mode()

        async def reject(
            status: int,
            *,
            code: int,
            error: str,
            reason: str,
            extra_headers: list[tuple[bytes, bytes]] | None = None,
        ) -> None:
            # MCP clients parse JSON-RPC, so MCP paths get an error envelope;
            # other paths keep {"error", "reason"}. HTTP status is unchanged.
            payload: dict[str, Any] = (
                {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": code,
                        "message": reason,
                        "data": {"error": error},
                    },
                    "id": None,
                }
                if any_mcp_request
                else {"error": error, "reason": reason}
            )
            await self._json(send, status, payload, extra_headers=extra_headers)

        if (
            mcp_request
            and not token
            and not public_remote
        ):
            await reject(
                503,
                code=JSONRPC_NOT_CONFIGURED,
                error="Remote MCP authentication is not configured",
                reason=REMOTE_MCP_NOT_CONFIGURED_REASON,
            )
            return
        if (
            claude_mcp_request
            and not token
            and not public_claude_remote
        ):
            await reject(
                503,
                code=JSONRPC_NOT_CONFIGURED,
                error="Public Claude MCP is not enabled",
                reason=PUBLIC_CLAUDE_MCP_DISABLED_REASON,
            )
            return
        if token and any_mcp_request:
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
                # Failed attempts are charged to their own pre-auth bucket;
                # authorized callers are never rate limited here.
                endpoint = CLAUDE_MCP_PATH if claude_mcp_request else MCP_PATH
                if not self._anonymous_rate_limiter.allow(
                    scope,
                    f"{endpoint}#unauthorized",
                    limit=_public_requests_per_minute_or_default(),
                ):
                    await reject(
                        429,
                        code=JSONRPC_RATE_LIMITED,
                        error="Public request rate limit exceeded",
                        reason=PUBLIC_RATE_LIMIT_REASON,
                        extra_headers=[(b"retry-after", b"60")],
                    )
                    return
                await reject(
                    401,
                    code=JSONRPC_UNAUTHORIZED,
                    error="Unauthorized",
                    reason=UNAUTHORIZED_REASON,
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
                await reject(
                    500,
                    code=JSONRPC_INTERNAL_ERROR,
                    error="Invalid public request rate limit",
                    reason=SERVER_CONFIGURATION_REASON,
                )
                return
            if not self._anonymous_rate_limiter.allow(
                scope,
                CLAUDE_MCP_PATH if claude_mcp_request else MCP_PATH,
                limit=requests_per_minute,
            ):
                await reject(
                    429,
                    code=JSONRPC_RATE_LIMITED,
                    error="Public request rate limit exceeded",
                    reason=PUBLIC_RATE_LIMIT_REASON,
                    extra_headers=[(b"retry-after", b"60")],
                )
                return

        if path == ASSISTED_REVIEW_PATH:
            try:
                assisted_limit = _assisted_review_requests_per_minute()
            except ValueError:
                await reject(
                    500,
                    code=JSONRPC_INTERNAL_ERROR,
                    error="Invalid assisted review rate limit",
                    reason=SERVER_CONFIGURATION_REASON,
                )
                return
            bucket, limit = self._assisted_review_bucket(scope, assisted_limit)
            if not self._anonymous_rate_limiter.allow(scope, bucket, limit=limit):
                await reject(
                    429,
                    code=JSONRPC_RATE_LIMITED,
                    error="Assisted review rate limit exceeded",
                    reason=ASSISTED_RATE_LIMIT_REASON,
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
            await reject(
                500,
                code=JSONRPC_INTERNAL_ERROR,
                error="Invalid request size limit",
                reason=SERVER_CONFIGURATION_REASON,
            )
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
            await reject(
                400,
                code=JSONRPC_INVALID_REQUEST,
                error="Ambiguous request length",
                reason=AMBIGUOUS_LENGTH_REASON,
            )
            return
        if content_lengths:
            try:
                declared_length = content_lengths[0].decode("ascii")
                if not declared_length.isdecimal():
                    raise ValueError
                too_large = int(declared_length) > max_bytes
            except (UnicodeDecodeError, ValueError):
                await reject(
                    400,
                    code=JSONRPC_INVALID_REQUEST,
                    error="Invalid Content-Length",
                    reason=INVALID_CONTENT_LENGTH_REASON,
                )
                return
            if too_large:
                await reject(
                    413,
                    code=JSONRPC_REQUEST_TOO_LARGE,
                    error="Request body too large",
                    reason=_body_too_large_reason(max_bytes),
                )
                return

        bounded_receive = await self._buffer_request(receive, max_bytes=max_bytes)
        if bounded_receive is None:
            await reject(
                413,
                code=JSONRPC_REQUEST_TOO_LARGE,
                error="Request body too large",
                reason=_body_too_large_reason(max_bytes),
            )
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
    def _assisted_review_bucket(scope: Scope, assisted_limit: int) -> tuple[str, int]:
        """Separate budgets so unauthorized traffic cannot starve real callers.

        Only POST spends AI provider keys. A POST with the internal bearer
        token is charged to its own bucket; every other POST (anonymous or
        browser-origin, whose headers are forgeable) goes to the per-client
        pre-auth bucket. The capability GET is cheap and uses the general
        public limit so page loads never consume the POST budget.
        """

        if str(scope.get("method", "GET")).upper() != "POST":
            return f"{ASSISTED_REVIEW_PATH}#capability", max(
                assisted_limit, _public_requests_per_minute_or_default()
            )
        if _assisted_review_bearer_matches(scope.get("headers", [])):
            return f"{ASSISTED_REVIEW_PATH}#token", assisted_limit
        return ASSISTED_REVIEW_PATH, assisted_limit

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
