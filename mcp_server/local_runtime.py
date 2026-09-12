"""Opt-in local launcher: private settings, web UI, assisted API and full MCP."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from starlette.applications import Starlette
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles


LOCAL_CONFIG_NAME = ".env.fairpost.local.json"
CONFIG_KEYS = frozenset({
    "FAIRPOST_AI_PROVIDER", "FAIRPOST_AI_API_URL", "FAIRPOST_AI_API_KEY", "FAIRPOST_AI_MODEL",
    "FAIRPOST_AI_TIMEOUT_SECONDS", "FAIRPOST_AI_REASONING_EFFORT",
    "FAIRPOST_ANTHROPIC_API_URL", "FAIRPOST_ANTHROPIC_API_KEY", "FAIRPOST_ANTHROPIC_MODEL",
    "FAIRPOST_OPENAI_API_URL", "FAIRPOST_OPENAI_API_KEY", "FAIRPOST_OPENAI_MODEL",
    "FAIRPOST_GEMINI_API_URL", "FAIRPOST_GEMINI_API_KEY", "FAIRPOST_GEMINI_MODEL",
    "FAIRPOST_KOREAN_LAW_MCP_URL", "FAIRPOST_KOREAN_LAW_MCP_TOKEN",
    "FAIRPOST_KOREAN_LAW_MCP_COMMAND", "FAIRPOST_KOREAN_LAW_MCP_ARGS",
    "FAIRPOST_KOREAN_LAW_MCP_CWD", "FAIRPOST_KOREAN_LAW_MCP_ENV_ALLOWLIST",
    "FAIRPOST_KOREAN_LAW_SEARCH_TOOL", "FAIRPOST_KOREAN_LAW_TEXT_TOOL",
})


def load_local_settings(path: Path) -> bool:
    """Load only when explicitly launched locally; existing environment wins."""
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Local settings must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or any(
        key not in CONFIG_KEYS or not isinstance(value, str) or "\0" in value
        for key, value in payload.items()
    ):
        raise ValueError("Local settings contain unsupported keys or values")
    for key, value in payload.items():
        os.environ.setdefault(key, value)
    return True


class SameOriginMiddleware:
    """Prevent other websites from driving an unauthenticated loopback server."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            origin = headers.get(b"origin")
            expected = (
                scope.get("scheme", "http").encode("ascii")
                + b"://" + headers.get(b"host", b"")
            )
            if (origin is not None and origin != expected) or headers.get(
                b"sec-fetch-site"
            ) in {b"cross-site", b"same-site"}:
                await JSONResponse(
                    {"error": "Local access requires the same origin"},
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_local_app(web_dir: Path | None = None) -> Starlette:
    # Import after settings are loaded; the remote/cloud entrypoint never loads them.
    from .remote import ASSISTED_REVIEW_PATH, RemoteSecurityMiddleware, assisted_review
    from .server import MCP_PATH, mcp

    if web_dir is None:
        candidates = (
            Path(__file__).resolve().parents[1] / "web",
            Path(sys.prefix) / "share" / "fairpost" / "web",
        )
        web_dir = next((path for path in candidates if (path / "index.html").is_file()), candidates[0])
    if not (web_dir / "index.html").is_file():
        raise ValueError("FairPost web assets are not installed")
    if MCP_PATH != "/mcp":
        raise ValueError("Local web launcher requires the default /mcp path")

    async def home(_request):
        return RedirectResponse("/web/")

    async def favicon(_request):
        return FileResponse(web_dir.parent / "favicon.svg", media_type="image/svg+xml")

    mcp_app = mcp.streamable_http_app()
    web_app = Starlette(routes=[
        Route("/", home),
        *([Route("/favicon.svg", favicon)] if (web_dir.parent / "favicon.svg").is_file() else []),
        Route(ASSISTED_REVIEW_PATH, assisted_review, methods=["GET", "POST"]),
        Mount("/web", app=StaticFiles(directory=web_dir, html=True)),
    ])
    # The authenticated remote MCP middleware is only used on web/API routes.
    # Local full MCP keeps its own SDK host/origin protection and local storage.
    app = Starlette(
        routes=[*mcp_app.routes, Mount("/", app=RemoteSecurityMiddleware(web_app))],
        lifespan=mcp_app.router.lifespan_context,
    )
    app.add_middleware(SameOriginMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("web", "mcp", "stdio"), default="web")
    parser.add_argument("--config", type=Path, default=Path.cwd() / LOCAL_CONFIG_NAME)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    port = args.port if args.port is not None else 8000
    if not 1 <= port <= 65535:
        parser.error("port must be between 1 and 65535")
    if args.mode != "web" and args.port is not None:
        parser.error("--port is only available in web mode; MCP uses FAIRPOST_MCP_PORT")
    try:
        load_local_settings(args.config)
        from .assisted_review import AiApiConfig
        from .review import LawMcpConfig
        AiApiConfig.from_environment()
        LawMcpConfig.from_environment()
    except ValueError as exc:
        parser.error(str(exc))
    if args.mode == "web":
        import uvicorn
        print(f"FairPost web: http://127.0.0.1:{port}/web/", flush=True)
        print(f"FairPost MCP: http://127.0.0.1:{port}/mcp", flush=True)
        uvicorn.run(create_local_app(), host="127.0.0.1", port=port)
    else:
        from .server import main as mcp_main, main_stdio
        (main_stdio if args.mode == "stdio" else mcp_main)()


if __name__ == "__main__":
    main()
