from __future__ import annotations

import json
import locale
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
                f"HTTP MCP 서버가 조기 종료했습니다.\nstdout={stdout}\nstderr={stderr}"
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
                    "save_answer",
                    "get_saved_answers",
                }
                check_tool = next(
                    tool for tool in tools.tools if tool.name == "check_job_posting"
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
                assert save_tool.annotations.destructiveHint is False
                assert set(check_tool.outputSchema["properties"]) == {
                    "findings",
                    "slots",
                    "questions",
                    "counts",
                    "ruleset_version",
                    "statute_snapshot_date",
                    "statute_notice",
                    "disclaimer",
                }

                check = await session.call_tool(
                    "check_job_posting",
                    {"text": "청년인턴 채용", "org_id": "org-protocol"},
                )
                assert check.isError is False
                assert check.structuredContent is not None
                assert check.structuredContent["ruleset_version"]
                assert check.structuredContent["statute_snapshot_date"] == "2026-07-26"
                assert "공식 대조 스냅샷" in check.structuredContent["statute_notice"]
                assert check.structuredContent["counts"]["findings"] == 0

                saved = await session.call_tool(
                    "save_answer",
                    {
                        "org_id": "org-protocol",
                        "question_id": "Q-INFO-001",
                        "answer": "인사팀 이메일로 접수합니다.",
                    },
                )
                assert saved.isError is False

                loaded = await session.call_tool(
                    "get_saved_answers",
                    {"org_id": "org-protocol"},
                )
                assert loaded.isError is False
                assert loaded.structuredContent == {
                    "Q-INFO-001": "인사팀 이메일로 접수합니다."
                }

        persisted = json.loads(answers_path.read_text(encoding="utf-8"))
        assert persisted == {
            "org-protocol": {
                "Q-INFO-001": "인사팀 이메일로 접수합니다."
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
                        "save_answer",
                        "get_saved_answers",
                    }
                    check_tool = next(
                        tool
                        for tool in tools.tools
                        if tool.name == "check_job_posting"
                    )
                    assert "Finding" in check_tool.outputSchema["$defs"]
                    assert "SlotStatus" in check_tool.outputSchema["$defs"]
                    assert "Question" in check_tool.outputSchema["$defs"]
                    question_properties = check_tool.outputSchema["$defs"][
                        "Question"
                    ]["properties"]
                    assert {
                        "review_scope",
                        "matched_text",
                        "offset",
                        "section",
                        "reference",
                    } <= set(question_properties)

                    check = await session.call_tool(
                        "check_job_posting",
                        {"text": "청년인턴 채용", "org_id": "org-http"},
                    )
                    assert check.isError is False
                    assert check.structuredContent is not None
                    assert check.structuredContent["counts"]["findings"] == 0

                    finding_check = await session.call_tool(
                        "check_job_posting",
                        {"text": "남성만 지원 가능"},
                    )
                    basis = finding_check.structuredContent["findings"][0]["basis"]
                    assert basis["effective_date"] <= basis["snapshot_date"]

                    question_check = await session.call_tool(
                        "check_job_posting",
                        {"text": "지원자격\n세례교인에 한함"},
                    )
                    question = next(
                        item
                        for item in question_check.structuredContent["questions"]
                        if item["id"] == "Q-DIST-012"
                    )
                    assert question["matched_text"] == "세례"
                    assert question["review_scope"] == "posting"
                    assert question["reference"]["source_url"].startswith(
                        "https://www.ncs.go.kr/"
                    )

                    saved = await session.call_tool(
                        "save_answer",
                        {
                            "org_id": "org-http",
                            "question_id": "Q-PROC-001",
                            "answer": "공고에 단계별 기준을 공개합니다.",
                        },
                    )
                    assert saved.isError is False

                    loaded = await session.call_tool(
                        "get_saved_answers",
                        {"org_id": "org-http"},
                    )
                    assert loaded.structuredContent == {
                        "Q-PROC-001": "공고에 단계별 기준을 공개합니다."
                    }

                    rejected = await session.call_tool(
                        "save_answer",
                        {
                            "org_id": "org-http",
                            "question_id": "Q-NOT-FOUND",
                            "answer": "저장되면 안 됩니다.",
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
    assert persisted["org-http"]["Q-PROC-001"] == "공고에 단계별 기준을 공개합니다."


def test_http_rejects_remote_binding_without_explicit_opt_in() -> None:
    environment = {
        **os.environ,
        "FAIRPOST_MCP_HOST": "0.0.0.0",
        "FAIRPOST_ALLOW_REMOTE": "0",
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
        encoding=locale.getpreferredencoding(False),
        errors="replace",
    )
    assert completed.returncode != 0
    assert "FAIRPOST_ALLOW_REMOTE=1" in completed.stderr


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

        health = httpx.get(f"http://127.0.0.1:{port}/api/health")
        assert health.status_code == 200
        assert health.json()["mcp_endpoint"] == "/api/mcp"
        assert health.json()["claude_readonly_mcp_endpoint"] == (
            "/api/claude-mcp"
        )
        assert health.json()["authentication"] == "bearer"
        assert health.json()["claude_readonly_authentication"] == "none"
        assert health.json()["answer_store"] == "unavailable"

        async def exercise() -> None:
            async with httpx.AsyncClient(
                headers={"Authorization": f"Bearer {token}"}
            ) as client:
                async with streamable_http_client(
                    f"http://127.0.0.1:{port}/api/mcp",
                    http_client=client,
                ) as (read, write, _session_id):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        assert {tool.name for tool in tools.tools} == {
                            "check_job_posting",
                            "save_answer",
                            "get_saved_answers",
                        }
                        checked = await session.call_tool(
                            "check_job_posting",
                            {"text": "남성만 지원 가능"},
                        )
                        assert checked.isError is False
                        assert checked.structuredContent["findings"][0]["id"] == (
                            "SEX-001"
                        )
                        unavailable = await session.call_tool(
                            "save_answer",
                            {
                                "org_id": "org-remote",
                                "question_id": "Q-INFO-001",
                                "answer": "인사팀에서 접수합니다.",
                            },
                        )
                        assert unavailable.isError is True

        anyio.run(exercise)

        async def exercise_claude_readonly() -> None:
            async with streamable_http_client(
                f"http://127.0.0.1:{port}/api/claude-mcp"
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
                    assert checked.structuredContent["findings"][0]["id"] == (
                        "SEX-001"
                    )

        anyio.run(exercise_claude_readonly)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
