from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from core import FairpostEngine
from tools import build_private_review_queue as queue


ROOT = Path(__file__).resolve().parents[1]


def record(
    text: str,
    *,
    sector: str = "private",
    split: str | None = "train",
    **metadata: str,
) -> dict[str, str]:
    value = {
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "sector": sector,
        "text": text,
        **metadata,
    }
    if split is not None:
        value["split"] = split
    return value


def write_jsonl(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def run_build(
    tmp_path: Path,
    rows: list[object],
    *,
    name: str,
    rule_ids: tuple[str, ...] = ("SEX-001",),
    per_rule: int = 20,
    context_chars: int = 120,
) -> tuple[Path, dict[str, int]]:
    input_path = tmp_path / name / "train" / "records.jsonl"
    output_path = tmp_path / name / "review-queue.jsonl"
    write_jsonl(input_path, rows)
    counts = queue.build_review_queue(
        input_path,
        output_path,
        rule_ids=rule_ids,
        per_rule=per_rule,
        context_chars=context_chars,
    )
    return output_path, counts


def test_is_deterministic_for_reordered_input_and_deduplicates_context(
    tmp_path: Path,
) -> None:
    rows = [
        record("채용 조건 A: 여성만 모집합니다."),
        record("채용 조건 B: 여성만 모집합니다."),
        record("채용 조건 C: 여성만 모집합니다."),
        record("채용 조건 A: 여성만 모집합니다.", id="secret-duplicate"),
    ]
    first_path, first_counts = run_build(tmp_path, rows, name="first")
    second_path, second_counts = run_build(
        tmp_path, list(reversed(rows)), name="second"
    )

    assert first_counts == second_counts == {"SEX-001": 3}
    assert first_path.read_bytes() == second_path.read_bytes()
    first_manifest = json.loads(
        queue.queue_manifest_path(first_path).read_text(encoding="utf-8")
    )
    second_manifest = json.loads(
        queue.queue_manifest_path(second_path).read_text(encoding="utf-8")
    )
    assert first_manifest["immutable_rows_sha256"] == second_manifest[
        "immutable_rows_sha256"
    ]
    assert first_manifest["rule_sampling"]["SEX-001"] == {
        "candidate_matches": 4,
        "unique_contexts": 3,
        "selected_rows": 3,
        "collapsed_duplicate_contexts": 1,
        "truncated_unique_contexts": 0,
    }


def test_enforces_per_rule_quota_independently(tmp_path: Path) -> None:
    rows = [
        record(
            f"사례 {index} 지원자격: 여성만 모집하며 TOEIC 750점 이상을 요구합니다."
        )
        for index in range(8)
    ]
    output, counts = run_build(
        tmp_path,
        rows,
        name="quota",
        rule_ids=("SEX-001", "Q-DIST-014"),
        per_rule=3,
    )
    loaded = read_jsonl(output)

    assert counts == {"Q-DIST-014": 3, "SEX-001": 3}
    assert len(loaded) == 6
    assert {row["rule_id"] for row in loaded} == {"SEX-001", "Q-DIST-014"}
    manifest = json.loads(
        queue.queue_manifest_path(output).read_text(encoding="utf-8")
    )
    assert manifest["rule_sampling"]["SEX-001"]["truncated_unique_contexts"] == 5
    assert manifest["rule_sampling"]["Q-DIST-014"][
        "truncated_unique_contexts"
    ] == 5


def test_defaults_to_presence_rules_and_never_outputs_absence_questions(
    tmp_path: Path,
) -> None:
    output, counts = run_build(
        tmp_path,
        [record("간단한 채용 공고이며 여성만 모집합니다.")],
        name="presence-only",
        rule_ids=(),
    )
    rows = read_jsonl(output)
    engine = FairpostEngine()
    absence_ids = {
        rule["id"]
        for rule in engine.ruleset.rules
        if rule["trigger"]["type"] == "absence"
    }

    assert not ({row["rule_id"] for row in rows} & absence_ids)
    assert not (set(counts) & absence_ids)
    assert any(row["rule_id"] == "SEX-001" for row in rows)


def test_redeidentifies_context_and_emits_only_anonymous_schema(
    tmp_path: Path,
) -> None:
    secrets = {
        "organization": "은하물산",
        "email": "private.person@example.com",
        "phone": "010-9876-5432",
        "name": "홍길동",
        "record_id": "private:secret-record-id",
        "source": "secret-source",
        "source_url": "https://secret.example/jobs/1",
        "body_https_url": "https://body-secret.example/jobs/2?token=private",
        "body_http_url": "http://body-secret.example/jobs/3#contact",
    }
    text = (
        f"{secrets['organization']} 여성만 모집합니다. 담당자: {secrets['name']} "
        f"{secrets['email']} {secrets['phone']} "
        f"{secrets['body_https_url']} {secrets['body_http_url']}"
    )
    output, _ = run_build(
        tmp_path,
        [
            record(
                text,
                organization=secrets["organization"],
                id=secrets["record_id"],
                source=secrets["source"],
                source_url=secrets["source_url"],
            )
        ],
        name="privacy",
        context_chars=500,
    )
    row = read_jsonl(output)[0]
    encoded = json.dumps(row, ensure_ascii=False, sort_keys=True)

    assert set(row) == queue.OUTPUT_FIELDS
    for secret in secrets.values():
        assert secret not in encoded
    assert row["label"] == "unreviewed"
    assert row["allowed_labels"] == [
        "true_positive",
        "false_positive",
        "uncertain",
    ]
    assert row["layer"] == "law"
    assert row["matched_text"]
    assert str(row["context"]).count("[URL]") == 2
    assert len(str(row["review_id"])) == 64
    assert set(str(row["review_id"])) <= set("0123456789abcdef")
    assert not ({"source", "source_url", "id", "organization"} & set(row))


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("per_rule", True, "--per-rule"),
        ("per_rule", 1.5, "--per-rule"),
        ("per_rule", 0, "--per-rule"),
        ("context_chars", True, "--context-chars"),
        ("context_chars", 1.5, "--context-chars"),
        ("context_chars", -1, "--context-chars"),
    ],
)
def test_programmatic_options_require_non_bool_integers_before_path_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    option: str,
    value: object,
    message: str,
) -> None:
    monkeypatch.setattr(
        queue,
        "validate_paths",
        lambda *_args: pytest.fail("must validate options before paths"),
    )
    arguments: dict[str, object] = {"per_rule": 1, "context_chars": 120}
    arguments[option] = value

    with pytest.raises(ValueError, match=message):
        queue.build_review_queue(
            tmp_path / "train" / "records.jsonl",
            tmp_path / "review.jsonl",
            **arguments,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "partition", ["holdout", "hold-out", "test", "tests", "dev", "evaluation", "eval"]
)
def test_rejects_non_train_path_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    partition: str,
) -> None:
    input_path = tmp_path / partition / "records.jsonl"
    output_path = tmp_path / "queue.jsonl"
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("must not read"))

    with pytest.raises(ValueError, match="비학습|train"):
        queue.build_review_queue(
            input_path,
            output_path,
            rule_ids=("SEX-001",),
        )


