from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import lru_cache
import re
import unicodedata

try:  # Parser of the standard re module, used to find literals a regex requires.
    from re import _constants as _re_constants, _parser as _re_parser
except ImportError:  # pragma: no cover - layout of a future Python
    _re_constants = _re_parser = None

MORPH_VERSION = "local-v5-nfkc-whitespace-zero-width-regex-source-offset"
MORPH_REWRITES: tuple[tuple[str, str], ...] = (
    ("않습니다", "않음"),
    ("있으신", "있는"),
    ("하시는", "하는"),
    ("합니다", "함"),
    ("하신", "한"),
    ("이신", "인"),
    ("으신", "은"),
)
# One alternation in MORPH_REWRITES order. ``finditer`` scans left to right,
# tries the alternatives in tuple order at each position and resumes after the
# consumed source. That is the rewrite cursor loop: at a cursor the first
# rewrite (in tuple order) whose source starts there wins; otherwise the
# cursor advances by one character.
_MORPH_SOURCE_PATTERN = re.compile(
    "|".join(re.escape(source) for source, _target in MORPH_REWRITES)
)
_MORPH_TARGETS: dict[str, str] = {}
for _source, _target in MORPH_REWRITES:
    _MORPH_TARGETS.setdefault(_source, _target)
del _source, _target
ZERO_WIDTH = frozenset({"\u200b", "\u200c", "\u200d", "\ufeff"})
_KEPT_CONTROLS = frozenset({"\r", "\n", "\t"})
# Characters whose per-character replacement (see _uncached_char_replacement)
# is the character itself: tab/LF/CR, printable ASCII, a few stable symbol and
# CJK blocks, punctuation common in postings and precomposed Hangul syllables.
# tests/test_morph_fast_paths.py checks every member of this class. Characters
# outside it are classified one distinct character at a time.
_POSSIBLY_CHANGED_CHARACTER = re.compile(
    r"[^\t\n\r\x20-\x7e\u00b7\u2013\u2014\u2018\u2019\u201c\u201d\u2022\u203b"
    r"\u2190-\u21ff\u2500-\u27bf\u3008-\u3011\u4e00-\u9fff\uac00-\ud7a3]"
)
# Characters without case: ASCII digits and punctuation, Hangul and CJK
# ideographs. re.IGNORECASE compiles an uncased literal character to an exact
# code point comparison, so a pattern chunk made only of them occurs verbatim
# in every match, and ``chunk not in text`` rules the pattern out without a scan.
_CASELESS_CHUNK = re.compile(
    r"[\x21-\x40\x5b-\x60\x7b-\x7e\u1100-\u11ff\u3131-\u318e\u4e00-\u9fff\uac00-\ud7a3]+"
)
_REPLACEMENT_CACHE: dict[str, str] = {}
_REPLACEMENT_CACHE_LIMIT = 1 << 16
# Views hold offsets only for texts that normalization or morphology changes,
# so 1024 entries stay below the three 512-entry tuple caches they replace.
_CACHE_SIZE = 1024

# (text, starts, ends): output character i comes from source[starts[i]:ends[i]].
# The offsets are ``range`` objects when output character i always comes from
# source character i, and tuples otherwise.
_View = tuple[str, Sequence[int], Sequence[int]]


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


