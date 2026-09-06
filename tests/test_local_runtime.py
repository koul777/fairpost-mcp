from pathlib import Path
import json

import anyio
import httpx
import pytest

from mcp_server.local_runtime import create_local_app, load_local_settings


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
