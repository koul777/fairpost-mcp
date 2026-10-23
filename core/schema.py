from __future__ import annotations

from dataclasses import dataclass, field, fields
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
class QuestionReference:
    type: str
    title: str | None = None
    publisher: str | None = None
    year: int | None = None
    pages: list[int] | None = None
    source_url: str | None = None
    accessed_at: str | None = None
    sections: list[str] | None = None


@dataclass(frozen=True)
class Question:
    id: str
    dimension: str
    question: str
    follow_up: list[str]
    basis_type: str
    book_ref: str
    review_scope: str
    saved_answer: str | None
    matched_text: str | None = None
    offset: tuple[int, int] | None = None
    section: str | None = None
    reference: QuestionReference | None = None
    trigger_reason: str = "presence"
    linked_findings: list[str] = field(default_factory=list)
    priority: int = 3


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
        """The plain-data form of the result, equal to dataclasses.asdict(self)
        with every offset turned into a list. The conversion is written out
        field by field because asdict copies every value through a generic
        recursive walk, which dominated the cost of serializing a result."""
        return {
            "findings": [_finding_dict(finding) for finding in self.findings],
            "slots": [_slot_dict(slot) for slot in self.slots],
            "questions": [_question_dict(question) for question in self.questions],
            "counts": dict(self.counts),
            "ruleset_version": self.ruleset_version,
            "statute_snapshot_date": self.statute_snapshot_date,
            "statute_notice": self.statute_notice,
            "disclaimer": self.disclaimer,
        }


def _finding_dict(finding: Finding) -> dict[str, Any]:
    basis = finding.basis
    offset = finding.offset
    return {
        "id": finding.id,
        "dimension": finding.dimension,
        "message": finding.message,
        "matched_text": finding.matched_text,
        "offset": None if offset is None else list(offset),
        "section": finding.section,
        "severity": finding.severity,
        "basis": {
            "type": basis.type,
            "law": basis.law,
            "article": basis.article,
            "statute_id": basis.statute_id,
            "snapshot_date": basis.snapshot_date,
            "effective_date": basis.effective_date,
            "title": basis.title,
            "text": basis.text,
        },
        "alternatives": list(finding.alternatives),
        "provenance_method": finding.provenance_method,
        "book_ref": finding.book_ref,
    }


def _slot_dict(slot: SlotStatus) -> dict[str, Any]:
    return {
        "slot": slot.slot,
        "label": slot.label,
        "found": slot.found,
        "components_found": list(slot.components_found),
        "components_total": slot.components_total,
        "evidence": slot.evidence,
        "section": slot.section,
    }


def _question_dict(question: Question) -> dict[str, Any]:
    reference = question.reference
    offset = question.offset
    return {
        "id": question.id,
        "dimension": question.dimension,
        "question": question.question,
        "follow_up": list(question.follow_up),
        "basis_type": question.basis_type,
        "book_ref": question.book_ref,
        "review_scope": question.review_scope,
        "saved_answer": question.saved_answer,
        "matched_text": question.matched_text,
        "offset": None if offset is None else list(offset),
        "section": question.section,
        "reference": None if reference is None else _reference_dict(reference),
        "trigger_reason": question.trigger_reason,
        "linked_findings": list(question.linked_findings),
        "priority": question.priority,
    }


def _reference_dict(reference: QuestionReference) -> dict[str, Any]:
    return {
        "type": reference.type,
        "title": reference.title,
        "publisher": reference.publisher,
        "year": reference.year,
        "pages": None if reference.pages is None else list(reference.pages),
        "source_url": reference.source_url,
        "accessed_at": reference.accessed_at,
        "sections": None if reference.sections is None else list(reference.sections),
    }


# to_dict above names every field. A field added to a class without a matching
# entry would silently disappear from the output, so fail at import instead.
for _cls, _names in (
    (Basis, ("type", "law", "article", "statute_id", "snapshot_date", "effective_date", "title", "text")),
    (
        Finding,
        ("id", "dimension", "message", "matched_text", "offset", "section", "severity",
         "basis", "alternatives", "provenance_method", "book_ref"),
    ),
    (SlotStatus, ("slot", "label", "found", "components_found", "components_total", "evidence", "section")),
    (
        QuestionReference,
        ("type", "title", "publisher", "year", "pages", "source_url", "accessed_at", "sections"),
    ),
    (
        Question,
        ("id", "dimension", "question", "follow_up", "basis_type", "book_ref", "review_scope",
         "saved_answer", "matched_text", "offset", "section", "reference", "trigger_reason",
         "linked_findings", "priority"),
    ),
    (
        CheckResult,
        ("findings", "slots", "questions", "counts", "ruleset_version", "statute_snapshot_date",
         "statute_notice", "disclaimer"),
    ),
):
    if tuple(_field.name for _field in fields(_cls)) != _names:
        raise RuntimeError(f"{_cls.__name__}.to_dict must be updated for the fields of the dataclass")
del _cls, _names
