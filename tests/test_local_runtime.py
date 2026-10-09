from pathlib import Path
import json

import anyio
import httpx
import pytest

from mcp_server.local_runtime import create_local_app, load_local_settings


@pytest.fixture
def isolated_local_mcp(monkeypatch):
    """Each local-app test gets an SDK manager with its own one-shot lifespan."""
    from mcp.server.fastmcp import FastMCP
    from mcp_server import server

    instance = FastMCP(
        "fairpost-local-runtime-test", host="127.0.0.1",
        stateless_http=True, json_response=True,
    )
    monkeypatch.setattr(server, "mcp", instance)
    return instance


def test_settings_are_opt_in_and_existing_environment_wins(tmp_path, monkeypatch):
    monkeypatch.delenv("FAIRPOST_AI_API_URL", raising=False)
    monkeypatch.setenv("FAIRPOST_AI_MODEL", "existing")
    path = tmp_path / ".env.fairpost.local.json"
    assert load_local_settings(path) is False
    path.write_text(json.dumps({
        "FAIRPOST_AI_MODEL": "local-model",
        "FAIRPOST_AI_API_URL": "http://127.0.0.1:11434/v1/chat/completions",
    }), encoding="utf-8")
    assert load_local_settings(path) is True
    import os
    assert os.environ["FAIRPOST_AI_MODEL"] == "existing"
    assert os.environ["FAIRPOST_AI_API_URL"].startswith("http://127.0.0.1:")


@pytest.mark.parametrize("payload", [[], {"PATH": "secret"}, {"FAIRPOST_AI_MODEL": 8}, {"FAIRPOST_AI_MODEL": "bad\0value"}])
def test_local_settings_reject_unsupported_values_without_echo(tmp_path, payload):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported") as exc:
        load_local_settings(path)
    assert "secret" not in str(exc.value)


