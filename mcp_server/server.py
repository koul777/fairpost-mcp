from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from core import FairpostEngine
from core.schema import CheckResult
from .storage import UpstashAnswerStore, build_answer_store


def _host_from_environment() -> str:
    default_host = "0.0.0.0" if os.environ.get("VERCEL") else "127.0.0.1"
    host = os.environ.get("FAIRPOST_MCP_HOST", default_host).strip()
    if not host:
        raise ValueError("FAIRPOST_MCP_HOST는 비어 있을 수 없습니다")
    loopback_hosts = {"127.0.0.1", "::1", "localhost"}
    remote_allowed = bool(os.environ.get("VERCEL")) or os.environ.get(
        "FAIRPOST_ALLOW_REMOTE", ""
    ).casefold() in {"1", "true", "yes"}
    if host.casefold() not in loopback_hosts and not remote_allowed:
        raise ValueError(
            "외부 호스트 바인딩은 공고문 원격 전송 위험이 있습니다. "
            "운영 정책을 변경한 경우에만 FAIRPOST_ALLOW_REMOTE=1을 설정하십시오"
        )
    return host


def _transport_security_from_environment() -> TransportSecuritySettings:
    allowed_hosts = [
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
    ]
    allowed_origins = [
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
    ]
    if os.environ.get("VERCEL"):
        allowed_hosts.append("fairpost-mcp.vercel.app")
        allowed_origins.append("https://fairpost-mcp.vercel.app")
    allowed_hosts.extend(
        value.strip()
        for value in os.environ.get(
            "FAIRPOST_MCP_ALLOWED_HOSTS", ""
        ).split(",")
        if value.strip()
    )
    allowed_origins.extend(
        value.strip()
        for value in os.environ.get(
            "FAIRPOST_MCP_ALLOWED_ORIGINS", ""
        ).split(",")
        if value.strip()
    )
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(dict.fromkeys(allowed_hosts)),
        allowed_origins=list(dict.fromkeys(allowed_origins)),
    )


def _port_from_environment() -> int:
    raw = os.environ.get("FAIRPOST_MCP_PORT") or os.environ.get("PORT") or "8000"
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("FAIRPOST_MCP_PORT는 정수여야 합니다") from exc
    if not 1 <= port <= 65535:
        raise ValueError("FAIRPOST_MCP_PORT는 1~65535 범위여야 합니다")
    return port


def _validated_mcp_path(value: str, variable_name: str) -> str:
    path = value.strip()
    if not path.startswith("/") or path.endswith("/") or "//" in path:
        raise ValueError(
            f"{variable_name}는 '/'로 시작하고 '/'로 끝나지 않는 경로여야 합니다"
        )
    return path


def _mcp_path_from_environment() -> str:
    default = "/api/mcp" if os.environ.get("VERCEL") else "/mcp"
    return _validated_mcp_path(
        os.environ.get("FAIRPOST_MCP_PATH", default),
        "FAIRPOST_MCP_PATH",
    )


def _claude_mcp_path_from_environment() -> str:
    default = "/api/claude-mcp" if os.environ.get("VERCEL") else "/claude-mcp"
    return _validated_mcp_path(
        os.environ.get("FAIRPOST_CLAUDE_MCP_PATH", default),
        "FAIRPOST_CLAUDE_MCP_PATH",
    )


READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
ADDITIVE_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


mcp = FastMCP(
    "fairpost",
    instructions=(
        "채용공고문을 규칙 기반으로 점검합니다. 반환 JSON을 요약하거나 생략하지 말고 "
        "사용자에게 그대로 제시하십시오. 공정성 여부 판정이나 법률 자문을 제공하지 않습니다."
    ),
    host=_host_from_environment(),
    port=_port_from_environment(),
    streamable_http_path=_mcp_path_from_environment(),
    json_response=True,
    stateless_http=True,
    transport_security=_transport_security_from_environment(),
)
claude_mcp = FastMCP(
    "fairpost-readonly",
    instructions=(
        "채용공고문을 규칙 기반으로 점검하는 공개 읽기 전용 도구입니다. "
        "반환 JSON을 요약하거나 생략하지 말고 사용자에게 그대로 제시하십시오. "
        "공정성 여부 판정이나 법률 자문을 제공하지 않습니다."
    ),
    host=_host_from_environment(),
    port=_port_from_environment(),
    streamable_http_path=_claude_mcp_path_from_environment(),
    json_response=True,
    stateless_http=True,
    transport_security=_transport_security_from_environment(),
)
engine = FairpostEngine()
answer_store = build_answer_store()


def _question_ids() -> frozenset[str]:
    return frozenset(
        rule["id"]
        for rule in engine.ruleset.rules
        if rule["layer"] == "question"
    )


@mcp.tool(
    description=(
        "채용공고문을 점검하여 관련 법령 조항과 검토가 필요한 질문을 제시합니다. "
        "공정성 여부를 판정하지 않습니다. 반환된 질문은 요약하거나 생략하지 말고 "
        "사용자에게 그대로 제시하십시오. 법률 자문을 제공하지 않습니다."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
)
def check_job_posting(text: str, org_id: str | None = None) -> CheckResult:
    """Return the complete deterministic CheckResult JSON."""
    answers = answer_store.get(org_id) if org_id else {}
    return engine.check(text, saved_answers=answers)


@claude_mcp.tool(
    name="check_job_posting",
    description=(
        "채용공고문을 점검하여 관련 법령 조항과 검토가 필요한 질문을 제시합니다. "
        "입력 공고문을 저장하거나 외부 시스템을 변경하지 않습니다. "
        "공정성 여부를 판정하지 않습니다. 반환된 JSON과 질문은 요약하거나 "
        "생략하지 말고 사용자에게 그대로 제시하십시오. 법률 자문을 제공하지 않습니다."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
)
def check_job_posting_readonly(text: str) -> CheckResult:
    """Return the complete deterministic CheckResult without saved answers."""
    return engine.check(text)


@mcp.tool(
    description=(
        "조직의 검토 질문 답변을 사용자 컴퓨터의 로컬 JSON에만 저장합니다. "
        "채용공고문 원문은 저장하지 않습니다."
    ),
    annotations=ADDITIVE_WRITE_ANNOTATIONS,
)
def save_answer(org_id: str, question_id: str, answer: str) -> dict[str, str]:
    if question_id not in _question_ids():
        raise ValueError(f"현재 사전에 없는 question_id입니다: {question_id}")
    answer_store.save(org_id, question_id, answer)
    status = (
        "stored_remotely"
        if isinstance(answer_store, UpstashAnswerStore)
        else "stored_locally"
    )
    return {"org_id": org_id, "question_id": question_id, "status": status}


@mcp.tool(
    description="조직별로 로컬 JSON에 저장된 검토 질문 답변을 그대로 반환합니다.",
    annotations=READ_ONLY_ANNOTATIONS,
)
def get_saved_answers(org_id: str) -> dict[str, str]:
    return answer_store.get(org_id)


def main() -> None:
    """Run the default local Streamable HTTP MCP endpoint."""
    mcp.run(transport="streamable-http")


def main_stdio() -> None:
    """Run the compatibility stdio transport for local legacy clients."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
