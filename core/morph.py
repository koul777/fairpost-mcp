from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
import unicodedata

MORPH_VERSION = "local-v4-nfkc-whitespace-zero-width-source-offset"
MORPH_REWRITES: tuple[tuple[str, str], ...] = (
    ("않습니다", "않음"),
    ("있으신", "있는"),
    ("하시는", "하는"),
    ("합니다", "함"),
    ("하신", "한"),
    ("이신", "인"),
    ("으신", "은"),
)
ZERO_WIDTH = frozenset({"\u200b", "\u200c", "\u200d", "\ufeff"})


@dataclass(frozen=True)
class SourceMatch:
    source: str
    start_offset: int
    end_offset: int

    def start(self) -> int:
        return self.start_offset

    def end(self) -> int:
        return self.end_offset

    def group(self, index: int = 0) -> str:
        if index != 0:
            raise IndexError("SourceMatch는 전체 일치만 제공합니다")
        return self.source[self.start_offset : self.end_offset]


def normalize(text: str) -> str:
    """Keep the source byte-for-character stable so returned offsets remain original."""
    return text


def pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Turn dictionary patterns into deterministic, whitespace-tolerant regexes."""
    if pattern.startswith("re:"):
        return re.compile(pattern[3:], re.IGNORECASE)
    chunks = re.split(r"(\s+)", unicodedata.normalize("NFC", pattern))
    expression = "".join(r"\s*" if chunk.isspace() else re.escape(chunk) for chunk in chunks)
    return re.compile(expression, re.IGNORECASE)


@lru_cache(maxsize=512)
def _morph_text_with_offsets(text: str) -> tuple[str, tuple[int, ...], tuple[int, ...]]:
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


def _morph_plain(text: str) -> str:
    return _morph_text_with_offsets(text)[0]


@lru_cache(maxsize=512)
def _normalized_text_with_offsets(
    text: str,
) -> tuple[str, tuple[int, ...], tuple[int, ...]]:
    normalized: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for cursor, character in enumerate(text):
        if character in ZERO_WIDTH:
            continue
        replacement = (
            " " if character.isspace() else unicodedata.normalize("NFKC", character)
        )
        normalized.extend(replacement)
        starts.extend([cursor] * len(replacement))
        ends.extend([cursor + 1] * len(replacement))
    return "".join(normalized), tuple(starts), tuple(ends)


@lru_cache(maxsize=512)
def _match_text_with_offsets(
    text: str,
) -> tuple[str, tuple[int, ...], tuple[int, ...]]:
    normalized, source_starts, source_ends = _normalized_text_with_offsets(text)
    morph_text, morph_starts, morph_ends = _morph_text_with_offsets(normalized)
    if morph_text == normalized:
        return normalized, source_starts, source_ends
    starts = tuple(source_starts[index] for index in morph_starts)
    ends = tuple(source_ends[index - 1] for index in morph_ends)
    return morph_text, starts, ends


def _match_plain(text: str) -> str:
    normalized, _starts, _ends = _normalized_text_with_offsets(text)
    return _morph_plain(normalized)


def find_first(
    text: str,
    patterns: list[str],
) -> re.Match[str] | SourceMatch | None:
    matches = find_matches(text, patterns)
    return matches[0] if matches else None


def find_matches(
    text: str,
    patterns: list[str],
) -> list[re.Match[str] | SourceMatch]:
    matches: list[re.Match[str] | SourceMatch] = []
    seen: set[tuple[int, int, str]] = set()
    match_text, match_starts, match_ends = _match_text_with_offsets(text)
    needs_match_view = match_text != text
    for pattern in patterns:
        exact_matches = list(pattern_to_regex(pattern).finditer(text))
        for match in exact_matches:
            key = (match.start(), match.end(), match.group(0).casefold())
            if key not in seen:
                matches.append(match)
                seen.add(key)
        if pattern.startswith("re:"):
            continue
        if not needs_match_view:
            continue
        match_pattern = _match_plain(pattern)
        for match in pattern_to_regex(match_pattern).finditer(match_text):
            if match.start() == match.end():
                continue
            start = match_starts[match.start()]
            end = match_ends[match.end() - 1]
            source_match = SourceMatch(text, start, end)
            key = (start, end, source_match.group(0).casefold())
            if key not in seen:
                matches.append(source_match)
                seen.add(key)
    return sorted(matches, key=lambda item: (item.start(), item.end(), item.group(0)))


def is_excluded(text: str, start: int, end: int, excludes: list[dict]) -> bool:
    for exclusion in excludes:
        window = int(exclusion.get("window", 0))
        left = max(0, start - window)
        right = min(len(text), end + window)
        if find_first(text[left:right], [str(exclusion["term"])]):
            return True
    return False
