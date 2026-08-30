from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Iterable

from core import FairpostEngine, RuleLoadError


def _read_stdin(encoding: str) -> str:
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:
        return sys.stdin.read()
    return buffer.read().decode(encoding)


def _read_inputs(paths: list[str], encoding: str) -> Iterable[tuple[str, str]]:
    if not paths:
        yield "-", _read_stdin(encoding)
        return
    for raw_path in paths:
        if raw_path == "-":
            yield "-", _read_stdin(encoding)
            continue
        path = Path(raw_path)
        yield str(path), path.read_text(encoding=encoding)


def _normalize_argv(argv: list[str] | None) -> list[str]:
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] == "check":
        return values[1:]
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fairpost",
        description="채용공고문에서 법령 관련 표현과 함께 검토할 질문을 확인합니다.",
    )
    parser.add_argument("files", nargs="*", help="점검할 UTF-8 텍스트 파일. 생략하면 표준입력")
    parser.add_argument(
        "--encoding",
        default="utf-8",
        help="입력 파일과 표준 입력 인코딩",
    )
    parser.add_argument("--data-dir", type=Path, help="규칙 데이터 디렉터리")
    parser.add_argument("--local-rules", type=Path, help="기관 자체 질문 규칙 YAML")
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="단일 입력 결과를 들여쓰기한 JSON으로 출력",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(_normalize_argv(argv))
    try:
        engine = FairpostEngine(args.data_dir, args.local_rules)
        inputs = list(_read_inputs(args.files, args.encoding))
    except (OSError, UnicodeError, RuleLoadError) as exc:
        print(f"fairpost: {exc}", file=sys.stderr)
        return 2

    for source, text in inputs:
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
