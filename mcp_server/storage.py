from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import Lock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_ORG_ID_CHARS = 256
MAX_QUESTION_ID_CHARS = 128
MAX_ANSWER_CHARS = 10_000
MAX_UPSTASH_RESPONSE_BYTES = 1024 * 1024


def _validate_answer_fields(
    org_id: str,
    question_id: str | None = None,
    answer: str | None = None,
) -> None:
    if not isinstance(org_id, str) or not org_id.strip():
        raise ValueError("org_id는 비어 있을 수 없습니다")
    if len(org_id) > MAX_ORG_ID_CHARS:
        raise ValueError(f"org_id는 {MAX_ORG_ID_CHARS}자를 넘을 수 없습니다")
    if question_id is not None:
        if not isinstance(question_id, str) or not question_id.strip():
            raise ValueError("question_id는 비어 있을 수 없습니다")
        if len(question_id) > MAX_QUESTION_ID_CHARS:
            raise ValueError(
                f"question_id는 {MAX_QUESTION_ID_CHARS}자를 넘을 수 없습니다"
            )
    if answer is not None:
        if not isinstance(answer, str):
            raise ValueError("answer는 문자열이어야 합니다")
        if len(answer) > MAX_ANSWER_CHARS:
            raise ValueError(f"answer는 {MAX_ANSWER_CHARS}자를 넘을 수 없습니다")


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_upstash(request: Request, *, timeout: float):
    return build_opener(_RejectRedirects()).open(request, timeout=timeout)


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
        _validate_answer_fields(org_id)
        with self._lock:
            return dict(self._read().get(org_id, {}))

    def save(self, org_id: str, question_id: str, answer: str) -> None:
        _validate_answer_fields(org_id, question_id, answer)
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
        parsed = urlsplit(self.url)
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Upstash REST URL은 자격정보ㆍ쿼리ㆍfragment가 없는 HTTPS URL이어야 합니다"
            )

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
            with _open_upstash(request, timeout=10) as response:
                payload = response.read(MAX_UPSTASH_RESPONSE_BYTES + 1)
                if len(payload) > MAX_UPSTASH_RESPONSE_BYTES:
                    raise ValueError("원격 답변 저장소 응답이 너무 큽니다")
                body = json.loads(payload.decode("utf-8"))
        except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
            raise ValueError("원격 답변 저장소 요청에 실패했습니다") from exc
        if not isinstance(body, dict):
            raise ValueError("원격 답변 저장소 응답 형식이 올바르지 않습니다")
        if body.get("error"):
            raise ValueError("원격 답변 저장소가 요청을 거부했습니다")
        return body.get("result")

    def get(self, org_id: str) -> dict[str, str]:
        _validate_answer_fields(org_id)
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
        _validate_answer_fields(org_id, question_id, answer)
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
        "원격 답변 저장은 비활성화되어 있습니다. 답변 저장이 필요하면 "
        "루프백 로컬 MCP를 사용하십시오"
    )

    def get(self, org_id: str) -> dict[str, str]:
        _validate_answer_fields(org_id)
        raise ValueError(self.MESSAGE)

    def save(self, org_id: str, question_id: str, answer: str) -> None:
        _validate_answer_fields(org_id, question_id, answer)
        raise ValueError(self.MESSAGE)


class EphemeralAnswerStore:
    """Best-effort in-memory answers for remote deployments without Redis."""

    def __init__(self) -> None:
        self._answers: dict[str, dict[str, str]] = {}
        self._lock = Lock()

    def get(self, org_id: str) -> dict[str, str]:
        _validate_answer_fields(org_id)
        with self._lock:
            return dict(self._answers.get(org_id, {}))

    def save(self, org_id: str, question_id: str, answer: str) -> None:
        _validate_answer_fields(org_id, question_id, answer)
        with self._lock:
            self._answers.setdefault(org_id, {})[question_id] = answer


def build_answer_store() -> LocalAnswerStore | UpstashAnswerStore | UnavailableRemoteAnswerStore:
    if os.environ.get("VERCEL"):
        return UnavailableRemoteAnswerStore()
    # Product policy is on-device first. Merely inheriting cloud-storage
    # credentials must never redirect local review answers off the machine.
    return LocalAnswerStore()
