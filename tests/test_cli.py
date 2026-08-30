from __future__ import annotations

import io
import json
import sys

from cli.main import main
from mcp_server.storage import LocalAnswerStore


def test_check_subcommand_accepts_a_file(tmp_path, capsys) -> None:
    posting = tmp_path / "posting.txt"
    posting.write_text("지원자격: 여성만 지원 가능", encoding="utf-8")

    assert main(["check", str(posting)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert any(item["id"] == "SEX-001" for item in payload["findings"])


def test_check_dash_reads_standard_input(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("지원자격: 여성만 지원 가능"))

    assert main(["check", "-"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert any(item["id"] == "SEX-001" for item in payload["findings"])


def test_check_dash_decodes_raw_standard_input_with_requested_encoding(
    monkeypatch, capsys
) -> None:
    raw = io.BytesIO("지원자격: 여성만 지원 가능".encode("utf-8"))
    stdin = io.TextIOWrapper(raw, encoding="cp949", errors="surrogateescape")
    monkeypatch.setattr(sys, "stdin", stdin)

    assert main(["check", "-", "--encoding", "utf-8"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert any(item["id"] == "SEX-001" for item in payload["findings"])


def test_legacy_direct_file_syntax_remains_supported(tmp_path, capsys) -> None:
    posting = tmp_path / "posting.txt"
    posting.write_text("지원자격: 여성만 지원 가능", encoding="utf-8")

    assert main([str(posting)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert any(item["id"] == "SEX-001" for item in payload["findings"])


def test_purge_answers_removes_all_local_answers(
    tmp_path, monkeypatch, capsys
) -> None:
    path = tmp_path / "answers.json"
    monkeypatch.setenv("FAIRPOST_ANSWERS_PATH", str(path))
    store = LocalAnswerStore(path)
    store.save("private-org-a", "Q-INFO-001", "private answer a")
    store.save("private-org-b", "Q-PROC-001", "private answer b")

    assert main(["purge-answers"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == "Local answer storage purged.\n"
    assert not path.exists()
    assert "private" not in captured.out


def test_purge_answers_can_limit_deletion_to_one_org(
    tmp_path, monkeypatch, capsys
) -> None:
    path = tmp_path / "answers.json"
    monkeypatch.setenv("FAIRPOST_ANSWERS_PATH", str(path))
    store = LocalAnswerStore(path)
    store.save("private-org-a", "Q-INFO-001", "private answer a")
    store.save("private-org-b", "Q-PROC-001", "private answer b")

    assert main(["purge-answers", "--org-id", "private-org-a"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == "Local answer storage purged.\n"
    assert store.get("private-org-a") == {}
    assert store.get("private-org-b") == {"Q-PROC-001": "private answer b"}
    assert "private-org-a" not in captured.out


def test_purge_answers_is_idempotent_when_store_is_missing(
    tmp_path, monkeypatch, capsys
) -> None:
    path = tmp_path / "missing.json"
    monkeypatch.setenv("FAIRPOST_ANSWERS_PATH", str(path))

    assert main(["purge-answers"]) == 0
    first = capsys.readouterr()
    assert main(["purge-answers", "--org-id", "private-org-a"]) == 0
    second = capsys.readouterr()

    assert first.err == second.err == ""
    assert first.out == second.out == "No matching local answer storage found.\n"
    assert not path.exists()


def test_purge_answers_error_does_not_echo_private_inputs(
    tmp_path, monkeypatch, capsys
) -> None:
    path = tmp_path / "answers.json"
    path.write_text("private-org-a private answer", encoding="utf-8")
    monkeypatch.setenv("FAIRPOST_ANSWERS_PATH", str(path))

    assert main(["purge-answers", "--org-id", "private-org-a"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "fairpost: unable to purge local answer storage\n"
    assert "private-org-a" not in captured.err
    assert "private answer" not in captured.err


def test_purge_answers_argument_errors_do_not_echo_private_inputs(capsys) -> None:
    assert main(["purge-answers", "private-org-a"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "fairpost: invalid purge-answers arguments\n"
    assert "private-org-a" not in captured.err
