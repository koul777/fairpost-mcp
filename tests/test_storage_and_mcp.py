from __future__ import annotations

from pathlib import Path
import json

import pytest

import mcp_server.server as server
import mcp_server.storage as storage
from mcp_server.storage import (
    LocalAnswerStore,
    UnavailableRemoteAnswerStore,
    UpstashAnswerStore,
)


def test_answer_store_writes_only_configured_local_file(tmp_path: Path) -> None:
    path = tmp_path / "local" / "answers.json"
    store = LocalAnswerStore(path)
    store.save("org-a", "Q-INFO-001", "인사팀에서 검토합니다.")
    assert store.get("org-a") == {"Q-INFO-001": "인사팀에서 검토합니다."}
    assert path.exists()
    assert list(tmp_path.rglob("*.json")) == [path]


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
    next_question = server.next_review_question_public("간단한 채용 공고")

    assert "저장된 답변:" not in checked
    assert isinstance(next_question["progress"], dict)


def test_check_output_describes_candidates_without_declaring_illegality() -> None:
    checked = server.check_job_posting_public("여성만 지원 가능")

    assert "관련 법령 표현 검토 후보" in checked
    assert "법령 위반 사항" not in checked


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


def test_mcp_rejects_unknown_question_id_without_writing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "answers.json"
    monkeypatch.setattr(server, "answer_store", LocalAnswerStore(path))
    with pytest.raises(ValueError, match="현재 사전에 없는 question_id"):
        server.save_answer("org-b", "Q-NOT-FOUND", "저장되면 안 됩니다")
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


def test_upstash_store_hashes_org_id_and_uses_request_body(
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []
    responses = iter(
        [
            {"result": 1},
            {"result": ["Q-INFO-001", "인사팀에서 검토"]},
        ]
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit: int = -1) -> bytes:
            return json.dumps(next(responses), ensure_ascii=False).encode("utf-8")

    def fake_urlopen(request, *, timeout):
        calls.append(
            {
                "url": request.full_url,
                "body": json.loads(request.data.decode("utf-8")),
                "authorization": request.headers["Authorization"],
                "timeout": timeout,
            }
        )
        return Response()

    monkeypatch.setattr(storage, "_open_upstash", fake_urlopen)
    store = UpstashAnswerStore("https://redis.example", "secret")
    store.save("실제 기관명", "Q-INFO-001", "인사팀에서 검토")
    assert store.get("실제 기관명") == {"Q-INFO-001": "인사팀에서 검토"}

    assert calls[0]["url"] == "https://redis.example"
    assert "실제 기관명" not in json.dumps(calls, ensure_ascii=False)
    assert calls[0]["body"][0] == "HSET"
    assert calls[1]["body"][0] == "HGETALL"
    assert calls[0]["authorization"] == "Bearer secret"


def test_upstash_store_requires_safe_https_endpoint() -> None:
    with pytest.raises(ValueError, match="HTTPS URL"):
        UpstashAnswerStore("http://redis.example", "secret")
    with pytest.raises(ValueError, match="HTTPS URL"):
        UpstashAnswerStore("https://user:pass@redis.example", "secret")
    with pytest.raises(ValueError, match="HTTPS URL"):
        UpstashAnswerStore("https://redis.example?token=leak", "secret")


def test_upstash_store_rejects_oversized_response(monkeypatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit: int = -1) -> bytes:
            return b"x" * (storage.MAX_UPSTASH_RESPONSE_BYTES + 1)

    monkeypatch.setattr(
        storage,
        "_open_upstash",
        lambda _request, *, timeout: Response(),
    )
    store = UpstashAnswerStore("https://redis.example", "secret")

    with pytest.raises(ValueError, match="응답이 너무 큽니다"):
        store.get("org-a")


def test_upstash_redirect_handler_refuses_redirects() -> None:
    handler = storage._RejectRedirects()
    assert handler.redirect_request(None, None, 307, "redirect", {}, None) is None


def test_answer_store_rejects_unbounded_values(tmp_path: Path) -> None:
    store = LocalAnswerStore(tmp_path / "answers.json")
    with pytest.raises(ValueError, match="org_id는 256자"):
        store.get("o" * 257)
    with pytest.raises(ValueError, match="answer는 10000자"):
        store.save("org", "Q-INFO-001", "a" * 10_001)
