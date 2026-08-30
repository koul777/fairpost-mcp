from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from core import FairpostEngine
from tools import build_private_review_ui as review_ui


ROOT = Path(__file__).resolve().parents[1]


def eligible_rule(rule_id: str = "SEX-001") -> dict[str, object]:
    for rule in FairpostEngine().ruleset.rules:
        if rule["id"] == rule_id:
            return rule
    raise AssertionError("test rule not found")


def row(
    *,
    review_id: str = "a" * 64,
    rule_id: str = "SEX-001",
    context: str = "채용 조건에서 남성만 모집합니다.",
    matched_text: str = "남성만",
    label: str = "unreviewed",
) -> dict[str, object]:
    rule = eligible_rule(rule_id)
    return {
        "review_id": review_id,
        "rule_id": rule_id,
        "layer": rule["layer"],
        "dimension": rule["dimension"],
        "context": context,
        "matched_text": matched_text,
        "section": "채용 조건",
        "label": label,
        "allowed_labels": list(review_ui.ALLOWED_LABELS),
    }


def write_jsonl(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in rows),
        encoding="utf-8",
    )


def embedded_rows(html: str) -> list[dict[str, object]]:
    match = re.search(r"const encodedQueue = '([A-Za-z0-9+/=]*)';", html)
    assert match
    decoded = base64.b64decode(match.group(1)).decode("utf-8")
    return json.loads(decoded)


def embedded_rule_metadata(html: str) -> dict[str, dict[str, str]]:
    match = re.search(
        r"const encodedRuleMetadata = '([A-Za-z0-9+/=]*)';", html
    )
    assert match
    decoded = base64.b64decode(match.group(1)).decode("utf-8")
    return json.loads(decoded)


