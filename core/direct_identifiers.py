"""Single source of truth for direct personal identifier patterns.

The same patterns are used in three places:

* ``core.review_packet`` rejects role-review notes that contain them;
* ``mcp_server.assisted_review`` masks them before evidence leaves the
  FairPost server for an AI provider;
* the static web app (``web/app.js``) checks role-review notes in the browser.

``tools/export_web_bundle.py`` copies :data:`DIRECT_IDENTIFIER_PATTERNS` into
``web/data.js`` so the browser never keeps its own copy. Every ``source`` must
therefore mean the same thing in Python ``re`` and in an ECMAScript ``RegExp``
built with the ``u`` flag: use explicit ASCII classes such as ``[0-9]`` and
``[A-Za-z]`` instead of ``\\d``/``\\w``/``\\s`` (their Unicode behaviour
differs), keep ``-`` at the end of character classes, and avoid inline flags.

The server additionally screens look-alike spellings that the shared ASCII
patterns cannot see: full-width digits and ``＠``, Unicode dashes, no-break
and other Unicode spaces, and zero-width characters inside a value. The
``*_folded`` functions match on a folded copy (per-character NFKC plus dash
and space folding) and mask the corresponding spans of the original text, so
everything outside an identifier is returned unchanged. The browser keeps
using the plain patterns; the folded screen is a server-side superset.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
import unicodedata


DIRECT_IDENTIFIER_SCHEMA_VERSION = "fairpost-direct-identifiers-v1"


@dataclass(frozen=True)
class DirectIdentifierPattern:
    kind: str
    label: str
    mask: str
    source: str

    @property
    def regex(self) -> re.Pattern[str]:
        return _COMPILED[self.kind]


_EMAIL = (
    # Start only at the beginning of a local-part run so long unspaced text
    # cannot cause quadratic rescans. Hangul is accepted in the local part so
    # an adjacent label such as "인사팀recruit@..." is masked as a whole.
    r"(?<![A-Za-z0-9._%+가-힣-])"
    r"[A-Za-z0-9._%+가-힣-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
    r"(?![A-Za-z0-9-])"
)
_RESIDENT_REGISTRATION_NUMBER = (
    # YYMMDD with a plausible month/day, optional space or hyphen, then a
    # 1-8 gender/century digit and six digits. Letters may precede the value
    # ("ID" + YYMMDD + seven digits); digits may not.
    r"(?<![0-9])[0-9]{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01])"
    r"[ -]?[1-8][0-9]{6}(?![0-9])"
)
_PHONE = (
    r"(?<![0-9])(?:"
    # International +82 form drops the leading 0, optionally keeping it as
    # "(0)": +82 10-NNNN-NNNN, +82-2-NNNN-NNNN, +82 (0)31 NNN NNNN.
    r"\+82[ .-]?(?:\(0\)[ .-]?)?(?:"
    r"1[016789][ .-]?[0-9]{3,4}[ .-]?[0-9]{4}"
    r"|"
    r"(?:2|3[1-3]|4[1-4]|5[1-5]|6[1-4]|70|80|50[2-8])[ .-]?[0-9]{3,4}[ .-][0-9]{4}"
    r")"
    r"|"
    # Mobile numbers may omit separators: 010-NNNN-NNNN or 010NNNNNNNN.
    r"01[016789][ .-]?[0-9]{3,4}[ .-]?[0-9]{4}"
    r"|"
    # Landline, internet, toll-free and personal numbers need a separator
    # after the area code so 10-digit NCS unit codes ("02" + eight digits +
    # "_17v2") never match: 02-NNNN-NNNN, 031-NNN-NNNN, (02) NNN-NNNN,
    # 02)NNNN-NNNN, 070-NNNN-NNNN.
    r"\(?0(?:2|3[1-3]|4[1-4]|5[1-5]|6[1-4]|70|80|50[2-8])"
    r"(?:\) ?|[ .-])[0-9]{3,4}[ .-][0-9]{4}"
    r")(?![0-9_])"
)

# Order matters for masking: e-mail first, then resident registration
# numbers, then telephone numbers. Masks contain no digits or "@", so a later
# pattern never re-matches an earlier mask.
DIRECT_IDENTIFIER_PATTERNS: tuple[DirectIdentifierPattern, ...] = (
    DirectIdentifierPattern(
        kind="email",
        label="이메일",
        mask="[이메일 마스킹]",
        source=_EMAIL,
    ),
    DirectIdentifierPattern(
        kind="resident_registration_number",
        label="주민등록번호",
        mask="[주민등록번호 마스킹]",
        source=_RESIDENT_REGISTRATION_NUMBER,
    ),
    DirectIdentifierPattern(
        kind="phone",
        label="전화번호",
        mask="[전화번호 마스킹]",
        source=_PHONE,
    ),
)

_COMPILED: dict[str, re.Pattern[str]] = {
    pattern.kind: re.compile(pattern.source) for pattern in DIRECT_IDENTIFIER_PATTERNS
}


def direct_identifier_kinds(text: str) -> tuple[str, ...]:
    """Return the identifier kinds present in ``text`` in declaration order."""

    if not isinstance(text, str) or not text:
        return ()
    return tuple(
        pattern.kind
        for pattern in DIRECT_IDENTIFIER_PATTERNS
        if pattern.regex.search(text)
    )


def contains_direct_identifier(text: str) -> bool:
    return bool(direct_identifier_kinds(text))


def mask_direct_identifiers(text: str) -> str:
    """Replace every direct identifier with its fixed Korean mask label."""

    masked = text
    for pattern in DIRECT_IDENTIFIER_PATTERNS:
        masked = pattern.regex.sub(pattern.mask, masked)
    return masked


_DASHES = frozenset("‐‑‒–—―−﹘﹣－")
_ZERO_WIDTH = frozenset("​‌‍⁠﻿­")


def fold_identifier_variants(text: str) -> tuple[str, tuple[int, ...]]:
    """Fold look-alike characters and map each folded index to the original.

    Each character is folded on its own: Unicode dashes become ``-``, Unicode
    spaces become `` ``, zero-width characters are dropped, and a character
    whose NFKC form is ASCII (full-width digits, ``＠``, ``＋``, ``（``)
    takes that form. Everything else (Hangul included) is kept as is.
    """

    folded: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        if char.isascii():
            replacement = char
        elif char in _ZERO_WIDTH:
            continue
        elif char in _DASHES:
            replacement = "-"
        elif char.isspace():
            replacement = " "
        else:
            normalized = unicodedata.normalize("NFKC", char)
            replacement = normalized if normalized.isascii() else char
        folded.append(replacement)
        origin.extend([index] * len(replacement))
    return "".join(folded), tuple(origin)


def contains_direct_identifier_folded(text: str) -> bool:
    """Server-side screen that also catches look-alike spellings."""

    if not isinstance(text, str) or not text:
        return False
    folded, _origin = fold_identifier_variants(text)
    return any(pattern.regex.search(folded) for pattern in DIRECT_IDENTIFIER_PATTERNS)


def mask_direct_identifiers_folded(text: str) -> str:
    """Mask identifiers found in the folded text, keeping all other characters.

    Patterns run in declaration order on the progressively masked text, as in
    :func:`mask_direct_identifiers`; for plain ASCII identifiers the result is
    identical.
    """

    masked = text
    for pattern in DIRECT_IDENTIFIER_PATTERNS:
        folded, origin = fold_identifier_variants(masked)
        spans = [
            (origin[match.start()], origin[match.end() - 1] + 1)
            for match in pattern.regex.finditer(folded)
            if match.end() > match.start()
        ]
        if not spans:
            continue
        pieces: list[str] = []
        cursor = 0
        for start, end in spans:
            if start < cursor:
                continue
            pieces.append(masked[cursor:start])
            pieces.append(pattern.mask)
            cursor = end
        pieces.append(masked[cursor:])
        masked = "".join(pieces)
    return masked


def direct_identifier_web_bundle() -> dict[str, Any]:
    """Serializable copy for ``web/data.js`` (consumed by ``web/app.js``)."""

    return {
        "schema_version": DIRECT_IDENTIFIER_SCHEMA_VERSION,
        "flags": "u",
        "patterns": [
            {
                "kind": pattern.kind,
                "label": pattern.label,
                "mask": pattern.mask,
                "source": pattern.source,
            }
            for pattern in DIRECT_IDENTIFIER_PATTERNS
        ],
    }
