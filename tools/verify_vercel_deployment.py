from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


EXPECTED_TOOLS = {
    "check_job_posting",
    "get_saved_answers",
    "save_answer",
}


async def verify(
    base_url: str,
    token: str,
    deployment_id: str | None,
) -> dict[str, Any]:
    base_url = base_url.rstrip("/")
    endpoint = f"{base_url}/api/mcp"
    health_url = f"{base_url}/api/health"
    timeout = httpx.Timeout(60.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        health_response = await client.get(health_url)
        health_response.raise_for_status()
        health = health_response.json()
        unauthorized = await client.post(
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

    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    ) as authenticated_client:
        async with streamable_http_client(
            endpoint,
            http_client=authenticated_client,
        ) as (read, write, _session_id):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                checked = await session.call_tool(
                    "check_job_posting",
                    {"text": "남성만 지원 가능"},
                )
                saved = await session.call_tool(
                    "save_answer",
                    {
                        "org_id": "deployment-verification",
                        "question_id": "Q-INFO-001",
                        "answer": "문의 채널을 명시합니다.",
                    },
                )

    tool_names = {tool.name for tool in listed.tools}
    findings = checked.structuredContent.get("findings", [])
    first_finding = findings[0] if findings else {}
    basis = first_finding.get("basis") or {}
    answer_store = health.get("answer_store")
    save_behavior_matches = (
        bool(saved.isError)
        if answer_store == "unavailable"
        else not bool(saved.isError)
    )
    checks = {
        "health_ok": health_response.status_code == 200
        and health.get("status") == "ok",
        "bearer_required": unauthorized.status_code == 401,
        "expected_tools": tool_names == EXPECTED_TOOLS,
        "tool_call_succeeded": not bool(checked.isError),
        "sex_rule_detected": first_finding.get("id") == "SEX-001",
        "article_7_returned": basis.get("article") == "제7조",
        "equal_employment_law_returned": basis.get("law")
        == "남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률",
        "save_behavior_matches_store": save_behavior_matches,
    }
    return {
        "verified_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(
            timespec="seconds"
        ),
        "production_url": base_url,
        "mcp_endpoint": endpoint,
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
                "answer_store",
                "ruleset_version",
                "matching_version",
            )
        },
        "unauthorized_status": unauthorized.status_code,
        "server_name": initialized.serverInfo.name,
        "tools": sorted(tool_names),
        "test_case": {
            "id": "restricted-sex-eligibility",
            "raw_posting_recorded": False,
            "finding_count": len(findings),
            "finding_id": first_finding.get("id"),
            "basis_type": basis.get("type"),
            "statute": basis.get("law"),
            "article": basis.get("article"),
            "is_error": bool(checked.isError),
        },
        "save_answer_is_error": bool(saved.isError),
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
        default="https://fairpost-mcp.vercel.app",
    )
    parser.add_argument("--deployment-id")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/vercel_deployment_audit.json"),
    )
    args = parser.parse_args()
    token = os.environ.get("FAIRPOST_MCP_TOKEN", "")
    if not token:
        raise SystemExit("FAIRPOST_MCP_TOKEN 환경변수가 필요합니다")
    report = anyio.run(verify, args.url, token, args.deployment_id)
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
