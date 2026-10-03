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
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


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
    # ("ID9001011234567"); digits may not.
    r"(?<![0-9])[0-9]{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01])"
    r"[ -]?[1-8][0-9]{6}(?![0-9])"
)
_PHONE = (
    r"(?<![0-9])(?:"
    # Mobile numbers may omit separators: 010-1234-5678, 01012345678.
    r"01[016789][ .-]?[0-9]{3,4}[ .-]?[0-9]{4}"
    r"|"
    # Landline, internet, toll-free and personal numbers need a separator
    # after the area code so 10-digit NCS codes (0202010101_17v2) never match:
    # 02-1234-5678, 031-123-4567, (02) 123-4567, 02)1234-5678, 070-1234-5678.
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
