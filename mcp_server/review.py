from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, AsyncIterator, Literal
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from core import FairpostEngine
from core.guidance import GuidanceContext, load_guidance_catalog
from core.schema import CheckResult, Finding


MAX_UPSTREAM_TEXT_CHARS = 100_000
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_STDIO_BASE_ENV = frozenset(
    {
        "APPDATA",
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SHELL",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
)


@dataclass(frozen=True)
class LawMcpRequest:
    search_tool: str
    search_arguments: dict[str, str]
    text_tool: str
    text_arguments_after_search: dict[str, str]
    posting_text_sent: Literal[False] = False


@dataclass(frozen=True)
class LawVerificationResult:
    finding_ids: list[str]
    law: str
    article: str
    statute_id: str
    snapshot_date: str
    snapshot_effective_date: str
    official_source_url: str | None
    official_id: str | None
    request: LawMcpRequest
    status: str
    checked_at: str | None
    current_law_id: str | None
    current_mst: str | None
    current_text: str | None
    response_truncated: bool
    note: str


@dataclass(frozen=True)
class HrReviewPacket:
    schema_version: Literal["fairpost-hr-review-v1"]
    check_schema_version: Literal["fairpost-structured-check-v1"]
    check: CheckResult
    law_mcp_transport: str
    law_verifications: list[LawVerificationResult]
    ncs_guidance: GuidanceContext
    review_notice: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LawMcpConfig:
    transport: Literal["disabled", "http", "stdio"]
    url: str | None = None
    bearer_token: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: str | None = None
    env_names: tuple[str, ...] = ()
    search_tool: str = "search_law"
    text_tool: str = "get_law_text"

    @classmethod
    def from_environment(cls) -> LawMcpConfig:
        url = os.environ.get("FAIRPOST_KOREAN_LAW_MCP_URL", "").strip()
        command = os.environ.get("FAIRPOST_KOREAN_LAW_MCP_COMMAND", "").strip()
        if url and command:
            raise ValueError("Korean Law MCP transport must be configured once")

        search_tool = _validated_tool_name(
            os.environ.get("FAIRPOST_KOREAN_LAW_SEARCH_TOOL", "search_law")
        )
        text_tool = _validated_tool_name(
            os.environ.get("FAIRPOST_KOREAN_LAW_TEXT_TOOL", "get_law_text")
        )
        if url:
            return cls(
                transport="http",
                url=_validated_mcp_url(url),
                bearer_token=os.environ.get("FAIRPOST_KOREAN_LAW_MCP_TOKEN") or None,
                search_tool=search_tool,
                text_tool=text_tool,
            )
        if not command:
            return cls(
                transport="disabled",
                search_tool=search_tool,
                text_tool=text_tool,
            )

        raw_args = os.environ.get("FAIRPOST_KOREAN_LAW_MCP_ARGS", "[]")
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            raise ValueError("Korean Law MCP args must be a JSON array") from exc
        if (
            not isinstance(args, list)
            or len(args) > 64
            or any(
                not isinstance(value, str) or "\0" in value or len(value) > 4096
                for value in args
            )
        ):
            raise ValueError("Korean Law MCP args must be a bounded string array")
        if "\0" in command or len(command) > 4096:
            raise ValueError("Korean Law MCP command is invalid")
        cwd = os.environ.get("FAIRPOST_KOREAN_LAW_MCP_CWD", "").strip() or None
        raw_env_names = os.environ.get(
            "FAIRPOST_KOREAN_LAW_MCP_ENV_ALLOWLIST", ""
        )
        env_names = tuple(
            dict.fromkeys(
                name.strip() for name in raw_env_names.split(",") if name.strip()
            )
        )
        if len(env_names) > 32 or any(
            not _ENV_NAME.fullmatch(name) for name in env_names
        ):
            raise ValueError("Korean Law MCP environment allowlist is invalid")
        return cls(
            transport="stdio",
            command=command,
            args=tuple(args),
            cwd=cwd,
            env_names=env_names,
            search_tool=search_tool,
            text_tool=text_tool,
        )


def _validated_tool_name(value: str) -> str:
    name = value.strip()
    if not _TOOL_NAME.fullmatch(name):
        raise ValueError("Korean Law MCP tool name is invalid")
    return name


def _validated_mcp_url(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold()
    loopback = host in {"127.0.0.1", "::1", "localhost"}
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("Korean Law MCP URL must be HTTP(S)")
    if parsed.scheme == "http" and not loopback:
        raise ValueError("Remote Korean Law MCP URL must use HTTPS")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Korean Law MCP URL must not contain credentials or fragments")
    return value


@dataclass(frozen=True)
class _LawReference:
    finding_ids: list[str]
    law: str
    article: str
    statute_id: str
    snapshot_date: str
    snapshot_effective_date: str
    official_source_url: str | None
    official_id: str | None


def _references(
    findings: list[Finding], statutes: dict[str, Any]
) -> list[_LawReference]:
    grouped: dict[tuple[str, str], list[Finding]] = {}
    for finding in findings:
        if not finding.basis.law or not finding.basis.article:
            continue
        grouped.setdefault(
            (finding.basis.law, finding.basis.article), []
        ).append(finding)

    references: list[_LawReference] = []
    for (_law, _article), linked in grouped.items():
        first = linked[0]
        statute = statutes.get(first.basis.statute_id or "", {})
        references.append(
            _LawReference(
                finding_ids=sorted(item.id for item in linked),
                law=first.basis.law or "",
                article=first.basis.article or "",
                statute_id=first.basis.statute_id or "",
                snapshot_date=first.basis.snapshot_date or "",
                snapshot_effective_date=first.basis.effective_date or "",
                official_source_url=statute.get("source_url"),
                official_id=(
                    str(statute["official_id"])
                    if statute.get("official_id") is not None
                    else None
                ),
            )
        )
    return sorted(references, key=lambda item: (item.law, item.article))


def _request(reference: _LawReference, config: LawMcpConfig) -> LawMcpRequest:
    return LawMcpRequest(
        search_tool=config.search_tool,
        search_arguments={"query": reference.law},
        text_tool=config.text_tool,
        text_arguments_after_search={
            "mst": "<search_law의 현행 MST>",
            "jo": reference.article,
        },
    )


def _not_run(
    reference: _LawReference,
    config: LawMcpConfig,
    *,
    status: str,
    note: str,
) -> LawVerificationResult:
    return LawVerificationResult(
        finding_ids=reference.finding_ids,
        law=reference.law,
        article=reference.article,
        statute_id=reference.statute_id,
        snapshot_date=reference.snapshot_date,
        snapshot_effective_date=reference.snapshot_effective_date,
        official_source_url=reference.official_source_url,
        official_id=reference.official_id,
        request=_request(reference, config),
        status=status,
        checked_at=None,
        current_law_id=None,
        current_mst=None,
        current_text=None,
        response_truncated=False,
        note=note,
    )


def _result_text(result: Any) -> str:
    values = [
        str(block.text)
        for block in getattr(result, "content", [])
        if getattr(block, "type", None) == "text" and getattr(block, "text", None)
    ]
    return "\n".join(values)


def _current_identifiers(text: str, law: str) -> tuple[str, str] | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        candidate = re.sub(r"^\s*\d+\.\s*", "", line).strip()
        if candidate != f"{law} [현행]":
            continue
        block = []
        for detail in lines[index + 1 : index + 10]:
            if re.match(r"^\s*\d+\.\s", detail):
                break
            block.append(detail)
        window = "\n".join(block)
        law_id = re.search(r"법령ID\s*:\s*([0-9]+)", window)
        mst = re.search(r"MST\s*:\s*([0-9]+)", window)
        if law_id and mst:
            return law_id.group(1), mst.group(1)
    return None


def _retrieved_article(text: str, law: str, article: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return re.sub(r"\s+", "", law) in compact and re.sub(
        r"\s+", "", article
    ) in compact


class LiveLawVerifier:
    def __init__(self, config: LawMcpConfig) -> None:
        self.config = config

    @classmethod
    def from_environment(cls) -> LiveLawVerifier:
        return cls(LawMcpConfig.from_environment())

    async def verify(
        self, findings: list[Finding], statutes: dict[str, Any]
    ) -> list[LawVerificationResult]:
        references = _references(findings, statutes)
        if not references:
            return []
        if self.config.transport == "disabled":
            return [
                _not_run(
                    reference,
                    self.config,
                    status="not_configured",
                    note=(
                        "Korean Law MCP가 설정되지 않아 저장된 공식 스냅샷만 사용했습니다."
                    ),
                )
                for reference in references
            ]
        try:
            async with self._session() as session:
                return await self._verify_with_session(session, references)
        except Exception as exc:
            failure = type(exc).__name__
            return [
                _not_run(
                    reference,
                    self.config,
                    status="upstream_unavailable",
                    note=f"Korean Law MCP 조회를 완료하지 못했습니다({failure}).",
                )
                for reference in references
            ]

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ClientSession]:
        if self.config.transport == "http":
            headers = {}
            if self.config.bearer_token:
                headers["Authorization"] = f"Bearer {self.config.bearer_token}"
            timeout = httpx.Timeout(20.0, connect=10.0)
            async with httpx.AsyncClient(
                headers=headers,
                timeout=timeout,
                follow_redirects=False,
            ) as client:
                async with streamable_http_client(
                    self.config.url or "", http_client=client
                ) as (read, write, _session_id):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        yield session
            return

        stdio_environment = {
            name: value
            for name in _STDIO_BASE_ENV
            if (value := os.environ.get(name)) is not None
        }
        stdio_environment.update(
            {
                name: os.environ[name]
                for name in self.config.env_names
                if name in os.environ
            }
        )
        parameters = StdioServerParameters(
            command=self.config.command or "",
            args=list(self.config.args),
            cwd=Path(self.config.cwd) if self.config.cwd else None,
            env=stdio_environment,
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def _verify_with_session(
        self,
        session: Any,
        references: list[_LawReference],
    ) -> list[LawVerificationResult]:
        listed = await session.list_tools()
        tool_names = {tool.name for tool in listed.tools}
        required = {self.config.search_tool, self.config.text_tool}
        if not required.issubset(tool_names):
            return [
                _not_run(
                    reference,
                    self.config,
                    status="tool_not_available",
                    note="Korean Law MCP에 필요한 법령 검색ㆍ조문 조회 도구가 없습니다.",
                )
                for reference in references
            ]

        results: list[LawVerificationResult] = []
        for reference in references:
            checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            search_result = await session.call_tool(
                self.config.search_tool, {"query": reference.law}
            )
            if getattr(search_result, "isError", False):
                results.append(
                    _not_run(
                        reference,
                        self.config,
                        status="search_failed",
                        note="현행 법령 식별자 검색이 실패했습니다.",
                    )
                )
                continue
            identifiers = _current_identifiers(
                _result_text(search_result), reference.law
            )
            if identifiers is None:
                results.append(
                    _not_run(
                        reference,
                        self.config,
                        status="current_law_not_resolved",
                        note="검색 결과에서 정확히 일치하는 현행 법령을 식별하지 못했습니다.",
                    )
                )
                continue
            law_id, mst = identifiers
            article_result = await session.call_tool(
                self.config.text_tool,
                {"mst": mst, "jo": reference.article},
            )
            text = _result_text(article_result)
            if getattr(article_result, "isError", False) or not _retrieved_article(
                text, reference.law, reference.article
            ):
                results.append(
                    LawVerificationResult(
                        finding_ids=reference.finding_ids,
                        law=reference.law,
                        article=reference.article,
                        statute_id=reference.statute_id,
                        snapshot_date=reference.snapshot_date,
                        snapshot_effective_date=reference.snapshot_effective_date,
                        official_source_url=reference.official_source_url,
                        official_id=reference.official_id,
                        request=_request(reference, self.config),
                        status="article_not_resolved",
                        checked_at=checked_at,
                        current_law_id=law_id,
                        current_mst=mst,
                        current_text=None,
                        response_truncated=False,
                        note="현행 법령은 찾았지만 요청 조문 원문을 확인하지 못했습니다.",
                    )
                )
                continue
            truncated = len(text) > MAX_UPSTREAM_TEXT_CHARS
            results.append(
                LawVerificationResult(
                    finding_ids=reference.finding_ids,
                    law=reference.law,
                    article=reference.article,
                    statute_id=reference.statute_id,
                    snapshot_date=reference.snapshot_date,
                    snapshot_effective_date=reference.snapshot_effective_date,
                    official_source_url=reference.official_source_url,
                    official_id=reference.official_id,
                    request=_request(reference, self.config),
                    status="current_article_retrieved",
                    checked_at=checked_at,
                    current_law_id=law_id,
                    current_mst=mst,
                    current_text=text[:MAX_UPSTREAM_TEXT_CHARS],
                    response_truncated=truncated,
                    note=(
                        "현행 조문을 조회했습니다. 스냅샷과의 법적 동일성ㆍ적용 여부는 "
                        "자동 판정하지 않으며 담당자 또는 법률 전문가가 확인해야 합니다."
                    ),
                )
            )
        return results


async def prepare_hr_review_packet(
    engine: FairpostEngine,
    text: str,
    *,
    saved_answers: dict[str, str] | None = None,
    verifier: LiveLawVerifier | None = None,
) -> HrReviewPacket:
    result = engine.check(text, saved_answers=saved_answers)
    question_ids = {rule["id"] for rule in engine.ruleset.rules if rule["layer"] == "question"}
    guidance = load_guidance_catalog(question_ids=question_ids).context_for_questions(
        {question.id for question in result.questions}
    )
    active_verifier = verifier or LiveLawVerifier.from_environment()
    verifications = await active_verifier.verify(
        result.findings, engine.ruleset.statutes
    )
    return HrReviewPacket(
        schema_version="fairpost-hr-review-v1",
        check_schema_version="fairpost-structured-check-v1",
        check=result,
        law_mcp_transport=active_verifier.config.transport,
        law_verifications=verifications,
        ncs_guidance=guidance,
        review_notice=(
            "법령 조회 결과와 NCS 자료는 사람의 채용 검토를 지원하는 근거입니다. "
            "위법ㆍ공정성 또는 지원자 합격 여부를 자동 판정하지 않습니다."
        ),
    )
