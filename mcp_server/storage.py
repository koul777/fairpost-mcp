from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import Lock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LocalAnswerStore:
    """Small, local-only JSON store for organization question answers."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured = os.environ.get("FAIRPOST_ANSWERS_PATH")
        self.path = Path(path or configured or Path.home() / ".fairpost" / "answers.json")
        self._lock = Lock()

    def _read(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            payload: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"답변 저장소를 읽을 수 없습니다: {self.path}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"답변 저장소 형식이 올바르지 않습니다: {self.path}")
        result: dict[str, dict[str, str]] = {}
        for org_id, answers in payload.items():
            if isinstance(org_id, str) and isinstance(answers, dict):
                result[org_id] = {
                    str(question_id): str(answer)
                    for question_id, answer in answers.items()
                }
        return result

    def get(self, org_id: str) -> dict[str, str]:
        if not org_id.strip():
            raise ValueError("org_id는 비어 있을 수 없습니다")
        with self._lock:
            return dict(self._read().get(org_id, {}))

    def save(self, org_id: str, question_id: str, answer: str) -> None:
        if not org_id.strip() or not question_id.strip():
            raise ValueError("org_id와 question_id는 비어 있을 수 없습니다")
        with self._lock:
            payload = self._read()
            payload.setdefault(org_id, {})[question_id] = answer
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary_name = tempfile.mkstemp(
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary.exists():
                    temporary.unlink()


class UpstashAnswerStore:
    """Durable answer storage for stateless remote deployments."""

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
    ) -> None:
        self.url = (
            url
            or os.environ.get("UPSTASH_REDIS_REST_URL")
            or os.environ.get("KV_REST_API_URL")
            or ""
        ).rstrip("/")
        self.token = (
            token
            or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
            or os.environ.get("KV_REST_API_TOKEN")
            or ""
        )
        if not self.url or not self.token:
            raise ValueError("Upstash REST URL과 토큰이 모두 필요합니다")

    @staticmethod
    def _key(org_id: str) -> str:
        digest = hashlib.sha256(org_id.strip().encode("utf-8")).hexdigest()
        return f"fairpost:answers:{digest}"

    def _command(self, *parts: str) -> Any:
        payload = json.dumps(list(parts), ensure_ascii=False).encode("utf-8")
        request = Request(
            self.url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "fairpost/0.3",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
            raise ValueError("원격 답변 저장소 요청에 실패했습니다") from exc
        if not isinstance(body, dict):
            raise ValueError("원격 답변 저장소 응답 형식이 올바르지 않습니다")
        if body.get("error"):
            raise ValueError("원격 답변 저장소가 요청을 거부했습니다")
        return body.get("result")

    def get(self, org_id: str) -> dict[str, str]:
        if not org_id.strip():
            raise ValueError("org_id는 비어 있을 수 없습니다")
        result = self._command("HGETALL", self._key(org_id))
        if result is None:
            return {}
        if isinstance(result, dict):
            return {str(key): str(value) for key, value in result.items()}
        if isinstance(result, list) and len(result) % 2 == 0:
            return {
                str(result[index]): str(result[index + 1])
                for index in range(0, len(result), 2)
            }
        raise ValueError("원격 답변 저장소의 HGETALL 응답 형식이 올바르지 않습니다")

    def save(self, org_id: str, question_id: str, answer: str) -> None:
        if not org_id.strip() or not question_id.strip():
            raise ValueError("org_id와 question_id는 비어 있을 수 없습니다")
        result = self._command(
            "HSET",
            self._key(org_id),
            question_id,
            answer,
        )
        if not isinstance(result, int):
            raise ValueError("원격 답변 저장소의 HSET 응답 형식이 올바르지 않습니다")


class UnavailableRemoteAnswerStore:
    """Fail explicitly instead of pretending serverless files are durable."""

    MESSAGE = (
        "원격 답변 저장소가 구성되지 않았습니다. Vercel 프로젝트에 "
        "UPSTASH_REDIS_REST_URL과 UPSTASH_REDIS_REST_TOKEN을 연결하십시오"
    )

    def get(self, org_id: str) -> dict[str, str]:
        if not org_id.strip():
            raise ValueError("org_id는 비어 있을 수 없습니다")
        raise ValueError(self.MESSAGE)

    def save(self, org_id: str, question_id: str, answer: str) -> None:
        if not org_id.strip() or not question_id.strip():
            raise ValueError("org_id와 question_id는 비어 있을 수 없습니다")
        raise ValueError(self.MESSAGE)


def build_answer_store() -> LocalAnswerStore | UpstashAnswerStore | UnavailableRemoteAnswerStore:
    remote_url = os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get(
        "KV_REST_API_URL"
    )
    remote_token = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get(
        "KV_REST_API_TOKEN"
    )
    if remote_url or remote_token:
        return UpstashAnswerStore(remote_url, remote_token)
    if os.environ.get("VERCEL"):
        return UnavailableRemoteAnswerStore()
    return LocalAnswerStore()
