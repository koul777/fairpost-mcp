from __future__ import annotations

import argparse
import asyncio
import codecs
import json
from pathlib import Path
import sys
from typing import Iterable, NoReturn


DEFAULT_ENCODING = "utf-8"
SUBCOMMAND_EPILOG = """\
하위 명령:
  fairpost check [파일 ...]            채용공고를 점검해 JSON으로 출력합니다(check는 생략 가능).
  fairpost web [--port N] [--open-browser]
                                      로컬 화면(http://127.0.0.1:8000/web/)을 실행합니다.
  fairpost purge-answers [--org-id ID]
                                      로컬 답변 저장소의 답변을 삭제합니다.

각 하위 명령의 옵션은 fairpost web --help처럼 확인합니다.
UTF-8 BOM과 UTF-16(BOM 포함) 입력은 자동으로 처리합니다. 다른 인코딩은
--encoding cp949처럼 지정합니다.
"""


class _PurgeArgumentError(Exception):
    pass


class _InputDecodeError(Exception):
    def __init__(self, source: str, encoding: str) -> None:
        super().__init__(source, encoding)
        self.source = source
        self.encoding = encoding


class _PrivacySafePurgeParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise _PurgeArgumentError


def _configure_utf8_output() -> None:
    """Write JSON and messages as UTF-8 even when Windows redirects to cp949.

    A redirected stdout otherwise inherits the ANSI code page, which cannot
    encode characters such as an en dash and aborts after writing nothing.
    """

    for stream, errors in ((sys.stdout, "strict"), (sys.stderr, "backslashreplace")):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors=errors)
        except (OSError, ValueError):
            continue


def _canonical_encoding(encoding: str) -> str:
    return codecs.lookup(encoding).name


def _decode_input(raw: bytes, encoding: str, source: str) -> str:
    """Decode input bytes and drop a byte-order mark so offsets start at 0."""

    try:
        if _canonical_encoding(encoding) == "utf-8":
            if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
                text = raw.decode("utf-16")
            else:
                text = raw.decode("utf-8-sig")
        else:
            text = raw.decode(encoding)
    except UnicodeDecodeError as exc:
        raise _InputDecodeError(source, encoding) from exc
    return text[1:] if text.startswith("﻿") else text


def _read_stdin(encoding: str) -> str:
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:
        text = sys.stdin.read()
        return text[1:] if text.startswith("﻿") else text
    return _decode_input(buffer.read(), encoding, "표준 입력")


def _read_inputs(paths: list[str], encoding: str) -> Iterable[tuple[str, str]]:
    if not paths:
        yield "-", _read_stdin(encoding)
        return
    for raw_path in paths:
        if raw_path == "-":
            yield "-", _read_stdin(encoding)
            continue
        path = Path(raw_path)
        yield str(path), _decode_input(path.read_bytes(), encoding, str(path))


def _normalize_argv(argv: list[str] | None) -> list[str]:
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] == "check":
        return values[1:]
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fairpost",
        description=(
            "FairPost 채용공고 검토 프로그램. fairpost web으로 로컬 화면을 열거나 "
            "채용공고문에서 법령 관련 표현과 검토 질문을 확인합니다."
        ),
        epilog=SUBCOMMAND_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("files", nargs="*", help="점검할 텍스트 파일. 생략하거나 -이면 표준 입력")
    parser.add_argument(
        "--encoding",
        default=DEFAULT_ENCODING,
        help=(
            "입력 파일과 표준 입력의 인코딩(기본 utf-8, BOM 자동 처리). "
            "한글 Windows 메모장의 ANSI 파일은 cp949"
        ),
    )
    parser.add_argument("--data-dir", type=Path, help="규칙 데이터 디렉터리")
    parser.add_argument("--local-rules", type=Path, help="기관 자체 질문 규칙 YAML")
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="단일 입력 결과를 들여쓰기한 JSON으로 출력",
    )
    parser.add_argument(
        "--review-packet",
        action="store_true",
        help=(
            "NCS 공정채용 통제와 선택적 Korean Law MCP 현행 조문 조회를 포함한 "
            "HR 검토 패킷 출력"
        ),
    )
    return parser


