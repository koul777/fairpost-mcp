"""The optimized morph views and matcher must equal the original implementation.

The ``_ref_*`` functions below are verbatim copies of the per-character code
that core/morph.py used before the fast paths were added (commit 08e285f).
Every optimized function is compared with them on adversarial inputs:
compatibility characters, per-character versus whole-string NFKC differences,
zero-width and Unicode whitespace, CRLF, multi-character expansions and morph
rewrite sources at every boundary.
"""

from __future__ import annotations

from functools import lru_cache
import random
import re
import sys
import unicodedata

import pytest

from core import morph
from core.morph import (
    MORPH_REWRITES,
    ZERO_WIDTH,
    SourceMatch,
    _match_plain,
    _match_text_with_offsets,
    _morph_plain,
    _morph_text_with_offsets,
    _normalized_text_with_offsets,
    find_first,
    find_matches,
    pattern_to_regex,
)

# --- reference implementation (verbatim from 08e285f) ------------------------

_REF_REWRITES_BY_INITIAL = {
    initial: tuple(
        rewrite for rewrite in MORPH_REWRITES if rewrite[0][0] == initial
    )
    for initial in {source[0] for source, _target in MORPH_REWRITES}
}


@lru_cache(maxsize=512)
def _ref_morph_text_with_offsets(
    text: str,
) -> tuple[str, tuple[int, ...], tuple[int, ...]]:
    normalized: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    cursor = 0
    while cursor < len(text):
        rewrite = None
        for candidate in _REF_REWRITES_BY_INITIAL.get(text[cursor], ()):
            if text.startswith(candidate[0], cursor):
                rewrite = candidate
                break
        if rewrite is None:
            normalized.append(text[cursor])
            starts.append(cursor)
            ends.append(cursor + 1)
            cursor += 1
            continue
        source, target = rewrite
        source_end = cursor + len(source)
        normalized.extend(target)
        starts.extend([cursor] * len(target))
        ends.extend([source_end] * len(target))
        cursor = source_end
    return "".join(normalized), tuple(starts), tuple(ends)


@lru_cache(maxsize=512)
def _ref_normalized_text_with_offsets(
    text: str,
) -> tuple[str, tuple[int, ...], tuple[int, ...]]:
    normalized: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for cursor, character in enumerate(text):
        if character in ZERO_WIDTH:
            continue
        replacement = (
            character
            if character in {"\r", "\n", "\t"}
            else " "
            if character.isspace()
            else unicodedata.normalize("NFKC", character)
        )
        normalized.extend(replacement)
        starts.extend([cursor] * len(replacement))
        ends.extend([cursor + 1] * len(replacement))
    return "".join(normalized), tuple(starts), tuple(ends)


@lru_cache(maxsize=512)
def _ref_match_text_with_offsets(
    text: str,
) -> tuple[str, tuple[int, ...], tuple[int, ...]]:
    normalized, source_starts, source_ends = _ref_normalized_text_with_offsets(text)
    morph_text, morph_starts, morph_ends = _ref_morph_text_with_offsets(normalized)
    if morph_text == normalized:
        return normalized, source_starts, source_ends
    starts = tuple(source_starts[index] for index in morph_starts)
    ends = tuple(source_ends[index - 1] for index in morph_ends)
    return morph_text, starts, ends


def _ref_match_plain(text: str) -> str:
    normalized, _starts, _ends = _ref_normalized_text_with_offsets(text)
    return _ref_morph_text_with_offsets(normalized)[0]


