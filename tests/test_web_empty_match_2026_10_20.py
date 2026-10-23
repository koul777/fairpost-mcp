"""The web engine scans past empty matches the way Python's re.finditer does.

A pattern that matches the empty string (``re:x*``, a literal made of
whitespace only, a literal of zero-width characters whose match view is empty)
found at a surrogate pair used to stop web/engine.js for ever: the exec loop
moved lastIndex by one code unit, and V8 sets a "u" regex back to the first unit
of a pair. The loop now moves to the next code point. tests/js_empty_match_2026_10_20.cjs
runs findMatches for every (text, pattern list) pair of the file below with a
2 second limit per call; core.morph.find_matches gives the expected spans.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.morph import find_matches
from web_engine import ROOT, run_node

pytestmark = pytest.mark.requires_node

EMOJI = chr(0x1F600)
LONE_HIGH, LONE_LOW = chr(0xD83D), chr(0xDE00)
ZERO_WIDTH = chr(0x200B)
COMBINING_ACUTE = chr(0x0301)
FULLWIDTH_AI = chr(0xFF21) + chr(0xFF29)
IDEOGRAPHIC_SPACE = chr(0x3000)

# Each pattern list must hold three patterns at least in some cases, so the
# list gate and the plan cache see them too.
PATTERN_LISTS: list[list[str]] = [
    ["re:x*"],
    ["re:(?:)"],
    ["re:\\s*"],
    ["re:a|"],
    ["re:(?=a)"],
    ["re:(?<=" + EMOJI + ")"],
    ["re:$"],
    ["re:^"],
    [" "],
    [IDEOGRAPHIC_SPACE],
    [ZERO_WIDTH],
    [ZERO_WIDTH + ZERO_WIDTH],
    ["합니다", "re:x*", "면접"],
    ["면접", "re:a|", ZERO_WIDTH],
    ["AI 면접", " ", "re:\\d*"],
    ["re:x*", "re:(?:)", "re:\\s*", ZERO_WIDTH],
]

TEXTS: list[str] = [
    "",
    "a",
    "a" + EMOJI + "b",
    EMOJI,
    EMOJI + EMOJI,
    EMOJI + "a",
    "a" + EMOJI,
    LONE_HIGH,
    LONE_LOW,
    "a" + LONE_HIGH + "b",
    LONE_LOW + LONE_HIGH,
    EMOJI + LONE_HIGH + EMOJI,
    "e" + COMBINING_ACUTE + "x" + EMOJI,
    ZERO_WIDTH + "a" + ZERO_WIDTH,
    ZERO_WIDTH + EMOJI + ZERO_WIDTH,
    "면접" + ZERO_WIDTH + EMOJI + "합니다",
    FULLWIDTH_AI + "면접" + EMOJI,
    "한글합니다 " + EMOJI + " 하시는",
    "가나다\n" + EMOJI + " 라마",
    "서류 " + EMOJI + " 전형\r\n" + EMOJI + EMOJI,
    # Longer than the 256 units from which scans are kept with the text.
    (EMOJI + "가나다 면접 ") * 60,
    ("a" + ZERO_WIDTH + EMOJI) * 120,
]


def _expected(text: str, patterns: list[str]) -> list[list]:
    return [[match.start(), match.end(), match.group(0)] for match in find_matches(text, patterns)]


def test_empty_matches_end_and_agree_with_python(tmp_path: Path) -> None:
    request = tmp_path / "empty-match-cases.json"
    # ASCII escapes: lone surrogates cannot be written as UTF-8.
    request.write_text(json.dumps({"texts": TEXTS, "patternLists": PATTERN_LISTS}), encoding="ascii")
    output = json.loads(run_node([str(ROOT / "tests/js_empty_match_2026_10_20.cjs"), str(request)]))
    results = output["results"]
    assert len(results) == len(TEXTS) * len(PATTERN_LISTS)
    assert output["slowest"] < 2.0
    index = 0
    empty_spans = 0
    for text in TEXTS:
        for patterns in PATTERN_LISTS:
            expected = _expected(text, patterns)
            assert results[index] == expected, (patterns, text[:40])
            empty_spans += sum(1 for start, end, _ in expected if start == end)
            index += 1
    # The cases do contain empty matches next to surrogate pairs.
    assert empty_spans > 1000