def build_purge_parser() -> argparse.ArgumentParser:
    parser = _PrivacySafePurgeParser(
        prog="fairpost purge-answers",
        description="로컬 FairPost 답변 저장소에서 답변을 삭제합니다.",
    )
    parser.add_argument(
        "--org-id",
        help="이 조직의 답변만 삭제합니다. 생략하면 모든 답변을 삭제합니다.",
    )
    return parser


def _purge_answers(argv: list[str]) -> int:
    from mcp_server.storage import LocalAnswerStore

    try:
        args = build_purge_parser().parse_args(argv)
    except _PurgeArgumentError:
        print("fairpost: purge-answers 인자가 올바르지 않습니다", file=sys.stderr)
        return 2
    try:
        changed = LocalAnswerStore().purge(args.org_id)
    except (OSError, ValueError):
        # Do not echo answer-store paths, organization IDs, or malformed contents.
        print("fairpost: 로컬 답변 저장소를 삭제할 수 없습니다", file=sys.stderr)
        return 2
    if changed:
        print("로컬 답변 저장소를 삭제했습니다.")
    else:
        print("삭제할 로컬 답변이 없습니다.")
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_output()
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] == "web":
        try:
            from mcp_server.local_runtime import main as local_main
            return local_main(["web", *values[1:]])
        except ModuleNotFoundError:
            print(
                "fairpost: 로컬 화면에 필요한 패키지가 없습니다. "
                "python -m pip install . 을 먼저 실행하세요.",
                file=sys.stderr,
            )
            return 2
    from core import FairpostEngine, RuleLoadError
    from mcp_server.review import LiveLawVerifier, prepare_hr_review_packet

    if values and values[0] == "purge-answers":
        return _purge_answers(values[1:])
    args = build_parser().parse_args(_normalize_argv(values))
    try:
        _canonical_encoding(args.encoding)
    except LookupError:
        print(
            f"fairpost: 알 수 없는 인코딩입니다: {args.encoding}. "
            "utf-8 또는 cp949처럼 지정하세요.",
            file=sys.stderr,
        )
        return 2
    verifier = None
    if args.review_packet:
        try:
            verifier = LiveLawVerifier.from_environment()
        except ValueError as exc:
            # Configuration messages are fixed strings and never echo secrets.
            print(
                "fairpost: Korean Law MCP 연동 설정이 올바르지 않아 HR 검토 패킷을 "
                f"만들 수 없습니다({exc}). FAIRPOST_KOREAN_LAW_MCP_* 환경변수를 "
                "확인하거나 비우세요.",
                file=sys.stderr,
            )
            return 2
    try:
        engine = FairpostEngine(args.data_dir, args.local_rules)
        inputs = list(_read_inputs(args.files, args.encoding))
    except _InputDecodeError as exc:
        print(
            f"fairpost: {exc.source}을(를) {exc.encoding} 인코딩으로 읽을 수 없습니다. "
            "파일 인코딩을 확인하고 --encoding cp949처럼 지정하세요.",
            file=sys.stderr,
        )
        return 2
    except FileNotFoundError as exc:
        print(f"fairpost: 파일을 찾을 수 없습니다: {exc.filename}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"fairpost: 파일을 읽을 수 없습니다: {exc.filename or exc}", file=sys.stderr)
        return 2
    except RuleLoadError as exc:
        print(f"fairpost: {exc}", file=sys.stderr)
        return 2

    # Validate the whole batch before emitting JSON or starting optional review.
    # Keep the original text intact so finding offsets still address the input.
    for source, text in inputs:
        if not text.strip():
            label = "표준 입력" if source == "-" else source
            print(
                f"fairpost: {label}에 검토할 공고문이 없습니다. "
                "비어 있거나 공백뿐인 입력은 점검할 수 없습니다.",
                file=sys.stderr,
            )
            return 2

    for source, text in inputs:
        if args.review_packet:
            payload = asyncio.run(
                prepare_hr_review_packet(engine, text, verifier=verifier)
            ).to_dict()
        else:
            payload = engine.check(text).to_dict()
        if len(inputs) > 1:
            payload = {"source": source, "result": payload}
        print(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                indent=2 if args.pretty and len(inputs) == 1 else None,
                separators=None if args.pretty and len(inputs) == 1 else (",", ":"),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
