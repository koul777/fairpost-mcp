from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlsplit

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.engine import DISCLAIMER  # noqa: E402
from core.loader import load_ruleset  # noqa: E402
from mcp_server.build_identity import (  # noqa: E402
    runtime_source_fingerprint,
    runtime_source_manifest,
)
from tools._paths import atomic_write_text, paths_alias  # noqa: E402


EXPECTED_PUBLIC_TOOLS = {
    "check_job_posting",
    "check_job_posting_structured",
    "next_review_question",
}
KST = timezone(timedelta(hours=9))
SAMPLE_POSTING = "여성만 지원 가능"
EXPECTED_FINDING_ID = "SEX-001"
EXPECTED_STATUTE = "남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률"
EXPECTED_ARTICLE = "제7조"
# The book QR prints this fixed address, so it has to stay a temporary redirect.
BOOK_ENTRY_PATH = "/book"
BOOK_ENTRY_TARGET = "/web/?entry=book"
TEMPORARY_REDIRECT_STATUSES = {302, 307}
FULL_AUDIT_SCHEMA_VERSION = "fairpost-vercel-deployment-audit-v3"
# The token-less check has its own schema so it can never be mistaken for the
# authenticated audit that release evidence is built from.
PUBLIC_CHECK_SCHEMA_VERSION = "fairpost-vercel-public-check-v1"
FULL_AUDIT_OUTPUT = Path("reports/vercel_deployment_audit.json")


def _book_entry_redirect_is_temporary(
    status_code: int,
    location: str | None,
    base_url: str,
) -> bool:
    """True for a 302/307 from /book to the web entry on the same host."""

    if status_code not in TEMPORARY_REDIRECT_STATUSES or not isinstance(location, str):
        return False
    target = urlsplit(location)
    if target.netloc and target.netloc != urlsplit(base_url).netloc:
        return False
    return (
        location.endswith(BOOK_ENTRY_TARGET)
        and target.path == "/web/"
        and target.query == "entry=book"
        and not target.fragment
    )


def _expected_tools(_authentication: str | None) -> set[str]:
    return EXPECTED_PUBLIC_TOOLS


def _anonymous_authentication_behavior_matches(
    authentication: str | None,
    status_code: int,
) -> bool:
    if authentication == "bearer":
        return status_code == 401
    if authentication == "disabled":
        return status_code == 503
    return status_code not in {401, 503}


def _first_text_content(result: Any | None) -> str:
    """Return the first text block without assuming every MCP block is text."""

    if result is None:
        return ""
    for item in result.content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            return text
    return ""


def _initialize_request(request_id: int) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {
                "name": "fairpost-deployment-verifier",
                "version": "1",
            },
        },
    }


_MCP_ACCEPT = {"Accept": "application/json, text/event-stream"}


async def _fetch_anonymous_surface(
    client: httpx.AsyncClient, base_url: str
) -> dict[str, Any]:
    """The requests that need no token: health, /book, and both MCP gates."""

    endpoint = f"{base_url}/api/mcp"
    claude_endpoint = f"{base_url}/api/claude-mcp"
    health_response = await client.get(f"{base_url}/api/health")
    health_response.raise_for_status()
    # Do not follow the redirect: the status code and Location are the contract.
    book_response = await client.get(
        f"{base_url}{BOOK_ENTRY_PATH}", follow_redirects=False
    )
    anonymous = await client.post(
        endpoint, headers=_MCP_ACCEPT, json=_initialize_request(1)
    )
    claude_anonymous = await client.post(
        claude_endpoint, headers=_MCP_ACCEPT, json=_initialize_request(2)
    )
    return {
        "health_response": health_response,
        "health": health_response.json(),
        "book_response": book_response,
        "anonymous": anonymous,
        "claude_anonymous": claude_anonymous,
    }


def _security_headers(response: Any) -> dict[str, str | None]:
    return {
        "cache_control": response.headers.get("cache-control"),
        "content_type_options": response.headers.get("x-content-type-options"),
        "frame_options": response.headers.get("x-frame-options"),
        "referrer_policy": response.headers.get("referrer-policy"),
    }


