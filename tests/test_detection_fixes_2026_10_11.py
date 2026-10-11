"""Synthetic development regressions for the 2026-10-11 detection fixes.

Hand-written cases, not human-labelled gold data (docs/detection-fixes-2026-10-11.md):

* DET-7: "서류 반환 불가" is RETURN-001's refusal, so it must not also mark the
  document-return notice as found; a return on request stated in the passive
  ("반환 요청이 있는 경우 반환됩니다") is a notice.
* DET-6: Q-INTER-007 shares Q-PROC-011's patterns, and now its pay-negotiation
  exclusions too ("급여는 면접 시 협의").

Every case also runs through the browser engine and must match Python exactly.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess

import pytest
import yaml

from core import FairpostEngine


ROOT = Path(__file__).resolve().parents[1]
RETURN_QUESTION = "Q-INFO-004"

# Refusals: RETURN-001 is reported and the notice slot stays not found.
RETURN_REFUSALS = {
    "bare-refusal": "제출서류\n서류 반환 불가",
    "refusal-in-sentence": "유의사항\n제출하신 서류 반환 불가합니다.",
    "recruitment-documents-refusal": "유의사항\n채용서류 반환 불가",
}

# Refusal wording without the RETURN-001 pattern: still not a notice.
RETURN_NOT_NOTICE = {
    "topic-particle-refusal": "유의사항\n제출 서류 반환은 하지 않습니다.",
    "passive-refusal": "유의사항\n제출 서류 반환되지 않습니다.",
}

# Stated return notices. (text, components that must be found)
RETURN_NOTICES = {
    "passive-on-request": (
        "유의사항\n※ 불합격자가 발표 후 14일 이내 반환 요청을 하면 우편으로 반환됩니다.",
        ["return_period"]),
    "may-request": ("유의사항\n지원자는 최종 발표 뒤 제출 서류의 반환을 요청할 수 있습니다.", []),
    "returned-on-request-polite": ("유의사항\n반환 요청 시 우편으로 반환해 드립니다.", []),
    "claim-period": ("유의사항\n채용서류 반환 청구는 최종 발표 후 14일 이내 가능합니다.", ["return_period"]),
    # A refusal next to a real return path: both are reported.
    "refusal-with-claim-path": (
        "유의사항\n서류 반환 불가, 단 채용서류 반환 청구 시 14일 이내 반환", ["return_period"]),
}


INTERVIEW_QUESTIONS = {"Q-INTER-007", "Q-PROC-011"}

# Pay negotiated at the interview: no interview question from that line.
PAY_AT_INTERVIEW = {
    "pay-negotiated-at-interview": "근무조건\n급여: 면접 시 협의",
    "salary-decided-after-interview": "근무조건\n연봉은 면접 후 결정",
    "pay-agreed-at-interview": "급여조건\n월급 (면접시 합의)",
}

# Real interviews still raise Q-INTER-007, also next to a pay line.
REAL_INTERVIEWS = {
    "interview-stage": "전형절차\n서류전형 후 면접",
    "interview-and-pay-on-other-lines": "전형절차\n서류전형 → 면접전형\n근무조건\n급여: 면접 시 협의",
    "stage-list-after-pay-line": "급여: 면접 후 결정, 선발: 서류전형 후 면접 진행",
    "hiring-decision-after-interview": "채용 안내\n- 1차 AI 면접 실시 후 대면 면접으로 최종 결정",
    "interview-schedule": "일정\n면접 일정은 개별 안내합니다.",
}


def _check(text: str) -> dict:
    return FairpostEngine().check(text).to_dict()


def _slot(result: dict, slot_id: str) -> dict:
    return next(s for s in result["slots"] if s["slot"] == slot_id)


def _ids(result: dict) -> set[str]:
    return {f["id"] for f in result["findings"]} | {q["id"] for q in result["questions"]}


@pytest.mark.parametrize("text", RETURN_REFUSALS.values(), ids=RETURN_REFUSALS.keys())
def test_return_refusal_is_a_finding_not_a_notice(text: str) -> None:
    result = _check(text)
    assert "RETURN-001" in {f["id"] for f in result["findings"]}
    slot = _slot(result, "document_return")
    assert slot["found"] is False
    assert slot["evidence"] is None
    assert RETURN_QUESTION in _ids(result)


@pytest.mark.parametrize("text", RETURN_NOT_NOTICE.values(), ids=RETURN_NOT_NOTICE.keys())
def test_other_refusal_wording_is_not_a_notice(text: str) -> None:
    assert _slot(_check(text), "document_return")["found"] is False


@pytest.mark.parametrize(("text", "components"), RETURN_NOTICES.values(), ids=RETURN_NOTICES.keys())
def test_stated_return_notice_is_found(text: str, components: list[str]) -> None:
    result = _check(text)
    slot = _slot(result, "document_return")
    assert slot["found"] is True
    assert slot["section"] == "유의사항"
    assert slot["components_found"] == components
    # Q-INFO-004 is no longer asked because the notice is missing (a RETURN-001
    # finding may still link it).
    assert not any(
        q["id"] == RETURN_QUESTION and q["trigger_reason"] == "absence" for q in result["questions"]
    )


def test_refusal_lookahead_is_on_both_return_patterns() -> None:
    slots = yaml.safe_load((ROOT / "data" / "slots.yaml").read_text(encoding="utf-8"))
    patterns = slots["document_return"]["accept_patterns"]
    for literal in ("채용서류 반환", "서류 반환"):
        assert literal not in patterns
    assert sum("(?!" in p and "불가|하지|되지" in p for p in patterns) == 2


@pytest.mark.parametrize("text", PAY_AT_INTERVIEW.values(), ids=PAY_AT_INTERVIEW.keys())
def test_pay_negotiated_at_the_interview_raises_no_interview_question(text: str) -> None:
    assert not INTERVIEW_QUESTIONS & _ids(_check(text))


@pytest.mark.parametrize("text", REAL_INTERVIEWS.values(), ids=REAL_INTERVIEWS.keys())
def test_real_interview_still_raises_the_interview_question(text: str) -> None:
    question = next(q for q in _check(text)["questions"] if q["id"] == "Q-INTER-007")
    assert question["trigger_reason"] == "presence"
    assert "면접" in question["matched_text"]


def test_interview_question_exclusions_are_scoped_to_their_candidate() -> None:
    # The same patterns and pay-negotiation wording as Q-PROC-011, but an
    # exclusion only removes the 면접 it contains.
    rules = yaml.safe_load((ROOT / "data" / "rules" / "questions.yaml").read_text(encoding="utf-8"))
    triggers = {rule["id"]: rule["trigger"] for rule in rules if rule["id"] in INTERVIEW_QUESTIONS}
    assert triggers["Q-INTER-007"]["patterns"] == triggers["Q-PROC-011"]["patterns"]
    inter, proc = triggers["Q-INTER-007"]["exclude"], triggers["Q-PROC-011"]["exclude"]
    assert len(inter) == len(proc) == 2
    assert inter[0]["term"] == proc[0]["term"]
    assert inter[1]["term"].replace(
        "(?<!최종)(?<!최종\\s)(?<!합격)(?<!합격\\s)(?<!채용)(?<!채용\\s)결정", "결정") == proc[1]["term"]
    assert all(item["overlap_candidate"] is True and item["window"] == 35 for item in inter)


# --- Python / web parity ---------------------------------------------------------


def _web_check_batch(texts: list[str], tmp_path: Path) -> list[dict]:
    batch = tmp_path / "web-batch.json"
    batch.write_text(
        json.dumps([base64.b64encode(t.encode("utf-8")).decode("ascii") for t in texts]),
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["node", "tests/js_runner.cjs", "--batch", str(batch)],
        cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8",
    )
    results = json.loads(completed.stdout)
    assert len(results) == len(texts)
    return results


def _parity_cases() -> dict[str, str]:
    cases: dict[str, str] = {}
    for prefix, table in (
        ("return-refusal", RETURN_REFUSALS),
        ("return-not-notice", RETURN_NOT_NOTICE),
        ("return-notice", {k: v[0] for k, v in RETURN_NOTICES.items()}),
        ("pay-at-interview", PAY_AT_INTERVIEW),
        ("real-interview", REAL_INTERVIEWS),
    ):
        for key, text in table.items():
            cases[f"{prefix}:{key}"] = text
            cases[f"{prefix}:{key}:crlf"] = text.replace("\n", "\r\n")
            cases[f"{prefix}:{key}:in-posting"] = f"2026년 사무직 채용\n전형절차\n서류전형 → 면접전형\n{text}"
    return cases


@pytest.mark.requires_node
def test_python_and_web_agree_on_every_case(tmp_path: Path) -> None:
    cases = _parity_cases()
    labels = list(cases)
    web_results = _web_check_batch([cases[label] for label in labels], tmp_path)
    engine = FairpostEngine()
    for label, web in zip(labels, web_results, strict=True):
        assert web == engine.check(cases[label]).to_dict(), label
