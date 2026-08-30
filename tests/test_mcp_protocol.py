from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import anyio
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


ROOT = Path(__file__).resolve().parents[1]


def _unused_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_port(process: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                "HTTP MCP 서버가 시작 전에 종료되었습니다.\n"
                f"stdout={stdout}\nstderr={stderr}"
            )
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.1)
            if connection.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise AssertionError("HTTP MCP 서버 시작 시간이 초과되었습니다")


def test_stdio_mcp_protocol_lists_and_calls_all_tools(tmp_path: Path) -> None:
    answers_path = tmp_path / "answers.json"

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-c",
                "from mcp_server.server import main_stdio; main_stdio()",
            ],
            cwd=str(ROOT),
            env={"FAIRPOST_ANSWERS_PATH": str(answers_path)},
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "fairpost"

                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    "check_job_posting",
                    "check_job_posting_structured",
                    "next_review_question",
                    "save_answer",
                    "get_saved_answers",
                }
                check_tool = next(
                    tool for tool in tools.tools if tool.name == "check_job_posting"
                )
                structured_tool = next(
                    tool
                    for tool in tools.tools
                    if tool.name == "check_job_posting_structured"
                )
                save_tool = next(
                    tool for tool in tools.tools if tool.name == "save_answer"
                )
                get_tool = next(
                    tool for tool in tools.tools if tool.name == "get_saved_answers"
                )
                assert check_tool.annotations is not None
                assert check_tool.annotations.readOnlyHint is True
                assert check_tool.annotations.destructiveHint is False
                assert get_tool.annotations is not None
                assert get_tool.annotations.readOnlyHint is True
                assert save_tool.annotations is not None
                assert save_tool.annotations.readOnlyHint is False
                assert save_tool.annotations.destructiveHint is True
                assert "replaces an existing answer" in save_tool.description
                assert check_tool.outputSchema is None
                assert structured_tool.outputSchema is not None
                assert set(structured_tool.outputSchema["required"]) == {
                    "schema_version",
                    "findings",
                    "slots",
                    "questions",
                    "counts",
                    "ruleset_version",
                    "statute_snapshot_date",
                    "statute_notice",
                    "disclaimer",
                }
                assert structured_tool.outputSchema["properties"]["schema_version"][
                    "const"
                ] == "fairpost-structured-check-v1"
                finding_schema = structured_tool.outputSchema["$defs"]["Finding"]
                assert {"id", "matched_text", "offset", "basis", "book_ref"} <= set(
                    finding_schema["required"]
                )
                question_schema = structured_tool.outputSchema["$defs"]["Question"]
                assert {"id", "book_ref", "review_scope"} <= set(
                    question_schema["required"]
                )
                assert structured_tool.annotations is not None
                assert structured_tool.annotations.readOnlyHint is True

                check = await session.call_tool(
                    "check_job_posting",
                    {"text": "청년인턴 채용", "org_id": "org-protocol"},
                )
                assert check.isError is False
                assert check.structuredContent is None
                check_text = check.content[0].text
                assert "발견 0건" in check_text
                assert "2026-07-26" in check_text
                assert "공식 대조 스냅샷" in check_text

                structured = await session.call_tool(
                    "check_job_posting_structured",
                    {"text": "여성만 지원 가능", "org_id": "org-protocol"},
                )
                assert structured.isError is False
                assert structured.structuredContent is not None
                assert structured.structuredContent["schema_version"] == (
                    "fairpost-structured-check-v1"
                )
                assert structured.structuredContent["findings"][0]["id"] == (
                    "SEX-001"
                )
                assert structured.structuredContent["disclaimer"].startswith(
                    "이 결과는 점검 참고자료"
                )

                saved = await session.call_tool(
                    "save_answer",
                    {
                        "org_id": "org-protocol",
                        "question_id": "Q-INFO-001",
                        "answer": "채용서류 반환 절차를 안내합니다.",
                    },
                )
                assert saved.isError is False

                loaded = await session.call_tool(
                    "get_saved_answers",
                    {"org_id": "org-protocol"},
                )
                assert loaded.isError is False
                assert loaded.structuredContent == {
                    "Q-INFO-001": "채용서류 반환 절차를 안내합니다."
                }

        persisted = json.loads(answers_path.read_text(encoding="utf-8"))
        assert persisted == {
            "org-protocol": {
                "Q-INFO-001": "채용서류 반환 절차를 안내합니다."
            }
        }

    anyio.run(exercise)


