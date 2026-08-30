from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from core import FairpostEngine
from core.schema import CheckResult, Question
from .storage import (
    EphemeralAnswerStore,
    UnavailableRemoteAnswerStore,
    UpstashAnswerStore,
    build_answer_store,
)


def _host_from_environment() -> str:
    """Resolve the full four-tool MCP host, which is always loopback-only."""

    host = os.environ.get("FAIRPOST_MCP_HOST", "127.0.0.1").strip()
    if not host:
        raise ValueError("FAIRPOST_MCP_HOST는 비어 있을 수 없습니다")
    loopback_hosts = {"127.0.0.1", "::1", "localhost"}
    if host.casefold() not in loopback_hosts:
        raise ValueError(
            "답변 저장 기능이 있는 전체 MCP는 루프백에서만 실행할 수 있습니다. "
            "네트워크 배포에는 읽기 전용 mcp_server.remote:app을 사용하십시오"
        )
    return host


def _read_only_host_from_environment() -> str:
    """Resolve the stateless read-only profile used by the remote ASGI app."""

    default_host = "0.0.0.0" if os.environ.get("VERCEL") else "127.0.0.1"
    host = os.environ.get("FAIRPOST_READ_ONLY_MCP_HOST", default_host).strip()
    if not host:
        raise ValueError("FAIRPOST_READ_ONLY_MCP_HOST는 비어 있을 수 없습니다")
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
        allowed_hosts.extend(
            ["fairmcp.vercel.app", "fairpost-mcp.vercel.app"]
        )
        allowed_origins.extend(
            [
                "https://fairmcp.vercel.app",
                "https://fairpost-mcp.vercel.app",
            ]
        )
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


MCP_PATH = _mcp_path_from_environment()
CLAUDE_MCP_PATH = _claude_mcp_path_from_environment()


# Questions whose matched_text is one of these near-universal words fire on
# almost any posting regardless of whether anything is actually wrong; they
# are demoted to the "참고용" tier in _format_check_result_text unless also
# linked to a real finding.
_GENERIC_QUESTION_MATCHES = frozenset({"채용", "모집"})

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
        "채용공고문을 규칙 기반으로 점검합니다. check_job_posting은 findings/"
        "slots/questions를 하나도 누락하지 않고 근거 법령ㆍ조문ㆍ해당 문구ㆍ대안 "
        "표현이 드러나는 사람이 읽기 쉬운 텍스트로 반환합니다. 이 텍스트를 요약하거나 "
        "생략하지 말고 그대로 제시하십시오. 공정성 여부 판정이나 법률 자문을 "
        "제공하지 않습니다."
    ),
    host=_host_from_environment(),
    port=_port_from_environment(),
    streamable_http_path=MCP_PATH,
    json_response=False,
    stateless_http=True,
    transport_security=_transport_security_from_environment(),
)
public_mcp = FastMCP(
    "fairpost",
    instructions=(
        "Analyze job postings with deterministic rules and return findings plus "
        "review questions without persisting answers."
    ),
    host=_read_only_host_from_environment(),
    port=_port_from_environment(),
    streamable_http_path=MCP_PATH,
    json_response=False,
    stateless_http=True,
    transport_security=_transport_security_from_environment(),
)
claude_mcp = FastMCP(
    "fairpost-readonly",
    instructions=(
        "채용공고문을 규칙 기반으로 점검하는 공개 읽기 전용 도구입니다. "
        "check_job_posting은 findings/slots/questions를 하나도 누락하지 않고 "
        "근거 법령ㆍ조문ㆍ해당 문구ㆍ대안 표현이 드러나는 사람이 읽기 쉬운 텍스트로 "
        "반환합니다. 이 텍스트를 요약하거나 생략하지 말고 그대로 제시하십시오. "
        "공정성 여부 판정이나 법률 자문을 제공하지 않습니다."
    ),
    host=_read_only_host_from_environment(),
    port=_port_from_environment(),
    streamable_http_path=CLAUDE_MCP_PATH,
    json_response=True,
    stateless_http=True,
    transport_security=_transport_security_from_environment(),
)
engine = FairpostEngine()
answer_store = (
    UnavailableRemoteAnswerStore()
    if os.environ.get("VERCEL")
    else build_answer_store()
)


def _question_ids() -> frozenset[str]:
    return frozenset(
        rule["id"]
        for rule in engine.ruleset.rules
        if rule["layer"] == "question"
    )


def _saved_answers(org_id: str | None) -> dict[str, str]:
    """Look up previously saved answers, degrading gracefully when unavailable.

    An unconfigured remote answer store must still fail loudly for
    save_answer (a write claiming persistence it can't provide), but here
    it's only an optional enrichment for check_job_posting/next_review_question.
    Letting it raise would take down the entire fairness check over a
    missing Upstash connection unrelated to the check itself.
    """
    if not org_id or isinstance(answer_store, UnavailableRemoteAnswerStore):
        return {}
    return answer_store.get(org_id)