def _security_headers_ok(headers: dict[str, str | None]) -> bool:
    cache_control_values = {
        value.strip()
        for value in (headers["cache_control"] or "").split(",")
        if value.strip()
    }
    return (
        cache_control_values == {"no-store"}
        and headers["content_type_options"] == "nosniff"
        and headers["frame_options"] == "DENY"
        and headers["referrer_policy"] == "no-referrer"
    )


def _expected_claude_authentication(authentication: str | None) -> str:
    if authentication == "bearer":
        return "bearer"
    allowed = os.environ.get("FAIRPOST_ALLOW_PUBLIC_CLAUDE_REMOTE", "").casefold()
    return "none" if allowed in {"1", "true", "yes"} else "disabled"


def _anonymous_access_controls_present(
    health: dict[str, Any],
    authentication: str | None,
    expected_claude_authentication: str,
) -> bool:
    if authentication != "none" and expected_claude_authentication != "none":
        return True
    controls = health.get("anonymous_access_controls")
    return (
        isinstance(controls, dict)
        and controls.get("enabled") is True
        and controls.get("strategy") == "per_instance_client_fixed_window"
        and isinstance(controls.get("requests_per_minute"), int)
        and controls.get("stores_raw_client_address") is False
        and controls.get("stores_request_content") is False
    )


def _bearer_challenge(response: Any) -> bool:
    """True when a 401 names the Bearer scheme in WWW-Authenticate."""

    value = response.headers.get("www-authenticate")
    return isinstance(value, str) and value.strip().casefold().startswith("bearer")


def _anonymous_gate_matches(
    authentication: str | None, response: Any
) -> bool:
    """The anonymous status fits the mode, and a 401 carries the Bearer challenge."""

    return _anonymous_authentication_behavior_matches(
        authentication, response.status_code
    ) and (authentication != "bearer" or _bearer_challenge(response))


HEALTH_REPORT_KEYS = (
    "status",
    "transport",
    "stateless",
    "authentication",
    "claude_readonly_authentication",
    "answer_store",
    "remote_tool_profile",
    "ruleset_version",
    "matching_version",
    "runtime_source_fingerprint",
    "runtime_source_manifest",
    "anonymous_access_controls",
)


