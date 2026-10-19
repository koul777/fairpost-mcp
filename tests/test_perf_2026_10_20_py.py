"""2026-10-20 Python 속도 개선이 결과를 바꾸지 않는지 확인한다.

직렬화(``CheckResult.to_dict``)는 ``dataclasses.asdict`` 와 같은 값, 같은 타입,
같은 키 순서를 내야 한다.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from core import FairpostEngine
from core.schema import (
    Basis,
    CheckResult,
    Finding,
    Question,
    QuestionReference,
    SlotStatus,
)

POSTINGS = [
    "",
    "채용 안내\n만 20세 이상 39세 이하 남성만 지원할 수 있습니다.\n면접은 AI 면접으로 진행합니다.",
    (
        "[채용개요]\n사무 보조 1명\n[자격요건]\n① 해당 분야 경력 2년 이상\n② 여성 우대\n"
        "[전형절차]\n서류전형 → 면접전형 → 최종합격\n[일정]\n접수기간: 상시\n"
        "[문의처]\n인사팀 채용 담당자에게 문의하십시오.\n"
    ),
    "결혼 여부와 출신 학교는 기재하지 마세요.\n혼인 여부를 기재하세요.\n키 175cm 이상 필수",
    "ＡＩ 면접을 실시합니다. 면​접 결과는 별도 통보 없이 불합격 처리합니다. ㎡ ㆍ 합니다 하신 분",
    "급여\n연봉 협의\n" + "\n".join(f"{index}. 담당업무 안내 항목" for index in range(30)),
    "가나다 " * 400,
]


def _asdict_result(result: CheckResult) -> dict:
    payload = dataclasses.asdict(result)
    for finding in payload["findings"]:
        if finding["offset"] is not None:
            finding["offset"] = list(finding["offset"])
    for question in payload["questions"]:
        if question["offset"] is not None:
            question["offset"] = list(question["offset"])
    return payload


@pytest.fixture(scope="module")
def engine() -> FairpostEngine:
    return FairpostEngine()


# --- to_dict ----------------------------------------------------------------------


def test_to_dict_equals_asdict_for_engine_results(engine: FairpostEngine) -> None:
    seen_finding = seen_reference = False
    for posting in POSTINGS:
        result = engine.check(posting)
        expected = _asdict_result(result)
        actual = result.to_dict()
        assert actual == expected
        # repr tells list from tuple and int from bool, and keeps the key order.
        assert repr(actual) == repr(expected)
        assert json.dumps(actual, ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)
        seen_finding = seen_finding or bool(result.findings)
        seen_reference = seen_reference or any(q.reference for q in result.questions)
    assert seen_finding and seen_reference


def test_to_dict_equals_asdict_for_hand_built_result() -> None:
    basis = Basis(type="statute", law="법", article="제1조", title="제목", text="본문")
    result = CheckResult(
        findings=[
            Finding(
                id="X-001",
                dimension="d",
                message="m",
                matched_text=None,
                offset=None,
                section=None,
                severity=None,
                basis=basis,
                alternatives=[],
                provenance_method="p",
                book_ref="b",
            ),
            Finding(
                id="X-002",
                dimension="d",
                message="m",
                matched_text="가",
                offset=(1, 2),
                section="전체",
                severity="high",
                basis=Basis(type="consensus"),
                alternatives=["대안"],
                provenance_method="p",
                book_ref="b",
            ),
        ],
        slots=[
            SlotStatus("s", "l", True, ["a"], 2, "근거", "전체"),
            SlotStatus("t", "l", False, [], 0, None, None),
        ],
        questions=[
            Question("Q-001", "d", "q", [], "t", "b", "posting", None),
            Question(
                "Q-002",
                "d",
                "q",
                ["후속"],
                "t",
                "b",
                "common",
                "답",
                matched_text="가",
                offset=(3, 4),
                section="전체",
                reference=QuestionReference("guide", pages=[1, 2], sections=["가"], year=2024),
                trigger_reason="finding",
                linked_findings=["X-001"],
                priority=1,
            ),
        ],
        counts={"findings": 2, "not_found": 1, "questions": 2},
        ruleset_version="v",
        statute_snapshot_date="2026-01-01",
        statute_notice="n",
        disclaimer="d",
    )
    expected = _asdict_result(result)
    assert result.to_dict() == expected
    assert repr(result.to_dict()) == repr(expected)


def test_to_dict_returns_independent_containers(engine: FairpostEngine) -> None:
    result = engine.check(POSTINGS[2])
    first = result.to_dict()
    first["counts"]["findings"] = -1
    for question in first["questions"]:
        question["follow_up"].append("변경")
        question["linked_findings"].append("변경")
        if question["reference"] and question["reference"]["sections"] is not None:
            question["reference"]["sections"].append("변경")
    second = result.to_dict()
    assert second == _asdict_result(result)
    assert all("변경" not in question.follow_up for question in result.questions)
