"""Privacy-preserving multi-role review packet contract.

The packet records who reviewed a deterministic result, at which NCS process
stage, and what follow-up is needed. It deliberately carries a posting
fingerprint rather than the posting text so coordination logs do not become
copies of the input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Literal


REVIEW_PACKET_SCHEMA_VERSION = "fairpost-review-packet-v1"

ReviewStage = Literal[
    "analysis",
    "design",
    "development",
    "implementation",
    "evaluation",
]
ReviewRole = Literal[
    "chair",
    "hr_owner",
    "job_sme",
    "interviewer",
    "policy_reviewer",
    "auditor",
    "candidate_advocate",
]
ReviewAction = Literal[
    "note",
    "confirm",
    "edit_requested",
    "escalate",
    "resolve",
]

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
_STAGES = frozenset(
    {"analysis", "design", "development", "implementation", "evaluation"}
)
_ROLES = frozenset(
    {
        "chair",
        "hr_owner",
        "job_sme",
        "interviewer",
        "policy_reviewer",
        "auditor",
        "candidate_advocate",
    }
)
REVIEW_ROLES = (
    "chair",
    "hr_owner",
    "job_sme",
    "interviewer",
    "policy_reviewer",
    "auditor",
    "candidate_advocate",
)
_ACTIONS = frozenset(
    {"note", "confirm", "edit_requested", "escalate", "resolve"}
)
_MAX_NOTE_LENGTH = 4000
_MAX_EVIDENCE_REFS = 32
_SENSITIVE_NOTE_PATTERNS = (
    re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+"),
    re.compile(r"(?<!\d)01[016789][ -]?\d{3,4}[ -]?\d{4}(?!\d)"),
    re.compile(r"(?<!\d)\d{6}[ -]?\d{7}(?!\d)"),
)


class ReviewPacketError(ValueError):
    """Raised when an untrusted review packet payload violates the contract."""


def posting_fingerprint(posting_text: str) -> str:
    """Return the stable SHA-256 fingerprint used to correlate a review."""

    if not isinstance(posting_text, str) or not posting_text:
        raise ReviewPacketError("posting_text must be a non-empty string")
    return hashlib.sha256(posting_text.encode("utf-8")).hexdigest()


def _bounded_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ReviewPacketError(
            f"{field_name} must be 1-128 ASCII characters and start with a letter or digit"
        )
    return value


def _enum(value: Any, allowed: frozenset[str], field_name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ReviewPacketError(f"unknown {field_name}: {value!r}")
    return value


def _timestamp(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewPacketError(f"{field_name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ReviewPacketError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReviewPacketError(f"{field_name} must include a timezone offset")
    return value


def _fingerprint(value: Any) -> str:
    if not isinstance(value, str) or not _FINGERPRINT_RE.fullmatch(value):
        raise ReviewPacketError("posting_fingerprint must be a SHA-256 hex digest")
    return value


def _evidence_refs(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > _MAX_EVIDENCE_REFS:
        raise ReviewPacketError("evidence_refs must be a list of at most 32 IDs")
    refs = tuple(_bounded_id(item, "evidence_refs item") for item in value)
    if len(refs) != len(set(refs)):
        raise ReviewPacketError("evidence_refs must not contain duplicates")
    return refs


def _validate_note(value: Any) -> None:
    if not isinstance(value, str) or len(value) > _MAX_NOTE_LENGTH:
        raise ReviewPacketError("note must be a string of at most 4000 characters")
    if any(pattern.search(value) for pattern in _SENSITIVE_NOTE_PATTERNS):
        raise ReviewPacketError(
            "note must not contain direct personal identifiers; use evidence_refs instead"
        )


def _reject_raw_posting(payload: dict[str, Any]) -> None:
    forbidden = {
        "posting_text",
        "raw_posting",
        "raw_posting_text",
        "candidate_records",
        "applicant_data",
    }
    present = sorted(forbidden.intersection(payload))
    if present:
        raise ReviewPacketError(
            "review packets cannot contain raw posting or candidate data: "
            + ", ".join(present)
        )


@dataclass(frozen=True)
class ReviewEvent:
    """One role's bounded, auditable contribution to a review."""

    event_id: str
    stage: ReviewStage
    role: ReviewRole
    action: ReviewAction
    occurred_at: str
    note: str = ""
    evidence_refs: tuple[str, ...] = ()
    actor_ref: str | None = None
    ruleset_version: str = ""
    guidance_catalog_version: str = ""
    resolves_event_id: str | None = None

    def __post_init__(self) -> None:
        _bounded_id(self.event_id, "event_id")
        _enum(self.stage, _STAGES, "stage")
        _enum(self.role, _ROLES, "role")
        _enum(self.action, _ACTIONS, "action")
        _timestamp(self.occurred_at, "occurred_at")
        _validate_note(self.note)
        if not isinstance(self.evidence_refs, tuple):
            raise ReviewPacketError("evidence_refs must be a tuple")
        _evidence_refs(list(self.evidence_refs))
        if self.actor_ref is not None:
            _bounded_id(self.actor_ref, "actor_ref")
        if not isinstance(self.ruleset_version, str) or len(self.ruleset_version) > 128:
            raise ReviewPacketError("ruleset_version must be a bounded string")
        if not isinstance(self.guidance_catalog_version, str) or len(
            self.guidance_catalog_version
        ) > 128:
            raise ReviewPacketError("guidance_catalog_version must be a bounded string")
        if self.resolves_event_id is not None:
            _bounded_id(self.resolves_event_id, "resolves_event_id")
            if self.action != "resolve":
                raise ReviewPacketError(
                    "resolves_event_id is only valid for resolve actions"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "stage": self.stage,
            "role": self.role,
            "action": self.action,
            "occurred_at": self.occurred_at,
            "note": self.note,
            "evidence_refs": list(self.evidence_refs),
            "actor_ref": self.actor_ref,
            "ruleset_version": self.ruleset_version,
            "guidance_catalog_version": self.guidance_catalog_version,
            "resolves_event_id": self.resolves_event_id,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReviewEvent":
        if not isinstance(payload, dict):
            raise ReviewPacketError("event must be an object")
        _reject_raw_posting(payload)
        evidence_refs = payload.get("evidence_refs", [])
        if not isinstance(evidence_refs, list):
            raise ReviewPacketError("evidence_refs must be a list")
        return cls(
            event_id=payload.get("event_id"),
            stage=payload.get("stage"),
            role=payload.get("role"),
            action=payload.get("action"),
            occurred_at=payload.get("occurred_at"),
            note=payload.get("note", ""),
            evidence_refs=tuple(evidence_refs),
            actor_ref=payload.get("actor_ref"),
            ruleset_version=payload.get("ruleset_version", ""),
            guidance_catalog_version=payload.get("guidance_catalog_version", ""),
            resolves_event_id=payload.get("resolves_event_id"),
        )


@dataclass(frozen=True)
class ReviewPacket:
    """Role-based review coordination data with an explicit local-only boundary."""

    packet_id: str
    posting_fingerprint: str
    ruleset_version: str
    guidance_catalog_version: str
    created_at: str
    events: tuple[ReviewEvent, ...] = field(default_factory=tuple)
    schema_version: str = REVIEW_PACKET_SCHEMA_VERSION
    raw_posting_included: bool = False

    def __post_init__(self) -> None:
        _bounded_id(self.packet_id, "packet_id")
        _fingerprint(self.posting_fingerprint)
        _timestamp(self.created_at, "created_at")
        if self.schema_version != REVIEW_PACKET_SCHEMA_VERSION:
            raise ReviewPacketError("unsupported review packet schema_version")
        if self.raw_posting_included:
            raise ReviewPacketError("raw_posting_included must remain false")
        if not isinstance(self.events, tuple):
            raise ReviewPacketError("events must be a tuple")
        for event in self.events:
            if not isinstance(event, ReviewEvent):
                raise ReviewPacketError("events must contain ReviewEvent values")
        event_ids = [event.event_id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            raise ReviewPacketError("events must not contain duplicate event_id values")
        event_by_id = {event.event_id: event for event in self.events}
        for event in self.events:
            if event.resolves_event_id is None:
                if event.action == "resolve":
                    raise ReviewPacketError(
                        "resolve events require resolves_event_id"
                    )
                continue
            target = event_by_id.get(event.resolves_event_id)
            if target is None:
                raise ReviewPacketError(
                    "resolves_event_id must reference an event in this packet"
                )
            if target.action not in {"edit_requested", "escalate"}:
                raise ReviewPacketError(
                    "resolve events must target an edit_requested or escalate event"
                )
        for field_name in ("ruleset_version", "guidance_catalog_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or len(value) > 128:
                raise ReviewPacketError(f"{field_name} must be a bounded string")

    @property
    def participating_roles(self) -> frozenset[str]:
        return frozenset(event.role for event in self.events)

    @property
    def missing_roles(self) -> tuple[str, ...]:
        participating = self.participating_roles
        return tuple(role for role in REVIEW_ROLES if role not in participating)

    @property
    def issue_statuses(self) -> tuple[dict[str, Any], ...]:
        resolved = {
            event.resolves_event_id
            for event in self.events
            if event.action == "resolve" and event.resolves_event_id is not None
        }
        return tuple(
            {
                "event_id": event.event_id,
                "action": event.action,
                "resolved": event.event_id in resolved,
            }
            for event in self.events
            if event.action in {"edit_requested", "escalate"}
        )

    @property
    def open_issue_count(self) -> int:
        return sum(1 for issue in self.issue_statuses if not issue["resolved"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "packet_id": self.packet_id,
            "posting_fingerprint": self.posting_fingerprint,
            "ruleset_version": self.ruleset_version,
            "guidance_catalog_version": self.guidance_catalog_version,
            "created_at": self.created_at,
            "raw_posting_included": False,
            "events": [event.to_dict() for event in self.events],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReviewPacket":
        if not isinstance(payload, dict):
            raise ReviewPacketError("review packet must be an object")
        _reject_raw_posting(payload)
        events = payload.get("events", [])
        if not isinstance(events, list):
            raise ReviewPacketError("events must be a list")
        return cls(
            schema_version=payload.get("schema_version"),
            packet_id=payload.get("packet_id"),
            posting_fingerprint=payload.get("posting_fingerprint"),
            ruleset_version=payload.get("ruleset_version", ""),
            guidance_catalog_version=payload.get("guidance_catalog_version", ""),
            created_at=payload.get("created_at"),
            raw_posting_included=payload.get("raw_posting_included", False),
            events=tuple(ReviewEvent.from_dict(item) for item in events),
        )

    @classmethod
    def from_json(cls, value: str) -> "ReviewPacket":
        try:
            payload = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ReviewPacketError("review packet JSON is invalid") from exc
        return cls.from_dict(payload)
