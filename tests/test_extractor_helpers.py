"""The memoized pure helpers of core.extractor must return what a fresh call returns.

Heading detection runs the same lines through ``_normalized_heading``,
``_heading_name``, ``_compact``, ``_heading_like`` and ``_line_marker`` once per
slot, so they are cached. These tests pin that a cached value is the uncached
value, that the per-character NFKC (not whole-string NFKC) behaviour is kept,
and that the caches stay bounded.
"""

from __future__ import annotations

import unicodedata

import pytest

from core import extractor
from core.extractor import split_sections
from core.morph import ZERO_WIDTH

CACHED_HELPERS = [
    extractor._normalized_heading,
    extractor._heading_name,
    extractor._compact,
    extractor._heading_like,
    extractor._line_marker,
]

SAMPLE_LINES = [
    "",
    "   ",
    "자격요건",
    "● 자격 요건 :",
    "1. 담당업무",
    "[우대사항]",
    "【전형절차】",
    "＜근무조건＞",  # fullwidth angle brackets
    "ＷＨＡＴ　ＹＯＵ’ＬＬ　ＤＯ",  # fullwidth latin and ideographic space
    "채용서류 반환\u318d파기 안내",
    "채용서류 반환·파기 안내",
    "\ufeff자격요건\u200b",
    "- 사무 보조",
    "(1) 인터뷰",
    "※ 상세 업무는 입사 후 결정",
    "합류 여정",
    "㈜페어포스 채용 안내입니다. 연락처는 02-1234-5678 입니다.",
    "x" * 31,
    "문의처\n",
    "문의처\r\n",
]


def _per_character_nfkc(line: str) -> str:
    """The definition the translate table must equal."""
    return "".join(
        unicodedata.normalize("NFKC", char) for char in line if char not in ZERO_WIDTH
    )


def test_normalized_heading_equals_per_character_nfkc_for_every_code_point() -> None:
    every_code_point = "".join(chr(code_point) for code_point in range(0x110000))

    assert extractor._normalized_heading.__wrapped__(every_code_point) == _per_character_nfkc(
        every_code_point
    )


@pytest.mark.parametrize("line", SAMPLE_LINES)
def test_normalized_heading_equals_per_character_nfkc_for_sample_lines(line: str) -> None:
    assert extractor._normalized_heading(line) == _per_character_nfkc(line)


def test_normalized_heading_never_composes_across_characters() -> None:
    # Whole-string NFKC would compose these; the engine and web/engine.js do not.
    for line in ("e\u0301", "\u1100\u1161\u11a8", "A\u030a"):
        assert unicodedata.normalize("NFKC", line) != line
        assert extractor._normalized_heading(line) == line
    assert extractor._normalized_heading("a\u200bb\u200cc\u200dd\ufeffe") == "abcde"
    assert extractor._normalized_heading("㈜") == "(주)"
    assert extractor._normalized_heading("\u318d") == "\u119e"  # U+318D, as in the heading 채용서류 반환ㆍ파기 안내


def test_character_table_is_bounded_but_stays_correct_beyond_its_limit(monkeypatch) -> None:
    monkeypatch.setattr(extractor, "_NFKC_TABLE_MAX_ENTRIES", 3)
    table = extractor._PerCharacterNfkc()
    zero_width, ligature, astral = chr(0x200B), chr(0xFB03), chr(0x1F600)
    line = f"자격{zero_width}요건 ㈜ ＡＢＣ {ligature} 1{astral}"

    for _ in range(2):  # characters past the limit are recomputed, never wrong
        assert line.translate(table) == _per_character_nfkc(line)

    assert len(table) == 3


@pytest.mark.parametrize("helper", CACHED_HELPERS, ids=lambda helper: helper.__name__)
def test_cached_helpers_return_the_uncached_value(helper) -> None:
    helper.cache_clear()
    for _ in range(2):  # the second pass is served from the cache
        for line in SAMPLE_LINES:
            assert helper(line) == helper.__wrapped__(line), repr(line)
    assert helper.cache_info().hits > 0


def test_section_names_do_not_change_when_lines_are_served_from_the_cache() -> None:
    text = "채용 안내\n● 자격 요건 :\n남성만\n【전형절차】\n서류\n우대사항\n자격\n"
    extractor._heading_name.cache_clear()

    first = split_sections(text)
    assert extractor._heading_name.cache_info().hits == 0
    second = split_sections(text)

    assert extractor._heading_name.cache_info().hits > 0
    assert first == second
    assert [section.name for section in first] == ["전체", "자격요건", "전형절차", "우대사항"]


def test_caches_are_bounded_in_entries() -> None:
    helper = extractor._heading_name
    helper.cache_clear()
    for index in range(extractor._STRING_CACHE_ENTRIES + 25):
        helper(f"고유한 줄 {index}")

    info = helper.cache_info()
    assert info.maxsize == extractor._STRING_CACHE_ENTRIES
    assert info.currsize == extractor._STRING_CACHE_ENTRIES
    helper.cache_clear()


def test_lines_longer_than_the_key_limit_are_not_cached() -> None:
    long_line = "가" * (extractor._STRING_CACHE_MAX_CHARS + 1)
    limit_line = "가" * extractor._STRING_CACHE_MAX_CHARS
    for helper in CACHED_HELPERS:
        helper.cache_clear()

        assert helper(long_line) == helper.__wrapped__(long_line)
        assert helper.cache_info().currsize == 0, helper.__name__

        assert helper(limit_line) == helper.__wrapped__(limit_line)
        assert helper.cache_info().currsize == 1, helper.__name__
        helper.cache_clear()


def test_hoisted_constants_equal_what_each_call_used_to_derive() -> None:
    bare = {extractor._compact(term) for term in extractor._BARE_STAGE_TERMS}
    named = [event for event in extractor._STAGE_EVENTS if extractor._compact(event) not in bare]

    assert extractor._DUTY_HEADING_COMPACTS == {
        extractor._compact(heading) for heading in extractor._DUTY_HEADINGS
    }
    assert extractor._BARE_STAGE_COMPACTS == bare
    assert extractor._NAMED_STAGE_EVENTS == named
    assert extractor._STAGE_CONTEXT_PATTERNS == extractor._STAGE_MARKERS + named + ["면접"]
    assert extractor._DATE_CONTENT_NUMERIC == extractor._DATE_CONTENT[:2]
    # Bare stage terms are exactly the ones that are not named stages.
    assert not bare & {extractor._compact(event) for event in named}


def test_precompiled_patterns_behave_like_the_inline_ones() -> None:
    assert extractor._HTML_TAG.sub("", "a<b>c</b >d<") == "acd<"
    assert extractor._ATTACHMENT_FILENAME.search("지원서 양식.PDF ") is not None
    assert extractor._ATTACHMENT_FILENAME.search("지원서 양식.pdf 첨부") is None
    assert extractor._STAGE_ARROW.search("서류 → 면접") is not None
    assert extractor._STAGE_ARROW.search("서류 면접") is None
    assert extractor._ASCII_ALNUM.match("7") is not None
    assert extractor._ASCII_ALNUM.match("가") is None
