"""Shortcuts in slot extraction must return what the full computation returns.

``_section_accept_matches`` scans the whole text once to skip sections without
accept matches; for every section it must return exactly the matches
``find_matches(section.text, patterns)`` returns. These tests pin that for the
shipped slot patterns and for patterns chosen to stress the shortcut: matches
across line breaks, regexes with lookarounds or anchors, patterns that match
the empty string, and views changed by normalization or morph rewrites.
Heading helpers skip normalizing a line that holds more letters than a heading
or a compact value can; those answers must equal the full computation too.
"""

from __future__ import annotations

import random
import re
import unicodedata

import pytest

from core import FairpostEngine, extractor
from core.extractor import Section, split_sections
from core.morph import find_matches


def _spans(matches) -> list[tuple[int, int, str]]:
    return [(match.start(), match.end(), match.group(0)) for match in matches]


def _assert_same_as_plain_scan(text: str, patterns: list[str]) -> None:
    sections = split_sections(text)
    aligned = extractor._whitespace_aligned(text, sections)
    assert aligned
    shortcut = extractor._section_accept_matches(text, sections, patterns, aligned)
    plain = [find_matches(section.text, patterns) for section in sections]
    assert [_spans(matches) for matches in shortcut] == [_spans(matches) for matches in plain], (text, patterns)


@pytest.mark.parametrize(
    ("pattern", "context_free"),
    [
        ("전형 절차", True),
        ("  ", False),  # whitespace only: matches the empty string
        ("re:\\d[\\d,]*(?:만)?\\s*원", True),
        ("re:[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}", True),
        ("re:(?:a|(b))\\1?(?(1)c|d)(?>e)f++", True),
        ("re:(?<![\\d-])1[568]\\d{2}-\\d{4}(?![\\d-])", False),
        ("re:면접(?!관)", False),
        ("re:^별도$", False),
        ("re:\\b채용", False),
        ("re:x*", False),
        ("re:(?:)", False),
    ],
)
def test_context_free_classification(pattern: str, context_free: bool) -> None:
    assert extractor._context_free(pattern) is context_free


def test_rewrite_sources_hold_no_whitespace() -> None:
    # The shortcut relies on it; if it ever changes, the plain scan is used.
    assert extractor._REWRITES_AVOID_WHITESPACE


def test_shipped_slot_patterns_match_the_plain_scan() -> None:
    slots = FairpostEngine().ruleset.slots
    texts = [
        "",
        "전형절차\n서류전형 → 면접\n문의처\n인사팀 02-123-4567\n",
        "모집개요\r\n채용 일정은 추후 안내\r\n전형절차\r\n1차 면접 최종 면접관\n",
        "지원자격\n전형\n절차\n　\n접수 기간: 2026. 8. 1. ~ 8. 10.\n유의사항\n합격자 발표 안내합니다\n",
        "＜전형절차＞\n서류​전형 및 인터뷰\n우대사항\n우대합니다\n기타\n채용서류 반환 청구\n",
        "근무조건\n연봉 3,000만 원\x0b보수\x1c규정에 따름\x85문의\n이메일 hr@example.com\n",
    ]
    for text in texts:
        for definition in slots.values():
            patterns = definition.get("accept_patterns", [])
            _assert_same_as_plain_scan(text, patterns)
            for component in definition.get("components", []):
                _assert_same_as_plain_scan(text, component.get("patterns", []))


_PIECES = [
    "전형", "절차", "전형 절차", "가", "가가", " ", "  ", "\t", "\n", "\r\n", "\r", "\x0b", " ",
    "　", "​", "﻿", "ＡＢ", "ab", "AB", "1", "2026.", "합니다", "하신", "않습니다", "니다",
    "자격요건", "전형절차", "우대사항", "문의처", "● 일정", "[유의사항]", "면접", "면접관", "1차 면접",
    "x", "-", "1588-1234", ".", "!", "：",
]
_ADVERSARIAL_PATTERNS = [
    ["전형 절차"],
    ["가 가", "가가"],
    ["re:가\\s*가"],
    ["re:x*"],
    ["  "],
    ["re:(?<!\\n)전형"],
    ["re:절차$", "전형"],
    ["re:^전형", "절차"],
    ["re:\\b면접", "re:(?:최종|\\d\\s*차)\\s*면접(?!관)"],
    ["합니다", "함", "하신", "한"],
    ["re:함", "re:한\\s*가"],
    ["ab", "re:[a-z]+"],
    ["re:(?<![\\d-])1[568]\\d{2}-\\d{4}(?![\\d-])", "2026"],
    ["re:[^\\n.!?]{0,6}절차"],
    ["re:\\s+"],
]


