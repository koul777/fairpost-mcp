from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cli.main import main
from mcp_server.storage import LocalAnswerStore


ROOT = Path(__file__).resolve().parents[1]
POSTING = "지원자격: 여성만 지원 가능"


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


def test_review_packet_includes_ncs_guidance_and_safe_law_requests(
    tmp_path, monkeypatch, capsys
) -> None:
    posting = tmp_path / "posting.txt"
    posting.write_text("지원자격: 여성만 지원 가능", encoding="utf-8")
    for name in (
        "FAIRPOST_KOREAN_LAW_MCP_URL",
        "FAIRPOST_KOREAN_LAW_MCP_COMMAND",
    ):
        monkeypatch.delenv(name, raising=False)

    assert main([str(posting), "--review-packet"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "fairpost-hr-review-v1"
    assert payload["law_mcp_transport"] == "disabled"
    assert payload["law_verifications"][0]["status"] == "not_configured"
    assert payload["law_verifications"][0]["request"]["posting_text_sent"] is False
    assert payload["ncs_guidance"]["authority"] == "guidance_not_law"


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
    assert captured.out == "로컬 답변 저장소를 삭제했습니다.\n"
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
    assert captured.out == "로컬 답변 저장소를 삭제했습니다.\n"
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
    assert first.out == second.out == "삭제할 로컬 답변이 없습니다.\n"
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
    assert captured.err == "fairpost: 로컬 답변 저장소를 삭제할 수 없습니다\n"
    assert "private-org-a" not in captured.err
    assert "private answer" not in captured.err


def test_purge_answers_argument_errors_do_not_echo_private_inputs(capsys) -> None:
    assert main(["purge-answers", "private-org-a"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "fairpost: purge-answers 인자가 올바르지 않습니다\n"
    assert "private-org-a" not in captured.err


def test_web_subcommand_forwards_reader_options(monkeypatch) -> None:
    from mcp_server import local_runtime
    calls = []
    monkeypatch.setattr(local_runtime, "main", lambda args: calls.append(args) or 0)

    assert main(["web", "--port", "8123", "--open-browser"]) == 0
    assert calls == [["web", "--port", "8123", "--open-browser"]]


def test_web_subcommand_preserves_startup_failure_status(monkeypatch) -> None:
    from mcp_server import local_runtime
    monkeypatch.setattr(local_runtime, "main", lambda _args: 2)
    assert main(["web"]) == 2


def test_web_missing_dependencies_gives_install_instruction(monkeypatch, capsys) -> None:
    from mcp_server import local_runtime

    def missing(_args):
        raise ModuleNotFoundError("private dependency path")

    monkeypatch.setattr(local_runtime, "main", missing)
    assert main(["web"]) == 2
    message = capsys.readouterr().err
    assert "python -m pip install ." in message
    assert "private dependency path" not in message


def _run_cli(
    args: list[str], *, stdin: bytes = b"", env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    environment = dict(os.environ)
    for name in ("FAIRPOST_KOREAN_LAW_MCP_URL", "FAIRPOST_KOREAN_LAW_MCP_COMMAND"):
        environment.pop(name, None)
    environment.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "cli.main", *args],
        cwd=ROOT,
        input=stdin,
        env=environment,
        capture_output=True,
        timeout=120,
    )


def test_redirected_output_is_utf8_even_with_a_legacy_console_code_page() -> None:
    # Windows redirects stdout with the ANSI code page (cp949 on Korean
    # systems), which cannot encode an en dash.
    completed = _run_cli([], stdin="지원자격: 만 25–35세".encode("utf-8"),
                         env={"PYTHONIOENCODING": "cp949"})

    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    payload = json.loads(completed.stdout.decode("utf-8"))
    assert any(item["id"].startswith("AGE-") for item in payload["findings"])
    assert completed.stderr == b""


def test_help_lists_every_subcommand() -> None:
    completed = _run_cli(["--help"], env={"PYTHONIOENCODING": "cp949"})

    assert completed.returncode == 0
    text = completed.stdout.decode("utf-8")
    for subcommand in ("fairpost check", "fairpost web", "fairpost purge-answers"):
        assert subcommand in text
    assert "--encoding cp949" in text


def test_purge_help_is_korean(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["purge-answers", "--help"])

    assert exc.value.code == 0
    text = capsys.readouterr().out
    assert "로컬 FairPost 답변 저장소" in text
    assert "모든 답변을 삭제" in text


@pytest.mark.parametrize("via_stdin", [False, True], ids=["file", "stdin"])
def test_utf8_bom_does_not_shift_offsets(tmp_path, monkeypatch, capsys, via_stdin) -> None:
    plain = tmp_path / "plain.txt"
    plain.write_bytes(POSTING.encode("utf-8"))
    assert main([str(plain)]) == 0
    expected = json.loads(capsys.readouterr().out)

    with_bom = b"\xef\xbb\xbf" + POSTING.encode("utf-8")
    if via_stdin:
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(with_bom)))
        assert main(["-"]) == 0
    else:
        bom_file = tmp_path / "bom.txt"
        bom_file.write_bytes(with_bom)
        assert main([str(bom_file)]) == 0
    actual = json.loads(capsys.readouterr().out)

    finding = next(item for item in actual["findings"] if item["id"] == "SEX-001")
    assert finding["offset"] == next(
        item["offset"] for item in expected["findings"] if item["id"] == "SEX-001"
    )
    assert POSTING[slice(*finding["offset"])] == finding["matched_text"]
    assert actual == expected


def test_utf16_file_with_bom_is_detected(tmp_path, capsys) -> None:
    # Windows PowerShell 5.1 redirection writes UTF-16LE with a BOM.
    posting = tmp_path / "utf16.txt"
    posting.write_bytes(POSTING.encode("utf-16"))

    assert main([str(posting)]) == 0

    payload = json.loads(capsys.readouterr().out)
    finding = next(item for item in payload["findings"] if item["id"] == "SEX-001")
    assert POSTING[slice(*finding["offset"])] == finding["matched_text"]


def test_cp949_file_needs_encoding_option_and_reports_it_in_korean(
    tmp_path, capsys
) -> None:
    posting = tmp_path / "ansi.txt"
    posting.write_bytes(POSTING.encode("cp949"))

    assert main([str(posting)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "utf-8 인코딩으로 읽을 수 없습니다" in captured.err
    assert "--encoding cp949" in captured.err
    assert "codec" not in captured.err

    assert main([str(posting), "--encoding", "cp949"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert any(item["id"] == "SEX-001" for item in payload["findings"])


def test_unknown_encoding_and_missing_file_are_reported_in_korean(
    tmp_path, capsys
) -> None:
    posting = tmp_path / "posting.txt"
    posting.write_text(POSTING, encoding="utf-8")

    assert main([str(posting), "--encoding", "no-such-codec"]) == 2
    assert "알 수 없는 인코딩" in capsys.readouterr().err

    assert main([str(tmp_path / "missing.txt")]) == 2
    message = capsys.readouterr().err
    assert "파일을 찾을 수 없습니다" in message
    assert "Errno" not in message


def test_review_packet_with_invalid_law_settings_exits_cleanly(
    tmp_path, monkeypatch, capsys
) -> None:
    posting = tmp_path / "posting.txt"
    posting.write_text(POSTING, encoding="utf-8")
    monkeypatch.delenv("FAIRPOST_KOREAN_LAW_MCP_COMMAND", raising=False)
    monkeypatch.setenv("FAIRPOST_KOREAN_LAW_MCP_URL", "http://law.example/mcp")

    assert main([str(posting), "--review-packet"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Korean Law MCP 연동 설정이 올바르지 않아" in captured.err
    assert "Traceback" not in captured.err
