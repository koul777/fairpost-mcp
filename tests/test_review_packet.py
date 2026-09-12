from __future__ import annotations

from dataclasses import replace

import pytest

from core import ReviewEvent, ReviewPacket, ReviewPacketError, posting_fingerprint


CREATED_AT = "2026-09-11T09:00:00+09:00"


def _event(event_id: str = "evt-chair-1", role: str = "chair") -> ReviewEvent:
    return ReviewEvent(
        event_id=event_id,
        stage="analysis",
        role=role,
        action="confirm",
        occurred_at=CREATED_AT,
        note="직무분석 근거와 공고 요건의 연결을 확인함",
        evidence_refs=("Q-DIST-002", "ncs-process"),
        actor_ref="local-reviewer",
        ruleset_version="rules-abc",
        guidance_catalog_version="guidance-def",
    )


def _packet(*events: ReviewEvent) -> ReviewPacket:
    return ReviewPacket(
        packet_id="packet-001",
        posting_fingerprint=posting_fingerprint("로컬 공고 초안"),
        ruleset_version="rules-abc",
        guidance_catalog_version="guidance-def",
        created_at=CREATED_AT,
        events=events,
    )


def test_review_packet_round_trips_without_raw_posting() -> None:
    packet = _packet(_event(), _event("evt-sme-1", "job_sme"))

    restored = ReviewPacket.from_json(packet.to_json())

    assert restored == packet
    assert restored.participating_roles == {"chair", "job_sme"}
    assert restored.missing_roles == (
        "hr_owner",
        "interviewer",
        "policy_reviewer",
        "auditor",
        "candidate_advocate",
    )
    assert "로컬 공고 초안" not in packet.to_json()
    assert packet.to_dict()["raw_posting_included"] is False


def test_review_packet_rejects_raw_posting_payload() -> None:
    payload = _packet(_event()).to_dict()
    payload["posting_text"] = "지원자 개인정보가 포함된 원문"

    with pytest.raises(ReviewPacketError, match="raw posting"):
        ReviewPacket.from_dict(payload)


def test_review_packet_rejects_duplicate_event_ids() -> None:
    with pytest.raises(ReviewPacketError, match="duplicate event_id"):
        _packet(_event(), _event())


def test_review_event_rejects_naive_timestamps_and_unknown_roles() -> None:
    event_payload = _event("evt-1").to_dict()
    event_payload["occurred_at"] = "2026-09-11T09:00:00"
    with pytest.raises(ReviewPacketError, match="timezone"):
        ReviewEvent.from_dict(event_payload)

    with pytest.raises(ReviewPacketError, match="unknown role"):
        replace(_event("evt-2"), role="unknown")


@pytest.mark.parametrize(
    "sensitive_note",
    [
        "contact reviewer@example.com",
        "연락처 010-1234-5678",
        "식별번호 900101-1234567",
    ],
)
def test_review_event_rejects_high_confidence_personal_identifiers(
    sensitive_note: str,
) -> None:
    with pytest.raises(ReviewPacketError, match="direct personal identifiers"):
        replace(_event("evt-sensitive"), note=sensitive_note)


def test_posting_fingerprint_is_sha256() -> None:
    assert posting_fingerprint("abc") == (
        "ba7816bf8f01cfea414140de5dae2223"
        "b00361a396177a9cb410ff61f20015ad"
    )


def test_resolve_event_closes_an_issue_and_reports_status() -> None:
    requested = replace(_event("evt-request"), action="edit_requested")
    resolved = replace(
        _event("evt-resolve", "auditor"),
        action="resolve",
        resolves_event_id="evt-request",
    )

    packet = _packet(requested, resolved)

    assert packet.open_issue_count == 0
    assert packet.issue_statuses == (
        {"event_id": "evt-request", "action": "edit_requested", "resolved": True},
    )
    assert ReviewPacket.from_json(packet.to_json()) == packet


def test_resolve_event_rejects_dangling_or_non_issue_targets() -> None:
    unlinked = replace(_event("evt-unlinked", "auditor"), action="resolve")
    with pytest.raises(ReviewPacketError, match="require resolves_event_id"):
        _packet(_event("evt-request"), unlinked)

    dangling = replace(
        _event("evt-resolve", "auditor"),
        action="resolve",
        resolves_event_id="evt-missing",
    )
    with pytest.raises(ReviewPacketError, match="reference an event"):
        _packet(_event("evt-request"), dangling)

    wrong_target = replace(
        _event("evt-resolve", "auditor"),
        action="resolve",
        resolves_event_id="evt-confirm",
    )
    with pytest.raises(ReviewPacketError, match="edit_requested or escalate"):
        _packet(_event("evt-confirm"), wrong_target)


def test_resolves_event_id_is_only_valid_for_resolve_action() -> None:
    with pytest.raises(ReviewPacketError, match="only valid"):
        replace(_event("evt-confirm"), resolves_event_id="evt-request")


def test_review_packet_rejects_non_event_values_with_contract_error() -> None:
    with pytest.raises(ReviewPacketError, match="ReviewEvent values"):
        _packet(object())  # type: ignore[arg-type]
