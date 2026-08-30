from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json

import anyio
import pytest

from tools import verify_vercel_deployment as verify_mod


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int,
        payload: dict[str, object],
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"unexpected status {self.status_code}")

    def json(self) -> dict[str, object]:
        return self._payload


def test_anonymous_authentication_behavior_rejects_fail_open_states() -> None:
    assert verify_mod._anonymous_authentication_behavior_matches("bearer", 200) is False
    assert verify_mod._anonymous_authentication_behavior_matches("disabled", 200) is False
    assert verify_mod._anonymous_authentication_behavior_matches("none", 401) is False
    assert verify_mod._anonymous_authentication_behavior_matches("bearer", 401) is True
    assert verify_mod._anonymous_authentication_behavior_matches("disabled", 503) is True


def test_verify_skips_live_write_check_by_default(monkeypatch) -> None:
    calls: list[str] = []

    class FakeHTTPClient:
        instances = 0

        def __init__(self, *args, **kwargs) -> None:
            type(self).instances += 1
            self.instance_id = type(self).instances

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get(self, url: str) -> _FakeResponse:
            assert self.instance_id == 1
            assert url.endswith("/api/health")
            return _FakeResponse(
                status_code=200,
                payload={
                    "status": "ok",
                    "transport": "streamable-http",
                    "stateless": True,
                    "authentication": "bearer",
                    "claude_readonly_authentication": "bearer",
                    "answer_store": "disabled_on_remote_endpoint",
                    "remote_tool_profile": "read_only",
                    "ruleset_version": "rules-v1",
                    "matching_version": "match-v1",
                    "runtime_source_fingerprint": "runtime-test",
                    "runtime_source_manifest": {"source.py": "sha256:test"},
                    "anonymous_access_controls": {
                        "enabled": True,
                        "strategy": "per_instance_client_fixed_window",
                        "requests_per_minute": 60,
                        "stores_raw_client_address": False,
                        "stores_request_content": False,
                        "client_key": "ephemeral_hmac_sha256",
                        "maximum_retention_seconds": 60,
                        "distributed": False,
                    },
                },
                headers={
                    "cache-control": "no-store",
                    "x-content-type-options": "nosniff",
                    "x-frame-options": "DENY",
                    "referrer-policy": "no-referrer",
                },
            )

        async def post(self, url: str, **_kwargs) -> _FakeResponse:
            assert self.instance_id == 1
            if url.endswith("/api/mcp"):
                return _FakeResponse(status_code=401, payload={})
            assert url.endswith("/api/claude-mcp")
            return _FakeResponse(status_code=401, payload={})

    class FakeStreamClient:
        async def __aenter__(self):
            return object(), object(), None

        async def __aexit__(self, *_args) -> None:
            return None

    class FakeSession:
        instance_count = 0

        def __init__(self, _read, _write) -> None:
            type(self).instance_count += 1
            self.instance_id = type(self).instance_count

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def initialize(self):
            name = "fairpost" if self.instance_id == 1 else "fairpost-readonly"
            return SimpleNamespace(serverInfo=SimpleNamespace(name=name))

        async def list_tools(self):
            if self.instance_id == 2:
                return SimpleNamespace(
                    tools=[SimpleNamespace(name="check_job_posting")]
                )
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(name="check_job_posting"),
                    SimpleNamespace(name="check_job_posting_structured"),
                    SimpleNamespace(name="next_review_question"),
                ]
            )

        async def call_tool(self, name: str, _arguments: dict[str, object]):
            calls.append(name)
            if name == "check_job_posting":
                return SimpleNamespace(
                    isError=False,
                    structuredContent=None,
                    content=[
                        SimpleNamespace(
                            text=(
                                f"{verify_mod.DISCLAIMER}\n\n"
                                "채용공고 점검 결과\n"
                                "| 검토 우선도 |\n"
                                "SEX-001\n"
                                "남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률\n"
                                "제7조"
                            )
                        )
                    ],
                )
            if name == "next_review_question":
                return SimpleNamespace(
                    isError=False,
                    structuredContent={"progress": {"total": 1, "answered": 0}},
                    content=[],
                )
            if name == "check_job_posting_structured":
                return SimpleNamespace(
                    isError=False,
                    structuredContent={
                        "schema_version": "fairpost-structured-check-v1",
                        "disclaimer": verify_mod.DISCLAIMER,
                        "findings": [
                            {
                                "id": "SEX-001",
                                "matched_text": "여성만",
                                "offset": [0, 3],
                            }
                        ],
                        "questions": [
                            {
                                "id": "Q-DIST-015",
                                "linked_findings": ["SEX-001"],
                                "book_ref": "question-basis",
                            }
                        ],
                    },
                    content=[],
                )
            raise AssertionError(f"unexpected tool call: {name}")

    monkeypatch.setattr(verify_mod.httpx, "AsyncClient", FakeHTTPClient)
    monkeypatch.setattr(
        verify_mod,
        "streamable_http_client",
        lambda *args, **kwargs: FakeStreamClient(),
    )
    monkeypatch.setattr(verify_mod, "ClientSession", FakeSession)
    monkeypatch.setattr(
        verify_mod,
        "load_ruleset",
        lambda: SimpleNamespace(version="rules-v1", matching_version="match-v1", rules=[1]),
    )
    monkeypatch.setattr(
        verify_mod,
        "runtime_source_fingerprint",
        lambda **_kwargs: "runtime-test",
    )
    monkeypatch.setattr(
        verify_mod,
        "runtime_source_manifest",
        lambda: {"source.py": "sha256:test"},
    )

    report = anyio.run(
        verify_mod.verify,
        "https://example.test",
        "test-token",
        None,
    )

    assert calls == [
        "check_job_posting",
        "check_job_posting_structured",
        "next_review_question",
        "check_job_posting",
    ]
    assert report["passed"] is True
    assert report["write_check_performed"] is False
    assert report["save_answer_is_error"] is None
    assert report["allow_write_check"] is False
    assert report["tools"] == [
        "check_job_posting",
        "check_job_posting_structured",
        "next_review_question",
    ]
    assert report["verified_at"].endswith("+09:00")
    assert report["schema_version"] == "fairpost-vercel-deployment-audit-v3"
    assert report["anonymous_claude_initialize_status"] == 401
    assert report["claude_server_name"] == "fairpost-readonly"
    assert report["claude_tools"] == ["check_job_posting"]
    assert report["checks"]["claude_readonly_profile_verified"] is True