def test_builds_single_file_offline_labeler_with_expected_controls(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "queue.jsonl"
    output_path = tmp_path / "local-review.html"
    rows = [row(), row(review_id="b" * 64, rule_id="AGE-002")]
    write_jsonl(input_path, rows)

    summary = review_ui.build_review_ui(input_path, output_path)
    html = output_path.read_text(encoding="utf-8")

    assert summary == {"rows": 2, "rules": 2}
    assert embedded_rows(html) == rows
    metadata = embedded_rule_metadata(html)
    assert set(metadata) == {"AGE-002", "SEX-001"}
    assert "법 위반 확정은 아닙니다" in metadata["SEX-001"]["guidance"]
    assert metadata["AGE-002"]["criterion"] == eligible_rule("AGE-002")["message"]
    assert "검토 기준" in html
    assert "true_positive" in html
    assert "false_positive" in html
    assert "uncertain" in html
    assert "미검토로 되돌리기" in html
    assert "진행률" in html
    assert "규칙 필터" in html
    assert "private-review-labeled.jsonl" in html
    assert "connect-src 'none'" in html
    assert "XMLHttpRequest" not in html
    assert "fetch(" not in html
    assert "https://" not in html
    assert "http://" not in html
    assert "<link" not in html
    assert "<script src=" not in html


def test_question_rule_guidance_does_not_present_review_as_violation(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "queue.jsonl"
    output_path = tmp_path / "review.html"
    value = row(
        rule_id="Q-INFO-014",
        context="합격자에 한해 개별 통보합니다.",
        matched_text="합격자에 한해 개별 통보",
    )
    write_jsonl(input_path, [value])

    review_ui.build_review_ui(input_path, output_path)
    html = output_path.read_text(encoding="utf-8")
    metadata = embedded_rule_metadata(html)["Q-INFO-014"]

    assert metadata["criterion"] == eligible_rule("Q-INFO-014")["question"]
    assert "사람 검토 질문과 관련" in metadata["guidance"]
    assert "차별 또는 법 위반 확정은 아닙니다" in metadata["guidance"]
    assert "질문 적합" in html
    assert "질문 부적합" in html


def test_each_card_can_reset_a_selected_label_to_unreviewed(tmp_path: Path) -> None:
    input_path = tmp_path / "queue.jsonl"
    output_path = tmp_path / "review.html"
    write_jsonl(input_path, [row(label="false_positive")])

    review_ui.build_review_ui(input_path, output_path)
    html = output_path.read_text(encoding="utf-8")

    assert "resetButton.textContent = '미검토로 되돌리기'" in html
    assert "resetButton.disabled = row.label === 'unreviewed'" in html
    assert "row.label = 'unreviewed';" in html
    assert embedded_rows(html)[0]["label"] == "false_positive"


def test_repeated_builds_are_byte_identical(tmp_path: Path) -> None:
    input_path = tmp_path / "queue.jsonl"
    first_output = tmp_path / "review-first.html"
    second_output = tmp_path / "review-second.html"
    write_jsonl(
        input_path,
        [row(), row(review_id="b" * 64, rule_id="AGE-002")],
    )

    review_ui.build_review_ui(input_path, first_output)
    review_ui.build_review_ui(input_path, second_output)

    assert first_output.read_bytes() == second_output.read_bytes()


def test_empty_queue_builds_a_valid_zero_progress_ui(tmp_path: Path) -> None:
    input_path = tmp_path / "queue.jsonl"
    output_path = tmp_path / "review.html"
    input_path.write_text("", encoding="utf-8")

    summary = review_ui.build_review_ui(input_path, output_path)
    html = output_path.read_text(encoding="utf-8")

    assert summary == {"rows": 0, "rules": 0}
    assert embedded_rows(html) == []
    assert "rows.length" in html
    assert "표시할 검토 항목이 없습니다." in html


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
def test_inline_script_is_valid_javascript(tmp_path: Path) -> None:
    input_path = tmp_path / "queue.jsonl"
    output_path = tmp_path / "review.html"
    script_path = tmp_path / "review-script.js"
    write_jsonl(input_path, [row()])
    review_ui.build_review_ui(input_path, output_path)
    html = output_path.read_text(encoding="utf-8")
    match = re.search(r"<script>([\s\S]*)</script>", html)
    assert match
    script_path.write_text(match.group(1), encoding="utf-8")

    completed = subprocess.run(
        ["node", "--check", str(script_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr


def test_script_breakout_and_html_are_only_base64_embedded(tmp_path: Path) -> None:
    attack = "</script><script>globalThis.pwned=true</script><img src=x onerror=alert(1)>"
    input_path = tmp_path / "queue.jsonl"
    output_path = tmp_path / "review.html"
    value = row(context=attack, matched_text="<svg onload=alert(2)>")
    write_jsonl(input_path, [value])

    review_ui.build_review_ui(input_path, output_path)
    html = output_path.read_text(encoding="utf-8")

    assert attack not in html
    assert value["matched_text"] not in html
    assert html.count("</script>") == 1
    assert embedded_rows(html) == [value]
    assert ".textContent = value" in html
    assert "innerHTML" not in html


def test_accepts_existing_labels_and_rejects_invalid_label(tmp_path: Path) -> None:
    input_path = tmp_path / "queue.jsonl"
    output_path = tmp_path / "review.html"
    write_jsonl(input_path, [row(label="true_positive")])
    review_ui.build_review_ui(input_path, output_path)
    assert embedded_rows(output_path.read_text(encoding="utf-8"))[0]["label"] == (
        "true_positive"
    )

    write_jsonl(input_path, [row(label="approved")])
    with pytest.raises(review_ui.ReviewUIError, match=r"line 1: label"):
        review_ui.build_review_ui(input_path, output_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.pop("dimension"), "schema"),
        (lambda value: value.update(layer="public"), "layer"),
        (lambda value: value.update(context=""), "context"),
        (lambda value: value.update(allowed_labels=["true_positive"]), "allowed_labels"),
        (lambda value: value.update(review_id="not-a-review-id"), "review_id"),
    ],
)
def test_rejects_schema_errors_without_publishing(
    tmp_path: Path, mutation: object, message: str
) -> None:
    input_path = tmp_path / "queue.jsonl"
    output_path = tmp_path / "review.html"
    value = row()
    mutation(value)  # type: ignore[operator]
    write_jsonl(input_path, [value])

    with pytest.raises(review_ui.ReviewUIError, match=message):
        review_ui.build_review_ui(input_path, output_path)

    assert not output_path.exists()


def test_rejects_malformed_duplicate_fields_and_duplicate_review_ids(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "queue.jsonl"
    output_path = tmp_path / "review.html"
    input_path.write_text("{bad json\n", encoding="utf-8")
    with pytest.raises(review_ui.ReviewUIError, match="malformed JSON"):
        review_ui.build_review_ui(input_path, output_path)

    value = row()
    raw = json.dumps(value, ensure_ascii=False)[:-1] + ',"label":"unreviewed"}\n'
    input_path.write_text(raw, encoding="utf-8")
    with pytest.raises(review_ui.ReviewUIError, match="duplicate object field"):
        review_ui.build_review_ui(input_path, output_path)

    write_jsonl(input_path, [value, value])
    with pytest.raises(review_ui.ReviewUIError, match="duplicate review_id"):
        review_ui.build_review_ui(input_path, output_path)


def test_rejects_same_path_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "queue.jsonl"
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: pytest.fail("read"))

    with pytest.raises(review_ui.ReviewUIError, match="different files"):
        review_ui.build_review_ui(path, path)


def test_rejects_hardlink_without_changing_queue(tmp_path: Path) -> None:
    input_path = tmp_path / "queue.jsonl"
    output_path = tmp_path / "review.html"
    write_jsonl(input_path, [row()])
    original = input_path.read_bytes()
    os.link(input_path, output_path)

    with pytest.raises(review_ui.ReviewUIError, match="different files"):
        review_ui.build_review_ui(input_path, output_path)

    assert input_path.read_bytes() == original


def test_publish_failure_preserves_previous_output_and_cleans_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "queue.jsonl"
    output_path = tmp_path / "review.html"
    write_jsonl(input_path, [row()])
    output_path.write_text("previous output", encoding="utf-8")
    monkeypatch.setattr(
        review_ui.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("private path and data")),
    )

    with pytest.raises(review_ui.ReviewUIError, match="could not be written") as exc:
        review_ui.build_review_ui(input_path, output_path)

    assert "private path and data" not in str(exc.value)
    assert output_path.read_text(encoding="utf-8") == "previous output"
    assert not list(tmp_path.glob(".*.fairpost-temp-*"))


def test_cli_stdout_and_errors_do_not_expose_private_values(tmp_path: Path) -> None:
    input_path = tmp_path / "queue-secret.jsonl"
    output_path = tmp_path / "review-secret.html"
    secret = "private.person@example.com </script>"
    review_id = "c" * 64
    write_jsonl(input_path, [row(review_id=review_id, context=secret)])

    completed = subprocess.run(
        [
            sys.executable,
            "tools/build_private_review_ui.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"rows": 1, "rules": 1}
    for private_value in (secret, review_id, str(input_path), str(output_path)):
        assert private_value not in completed.stdout
        assert private_value not in completed.stderr

    input_path.write_text("{malformed " + secret, encoding="utf-8")
    failed = subprocess.run(
        [
            sys.executable,
            "tools/build_private_review_ui.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert failed.returncode == 1
    for private_value in (secret, review_id, str(input_path), str(output_path)):
        assert private_value not in failed.stdout
        assert private_value not in failed.stderr


def test_help_warns_about_local_only_reidentification_risk() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/build_private_review_ui.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert completed.returncode == 0
    assert "re-identification risk" in completed.stdout
    assert ".private-review" in completed.stdout
    assert "never share" in completed.stdout