def _ref_find_matches(
    text: str,
    patterns: list[str],
) -> list[re.Match[str] | SourceMatch]:
    matches: list[re.Match[str] | SourceMatch] = []
    seen: set[tuple[int, int, str]] = set()
    normalized_text, normalized_starts, normalized_ends = (
        _ref_normalized_text_with_offsets(text)
    )
    match_text, match_starts, match_ends = _ref_match_text_with_offsets(text)
    needs_match_view = match_text != text
    for pattern in patterns:
        is_regex = pattern.startswith("re:")
        exact_matches = (
            []
            if is_regex and normalized_text != text
            else list(pattern_to_regex(pattern).finditer(text))
        )
        for match in exact_matches:
            key = (match.start(), match.end(), match.group(0).casefold())
            if key not in seen:
                matches.append(match)
                seen.add(key)
        if is_regex:
            if normalized_text == text:
                continue
            search_text = normalized_text
            search_starts = normalized_starts
            search_ends = normalized_ends
        else:
            if not needs_match_view:
                continue
            search_text = match_text
            search_starts = match_starts
            search_ends = match_ends
        match_pattern = pattern if is_regex else _ref_match_plain(pattern)
        for match in pattern_to_regex(match_pattern).finditer(search_text):
            if match.start() == match.end():
                continue
            start = search_starts[match.start()]
            end = search_ends[match.end() - 1]
            source_match = SourceMatch(text, start, end)
            key = (start, end, source_match.group(0).casefold())
            if key not in seen:
                matches.append(source_match)
                seen.add(key)
    return sorted(matches, key=lambda item: (item.start(), item.end(), item.group(0)))


def _ref_char_replacement(character: str) -> str:
    return _ref_normalized_text_with_offsets(character)[0]


# --- adversarial inputs -------------------------------------------------------

_SOURCES = [source for source, _target in MORPH_REWRITES]
_TARGETS = [target for _source, target in MORPH_REWRITES]
_SPACES = [
    "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x1f", "\x85", "\xa0", "\u1680",
    *(chr(code) for code in range(0x2000, 0x200B)), "\u2028", "\u2029",
    "\u202f", "\u205f", "\u3000",
]
_EXPANSIONS = [
    "\u2026", "\u2025", "\u3231", "\u321c", "\u33a1", "\u2103", "\xbd",
    "\ufb01", "\u2122", "\u2161", "\ufdfa", "\u0344", "\u1e9b", "\u2460",
]
_COMPATIBLE = [
    "\uff21", "\uff29", "\uff10", "\uff12", "\uff5e", "\uff08", "\uff09",
    "\u318d", "\u3147", "\u3131", "\u314f", "\u212b", "\u2126", "\xb2",
    "\u017f", "\xb5", "\xa8", "\u2011", "\u3038", "\u222c", "\uf900",
]
_COMBINING = ["\u0301", "\u0308", "\u0323", "\u302e", "\u3099", "\u309a"]
_JAMO = ["\u1100", "\u1161", "\u11a8", "\u110b", "\u1175", "\u11ab"]
_PLAIN = ["가", "a", "A", " ", "\t", "\n", "\r\n", "1", "\xb7", "\u203b", "\u25cb", "\u967d", "있", "는"]
_ALPHABET = [
    *_SOURCES, *_TARGETS, *_SPACES, *_EXPANSIONS, *_COMPATIBLE, *_COMBINING,
    *_JAMO, *_PLAIN, *sorted(ZERO_WIDTH),
    *(unicodedata.normalize("NFD", source) for source in _SOURCES),
]


def _boundary_samples() -> list[str]:
    samples = {
        "",
        "남성만",
        "지원 자격 요건\r\n남성만\r\n",
        "ai 면접과 \uff21\uff29 평가",
        "\uff12\uff10세 이상",
        "여\u200b성만 지원",
        "\ufeff남성만",
        "\u318d서류 전형\u318d면접",
        "(주)회사 \u321c회사 \u321c하신",
        "\u2026합니다\u2026",
        "\ufdfa이신\ufdfa",
        "합\u200b니다 하\u200d신 있으\ufeff신",
        "합\u3000니다",
        "\u3000\u2028\xa0",
        "\u1100\u1161\u11a8",  # decomposed 각: NFKC per character keeps it apart
        unicodedata.normalize("NFD", "경력이 있으신 분은 지원합니다"),
        "e\u0301 가\u0301 \u0301",
        "A\u030a \u212b \xc5",
        "\ud800 lone surrogate",
        *_SOURCES,
        *(f"앞{source}뒤" for source in _SOURCES),
        *(f"{source}뒤" for source in _SOURCES),
        *(f"앞{source}" for source in _SOURCES),
        *(left + right for left in _SOURCES for right in _SOURCES),
        *(left[:-1] + right for left in _SOURCES for right in _SOURCES),
        *(source[:cut] + "\u200b" + source[cut:] for source in _SOURCES for cut in range(1, len(source))),
        *(source[:cut] + "\uff5e" + source[cut:] for source in _SOURCES for cut in range(1, len(source))),
        *(source + expansion for source in _SOURCES for expansion in _EXPANSIONS),
        *(expansion + source for source in _SOURCES for expansion in _EXPANSIONS),
        *(f"a{space}b" for space in _SPACES),
        *(f"{zero}가{zero}" for zero in ZERO_WIDTH),
        *(character * 3 for character in _ALPHABET),
        "하시는신 합니다합니다 않습니다신 있으신신 하하신 이으신 으신이신",
    }
    return sorted(samples)


