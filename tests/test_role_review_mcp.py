from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest

import mcp_server.server as server
import mcp_server.storage as storage
from core import ReviewEvent, posting_fingerprint
from mcp_server.storage import LocalReviewPacketStore


def test_local_review_packet_store_appends_roles_without_posting_text(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        store = LocalReviewPacketStore(tmp_path / "review-packets.json")
        original_store = server.review_packet_store
        server.review_packet_store = store
        try:
            created = await server.start_role_review(
                "여성만 지원 가능\nprivate-posting-marker"
            )
            review_packet = created["review_packet"]
            fingerprint = review_packet["posting_fingerprint"]
            packet_id = review_packet["packet_id"]

            updated = server.record_review_event(
                packet_id,
                fingerprint,
                "design",
                "job_sme",
                "edit_requested",
                note="직무관련성 근거를 공고에 명시해야 함",
                evidence_refs=["Q-DIST-002"],
                actor_ref="sme-1",
            )
            loaded = server.get_role_review(packet_id)

            assert updated["participating_roles"] == ["chair", "job_sme"]
            assert updated["missing_roles"] == [
                "hr_owner",
                "interviewer",
                "policy_reviewer",
                "auditor",
                "candidate_advocate",
            ]
            assert loaded["event_count"] == 2
            assert "private-posting-marker" not in json.dumps(
                json.loads((tmp_path / "review-packets.json").read_text(encoding="utf-8")),
                ensure_ascii=False,
            )
            assert loaded["review_packet"]["events"][1]["role"] == "job_sme"
        finally:
            server.review_packet_store = original_store

    anyio.run(exercise)


def test_record_review_event_requires_matching_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalReviewPacketStore(tmp_path / "review-packets.json")
    monkeypatch.setattr(server, "review_packet_store", store)

    async def exercise() -> None:
        created = await server.start_role_review("간단한 채용 공고")
        packet = created["review_packet"]
        with pytest.raises(ValueError, match="일치하지 않습니다"):
            server.record_review_event(
                packet["packet_id"],
                posting_fingerprint("다른 공고"),
                "analysis",
                "auditor",
                "note",
            )

    anyio.run(exercise)


def test_local_review_packet_store_purges_without_returning_packet_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalReviewPacketStore(tmp_path / "review-packets.json")
    monkeypatch.setattr(server, "review_packet_store", store)

    async def exercise() -> None:
        created = await server.start_role_review("삭제 수명주기 검토 공고")
        packet_id = created["review_packet"]["packet_id"]

        purged = server.purge_role_review(packet_id)

        assert purged == {
            "status": "purged_locally",
            "deleted": True,
            "packet_id": packet_id,
        }
        assert not (tmp_path / "review-packets.json").exists()
        assert server.purge_role_review(packet_id) == {
            "status": "not_found",
            "deleted": False,
            "packet_id": packet_id,
        }

    anyio.run(exercise)


def test_local_review_packet_store_validates_packet_id_before_append(
    tmp_path: Path,
) -> None:
    store = LocalReviewPacketStore(tmp_path / "review-packets.json")

    with pytest.raises(ValueError, match="packet_id"):
        store.append("../outside", None)  # type: ignore[arg-type]


def test_append_rejects_event_with_mismatched_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalReviewPacketStore(tmp_path / "review-packets.json")
    monkeypatch.setattr(server, "review_packet_store", store)

    async def exercise() -> None:
        created = await server.start_role_review("버전 불일치 검토 공고")
        packet = created["review_packet"]
        event = ReviewEvent(
            event_id="evt-mismatched-version",
            stage="analysis",
            role="auditor",
            action="note",
            occurred_at="2026-09-12T00:00:00+00:00",
            note="버전 불일치 이벤트",
            ruleset_version="different-ruleset",
            guidance_catalog_version=packet["guidance_catalog_version"],
        )

        with pytest.raises(ValueError):
            store.append(packet["packet_id"], event)
        assert store.get(packet["packet_id"]).to_dict()["events"]

    anyio.run(exercise)


def test_targeted_review_reads_survive_an_unrelated_invalid_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalReviewPacketStore(tmp_path / "review-packets.json")
    monkeypatch.setattr(server, "review_packet_store", store)

    async def exercise() -> None:
        first = await server.start_role_review("첫 번째 검토 공고")
        second = await server.start_role_review("두 번째 검토 공고")
        first_id = first["review_packet"]["packet_id"]
        second_id = second["review_packet"]["packet_id"]

        persisted = json.loads(
            (tmp_path / "review-packets.json").read_text(encoding="utf-8")
        )
        persisted[first_id]["events"] = [{"corrupted": True}]
        (tmp_path / "review-packets.json").write_text(
            json.dumps(persisted), encoding="utf-8"
        )

        loaded = server.get_role_review(second_id)

        assert loaded["review_packet"]["packet_id"] == second_id
        assert loaded["event_count"] == 1
        updated = server.record_review_event(
            second_id,
            second["review_packet"]["posting_fingerprint"],
            "evaluation",
            "auditor",
            "note",
            note="정상 패킷의 독립 검토 기록",
            evidence_refs=["Q-DIST-002"],
        )
        assert updated["review_packet"]["packet_id"] == second_id
        assert len(updated["review_packet"]["events"]) == 2
        with pytest.raises(ValueError):
            server.get_role_review(first_id)

    anyio.run(exercise)


def test_start_review_survives_an_unrelated_invalid_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalReviewPacketStore(tmp_path / "review-packets.json")
    monkeypatch.setattr(server, "review_packet_store", store)

    async def exercise() -> None:
        first = await server.start_role_review("첫 번째 손상 격리 공고")
        first_id = first["review_packet"]["packet_id"]
        persisted = json.loads(
            (tmp_path / "review-packets.json").read_text(encoding="utf-8")
        )
        persisted[first_id]["events"] = [{"corrupted": True}]
        (tmp_path / "review-packets.json").write_text(
            json.dumps(persisted), encoding="utf-8"
        )

        second = await server.start_role_review("두 번째 손상 격리 공고")

        assert second["review_packet"]["packet_id"] != first_id
        with pytest.raises(ValueError):
            server.get_role_review(first_id)

    anyio.run(exercise)


def test_targeted_read_rejects_packet_that_exceeds_event_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalReviewPacketStore(tmp_path / "review-packets.json")
    monkeypatch.setattr(server, "review_packet_store", store)

    async def exercise() -> None:
        created = await server.start_role_review("이벤트 상한 검증 공고")
        packet = created["review_packet"]
        packet_id = packet["packet_id"]
        events = list(packet["events"])
        for index in range(256):
            event = dict(packet["events"][0])
            event["event_id"] = f"evt-over-limit-{index}"
            events.append(event)
        packet["events"] = events
        (tmp_path / "review-packets.json").write_text(
            json.dumps({packet_id: packet}), encoding="utf-8"
        )

        with pytest.raises(ValueError, match="256"):
            store.get(packet_id)

    anyio.run(exercise)
def test_targeted_reads_and_append_reject_oversized_persisted_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalReviewPacketStore(tmp_path / "review-packets.json")
    monkeypatch.setattr(server, "review_packet_store", store)

    async def exercise() -> None:
        created = await server.start_role_review("패킷 바이트 상한 검증 공고")
        packet = created["review_packet"]
        packet_id = packet["packet_id"]
        events = list(packet["events"])
        for index in range(65):
            event = dict(packet["events"][0])
            event["event_id"] = f"evt-byte-limit-{index}"
            event["note"] = "x" * 4000
            events.append(event)
        packet["events"] = events
        (tmp_path / "review-packets.json").write_text(
            json.dumps({packet_id: packet}), encoding="utf-8"
        )

        with pytest.raises(ValueError):
            store.get(packet_id)
        with pytest.raises(ValueError):
            store.append(
                packet_id,
                ReviewEvent(
                    event_id="evt-after-byte-limit",
                    stage="evaluation",
                    role="auditor",
                    action="note",
                    occurred_at="2026-09-12T00:00:00+00:00",
                    note="정상 범위의 추가 메모",
                    ruleset_version=packet["ruleset_version"],
                    guidance_catalog_version=packet["guidance_catalog_version"],
                ),
            )

    anyio.run(exercise)


def test_review_packet_store_enforces_global_byte_limit_and_full_purge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "review-packets.json"
    store = LocalReviewPacketStore(path)
    monkeypatch.setattr(storage, "MAX_REVIEW_STORE_BYTES", 32)
    path.write_text("{" + "x" * 64 + "}", encoding="utf-8")

    with pytest.raises(ValueError, match="byte limit"):
        store.get("packet-1")

    assert store.purge() is True
    assert not path.exists()

    monkeypatch.setattr(server, "review_packet_store", store)

    async def create() -> None:
        await server.start_role_review("global byte limit")

    with pytest.raises(ValueError, match="byte limit"):
        anyio.run(create)
    assert not path.exists()
def test_resolve_event_closes_a_recorded_edit_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalReviewPacketStore(tmp_path / "review-packets.json")
    monkeypatch.setattr(server, "review_packet_store", store)

    async def exercise() -> None:
        created = await server.start_role_review("resolve workflow")
        packet = created["review_packet"]
        updated = server.record_review_event(
            packet["packet_id"],
            packet["posting_fingerprint"],
            "design",
            "job_sme",
            "edit_requested",
            note="직무 요건 근거를 공고에 보강해야 합니다.",
            evidence_refs=["Q-DIST-002"],
        )
        issue_event_id = updated["review_packet"]["events"][-1]["event_id"]

        with pytest.raises(ValueError, match="requires resolves_event_id"):
            server.record_review_event(
                packet["packet_id"],
                packet["posting_fingerprint"],
                "implementation",
                "hr_owner",
                "resolve",
                note="연결 대상 없는 해결 기록",
            )

        resolved = server.record_review_event(
            packet["packet_id"],
            packet["posting_fingerprint"],
            "implementation",
            "hr_owner",
            "resolve",
            note="직무 요건 근거를 공고에 반영했습니다.",
            evidence_refs=["Q-DIST-002"],
            resolves_event_id=issue_event_id,
        )
        assert resolved["open_issue_count"] == 0
        loaded = server.get_role_review(packet["packet_id"])
        assert loaded["open_issue_count"] == 0
        assert loaded["issue_statuses"] == [
            {
                "event_id": issue_event_id,
                "action": "edit_requested",
                "resolved": True,
            }
        ]

    anyio.run(exercise)