def test_rejects_non_private_or_non_train_record(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="sector=private"):
        run_build(
            tmp_path,
            [record("여성만 모집", sector="public")],
            name="public",
        )
    with pytest.raises(ValueError, match="split.*train"):
        run_build(
            tmp_path,
            [record("여성만 모집", split="dev")],
            name="dev-record",
        )


@pytest.mark.parametrize("rule_id", ["NOT-A-RULE", "Q-DIST-001"])
def test_rejects_invalid_or_absence_rule_id_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rule_id: str,
) -> None:
    input_path = tmp_path / "train" / "records.jsonl"
    output_path = tmp_path / "queue.jsonl"
    engine = FairpostEngine()
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("must not read"))

    with pytest.raises(ValueError, match="규칙|presence"):
        queue.build_review_queue(
            input_path,
            output_path,
            rule_ids=(rule_id,),
            engine=engine,
        )


def test_rejects_input_output_collision_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "train" / "records.jsonl"
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("must not read"))

    with pytest.raises(ValueError, match="서로 다른"):
        queue.build_review_queue(path, path, rule_ids=("SEX-001",))


def test_rejects_hardlink_alias_without_overwriting_train(tmp_path: Path) -> None:
    input_path = tmp_path / "train" / "records.jsonl"
    output_path = tmp_path / "review-queue.jsonl"
    write_jsonl(input_path, [record("여성만 모집합니다.")])
    original = input_path.read_bytes()
    os.link(input_path, output_path)

    with pytest.raises(ValueError, match="서로 다른"):
        queue.build_review_queue(
            input_path,
            output_path,
            rule_ids=("SEX-001",),
        )

    assert input_path.read_bytes() == original


