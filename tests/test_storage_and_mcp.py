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
    question = next(
        item for item in result.questions if item.id == "Q-INFO-001"
    )
    assert question.saved_answer == "온라인 이의신청 창구를 마련합니다."


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
    with pytest.raises(ValueError, match="UPSTASH_REDIS_REST_URL"):
        store.save("org-a", "Q-INFO-001", "답변")


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

        def read(self) -> bytes:
            return json.dumps(next(responses), ensure_ascii=False).encode("utf-8")

    def fake_urlopen(request, timeout):
        calls.append(
            {
                "url": request.full_url,
                "body": json.loads(request.data.decode("utf-8")),
                "authorization": request.headers["Authorization"],
                "timeout": timeout,
            }
        )
        return Response()

    monkeypatch.setattr(storage, "urlopen", fake_urlopen)
    store = UpstashAnswerStore("https://redis.example", "secret")
    store.save("실제 기관명", "Q-INFO-001", "인사팀에서 검토")
    assert store.get("실제 기관명") == {"Q-INFO-001": "인사팀에서 검토"}

    assert calls[0]["url"] == "https://redis.example"
    assert "실제 기관명" not in json.dumps(calls, ensure_ascii=False)
    assert calls[0]["body"][0] == "HSET"
    assert calls[1]["body"][0] == "HGETALL"
    assert calls[0]["authorization"] == "Bearer secret"
