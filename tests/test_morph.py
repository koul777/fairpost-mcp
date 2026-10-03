"""Behavior-pinning tests for core.morph matching and exclusions."""

from __future__ import annotations

import pytest

from core.morph import SourceMatch, find_first, find_matches, is_excluded


def _spans(text: str, patterns: list[str]) -> list[tuple[int, int, str]]:
    return [
        (match.start(), match.end(), match.group(0))
        for match in find_matches(text, patterns)
    ]


@pytest.mark.parametrize(
    ("text", "patterns", "expected"),
    [
        ("", ["남성"], []),
        ("남성만", [], []),
        ("지원자격 및 지원  자격", ["지원 자격"], [(0, 4, "지원자격"), (7, 13, "지원  자격")]),
        ("지원\n자격", ["지원 자격"], [(0, 5, "지원\n자격")]),
        ("지원\r\n자격", ["지원 자격"], [(0, 6, "지원\r\n자격")]),
        ("자격요건\r\n남성만\r\n", ["남성만"], [(6, 9, "남성만")]),
        ("ai 면접과 AI 평가", ["AI"], [(0, 2, "ai"), (7, 9, "AI")]),
        ("20세 이상", ["re:\\d+세"], [(0, 3, "20세")]),
    ],
    ids=[
        "empty-text",
        "no-patterns",
        "whitespace-tolerant",
        "newline-tolerant",
        "crlf-tolerant",
        "crlf-offsets",
        "case-insensitive",
        "raw-regex",
    ],
)
def test_find_matches_on_plain_text(
    text: str, patterns: list[str], expected: list[tuple[int, int, str]]
) -> None:
    assert _spans(text, patterns) == expected


@pytest.mark.parametrize(
    ("text", "patterns", "expected"),
    [
        ("ai 면접과 ＡＩ 평가", ["AI"], [(0, 2, "ai"), (7, 9, "ＡＩ")]),
        ("２０세 이상", ["re:\\d+세"], [(0, 3, "２０세")]),
        ("여​성만 지원", ["여성"], [(0, 3, "여​성")]),
        ("﻿남성만", ["남성만"], [(1, 4, "남성만")]),
        ("남​성 만", ["남성 만"], [(0, 5, "남​성 만")]),
        ("남​성만", ["re:남성\\s*만"], [(0, 4, "남​성만")]),
    ],
    ids=[
        "nfkc-fullwidth-latin",
        "nfkc-fullwidth-digits-regex",
        "zero-width-space",
        "byte-order-mark",
        "zero-width-whitespace-pattern",
        "zero-width-regex",
    ],
)
def test_find_matches_normalizes_but_reports_source_offsets(
    text: str, patterns: list[str], expected: list[tuple[int, int, str]]
) -> None:
    matches = find_matches(text, patterns)

    assert _spans(text, patterns) == expected
    assert all(text[m.start() : m.end()] == m.group(0) for m in matches)


@pytest.mark.parametrize(
    ("text", "pattern", "expected"),
    [
        ("경력이 있으신 분", "경력이 있는", (0, 7, "경력이 있으신")),
        ("서류를 제출합니다", "제출함", (4, 9, "제출합니다")),
        ("지원하실 수 없습니다", "없습니다", (7, 11, "없습니다")),
    ],
)
def test_find_matches_applies_honorific_rewrites(
    text: str, pattern: str, expected: tuple[int, int, str]
) -> None:
    assert _spans(text, [pattern]) == [expected]


def test_fullwidth_pattern_does_not_match_ascii_text() -> None:
    # Patterns are only NFKC-normalized when the text itself needs it.
    assert find_matches("AI 면접", ["ＡＩ 면접"]) == []


def test_find_matches_sorts_and_deduplicates() -> None:
    assert _spans("남성만 지원", ["남성만", "남성", "남성만"]) == [
        (0, 2, "남성"),
        (0, 3, "남성만"),
    ]
    assert _spans("남성 남성", ["남성", "남성"]) == [(0, 2, "남성"), (3, 5, "남성")]


def test_find_first_returns_earliest_match_or_none() -> None:
    first = find_first("여성 또는 남성", ["남성", "여성"])

    assert first is not None
    assert (first.start(), first.end(), first.group(0)) == (0, 2, "여성")
    assert find_first("무관", ["남성"]) is None


def test_source_match_exposes_only_the_whole_match() -> None:
    match = SourceMatch("여​성만", 0, 3)

    assert match.group() == "여​성"
    with pytest.raises(IndexError):
        match.group(1)


def test_is_excluded_without_exclusions_is_false() -> None:
    assert is_excluded("남성만 지원", 0, 3, []) is False


def test_is_excluded_window_counts_characters_from_candidate() -> None:
    text = "나이 제한은 없음"

    assert [
        is_excluded(text, 0, 2, [{"term": "없음", "window": window}])
        for window in (6, 7)
    ] == [False, True]
    assert is_excluded("제외 남성", 3, 5, [{"term": "제외", "window": 99}]) is True


def test_is_excluded_candidate_filter_skips_unrelated_exclusions() -> None:
    text = "남성만 지원"

    assert (
        is_excluded(text, 0, 3, [{"candidate": "여성", "term": "지원", "window": 10}])
        is False
    )
    assert (
        is_excluded(text, 0, 3, [{"candidate": "남성", "term": "지원", "window": 10}])
        is True
    )


def test_is_excluded_overlap_candidate_requires_overlap() -> None:
    text = "남성만 지원"

    assert (
        is_excluded(
            text, 0, 3, [{"term": "지원", "window": 10, "overlap_candidate": True}]
        )
        is False
    )
    assert (
        is_excluded(
            text, 0, 3, [{"term": "성만", "window": 0, "overlap_candidate": True}]
        )
        is True
    )


def test_is_excluded_sees_normalized_terms() -> None:
    assert is_excluded("남성 제​외", 0, 2, [{"term": "제외", "window": 5}]) is True
    overlapping = [{"term": "AI 미사용", "window": 4, "overlap_candidate": True}]
    assert is_excluded("ＡＩ 미사용", 0, 2, overlapping) is True
    assert is_excluded("ＡＩ 미사", 0, 2, overlapping) is False
