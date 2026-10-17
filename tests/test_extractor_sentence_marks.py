"""Sentence marks must equal the original per-character scan for every window."""

from __future__ import annotations

import random

import pytest

from core import extractor
from core.extractor import _ASCII_ALNUM, _SENTENCE_BOUNDARIES


def _reference_marks(text: str, left: int, right: int) -> list[int]:
    # Numeric enumeration and dotted dates are part of the evidence, not sentences.
    return [i for i in range(left, right) if text[i] in _SENTENCE_BOUNDARIES
            and not (text[i] == "." and i > 0 and (text[i - 1].isdecimal()
                     or (i + 1 < len(text) and _ASCII_ALNUM.match(text[i - 1]) and _ASCII_ALNUM.match(text[i + 1]))))]


@pytest.mark.parametrize(
    "text,left,right,expected",
    [
        ("", 0, 0, []),
        (".", 0, 1, [0]),
        (".a", 0, 1, [0]),
        ("0.", 0, 2, []),
        ("9.", 1, 2, []),  # Lookbehind sees the decimal digit before left.
        ("a.b", 0, 3, []),
        ("v1.2", 0, 4, []),
        ("a.b", 0, 2, []),  # Lookahead still sees the ASCII letter at right.
        ("a.9", 0, 2, []),
        ("a.b", 1, 2, []),  # Both neighbours are outside the window.
        ("a.", 0, 2, [1]),
        ("가.", 0, 2, [1]),
        ("a. ", 0, 2, [1]),
        ("a.가", 0, 2, [1]),
        ("가.b", 0, 2, [1]),
        ("a.\U0001D7CF", 0, 2, [1]),
        ("。！？", 0, 3, [0, 1, 2]),
        ("9。！？", 0, 4, [1, 2, 3]),
        ("٣.", 0, 2, []),
        ("\U0001D7CF.", 0, 2, []),
        (".!?。！？", 0, 6, [0, 1, 2, 3, 4, 5]),
        (".!?", 0, 2, [0, 1]),  # A boundary at right is excluded.
        (".", 0, 0, []),
        (".!", 1, 1, []),
        (".!?", 2, 1, []),
        (".!?", 3, 0, []),
    ],
)
def test_sentence_marks_boundary_cases(
    text: str, left: int, right: int, expected: list[int]
) -> None:
    reference = _reference_marks(text, left, right)
    assert reference == expected
    assert extractor._sentence_marks(text, left, right) == reference


def test_sentence_marks_equal_reference_for_random_windows() -> None:
    rng = random.Random(20261016)
    alphabet = list(".!?。！？aZ09٣ 가\n") + ["\U0001D7CF"]

    for _ in range(20_000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(13)))
        left = rng.randrange(len(text) + 1)
        right = rng.randrange(len(text) + 1)
        assert extractor._sentence_marks(text, left, right) == _reference_marks(
            text, left, right
        ), (text, left, right)


def test_sentence_mark_character_class_equals_sentence_boundaries() -> None:
    pattern = extractor._SENTENCE_MARK.pattern
    assert pattern.startswith("[")
    assert set(pattern[1:pattern.index("]")]) == set(extractor._SENTENCE_BOUNDARIES)