def _random_samples(count: int, max_tokens: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    return [
        "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, max_tokens)))
        for _ in range(count)
    ]


def _long_samples() -> list[str]:
    rng = random.Random(20261011)
    plain = "지원 자격은 경력 3년 이상이며, 서류 전형 후 면접을 진행합니다.\n"
    return [
        plain * 120,
        "".join(rng.choice(_ALPHABET) for _ in range(6000)),
        "가" * 5000 + "\u2026" + "합니다" * 300 + "\u200b" * 50,
        ("\u318d" + "있으신 분" + "\u3000") * 400,
    ]


SAMPLES = _boundary_samples() + _random_samples(800, 30, 7) + _long_samples()
# Index ids: raw sample ids would exceed the Windows environment-variable limit.
SAMPLE_IDS = [f"sample-{index}" for index in range(len(SAMPLES))]

PATTERNS = [
    "",
    " ",
    "지원 ",
    " 자격",
    "AI 면접",
    "1:1 면접",
    "ai",
    "AI",
    "남성만",
    "지원 자격",
    "있는",
    "경력이 있는",
    "제출함",
    "지원함",
    "않음",
    "하는 분",
    "한",
    "인",
    "은",
    "(주)",
    "1",
    "...",
    "\u318d",
    "\u119e",
    "\u110b",
    "가\u0301",
    "e\u0301",
    "남\u200b성",
    "\uff52\uff45:\\d",
    "re:\\d+",
    "re:\\d+세",
    "re:남성\\s*만",
    "re:있(?:는|으신)",
    "re:(?=있)",
    "re:a*",
    "re:.",
    "re:\\s+",
    "re:[가-힣]{2}",
    "re:합니다|함",
]


def _describe(matches: list[re.Match[str] | SourceMatch]) -> list[tuple[object, ...]]:
    described: list[tuple[object, ...]] = []
    for match in matches:
        if isinstance(match, SourceMatch):
            described.append(("source", match))
        else:
            described.append(("raw", match.re, match.string, match.span(), match.group(0)))
    return described


# --- tests --------------------------------------------------------------------


def test_safe_character_class_only_contains_unchanged_characters() -> None:
    changed = [
        hex(code)
        for code in range(sys.maxunicode + 1)
        if morph._POSSIBLY_CHANGED_CHARACTER.match(chr(code)) is None
        and _ref_char_replacement(chr(code)) != chr(code)
    ]
    assert changed == []


def test_caseless_chunk_class_only_contains_uncased_characters() -> None:
    # The substring prefilter relies on re.IGNORECASE matching these
    # characters exactly; the regex compiler does that for uncased ones.
    import _sre

    members = [
        chr(code)
        for code in range(sys.maxunicode + 1)
        if morph._CASELESS_CHUNK.fullmatch(chr(code))
    ]
    assert len(members) > 30000
    for character in members:
        assert character.lower() == character == character.upper(), hex(ord(character))
        assert not character.isspace(), hex(ord(character))
        if hasattr(_sre, "unicode_iscased"):
            assert not _sre.unicode_iscased(ord(character)), hex(ord(character))


def test_required_literals_occur_in_every_match() -> None:
    assert morph._required_literals("re:있는") == ()
    assert morph._required_literals("지원  자격") == ("지원", "자격")
    assert morph._required_literals("AI 면접") == ("면접",)
    assert morph._required_literals("") == ()
    literal_patterns = [pattern for pattern in PATTERNS if not pattern.startswith("re:")]
    literal_patterns += [_ref_match_plain(pattern) for pattern in literal_patterns]
    for sample in SAMPLES[:400] + _long_samples():
        for pattern in literal_patterns:
            required = morph._required_literals(pattern)
            for match in pattern_to_regex(pattern).finditer(sample):
                assert all(literal in match.group(0) for literal in required), pattern


def test_normalized_view_matches_reference_across_all_code_points() -> None:
    # Consecutive code points put combining marks, conjoining jamo and
    # expansions next to each other; the per-character NFKC must hold.
    step = 2048
    for first in range(0, sys.maxunicode + 1, step):
        chunk = "".join(chr(code) for code in range(first, min(first + step, sys.maxunicode + 1)))
        assert _normalized_text_with_offsets(chunk) == _ref_normalized_text_with_offsets(chunk), hex(first)


def test_per_character_nfkc_is_not_whole_string_nfkc() -> None:
    decomposed = "\u1100\u1161\u11a8"
    assert unicodedata.normalize("NFKC", decomposed) == "각"
    assert not unicodedata.is_normalized("NFKC", decomposed)
    assert _normalized_text_with_offsets(decomposed) == (decomposed, (0, 1, 2), (1, 2, 3))
    assert _normalized_text_with_offsets("e\u0301") == ("e\u0301", (0, 1), (1, 2))


@pytest.mark.parametrize("sample", SAMPLES, ids=SAMPLE_IDS)
def test_views_match_reference(sample: str) -> None:
    assert _normalized_text_with_offsets(sample) == _ref_normalized_text_with_offsets(sample)
    assert _morph_text_with_offsets(sample) == _ref_morph_text_with_offsets(sample)
    assert _match_text_with_offsets(sample) == _ref_match_text_with_offsets(sample)
    assert _morph_plain(sample) == _ref_morph_text_with_offsets(sample)[0]
    assert _match_plain(sample) == _ref_match_plain(sample)


@pytest.mark.parametrize("sample", SAMPLES, ids=SAMPLE_IDS)
def test_find_matches_matches_reference(sample: str) -> None:
    expected = _ref_find_matches(sample, PATTERNS)
    assert _describe(find_matches(sample, PATTERNS)) == _describe(expected)
    first = find_first(sample, PATTERNS)
    assert _describe([first] if first is not None else []) == _describe(expected[:1])
    for pattern in PATTERNS:
        single = _ref_find_matches(sample, [pattern])
        assert _describe(find_matches(sample, [pattern])) == _describe(single), pattern


def test_find_matches_repeated_calls_hit_cached_views_identically() -> None:
    text = "경력이 있으신 분\u318d\uff0a합니다\u200b" * 50
    expected = _describe(_ref_find_matches(text, PATTERNS))
    for _ in range(3):
        assert _describe(find_matches(text, PATTERNS)) == expected


def test_literal_pattern_folding_to_regex_prefix_fails_only_when_match_view_is_used() -> None:
    # "\uff52\uff45:(" is a literal pattern, but its normalized form "re:(" is compiled
    # as a regex only when the match view is needed; that must stay lazy.
    pattern = "\uff52\uff45:("
    assert find_matches("평범한 문장", [pattern]) == _ref_find_matches("평범한 문장", [pattern]) == []
    for text in ("\uff21\uff29 문장", "제출합니다"):
        with pytest.raises(re.error):
            _ref_find_matches(text, [pattern])
        with pytest.raises(re.error):
            find_matches(text, [pattern])


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        (r"(?:최종|임원)\s*면접(?!관)", ("면접",)),
        (r"(?:면접){0,2}", ()),
        (r"a|면접", ()),
        (r"(면접)+관", ("면접", "관")),
        (r"(?=면접)", ()),
        (r"(?!면접)", ()),
        (r"(?<=면접)", ()),
        (r"\d{2}\s*년생", ("년생",)),
        (r"(?i:면접)", ("면접",)),
        (r"(?>면접)", ("면접",)),
        (r"면접++", ("면", "접")),
        (r"면접*", ("면",)),
        (r"(?:면접관|면접비)", ("면접",)),
        (r"AI면접", ("면접",)),
        (r"(?x) 면 접 ", ("면접",)),
        (r"(면)?(?(1)접|관)", ()),
        (r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", ("@", ".")),
        (r"(", ()),
    ],
)
def test_regex_required_literals(expression: str, expected: tuple[str, ...]) -> None:
    assert morph._regex_required_literals(expression) == expected