def test_rejects_manifest_hardlink_alias_without_overwriting_train(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "train" / "records.jsonl"
    output_path = tmp_path / "review-queue.jsonl"
    manifest_path = queue.queue_manifest_path(output_path)
    write_jsonl(input_path, [record("여성만 모집합니다.")])
    original = input_path.read_bytes()
    os.link(input_path, manifest_path)

    with pytest.raises(ValueError, match="서로 다른"):
        queue.build_review_queue(
            input_path,
            output_path,
            rule_ids=("SEX-001",),
        )

    assert input_path.read_bytes() == manifest_path.read_bytes() == original


def test_rejects_duplicate_json_fields(tmp_path: Path) -> None:
    input_path = tmp_path / "train" / "records.jsonl"
    output_path = tmp_path / "review-queue.jsonl"
    value = record("여성만 모집합니다.")
    raw = json.dumps(value, ensure_ascii=False)[:-1] + ',"sector":"private"}\n'
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text(raw, encoding="utf-8")

    with pytest.raises(ValueError, match="중복 JSON"):
        queue.build_review_queue(
            input_path,
            output_path,
            rule_ids=("SEX-001",),
        )

    assert not output_path.exists()


def test_build_parses_the_single_captured_input_instead_of_reopening_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "train" / "records.jsonl"
    output_path = tmp_path / "review-queue.jsonl"
    write_jsonl(input_path, [record("여성만 모집합니다.")])
    monkeypatch.setattr(
        queue,
        "load_private_train_records",
        lambda _path: (_ for _ in ()).throw(AssertionError("path reopened")),
    )

    counts = queue.build_review_queue(
        input_path,
        output_path,
        rule_ids=("SEX-001",),
    )

    assert counts == {"SEX-001": 1}
    assert output_path.exists()


def test_atomic_write_failure_preserves_existing_queue_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "train" / "records.jsonl"
    output_path = tmp_path / "review-queue.jsonl"
    write_jsonl(input_path, [record("여성만 모집합니다.")])
    output_path.write_text("previous queue\n", encoding="utf-8")
    manifest_path = queue.queue_manifest_path(output_path)
    manifest_path.write_text("previous manifest\n", encoding="utf-8")
    monkeypatch.setattr(
        queue.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("simulated")),
    )

    with pytest.raises(ValueError, match="게시할 수 없습니다"):
        queue.build_review_queue(
            input_path,
            output_path,
            rule_ids=("SEX-001",),
        )

    assert output_path.read_text(encoding="utf-8") == "previous queue\n"
    assert manifest_path.read_text(encoding="utf-8") == "previous manifest\n"
    assert not list(tmp_path.rglob("*.fairpost-temp-*"))
    assert not list(tmp_path.rglob("*.fairpost-backup-*"))


def test_manifest_is_text_free_and_label_edits_preserve_immutable_digest(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "manifest" / "train" / "records.jsonl"
    output_path = tmp_path / "manifest" / "review.jsonl"
    write_jsonl(
        input_path,
        [record("민감 문맥 여성만 모집합니다.")],
    )
    queue.build_review_queue(
        input_path,
        output_path,
        rule_ids=("SEX-001",),
        per_rule=2,
        context_chars=50,
    )
    rows = read_jsonl(output_path)
    manifest = json.loads(
        queue.queue_manifest_path(output_path).read_text(encoding="utf-8")
    )
    engine = FairpostEngine()

    assert manifest == {
        "schema_version": "private-review-queue-manifest-v1",
        "ruleset_version": engine.ruleset.version,
        "matching_version": engine.ruleset.matching_version,
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "selected_rule_ids": ["SEX-001"],
        "per_rule": 2,
        "context_chars": 50,
        "row_count": 1,
        "rule_sampling": {
            "SEX-001": {
                "candidate_matches": 1,
                "unique_contexts": 1,
                "selected_rows": 1,
                "collapsed_duplicate_contexts": 0,
                "truncated_unique_contexts": 0,
            }
        },
        "immutable_rows_sha256": queue.immutable_rows_sha256(rows),
    }
    relabeled = [dict(rows[0], label="true_positive")]
    assert queue.immutable_rows_sha256(relabeled) == manifest[
        "immutable_rows_sha256"
    ]
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    assert "민감 문맥" not in encoded
    assert rows[0]["review_id"] not in encoded


def test_cli_writes_queue_and_stdout_contains_counts_only(tmp_path: Path) -> None:
    input_path = tmp_path / "train" / "records.jsonl"
    output_path = tmp_path / "review.jsonl"
    secret = "원문비밀 여성만 모집"
    write_jsonl(input_path, [record(secret)])

    completed = subprocess.run(
        [
            sys.executable,
            "tools/build_private_review_queue.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--rule-id",
            "SEX-001",
            "--per-rule",
            "1",
            "--context-chars",
            "10",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"SEX-001": 1}
    assert secret not in completed.stdout
    assert str(input_path) not in completed.stdout
    assert output_path.exists()
    assert queue.queue_manifest_path(output_path).exists()


@pytest.mark.parametrize(
    "error",
    [
        OSError("SECRET_PATH_VALUE"),
        UnicodeError("SECRET_UNICODE_VALUE"),
        ValueError("SECRET_ROW_VALUE"),
    ],
)
def test_cli_errors_are_fixed_and_do_not_reflect_exception_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    monkeypatch.setattr(
        queue,
        "build_review_queue",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    return_code = queue.main(
        [
            "--input",
            str(tmp_path / "PRIVATE_PATH" / "train" / "records.jsonl"),
            "--output",
            str(tmp_path / "PRIVATE_OUTPUT" / "review.jsonl"),
        ]
    )
    captured = capsys.readouterr()

    assert return_code == 1
    assert captured.out == ""
    assert captured.err == "error: 민간 검토 큐를 생성할 수 없습니다\n"
    assert "SECRET_" not in captured.err
    assert "PRIVATE_" not in captured.err


def test_cli_help_documents_privacy_warning() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/build_private_review_queue.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert completed.returncode == 0
    assert "개인정보 경고" in completed.stdout
    assert "로컬" in completed.stdout
