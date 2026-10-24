"""Opt-in document input: kordoc JSON flattening, the kordoc wrapper and the CLI wiring.

No test here reads a real posting. Fixtures are small hand-written kordoc JSON
documents, and the wrapper tests run a fake ``dist/cli.js`` under Node that
writes one of them to the ``-o`` path. One optional test runs the real kordoc.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from cli import document_input
from cli.document_input import (
    DocumentInputError,
    KordocRuntime,
    convert_document,
    flatten_kordoc_json,
    locate_kordoc,
)
from cli.main import main

requires_node = pytest.mark.requires_node


# --- fixtures -------------------------------------------------------------------


def _cell(text: str) -> dict:
    return {"text": text, "colSpan": 1, "rowSpan": 1}


def _table(*rows: tuple[str, ...]) -> dict:
    return {
        "type": "table",
        "table": {
            "rows": len(rows),
            "cols": max(len(row) for row in rows),
            "cells": [[_cell(text) for text in row] for row in rows],
            "hasHeader": True,
        },
    }


def _doc(blocks: list[dict], file_type: str = "docx", **extra) -> dict:
    return {
        "success": True,
        "fileType": file_type,
        "markdown": "",
        "blocks": blocks,
        "metadata": {},
        **extra,
    }


POSTING_BLOCKS = [
    {"type": "heading", "level": 1, "text": "2027년 시설관리직 모집"},
    {"type": "paragraph", "text": "모집분야: 시설관리 1명"},
    _table(
        ("지원자격", "해당 분야 경력 2년 이상\n여성만 지원 가능"),
        ("근무조건", "주 5일 근무"),
    ),
    {
        "type": "list",
        "listType": "unordered",
        "text": "전형 절차는 서류와 면접 순서로 진행",
        "children": [
            {
                "type": "list",
                "listType": "unordered",
                "text": "서류 합격자에게만 면접 일정을 안내",
            }
        ],
    },
]
POSTING_TEXT = (
    "2027년 시설관리직 모집\n"
    "모집분야: 시설관리 1명\n"
    "지원자격\n"
    "해당 분야 경력 2년 이상\n"
    "여성만 지원 가능\n"
    "근무조건: 주 5일 근무\n"
    "전형 절차는 서류와 면접 순서로 진행\n"
    "서류 합격자에게만 면접 일정을 안내\n"
)


# --- flatten_kordoc_json --------------------------------------------------------


def test_paragraphs_and_headings_become_one_line_each() -> None:
    document = _doc(
        [
            {"type": "heading", "level": 2, "text": "채용 개요"},
            {"type": "paragraph", "text": "첫째 문단입니다."},
            {"type": "paragraph", "text": "둘째 문단입니다."},
        ]
    )

    assert flatten_kordoc_json(document) == "채용 개요\n첫째 문단입니다.\n둘째 문단입니다.\n"


def test_full_posting_fixture_flattens_to_the_expected_text() -> None:
    assert flatten_kordoc_json(_doc(POSTING_BLOCKS)) == POSTING_TEXT


def test_nested_list_items_follow_their_parent_in_order() -> None:
    document = _doc(
        [
            {
                "type": "list",
                "listType": "ordered",
                "text": "가. 제출 서류",
                "children": [
                    {
                        "type": "list",
                        "listType": "unordered",
                        "text": "이력서",
                        "children": [
                            {"type": "list", "listType": "unordered", "text": "자유 양식"}
                        ],
                    },
                    {"type": "list", "listType": "unordered", "text": "자기소개서"},
                ],
            },
            {"type": "list", "listType": "ordered", "text": "나. 제출 방법"},
        ]
    )

    assert flatten_kordoc_json(document).splitlines() == [
        "가. 제출 서류",
        "이력서",
        "자유 양식",
        "자기소개서",
        "나. 제출 방법",
    ]


def test_heading_cell_with_multi_line_body_becomes_heading_and_body_lines() -> None:
    document = _doc(
        [_table(("우대사항", "관련 자격증 소지자\n영어 사용 가능자"), ("제출서류", "이력서 1부"))]
    )

    assert flatten_kordoc_json(document) == (
        "우대사항\n관련 자격증 소지자\n영어 사용 가능자\n제출서류: 이력서 1부\n"
    )


def test_long_left_cell_is_not_a_heading_so_the_row_stays_on_one_line() -> None:
    document = _doc(
        [_table(("아래 일정은 상황에 따라 바뀔 수 있음", "서류 접수\n면접 진행"))]
    )

    assert flatten_kordoc_json(document) == (
        "아래 일정은 상황에 따라 바뀔 수 있음: 서류 접수 면접 진행\n"
    )


def test_key_value_table_rows_become_colon_lines() -> None:
    document = _doc(
        [_table(("근무지", "서울 본사"), ("근무시간", "09시부터 18시까지"), ("고용형태", "정규직"))]
    )

    assert flatten_kordoc_json(document) == (
        "근무지: 서울 본사\n근무시간: 09시부터 18시까지\n고용형태: 정규직\n"
    )


def test_three_column_rows_are_joined_with_single_spaces() -> None:
    document = _doc([_table(("구분", "일정", "장소"), ("서류", "11월 3일", "온라인"))])

    assert flatten_kordoc_json(document) == "구분 일정 장소\n서류 11월 3일 온라인\n"


def test_empty_cells_are_dropped_before_the_columns_are_counted() -> None:
    document = _doc([_table(("면접", "", "본사"), ("", "", ""), ("", "비고 없음", ""))])

    # A row with two non-empty cells reads "a: b"; an all-empty row stays an empty line.
    assert flatten_kordoc_json(document) == "면접: 본사\n\n비고 없음\n"


def test_pdf_cell_lines_are_glued_back_but_docx_lines_are_not() -> None:
    row = ("자격요건", "해당 분야 경력이 필요하며\n관련 자격증 소지자 우대\n- 영어 가능자")
    docx = flatten_kordoc_json(_doc([_table(row)], "docx"))
    pdf = flatten_kordoc_json(_doc([_table(row)], "pdf"))

    assert docx == "자격요건\n해당 분야 경력이 필요하며\n관련 자격증 소지자 우대\n- 영어 가능자\n"
    # The wrapped first line is joined to the next; a list marker starts a new line.
    assert pdf == "자격요건\n해당 분야 경력이 필요하며 관련 자격증 소지자 우대\n- 영어 가능자\n"


def test_table_caption_is_kept_before_or_after_the_rows() -> None:
    above = _table(("구분", "내용"))
    above["table"]["caption"] = "표 1. 전형 일정"
    below = _table(("구분", "내용"))
    below["table"].update({"caption": "표 2. 비고", "captionBelow": True})

    assert flatten_kordoc_json(_doc([above])) == "표 1. 전형 일정\n구분: 내용\n"
    assert flatten_kordoc_json(_doc([below])) == "구분: 내용\n표 2. 비고\n"


def test_footnote_text_follows_its_block() -> None:
    document = _doc(
        [
            {"type": "paragraph", "text": "응시 연령은 별도 안내함", "footnoteText": "세부 기준은 공고문 참조"},
            {"type": "paragraph", "text": "다음 문단"},
        ]
    )

    assert flatten_kordoc_json(document) == (
        "응시 연령은 별도 안내함\n(주: 세부 기준은 공고문 참조)\n다음 문단\n"
    )


def test_picture_and_separator_blocks_add_no_text() -> None:
    document = _doc(
        [
            {"type": "paragraph", "text": "본문"},
            {"type": "image", "text": "image_001.png", "footnoteText": "그림 설명"},
            {"type": "separator"},
            {"type": "paragraph", "text": "끝"},
        ]
    )

    assert flatten_kordoc_json(document) == "본문\n끝\n"


@pytest.mark.parametrize(
    "block",
    [
        {"type": "formula", "text": "x = 1"},
        {"type": "chart"},
        {"text": "종류 없는 블록"},
    ],
    ids=["formula", "chart", "no-type"],
)
def test_unknown_block_type_raises_instead_of_dropping_content(block: dict) -> None:
    document = _doc([{"type": "paragraph", "text": "앞"}, block])

    with pytest.raises(DocumentInputError, match="알 수 없는 블록 종류"):
        flatten_kordoc_json(document)


def test_unknown_block_inside_children_also_raises() -> None:
    document = _doc(
        [
            {
                "type": "list",
                "listType": "unordered",
                "text": "항목",
                "children": [{"type": "textbox", "text": "숨은 글"}],
            }
        ]
    )

    with pytest.raises(DocumentInputError, match="알 수 없는 블록 종류"):
        flatten_kordoc_json(document)


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"blocks": []},
        {"fileType": "docx", "blocks": {"type": "paragraph"}},
        {"fileType": "docx", "blocks": ["문자열"]},
        {"fileType": "docx", "blocks": [{"type": "paragraph", "text": 3}]},
        {"fileType": "docx", "blocks": [{"type": "table", "table": {}}]},
        {"fileType": "docx", "blocks": [{"type": "table", "table": {"cells": [[{"colSpan": 1}]]}}]},
    ],
    ids=["list", "no-file-type", "blocks-not-list", "block-not-object", "text-not-str", "no-cells", "cell-no-text"],
)
def test_malformed_structure_raises_a_korean_error(document) -> None:
    with pytest.raises(DocumentInputError, match="구조를 해석할 수 없습니다"):
        flatten_kordoc_json(document)


def test_flattening_is_deterministic_and_does_not_modify_its_input() -> None:
    document = _doc(POSTING_BLOCKS + [_table(("구분", "일정", "장소"))], "pdf")
    before = copy.deepcopy(document)

    first = flatten_kordoc_json(document)
    second = flatten_kordoc_json(copy.deepcopy(document))

    assert first == second
    assert document == before
    assert [flatten_kordoc_json(document) for _ in range(5)] == [first] * 5


# --- locating kordoc ------------------------------------------------------------


def _install_fake_cli(root: Path, version: str | None = "9.9.9") -> Path:
    cli_js = root / "node_modules" / "kordoc" / "dist" / "cli.js"
    cli_js.parent.mkdir(parents=True)
    cli_js.write_text("// fake\n", encoding="utf-8")
    if version is not None:
        (root / "node_modules" / "kordoc" / "package.json").write_text(
            json.dumps({"name": "kordoc", "version": version}), encoding="utf-8"
        )
    return cli_js


def test_windows_launcher_resolves_to_the_cli_next_to_it(tmp_path: Path) -> None:
    cli_js = _install_fake_cli(tmp_path)
    shim = tmp_path / "kordoc.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")

    assert document_input._cli_from_shim(str(shim), windows=True) == cli_js


def test_windows_launcher_without_the_package_resolves_to_nothing(tmp_path: Path) -> None:
    shim = tmp_path / "kordoc.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")

    assert document_input._cli_from_shim(str(shim), windows=True) is None


def test_posix_launcher_symlink_is_followed(tmp_path: Path) -> None:
    cli_js = _install_fake_cli(tmp_path / "lib")
    link = tmp_path / "bin" / "kordoc"
    link.parent.mkdir()
    try:
        os.symlink(cli_js, link)
    except (OSError, NotImplementedError):
        pytest.skip("심볼릭 링크를 만들 수 없는 환경입니다")

    assert document_input._cli_from_shim(str(link), windows=False) == cli_js.resolve()


def test_posix_launcher_that_is_not_a_cli_link_resolves_to_nothing(tmp_path: Path) -> None:
    wrapper = tmp_path / "kordoc"
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")

    assert document_input._cli_from_shim(str(wrapper), windows=False) is None


def test_environment_variable_wins_over_path_lookup(tmp_path: Path, monkeypatch) -> None:
    cli_js = _install_fake_cli(tmp_path)
    looked_up: list[str] = []

    def fake_which(name: str, *args, **kwargs):
        looked_up.append(name)
        return "node-from-path" if name == "node" else "/unused/kordoc"

    monkeypatch.setenv(document_input.KORDOC_CLI_ENV, str(cli_js))
    monkeypatch.setattr(document_input.shutil, "which", fake_which)

    runtime = locate_kordoc()

    assert runtime == KordocRuntime(node="node-from-path", cli_js=cli_js.resolve(), version="9.9.9")
    assert "kordoc" not in looked_up


def test_missing_kordoc_gives_an_install_hint(monkeypatch) -> None:
    monkeypatch.delenv(document_input.KORDOC_CLI_ENV, raising=False)
    monkeypatch.setattr(document_input.shutil, "which", lambda *_a, **_k: None)

    with pytest.raises(DocumentInputError) as excinfo:
        locate_kordoc()

    assert "npm install -g kordoc" in str(excinfo.value)
    assert document_input.KORDOC_CLI_ENV in str(excinfo.value)


def test_environment_variable_pointing_nowhere_is_reported(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(document_input.KORDOC_CLI_ENV, str(tmp_path / "없는" / "cli.js"))

    with pytest.raises(DocumentInputError, match="찾을 수 없습니다"):
        locate_kordoc()


def test_missing_node_is_reported(tmp_path: Path, monkeypatch) -> None:
    cli_js = _install_fake_cli(tmp_path)
    monkeypatch.setenv(document_input.KORDOC_CLI_ENV, str(cli_js))
    monkeypatch.setattr(document_input.shutil, "which", lambda *_a, **_k: None)

    with pytest.raises(DocumentInputError, match="Node.js"):
        locate_kordoc()


@pytest.mark.parametrize("version", [None, ""], ids=["no-package-json", "empty-version"])
def test_unreadable_kordoc_version_is_reported(tmp_path: Path, monkeypatch, version) -> None:
    cli_js = _install_fake_cli(tmp_path, version)
    monkeypatch.setenv(document_input.KORDOC_CLI_ENV, str(cli_js))
    monkeypatch.setattr(document_input.shutil, "which", lambda *_a, **_k: "node")

    with pytest.raises(DocumentInputError, match="kordoc"):
        locate_kordoc()


# --- convert_document with a fake kordoc ----------------------------------------

FAKE_CLI = r"""
const fs = require("fs");
const args = process.argv.slice(2);
const mode = process.env.FAKE_KORDOC_MODE || "ok";
const outPath = args[args.indexOf("-o") + 1];
const cache = process.env.KORDOC_MODEL_CACHE || null;
if (process.env.FAKE_KORDOC_RECORD) {
  fs.writeFileSync(
    process.env.FAKE_KORDOC_RECORD,
    JSON.stringify({
      args: args,
      offline: process.env.KORDOC_OFFLINE || null,
      cache: cache,
      cacheEntries: cache && fs.existsSync(cache) ? fs.readdirSync(cache) : null,
      inputExists: fs.existsSync(args[0]),
    }),
    "utf8"
  );
}
if (mode === "hang") {
  setTimeout(function () {}, 60000);
} else if (mode === "fail") {
  process.stdout.write(
    JSON.stringify({ success: false, fileType: "unknown", error: "지원하지 않는 파일 형식입니다.", code: "UNSUPPORTED_FORMAT" })
  );
  process.stderr.write(" FAIL\n  → 지원하지 않는 파일 형식입니다.\n");
  process.exit(1);
} else if (mode === "crash") {
  process.stderr.write("TypeError: boom\n    at somewhere\n");
  process.exit(3);
} else if (mode === "silent") {
  // exit 0 without writing the output file
} else {
  fs.copyFileSync(process.env.FAKE_KORDOC_FIXTURE, outPath);
}
"""


class FakeKordoc:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = tmp_path / "fake-kordoc"
        (self.root / "dist").mkdir(parents=True)
        (self.root / "package.json").write_text(
            json.dumps({"name": "kordoc", "version": "9.9.9"}), encoding="utf-8"
        )
        self.cli_js = self.root / "dist" / "cli.js"
        self.cli_js.write_text(FAKE_CLI, encoding="utf-8")
        self.fixture = tmp_path / "fixture.json"
        self.record_path = tmp_path / "record.json"
        monkeypatch.setenv(document_input.KORDOC_CLI_ENV, str(self.cli_js))
        monkeypatch.setenv("FAKE_KORDOC_FIXTURE", str(self.fixture))
        monkeypatch.setenv("FAKE_KORDOC_RECORD", str(self.record_path))
        self.monkeypatch = monkeypatch

    def respond_with(self, document: dict) -> None:
        self.fixture.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    def mode(self, name: str) -> None:
        self.monkeypatch.setenv("FAKE_KORDOC_MODE", name)

    def record(self) -> dict:
        return json.loads(self.record_path.read_text(encoding="utf-8"))


@pytest.fixture
def fake_kordoc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeKordoc:
    fake = FakeKordoc(tmp_path, monkeypatch)
    fake.respond_with(_doc(POSTING_BLOCKS))
    return fake


def _document_file(tmp_path: Path, name: str = "posting.docx") -> Path:
    path = tmp_path / name
    path.write_bytes(b"placeholder: the fake kordoc never reads this file")
    return path


@requires_node
def test_convert_returns_text_and_provenance_from_kordoc(
    tmp_path: Path, fake_kordoc: FakeKordoc
) -> None:
    text, info = convert_document(_document_file(tmp_path))

    assert text == POSTING_TEXT
    assert info == {
        "source_format": "docx",
        "converter": "kordoc@9.9.9",
        "text_sha256": hashlib.sha256(POSTING_TEXT.encode("utf-8")).hexdigest(),
        "skipped_images": 0,
        "ocr_needed_pages": [],
    }


@requires_node
def test_convert_runs_kordoc_offline_with_a_fresh_empty_model_cache(
    tmp_path: Path, fake_kordoc: FakeKordoc, monkeypatch
) -> None:
    user_cache = tmp_path / "user-model-cache"
    user_cache.mkdir()
    (user_cache / "ppocr-model.onnx").write_bytes(b"model")
    monkeypatch.setenv("KORDOC_MODEL_CACHE", str(user_cache))
    monkeypatch.delenv("KORDOC_OFFLINE", raising=False)
    source = _document_file(tmp_path, "공고 파일 (최종).docx")

    convert_document(source)

    seen = fake_kordoc.record()
    assert seen["offline"] == "1"
    assert seen["cache"] != str(user_cache)
    assert seen["cacheEntries"] == []
    assert seen["inputExists"] is True
    assert seen["args"][0] == str(source.resolve())
    assert seen["args"][1:6] == ["--format", "json", "--no-images", "--silent", "-o"]
    assert not any(arg.startswith("--ocr") or arg == "--formula-ocr" for arg in seen["args"])
    assert not {"watch", "setup", "models"} & set(seen["args"])
    # The temporary output and cache folders are removed, and the caller's
    # environment is left as it was.
    assert not Path(seen["cache"]).exists()
    assert not Path(seen["args"][-1]).exists()
    assert os.environ["KORDOC_MODEL_CACHE"] == str(user_cache)
    assert "KORDOC_OFFLINE" not in os.environ
    assert (user_cache / "ppocr-model.onnx").read_bytes() == b"model"


@requires_node
def test_convert_reports_a_nonzero_exit_with_kordoc_own_message(
    tmp_path: Path, fake_kordoc: FakeKordoc
) -> None:
    fake_kordoc.mode("fail")

    with pytest.raises(DocumentInputError) as excinfo:
        convert_document(_document_file(tmp_path))

    message = str(excinfo.value)
    assert "종료 코드 1" in message
    assert "지원하지 않는 파일 형식입니다." in message


@requires_node
def test_convert_falls_back_to_the_last_stderr_line(
    tmp_path: Path, fake_kordoc: FakeKordoc
) -> None:
    fake_kordoc.mode("crash")

    with pytest.raises(DocumentInputError) as excinfo:
        convert_document(_document_file(tmp_path))

    assert "종료 코드 3" in str(excinfo.value)
    assert "at somewhere" in str(excinfo.value)


@requires_node
def test_convert_refuses_image_only_pdf(tmp_path: Path, fake_kordoc: FakeKordoc) -> None:
    fake_kordoc.respond_with(
        _doc(
            [{"type": "image", "text": "image_001.png", "pageNumber": 1}],
            "pdf",
            isImageBased=True,
            warnings=[{"code": "NEEDS_OCR", "message": "OCR 필요"}],
        )
    )

    with pytest.raises(DocumentInputError, match="스캔본·이미지 PDF는 지원하지 않습니다"):
        convert_document(_document_file(tmp_path, "scan.pdf"))


@requires_node
@pytest.mark.parametrize(
    "document",
    [_doc([]), _doc([{"type": "paragraph", "text": "  \n "}]), _doc([{"type": "image", "text": "image_001.png"}])],
    ids=["no-blocks", "blank-text", "picture-only"],
)
def test_convert_rejects_empty_text(tmp_path: Path, fake_kordoc: FakeKordoc, document) -> None:
    fake_kordoc.respond_with(document)

    with pytest.raises(DocumentInputError, match="변환한 글이 비어 있습니다"):
        convert_document(_document_file(tmp_path))


@requires_node
def test_convert_rejects_a_missing_output_file(tmp_path: Path, fake_kordoc: FakeKordoc) -> None:
    fake_kordoc.mode("silent")

    with pytest.raises(DocumentInputError, match="결과 파일을 만들지 않았습니다"):
        convert_document(_document_file(tmp_path))


@requires_node
def test_convert_times_out_and_cleans_up(tmp_path: Path, fake_kordoc: FakeKordoc) -> None:
    fake_kordoc.mode("hang")

    with pytest.raises(DocumentInputError, match="2초 안에 끝나지 않아"):
        convert_document(_document_file(tmp_path), timeout=2)

    if fake_kordoc.record_path.exists():
        assert not Path(fake_kordoc.record()["cache"]).exists()


@requires_node
def test_convert_refuses_unknown_block_types_from_kordoc(
    tmp_path: Path, fake_kordoc: FakeKordoc
) -> None:
    fake_kordoc.respond_with(_doc([{"type": "paragraph", "text": "본문"}, {"type": "formula", "text": "x"}]))

    with pytest.raises(DocumentInputError, match="알 수 없는 블록 종류"):
        convert_document(_document_file(tmp_path))


@requires_node
def test_convert_counts_pictures_and_pdf_pages_without_a_text_layer(
    tmp_path: Path, fake_kordoc: FakeKordoc
) -> None:
    fake_kordoc.respond_with(
        _doc(
            [
                {"type": "paragraph", "text": "본문 첫 줄"},
                {"type": "image", "text": "image_001.png"},
                {"type": "image", "text": "image_002.png"},
            ],
            "pdf",
            warnings=[
                {"code": "NEEDS_OCR", "page": 3, "message": "글자층 없음"},
                {"code": "NEEDS_OCR", "page": 2, "message": "글자층 없음"},
                {"code": "SKIPPED_IMAGE", "message": "무관"},
            ],
        )
    )

    text, info = convert_document(_document_file(tmp_path, "mixed.pdf"))

    assert text == "본문 첫 줄\n"
    assert info["source_format"] == "pdf"
    assert info["skipped_images"] == 2
    assert info["ocr_needed_pages"] == [2, 3]


@requires_node
@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ({"success": False, "fileType": "docx", "error": "손상된 파일입니다"}, "손상된 파일입니다"),
        (_doc([{"type": "paragraph", "text": "본문"}], "hwp"), "DOCX, HWPX, PDF가 아닙니다"),
        (_doc([{"type": "paragraph", "text": "본문"}], "xlsx"), "DOCX, HWPX, PDF가 아닙니다"),
    ],
    ids=["success-false", "binary-hwp-content", "xlsx-content"],
)
def test_convert_rejects_unexpected_kordoc_results(
    tmp_path: Path, fake_kordoc: FakeKordoc, document, expected
) -> None:
    fake_kordoc.respond_with(document)

    with pytest.raises(DocumentInputError, match=expected):
        convert_document(_document_file(tmp_path))


def test_convert_reports_a_missing_input_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as excinfo:
        convert_document(tmp_path / "없는파일.docx")

    assert "없는파일.docx" in str(excinfo.value.filename)


def test_convert_rejects_unsupported_extension_and_folders(tmp_path: Path) -> None:
    other = tmp_path / "notes.txt"
    other.write_text("본문", encoding="utf-8")

    with pytest.raises(DocumentInputError, match="DOCX, HWPX, PDF"):
        convert_document(other)
    with pytest.raises(DocumentInputError, match="폴더"):
        convert_document(tmp_path)


def test_kordoc_is_started_without_shell_console_stdin_or_ocr(
    tmp_path: Path, monkeypatch
) -> None:
    cli_js = _install_fake_cli(tmp_path / "runtime")
    runtime = KordocRuntime(node="node-exe", cli_js=cli_js, version="9.9.9")
    calls: list[tuple[list[str], dict]] = []

    def fake_run(command, **kwargs):
        calls.append((list(command), kwargs))
        out_path = Path(command[command.index("-o") + 1])
        out_path.write_text(json.dumps(_doc(POSTING_BLOCKS), ensure_ascii=False), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(document_input, "locate_kordoc", lambda: runtime)
    monkeypatch.setattr(document_input.subprocess, "run", fake_run)
    monkeypatch.setenv("SOME_PARENT_SETTING", "kept")
    source = _document_file(tmp_path)

    convert_document(source)

    (command, kwargs), = calls
    assert command[:3] == ["node-exe", str(cli_js), str(source.resolve())]
    assert command[3:8] == ["--format", "json", "--no-images", "--silent", "-o"]
    assert isinstance(command, list) and not kwargs.get("shell")
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["timeout"] == document_input.DEFAULT_TIMEOUT_SECONDS == 120.0
    assert kwargs["env"]["KORDOC_OFFLINE"] == "1"
    assert kwargs["env"]["SOME_PARENT_SETTING"] == "kept"
    assert kwargs["env"]["KORDOC_MODEL_CACHE"]
    if sys.platform == "win32":
        assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert not any(part.startswith("--ocr") for part in command)


def test_encrypted_hwpx_is_refused_before_kordoc_can_start_hancom(
    tmp_path: Path, monkeypatch
) -> None:
    def forbidden(*_args, **_kwargs):
        pytest.fail("암호화된 HWPX는 kordoc을 실행하기 전에 거부해야 합니다")

    monkeypatch.setattr(document_input.subprocess, "run", forbidden)
    monkeypatch.setattr(document_input, "locate_kordoc", forbidden)
    protected = tmp_path / "protected.hwpx"
    with zipfile.ZipFile(protected, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("META-INF/manifest.xml", "<manifest><encryption-data/></manifest>")

    with pytest.raises(DocumentInputError, match="암호화된"):
        convert_document(protected)


@pytest.mark.parametrize("name", ["legacy.docx", "legacy.hwpx", "legacy.pdf"])
def test_binary_office_container_is_refused_before_kordoc_runs(
    tmp_path: Path, monkeypatch, name: str
) -> None:
    def forbidden(*_args, **_kwargs):
        pytest.fail("바이너리 문서는 kordoc을 실행하기 전에 거부해야 합니다")

    monkeypatch.setattr(document_input.subprocess, "run", forbidden)
    monkeypatch.setattr(document_input, "locate_kordoc", forbidden)
    legacy = tmp_path / name
    legacy.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(64))

    with pytest.raises(DocumentInputError, match="바이너리 문서"):
        convert_document(legacy)


# --- the check command ----------------------------------------------------------


def _check(args: list[str], capsys) -> tuple[int, str, str]:
    code = main(args)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _txt(tmp_path: Path, text: str = POSTING_TEXT, name: str = "reference.txt") -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return path


@requires_node
def test_docx_gives_the_same_json_as_the_same_text_in_a_txt_file(
    tmp_path: Path, fake_kordoc: FakeKordoc, capsys
) -> None:
    code, expected, _ = _check([str(_txt(tmp_path))], capsys)
    assert code == 0
    assert any(item["id"] == "SEX-001" for item in json.loads(expected)["findings"])

    code, actual, err = _check([str(_document_file(tmp_path))], capsys)

    assert code == 0
    assert actual == expected
    assert err.startswith("fairpost: ")


@requires_node
def test_converted_file_prints_a_korean_notice_on_stderr_only(
    tmp_path: Path, fake_kordoc: FakeKordoc, capsys
) -> None:
    source = _document_file(tmp_path, "공고.DOCX")

    code, out, err = _check(["check", str(source)], capsys)

    assert code == 0
    json.loads(out)
    assert str(source) in err
    assert "kordoc@9.9.9로 변환해 점검합니다" in err
    assert "인용문과 위치는 원본 파일이 아니라 변환한 글을 기준" in err
    assert "머리글·바닥글이 본문에 섞일 수 있습니다" in err
    assert "--converted-text" in err
    assert "Traceback" not in err


@requires_node
def test_notice_mentions_pictures_and_pages_that_were_not_read(
    tmp_path: Path, fake_kordoc: FakeKordoc, capsys
) -> None:
    fake_kordoc.respond_with(
        _doc(
            POSTING_BLOCKS + [{"type": "image", "text": "image_001.png"}],
            "pdf",
            warnings=[{"code": "NEEDS_OCR", "page": 2, "message": "글자층 없음"}],
        )
    )

    code, _, err = _check([str(_document_file(tmp_path, "posting.pdf"))], capsys)

    assert code == 0
    assert "그림 1개는 글자를 읽지 않았습니다" in err
    assert "PDF 2쪽은 글자층이 없어 읽지 못했습니다" in err


@requires_node
def test_converted_text_option_writes_utf8_text_with_matching_offsets(
    tmp_path: Path, fake_kordoc: FakeKordoc, capsys
) -> None:
    saved = tmp_path / "inspect" / "converted.txt"
    saved.parent.mkdir()

    code, from_document, err = _check(
        [str(_document_file(tmp_path)), "--converted-text", str(saved)], capsys
    )

    assert code == 0
    assert saved.read_bytes() == POSTING_TEXT.encode("utf-8")
    assert b"\r" not in saved.read_bytes()
    assert f"변환한 글을 {saved}에 저장했습니다" in err
    assert "--converted-text 경로를 지정하면" not in err
    # The saved text is exactly what was checked, so the offsets line up.
    code, from_saved, _ = _check([str(saved)], capsys)
    assert code == 0
    assert from_saved == from_document
    finding = next(item for item in json.loads(from_document)["findings"] if item["id"] == "SEX-001")
    assert POSTING_TEXT[slice(*finding["offset"])] == finding["matched_text"]


@requires_node
def test_converted_text_option_reports_an_unwritable_path(
    tmp_path: Path, fake_kordoc: FakeKordoc, capsys
) -> None:
    target = tmp_path / "no-such-folder" / "converted.txt"

    code, out, err = _check([str(_document_file(tmp_path)), "--converted-text", str(target)], capsys)

    assert code == 2
    assert out == ""
    assert "변환한 글을 저장할 수 없습니다" in err


@requires_node
def test_multi_file_output_keeps_the_existing_source_wrapper(
    tmp_path: Path, fake_kordoc: FakeKordoc, capsys
) -> None:
    document = _document_file(tmp_path)
    reference = _txt(tmp_path)

    code, out, _ = _check([str(document), str(reference)], capsys)

    assert code == 0
    first, second = (json.loads(line) for line in out.splitlines())
    assert first["source"] == str(document)
    assert second["source"] == str(reference)
    assert first["result"] == second["result"]


@requires_node
def test_review_packet_works_on_converted_text(
    tmp_path: Path, fake_kordoc: FakeKordoc, capsys
) -> None:
    code, expected, _ = _check([str(_txt(tmp_path)), "--review-packet"], capsys)
    assert code == 0

    code, actual, _ = _check([str(_document_file(tmp_path)), "--review-packet"], capsys)

    assert code == 0
    packet = json.loads(actual)
    assert packet["schema_version"] == "fairpost-hr-review-v1"
    # The packet itself carries timestamps and random event ids; the check inside
    # it is what the converted text must reproduce.
    assert packet["check"] == json.loads(expected)["check"]


@requires_node
def test_kordoc_failure_is_reported_in_korean_with_exit_code_2(
    tmp_path: Path, fake_kordoc: FakeKordoc, capsys
) -> None:
    fake_kordoc.mode("fail")
    source = _document_file(tmp_path, "broken.pdf")

    code, out, err = _check([str(source)], capsys)

    assert code == 2
    assert out == ""
    assert f"fairpost: {source}: kordoc 변환에 실패했습니다(종료 코드 1)" in err
    assert "지원하지 않는 파일 형식입니다." in err
    assert "Traceback" not in err


def test_missing_kordoc_is_reported_with_an_install_hint(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv(document_input.KORDOC_CLI_ENV, raising=False)
    monkeypatch.setattr(document_input.shutil, "which", lambda *_a, **_k: None)

    code, out, err = _check([str(_document_file(tmp_path))], capsys)

    assert code == 2
    assert out == ""
    assert "npm install -g kordoc" in err
    assert "Traceback" not in err


def test_missing_document_file_uses_the_existing_not_found_message(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "없는 공고.docx"

    code, out, err = _check([str(missing)], capsys)

    assert code == 2
    assert out == ""
    assert "파일을 찾을 수 없습니다" in err
    assert "없는 공고.docx" in err


@pytest.mark.parametrize(
    ("name", "phrases"),
    [
        ("posting.hwp", ("바이너리 HWP", "아직 지원하지 않습니다", "HWPX", "PDF")),
        ("POSTING.HWP", ("바이너리 HWP", "HWPX")),
        ("posting.doc", ("구형 Word", "DOCX", "PDF")),
        *[
            (f"scan{suffix}", ("그림 파일은 지원하지 않습니다",))
            for suffix in (".png", ".jpg", ".JPEG", ".webp", ".gif", ".bmp", ".tif", ".tiff")
        ],
    ],
)
def test_hwp_doc_and_image_files_are_refused_in_korean(
    tmp_path: Path, monkeypatch, capsys, name: str, phrases: tuple[str, ...]
) -> None:
    def forbidden(*_args, **_kwargs):
        pytest.fail("지원하지 않는 형식은 변환을 시도하면 안 됩니다")

    monkeypatch.setattr(document_input, "convert_document", forbidden)
    source = tmp_path / name
    source.write_bytes(b"placeholder")

    code, out, err = _check([str(source)], capsys)

    assert code == 2
    assert out == ""
    assert f"fairpost: {source}: " in err
    for phrase in phrases:
        assert phrase in err
    assert "Traceback" not in err


def test_unsupported_file_in_a_batch_stops_everything_before_any_output(
    tmp_path: Path, capsys
) -> None:
    code, out, err = _check([str(_txt(tmp_path)), str(tmp_path / "old.hwp")], capsys)

    assert code == 2
    assert out == ""
    assert "바이너리 HWP" in err


@pytest.mark.parametrize("name", ["posting.txt", "posting.md", "posting", "posting.docx.txt"])
def test_other_files_never_reach_kordoc(tmp_path: Path, monkeypatch, capsys, name: str) -> None:
    def forbidden(*_args, **_kwargs):
        pytest.fail("텍스트 입력 경로에서 kordoc을 호출하면 안 됩니다")

    monkeypatch.setattr(document_input, "convert_document", forbidden)
    monkeypatch.setattr(document_input, "locate_kordoc", forbidden)
    source = _txt(tmp_path, POSTING_TEXT, name)

    code, out, err = _check([str(source)], capsys)

    assert code == 0
    assert err == ""
    assert any(item["id"] == "SEX-001" for item in json.loads(out)["findings"])


def test_standard_input_never_reaches_kordoc(monkeypatch, capsys) -> None:
    import io

    def forbidden(*_args, **_kwargs):
        pytest.fail("표준 입력에서 kordoc을 호출하면 안 됩니다")

    monkeypatch.setattr(document_input, "convert_document", forbidden)
    monkeypatch.setattr(sys, "stdin", io.StringIO(POSTING_TEXT))

    code, out, err = _check(["check", "-"], capsys)

    assert code == 0
    assert err == ""
    assert json.loads(out)["findings"]


def test_converted_text_option_needs_exactly_one_converted_file(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    def forbidden(*_args, **_kwargs):
        pytest.fail("옵션 검증이 변환보다 먼저 끝나야 합니다")

    monkeypatch.setattr(document_input, "convert_document", forbidden)
    target = tmp_path / "converted.txt"
    first = _document_file(tmp_path, "first.docx")
    second = _document_file(tmp_path, "second.pdf")

    code, out, err = _check([str(first), str(second), "--converted-text", str(target)], capsys)
    assert (code, out) == (2, "")
    assert "변환하는 파일이 하나일 때만" in err

    code, out, err = _check([str(_txt(tmp_path)), "--converted-text", str(target)], capsys)
    assert (code, out) == (2, "")
    assert "DOCX, HWPX, PDF 파일을 변환할 때만" in err

    code, out, err = _check([str(first), "--converted-text", str(first)], capsys)
    assert (code, out) == (2, "")
    assert "입력 파일과 같은 경로" in err
    assert not target.exists()
    assert first.read_bytes().startswith(b"placeholder")


def test_help_describes_document_input(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    text = capsys.readouterr().out
    assert "--converted-text" in text
    assert "kordoc" in text
    assert "npm install -g kordoc" in text


# --- optional: real kordoc ------------------------------------------------------


def test_real_kordoc_gives_the_same_check_result_as_the_text_path(tmp_path: Path, capsys) -> None:
    docx = pytest.importorskip("docx", reason="python-docx가 없습니다")
    try:
        locate_kordoc()
    except DocumentInputError:
        pytest.skip("kordoc과 Node.js가 설치된 환경에서만 실행합니다")

    reference = (
        "2027년 물류관리직 모집\n"
        "모집분야: 물류관리 2명\n"
        "지원자격\n"
        "해당 분야 경력 3년 이상\n"
        "여성만 지원 가능\n"
        "접수 기간: 2027. 3. 2. ~ 3. 13.\n"
        "서류전형 후 면접전형으로 선발\n"
    )
    document = docx.Document()
    for line in reference.splitlines()[:2]:
        document.add_paragraph(line)
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "지원자격"
    table.rows[0].cells[1].text = "해당 분야 경력 3년 이상"
    table.rows[0].cells[1].add_paragraph("여성만 지원 가능")
    for line in reference.splitlines()[5:]:
        document.add_paragraph(line)
    source = tmp_path / "synthetic.docx"
    document.save(str(source))
    reference_path = _txt(tmp_path, reference)

    code, expected, _ = _check([str(reference_path)], capsys)
    assert code == 0
    code, actual, err = _check([str(source)], capsys)

    assert code == 0, err
    assert json.loads(actual) == json.loads(expected)
    assert any(item["id"] == "SEX-001" for item in json.loads(actual)["findings"])
    assert shutil.which("node") is not None
