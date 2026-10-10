"""Role participation must be the same in Python, MCP output and the web panel.

The automatic chair event that ``start_role_review`` creates (reserved actor
``system-chair``) records that a review flow exists, not that anyone reviewed.
web/app.js never counted it as participation; ``ReviewPacket.participating_roles``
and therefore ``missing_roles`` in the MCP responses used to, so the chair role
was reported as participating in Python/MCP and as "no record yet" in the web
for the very same packet.

The cross-check tests run the real web bundle through
``tests/web_role_participation_runner.cjs``; they do not reimplement the web
rule.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Any

import anyio
import pytest

import mcp_server.server as server
from core import ReviewEvent, ReviewPacket, posting_fingerprint
from core.review_packet import (
    REVIEW_ROLES,
    SYSTEM_CHAIR_ACTOR_REF,
    SYSTEM_CHAIR_NOTE,
)
from mcp_server.review import prepare_hr_review_packet
from mcp_server.storage import LocalReviewPacketStore


ROOT = Path(__file__).resolve().parents[1]
POSTING = "여성만 지원 가능"
CREATED_AT = "2026-10-10T09:00:00+09:00"


def _event(
    event_id: str,
    role: str,
    *,
    action: str = "confirm",
    actor_ref: str | None = "reviewer-1",
    note: str = "직무분석 근거와 공고 요건의 연결을 확인함",
    resolves_event_id: str | None = None,
) -> ReviewEvent:
    return ReviewEvent(
        event_id=event_id,
        stage="analysis",
        role=role,  # type: ignore[arg-type]
        action=action,  # type: ignore[arg-type]
        occurred_at=CREATED_AT,
        note=note,
        evidence_refs=("Q-DIST-002",),
        actor_ref=actor_ref,
        ruleset_version="rules-abc",
        guidance_catalog_version="guidance-def",
        resolves_event_id=resolves_event_id,
    )


def _system_chair(event_id: str = "evt-system-chair") -> ReviewEvent:
    """The chair event exactly as mcp_server.review creates it."""

    return _event(
        event_id,
        "chair",
        action="note",
        actor_ref=SYSTEM_CHAIR_ACTOR_REF,
        note=SYSTEM_CHAIR_NOTE,
    )


def _packet(*events: ReviewEvent) -> ReviewPacket:
    return ReviewPacket(
        packet_id="packet-parity",
        posting_fingerprint=posting_fingerprint(POSTING),
        ruleset_version="rules-abc",
        guidance_catalog_version="guidance-def",
        created_at=CREATED_AT,
        events=events,
    )


@dataclass(frozen=True)
class Case:
    name: str
    events: tuple[ReviewEvent, ...]
    participating: tuple[str, ...]  # expected, in REVIEW_ROLES order
    system_events: int  # events the web must render as "참여로 세지 않음"


CASES = (
    # Only the automatic chair event: nobody has reviewed anything yet.
    Case("system_only", (_system_chair(),), (), 1),
    # The reported divergence: one reviewer next to the automatic chair event.
    # Python/MCP used to answer participating {chair, job_sme}, missing 5;
    # the web answers participating {job_sme}, missing 6 (chair included).
    Case(
        "system_plus_reviewer",
        (_system_chair(), _event("evt-sme-1", "job_sme", actor_ref="sme-1")),
        ("job_sme",),
        1,
    ),
    # Human chair, several roles and a resolved issue next to the system event.
    Case(
        "mixed",
        (
            _system_chair(),
            _event("evt-chair-1", "chair", actor_ref="chair-1"),
            _event("evt-audit-1", "auditor", action="note", actor_ref=None),
            _event("evt-sme-request", "job_sme", action="edit_requested"),
            _event(
                "evt-hr-resolve",
                "hr_owner",
                action="resolve",
                resolves_event_id="evt-sme-request",
            ),
        ),
        ("chair", "hr_owner", "job_sme", "auditor"),
        1,
    ),
    # Every role has a human event: nothing is missing, system event or not.
    Case(
        "all_roles",
        (_system_chair(),)
        + tuple(_event(f"evt-{role}", role) for role in REVIEW_ROLES),
        REVIEW_ROLES,
        1,
    ),
    # A person writing as chair without an actor is a reviewer, not the system.
    Case(
        "unattributed_human_chair_note",
        (_event("evt-chair-note", "chair", action="note", actor_ref=None,
                note="공고 전체 흐름을 직접 점검함"),),
        ("chair",),
        0,
    ),
    # Unmarked chair note with the automatic text: the web upgrades such v1
    # events to system events when it loads them, so Python must too.
    Case(
        "unmarked_automatic_note",
        (_event("evt-legacy", "chair", action="note", actor_ref=None,
                note=SYSTEM_CHAIR_NOTE),),
        (),
        1,
    ),
    # The automatic text alone does not make an event system-generated when a
    # person is named as the actor or the action differs.
    Case(
        "automatic_text_with_named_actor",
        (_event("evt-named", "chair", action="note", actor_ref="chair-2",
                note=SYSTEM_CHAIR_NOTE),),
        ("chair",),
        0,
    ),
    Case(
        "automatic_text_other_action",
        (_event("evt-confirm", "chair", action="confirm", actor_ref=None,
                note=SYSTEM_CHAIR_NOTE),),
        ("chair",),
        0,
    ),
    # The web keys on the reserved actor alone, whatever role/action it carries.
    Case(
        "reserved_actor_on_other_role",
        (_event("evt-reserved", "job_sme", action="note",
                actor_ref=SYSTEM_CHAIR_ACTOR_REF),),
        (),
        1,
    ),
)
CASE_IDS = [case.name for case in CASES]


@pytest.fixture(scope="module")
def web_results() -> dict[str, Any]:
    payload = {
        "posting": POSTING,
        "cases": [
            {"name": case.name, "events": [e.to_dict() for e in case.events]}
            for case in CASES
        ],
    }
    completed = subprocess.run(
        ["node", "tests/web_role_participation_runner.cjs"],
        cwd=ROOT,
        input=json.dumps(payload, ensure_ascii=False),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_python_participation_excludes_system_generated_events(case: Case) -> None:
    packet = _packet(*case.events)

    assert packet.participating_roles == frozenset(case.participating)
    assert packet.missing_roles == tuple(
        role for role in REVIEW_ROLES if role not in case.participating
    )
    assert sum(event.is_system_generated for event in packet.events) == case.system_events


def test_system_only_packet_misses_every_role() -> None:
    packet = _packet(_system_chair())

    assert packet.participating_roles == frozenset()
    assert packet.missing_roles == REVIEW_ROLES
    assert packet.events[0].is_system_generated is True


def test_reviewer_event_next_to_system_chair_leaves_chair_missing() -> None:
    packet = _packet(_system_chair(), _event("evt-sme-1", "job_sme"))

    assert packet.participating_roles == {"job_sme"}
    assert packet.missing_roles == (
        "chair",
        "hr_owner",
        "interviewer",
        "policy_reviewer",
        "auditor",
        "candidate_advocate",
    )


def test_participation_survives_a_serialization_round_trip() -> None:
    packet = _packet(*CASES[2].events)  # "mixed"

    restored = ReviewPacket.from_json(packet.to_json())

    assert restored.participating_roles == packet.participating_roles
    assert restored.missing_roles == packet.missing_roles


def test_automatic_event_constants_match_the_mcp_producer() -> None:
    """start_role_review must keep emitting what core treats as automatic."""

    review = anyio.run(prepare_hr_review_packet, server.engine, POSTING)
    (event,) = review.review_packet.events

    assert event.role == "chair"
    assert event.actor_ref == SYSTEM_CHAIR_ACTOR_REF
    assert event.note == SYSTEM_CHAIR_NOTE
    assert event.is_system_generated is True
    assert review.review_packet.participating_roles == frozenset()
    assert review.review_packet.missing_roles == REVIEW_ROLES


def test_mcp_role_review_reports_system_chair_as_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        server, "review_packet_store", LocalReviewPacketStore(tmp_path / "packets.json")
    )

    async def exercise() -> None:
        created = await server.start_role_review(POSTING)
        packet = created["review_packet"]
        started = server.get_role_review(packet["packet_id"])
        assert started["participating_roles"] == []
        assert started["missing_roles"] == list(REVIEW_ROLES)

        recorded = server.record_review_event(
            packet["packet_id"],
            packet["posting_fingerprint"],
            "design",
            "job_sme",
            "confirm",
            note="직무관련성 근거 확인",
            actor_ref="sme-1",
        )
        assert recorded["participating_roles"] == ["job_sme"]
        assert recorded["missing_roles"] == [
            role for role in REVIEW_ROLES if role != "job_sme"
        ]
        loaded = server.get_role_review(packet["packet_id"])
        assert loaded["participating_roles"] == recorded["participating_roles"]
        assert loaded["missing_roles"] == recorded["missing_roles"]

    anyio.run(exercise)


def test_get_role_review_description_documents_the_rule() -> None:
    tools = anyio.run(server.mcp.list_tools)
    description = next(tool for tool in tools if tool.name == "get_role_review").description or ""

    assert SYSTEM_CHAIR_ACTOR_REF in description
    assert "participating_roles" in description
    assert "missing_roles" in description


@pytest.mark.requires_node
def test_web_constants_match_python(web_results: dict[str, Any]) -> None:
    constants = web_results["constants"]

    assert constants["system_actor_ref"] == SYSTEM_CHAIR_ACTOR_REF
    assert constants["system_note"] == SYSTEM_CHAIR_NOTE
    assert tuple(constants["role_order"]) == REVIEW_ROLES


@pytest.mark.requires_node
@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_python_and_web_report_the_same_roles(
    case: Case, web_results: dict[str, Any]
) -> None:
    web = next(item for item in web_results["cases"] if item["name"] == case.name)
    packet = _packet(*case.events)

    # The seeded packet really was loaded and rendered, so the comparison below
    # is about these events and not about a freshly started empty packet.
    assert web["loaded"] is True, web
    assert web["droppedEvents"] is False
    assert web["packetCount"] == 1
    assert web["eventItems"] == len(case.events)
    assert web["systemItems"] == sum(e.is_system_generated for e in packet.events)

    assert web["missingRoles"] == list(packet.missing_roles)
    assert web["participatingRoles"] == [
        role for role in REVIEW_ROLES if role in packet.participating_roles
    ]
    assert web["progress"].startswith(
        f"자기 기록 역할 {len(packet.participating_roles)}/7 · "
    )
    if not packet.missing_roles:
        assert web["missingText"].startswith("7개 역할 모두")


@pytest.mark.requires_node
def test_mcp_output_matches_the_web_for_a_started_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported scenario end to end: start_role_review + one reviewer."""

    monkeypatch.setattr(
        server, "review_packet_store", LocalReviewPacketStore(tmp_path / "packets.json")
    )

    async def exercise() -> dict[str, Any]:
        created = await server.start_role_review(POSTING)
        packet = created["review_packet"]
        server.record_review_event(
            packet["packet_id"],
            packet["posting_fingerprint"],
            "design",
            "job_sme",
            "confirm",
            note="직무관련성 근거 확인",
            actor_ref="sme-1",
        )
        return server.get_role_review(packet["packet_id"])

    mcp_output = anyio.run(exercise)
    completed = subprocess.run(
        ["node", "tests/web_role_participation_runner.cjs"],
        cwd=ROOT,
        input=json.dumps(
            {
                "posting": POSTING,
                "cases": [
                    {"name": "mcp", "events": mcp_output["review_packet"]["events"]}
                ],
            },
            ensure_ascii=False,
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    (web,) = json.loads(completed.stdout)["cases"]

    assert web["loaded"] is True, web
    assert mcp_output["missing_roles"] == web["missingRoles"]
    # The MCP field is alphabetical; the web lists roles in panel order.
    assert mcp_output["participating_roles"] == sorted(web["participatingRoles"])