_NOTICE_WITHOUT_RESULT_PATTERN = (
    r"re:(?:별도|개별)?[ \t]*(?:통보|통지|안내|연락)[^\r\n]{0,16}"
    r"(?:없이|하지[ \t]*않|없(?:음|습니다))[^\r\n]{0,16}(?:불합격|미합격|탈락)"
)


def test_notice_regex_plan_requires_each_branch_group() -> None:
    is_regex, regex, required, any_of = morph._pattern_plan(_NOTICE_WITHOUT_RESULT_PATTERN)
    assert is_regex
    assert regex is pattern_to_regex(_NOTICE_WITHOUT_RESULT_PATTERN)
    assert required == ()
    assert any_of == (
        ("통보", "통지", "안내", "연락"),
        ("없이", "하지", "없"),
        ("불합격", "미합격", "탈락"),
    )


@pytest.mark.parametrize(
    "expression",
    [
        r"필수(?:통보|안내)",
        r"필수(통보|안내)",
        r"필수(?i:통보|안내)",
        r"필수(?:통보|안내){1,2}",
        r"필수(?:통보|안내)+?",
        r"필수(?:통보|안내)++",
        r"필수(?>통보|안내)",
    ],
)
def test_regex_any_of_in_required_bodies(expression: str) -> None:
    assert morph._regex_required_conditions(expression) == (
        ("필수",),
        (("통보", "안내"),),
    )


