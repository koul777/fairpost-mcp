from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
from typing import Annotated, Any, Literal
import uuid

from mcp.server.fastmcp import FastMCP
from pydantic import Field
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from core import FairpostEngine
from core.review_packet import (
    SYSTEM_CHAIR_ACTOR_REF,
    ReviewAction,
    ReviewEvent,
    ReviewRole,
    ReviewStage,
)
from core.schema import CheckResult, Finding, Question, SlotStatus
from .review import HrReviewPacket, prepare_hr_review_packet
from .storage import (
    LocalReviewPacketStore,
    UnavailableRemoteAnswerStore,
    build_answer_store,
)


def _host_from_environment() -> str:
    """Resolve the full local MCP host, which is always loopback-only."""

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

# 100,000 Korean characters stay under the remote 1 MiB request body limit
# even when a client JSON-escapes every character (6 bytes each), and match
# the assisted-review posting limit.
MAX_POSTING_CHARS = 100_000
MAX_ANSWERED_QUESTION_IDS = 256
REMOTE_BODY_LIMIT_NOTE = "원격 엔드포인트는 요청 본문 1 MiB 이하만 받습니다."
VERBATIM_TEXT_INSTRUCTION = (
    "Present the returned text to the user verbatim; do not summarize or omit "
    "any finding, notice or question. 반환된 텍스트는 요약하거나 생략하지 말고 "
    "그대로 제시하십시오."
)

PostingText = Annotated[
    str,
    Field(
        description=(
            "점검할 채용공고 원문 전체(평문). 비어 있거나 공백뿐이면 거부합니다. "
            f"최대 {MAX_POSTING_CHARS:,}자이며 더 긴 공고는 나누어 점검하십시오. "
            + REMOTE_BODY_LIMIT_NOTE
        ),
        json_schema_extra={"minLength": 1, "maxLength": MAX_POSTING_CHARS},
    ),
]
LocalOrgId = Annotated[
    str | None,
    Field(
        description=(
            "로컬에 저장된 답변을 결과에 함께 표시할 조직 ID(선택, 최대 256자). "
            "루프백 로컬 MCP에서만 사용하며 원격 엔드포인트는 받지 않습니다."
        ),
        json_schema_extra={"maxLength": 256},
    ),
]
RequiredOrgId = Annotated[
    str,
    Field(
        description="답변을 저장하거나 조회할 조직 ID(최대 256자). 로컬 파일에만 저장됩니다.",
        json_schema_extra={"minLength": 1, "maxLength": 256},
    ),
]
QuestionId = Annotated[
    str,
    Field(
        description=(
            "검토 질문 ID(예: Q-INFO-001). check_job_posting이나 "
            "next_review_question이 반환한 question.id를 그대로 사용하십시오."
        ),
        json_schema_extra={"minLength": 1, "maxLength": 128},
    ),
]
AnswerText = Annotated[
    str,
    Field(
        description=(
            "사람이 작성한 답변(최대 10,000자). 전화번호ㆍ이메일ㆍ주민등록번호 같은 "
            "직접 식별정보는 넣을 수 없습니다."
        ),
        json_schema_extra={"maxLength": 10_000},
    ),
]
AnsweredQuestionIds = Annotated[
    list[str] | None,
    Field(
        description=(
            "이미 사람이 답한 question_id 목록(선택). 원격 서버는 답변을 저장하지 "
            "않으므로, 이전 호출에서 받은 question.id를 모아 다시 보내면 다음 질문과 "
            "진행 상황을 반환합니다. 현재 질문 사전에 있는 ID만 허용합니다."
        ),
        json_schema_extra={"maxItems": MAX_ANSWERED_QUESTION_IDS},
    ),
]
PacketId = Annotated[
    str,
    Field(
        description=(
            "검토 패킷 ID. start_role_review가 반환한 review_packet.packet_id"
            "(영문ㆍ숫자로 시작하는 1~128자 ASCII)."
        ),
        json_schema_extra={"minLength": 1, "maxLength": 128},
    ),
]
OptionalPacketId = Annotated[
    str | None,
    Field(
        description=(
            "새 검토 패킷에 쓸 ID(선택). 생략하면 자동으로 만듭니다. "
            "영문ㆍ숫자로 시작하는 1~128자 ASCII."
        ),
        json_schema_extra={"maxLength": 128},
    ),
]
PurgePacketId = Annotated[
    str | None,
    Field(
        description="삭제할 검토 패킷 ID(선택). 생략하면 로컬 검토 패킷을 모두 삭제합니다.",
        json_schema_extra={"maxLength": 128},
    ),
]
PostingFingerprint = Annotated[
    str,
    Field(
        description=(
            "start_role_review가 반환한 review_packet.posting_fingerprint"
            "(공고 원문의 SHA-256). 패킷과 일치해야 합니다."
        ),
        json_schema_extra={"minLength": 64, "maxLength": 64},
    ),
]
StageArgument = Annotated[
    ReviewStage,
    Field(description="NCS 공정채용 검토 단계."),
]
RoleArgument = Annotated[
    ReviewRole,
    Field(description="이벤트를 남기는 검토 역할."),
]
ActionArgument = Annotated[
    ReviewAction,
    Field(
        description=(
            "이벤트 종류. resolve는 resolves_event_id로 해결할 이벤트를 지정해야 합니다."
        )
    ),
]
ReviewNote = Annotated[
    str,
    Field(
        description=(
            "검토 메모(최대 4,000자). 공고 원문이나 지원자 정보, 전화번호ㆍ이메일ㆍ"
            "주민등록번호 같은 직접 식별정보는 넣을 수 없습니다."
        ),
        json_schema_extra={"maxLength": 4000},
    ),
]
EvidenceRefs = Annotated[
    list[str] | None,
    Field(
        description="근거 식별자 목록(선택, 최대 32개). finding ID나 question ID를 사용합니다.",
        json_schema_extra={"maxItems": 32},
    ),
]
ActorRef = Annotated[
    str | None,
    Field(
        description=(
            "이벤트 작성자를 가리키는 가명 식별자(선택). 실명이나 연락처를 넣지 "
            "마십시오. system-chair는 예약되어 있습니다."
        ),
        json_schema_extra={"maxLength": 128},
    ),
]
ResolvesEventId = Annotated[
    str | None,
    Field(
        description="action이 resolve일 때 해결하는 기존 이벤트의 event_id.",
        json_schema_extra={"maxLength": 128},
    ),
]