async def verify(
    base_url: str,
    token: str,
    deployment_id: str | None,
    allow_write_check: bool = False,
) -> dict[str, Any]:
    base_url = base_url.rstrip("/")
    endpoint = f"{base_url}/api/mcp"
    claude_endpoint = f"{base_url}/api/claude-mcp"
    health_url = f"{base_url}/api/health"
    book_url = f"{base_url}{BOOK_ENTRY_PATH}"
    timeout = httpx.Timeout(60.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        surface = await _fetch_anonymous_surface(client, base_url)
    health_response = surface["health_response"]
    health = surface["health"]
    book_response = surface["book_response"]
    anonymous = surface["anonymous"]
    claude_anonymous = surface["claude_anonymous"]

    client_headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(
        headers=client_headers,
        timeout=timeout,
    ) as mcp_client:
        async with streamable_http_client(
            endpoint,
            http_client=mcp_client,
        ) as (read, write, _session_id):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                checked = await session.call_tool(
                    "check_job_posting",
                    {"text": SAMPLE_POSTING},
                )
                checked_structured = await session.call_tool(
                    "check_job_posting_structured",
                    {"text": SAMPLE_POSTING},
                )
                next_question = await session.call_tool(
                    "next_review_question",
                    {"text": SAMPLE_POSTING},
                )

    claude_initialized = None
    claude_listed = None
    claude_checked = None
    if health.get("claude_readonly_authentication") != "disabled":
        claude_headers = (
            {"Authorization": f"Bearer {token}"}
            if health.get("claude_readonly_authentication") == "bearer"
            else {}
        )
        async with httpx.AsyncClient(
            headers=claude_headers,
            timeout=timeout,
        ) as claude_client:
            async with streamable_http_client(
                claude_endpoint,
                http_client=claude_client,
            ) as (read, write, _session_id):
                async with ClientSession(read, write) as session:
                    claude_initialized = await session.initialize()
                    claude_listed = await session.list_tools()
                    claude_checked = await session.call_tool(
                        "check_job_posting",
                        {"text": SAMPLE_POSTING},
                    )

    tool_names = {tool.name for tool in listed.tools}
    checked_text = _first_text_content(checked)
    authentication = health.get("authentication")
    expected_claude_authentication = _expected_claude_authentication(authentication)
    local_ruleset = load_ruleset()
    local_runtime_fingerprint = runtime_source_fingerprint(
        ruleset_version=local_ruleset.version,
        matching_version=local_ruleset.matching_version,
    )
    local_runtime_manifest = runtime_source_manifest()
    next_question_content = next_question.structuredContent or {}
    structured_content = checked_structured.structuredContent or {}
    claude_tool_names = (
        {tool.name for tool in claude_listed.tools}
        if claude_listed is not None
        else set()
    )
    claude_checked_text = _first_text_content(claude_checked)

    authentication_behavior_matches = _anonymous_authentication_behavior_matches(
        authentication,
        anonymous.status_code,
    )
    security_headers = _security_headers(health_response)
    book_location = book_response.headers.get("location")
    checks = {
        "health_ok": health_response.status_code == 200
        and health.get("status") == "ok",
        "book_entry_redirect_is_temporary": _book_entry_redirect_is_temporary(
            book_response.status_code,
            book_location,
            base_url,
        ),
        "authentication_behavior_matches": authentication_behavior_matches,
        "claude_authentication_mode_matches": health.get(
            "claude_readonly_authentication"
        )
        == expected_claude_authentication,
        "claude_authentication_behavior_matches": (
            _anonymous_authentication_behavior_matches(
                health.get("claude_readonly_authentication"),
                claude_anonymous.status_code,
            )
        ),
        "claude_readonly_profile_verified": (
            health.get("claude_readonly_authentication") == "disabled"
            or (
                claude_initialized is not None
                and claude_initialized.serverInfo.name == "fairpost-readonly"
                and claude_tool_names == {"check_job_posting"}
                and claude_checked is not None
                and not bool(claude_checked.isError)
                and claude_checked.structuredContent is None
                and claude_checked_text.strip().startswith(DISCLAIMER)
                and EXPECTED_FINDING_ID in claude_checked_text
            )
        ),
        "expected_tools": tool_names == _expected_tools(authentication),
        "tool_call_succeeded": not bool(checked.isError),
        "check_job_posting_returns_plain_text": (
            checked.structuredContent is None
            and checked_text.strip().startswith(DISCLAIMER)
            and "채용공고 점검 결과" in checked_text
            and "| 검토 우선도 |" in checked_text
        ),
        "structured_tool_call_succeeded": not bool(checked_structured.isError),
        "structured_contract_is_traceable": (
            structured_content.get("schema_version")
            == "fairpost-structured-check-v1"
            and structured_content.get("disclaimer") == DISCLAIMER
            and any(
                finding.get("id") == EXPECTED_FINDING_ID
                and finding.get("offset") == [0, 3]
                and finding.get("matched_text") == "여성만"
                for finding in structured_content.get("findings", [])
                if isinstance(finding, dict)
            )
            and any(
                EXPECTED_FINDING_ID in question.get("linked_findings", [])
                and bool(question.get("book_ref"))
                for question in structured_content.get("questions", [])
                if isinstance(question, dict)
            )
        ),
        "next_question_succeeded": not bool(next_question.isError)
        and isinstance(next_question_content.get("progress"), dict),
        "sex_rule_detected": EXPECTED_FINDING_ID in checked_text,
        "article_7_returned": EXPECTED_ARTICLE in checked_text,
        "equal_employment_law_returned": EXPECTED_STATUTE in checked_text,
        "write_tools_absent": not {
            "save_answer",
            "get_saved_answers",
        }
        & tool_names,
        "ruleset_version_matches_local": health.get("ruleset_version")
        == local_ruleset.version,
        "matching_version_matches_local": health.get("matching_version")
        == local_ruleset.matching_version,
        "runtime_source_fingerprint_matches_local": health.get(
            "runtime_source_fingerprint"
        )
        == local_runtime_fingerprint,
        "runtime_source_manifest_matches_local": health.get(
            "runtime_source_manifest"
        )
        == local_runtime_manifest,
        "security_headers_present": _security_headers_ok(security_headers),
        "anonymous_access_controls_present": _anonymous_access_controls_present(
            health, authentication, expected_claude_authentication
        ),
    }
    return {
        "schema_version": FULL_AUDIT_SCHEMA_VERSION,
        "verified_at": datetime.now(UTC).astimezone(KST).isoformat(
            timespec="seconds"
        ),
        "production_url": base_url,
        "mcp_endpoint": endpoint,
        "claude_mcp_endpoint": claude_endpoint,
        "health_endpoint": health_url,
        "book_entry": {
            "url": book_url,
            "status_code": book_response.status_code,
            "location": book_location,
            "expected_target": BOOK_ENTRY_TARGET,
        },
        "deployment_id": deployment_id,
        "deployment_target": "production",
        "health": {key: health.get(key) for key in HEALTH_REPORT_KEYS},
        "anonymous_initialize_status": anonymous.status_code,
        "anonymous_claude_initialize_status": claude_anonymous.status_code,
        "server_name": initialized.serverInfo.name,
        "claude_server_name": (
            claude_initialized.serverInfo.name
            if claude_initialized is not None
            else None
        ),
        "tools": sorted(tool_names),
        "claude_tools": sorted(claude_tool_names),
        "allow_write_check": allow_write_check,
        "local_ruleset": {
            "ruleset_version": local_ruleset.version,
            "matching_version": local_ruleset.matching_version,
            "rule_count": len(local_ruleset.rules),
            "runtime_source_fingerprint": local_runtime_fingerprint,
        },
        "security_headers": security_headers,
        "test_case": {
            "id": "restricted-sex-eligibility",
            "raw_posting_recorded": False,
            "finding_id_present": EXPECTED_FINDING_ID in checked_text,
            "basis_type": "statute",
            "statute": EXPECTED_STATUTE if EXPECTED_STATUTE in checked_text else None,
            "article": EXPECTED_ARTICLE if EXPECTED_ARTICLE in checked_text else None,
            "is_error": bool(checked.isError),
        },
        "write_check_performed": False,
        "write_check_note": (
            "Remote writes are disabled; --allow-write-check now verifies tool absence."
            if allow_write_check
            else None
        ),
        "save_answer_is_error": None,
        "checks": checks,
        "passed": all(checks.values()),
        "secret_recorded": False,
    }


async def verify_public(
    base_url: str,
    deployment_id: str | None = None,
) -> dict[str, Any]:
    """Check only what an anonymous caller can see; no token is read or sent.

    The authenticated audit (``verify``) needs the production MCP token, so a
    token mismatch blocks every kind of evidence. This runs the checks that do
    not need it: the public health document against the local build, the
    Bearer challenge on both MCP gates, the permanent-looking ``/book`` address
    that is printed as a QR code, and the security headers on ``/api/*``.
    It never calls the MCP tools and never calls ``/api/assisted-review``.
    """

    base_url = base_url.rstrip("/")
    endpoint = f"{base_url}/api/mcp"
    claude_endpoint = f"{base_url}/api/claude-mcp"
    health_url = f"{base_url}/api/health"
    book_url = f"{base_url}{BOOK_ENTRY_PATH}"

    # No Authorization header, by construction: the client has no credentials.
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        surface = await _fetch_anonymous_surface(client, base_url)
    health_response = surface["health_response"]
    health = surface["health"]
    book_response = surface["book_response"]
    anonymous = surface["anonymous"]
    claude_anonymous = surface["claude_anonymous"]

    authentication = health.get("authentication")
    claude_authentication = health.get("claude_readonly_authentication")
    expected_claude_authentication = _expected_claude_authentication(authentication)
    local_ruleset = load_ruleset()
    local_runtime_fingerprint = runtime_source_fingerprint(
        ruleset_version=local_ruleset.version,
        matching_version=local_ruleset.matching_version,
    )
    local_runtime_manifest = runtime_source_manifest()
    responses = {
        "health": health_response,
        "mcp": anonymous,
        "claude_mcp": claude_anonymous,
    }
    api_security_headers = {
        name: _security_headers(response) for name, response in responses.items()
    }
    book_location = book_response.headers.get("location")
    checks = {
        "health_ok": health_response.status_code == 200
        and health.get("status") == "ok",
        "ruleset_version_matches_local": health.get("ruleset_version")
        == local_ruleset.version,
        "matching_version_matches_local": health.get("matching_version")
        == local_ruleset.matching_version,
        "runtime_source_fingerprint_matches_local": health.get(
            "runtime_source_fingerprint"
        )
        == local_runtime_fingerprint,
        "runtime_source_manifest_matches_local": health.get(
            "runtime_source_manifest"
        )
        == local_runtime_manifest,
        "book_entry_redirect_is_temporary": _book_entry_redirect_is_temporary(
            book_response.status_code,
            book_location,
            base_url,
        ),
        "anonymous_mcp_gate_matches": _anonymous_gate_matches(
            authentication, anonymous
        ),
        "claude_authentication_mode_matches": claude_authentication
        == expected_claude_authentication,
        "anonymous_claude_mcp_gate_matches": _anonymous_gate_matches(
            claude_authentication, claude_anonymous
        ),
        "security_headers_present_on_api": all(
            _security_headers_ok(headers) for headers in api_security_headers.values()
        ),
        "anonymous_access_controls_present": _anonymous_access_controls_present(
            health, authentication, expected_claude_authentication
        ),
    }
    return {
        "schema_version": PUBLIC_CHECK_SCHEMA_VERSION,
        "verified_at": datetime.now(UTC).astimezone(KST).isoformat(
            timespec="seconds"
        ),
        "scope": "anonymous requests only",
        "authenticated_checks_performed": False,
        "token_used": False,
        "assisted_review_called": False,
        "production_url": base_url,
        "mcp_endpoint": endpoint,
        "claude_mcp_endpoint": claude_endpoint,
        "health_endpoint": health_url,
        "deployment_id": deployment_id,
        "health": {key: health.get(key) for key in HEALTH_REPORT_KEYS},
        "book_entry": {
            "url": book_url,
            "status_code": book_response.status_code,
            "location": book_location,
            "expected_target": BOOK_ENTRY_TARGET,
        },
        "anonymous_responses": {
            "mcp": {
                "status_code": anonymous.status_code,
                "www_authenticate": anonymous.headers.get("www-authenticate"),
            },
            "claude_mcp": {
                "status_code": claude_anonymous.status_code,
                "www_authenticate": claude_anonymous.headers.get("www-authenticate"),
            },
        },
        "api_security_headers": api_security_headers,
        "local_ruleset": {
            "ruleset_version": local_ruleset.version,
            "matching_version": local_ruleset.matching_version,
            "rule_count": len(local_ruleset.rules),
            "runtime_source_fingerprint": local_runtime_fingerprint,
        },
        "checks": checks,
        "passed": all(checks.values()),
        "secret_recorded": False,
    }


def _refuse_full_audit_output(output: Path) -> None:
    """The token-less report must never replace the authenticated evidence."""

    for protected in (ROOT / FULL_AUDIT_OUTPUT, FULL_AUDIT_OUTPUT):
        if paths_alias(output, protected):
            raise ValueError(
                "--public-only 결과는 reports/vercel_deployment_audit.json에 쓸 수 없습니다. "
                "그 파일은 인증 검증 증거입니다."
            )


def _run_public_only(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.output is None:
        parser.error("--public-only에는 --output이 필요합니다")
    if args.allow_write_check:
        parser.error("--public-only는 --allow-write-check와 함께 쓸 수 없습니다")
    try:
        _refuse_full_audit_output(args.output)
    except ValueError as error:
        parser.error(str(error))
    # FAIRPOST_MCP_TOKEN is deliberately not read here.
    try:
        report = anyio.run(verify_public, args.url, args.deployment_id)
    except httpx.HTTPError as error:
        raise SystemExit(f"Vercel 공개 검증 요청에 실패했습니다: {error}") from error
    report["verification_context"] = {
        "source_commit": args.source_commit,
        "verified_by": args.verified_by,
        "approval_ref": args.approval_ref,
    }
    atomic_write_text(
        args.output, json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    failed = sorted(name for name, ok in report["checks"].items() if not ok)
    if failed:
        raise SystemExit(
            f"Vercel 공개 검증에 실패했습니다: {', '.join(failed)} ({args.output})"
        )
    print(f"Vercel 공개 검증 통과: {report['production_url']} -> {args.output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the deployed FairPost Vercel MCP without recording secrets."
    )
    parser.add_argument(
        "--url",
        default="https://fairmcp.vercel.app",
    )
    parser.add_argument("--deployment-id")
    parser.add_argument(
        "--source-commit",
        default=os.environ.get("VERCEL_GIT_COMMIT_SHA"),
        help="Git commit whose deployable source was verified, when known.",
    )
    parser.add_argument(
        "--verified-by",
        default=os.environ.get("FAIRPOST_VERIFIED_BY"),
        help="Automation or person identifier responsible for this verification.",
    )
    parser.add_argument(
        "--approval-ref",
        default=os.environ.get("FAIRPOST_APPROVAL_REF"),
        help="Optional release-approval reference; never place secrets here.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Report path (default reports/vercel_deployment_audit.json). "
            "Required with --public-only, which may not write that file."
        ),
    )
    parser.add_argument(
        "--public-only",
        action="store_true",
        help=(
            "Run only the checks that need no token (health against the local build, "
            "the Bearer challenge on /api/mcp and /api/claude-mcp, /book, security "
            "headers) and write a separate report; it never reads FAIRPOST_MCP_TOKEN."
        ),
    )
    parser.add_argument(
        "--allow-write-check",
        action="store_true",
        help=(
            "Compatibility flag: verify that remote write tools are absent; "
            "no write request is sent."
        ),
    )
    args = parser.parse_args()
    if args.public_only:
        return _run_public_only(args, parser)
    if args.output is None:
        args.output = FULL_AUDIT_OUTPUT
    token = os.environ.get("FAIRPOST_MCP_TOKEN", "")
    report = anyio.run(
        verify,
        args.url,
        token,
        args.deployment_id,
        args.allow_write_check,
    )
    verification_context = {
        "source_commit": args.source_commit,
        "verified_by": args.verified_by,
        "approval_ref": args.approval_ref,
    }
    verification_context_complete = all(
        isinstance(value, str) and bool(value.strip())
        for value in verification_context.values()
    )
    report["verification_context"] = verification_context
    checks = report.setdefault("checks", {})
    if not isinstance(checks, dict):
        raise SystemExit("Vercel MCP 검증 checks 형식이 잘못되었습니다")
    checks["verification_context_complete"] = verification_context_complete
    report["passed"] = report.get("passed") is True and verification_context_complete
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not report["passed"]:
        raise SystemExit(
            "Vercel MCP 운영 검증에 실패했습니다. "
            "source commit, verifier, approval reference를 모두 확인하십시오."
        )
    print(
        "Vercel MCP 검증 통과: "
        f"{report['mcp_endpoint']} ({len(report['tools'])} tools)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
