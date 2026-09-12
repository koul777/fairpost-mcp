from __future__ import annotations

import unicodedata

from core.morph import (
    MORPH_REWRITES,
    _morph_text_with_offsets,
    pattern_to_regex,
)


def _reference_morph_text_with_offsets(
    text: str,
) -> tuple[str, tuple[int, ...], tuple[int, ...]]:
    """Straightforward reference for the optimized first-character dispatch."""
    normalized: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    cursor = 0
    while cursor < len(text):
        rewrite = next(
            (
                (source, target)
                for source, target in MORPH_REWRITES
                if text.startswith(source, cursor)
            ),
            None,
        )
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


def test_morph_initial_dispatch_matches_reference_for_boundaries_and_pairs() -> None:
    sources = [source for source, _target in MORPH_REWRITES]
    samples = {
        "",
        "rewrite prefix가 없는 일반 문장",
        *(source for source in sources),
        *(f"앞{source}뒤" for source in sources),
        *(left + right for left in sources for right in sources),
        *(unicodedata.normalize("NFD", source) for source in sources),
    }

    for sample in samples:
        assert _morph_text_with_offsets(sample) == (
            _reference_morph_text_with_offsets(sample)
        )


def test_pattern_regex_cache_is_bounded_and_reuses_compiled_pattern() -> None:
    pattern_to_regex.cache_clear()
    try:
        first = pattern_to_regex("지원 자격")
        second = pattern_to_regex("지원 자격")
        cache = pattern_to_regex.cache_info()

        assert first is second
        assert cache.maxsize == 1024
        assert cache.currsize == 1
        assert cache.misses == 1
        assert cache.hits == 1
    finally:
        pattern_to_regex.cache_clear()