def _validated_posting_text(text: str) -> str:
    """Reject inputs that would give a plausible but meaningless result."""

    if not isinstance(text, str) or not text.strip():
        raise ValueError(
            "검토할 공고문을 입력하세요. text가 비어 있거나 공백뿐입니다."
        )
    if len(text) > MAX_POSTING_CHARS:
        raise ValueError(
            f"공고문은 최대 {MAX_POSTING_CHARS:,}자까지 점검할 수 있습니다"
            f"(입력 {len(text):,}자). 공고를 나누어 점검하십시오."
        )
    return text


@dataclass(frozen=True)
class StructuredCheckResult:
    """Versioned machine-readable evidence contract for human-review clients."""

    schema_version: Literal["fairpost-structured-check-v1"]
    findings: list[Finding]
    slots: list[SlotStatus]
    questions: list[Question]
    counts: dict[str, int]
    ruleset_version: str
    statute_snapshot_date: str
    statute_notice: str
    disclaimer: str


def _structured_check_result(result: CheckResult) -> StructuredCheckResult:
    return StructuredCheckResult(
        schema_version="fairpost-structured-check-v1",
        findings=result.findings,
        slots=result.slots,
        questions=result.questions,
        counts=dict(result.counts),
        ruleset_version=result.ruleset_version,
        statute_snapshot_date=result.statute_snapshot_date,
        statute_notice=result.statute_notice,
        disclaimer=result.disclaimer,
    )

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
LOCAL_ANSWER_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
)
LIVE_LAW_REVIEW_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