def test_streamable_http_is_default_and_calls_all_tools(tmp_path: Path) -> None:
    answers_path = tmp_path / "http-answers.json"
    port = _unused_local_port()
    environment = {
        **os.environ,
        "FAIRPOST_ANSWERS_PATH": str(answers_path),
        "FAIRPOST_MCP_HOST": "127.0.0.1",
        "FAIRPOST_MCP_PORT": str(port),
        "PYTHONIOENCODING": "utf-8",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp_server.server"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        _wait_for_port(process, port)

        async def exercise() -> None:
            async with streamable_http_client(
                f"http://127.0.0.1:{port}/mcp"
            ) as (read, write, _session_id):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    assert initialized.serverInfo.name == "fairpost"

                    tools = await session.list_tools()
                    assert {tool.name for tool in tools.tools} == {
                        "check_job_posting",
                        "check_job_posting_structured",
                        "next_review_question",
                        "save_answer",
                        "get_saved_answers",
                    }
                    check_tool = next(
                        tool
                        for tool in tools.tools
                        if tool.name == "check_job_posting"
                    )
                    assert check_tool.outputSchema is None
                    structured_tool = next(
                        tool
                        for tool in tools.tools
                        if tool.name == "check_job_posting_structured"
                    )
                    assert structured_tool.outputSchema is not None

                    check = await session.call_tool(
                        "check_job_posting",
                        {"text": "청년인턴 채용", "org_id": "org-http"},
                    )
                    assert check.isError is False
                    assert check.structuredContent is None
                    assert "발견 0건" in check.content[0].text

                    finding_check = await session.call_tool(
                        "check_job_posting",
                        {"text": "남성만 지원 가능"},
                    )
                    finding_text = finding_check.content[0].text
                    assert "SEX-001" in finding_text
                    assert "남녀고용평등" in finding_text

                    structured = await session.call_tool(
                        "check_job_posting_structured",
                        {"text": "남성만 지원 가능"},
                    )
                    assert structured.isError is False
                    assert structured.structuredContent is not None
                    assert structured.structuredContent["findings"][0]["id"] == (
                        "SEX-001"
                    )

                    question_check = await session.call_tool(
                        "check_job_posting",
                        {"text": "지원자격\n세례교인에 한함"},
                    )
                    question_text = question_check.content[0].text
                    assert "Q-DIST-012" in question_text
                    assert "세례교인" in question_text

                    saved = await session.call_tool(
                        "save_answer",
                        {
                            "org_id": "org-http",
                            "question_id": "Q-PROC-001",
                            "answer": "평가기준을 사전에 안내합니다.",
                        },
                    )
                    assert saved.isError is False

                    loaded = await session.call_tool(
                        "get_saved_answers",
                        {"org_id": "org-http"},
                    )
                    assert loaded.structuredContent == {
                        "Q-PROC-001": "평가기준을 사전에 안내합니다."
                    }

                    rejected = await session.call_tool(
                        "save_answer",
                        {
                            "org_id": "org-http",
                            "question_id": "Q-NOT-FOUND",
                            "answer": "존재하지 않는 질문입니다.",
                        },
                    )
                    assert rejected.isError is True

        anyio.run(exercise)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    persisted = json.loads(answers_path.read_text(encoding="utf-8"))
    assert persisted["org-http"]["Q-PROC-001"] == "평가기준을 사전에 안내합니다."


def test_full_mcp_rejects_remote_binding_unconditionally() -> None:
    environment = {
        **os.environ,
        "FAIRPOST_MCP_HOST": "0.0.0.0",
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import mcp_server.server",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert completed.returncode != 0
    assert "전체 MCP는 루프백에서만" in completed.stderr


def test_vercel_environment_keeps_full_mcp_loopback_only() -> None:
    environment = {**os.environ, "VERCEL": "1", "PYTHONIOENCODING": "utf-8"}
    environment.pop("FAIRPOST_MCP_HOST", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import mcp_server.server as s; "
                "print(s.mcp.settings.host); print(s.public_mcp.settings.host)"
            ),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == ["127.0.0.1", "0.0.0.0"]


def test_remote_import_ignores_partial_storage_configuration() -> None:
    environment = {
        **os.environ,
        "VERCEL": "1",
        "UPSTASH_REDIS_REST_URL": "https://redis.example",
        "PYTHONIOENCODING": "utf-8",
    }
    for name in (
        "UPSTASH_REDIS_REST_TOKEN",
        "KV_REST_API_URL",
        "KV_REST_API_TOKEN",
        "FAIRPOST_MCP_HOST",
    ):
        environment.pop(name, None)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import mcp_server.remote as r; "
                "print(r.MCP_PATH); print('import-ok')"
            ),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines()[0] == "/api/mcp"


def test_vercel_asgi_entrypoint_requires_bearer_and_calls_mcp(
    tmp_path: Path,
) -> None:
    port = _unused_local_port()
    token = "test-vercel-bearer-token"
    environment = {
        **os.environ,
        "VERCEL": "1",
        "FAIRPOST_MCP_TOKEN": token,
        "PYTHONIOENCODING": "utf-8",
    }
    for name in (
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
        "KV_REST_API_URL",
        "KV_REST_API_TOKEN",
    ):
        environment.pop(name, None)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api.index:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        _wait_for_port(process, port)

        unauthorized = httpx.post(
            f"http://127.0.0.1:{port}/api/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert unauthorized.status_code == 401
        assert unauthorized.headers["www-authenticate"] == "Bearer"
        unauthorized_claude = httpx.post(
            f"http://127.0.0.1:{port}/api/claude-mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert unauthorized_claude.status_code == 401
        assert unauthorized_claude.headers["www-authenticate"] == "Bearer"

        health = httpx.get(f"http://127.0.0.1:{port}/api/health")
        assert health.status_code == 200
        assert health.json()["mcp_endpoint"] == "/api/mcp"
        assert health.json()["claude_readonly_mcp_endpoint"] == (
            "/api/claude-mcp"
        )
        assert health.json()["authentication"] == "bearer"
        assert health.json()["claude_readonly_authentication"] == "bearer"
        assert health.json()["answer_store"] == "disabled_on_remote_endpoint"
        assert health.json()["remote_tool_profile"] == "read_only"

        async def exercise() -> None:
            async with httpx.AsyncClient(
                headers={"Authorization": f"Bearer {token}"}
            ) as client:
                async with streamable_http_client(
                    f"http://127.0.0.1:{port}/api/mcp",
                    http_client=client,
                ) as (read, write, _session_id):
                    async with ClientSession(read, write) as session:
                        initialized = await session.initialize()
                        instructions = initialized.instructions or ""
                        assert "human review" in instructions
                        assert "do not judge fairness, legality" in instructions
                        assert "does not persist" in instructions
                        tools = await session.list_tools()
                        assert {tool.name for tool in tools.tools} == {
                            "check_job_posting",
                            "check_job_posting_structured",
                            "next_review_question",
                        }
                        for tool in tools.tools:
                            description = tool.description or ""
                            assert "human review" in description
                            assert "does not judge fairness, legality" in description
                            assert "does not persist" in description
                        checked = await session.call_tool(
                            "check_job_posting",
                            {"text": "남성만 지원 가능"},
                        )
                        assert checked.isError is False
                        assert checked.structuredContent is None
                        assert "SEX-001" in checked.content[0].text
                        structured = await session.call_tool(
                            "check_job_posting_structured",
                            {"text": "남성만 지원 가능"},
                        )
                        assert structured.isError is False
                        assert structured.structuredContent is not None
                        assert structured.structuredContent["schema_version"] == (
                            "fairpost-structured-check-v1"
                        )
                        assert structured.structuredContent["findings"][0]["id"] == (
                            "SEX-001"
                        )
                        denied_write = await session.call_tool(
                            "save_answer",
                            {
                                "org_id": "org-remote",
                                "question_id": "Q-INFO-001",
                                "answer": "저장되지 않아야 합니다.",
                            },
                        )
                        assert denied_write.isError is True
        anyio.run(exercise)

        async def exercise_claude_readonly() -> None:
            async with httpx.AsyncClient(
                headers={"Authorization": f"Bearer {token}"}
            ) as client:
                async with streamable_http_client(
                    f"http://127.0.0.1:{port}/api/claude-mcp",
                    http_client=client,
                ) as (read, write, _session_id):
                    async with ClientSession(read, write) as session:
                        initialized = await session.initialize()
                        assert initialized.serverInfo.name == "fairpost-readonly"
                        tools = await session.list_tools()
                        assert [tool.name for tool in tools.tools] == [
                            "check_job_posting"
                        ]
                        tool = tools.tools[0]
                        assert tool.annotations is not None
                        assert tool.annotations.readOnlyHint is True
                        assert tool.annotations.destructiveHint is False
                        assert tool.annotations.idempotentHint is True
                        assert tool.annotations.openWorldHint is False
                        assert set(tool.inputSchema["properties"]) == {"text"}

                        checked = await session.call_tool(
                            "check_job_posting",
                            {"text": "남성만 지원 가능"},
                        )
                        assert checked.isError is False
                        assert checked.structuredContent is None
                        assert "SEX-001" in checked.content[0].text

        anyio.run(exercise_claude_readonly)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_vercel_public_remote_exposes_only_read_only_analysis_tools() -> None:
    port = _unused_local_port()
    environment = {
        **os.environ,
        "VERCEL": "1",
        "FAIRPOST_ALLOW_PUBLIC_REMOTE": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    for name in (
        "FAIRPOST_MCP_TOKEN",
        "FAIRPOST_ALLOW_PUBLIC_CLAUDE_REMOTE",
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
        "KV_REST_API_URL",
        "KV_REST_API_TOKEN",
    ):
        environment.pop(name, None)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api.index:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        _wait_for_port(process, port)

        health = httpx.get(f"http://127.0.0.1:{port}/api/health")
        assert health.status_code == 200
        assert health.json()["authentication"] == "none"
        assert health.json()["claude_readonly_authentication"] == "disabled"
        assert health.json()["answer_store"] == "disabled_on_remote_endpoint"
        assert health.json()["remote_tool_profile"] == "read_only"
        disabled_claude = httpx.post(
            f"http://127.0.0.1:{port}/api/claude-mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        )
        assert disabled_claude.status_code == 503

        async def exercise() -> None:
            async with streamable_http_client(
                f"http://127.0.0.1:{port}/api/mcp"
            ) as (read, write, _session_id):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    assert initialized.serverInfo.name == "fairpost"
                    instructions = initialized.instructions or ""
                    assert "human review" in instructions
                    assert "do not judge fairness, legality" in instructions
                    assert "does not persist" in instructions
                    tools = await session.list_tools()
                    assert {tool.name for tool in tools.tools} == {
                        "check_job_posting",
                        "check_job_posting_structured",
                        "next_review_question",
                    }
                    for tool in tools.tools:
                        assert set(tool.inputSchema["properties"]) == {"text"}
                        description = tool.description or ""
                        assert "human review" in description
                        assert "does not judge fairness, legality" in description
                        assert "does not persist" in description
                    checked = await session.call_tool(
                        "check_job_posting",
                        {"text": "여성만 지원 가능"},
                    )
                    assert checked.isError is False
                    structured = await session.call_tool(
                        "check_job_posting_structured",
                        {"text": "여성만 지원 가능"},
                    )
                    assert structured.isError is False
                    assert structured.structuredContent is not None
                    assert structured.structuredContent["findings"][0]["id"] == (
                        "SEX-001"
                    )
                    next_question = await session.call_tool(
                        "next_review_question",
                        {"text": "여성만 지원 가능"},
                    )
                    assert next_question.isError is False

        anyio.run(exercise)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