def test_main_forwards_allow_write_check_flag(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    captured: dict[str, object] = {}
    output_path = tmp_path / "audit.json"

    def fake_run(func, url, token, deployment_id, allow_write_check):
        captured["func"] = func
        captured["url"] = url
        captured["token"] = token
        captured["deployment_id"] = deployment_id
        captured["allow_write_check"] = allow_write_check
        return {
            "passed": True,
            "mcp_endpoint": "https://example.test/api/mcp",
            "tools": ["check_job_posting"],
        }

    monkeypatch.setattr(verify_mod.anyio, "run", fake_run)
    monkeypatch.setenv("FAIRPOST_MCP_TOKEN", "secret-token")
    monkeypatch.setattr(
        verify_mod.sys,
        "argv",
        [
            "verify_vercel_deployment.py",
            "--url",
            "https://example.test",
            "--deployment-id",
            "dep-123",
            "--source-commit",
            "abc123",
            "--verified-by",
            "codex-agent",
            "--approval-ref",
            "user-approved-poc",
            "--output",
            str(output_path),
            "--allow-write-check",
        ],
    )

    assert verify_mod.main() == 0
    assert captured == {
        "func": verify_mod.verify,
        "url": "https://example.test",
        "token": "secret-token",
        "deployment_id": "dep-123",
        "allow_write_check": True,
    }
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "passed": True,
        "mcp_endpoint": "https://example.test/api/mcp",
        "tools": ["check_job_posting"],
        "checks": {"verification_context_complete": True},
        "verification_context": {
            "source_commit": "abc123",
            "verified_by": "codex-agent",
            "approval_ref": "user-approved-poc",
        },
    }
    assert "https://example.test/api/mcp (1 tools)" in capsys.readouterr().out


def test_main_rejects_missing_verification_context(
    monkeypatch,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "audit.json"

    def fake_run(*_args):
        return {
            "passed": True,
            "checks": {"tool_call_succeeded": True},
            "mcp_endpoint": "https://example.test/api/mcp",
            "tools": ["check_job_posting"],
        }

    monkeypatch.setattr(verify_mod.anyio, "run", fake_run)
    monkeypatch.setattr(
        verify_mod.sys,
        "argv",
        [
            "verify_vercel_deployment.py",
            "--url",
            "https://example.test",
            "--output",
            str(output_path),
        ],
    )
    monkeypatch.delenv("VERCEL_GIT_COMMIT_SHA", raising=False)
    monkeypatch.delenv("FAIRPOST_VERIFIED_BY", raising=False)
    monkeypatch.delenv("FAIRPOST_APPROVAL_REF", raising=False)

    with pytest.raises(SystemExit, match="source commit"):
        verify_mod.main()
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["passed"] is False
    assert report["checks"]["verification_context_complete"] is False