mcp = FastMCP(
    "fairpost",
    instructions=(
        "채용공고문을 규칙 기반으로 점검합니다. check_job_posting은 findings/"
        "slots/questions를 하나도 누락하지 않고 근거 법령ㆍ조문ㆍ해당 문구ㆍ대안 "
        "표현이 드러나는 사람이 읽기 쉬운 텍스트로 반환합니다. 이 텍스트를 요약하거나 "
        "생략하지 말고 그대로 제시하십시오. 공정성 여부 판정이나 법률 자문을 "
        "제공하지 않습니다. 기계가 근거 연결을 재사용해야 할 때는 기존 평문 도구를 "
        "바꾸지 말고 check_job_posting_structured를 사용하십시오. NCS 통제와 선택적 "
        "현행 법령 조회까지 필요한 로컬 검토에는 prepare_hr_review를 사용하십시오. "
        "여러 역할의 검토 이력을 남길 때는 start_role_review와 record_review_event를 "
        "사용하십시오."
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
        "Use deterministic evidence to support a human review of job postings. "
        "The findings and questions do not judge fairness, legality, or a "
        "candidate's pass/fail outcome. This read-only profile does not persist "
        "posting text, answers, or analysis results. Use check_job_posting for "
        "human-readable text and check_job_posting_structured for the versioned "
        "machine-readable evidence contract. "
        + VERBATIM_TEXT_INSTRUCTION
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
review_packet_store = LocalReviewPacketStore()

LOCAL_REVIEW_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
LOCAL_REVIEW_PURGE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
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
    disabled remote answer store unrelated to the check itself.
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
        result.disclaimer,
        "",
        f"채용공고 점검 결과 — 발견 {len(result.findings)}건, "
        f"검토 질문 {len(result.questions)}건, "
        f"확인된 안내 항목 {sum(1 for slot in result.slots if slot.found)}건"
        f"/{len(result.slots)}건",
        "",
    ]

    lines.append("## 1. 관련 법령 표현 검토 후보 (findings)")
    if result.findings:
        lines.append(
            "| ID | 구분 | 매칭 문구 | 근거 법령·조문 | 검토 우선도 | 대안 표현 |"
        )
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
            if question.linked_findings:
                suffixes.append(f"관련 발견: {', '.join(question.linked_findings)}")
            if question.matched_text:
                suffixes.append(f'매칭 문구: "{question.matched_text}"')
            if question.saved_answer:
                suffixes.append(f"저장된 답변: {question.saved_answer}")
            suffix = f" ({', '.join(suffixes)})" if suffixes else ""
            return f"- [{question.id}] {question.question}{suffix}"

        # Group by the engine's own metadata rather than by matched wording:
        # common checklist questions fire on generic words such as "구인" or
        # "지원자", so a matched phrase alone does not make them posting-specific.
        def question_group(question: Question) -> str:
            if question.linked_findings:
                return "core"
            if question.review_scope == "common":
                return "common"
            if question.trigger_reason == "absence":
                return "missing"
            return "wording"

        grouped: dict[str, list[Question]] = {
            "core": [],
            "wording": [],
            "missing": [],
            "common": [],
        }
        for item in result.questions:
            grouped[question_group(item)].append(item)

        def append_by_dimension(questions: list[Question]) -> None:
            for dimension in ("분배", "절차", "정보", "대인"):
                group = [item for item in questions if item.dimension == dimension]
                if not group:
                    continue
                lines.append(f"**{dimension} ({len(group)}건)**")
                lines.extend(question_line(item) for item in group)

        group_headings = (
            ("core", "### 핵심 질문 — 이 공고문의 findings와 직접 연결됨"),
            ("wording", "### 공고 문구 확인 질문 — 이 공고문의 구체적 표현에서 발동"),
            (
                "missing",
                "### 누락 안내 확인 질문 — 공고문에서 찾지 못한 안내 항목에서 발동",
            ),
            ("common", "### 공통 체크리스트 — 대부분의 공고에 반복되는 기본 점검 질문"),
        )
        for key, heading in group_headings:
            if not grouped[key]:
                continue
            lines.append("")
            lines.append(f"{heading} ({len(grouped[key])}건)")
            append_by_dimension(grouped[key])
    lines.append("")

    lines.append(f"규칙셋 버전: {result.ruleset_version}")
    lines.append(f"근거 법령 스냅샷: {result.statute_snapshot_date} — {result.statute_notice}")
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
def check_job_posting(text: PostingText, org_id: LocalOrgId = None) -> str:
    """Return the complete deterministic check result as human-readable text.

    Returns plain text (not the CheckResult dataclass) and disables
    structured_output so FastMCP never encodes the result as JSON: some MCP
    clients (observed with a ChatGPT connector) show a tool's structured
    output verbatim to the user instead of its text content, which made
    every prior representation of this result render as a raw JSON dump.
    """
    text = _validated_posting_text(text)
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
def check_job_posting_readonly(text: PostingText) -> str:
    """Return the complete deterministic check result as human-readable text.

    See check_job_posting for why this is plain text with structured_output
    disabled rather than the CheckResult dataclass.
    """
    result = engine.check(_validated_posting_text(text))
    return _format_check_result_text(result)


@public_mcp.tool(
    name="check_job_posting",
    description=(
        "Return deterministic evidence and follow-up questions for human review. "
        "This does not judge fairness, legality, or candidate pass/fail outcomes, "
        "and it does not persist the posting text or analysis. "
        + VERBATIM_TEXT_INSTRUCTION
        + f" The posting text must be non-empty and at most {MAX_POSTING_CHARS:,} "
        "characters; the remote request body limit is 1 MiB."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=False,
)
def check_job_posting_public(text: PostingText) -> str:
    return check_job_posting(text, None)


@mcp.tool(
    description=(
        "채용공고문의 findings, slots, questions와 근거ㆍ원문 위치를 버전이 있는 "
        "기계 판독 구조로 반환합니다. 사람의 검토를 지원하는 읽기 전용 도구이며 "
        "공정성ㆍ적법성ㆍ합격 여부를 판정하지 않습니다."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=True,
)
def check_job_posting_structured(
    text: PostingText,
    org_id: LocalOrgId = None,
) -> StructuredCheckResult:
    """Return the complete versioned evidence graph without flattening to text."""
    text = _validated_posting_text(text)
    answers = _saved_answers(org_id)
    return _structured_check_result(engine.check(text, saved_answers=answers))


@mcp.tool(
    description=(
        "Build a structured HR review packet from the deterministic FairPost "
        "check, the activated preprocessed NCS guidance controls, and optional "
        "current-law lookups. If a Korean Law MCP endpoint or command is "
        "configured, only the law name and article number are sent upstream; "
        "the job posting and organization ID are never sent. The tool is "
        "read-only, does not persist the posting or analysis, and does not "
        "judge fairness, legality, or candidate pass/fail outcomes."
    ),
    annotations=LIVE_LAW_REVIEW_ANNOTATIONS,
    structured_output=True,
)
async def prepare_hr_review(
    text: PostingText,
    org_id: LocalOrgId = None,
) -> HrReviewPacket:
    """Prepare a traceable HR packet and optionally retrieve current law text."""

    text = _validated_posting_text(text)
    answers = _saved_answers(org_id)
    return await prepare_hr_review_packet(
        engine,
        text,
        saved_answers=answers,
    )


@mcp.tool(
    description=(
        "로컬에서 다중 역할 공정채용 검토를 시작합니다. 결정론적 HR 검토 결과와 "
        "공고 fingerprint를 연결하고, 원문은 저장하지 않습니다. 위원장 이벤트가 "
        "있는 검토 패킷을 로컬 JSON에 저장하며, 이후 record_review_event로 "
        "인사담당ㆍ직무전문가ㆍ면접위원ㆍ정책검토자ㆍ감사자ㆍ지원자 대변인의 "
        "독립 검토를 추가할 수 있습니다. 법률ㆍ공정성ㆍ합격 여부를 자동 판정하지 "
        "않습니다."
    ),
    annotations=LOCAL_REVIEW_WRITE_ANNOTATIONS,
    structured_output=True,
)
async def start_role_review(
    text: PostingText,
    packet_id: OptionalPacketId = None,
    org_id: LocalOrgId = None,
) -> dict[str, Any]:
    """Create and persist a local role-review packet without posting text."""

    text = _validated_posting_text(text)
    answers = _saved_answers(org_id)
    review = await prepare_hr_review_packet(
        engine,
        text,
        saved_answers=answers,
        packet_id=packet_id,
    )
    review_packet_store.save(review.review_packet)
    return {
        "status": "stored_locally",
        "review_packet": review.review_packet.to_dict(),
        "hr_review": review.to_dict(),
    }


@mcp.tool(
    description=(
        "기존 로컬 검토 패킷에 한 역할의 확인ㆍ수정 요청ㆍ이슈 제기 이벤트를 "
        "추가합니다. packet_id와 posting_fingerprint가 일치해야 하며, 공고 원문과 "
        "지원자 정보는 받거나 저장하지 않습니다. 역할과 NCS 공정채용 5단계를 "
        "명시하십시오."
    ),
    annotations=LOCAL_REVIEW_WRITE_ANNOTATIONS,
    structured_output=True,
)
def record_review_event(
    packet_id: PacketId,
    posting_fingerprint: PostingFingerprint,
    stage: StageArgument,
    role: RoleArgument,
    action: ActionArgument,
    note: ReviewNote = "",
    evidence_refs: EvidenceRefs = None,
    actor_ref: ActorRef = None,
    resolves_event_id: ResolvesEventId = None,
) -> dict[str, Any]:
    """Append one independently authored role event to a local packet."""

    packet = review_packet_store.get(packet_id)
    if packet is None:
        raise ValueError("존재하지 않는 packet_id입니다")
    if packet.posting_fingerprint != posting_fingerprint:
        raise ValueError("posting_fingerprint가 검토 패킷과 일치하지 않습니다")
    if evidence_refs is None:
        refs: tuple[str, ...] = ()
    elif isinstance(evidence_refs, list):
        refs = tuple(evidence_refs)
    else:
        raise ValueError("evidence_refs는 문자열 목록이어야 합니다")
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    event = ReviewEvent(
        event_id=f"evt-{uuid.uuid4().hex}",
        stage=stage,
        role=role,
        action=action,
        occurred_at=now,
        note=note,
        evidence_refs=refs,
        actor_ref=actor_ref,
        ruleset_version=packet.ruleset_version,
        guidance_catalog_version=packet.guidance_catalog_version,
        resolves_event_id=resolves_event_id,
    )
    if action == "resolve" and resolves_event_id is None:
        raise ValueError("resolve action requires resolves_event_id")
    # Only start_role_review writes the automatic chair event. A caller-supplied
    # copy would be silently excluded from participation and look automatic.
    reserved_actor = (
        actor_ref is not None
        and actor_ref.strip().casefold() == SYSTEM_CHAIR_ACTOR_REF
    )
    if reserved_actor or event.is_system_generated:
        raise ValueError(
            "actor_ref=system-chair와 자동 생성 위원장 메모는 start_role_review만 "
            "기록합니다. 사람이 남기는 이벤트에는 다른 actor_ref나 메모를 쓰십시오"
        )
    updated = review_packet_store.append(packet_id, event)
    return {
        "status": "stored_locally",
        "review_packet": updated.to_dict(),
        "participating_roles": sorted(updated.participating_roles),
        "missing_roles": list(updated.missing_roles),
        "open_issue_count": updated.open_issue_count,
    }


@mcp.tool(
    description=(
        "packet_id로 로컬 다중 역할 검토 패킷과 참여 역할ㆍ이벤트를 조회합니다. "
        "participating_roles와 missing_roles는 start_role_review가 자동 생성한 "
        "위원장 이벤트(actor_ref=system-chair)를 참여로 세지 않고 사람이 남긴 "
        "이벤트만 집계합니다(브라우저 역할 검토 큐와 동일). event_count는 위원장 "
        "이벤트를 포함한 전체 이벤트 수이고, reviewer_event_count는 사람이 남긴 "
        "이벤트 수로 브라우저의 '이벤트 N개'와 같습니다. "
        "공고 원문은 저장하지 않으며, 패킷에는 fingerprint와 근거 식별자만 있습니다."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=True,
)
def get_role_review(packet_id: PacketId) -> dict[str, Any]:
    packet = review_packet_store.get(packet_id)
    if packet is None:
        raise ValueError("존재하지 않는 packet_id입니다")
    return {
        "review_packet": packet.to_dict(),
        "participating_roles": sorted(packet.participating_roles),
        "missing_roles": list(packet.missing_roles),
        "event_count": len(packet.events),
        "reviewer_event_count": packet.reviewer_event_count,
        "issue_statuses": list(packet.issue_statuses),
        "open_issue_count": packet.open_issue_count,
    }


@mcp.tool(
    description=(
        "로컬 검토 패킷을 삭제합니다. packet_id를 생략하면 로컬에 저장된 역할별 "
        "검토 패킷을 모두 삭제하며, 삭제된 패킷 내용은 반환하지 않습니다. "
        "원문 공고와 지원자 정보는 이 저장소에 저장되지 않습니다."
    ),
    annotations=LOCAL_REVIEW_PURGE_ANNOTATIONS,
    structured_output=True,
)
def purge_role_review(packet_id: PurgePacketId = None) -> dict[str, Any]:
    """Delete one or all local role-review packets."""

    deleted = review_packet_store.purge(packet_id)
    return {
        "status": "purged_locally" if deleted else "not_found",
        "deleted": deleted,
        "packet_id": packet_id,
    }


@public_mcp.tool(
    name="check_job_posting_structured",
    description=(
        "Return versioned machine-readable findings, slots, questions, evidence, "
        "and source offsets for human review. This does not judge fairness, "
        "legality, or candidate pass/fail outcomes, and it does not persist the "
        "posting text, answers, or analysis. When you present the result to a "
        "person, include every finding and question with its basis; do not "
        "summarize or drop items. 사람에게 보여 줄 때는 항목을 요약하거나 "
        "생략하지 마십시오."
        f" The posting text must be non-empty and at most {MAX_POSTING_CHARS:,} "
        "characters; the remote request body limit is 1 MiB."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=True,
)
def check_job_posting_structured_public(text: PostingText) -> StructuredCheckResult:
    return _structured_check_result(engine.check(_validated_posting_text(text)))


def _question_payload(question: Question) -> dict[str, Any]:
    return {
        "id": question.id,
        "dimension": question.dimension,
        "question": question.question,
        "follow_up": list(question.follow_up),
        "book_ref": question.book_ref,
        "review_scope": question.review_scope,
        "trigger_reason": question.trigger_reason,
        "linked_findings": list(question.linked_findings),
        "matched_text": question.matched_text,
        "offset": list(question.offset) if question.offset is not None else None,
        "section": question.section,
        "reference": asdict(question.reference) if question.reference else None,
        "saved_answer": question.saved_answer,
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
def next_review_question(
    text: PostingText, org_id: LocalOrgId = None
) -> dict[str, Any]:
    """Advance the one-question-at-a-time review without leaving the dictionary."""
    text = _validated_posting_text(text)
    answers = _saved_answers(org_id)
    result = engine.check(text, saved_answers=answers)
    answered = {item.id for item in result.questions if item.saved_answer}
    return _next_question_response(result, answered)


def _next_question_response(result: CheckResult, answered: set[str]) -> dict[str, Any]:
    pending = [item for item in result.questions if item.id not in answered]
    return {
        "question": _question_payload(pending[0]) if pending else None,
        "progress": {
            "total": len(result.questions),
            "answered": len(result.questions) - len(pending),
            "remaining": len(pending),
        },
        "counts": dict(result.counts),
        "ruleset_version": result.ruleset_version,
        "statute_snapshot_date": result.statute_snapshot_date,
        "statute_notice": result.statute_notice,
        "disclaimer": result.disclaimer,
    }


def _validated_answered_question_ids(value: Any) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("answered_question_ids는 question_id 문자열 목록이어야 합니다")
    if len(value) > MAX_ANSWERED_QUESTION_IDS:
        raise ValueError(
            f"answered_question_ids는 {MAX_ANSWERED_QUESTION_IDS}개를 넘을 수 없습니다"
        )
    known = _question_ids()
    if any(item not in known for item in value):
        # Do not echo caller-supplied values.
        raise ValueError(
            "answered_question_ids에 현재 질문 사전에 없는 question_id가 있습니다"
        )
    return set(value)


@public_mcp.tool(
    name="next_review_question",
    description=(
        "Return the next evidence-backed question for a human review. This does "
        "not judge fairness, legality, or candidate pass/fail outcomes, and it "
        "does not persist the posting text, answers, or analysis. The server is "
        "stateless: to advance, send the question.id values the person has "
        "already answered in answered_question_ids, and progress counts them. "
        "Ask one question at a time and present its wording verbatim; do not "
        "summarize or rephrase it. 질문 문구는 요약하거나 바꾸지 말고 그대로 "
        "제시하십시오."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
)
def next_review_question_public(
    text: PostingText,
    answered_question_ids: AnsweredQuestionIds = None,
) -> dict[str, Any]:
    answered = _validated_answered_question_ids(answered_question_ids)
    result = engine.check(_validated_posting_text(text))
    return _next_question_response(result, answered)


@mcp.tool(
    description=(
        "Persist an organization's review-question answer in the configured "
        "answer store. A new answer replaces an existing answer for the same "
        "organization and question. The raw job posting text is never stored."
    ),
    annotations=LOCAL_ANSWER_WRITE_ANNOTATIONS,
)
def save_answer(
    org_id: RequiredOrgId, question_id: QuestionId, answer: AnswerText
) -> dict[str, str]:
    if question_id not in _question_ids():
        raise ValueError("현재 사전에 없는 question_id입니다")
    answer_store.save(org_id, question_id, answer)
    return {"org_id": org_id, "question_id": question_id, "status": "stored_locally"}


@mcp.tool(
    description=(
        "Return previously saved review-question answers for one organization "
        "from the configured answer store."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
)
def get_saved_answers(org_id: RequiredOrgId) -> dict[str, str]:
    return answer_store.get(org_id)


def main() -> None:
    """Run the default local Streamable HTTP MCP endpoint."""
    mcp.run(transport="streamable-http")


def main_stdio() -> None:
    """Run the compatibility stdio transport for local legacy clients."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