@lru_cache(maxsize=1024)
def pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Turn dictionary patterns into deterministic, whitespace-tolerant regexes."""
    if pattern.startswith("re:"):
        return re.compile(pattern[3:], re.IGNORECASE)
    chunks = re.split(r"(\s+)", unicodedata.normalize("NFC", pattern))
    expression = "".join(r"\s*" if chunk.isspace() else re.escape(chunk) for chunk in chunks)
    return re.compile(expression, re.IGNORECASE)


def _identity_offsets(length: int) -> tuple[range, range]:
    return range(length), range(1, length + 1)


def _morph_view(text: str) -> _View:
    """Apply MORPH_REWRITES; each target character maps back to its whole source."""
    found = _MORPH_SOURCE_PATTERN.search(text)
    if found is None:
        return (text, *_identity_offsets(len(text)))
    pieces: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    previous = 0
    for match in _MORPH_SOURCE_PATTERN.finditer(text, found.start()):
        start, end = match.span()
        if previous < start:
            pieces.append(text[previous:start])
            starts.extend(range(previous, start))
            ends.extend(range(previous + 1, start + 1))
        target = _MORPH_TARGETS[match.group()]
        pieces.append(target)
        starts.extend([start] * len(target))
        ends.extend([end] * len(target))
        previous = end
    if previous < len(text):
        pieces.append(text[previous:])
        starts.extend(range(previous, len(text)))
        ends.extend(range(previous + 1, len(text) + 1))
    return "".join(pieces), tuple(starts), tuple(ends)


def _morph_text_with_offsets(text: str) -> tuple[str, tuple[int, ...], tuple[int, ...]]:
    morph_text, starts, ends = _morph_view(text)
    return morph_text, tuple(starts), tuple(ends)


def _morph_plain(text: str) -> str:
    return _morph_view(text)[0]


def _uncached_char_replacement(character: str) -> str:
    # Normalization is applied to each character on its own, never to the
    # whole string: decomposed jamo or combining sequences stay uncomposed.
    if character in ZERO_WIDTH:
        return ""
    if character in _KEPT_CONTROLS:
        return character
    if character.isspace():
        return " "
    return unicodedata.normalize("NFKC", character)


def _char_replacement(character: str) -> str:
    replacement = _REPLACEMENT_CACHE.get(character)
    if replacement is None:
        replacement = _uncached_char_replacement(character)
        if len(_REPLACEMENT_CACHE) < _REPLACEMENT_CACHE_LIMIT:
            _REPLACEMENT_CACHE[character] = replacement
    return replacement


def _normalized_view(text: str) -> _View:
    """Drop zero-width characters, fold whitespace and NFKC each character."""
    candidates = _POSSIBLY_CHANGED_CHARACTER.findall(text)
    if not candidates:
        return (text, *_identity_offsets(len(text)))
    changed: dict[int, str] = {}
    resized = False
    for character in set(candidates):
        replacement = _char_replacement(character)
        if replacement != character:
            changed[ord(character)] = replacement
            resized = resized or len(replacement) != 1
    if not changed:
        return (text, *_identity_offsets(len(text)))
    normalized = text.translate(changed)
    if not resized:
        # Every character still yields exactly one character in place.
        return (normalized, *_identity_offsets(len(text)))
    starts: list[int] = []
    ends: list[int] = []
    previous = 0
    for match in _POSSIBLY_CHANGED_CHARACTER.finditer(text):
        replacement = changed.get(ord(match.group()))
        if replacement is None or len(replacement) == 1:
            continue
        position = match.start()
        starts.extend(range(previous, position))
        ends.extend(range(previous + 1, position + 1))
        starts.extend([position] * len(replacement))
        ends.extend([position + 1] * len(replacement))
        previous = position + 1
    starts.extend(range(previous, len(text)))
    ends.extend(range(previous + 1, len(text) + 1))
    return normalized, tuple(starts), tuple(ends)


def _normalized_text_with_offsets(
    text: str,
) -> tuple[str, tuple[int, ...], tuple[int, ...]]:
    normalized, starts, ends = _normalized_view(text)
    return normalized, tuple(starts), tuple(ends)


def _compose_views(normalized: _View) -> _View:
    """Run the morph rewrites on a normalized view, mapping back to the source."""
    normalized_text, source_starts, source_ends = normalized
    morph_text, morph_starts, morph_ends = _morph_view(normalized_text)
    if morph_text == normalized_text:
        return normalized
    if type(source_starts) is range:
        # Identity normalization offsets: source_starts[i] == i and
        # source_ends[i - 1] == i, so the composition is the morph view itself.
        return morph_text, morph_starts, morph_ends
    starts = tuple(source_starts[index] for index in morph_starts)
    ends = tuple(source_ends[index - 1] for index in morph_ends)
    return morph_text, starts, ends


def _match_text_with_offsets(
    text: str,
) -> tuple[str, tuple[int, ...], tuple[int, ...]]:
    match_text, starts, ends = _compose_views(_normalized_view(text))
    return match_text, tuple(starts), tuple(ends)


def _match_plain(text: str) -> str:
    return _morph_plain(_normalized_view(text)[0])


@lru_cache(maxsize=_CACHE_SIZE)
def _text_views(
    text: str,
) -> tuple[bool, _View, bool, _View]:
    """(normalized view differs, normalized view, match view differs, match view)."""
    normalized = _normalized_view(text)
    matched = _compose_views(normalized)
    return normalized[0] != text, normalized, matched[0] != text, matched


def _required_literals(pattern: str) -> tuple[str, ...]:
    """Caseless chunks that every match of pattern_to_regex(pattern) contains verbatim."""
    if pattern.startswith("re:"):
        return ()
    # Mirrors pattern_to_regex: the non-whitespace chunks become escaped
    # literals joined by \s*, so each of them is part of every match.
    chunks = re.split(r"(\s+)", unicodedata.normalize("NFC", pattern))
    return tuple(chunk for chunk in chunks if _CASELESS_CHUNK.fullmatch(chunk))


_REQUIRED_REPEATS = (
    frozenset(
        getattr(_re_constants, name)
        for name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT")
        if hasattr(_re_constants, name)
    )
    if _re_constants is not None
    else frozenset()
)


def _collect_required_runs(
    items: object,
    runs: list[str],
    any_of: list[tuple[str, ...]] | None = None,
) -> None:
    run: list[str] = []
    for op, argument in items:  # type: ignore[attr-defined]
        if op == _re_constants.LITERAL and _CASELESS_CHUNK.fullmatch(chr(argument)):
            run.append(chr(argument))
            continue
        if run:
            runs.append("".join(run))
            run = []
        if op == _re_constants.BRANCH and any_of is not None:
            alternatives: list[str] = []
            for branch in argument[1]:
                if not isinstance(branch, _re_parser.SubPattern):
                    raise TypeError("unexpected regex parse tree")
                branch_runs: list[str] = []
                # A representative must occur in every match of this branch;
                # nested alternatives alone do not establish such a run.
                _collect_required_runs(branch, branch_runs)
                if not branch_runs:
                    break
                alternatives.append(max(branch_runs, key=len))
            else:
                if alternatives:
                    any_of.append(tuple(dict.fromkeys(alternatives)))
            continue
        # Only constructs that always match their body at least once are
        # entered; optional repeats and lookarounds are skipped.
        if op == _re_constants.SUBPATTERN:
            body = argument[3]
        elif op in _REQUIRED_REPEATS and argument[0] >= 1:
            body = argument[2]
        elif op == getattr(_re_constants, "ATOMIC_GROUP", None):
            body = argument
        else:
            continue
        if not isinstance(body, _re_parser.SubPattern):
            raise TypeError("unexpected regex parse tree")
        _collect_required_runs(body, runs, any_of)
    if run:
        runs.append("".join(run))


def _regex_required_conditions(
    expression: str,
) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """All-of and any-of caseless runs required by re.compile(expression, re.I).

    A match consumes each item of a sequence in order, so a run of uncased
    literal characters at sequence level (or inside a group or a repeat with
    a minimum of one) appears verbatim in the matched text. A required branch
    contributes an any-of condition only if every alternative has such a run.
    """
    if _re_parser is None:
        return (), ()
    runs: list[str] = []
    any_of: list[tuple[str, ...]] = []
    try:
        _collect_required_runs(_re_parser.parse(expression, re.IGNORECASE), runs, any_of)
    except Exception:  # an unknown parse tree only loses the prefilter
        return (), ()
    return tuple(dict.fromkeys(runs)), tuple(dict.fromkeys(any_of))


def _regex_required_literals(expression: str) -> tuple[str, ...]:
    """Caseless literal runs that every match of re.compile(expression, re.I) contains."""
    return _regex_required_conditions(expression)[0]


@lru_cache(maxsize=_CACHE_SIZE)
def _pattern_plan(
    pattern: str,
) -> tuple[bool, re.Pattern[str], tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """(is a raw regex, compiled pattern, required all-of runs, required any-of runs)."""
    regex = pattern_to_regex(pattern)
    if pattern.startswith("re:"):
        required, any_of = _regex_required_conditions(pattern[3:])
        return True, regex, required, any_of
    return False, regex, _required_literals(pattern), ()


@lru_cache(maxsize=_CACHE_SIZE)
def _match_view_plan(pattern: str) -> tuple[re.Pattern[str], tuple[str, ...]]:
    """Compiled literal pattern for the match view and its required literals."""
    plain = _match_plain(pattern)
    return pattern_to_regex(plain), _required_literals(plain)


def _admits(
    text: str, required: tuple[str, ...], required_any_of: tuple[tuple[str, ...], ...]
) -> bool:
    """True unless a required literal or every literal of an any-of group is missing."""
    for literal in required:
        if literal not in text:
            return False
    for group in required_any_of:
        for literal in group:
            if literal in text:
                break
        else:
            return False
    return True


# (pattern, is a raw regex, compiled pattern, required all-of runs, required
# any-of runs), i.e. the pattern and its _pattern_plan.
_Entry = tuple[
    str,
    bool,
    re.Pattern[str],
    tuple[str, ...],
    tuple[tuple[str, ...], ...],
]
# (entries, gate for the raw view of every pattern, gate for the raw view of
# literal patterns, gate for regex patterns, gate for the match view).
_ListPlan = tuple[
    tuple[_Entry, ...],
    re.Pattern[str] | None,
    re.Pattern[str] | None,
    re.Pattern[str] | None,
    re.Pattern[str] | None,
]
# Finds every text: the gate of a pattern group with an unconditional member.
_OPEN_GATE = re.compile("")
_LIST_PLANS: dict[tuple[str, ...], _ListPlan] = {}
_LIST_PLAN_LIMIT = 4096
# Results of _collect_matches for texts of at least this many characters, by
# (text, patterns). The engine and the slot extractor scan the same section or
# posting text with the same pattern list several times per check; shorter
# texts are cheaper to scan again than to cache.
_RESULTS: dict[
    tuple[str, tuple[str, ...]], dict[tuple[int, int], re.Match[str] | SourceMatch]
] = {}
_RESULT_MIN_CHARS = 128
_RESULT_LIMIT = _CACHE_SIZE
# The _pattern_plan the cached list plans and results were built with; a
# replaced _pattern_plan (tests patch it) invalidates them.
_list_plan_source: object = None


def _gate(
    conditions: list[tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]],
) -> re.Pattern[str] | None:
    """Exact regex that finds a text only if some condition can still hold there.

    Each (required, any_of) condition contributes one required literal, or
    every literal of one any-of group: a text without them fails _admits for
    that condition. None means there is no condition, _OPEN_GATE that one of
    them requires nothing.
    """
    if not conditions:
        return None
    keys: list[str] = []
    for required, any_of in conditions:
        if required:
            keys.append(max(required, key=len))
        elif any_of:
            keys.extend(any_of[0])
        else:
            return _OPEN_GATE
    return re.compile("|".join(re.escape(key) for key in dict.fromkeys(keys)))


def _build_list_plan(patterns: tuple[str, ...]) -> _ListPlan:
    entries: list[_Entry] = []
    literal_conditions: list[tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]] = []
    regex_conditions: list[tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]] = []
    view_conditions: list[tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]] = []
    for pattern in patterns:
        is_regex, regex, required, required_any_of = _pattern_plan(pattern)
        if is_regex:
            regex_conditions.append((required, required_any_of))
        else:
            literal_conditions.append((required, required_any_of))
            # The literals of _match_view_plan(pattern), without compiling it:
            # that stays lazy because a folded pattern can be an invalid regex.
            view_conditions.append(
                (_required_literals(_match_plain(pattern)), required_any_of)
            )
        entries.append((pattern, is_regex, regex, required, required_any_of))
    return (
        tuple(entries),
        _gate(literal_conditions + regex_conditions),
        _gate(literal_conditions),
        _gate(regex_conditions),
        _gate(view_conditions),
    )


def _collect_matches(
    text: str,
    patterns: list[str],
) -> dict[tuple[int, int], re.Match[str] | SourceMatch]:
    # The returned dict may be shared through _RESULTS: callers only read it.
    global _list_plan_source
    if _list_plan_source is not _pattern_plan:
        _LIST_PLANS.clear()
        _RESULTS.clear()
        _list_plan_source = _pattern_plan
    key = tuple(patterns)
    if len(text) < _RESULT_MIN_CHARS:
        return _scan_matches(text, key)
    result_key = (text, key)
    found = _RESULTS.get(result_key)
    if found is None:
        found = _scan_matches(text, key)
        if len(_RESULTS) >= _RESULT_LIMIT:
            _RESULTS.clear()
        _RESULTS[result_key] = found
    return found


def _scan_matches(
    text: str,
    key: tuple[str, ...],
) -> dict[tuple[int, int], re.Match[str] | SourceMatch]:
    # Keyed by (start, end): group(0) is always text[start:end] for raw and
    # source matches alike, so the span alone identifies a duplicate. The first
    # match found for a span is kept. A pattern is only scanned when the text
    # contains every required literal and at least one literal from each
    # required any-of group; otherwise it cannot match. The gates of the list
    # plan check that for the whole list at once: when no view it would scan
    # opens, no pattern is admitted anywhere and nothing can match.
    plan = _LIST_PLANS.get(key)
    if plan is None:
        if len(_LIST_PLANS) >= _LIST_PLAN_LIMIT:
            _LIST_PLANS.clear()
        plan = _LIST_PLANS[key] = _build_list_plan(key)
    entries, raw_gate, literal_gate, regex_gate, view_gate = plan
    normalized_differs, normalized, match_differs, matched = _text_views(text)
    if normalized_differs:
        is_open = (literal_gate is not None and literal_gate.search(text)) or (
            regex_gate is not None and regex_gate.search(normalized[0])
        )
    else:
        is_open = raw_gate is not None and raw_gate.search(text)
    if not is_open and not (
        match_differs and view_gate is not None and view_gate.search(matched[0])
    ):
        return {}
    found: dict[tuple[int, int], re.Match[str] | SourceMatch] = {}
    for pattern, is_regex, regex, required, required_any_of in entries:
        if is_regex and normalized_differs:
            # A raw regex can miss a normalized candidate in its own lookahead
            # and incorrectly consume a later protective phrase. Once
            # compatibility or invisible characters are present, the
            # normalized regex view is authoritative; morphology-only rewrites
            # still keep the raw view. Regex rules must see the same
            # NFKC/zero-width-normalized view as literal rules. This is
            # especially important for protective exclusions: otherwise a
            # normalized candidate can be found while its adjacent "do not
            # collect" expression is tested only against the raw text and
            # becomes a false positive.
            search_text, search_starts, search_ends = normalized
        else:
            if _admits(text, required, required_any_of):
                for match in regex.finditer(text):
                    span = match.span()
                    if span not in found:
                        found[span] = match
            if is_regex or not match_differs:
                continue
            regex, required = _match_view_plan(pattern)
            search_text, search_starts, search_ends = matched
        if _admits(search_text, required, required_any_of):
            for match in regex.finditer(search_text):
                start, end = match.span()
                if start == end:
                    continue
                span = (search_starts[start], search_ends[end - 1])
                if span not in found:
                    found[span] = SourceMatch(text, *span)
    return found


def find_first(
    text: str,
    patterns: list[str],
) -> re.Match[str] | SourceMatch | None:
    found = _collect_matches(text, patterns)
    return found[min(found)] if found else None


def find_matches(
    text: str,
    patterns: list[str],
) -> list[re.Match[str] | SourceMatch]:
    # Spans are unique, so ordering by span equals the (start, end, group(0)) order.
    found = _collect_matches(text, patterns)
    return [found[span] for span in sorted(found)]


# How many characters past the end of an exclusion window the search for a
# term's start and the bounded retry in _window_spans may read. Assertions of
# the match that is kept otherwise read the whole text. See
# docs/exclusion-window-2026-10-23.md.
EXCLUDE_LOOKAHEAD = 1024
_BOUNDED: dict[tuple[re.Pattern[str], int], re.Pattern[str]] = {}
_BOUNDED_LIMIT = 4096


def _bounded_regex(regex: re.Pattern[str], remaining: int) -> re.Pattern[str]:
    """regex, accepted only where at least ``remaining`` characters follow its match."""
    key = (regex, remaining)
    bounded = _BOUNDED.get(key)
    if bounded is None:
        if len(_BOUNDED) >= _BOUNDED_LIMIT:
            _BOUNDED.clear()
        bounded = _BOUNDED[key] = re.compile(
            f"(?:{regex.pattern})(?=[\\s\\S]{{{remaining}}})", regex.flags
        )
    return bounded


def _window_spans(
    regex: re.Pattern[str], text: str, left: int, right: int
) -> Iterator[tuple[int, int]]:
    """Matches of regex in ``text`` that start and end inside [left, right].

    Like finditer on ``text[left:right]`` (left to right, each search resuming
    at the end of the previous match), except that lookarounds, ``^`` and
    ``$`` read the text itself instead of a copy cut at the window. A start is
    one that a search of text[:limit] finds, limit being ``right`` plus
    EXCLUDE_LOOKAHEAD. The match kept there is regex.match on the whole text
    when it ends by ``right``; when it runs past ``right``, the first match at
    the same start that ends by ``right`` with assertions reading text[:limit].
    """
    limit = min(len(text), right + EXCLUDE_LOOKAHEAD)
    position = left
    while position <= right:
        found = regex.search(text, position, limit)
        if found is None:
            return
        start = found.start()
        if start > right:
            return
        match = regex.match(text, start)
        if match is not None and match.end() > right:
            match = _bounded_regex(regex, limit - right).match(text, start, limit)
        if match is None:
            position = start + 1
            continue
        end = match.end()
        yield start, end
        position = end if end > start else start + 1


def _window_admits(
    text: str,
    left: int,
    right: int,
    required: tuple[str, ...],
    required_any_of: tuple[tuple[str, ...], ...],
) -> bool:
    """_admits for text[left:right] without copying it."""
    for literal in required:
        if text.find(literal, left, right) < 0:
            return False
    for group in required_any_of:
        for literal in group:
            if text.find(literal, left, right) >= 0:
                break
        else:
            return False
    return True


def _term_in_window(
    text: str, left: int, right: int, pattern: str, overlap: tuple[int, int] | None
) -> bool:
    """Whether pattern matches inside [left, right] of text (and overlaps ``overlap``).

    The views and the scans follow _scan_matches: a raw regex reads the
    normalized view once the text has compatibility or invisible characters,
    a literal pattern reads the text and its match view. Views come from the
    whole text, and window offsets are mapped into each view.
    """
    is_regex, regex, required, required_any_of = _pattern_plan(pattern)
    normalized_differs, normalized, match_differs, matched = _text_views(text)
    scans: list[tuple[_View | None, re.Pattern[str], tuple[str, ...]]] = []
    if is_regex and normalized_differs:
        scans.append((normalized, regex, required))
    else:
        scans.append((None, regex, required))
        if not is_regex and match_differs:
            view_regex, view_required = _match_view_plan(pattern)
            scans.append((matched, view_regex, view_required))
    for view, scan_regex, scan_required in scans:
        starts = ends = None
        if view is None:
            search_text, view_left, view_right = text, left, right
        elif type(view[1]) is range:
            search_text, view_left, view_right = view[0], left, right
        else:
            search_text, starts, ends = view
            # View characters wholly from source[left:right]: offsets only grow.
            view_left = bisect_left(starts, left)
            view_right = bisect_right(ends, right)
            if view_right < view_left:
                continue
        if not _window_admits(
            search_text, view_left, view_right, scan_required, required_any_of
        ):
            continue
        for match_start, match_end in _window_spans(
            scan_regex, search_text, view_left, view_right
        ):
            if view is not None:
                # As in _scan_matches, a view keeps only non-empty matches.
                if match_start == match_end:
                    continue
                if starts is not None:
                    match_start, match_end = starts[match_start], ends[match_end - 1]
            if overlap is None or (
                match_start < overlap[1] and match_end > overlap[0]
            ):
                return True
    return False


def is_excluded(text: str, start: int, end: int, excludes: list[dict]) -> bool:
    for exclusion in excludes:
        candidate_pattern = exclusion.get("candidate")
        if candidate_pattern and find_first(
            text[start:end], [str(candidate_pattern)]
        ) is None:
            continue
        window = int(exclusion.get("window", 0))
        left = max(0, start - window)
        right = min(len(text), end + window)
        overlap = (start, end) if exclusion.get("overlap_candidate") else None
        if _term_in_window(text, left, right, str(exclusion["term"]), overlap):
            return True
    return False
