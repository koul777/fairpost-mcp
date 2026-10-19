"""Per-list plans, gates and the result cache must not change any match.

``_reference_collect`` is the per-pattern loop core/morph.py used before the
pattern list plans were added: it checks every pattern on its own with
``_pattern_plan`` and ``_admits``. The optimized matcher must return the same
match objects in the same order for any list of patterns and any text.
"""

from __future__ import annotations

import random
import re

import pytest

from core import morph
from core.morph import SourceMatch, find_first, find_matches

_PATTERNS = (
    "면접",
    "AI 면접",
    "ai",
    "\uff21\uff29",
    "지원 자격",
    "합니다",
    "하신 분",
    "않습니다",
    "남성",
    "나이 제한",
    "1995년생",
    " ",
    "re:\\d{2}\\s*세",
    "re:(?:통보|안내)\\s*(?:없이|하지)",
    "re:(?:최종|임원)\\s*면접(?!관)",
    "re:[가-힣]{2}자",
    "re:(?:AI|인공지능)",
    "re:남(?:성|자)",
    "re:(?:가|)",
    "re:(?=면접)",
    "re:면접?",
)
_FRAGMENTS = (
    "면접",
    "면\u200b접",
    "AI 면접",
    "\uff21\uff29면접",
    "ai",
    "지원자격",
    "지원 자격",
    "지원\u3000자격",
    "합니다",
    "하십니다",
    "하신 분",
    "않습니다",
    "남성",
    "남자",
    "나이제한",
    "１９９５년생",
    "1995년생",
    "25세",
    "２５ 세",
    "통보 없이",
    "안내하지",
    "최종 면접",
    "임원면접관",
    "지원자",
    "인공지능",
    " ",
    "\n",
    "\t",
    "가",
    "，",
)


def _reference_collect(text: str, patterns: list[str]) -> dict:
    found: dict = {}
    normalized_differs, normalized, match_differs, matched = morph._text_views(text)
    for pattern in patterns:
        is_regex, regex, required, required_any_of = morph._pattern_plan(pattern)
        if is_regex and normalized_differs:
            search_text, search_starts, search_ends = normalized
        else:
            if morph._admits(text, required, required_any_of):
                for match in regex.finditer(text):
                    span = match.span()
                    if span not in found:
                        found[span] = match
            if is_regex or not match_differs:
                continue
            regex, required = morph._match_view_plan(pattern)
            search_text, search_starts, search_ends = matched
        if morph._admits(search_text, required, required_any_of):
            for match in regex.finditer(search_text):
                start, end = match.span()
                if start == end:
                    continue
                span = (search_starts[start], search_ends[end - 1])
                if span not in found:
                    found[span] = SourceMatch(text, *span)
    return found


def _describe(matches: list) -> list[tuple[object, ...]]:
    described: list[tuple[object, ...]] = []
    for match in matches:
        if isinstance(match, SourceMatch):
            described.append(("source", match))
        else:
            described.append(("raw", match.re, match.string, match.span(), match.group(0)))
    return described


def _cases(seed: int, count: int) -> list[tuple[str, list[str]]]:
    generator = random.Random(seed)
    cases = []
    for _ in range(count):
        size = generator.randint(1, 60)
        text = "".join(generator.choice(_FRAGMENTS) for _ in range(size))
        if generator.random() < 0.3:
            # Long texts take the cached path.
            text = text * generator.randint(2, 12)
        patterns = generator.sample(_PATTERNS, generator.randint(1, 6))
        cases.append((text, patterns))
    return cases


@pytest.mark.parametrize("seed", range(4))
def test_list_plan_matches_reference_loop(seed: int) -> None:
    for text, patterns in _cases(seed, 400):
        reference = _reference_collect(text, patterns)
        expected = [reference[span] for span in sorted(reference)]
        # Twice: the second call may come from the result cache.
        for _ in range(2):
            assert _describe(find_matches(text, patterns)) == _describe(expected), (text, patterns)
            first = find_first(text, patterns)
            assert _describe([first] if first is not None else []) == _describe(expected[:1])


def test_gate_closes_only_when_no_pattern_is_admitted() -> None:
    patterns = ["면접", "re:(?:통보|안내)\\s*없이", "지원 자격"]
    assert find_matches("서류 전형 후 합격자 발표" * 20, patterns) == []
    assert [match.group(0) for match in find_matches("지원자격 확인 후 면접", patterns)] == [
        "지원자격",
        "면접",
    ]


def test_unconditional_pattern_keeps_the_gate_open() -> None:
    # "ai" has no caseless literal, so no text can close the gate of its list.
    for text in ("AI", "Ai 활용", "x" * 300 + "aI"):
        assert _describe(find_matches(text, ["면접", "ai"])) == _describe(
            sorted(_reference_collect(text, ["면접", "ai"]).values(), key=lambda m: (m.start(), m.end()))
        )
        assert find_matches(text, ["면접", "ai"])


def test_result_cache_returns_equal_results_for_equal_texts() -> None:
    base = "최종 면접은 별도 통보 없이 진행합니다. " * 10
    patterns = ["re:(?:최종|임원)\\s*면접(?!관)", "합니다"]
    first = _describe(find_matches(base, patterns))
    assert first
    # An equal text built anew gets the same matches as the reference loop.
    copy = "".join(list(base))
    assert copy is not base
    reference = _reference_collect(copy, patterns)
    expected = _describe([reference[span] for span in sorted(reference)])
    assert _describe(find_matches(copy, patterns)) == expected == first


def test_replaced_pattern_plan_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    patterns = ["면접"]
    text = "최종 면접 " * 40
    assert find_matches(text, patterns)

    class NoScan:
        def finditer(self, _text: str):
            return iter(())

    real = morph._pattern_plan
    monkeypatch.setattr(
        morph,
        "_pattern_plan",
        lambda pattern: (lambda plan: (plan[0], NoScan(), plan[2], plan[3]))(real(pattern)),
    )
    assert find_matches(text, patterns) == []
    monkeypatch.undo()
    assert find_matches(text, patterns)


def test_invalid_regex_still_raises_on_every_call() -> None:
    for _ in range(2):
        with pytest.raises(re.error):
            find_matches("면접 " * 50, ["면접", "re:("])
