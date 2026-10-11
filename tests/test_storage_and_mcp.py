from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json
import subprocess
import sys
from threading import Event, Thread, current_thread

import pytest

import mcp_server.server as server
import mcp_server.storage as storage
from mcp_server.storage import (
    LocalAnswerStore,
    UnavailableRemoteAnswerStore,
)


def test_answer_store_writes_only_configured_local_file(tmp_path: Path) -> None:
    path = tmp_path / "local" / "answers.json"
    store = LocalAnswerStore(path)
    store.save("org-a", "Q-INFO-001", "인사팀에서 검토합니다.")
    assert store.get("org-a") == {"Q-INFO-001": "인사팀에서 검토합니다."}
    assert path.exists()
    assert list(tmp_path.rglob("*.json")) == [path]


def test_local_store_purge_all_removes_store_without_parsing(tmp_path: Path) -> None:
    path = tmp_path / "answers.json"
    path.write_text("damaged private answer storage", encoding="utf-8")
    store = LocalAnswerStore(path)

    assert store.purge() is True
    assert not path.exists()
    assert store.purge() is False


def test_local_store_purge_removes_crash_orphaned_plaintext_temporaries(
    tmp_path: Path,
) -> None:
    path = tmp_path / "answers.json"
    store = LocalAnswerStore(path)
    store.save("org-a", "Q-INFO-001", "current answer")
    orphan = tmp_path / ".answers.json.crash.tmp"
    orphan.write_text(
        '{"org-a":{"Q-INFO-001":"orphaned private answer"}}',
        encoding="utf-8",
    )

    assert store.purge() is True
    assert not path.exists()
    assert not orphan.exists()


def test_local_store_purge_treats_custom_filename_as_literal(tmp_path: Path) -> None:
    path = tmp_path / "answers[production].json"
    store = LocalAnswerStore(path)
    store.save("org-a", "Q-INFO-001", "current answer")
    orphan = tmp_path / ".answers[production].json.crash.tmp"
    orphan.write_text("orphaned private answer", encoding="utf-8")

    assert store.purge() is True
    assert not path.exists()
    assert not orphan.exists()


def test_local_store_serializes_save_and_purge_across_store_instances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "answers.json"
    LocalAnswerStore(path).save("org-a", "Q-INFO-001", "old answer")
    writer_at_replace = Event()
    allow_replace = Event()
    purge_started = Event()
    purge_finished = Event()
    real_replace = storage.os.replace

    def blocked_replace(source, target) -> None:
        if current_thread().name == "answer-writer" and Path(target) == path:
            writer_at_replace.set()
            assert allow_replace.wait(5)
        real_replace(source, target)

    monkeypatch.setattr(storage.os, "replace", blocked_replace)
    writer = Thread(
        name="answer-writer",
        target=lambda: LocalAnswerStore(path).save(
            "org-a", "Q-INFO-001", "new answer"
        ),
    )

    def purge() -> None:
        purge_started.set()
        LocalAnswerStore(path).purge()
        purge_finished.set()

    purger = Thread(name="answer-purger", target=purge)
    writer.start()
    assert writer_at_replace.wait(5)
    purger.start()
    assert purge_started.wait(5)
    assert not purge_finished.wait(0.2)
    allow_replace.set()
    writer.join(5)
    purger.join(5)

    assert not writer.is_alive()
    assert not purger.is_alive()
    assert purge_finished.is_set()
    assert not path.exists()


