"""tools/verify_vercel_deployment.py --public-only: the checks that need no token.

The authenticated audit cannot run while the production token does not match,
and until then no deployment evidence could be refreshed at all. --public-only
runs only what an anonymous caller can see and writes a separate report. All
HTTP here goes through httpx.MockTransport; nothing touches the network.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

import anyio
import httpx
import pytest

from tools import verify_vercel_deployment as verify_mod

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "reports" / "vercel_deployment_audit.json"

SECURITY_HEADERS = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}
HEALTH = {
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
    },
}


class Site:
    """A fake deployment; the keyword arguments break one thing at a time."""

    def __init__(
        self,
        *,
        health: dict[str, Any] | None = None,
        health_status: int = 200,
        health_headers: dict[str, str] | None = None,
        book_status: int = 307,
        book_location: str | None = "/web/?entry=book",
        mcp_status: int = 401,
        mcp_headers: dict[str, str] | None = None,
        claude_status: int = 401,
        claude_headers: dict[str, str] | None = None,
    ) -> None:
        self.health = {**HEALTH, **(health or {})}
        self.health_status = health_status
        self.health_headers = SECURITY_HEADERS if health_headers is None else health_headers
        self.book_status, self.book_location = book_status, book_location
        challenge = {**SECURITY_HEADERS, "www-authenticate": "Bearer"}
        self.mcp_status = mcp_status
        self.mcp_headers = challenge if mcp_headers is None else mcp_headers
        self.claude_status = claude_status
        self.claude_headers = challenge if claude_headers is None else claude_headers
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.host == "example.test", request.url
        route = (request.method, request.url.path)
        if route == ("GET", "/api/health"):
            return httpx.Response(self.health_status, json=self.health, headers=self.health_headers)
        if route == ("GET", "/book"):
            headers = {"location": self.book_location} if self.book_location else {}
            return httpx.Response(self.book_status, headers=headers)
        if route == ("POST", "/api/mcp"):
            return httpx.Response(self.mcp_status, json={}, headers=self.mcp_headers)
        if route == ("POST", "/api/claude-mcp"):
            return httpx.Response(self.claude_status, json={}, headers=self.claude_headers)
        raise AssertionError(f"the public check must not request {route}")

    @property
    def routes(self) -> list[tuple[str, str]]:
        return [(request.method, request.url.path) for request in self.requests]


def serve(monkeypatch: pytest.MonkeyPatch, site: Site) -> Site:
    real = httpx.AsyncClient

    class Client(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(site.handle)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(verify_mod.httpx, "AsyncClient", Client)
    monkeypatch.setattr(
        verify_mod,
        "load_ruleset",
        lambda: type("Rules", (), {"version": "rules-v1", "matching_version": "match-v1", "rules": [1]})(),
    )
    monkeypatch.setattr(verify_mod, "runtime_source_fingerprint", lambda **_kwargs: "runtime-test")
    monkeypatch.setattr(verify_mod, "runtime_source_manifest", lambda: {"source.py": "sha256:test"})
    return site


def run_public(monkeypatch: pytest.MonkeyPatch, site: Site) -> dict[str, Any]:
    serve(monkeypatch, site)
    return anyio.run(verify_mod.verify_public, "https://example.test", "dep-1")


def failed_checks(report: dict[str, Any]) -> list[str]:
    return [name for name, ok in report["checks"].items() if not ok]


# --- the report ---------------------------------------------------------------


def test_a_healthy_deployment_passes_with_anonymous_requests_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAIRPOST_MCP_TOKEN", "token-in-the-environment")
    site = Site()
    report = run_public(monkeypatch, site)

    assert report["passed"] is True
    assert failed_checks(report) == []
    assert report["schema_version"] == "fairpost-vercel-public-check-v1"
    assert report["schema_version"] != verify_mod.FULL_AUDIT_SCHEMA_VERSION
    assert report["authenticated_checks_performed"] is False
    assert report["token_used"] is False
    assert report["assisted_review_called"] is False
    assert report["deployment_id"] == "dep-1"
    # Exactly the four public requests, in a fixed order, and none with credentials.
    assert site.routes == [
        ("GET", "/api/health"),
        ("GET", "/book"),
        ("POST", "/api/mcp"),
        ("POST", "/api/claude-mcp"),
    ]
    assert all("authorization" not in request.headers for request in site.requests)
    assert "token-in-the-environment" not in json.dumps(report)
    assert report["anonymous_responses"] == {
        "mcp": {"status_code": 401, "www_authenticate": "Bearer"},
        "claude_mcp": {"status_code": 401, "www_authenticate": "Bearer"},
    }
    assert report["book_entry"]["status_code"] == 307
    assert set(report["api_security_headers"]) == {"health", "mcp", "claude_mcp"}
    assert set(report["checks"]) >= {
        "runtime_source_fingerprint_matches_local",
        "runtime_source_manifest_matches_local",
        "anonymous_mcp_gate_matches",
        "anonymous_claude_mcp_gate_matches",
        "book_entry_redirect_is_temporary",
        "security_headers_present_on_api",
    }


def test_the_authenticated_mcp_tools_are_never_called(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        verify_mod, "streamable_http_client", lambda *a, **k: called.append("mcp") or None
    )
    report = run_public(monkeypatch, Site())
    assert called == []
    assert "tools" not in report and "server_name" not in report


BROKEN_DEPLOYMENTS = [
    (
        "runtime fingerprint differs",
        Site(health={"runtime_source_fingerprint": "runtime-other"}),
        "runtime_source_fingerprint_matches_local",
    ),
    (
        "source manifest differs",
        Site(health={"runtime_source_manifest": {"source.py": "sha256:other"}}),
        "runtime_source_manifest_matches_local",
    ),
    ("ruleset differs", Site(health={"ruleset_version": "rules-old"}), "ruleset_version_matches_local"),
    ("matching differs", Site(health={"matching_version": "match-old"}), "matching_version_matches_local"),
    ("health not ok", Site(health={"status": "degraded"}), "health_ok"),
    ("mcp answers anonymous callers", Site(mcp_status=200, mcp_headers=SECURITY_HEADERS), "anonymous_mcp_gate_matches"),
    ("mcp 401 without a Bearer challenge", Site(mcp_headers=SECURITY_HEADERS), "anonymous_mcp_gate_matches"),
    ("claude 401 without a Bearer challenge", Site(claude_headers=SECURITY_HEADERS), "anonymous_claude_mcp_gate_matches"),
    ("book redirect is permanent", Site(book_status=308), "book_entry_redirect_is_temporary"),
    ("book has no redirect target", Site(book_location=None), "book_entry_redirect_is_temporary"),
    (
        "health lacks a security header",
        Site(health_headers={k: v for k, v in SECURITY_HEADERS.items() if k != "x-frame-options"}),
        "security_headers_present_on_api",
    ),
    (
        "a 401 lacks a security header",
        Site(mcp_headers={"www-authenticate": "Bearer", **{k: v for k, v in SECURITY_HEADERS.items() if k != "cache-control"}}),
        "security_headers_present_on_api",
    ),
    ("claude mode drifted", Site(health={"claude_readonly_authentication": "none"}), "claude_authentication_mode_matches"),
]


@pytest.mark.parametrize(
    ("site", "failing"),
    [pytest.param(site, failing, id=label) for label, site, failing in BROKEN_DEPLOYMENTS],
)
def test_each_public_contract_has_its_own_failing_check(
    monkeypatch: pytest.MonkeyPatch, site: Site, failing: str
) -> None:
    report = run_public(monkeypatch, site)
    assert report["passed"] is False
    assert failing in failed_checks(report)


def test_an_unreachable_health_endpoint_raises_instead_of_reporting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(httpx.HTTPStatusError):
        run_public(monkeypatch, Site(health_status=503))


# --- the command line -----------------------------------------------------------


def argv(monkeypatch: pytest.MonkeyPatch, *args: object) -> None:
    monkeypatch.setattr(verify_mod.sys, "argv", ["verify_vercel_deployment.py", *map(str, args)])


def no_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(verify_mod.anyio, "run", lambda *a, **k: calls.append("run"))
    return calls


def test_public_only_requires_an_output_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = no_network(monkeypatch)
    argv(monkeypatch, "--public-only", "--url", "https://example.test")
    with pytest.raises(SystemExit) as stop:
        verify_mod.main()
    assert stop.value.code == 2
    assert "--output" in capsys.readouterr().err
    assert calls == []


def test_public_only_refuses_the_authenticated_audit_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = no_network(monkeypatch)
    monkeypatch.chdir(tmp_path)
    before = AUDIT.read_bytes() if AUDIT.exists() else None
    spellings = [
        AUDIT,  # absolute
        ROOT / "reports" / ".." / "reports" / "vercel_deployment_audit.json",
        Path("reports/vercel_deployment_audit.json"),  # the default, relative to cwd
        Path("reports/VERCEL_DEPLOYMENT_AUDIT.json"),  # letter case
    ]
    for output in spellings:
        argv(monkeypatch, "--public-only", "--output", output)
        with pytest.raises(SystemExit) as stop:
            verify_mod.main()
        assert stop.value.code == 2, output
    assert calls == []
    assert (AUDIT.read_bytes() if AUDIT.exists() else None) == before


def test_public_only_refuses_a_hard_link_to_the_audit_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if not AUDIT.exists():
        pytest.skip("no committed audit file to link to")
    alias = tmp_path / "alias.json"
    try:
        os.link(AUDIT, alias)
    except OSError as error:
        pytest.skip(f"hard links are not available here: {error}")
    no_network(monkeypatch)
    argv(monkeypatch, "--public-only", "--output", alias)
    with pytest.raises(SystemExit) as stop:
        verify_mod.main()
    assert stop.value.code == 2


def test_public_only_cannot_be_combined_with_the_write_check_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = no_network(monkeypatch)
    argv(monkeypatch, "--public-only", "--allow-write-check", "--output", tmp_path / "x.json")
    with pytest.raises(SystemExit) as stop:
        verify_mod.main()
    assert stop.value.code == 2
    assert calls == []


def test_public_only_writes_its_own_report_and_leaves_the_audit_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = serve(monkeypatch, Site())
    output = tmp_path / "out" / "public.json"
    before = AUDIT.read_bytes() if AUDIT.exists() else None
    reads: list[str] = []

    class Environment(dict):
        def get(self, key, default=None):
            reads.append(key)
            return super().get(key, default)

    monkeypatch.setattr(os, "environ", Environment(os.environ))
    argv(
        monkeypatch,
        "--public-only",
        "--url",
        "https://example.test/",
        "--output",
        output,
        "--source-commit",
        "abc123",
    )
    assert verify_mod.main() == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == "fairpost-vercel-public-check-v1"
    assert report["passed"] is True
    assert report["production_url"] == "https://example.test"
    assert report["verification_context"]["source_commit"] == "abc123"
    assert "FAIRPOST_MCP_TOKEN" not in reads  # the token is never even looked up
    assert (AUDIT.read_bytes() if AUDIT.exists() else None) == before
    assert site.routes[0] == ("GET", "/api/health")
    assert "공개 검증 통과" in capsys.readouterr().out


def test_a_failed_public_check_still_writes_the_report_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    serve(monkeypatch, Site(mcp_headers=SECURITY_HEADERS))
    output = tmp_path / "public.json"
    argv(monkeypatch, "--public-only", "--url", "https://example.test", "--output", output)
    with pytest.raises(SystemExit, match="anonymous_mcp_gate_matches"):
        verify_mod.main()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] is False
    assert failed_checks(report) == ["anonymous_mcp_gate_matches"]


def test_a_network_error_writes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    serve(monkeypatch, Site(health_status=503))
    output = tmp_path / "public.json"
    argv(monkeypatch, "--public-only", "--url", "https://example.test", "--output", output)
    with pytest.raises(SystemExit, match="공개 검증 요청에 실패"):
        verify_mod.main()
    assert not output.exists()


def test_the_authenticated_audit_keeps_its_default_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        verify_mod.anyio,
        "run",
        lambda *_a: {"passed": True, "checks": {}, "mcp_endpoint": "https://example.test/api/mcp", "tools": []},
    )
    argv(
        monkeypatch,
        "--url", "https://example.test",
        "--source-commit", "abc123", "--verified-by", "tester", "--approval-ref", "ref",
    )
    assert verify_mod.main() == 0
    written = tmp_path / "reports" / "vercel_deployment_audit.json"
    assert json.loads(written.read_text(encoding="utf-8"))["passed"] is True