def _format_check_result_text(result: CheckResult) -> str:
    """Render CheckResult as a complete, human-readable plain-text digest.

    check_job_posting returns this string directly (structured_output=False)
    instead of a typed object, because MCP clients differ in whether they
    render a tool's structuredContent or its text content to the user; some
    (observed with a ChatGPT connector) surface the raw structured JSON
    regardless of the accompanying text. A plain string return is the only
    representation FastMCP never JSON-encodes, so every client shows this
    text as-is. Every finding and question is included (no summarizing or
    dropping items).
    """
    lines: list[str] = [
        f"채용공고 점검 결과 — 발견 {len(result.findings)}건, "
        f"검토 질문 {len(result.questions)}건, "
        f"확인된 안내 항목 {sum(1 for slot in result.slots if slot.found)}건"
        f"/{len(result.slots)}건",
        "",
    ]

    lines.append("## 1. 관련 법령 표현 검토 후보 (findings)")
    if result.findings:
        lines.append("| ID | 구분 | 매칭 문구 | 근거 법령·조문 | 심각도 | 대안 표현 |")
        lines.append("|---|---|---|---|---|---|")
        for finding in result.findings:
            law_ref = "ㆍ".join(
                part for part in (finding.basis.law, finding.basis.article) if part
            )
            matched = (finding.matched_text or "").replace("|", "\\|")
            alternatives = (
                " / ".join(finding.alternatives).replace("|", "\\|")
                if finding.alternatives
                else "-"
            )
            lines.append(
                f'| {finding.id} | {finding.dimension} | "{matched}" | '
                f"{law_ref or '해당 없음'} | {finding.severity or '미지정'} | "
                f"{alternatives} |"
            )
    else:
        lines.append("발견된 항목이 없습니다.")
    lines.append("")

    found_slots = [slot for slot in result.slots if slot.found]
    if found_slots:
        lines.append("## 확인된 안내 항목 (slots)")
        for slot in found_slots:
            lines.append(f"- [{slot.slot}] {slot.label}: {slot.evidence or '확인됨'}")
    missing_slots = [slot for slot in result.slots if not slot.found]
    if missing_slots:
        lines.append(
            "## 확인되지 않은 안내 항목 (slots) — 없다는 뜻이 아니라 "
            "이 공고문에서 발견되지 않았다는 뜻입니다"
        )
        for slot in missing_slots:
            lines.append(f"- [{slot.slot}] {slot.label}")
    lines.append("")

    if result.questions:
        lines.append("## 2. 공정성 설계 질문 (questions)")

        def question_line(question: Question) -> str:
            suffixes = []
            if question.matched_text:
                suffixes.append(f'매칭 문구: "{question.matched_text}"')
            elif question.linked_findings:
                suffixes.append(f"관련 발견: {', '.join(question.linked_findings)}")
            if question.saved_answer:
                suffixes.append(f"저장된 답변: {question.saved_answer}")
            suffix = f" ({', '.join(suffixes)})" if suffixes else ""
            return f"- [{question.id}] {question.question}{suffix}"

        def is_core(question: Question) -> bool:
            return bool(question.linked_findings) or bool(
                question.matched_text
                and question.matched_text not in _GENERIC_QUESTION_MATCHES
            )

        core_questions = [item for item in result.questions if is_core(item)]
        general_questions = [
            item for item in result.questions if not is_core(item)
        ]

        def append_by_dimension(questions: list[Question]) -> None:
            for dimension in ("분배", "절차", "정보", "대인"):
                group = [item for item in questions if item.dimension == dimension]
                if not group:
                    continue
                lines.append(f"**{dimension} ({len(group)}건)**")
                lines.extend(question_line(item) for item in group)

        if core_questions:
            lines.append("")
            lines.append(
                "### 핵심 질문 — 이 공고문의 findings·구체적 문구와 직접 연결됨 "
                f"({len(core_questions)}건)"
            )
            append_by_dimension(core_questions)
        if general_questions:
            lines.append("")
            lines.append(
                "### 참고용 — 특정 finding과 무관한 일반 절차 점검 질문 "
                f"({len(general_questions)}건)"
            )
            append_by_dimension(general_questions)
    lines.append("")

    lines.append(f"규칙셋 버전: {result.ruleset_version}")
    lines.append(f"근거 법령 스냅샷: {result.statute_snapshot_date} — {result.statute_notice}")
    lines.append(result.disclaimer)
    return "\n".join(lines)


