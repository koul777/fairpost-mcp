from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
import json
import os
from typing import Any

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .server import (
    _claude_mcp_path_from_environment,
    _mcp_path_from_environment,
    claude_mcp,
    engine,
    mcp,
)
from .storage import (
    LocalAnswerStore,
    UnavailableRemoteAnswerStore,
    UpstashAnswerStore,
)


def _health_path() -> str:
    return "/api/health" if os.environ.get("VERCEL") else "/health"


def _root_path() -> str:
    return "/api" if os.environ.get("VERCEL") else "/"


def _answer_store_status() -> str:
    from .server import answer_store

    if isinstance(answer_store, UpstashAnswerStore):
        return "upstash"
    if isinstance(answer_store, LocalAnswerStore):
        return "local"
    if isinstance(answer_store, UnavailableRemoteAnswerStore):
        return "unavailable"
    return "unknown"


async def health(_request: Any) -> JSONResponse:
    token_required = bool(os.environ.get("FAIRPOST_MCP_TOKEN"))
    return JSONResponse(
        {
            "name": "fairpost",
            "status": "ok",
            "transport": "streamable-http",
            "stateless": True,
            "mcp_endpoint": _mcp_path_from_environment(),
            "claude_readonly_mcp_endpoint": _claude_mcp_path_from_environment(),
            "authentication": "bearer" if token_required else "none",
            "claude_readonly_authentication": "none",
            "answer_store": _answer_store_status(),
            "ruleset_version": engine.ruleset.version,
            "matching_version": engine.ruleset.matching_version,
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

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        token = os.environ.get("FAIRPOST_MCP_TOKEN", "")
        public_remote = os.environ.get(
            "FAIRPOST_ALLOW_PUBLIC_REMOTE", ""
        ).casefold() in {"1", "true", "yes"}
        if (
            path == _mcp_path_from_environment()
            and os.environ.get("VERCEL")
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
        if token and path == _mcp_path_from_environment():
            headers = {
                key.decode("latin-1").casefold(): value.decode("latin-1")
                for key, value in scope.get("headers", [])
            }
            supplied = headers.get("authorization", "")
            expected = f"Bearer {token}"
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

        max_bytes = int(os.environ.get("FAIRPOST_MAX_REQUEST_BYTES", "1048576"))
        content_length = next(
            (
                value
                for key, value in scope.get("headers", [])
                if key.lower() == b"content-length"
            ),
            None,
        )
        if content_length is not None:
            try:
                if int(content_length) > max_bytes:
                    await self._json(send, 413, {"error": "Request body too large"})
                    return
            except ValueError:
                await self._json(send, 400, {"error": "Invalid Content-Length"})
                return

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
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

        await self.app(scope, receive, secure_send)

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


_mcp_app = mcp.streamable_http_app()
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