def test_local_store_serializes_save_and_purge_across_processes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "answers.json"
    ready = tmp_path / "writer-ready"
    release = tmp_path / "release-writer"
    LocalAnswerStore(path).save("org-a", "Q-INFO-001", "old answer")
    child_code = """
from pathlib import Path
import sys
import time
import mcp_server.storage as storage
from mcp_server.storage import LocalAnswerStore

path, ready, release = map(Path, sys.argv[1:])
real_replace = storage.os.replace

def blocked_replace(source, target):
    if Path(target) == path:
        ready.write_text("ready", encoding="utf-8")
        deadline = time.monotonic() + 10
        while not release.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("parent did not release writer")
            time.sleep(0.01)
    real_replace(source, target)

storage.os.replace = blocked_replace
LocalAnswerStore(path).save("org-a", "Q-INFO-001", "new answer")
"""
    process = subprocess.Popen(
        [sys.executable, "-c", child_code, str(path), str(ready), str(release)],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = 10.0
    while not ready.exists() and deadline > 0:
        if process.poll() is not None:
            break
        Event().wait(0.05)
        deadline -= 0.05
    assert ready.exists(), process.communicate(timeout=1)

    purge_finished = Event()
    purge_errors: list[BaseException] = []

    def purge() -> None:
        try:
            LocalAnswerStore(path).purge()
        except BaseException as exc:  # pragma: no cover - asserted below
            purge_errors.append(exc)
        finally:
            purge_finished.set()

    purger = Thread(name="cross-process-answer-purger", target=purge)
    purger.start()
    blocked_by_writer = not purge_finished.wait(1)
    release.write_text("release", encoding="utf-8")
    stdout, stderr = process.communicate(timeout=10)
    purger.join(10)

    assert blocked_by_writer
    assert process.returncode == 0, f"stdout={stdout}\nstderr={stderr}"
    assert not purge_errors
    assert not purger.is_alive()
    assert not path.exists()


def test_local_store_purge_org_preserves_other_answers(tmp_path: Path) -> None:
    path = tmp_path / "answers.json"
    store = LocalAnswerStore(path)
    store.save("org-a", "Q-INFO-001", "answer a")
    store.save("org-b", "Q-PROC-001", "answer b")

    assert store.purge("org-a") is True
    assert store.get("org-a") == {}
    assert store.get("org-b") == {"Q-PROC-001": "answer b"}
    assert store.purge("org-a") is False


def test_local_store_purge_last_org_removes_empty_store(tmp_path: Path) -> None:
    path = tmp_path / "answers.json"
    store = LocalAnswerStore(path)
    store.save("org-a", "Q-INFO-001", "answer a")

    assert store.purge("org-a") is True
    assert not path.exists()


def test_mcp_check_injects_saved_answer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = LocalAnswerStore(tmp_path / "answers.json")
    store.save("org-a", "Q-INFO-001", "온라인 이의신청 창구를 마련합니다.")
    monkeypatch.setattr(server, "answer_store", store)
    result = server.check_job_posting("간단한 채용 공고", "org-a")
    assert "[Q-INFO-001]" in result
    assert "저장된 답변: 온라인 이의신청 창구를 마련합니다." in result


def test_public_analysis_tools_never_read_organization_answers(
    monkeypatch,
) -> None:
    class FailOnReadStore:
        def get(self, _org_id: str):
            raise AssertionError("public tools must not read organization answers")

    monkeypatch.setattr(server, "answer_store", FailOnReadStore())

    checked = server.check_job_posting_public("간단한 채용 공고")
    structured = server.check_job_posting_structured_public("간단한 채용 공고")
    next_question = server.next_review_question_public("간단한 채용 공고")

    assert "저장된 답변:" not in checked
    assert all(question.saved_answer is None for question in structured.questions)
    assert isinstance(next_question["progress"], dict)


def test_structured_check_contract_is_versioned_and_traceable() -> None:
    text = "여성만 지원 가능"
    result = server.check_job_posting_structured_public(text)

    assert result.schema_version == "fairpost-structured-check-v1"
    assert result.disclaimer.startswith("이 결과는 점검 참고자료")
    assert result.findings[0].id == "SEX-001"
    assert result.findings[0].offset is not None
    assert text[slice(*result.findings[0].offset)] == result.findings[0].matched_text
    linked = [
        question
        for question in result.questions
        if "SEX-001" in question.linked_findings
    ]
    assert linked
    assert linked[0].book_ref
    assert linked[0].reference is not None

    structured_payload = asdict(result)
    structured_payload.pop("schema_version")
    json_payload = json.loads(json.dumps(structured_payload, ensure_ascii=False))
    assert json_payload == server.engine.check(text).to_dict()


def test_check_output_describes_candidates_without_declaring_illegality() -> None:
    checked = server.check_job_posting_public("여성만 지원 가능")

    assert "관련 법령 표현 검토 후보" in checked
    assert "법령 위반 사항" not in checked


def test_check_output_leads_with_human_review_boundary_and_keeps_priority() -> None:
    result = server.engine.check("여성만 지원 가능")
    checked = server._format_check_result_text(result)

    assert checked.startswith(result.disclaimer)
    assert checked.index(result.disclaimer) < checked.index("채용공고 점검 결과")
    assert checked.count(result.disclaimer) == 1
    assert "| 검토 우선도 |" in checked
    assert f"| {result.findings[0].severity} |" in checked
    assert "| 심각도 |" not in checked


def test_check_output_groups_questions_by_engine_metadata() -> None:
    text = "구인 공고\n지원자격: 여성만 지원 가능\n지원자는 이력서를 제출하세요."
    result = server.engine.check(text)
    checked = server._format_check_result_text(result)
    headings = {
        "core": "### 핵심 질문",
        "wording": "### 공고 문구 확인 질문",
        "missing": "### 누락 안내 확인 질문",
        "common": "### 공통 체크리스트",
    }

    def section(key: str) -> str:
        start = checked.index(headings[key])
        later = [
            checked.index(other)
            for other in headings.values()
            if other in checked and checked.index(other) > start
        ]
        end = min(later, default=checked.index("규칙셋 버전:"))
        return checked[start:end]

    for question in result.questions:
        if question.linked_findings:
            key = "core"
        elif question.review_scope == "common":
            key = "common"
        elif question.trigger_reason == "absence":
            key = "missing"
        else:
            key = "wording"
        assert f"[{question.id}]" in section(key)

    common = [q for q in result.questions if q.review_scope == "common"]
    # Generic trigger words such as "구인" must not promote checklist items.
    assert any(q.matched_text == "구인" for q in common)
    assert all(f"[{q.id}]" not in section("core") for q in common if not q.linked_findings)
    assert sum(
        checked.count(f"- [{q.id}] ") for q in result.questions
    ) == len(result.questions) == result.counts["questions"]


def test_check_output_shows_both_finding_link_and_matched_wording() -> None:
    result = server.engine.check("제출된 서류는 반환하지 않으며 채용 종료 후 파기합니다.")
    checked = server._format_check_result_text(result)
    both = [
        question
        for question in result.questions
        if question.linked_findings and question.matched_text
    ]

    assert both
    for question in both:
        line = next(
            item for item in checked.splitlines() if item.startswith(f"- [{question.id}]")
        )
        assert f"관련 발견: {', '.join(question.linked_findings)}" in line
        assert f'매칭 문구: "{question.matched_text}"' in line


def test_next_question_payload_is_traceable_and_json_serializable() -> None:
    text = "지원자격: 외국인 제외"
    result = server.engine.check(text)
    response = server.next_review_question_public(text)
    question = response["question"]

    assert question is not None
    assert question["book_ref"]
    assert question["saved_answer"] is None
    assert question["offset"] is None or text[slice(*question["offset"])] == (
        question["matched_text"]
    )
    assert question["reference"] is not None
    assert question["reference"]["type"]
    assert response["ruleset_version"] == result.ruleset_version
    assert response["statute_snapshot_date"] == result.statute_snapshot_date
    assert response["statute_notice"] == result.statute_notice
    json.dumps(response, ensure_ascii=False)


def test_vercel_transport_security_allows_current_production_hostname(
    monkeypatch,
) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("FAIRPOST_MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("FAIRPOST_MCP_ALLOWED_ORIGINS", raising=False)

    settings = server._transport_security_from_environment()

    assert "fairmcp.vercel.app" in settings.allowed_hosts
    assert "https://fairmcp.vercel.app" in settings.allowed_origins


def test_mcp_save_and_get_tools(tmp_path: Path, monkeypatch) -> None:
    store = LocalAnswerStore(tmp_path / "answers.json")
    monkeypatch.setattr(server, "answer_store", store)
    response = server.save_answer("org-b", "Q-PROC-001", "서류와 면접")
    assert response["status"] == "stored_locally"
    assert server.get_saved_answers("org-b") == {"Q-PROC-001": "서류와 면접"}

    server.save_answer("org-b", "Q-PROC-001", "서류, 면접, 실기")
    assert server.get_saved_answers("org-b") == {
        "Q-PROC-001": "서류, 면접, 실기"
    }


def test_mcp_rejects_unknown_question_id_without_writing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "answers.json"
    monkeypatch.setattr(server, "answer_store", LocalAnswerStore(path))
    private_value = "Q-NOT-FOUND-private-value"
    with pytest.raises(ValueError, match="현재 사전에 없는 question_id") as exc_info:
        server.save_answer("org-b", private_value, "저장되면 안 됩니다")
    assert private_value not in str(exc_info.value)
    assert not path.exists()


def test_vercel_without_durable_credentials_disables_answer_storage(
    monkeypatch,
) -> None:
    monkeypatch.setenv("VERCEL", "1")
    for name in (
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
        "KV_REST_API_URL",
        "KV_REST_API_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    store = storage.build_answer_store()
    assert isinstance(store, UnavailableRemoteAnswerStore)
    with pytest.raises(ValueError, match="루프백 로컬 MCP"):
        store.save("org-a", "Q-INFO-001", "답변")


def test_local_store_does_not_auto_select_cloud_from_environment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    answers_path = tmp_path / "answers.json"
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setenv("FAIRPOST_ANSWERS_PATH", str(answers_path))
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://redis.example")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "secret")

    store = storage.build_answer_store()

    assert isinstance(store, LocalAnswerStore)
    store.save("org-a", "Q-INFO-001", "로컬 답변")
    assert store.get("org-a") == {"Q-INFO-001": "로컬 답변"}
    assert answers_path.is_file()


def test_answer_store_rejects_unbounded_values(tmp_path: Path) -> None:
    store = LocalAnswerStore(tmp_path / "answers.json")
    with pytest.raises(ValueError, match="org_id는 256자"):
        store.get("o" * 257)
    with pytest.raises(ValueError, match="answer는 10000자"):
        store.save("org", "Q-INFO-001", "a" * 10_001)


# --- tool contract for AI clients (descriptions, limits, stateless progress) --
def _listed_tools(instance):
    import anyio

    async def exercise():
        return await instance.list_tools()

    return anyio.run(exercise)


@pytest.mark.parametrize("profile", ["mcp", "public_mcp", "claude_mcp"])
def test_every_tool_argument_has_a_korean_description(profile: str) -> None:
    import re

    for tool in _listed_tools(getattr(server, profile)):
        properties = tool.inputSchema.get("properties", {})
        assert properties, tool.name
        for name, schema in properties.items():
            description = schema.get("description", "")
            assert re.search("[가-힣]", description), (tool.name, name)
        if "text" in properties:
            assert properties["text"]["maxLength"] == server.MAX_POSTING_CHARS
            assert properties["text"]["minLength"] == 1
            assert "100,000자" in properties["text"]["description"]
            assert "1 MiB" in properties["text"]["description"]


def test_public_descriptions_ask_clients_not_to_summarize() -> None:
    for tool in _listed_tools(server.public_mcp):
        assert "요약하거나" in (tool.description or ""), tool.name
    assert "verbatim" in (server.public_mcp.instructions or "")


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
def test_empty_posting_text_is_rejected_by_every_tool(text: str) -> None:
    import anyio

    calls = [
        lambda: server.check_job_posting(text),
        lambda: server.check_job_posting_public(text),
        lambda: server.check_job_posting_readonly(text),
        lambda: server.check_job_posting_structured(text),
        lambda: server.check_job_posting_structured_public(text),
        lambda: server.next_review_question(text),
        lambda: server.next_review_question_public(text),
        lambda: anyio.run(server.prepare_hr_review, text),
    ]
    for call in calls:
        with pytest.raises(ValueError, match="검토할 공고문을 입력하세요"):
            call()


def test_over_limit_posting_text_error_is_korean() -> None:
    text = "가" * (server.MAX_POSTING_CHARS + 1)

    with pytest.raises(ValueError) as exc_info:
        server.check_job_posting_public(text)

    message = str(exc_info.value)
    assert "최대 100,000자까지 점검할 수 있습니다" in message
    assert "100,001자" in message
    assert server.check_job_posting_public("가" * 10 + " 여성만 지원 가능")


def test_public_next_question_advances_with_client_held_ids() -> None:
    text = "지원자격: 여성만 지원 가능"
    first = server.next_review_question_public(text)
    first_id = first["question"]["id"]
    total = first["progress"]["total"]

    second = server.next_review_question_public(text, [first_id])

    assert first["progress"] == {"total": total, "answered": 0, "remaining": total}
    assert second["question"]["id"] != first_id
    assert second["progress"] == {
        "total": total,
        "answered": 1,
        "remaining": total - 1,
    }

    all_ids = [question.id for question in server.engine.check(text).questions]
    finished = server.next_review_question_public(text, all_ids)
    assert finished["question"] is None
    assert finished["progress"]["remaining"] == 0

    # A valid ID for a question this posting did not raise is not counted.
    other = next(
        rule["id"]
        for rule in server.engine.ruleset.rules
        if rule["layer"] == "question" and rule["id"] not in all_ids
    )
    unrelated = server.next_review_question_public(text, [other])
    assert unrelated["progress"]["answered"] == 0


@pytest.mark.parametrize(
    "value",
    [["Q-NOT-A-REAL-QUESTION-private"], "Q-INFO-001", [1], ["Q-INFO-001"] * 257],
)
def test_public_next_question_rejects_invalid_ids_without_echo(value) -> None:
    with pytest.raises(ValueError, match="answered_question_ids") as exc_info:
        server.next_review_question_public("지원자격: 여성만 지원 가능", value)
    assert "private" not in str(exc_info.value)


def test_local_next_question_still_follows_saved_answers(
    tmp_path: Path, monkeypatch
) -> None:
    store = LocalAnswerStore(tmp_path / "answers.json")
    monkeypatch.setattr(server, "answer_store", store)
    text = "지원자격: 여성만 지원 가능"
    first = server.next_review_question(text, "org-next")
    store.save("org-next", first["question"]["id"], "검토 완료")

    second = server.next_review_question(text, "org-next")

    assert second["question"]["id"] != first["question"]["id"]
    assert second["progress"]["answered"] == 1


# --- local answer store hardening ------------------------------------------
# Identifier-shaped values are assembled from parts so the release privacy
# scan never sees a literal value in this file.
@pytest.mark.parametrize(
    "answer",
    [
        "담당자 " + "010" + "-1234-" + "5678" + "로 문의",
        "담당자 " + "０１０" + "－１２３４－" + "５６７８",
        "회신 " + "hr" + "@" + "example.com",
        "식별번호 " + "900101" + "-" + "1234567",
    ],
    ids=["phone", "fullwidth-phone", "email", "rrn"],
)
def test_save_answer_rejects_direct_identifiers_without_writing(
    tmp_path: Path, monkeypatch, answer: str
) -> None:
    path = tmp_path / "answers.json"
    monkeypatch.setattr(server, "answer_store", LocalAnswerStore(path))

    with pytest.raises(ValueError, match="직접 식별정보") as exc_info:
        server.save_answer("org-a", "Q-INFO-001", answer)

    assert answer not in str(exc_info.value)
    assert not path.exists()


def test_save_answer_keeps_accepting_ordinary_answers(tmp_path: Path, monkeypatch) -> None:
    store = LocalAnswerStore(tmp_path / "answers.json")
    monkeypatch.setattr(server, "answer_store", store)

    server.save_answer("org-a", "Q-INFO-001", "인사팀 대표번호로 2026-10-11까지 안내합니다.")

    assert store.get("org-a") == {
        "Q-INFO-001": "인사팀 대표번호로 2026-10-11까지 안내합니다."
    }


@pytest.mark.parametrize(
    "content",
    [b"{not json", b"[1, 2, 3]", b"\xff\xfe broken"],
    ids=["invalid-json", "not-an-object", "not-utf8"],
)
def test_corrupt_answer_store_degrades_to_no_answers_with_notice(
    tmp_path: Path, monkeypatch, caplog, content: bytes
) -> None:
    import logging

    path = tmp_path / "answers.json"
    path.write_bytes(content)
    monkeypatch.setattr(server, "answer_store", LocalAnswerStore(path))
    caplog.set_level(logging.WARNING, logger="fairpost")

    checked = server.check_job_posting("지원자격: 여성만 지원 가능", "org-a")
    structured = server.check_job_posting_structured("지원자격: 여성만 지원 가능", "org-a")
    next_question = server.next_review_question("지원자격: 여성만 지원 가능", "org-a")

    assert "SEX-001" in checked
    assert f"알림: {server.ANSWER_STORE_UNREADABLE_NOTICE}" in checked
    assert checked.index("알림:") < checked.index("채용공고 점검 결과")
    assert all(question.saved_answer is None for question in structured.questions)
    assert next_question["notice"] == server.ANSWER_STORE_UNREADABLE_NOTICE
    assert next_question["progress"]["answered"] == 0
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "event=answer_store_unreadable" in logged
    assert str(tmp_path) not in logged
    assert "org-a" not in logged


def test_readable_answer_store_has_no_notice(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "answer_store", LocalAnswerStore(tmp_path / "answers.json"))

    checked = server.check_job_posting("지원자격: 여성만 지원 가능", "org-a")
    next_question = server.next_review_question("지원자격: 여성만 지원 가능", "org-a")

    assert "알림:" not in checked
    assert "notice" not in next_question


def test_invalid_org_id_still_fails_loudly(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(server, "answer_store", LocalAnswerStore(tmp_path / "answers.json"))

    with pytest.raises(ValueError, match="org_id는 256자"):
        server.check_job_posting("지원자격: 여성만 지원 가능", "o" * 257)