@mcp.tool(
    description=(
        "채용공고문을 점검하여 관련 법령 조항과 검토가 필요한 질문을 제시합니다. "
        "공정성 여부를 판정하지 않습니다. 반환된 텍스트는 findings와 질문을 하나도 "
        "빠짐없이 근거 법령ㆍ조문ㆍ해당 문구와 함께 정리한 결과이며, 요약하거나 "
        "생략하지 말고 그대로 제시하십시오. 법률 자문을 제공하지 않습니다."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=False,
)
def check_job_posting(text: str, org_id: str | None = None) -> str:
    """Return the complete deterministic check result as human-readable text.

    Returns plain text (not the CheckResult dataclass) and disables
    structured_output so FastMCP never encodes the result as JSON: some MCP
    clients (observed with a ChatGPT connector) show a tool's structured
    output verbatim to the user instead of its text content, which made
    every prior representation of this result render as a raw JSON dump.
    """
    answers = _saved_answers(org_id)
    result = engine.check(text, saved_answers=answers)
    return _format_check_result_text(result)


@claude_mcp.tool(
    name="check_job_posting",
    description=(
        "채용공고문을 점검하여 관련 법령 조항과 검토가 필요한 질문을 제시합니다. "
        "입력 공고문을 저장하거나 외부 시스템을 변경하지 않습니다. "
        "공정성 여부를 판정하지 않습니다. 반환된 텍스트는 findings와 질문을 하나도 "
        "빠짐없이 근거 법령ㆍ조문ㆍ해당 문구와 함께 정리한 결과이며, 요약하거나 "
        "생략하지 말고 그대로 제시하십시오. 법률 자문을 제공하지 않습니다."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=False,
)
def check_job_posting_readonly(text: str) -> str:
    """Return the complete deterministic check result as human-readable text.

    See check_job_posting for why this is plain text with structured_output
    disabled rather than the CheckResult dataclass.
    """
    result = engine.check(text)
    return _format_check_result_text(result)


@public_mcp.tool(
    name="check_job_posting",
    description=(
        "Analyze a job posting and return the complete findings and follow-up "
        "questions as plain text. The posting text is not persisted."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=False,
)
def check_job_posting_public(text: str) -> str:
    return check_job_posting(text, None)


def _question_payload(question: Question) -> dict[str, Any]:
    return {
        "id": question.id,
        "dimension": question.dimension,
        "question": question.question,
        "follow_up": list(question.follow_up),
        "review_scope": question.review_scope,
        "trigger_reason": question.trigger_reason,
        "linked_findings": list(question.linked_findings),
        "matched_text": question.matched_text,
        "section": question.section,
    }


@mcp.tool(
    description=(
        "아직 답변이 저장되지 않은 다음 검토 질문 하나와 진행 상황을 반환합니다. "
        "질문은 사전 문구 그대로이며 요약하거나 바꾸지 말고 제시하십시오. "
        "한 번에 한 질문만 묻고 답변은 save_answer로 저장하십시오. "
        "공정성 여부를 판정하지 않습니다."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
)
def next_review_question(text: str, org_id: str | None = None) -> dict[str, Any]:
    """Advance the one-question-at-a-time review without leaving the dictionary."""
    answers = _saved_answers(org_id)
    result = engine.check(text, saved_answers=answers)
    pending = [item for item in result.questions if not item.saved_answer]
    return {
        "question": _question_payload(pending[0]) if pending else None,
        "progress": {
            "total": len(result.questions),
            "answered": len(result.questions) - len(pending),
            "remaining": len(pending),
        },
        "counts": dict(result.counts),
        "disclaimer": result.disclaimer,
    }


@public_mcp.tool(
    name="next_review_question",
    description=(
        "Return the next unanswered review question for a job posting without "
        "storing answers or changing server state."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
)
def next_review_question_public(text: str) -> dict[str, Any]:
    return next_review_question(text, None)


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
    if isinstance(answer_store, UpstashAnswerStore):
        status = "stored_remotely"
    elif isinstance(answer_store, EphemeralAnswerStore):
        status = "stored_ephemerally"
    else:
        status = "stored_locally"
    return {"org_id": org_id, "question_id": question_id, "status": status}


@mcp.tool(
    description="조직별로 로컬 JSON에 저장된 검토 질문 답변을 그대로 반환합니다.",
    annotations=READ_ONLY_ANNOTATIONS,
)
def get_saved_answers(org_id: str) -> dict[str, str]:
    return answer_store.get(org_id)


def _set_tool_description(server_instance: FastMCP, name: str, description: str) -> None:
    tool = server_instance._tool_manager.get_tool(name)
    if tool is not None:
        tool.description = description


_set_tool_description(
    mcp,
    "save_answer",
    "Persist an organization's review-question answer in the configured "
    "answer store. The raw job posting text is never stored.",
)
_set_tool_description(
    mcp,
    "get_saved_answers",
    "Return previously saved review-question answers for one organization "
    "from the configured answer store.",
)


def main() -> None:
    """Run the default local Streamable HTTP MCP endpoint."""
    mcp.run(transport="streamable-http")


def main_stdio() -> None:
    """Run the compatibility stdio transport for local legacy clients."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
