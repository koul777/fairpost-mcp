"""Heading cleanup must preserve the previous output without rescanning runs."""

from __future__ import annotations

import itertools
from pathlib import Path
import random
import re
import subprocess
import sys

import pytest

from core import FairpostEngine, extractor
from web_engine import web_check_batch


_PREVIOUS_DECORATION = re.compile(
    r"^[\s#>*\-–—\d.()①-⑳\[\]■●○◆◇▶▷□◎※【】<]+|[\s:：\[\]】>]+$"
)


def test_heading_decoration_preserves_overlapping_and_interior_runs() -> None:
    # Each class relationship matters: leading-only '#', trailing-only ':',
    # both ']/space', neither '가', and a final newline with Python's '$'.
    for length in range(7):
        for characters in itertools.product("#:] 가\n", repeat=length):
            text = "".join(characters)
            assert extractor._HEADING_DECORATION.sub("", text) == _PREVIOUS_DECORATION.sub("", text), repr(text)


def test_heading_decoration_preserves_unicode_runs() -> None:
    rng = random.Random(20261013)
    characters = "자격요건:#>*-–—9.()①⑳[]■●○◆◇▶▷□◎※【】<： \t\n\r\v\f\x1c\x85\u00a0\u2028\u2029\u3000😀𝟙"
    for _ in range(2000):
        text = "".join(rng.choice(characters) for _ in range(rng.randrange(80)))
        assert extractor._HEADING_DECORATION.sub("", text) == _PREVIOUS_DECORATION.sub("", text), repr(text)


def test_heading_cleanup_finishes_for_a_long_interior_run() -> None:
    # A generous process-level bound catches the former repeated suffix scans
    # without a machine-dependent millisecond assertion or an unbounded test.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from core.extractor import _HEADING_DECORATION; "
            "text = '앞' + ' ' * 100000 + '뒤'; "
            "assert _HEADING_DECORATION.sub('', text) == text",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        timeout=5,
        capture_output=True,
    )


@pytest.mark.requires_node
def test_long_heading_whitespace_preserves_full_engine_results(tmp_path: Path, monkeypatch) -> None:
    texts = [
        "채용서류" + " " * 4000 + "반환합니다.",
        "자격요건\n남성만 지원 가능\n" + "[: \t]" * 300 + ":\n문의처\n인사팀 채용 누리집",
        "담당업무\n경력" + "\u00a0" * 1500 + "무관\n전형절차\n서류 → 면접",
        "#]:\n자격\u200b요건\n여성만 지원 가능",
        "\ufeff＜지원자격＞\n" + "😀" + "\t" * 1500 + "남성만 지원 가능",
    ]
    engine = FairpostEngine()

    def results():
        for helper in (extractor._heading_name, extractor._compact, extractor._heading_like, extractor._duty_block_end_in):
            helper.cache_clear()
        return [engine.check(text).to_dict() for text in texts]

    current = results()
    with monkeypatch.context() as patch:
        patch.setattr(extractor, "_HEADING_DECORATION", _PREVIOUS_DECORATION)
        previous = results()
    assert current == previous
    assert web_check_batch(texts, tmp_path) == current