def test_random_texts_match_the_plain_scan() -> None:
    rng = random.Random(20261019)
    for _ in range(400):
        text = "".join(rng.choice(_PIECES) for _ in range(rng.randrange(1, 40)))
        for patterns in _ADVERSARIAL_PATTERNS:
            _assert_same_as_plain_scan(text, patterns)


def test_kept_letters_survive_heading_cleanup_one_to_one() -> None:
    # _heading_name, _heading_like and _same_compact count these letters to
    # skip normalizing a long line; each must stay as exactly one character.
    letters = [chr(code) for code in range(0xAC00, 0xD7A4)] + [chr(code) for code in range(0x41, 0x5B)] + [
        chr(code) for code in range(0x61, 0x7B)
    ]
    pattern = re.compile(f"[{extractor._KEPT_LETTERS}]")
    assert all(pattern.fullmatch(letter) for letter in letters)
    assert sum(1 for code in range(0x110000) if pattern.fullmatch(chr(code))) == len(letters)
    for letter in letters:
        assert unicodedata.normalize("NFKC", letter) == letter
        assert letter not in extractor.ZERO_WIDTH and not letter.isspace()
        assert len(letter.casefold()) == 1
        for decorated in (letter, "#" + letter, letter + ":", "1. " + letter + " ]"):
            assert letter in extractor._HEADING_DECORATION.sub("", decorated)


def _long_lines(rng: random.Random) -> list[str]:
    pieces = ["자격", "요건", " ", "　", ":", "]", "#", "1.", "●", "​", "ＡＢ", "ab", "㈜", "①", "-", ".", "다"]
    lines = []
    for _ in range(300):
        line = "".join(rng.choice(pieces) for _ in range(rng.randrange(1, 60)))
        lines.append(line)
        lines.append(" " * rng.randrange(0, 1200) + line + ":" * rng.randrange(0, 40))
    return lines


def test_long_line_shortcuts_return_the_full_computation() -> None:
    rng = random.Random(1019)
    for line in _long_lines(rng):
        assert extractor._heading_name.__wrapped__(line) == _heading_name_reference(line), repr(line)
        assert extractor._heading_like.__wrapped__(line) == _heading_like_reference(line), repr(line)
        for value in ("자격요건", "자격 요건:", "ab", line[:7], line):
            assert extractor._same_compact(line, value) == (
                extractor._compact.__wrapped__(line) == extractor._compact.__wrapped__(value)
            ), (line, value)


def _cleaned(line: str) -> str:
    return extractor._HEADING_DECORATION.sub("", extractor._normalized_heading(line).strip())


def _heading_name_reference(line: str) -> str | None:
    cleaned = _cleaned(line)
    if not cleaned or len(cleaned) > 30:
        return None
    compact = extractor._WHITESPACE.sub("", cleaned).casefold()
    if compact.endswith("합류여정"):
        return "전형절차"
    return extractor._SECTION_BY_COMPACT_ALIAS.get(compact)


def _heading_like_reference(line: str) -> bool:
    cleaned = _cleaned(line)
    return 0 < len(cleaned) <= 30 and cleaned[-1] not in extractor._SENTENCE_END


def test_sections_that_do_not_tile_the_text_use_the_plain_scan() -> None:
    text = "안내\n전형절차\n서류전형\n면접\n"
    tiled = split_sections(text)
    assert [section.name for section in tiled] == ["전체", "전형절차"]
    assert extractor._whitespace_aligned(text, tiled)
    reordered = list(reversed(tiled))
    inside_a_line = [Section("전체", 0, 5, text[:5]), Section("전형절차", 5, len(text), text[5:])]
    foreign = [Section("전체", 0, len(text), text.replace("면접", "인터"))]
    for sections in (reordered, inside_a_line, foreign, tiled[1:]):
        assert not extractor._whitespace_aligned(text, sections)
        patterns = ["서류전형", "면접", "인터"]
        assert [_spans(m) for m in extractor._section_accept_matches(text, sections, patterns, False)] == [
            _spans(find_matches(section.text, patterns)) for section in sections
        ]