def test_local_app_serves_only_web_and_requires_same_origin(monkeypatch):
    from mcp_server import remote
    monkeypatch.setenv("FAIRPOST_ASSISTED_REVIEW_REQUESTS_PER_MINUTE", "100")
    monkeypatch.setattr(remote, "assisted_review_capability", lambda: {"ready": True})

    async def exercise():
        app = create_local_app()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
                root = await client.get("/")
                assert root.headers["location"] == "/web/"
                web = await client.get("/web/")
                assert web.status_code == 200
                assert 'id="assisted-review-toggle"' in web.text
                assert web.headers["cache-control"] == "no-store"
                capability = await client.get("/api/assisted-review")
                assert capability.json()["ready"] is True
                disabled = await client.post("/api/assisted-review", json={"text": "여성만 지원 가능"})
                assert disabled.status_code == 400
                for path in ("/.env.fairpost.local.json", "/.git/config", "/web/..%2f.env.fairpost.local.json", "/README.md"):
                    assert (await client.get(path)).status_code == 404
                assert (await client.get("/web/", headers={"host": "evil.example"})).status_code == 400
                for headers in ({"origin": "https://evil.example"}, {"origin": "http://127.0.0.1:9000"}, {"origin": "null"}, {"sec-fetch-site": "cross-site"}):
                    assert (await client.post("/api/assisted-review", headers=headers, json={})).status_code == 403
                assert (await client.get("/api/assisted-review", headers={"origin": "http://127.0.0.1:8000"})).status_code == 200
                mcp_response = await client.post("/mcp", headers={"Accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
                assert mcp_response.status_code == 200
                assert "prepare_hr_review" in mcp_response.text
                assert "save_answer" in mcp_response.text
    anyio.run(exercise)


def test_local_settings_template_has_no_credentials():
    from mcp_server.local_runtime import CONFIG_KEYS
    path = Path(__file__).resolve().parents[1] / "examples/local-connections.example.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload.keys() <= CONFIG_KEYS
    assert not any("KEY" in key or "TOKEN" in key for key in payload)


def test_asset_resolution_uses_checkout_and_installed_package(tmp_path, monkeypatch):
    from mcp_server import local_runtime
    required = ("index.html", "app.js", "engine.js", "data.js", "styles.css")
    checkout = tmp_path / "checkout"
    module = checkout / "mcp_server" / "local_runtime.py"
    monkeypatch.setattr(local_runtime, "__file__", str(module))
    prefix = tmp_path / "installed"
    monkeypatch.setattr(local_runtime.sys, "prefix", str(prefix))
    installed_web = prefix / "share" / "fairpost" / "web"
    installed_web.mkdir(parents=True)
    for name in required:
        (installed_web / name).write_text("installed", encoding="utf-8")
    # An incomplete checkout must not shadow a complete installed bundle.
    checkout_web = checkout / "web"
    checkout_web.mkdir(parents=True)
    (checkout_web / "index.html").write_text("incomplete", encoding="utf-8")
    assert local_runtime.resolve_web_dir() == installed_web
    for name in required:
        (checkout_web / name).write_text("checkout", encoding="utf-8")
    assert local_runtime.resolve_web_dir() == checkout_web
    assert local_runtime.resolve_web_dir(installed_web) == installed_web


def test_missing_assets_give_reinstall_instruction(tmp_path):
    from mcp_server.local_runtime import resolve_web_dir
    with pytest.raises(ValueError, match="python -m pip install"):
        resolve_web_dir(tmp_path)


@pytest.mark.parametrize("bad_settings", ['not JSON private secret', '{"PATH": "private secret"}'])
def test_web_starts_with_malformed_optional_settings(
    tmp_path, monkeypatch, capsys, bad_settings, isolated_local_mcp
):
    from mcp_server import local_runtime, remote
    path = tmp_path / "settings.json"
    path.write_text(bad_settings, encoding="utf-8")
    captured = []
    monkeypatch.setattr(local_runtime, "_serve_web", lambda app, port, **kwargs: captured.append((app, port, kwargs)) or 0)

    async def unexpected_assistance(_request):
        raise AssertionError("invalid local settings must disable assistance")

    monkeypatch.setattr(remote, "local_assisted_review", unexpected_assistance)
    assert local_runtime.main(["web", "--config", str(path), "--port", "8123"]) == 0
    assert captured[0][1:] == (8123, {"open_browser": False})
    assert "private secret" not in capsys.readouterr().err

    async def exercise():
        app = captured[0][0]
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8123") as client:
                assert (await client.get("/web/")).status_code == 200
                capability = await client.get("/api/assisted-review")
                assert capability.status_code == 200
                assert capability.json()["ready"] is False
                response = await client.post("/api/assisted-review", json={"assist_enabled": True, "text": "공고"})
                assert response.status_code == 503
                assert (await client.post("/api/assisted-review", headers={"origin": "https://evil.example"}, json={})).status_code == 403
    anyio.run(exercise)


def test_web_invalid_ai_environment_never_calls_external_review(
    tmp_path, monkeypatch, isolated_local_mcp
):
    from mcp_server import local_runtime, remote
    monkeypatch.setenv("FAIRPOST_AI_PROVIDER", "invalid-provider")
    captured = []
    monkeypatch.setattr(local_runtime, "_serve_web", lambda app, _port, **_kwargs: captured.append(app) or 0)

    async def unexpected_review(*_args, **_kwargs):
        raise AssertionError("basic review must not call external AI")

    monkeypatch.setattr(remote, "prepare_assisted_review", unexpected_review)
    assert local_runtime.main(["web", "--config", str(tmp_path / "missing.json")]) == 0

    async def exercise():
        app = captured[0]
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
                assert (await client.get("/web/")).status_code == 200
                assert (await client.get("/web/engine.js")).status_code == 200
                assert (await client.get("/api/assisted-review")).json()["ready"] is False
                assert (await client.post("/api/assisted-review", json={"assist_enabled": True, "text": "공고"})).status_code == 503
    anyio.run(exercise)


@pytest.mark.parametrize("mode", ["mcp", "stdio"])
def test_mcp_still_rejects_malformed_optional_settings(tmp_path, mode):
    from mcp_server.local_runtime import main
    path = tmp_path / "settings.json"
    path.write_text("bad JSON", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main([mode, "--config", str(path)])
    assert exc.value.code == 2


@pytest.mark.parametrize("mode", ["mcp", "stdio"])
def test_mcp_valid_settings_keep_existing_entrypoints(tmp_path, monkeypatch, mode):
    from mcp_server import local_runtime, assisted_review, review, server
    monkeypatch.setattr(assisted_review.AiApiConfig, "from_environment", lambda: None)
    monkeypatch.setattr(review.LawMcpConfig, "from_environment", lambda: None)
    calls = []
    monkeypatch.setattr(server, "main", lambda: calls.append("mcp"))
    monkeypatch.setattr(server, "main_stdio", lambda: calls.append("stdio"))
    assert local_runtime.main([mode, "--config", str(tmp_path / "missing.json")]) == 0
    assert calls == [mode]


@pytest.mark.parametrize("args", [["web", "--port", "0"], ["web", "--port", "65536"], ["mcp", "--port", "8123"], ["stdio", "--open-browser"]])
def test_launcher_rejects_invalid_options(args):
    from mcp_server.local_runtime import main
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2


@pytest.mark.parametrize("open_browser", [False, True])
@pytest.mark.parametrize("startup_succeeds", [False, True])
def test_browser_opens_only_when_requested_and_server_ready(monkeypatch, open_browser, startup_succeeds):
    import uvicorn
    from mcp_server import local_runtime
    observed = []
    opened = []

    class FakeServer:
        def __init__(self, config):
            assert config.host == "127.0.0.1"
            self.started = False

        async def startup(self, sockets=None):
            observed.append(sockets[0].getsockname()[0])
            self.started = startup_succeeds

        async def serve(self, sockets=None):
            await self.startup(sockets=sockets)

    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr(local_runtime, "_open_browser", opened.append)
    assert local_runtime._serve_web(object(), 0, open_browser=open_browser) == (0 if startup_succeeds else 2)
    assert observed == ["127.0.0.1"]
    assert opened == (["http://127.0.0.1:0/web/"] if open_browser and startup_succeeds else [])


def test_port_collision_is_reported_before_browser_opens(monkeypatch, capsys):
    import socket
    from mcp_server import local_runtime
    opened = []
    monkeypatch.setattr(local_runtime, "_open_browser", opened.append)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        assert local_runtime._serve_web(object(), port, open_browser=True) == 2
    assert opened == []
    assert "fairpost web --port 8001" in capsys.readouterr().err


@pytest.mark.parametrize("failure", [False, OSError("private browser details")])
def test_browser_failure_leaves_manual_url_available(monkeypatch, capsys, failure):
    import webbrowser
    from mcp_server.local_runtime import _open_browser

    def open_browser(_url):
        if isinstance(failure, OSError):
            raise failure
        return failure

    monkeypatch.setattr(webbrowser, "open", open_browser)
    _open_browser("http://127.0.0.1:8000/web/")
    message = capsys.readouterr().err
    assert "브라우저" in message
    assert "private browser details" not in message


def test_runtime_missing_dependencies_reports_install_instruction(tmp_path, monkeypatch, capsys):
    from mcp_server import local_runtime

    def missing(**_kwargs):
        raise ModuleNotFoundError("private dependency path")

    monkeypatch.setattr(local_runtime, "create_local_app", missing)
    assert local_runtime.main(["web", "--config", str(tmp_path / "missing.json")]) == 2
    message = capsys.readouterr().err
    assert "python -m pip install ." in message
    assert "private dependency path" not in message
