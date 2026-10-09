"""FairPost local screen, with optional assisted review and compatible MCP."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import socket
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.applications import Starlette


LOCAL_CONFIG_NAME = ".env.fairpost.local.json"
BOOK_ENTRY_TARGET = "/web/?entry=book"
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
        from starlette.responses import JSONResponse

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


def resolve_web_dir(web_dir: Path | None = None) -> Path:
    """Find checkout assets or the assets shipped in an installed wheel."""
    candidates = (
        (Path(web_dir),) if web_dir is not None else (
            Path(__file__).resolve().parents[1] / "web",
            Path(sys.prefix) / "share" / "fairpost" / "web",
        )
    )
    required = ("index.html", "app.js", "engine.js", "data.js", "posting-templates.js", "styles.css")
    for candidate in candidates:
        if all((candidate / name).is_file() for name in required):
            return candidate
    raise ValueError(
        "FairPost web assets are not installed. "
        "로컬 화면 파일이 없습니다. python -m pip install . 으로 다시 설치하세요."
    )


def create_local_app(
    web_dir: Path | None = None, *, assisted_review_enabled: bool = True
) -> Starlette:
    from starlette.applications import Starlette
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from starlette.responses import FileResponse, JSONResponse, RedirectResponse
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles

    # Import after settings are loaded; the remote/cloud entrypoint never loads them.
    from .remote import ASSISTED_REVIEW_PATH, RemoteSecurityMiddleware, local_assisted_review
    from .server import MCP_PATH, mcp

    web_dir = resolve_web_dir(web_dir)
    if MCP_PATH != "/mcp":
        raise ValueError("Local web launcher requires the default /mcp path")

    async def home(_request):
        return RedirectResponse("/web/")

    async def book(_request):
        # Fixed entry address printed in the book. Keep it a temporary (307)
        # redirect, like the /book entry in vercel.json, so the target can change.
        return RedirectResponse(BOOK_ENTRY_TARGET, status_code=307)

    async def favicon(_request):
        return FileResponse(web_dir.parent / "favicon.svg", media_type="image/svg+xml")

    async def optional_assisted_review(request):
        if not assisted_review_enabled:
            return JSONResponse(
                {
                    "schema_version": "fairpost-assisted-review-capability-v1",
                    "ready": False,
                    "ai_configured": False,
                    "law_mcp_configured": False,
                    "reason": "로컬 선택 연동 설정을 확인하세요. 기본 검토는 사용할 수 있습니다.",
                },
                status_code=200 if request.method == "GET" else 503,
                headers={"Cache-Control": "no-store"},
            )
        return await local_assisted_review(request)

    mcp_app = mcp.streamable_http_app()
    web_app = Starlette(routes=[
        Route("/", home),
        Route("/book", book),
        Route("/book/", book),
        *([Route("/favicon.svg", favicon)] if (web_dir.parent / "favicon.svg").is_file() else []),
        Route(ASSISTED_REVIEW_PATH, optional_assisted_review, methods=["GET", "POST"]),
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


def _open_browser(url: str) -> None:
    """An explicitly requested convenience; failure leaves the server usable."""
    import webbrowser

    try:
        opened = webbrowser.open(url)
    except (OSError, webbrowser.Error):
        opened = False
    if not opened:
        print("브라우저를 열 수 없습니다. 위 주소를 브라우저에 붙여 넣으세요.", file=sys.stderr)


def _serve_web(app: Starlette, port: int, *, open_browser: bool = False) -> int:
    import uvicorn

    url = f"http://127.0.0.1:{port}/web/"

    class ReaderServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                print(f"FairPost 채용공고 검토 프로그램: {url}", flush=True)
                print("기본 검토에는 MCP 클라이언트나 AI 키가 필요하지 않습니다.", flush=True)
                print(f"선택 MCP 연결: http://127.0.0.1:{port}/mcp", flush=True)
                if open_browser:
                    _open_browser(url)

    # Bind once, before opening a browser. Passing the bound socket to Uvicorn
    # also avoids the race between a port probe and the actual server startup.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            print(
                f"fairpost: 포트 {port}를 사용할 수 없습니다. 다른 FairPost 실행을 종료하거나 "
                "fairpost web --port 8001 로 다른 포트를 선택하세요.",
                file=sys.stderr,
            )
            return 2
        server = ReaderServer(uvicorn.Config(app, host="127.0.0.1", port=port))
        try:
            asyncio.run(server.serve(sockets=[listener]))
        except KeyboardInterrupt:
            return 0
        return 0 if server.started else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("web", "mcp", "stdio"), default="web")
    parser.add_argument("--config", type=Path, default=Path.cwd() / LOCAL_CONFIG_NAME,
                        help="선택 AI·법령 연동 설정 JSON 파일")
    parser.add_argument("--port", type=int, default=None, help="로컬 화면 포트 (기본: 8000)")
    parser.add_argument("--open-browser", action="store_true", help="서버 준비 뒤 기본 브라우저 열기")
    args = parser.parse_args(argv)
    port = args.port if args.port is not None else 8000
    if not 1 <= port <= 65535:
        parser.error("port must be between 1 and 65535")
    if args.mode != "web" and (args.port is not None or args.open_browser):
        parser.error("--port and --open-browser are only available in web mode; MCP uses FAIRPOST_MCP_PORT")
    assisted_review_enabled = True
    try:
        load_local_settings(args.config)
    except (OSError, ValueError):
        if args.mode != "web":
            parser.error("로컬 선택 연동 설정을 읽을 수 없습니다. JSON 파일을 확인하세요.")
        assisted_review_enabled = False
        print(
            "fairpost: 로컬 선택 연동 설정을 읽을 수 없어 AI·법령 보강을 비활성화했습니다. "
            "기본 검토는 사용할 수 있습니다.",
            file=sys.stderr,
        )
    try:
        if args.mode == "web":
            # Optional provider configuration is validated by its capability API
            # only. Malformed AI settings must never block the default screen.
            return _serve_web(
                create_local_app(assisted_review_enabled=assisted_review_enabled),
                port, open_browser=args.open_browser,
            )
        from .assisted_review import AiApiConfig
        from .review import LawMcpConfig
        AiApiConfig.from_environment()
        LawMcpConfig.from_environment()
        from .server import main as mcp_main, main_stdio
        (main_stdio if args.mode == "stdio" else mcp_main)()
        return 0
    except ModuleNotFoundError:
        print(
            "fairpost: 로컬 실행에 필요한 패키지가 없습니다. "
            "python -m pip install . 을 먼저 실행하세요.",
            file=sys.stderr,
        )
        return 2
    except (OSError, ValueError) as exc:
        # Startup configuration errors have fixed messages; credentials are not echoed.
        print(f"fairpost: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
