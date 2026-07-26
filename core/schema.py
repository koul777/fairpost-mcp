from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Basis:
    type: str
    law: str | None = None
    article: str | None = None
    statute_id: str | None = None
    snapshot_date: str | None = None
    effective_date: str | None = None
    title: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class Finding:
    id: str
    dimension: str
    message: str
    matched_text: str | None
    offset: tuple[int, int] | None
    section: str | None
    severity: str | None
    basis: Basis
    alternatives: list[str]
    provenance_method: str
    book_ref: str


@dataclass(frozen=True)
class SlotStatus:
    slot: str
    label: str
    found: bool
    components_found: list[str]
    components_total: int
    evidence: str | None
    section: str | None


@dataclass(frozen=True)
class Question:
    id: str
    dimension: str
    question: str
    follow_up: list[str]
    basis_type: str
    book_ref: str
    saved_answer: str | None


@dataclass(frozen=True)
class CheckResult:
    findings: list[Finding] = field(default_factory=list)
    slots: list[SlotStatus] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    ruleset_version: str = ""
    statute_snapshot_date: str = ""
    statute_notice: str = ""
    disclaimer: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for finding in payload["findings"]:
            if finding["offset"] is not None:
                finding["offset"] = list(finding["offset"])
        return payload