@pytest.mark.parametrize(
    "expression",
    [
        r"(?:가|\d+)",
        r"(?:가|)",
        r"(?:가|나?)",
        r"(?:가|(?=나))",
        r"(?:가|(?!나))",
        r"(?:가|(?<=나))",
        r"(?:가|AI)",
        r"(?:가|(나)?(?(1)다|라))",
        r"(?:가|(?:통보|안내){1,2})",
        r"(?:통보|안내)?",
        r"(?:통보|안내){0,2}",
        r"(?=(?:통보|안내))",
        r"(",
    ],
)
def test_regex_any_of_skips_unproven_requirements(expression: str) -> None:
    assert morph._regex_required_conditions(expression) == ((), ())


@pytest.mark.parametrize("prefix", ["모집공고\n", "모집\u200b공고\n"])
@pytest.mark.parametrize("suffix", ["", "통보", "통보 없이", "없이 불합격"])
def test_regex_any_of_skips_long_whitespace_scan(
    prefix: str, suffix: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    is_regex, _regex, required, any_of = morph._pattern_plan(_NOTICE_WITHOUT_RESULT_PATTERN)

    class UnexpectedScan:
        def finditer(self, text: str):
            pytest.fail("필수 문자열 조건이 없으면 정규식 탐색을 생략해야 합니다")

    # Fail immediately if the quadratic scan is reached, keeping this
    # regression fast even when the prefilter is broken. Cover both views
    # and cases where an earlier any-of condition is already satisfied.
    monkeypatch.setattr(
        morph, "_pattern_plan", lambda _pattern: (is_regex, UnexpectedScan(), required, any_of)
    )
    assert find_matches(prefix + " " * 99990 + suffix, [_NOTICE_WITHOUT_RESULT_PATTERN]) == []


@pytest.mark.parametrize("notice", ["통보", "통지", "안내", "연락"])
@pytest.mark.parametrize("negation", ["없이", "하지 않음", "없음", "없습니다"])
@pytest.mark.parametrize("result", ["불합격", "미합격", "탈락"])
def test_notice_regex_any_of_preserves_matches(notice: str, negation: str, result: str) -> None:
    text = f"결과: {notice} {negation} {result}\n별도\t{notice} {negation} {result}"
    patterns = [_NOTICE_WITHOUT_RESULT_PATTERN]
    for sample in (text, text.replace(notice, notice[0] + "\u200b" + notice[1:])):
        expected = _ref_find_matches(sample, patterns)
        assert len(expected) == 2
        assert _describe(find_matches(sample, patterns)) == _describe(expected)


@pytest.mark.parametrize(
    ("pattern", "text"),
    [
        (r"re:(?:가|\d+)", "123"),
        (r"re:(?:가|)", "나"),
        (r"re:(?:가|나?)", "나"),
        (r"re:(?:가|(?=나))", "나"),
        (r"re:(?:가|AI)", "ai Ai AI"),
        (r"re:(?:AI가|면접)", "ai가 Ai가 면접"),
        (r"re:(?:K|면접)", "k K 면접"),
        (r"re:필수(?:통보|안내)", "통보 필수안내 필수통\u200b보"),
        (r"re:(?:통보|안내){1,2}", "통보안내 안내통보"),
        (r"re:(?>통보|안내)", "통보 안내"),
        (r"re:(?:통보|안내)?", "모집공고"),
        (r"re:(?:통보|안내)", "통지 연락"),
        ("지원 자격", "지원\u200b 자격 지원자격"),
    ],
)
def test_regex_any_of_preserves_reference_matches(pattern: str, text: str) -> None:
    assert _describe(find_matches(text, [pattern])) == _describe(_ref_find_matches(text, [pattern]))


_ENGINE_POSTING = """2026년 행정직 채용

자격요건
관련 행정 업무 경력 2년 이상
용모 단정한 20대 지원자 우대
남성 지원자만 응시 가능하며 1995년생 이후 출생자
TOEIC 700점 이상, 만 25세 ~ 35세

전형절차
서류전형 후 최종 면접을 진행합니다
결과는 별도 통보 없이 불합격 처리합니다
제출된 지원서는 반환하지 않습니다

근무조건
연봉 4,000만원, 경조사비 지원 50만원

문의처
인사팀 02-1234-5678 recruit@example.com
"""


def _engine_postings() -> list[str]:
    zero, dot, wide_two = chr(0x200B), chr(0x318D), chr(0xFF12)
    return [
        _ENGINE_POSTING,
        _ENGINE_POSTING.replace("면접", "면" + zero + "접").replace("2년", wide_two + "년"),
        _ENGINE_POSTING.replace("\n", "\r\n").replace(" 후 ", dot),
        _ENGINE_POSTING.replace("합니다", "하십니다").replace("지원자", "지원하신 분"),
    ]


def test_engine_pattern_calls_match_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    from core import FairpostEngine

    calls: list[tuple[str, list[str]]] = []
    original = morph._collect_matches

    def recording(text: str, patterns: list[str]) -> dict:
        calls.append((text, list(patterns)))
        return original(text, patterns)

    monkeypatch.setattr(morph, "_collect_matches", recording)
    engine = FairpostEngine(data_dir=Path(__file__).resolve().parents[1] / "data")
    for posting in _engine_postings():
        engine.check(posting)
    monkeypatch.undo()

    assert len(calls) > 100
    regex_patterns: set[str] = set()
    for text, patterns in calls:
        assert _describe(find_matches(text, patterns)) == _describe(_ref_find_matches(text, patterns))
        regex_patterns.update(pattern for pattern in patterns if pattern.startswith("re:"))
    assert regex_patterns
    # The literals the prefilter requires occur in every match of each rule regex.
    for pattern in sorted(regex_patterns):
        required = morph._regex_required_literals(pattern[3:])
        for text in _engine_postings() + SAMPLES[:300]:
            for match in pattern_to_regex(pattern).finditer(text):
                assert all(literal in match.group(0) for literal in required), pattern
