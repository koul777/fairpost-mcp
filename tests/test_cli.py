from __future__ import annotations

import io
import json
import sys

from cli.main import main


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
