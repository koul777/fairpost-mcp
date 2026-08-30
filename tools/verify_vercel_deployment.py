from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loader import load_ruleset  # noqa: E402
from mcp_server.build_identity import runtime_source_fingerprint  # noqa: E402


EXPECTED_PUBLIC_TOOLS = {
    "check_job_posting",
    "next_review_question",
}
KST = timezone(timedelta(hours=9))
SAMPLE_POSTING = "여성만 지원 가능"
EXPECTED_FINDING_ID = "SEX-001"
EXPECTED_STATUTE = "남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률"
EXPECTED_ARTICLE = "제7조"


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
    timeout = httpx.Timeout(60.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        health_response = await client.get(health_url)
        health_response.raise_for_status()
        health = health_response.json()
        anonymous = await client.post(
            endpoint,
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "fairpost-deployment-verifier",
                        "version": "1",
                    },
                },
            },
        )
        claude_anonymous = await client.post(
            claude_endpoint,
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "fairpost-deployment-verifier",
                        "version": "1",
                    },
                },
            },
        )

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
                next_question = await session.call_tool(
                    "next_review_question",
                    {"text": SAMPLE_POSTING},
                )
                saved = None

    tool_names = {tool.name for tool in listed.tools}
    checked_text = checked.content[0].text if checked.content else ""
    authentication = health.get("authentication")
    expected_claude_authentication = (
        "bearer"
        if authentication == "bearer"
        else (
            "none"
            if os.environ.get(
                "FAIRPOST_ALLOW_PUBLIC_CLAUDE_REMOTE", ""
            ).casefold()
            in {"1", "true", "yes"}
            else "disabled"
        )
    )
    local_ruleset = load_ruleset()
    local_runtime_fingerprint = runtime_source_fingerprint(
        ruleset_version=local_ruleset.version,
        matching_version=local_ruleset.matching_version,
    )
    next_question_content = next_question.structuredContent or {}

    authentication_behavior_matches = _anonymous_authentication_behavior_matches(
        authentication,
        anonymous.status_code,
    )
    security_headers = {
        "cache_control": health_response.headers.get("cache-control"),
        "content_type_options": health_response.headers.get(
            "x-content-type-options"
        ),
        "frame_options": health_response.headers.get("x-frame-options"),
        "referrer_policy": health_response.headers.get("referrer-policy"),
    }
    cache_control_values = {
        value.strip()
        for value in (security_headers["cache_control"] or "").split(",")
        if value.strip()
    }
    checks = {
        "health_ok": health_response.status_code == 200
        and health.get("status") == "ok",
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
        "expected_tools": tool_names == _expected_tools(authentication),
        "tool_call_succeeded": not bool(checked.isError),
        "check_job_posting_returns_plain_text": (
            checked.structuredContent is None
            and checked_text.strip().startswith("채용공고 점검 결과")
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
        "security_headers_present": cache_control_values == {"no-store"}
        and security_headers["content_type_options"] == "nosniff"
        and security_headers["frame_options"] == "DENY"
        and security_headers["referrer_policy"] == "no-referrer",
        "anonymous_access_controls_present": (
            isinstance(health.get("anonymous_access_controls"), dict)
            and health["anonymous_access_controls"].get("enabled") is True
            and health["anonymous_access_controls"].get("strategy")
            == "per_instance_client_fixed_window"
            and isinstance(
                health["anonymous_access_controls"].get("requests_per_minute"),
                int,
            )
            and health["anonymous_access_controls"].get("stores_raw_client_address")
            is False
            and health["anonymous_access_controls"].get("stores_request_content")
            is False
            if authentication == "none"
            or expected_claude_authentication == "none"
            else True
        ),
    }
    return {
        "schema_version": "fairpost-vercel-deployment-audit-v2",
        "verified_at": datetime.now(UTC).astimezone(KST).isoformat(
            timespec="seconds"
        ),
        "production_url": base_url,
        "mcp_endpoint": endpoint,
        "claude_mcp_endpoint": claude_endpoint,
        "health_endpoint": health_url,
        "deployment_id": deployment_id,
        "deployment_target": "production",
        "health": {
            key: health.get(key)
            for key in (
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
                "anonymous_access_controls",
            )
        },
        "anonymous_initialize_status": anonymous.status_code,
        "anonymous_claude_initialize_status": claude_anonymous.status_code,
        "server_name": initialized.serverInfo.name,
        "tools": sorted(tool_names),
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
        "save_answer_is_error": None if saved is None else bool(saved.isError),
        "checks": checks,
        "passed": all(checks.values()),
        "secret_recorded": False,
    }


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
        "--output",
        type=Path,
        default=Path("reports/vercel_deployment_audit.json"),
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
    token = os.environ.get("FAIRPOST_MCP_TOKEN", "")
    report = anyio.run(
        verify,
        args.url,
        token,
        args.deployment_id,
        args.allow_write_check,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not report["passed"]:
        raise SystemExit("Vercel MCP 운영 검증에 실패했습니다")
    print(
        "Vercel MCP 검증 통과: "
        f"{report['mcp_endpoint']} ({len(report['tools'])} tools)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
