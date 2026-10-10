"""Synthetic development regressions for duty headings and stage words.

These are hand-written cases, not human-labelled gold data. They pin a defect
found in review of the slot extractor:

* a duty heading (담당업무, 주요 업무 ...) suppressed every slot from the heading
  to the next *known* heading, so a posting whose later headings were decorated
  ("● 전형절차"), plain titles ("채용 안내") or pasted example titles ("평가 기준")
  lost its selection stages, pay, schedule, contact point, AI notice and all
  interview questions;
* words that also name ordinary work ("고객 인터뷰", "민원 서류접수", "계약 서류
  검토") were counted as selection stages anywhere, while real stage lists
  written as list items ("- 인터뷰", "2. AI 영상면접") were read as labels.

Every case also runs through the browser engine and must match Python exactly.

Follow-ups (2026-10-10 evening): interview names with a qualifier ("최종 면접",
"임원 면접", "1차 면접") are selection stages under the same stage-context rule
as a bare 인터뷰, and a 문의처 heading body with a 15xx/16xx/18xx number or a
department name ("총무과", "인사과 채용담당") is a contact point.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import random
import re
import subprocess

import pytest
import yaml

from core import FairpostEngine
from core import extractor
from core.extractor import split_sections


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_PATH = ROOT / "data" / "posting_templates.yaml"

STAGES_QUESTION = "Q-PROC-001"
PAY_QUESTION = "Q-INFO-006"
SCHEDULE_QUESTION = "Q-PROC-003"
CONTACT_QUESTION = "Q-INFO-003"
AI_NOTICE_QUESTION = "Q-INFO-005"
INTERVIEW_QUESTIONS = {"Q-INTER-007", "Q-PROC-011"}
# Questions about AI tools and interviews that a duty heading used to silence.
AI_AND_INTERVIEW_QUESTIONS = {
    "Q-DIST-008", "Q-DIST-009", "Q-DIST-011", "Q-INFO-009", "Q-INTER-003",
    "Q-INTER-004", "Q-INTER-007", "Q-PROC-004", "Q-PROC-010", "Q-PROC-014",
}

# --- reported cases ----------------------------------------------------------

DUTY_THEN_DECORATED_SECTIONS = (
    "담당업무\n- 회계 및 세무 업무\n● 전형절차\n- 서류전형 → 면접전형 → 최종합격\n"
    "● 근무조건\n- 연봉 4,000만원\n● 접수기간\n- 2026. 8. 1. ~ 8. 12.\n● 문의\n- 인사팀 02-1234-5678"
)
DUTY_THEN_PLAIN_TITLE = "담당업무\n- 사무 보조\n채용 안내\n- 1차 AI 면접 실시 후 대면 면접으로 최종 결정"

# Work descriptions that use a stage word: no selection stage is disclosed.
DUTY_NOT_STAGE = {
    "customer-interview": "마케터 채용\n- 고객 인터뷰를 통해 서비스를 개선합니다",
    "civil-document-intake": "행정직 채용\n- 민원 서류접수 및 증명서 발급 업무",
    "contract-document-review": "법무 담당 채용\n- 계약 서류 검토 및 법률 자문",
    # An eligibility reference date, not a stage of this hiring.
    "eligibility-reference-date": "지원자격\n서류접수 마감일 기준 만 18세 이상인 자",
}

# Real stage lists that were missed. (text, slots that must be found)
STAGE_LISTS = {
    "numbered-review-and-ai-video": (
        "전형절차\n1. 서류 리뷰\n2. AI 영상면접\n3. 최종 면접", {"selection_stages", "ai_disclosure"}),
    "bulleted-review-and-interview": (
        "채용 절차\n- 지원서 리뷰\n- 인터뷰\n- 최종 합격", {"selection_stages"}),
    "numbered-title-after-numbered-duty": (
        "1. 담당업무\n- 사무 보조\n2. 채용 안내\n- 1차 AI 면접 실시", {"ai_disclosure"}),
    "box-title-after-circle-duties": (
        "□ 담당업무\n ○ 회계 결산\n ○ 고객 인터뷰 기획\n□ 채용 안내\n ○ 서류 검토 → 인터뷰",
        {"selection_stages"}),
    "bracket-title-after-dot-duties": (
        "[담당업무]\n· 회계 결산\n[채용 안내]\n· 서류 검토 → 인터뷰 → 최종 합격", {"selection_stages"}),
    "crlf-plain-title": (
        "담당업무\r\n- 사무 보조\r\n채용 안내\r\n- 1차 AI 면접 실시 후 대면 면접으로 최종 결정", {"ai_disclosure"}),
    "fullwidth-bullets": ("담당업무\n－ 사무 보조\n채용 안내\n－ 1차 AI 면접 실시", {"ai_disclosure"}),
    "zero-width-duty-heading": (
        "담당​업무\n- 사무 보조\n채용 안내\n- 1차 AI 면접 실시", {"ai_disclosure"}),
    "lone-cr-lines": ("담당업무\r- 사무 보조\r채용 안내\r- 1차 AI 면접 실시", {"ai_disclosure"}),
    "bulleted-final-interview": ("전형절차\n- 서류전형\n- 최종 면접", {"selection_stages"}),
}

# Interview names with a qualifier in a stage context: a selection stage with an
# interview component. (text, section of the evidence)
INTERVIEW_NAME_STAGES = {
    "bulleted-final-interview": ("전형절차\n- 서류전형\n- 최종 면접", "전형절차"),
    "procedure-two-interviews": ("전형절차\n임원 면접 및 실무진 면접", "전형절차"),
    # Decision: in a 전형절차 section even a note names the procedure's stage.
    "procedure-final-interview-note": ("전형절차\n최종 면접 시 복장 자유", "전형절차"),
    "schedule-in-person-interview": ("일정\n- 대면 면접: 2026년 8월 20일", "일정"),
    "ordinal-then-final": ("1차 면접 후 최종 면접", "전체"),
    "document-stage-then-team-interview": ("서류 전형 합격자에 한해 실무진 면접 실시", "전체"),
    "executive-interview-arrow": ("임원 면접 → 최종 합격", "전체"),
    "duty-then-plain-title-interviews": (DUTY_THEN_PLAIN_TITLE, "개요"),
}

# The same names in recruiting work or in a note outside a stage context.
INTERVIEW_NAME_NOT_STAGE = {
    "recruiter-duty-review-and-schedule": "담당업무\n- 채용 지원자 서류 검토 및 면접 일정 조율",
    "recruiter-duty-executive-interview": "담당업무\n- 임원 면접 일정 조율",
    "unheaded-executive-interview-work": "임원 면접 일정 조율 및 채용 행정 지원",
    "unheaded-technical-interview-work": "기술 면접 질문 개발 및 평가 도구 운영",
    "interviewer-training": "최종 면접관 교육 지원",
    "dress-code-note": "면접 시 복장 자유",
    "final-interview-dress-code-in-notes": "유의사항\n최종 면접 시 복장 자유",
}

# A 문의처 body with a representative number or a department, or a
# representative number with inquiry context. (text, section, component)
CONTACT_FOUND = {
    "representative-number-under-heading": ("문의처\n1588-1234", "문의처", "phone"),
    "representative-number-under-short-heading": ("문의\n1577-0000", "문의처", "phone"),
    "decorated-heading-bulleted-number": ("● 문의\n- 1644-1234", "문의처", "phone"),
    "department-under-short-heading": ("문의\n총무과", "문의처", "department"),
    "department-and-desk": ("문의처\n인사과 채용담당", "문의처", "department"),
    "team-under-contact-heading": ("연락처\n경영지원팀", "문의처", "department"),
    "duty-then-decorated-contact": ("담당업무\n- 회계 결산\n▶ 문의\n- 1588-1234", "문의처", "phone"),
    "representative-number-with-inquiry-word": ("채용 문의 전화 1577-0000", "전체", "phone"),
    "representative-number-after-phone-label": ("전화: 1588-1234", "전체", "phone"),
}

# Still no contact point.
CONTACT_NOT_FOUND = {
    "redaction-token": "문의\n[PHONE]",
    "redaction-token-under-full-heading": "문의처\n[PHONE]",
    "deferred-body": "문의처\n추후 안내",
    "representative-number-in-duty-text": "고객센터 1588-1234 운영 업무",
    "result-notice-body": "문의\n결과 발표 후 개별 안내",
    "change-notice-body": "문의처\n본 공고 내용은 내부 사정에 따라 변경될 수 있습니다",
    "result-word-body": "문의처\n합격결과는 홈페이지에 게시",
    "number-without-separator": "문의\n15881234",
}

# --- look-alikes whose outcome must not change -------------------------------

# Lines that still belong to the duty list: nothing in them is a hiring slot and
# no interview question is raised from them.
DUTY_BLOCK_KEPT = {
    "recruiter-duties": "담당업무\n- 채용 지원자 인터뷰 진행 및 서류 검토\n- 면접 일정 조율",
    "unmarked-duty-lines": "주요 업무\n고객 인터뷰 기획\nAI 면접 솔루션 운영",
    "indented-continuation": "담당업무\n- 회계 결산\n  AI 면접 데이터 관리 및 인터뷰 일정 조율",
    "sentence-after-items": "담당업무\n- 회계 결산\n고객 인터뷰를 진행합니다.",
    "note-after-items": "담당업무\n- 사무 보조\n※ 상세 업무는 입사 후 결정\n- 고객 인터뷰 기획",
    "blank-separated-items": "담당업무\n\n- 회계 결산\n\n- AI 면접 솔루션 운영",
}

# Stage words in a stage context are still selection stages. (text, section)
STAGE_CONTEXT_KEPT = {
    "named-interview-stage": ("실무 인터뷰는 화상으로 진행합니다.", "전체"),
    "arrow-list": ("서류 검토 → 인터뷰 → 최종 합격", "전체"),
    "ordinal-interviews": ("1차 인터뷰 후 2차 인터뷰를 진행합니다.", "전체"),
    "review-then-interview": ("서류 검토 후 면접", "전체"),
    "schedule-section-intake": ("일정\n서류접수: 2026년 8월 1일 ~ 8월 12일", "일정"),
    "numbered-alias-heading": ("1. 전형절차\n서류전형 → 면접전형", "전형절차"),
    "duty-then-real-stages": (
        "담당업무\n\n- 회계 결산\n\n- AI 면접 솔루션 운영\n\n전형절차\n서류전형 → 면접전형", "전형절차"),
    "team-interview-duty-then-stages": (
        "담당업무\n팀원 인터뷰를 통한 요구사항 조사\n전형절차\n서류 전형 → 직무 인터뷰", "전형절차"),
    "customer-interview-task-in-procedure": (
        "전형절차\n- 고객 인터뷰 과제로 서비스 개선안을 발표합니다", "전형절차"),
}

# Still not a selection stage, as before.
NOT_STAGE_KEPT = {
    "label-only": "전형절차\n인터뷰",
    "team-interview-link": "팀원 인터뷰 보기",
    "inline-duty-label": "담당업무 건물 유지보수 및 안전점검.",
}

# Decorated headings now start their section. (text, section names, slot, found)
DECORATED_HEADINGS = {
    "lenticular": ("【전형절차】\n서류전형 → 면접전형", ["전형절차"], "selection_stages", True),
    "diamond": ("◆ 근무조건\n- 연봉 4,000만원", ["근무조건"], "compensation", True),
    "triangle": ("▶ 문의\n- 인사팀 02-1234-5678", ["문의처"], "contact_point", True),
    "angle": ("<근무조건>\n- 연봉 4,000만원", ["근무조건"], "compensation", True),
    "circle-label-with-amount": ("○ 급여\n- 월 250만원", ["근무조건"], "compensation", True),
    # Same as the undecorated "급여 / 추후 협의": a deferred label body is not pay.
    "triangle-label-deferred": ("▶ 급여\n추후 협의", ["근무조건"], "compensation", False),
    "reference-mark": ("※ 유의사항\n- 서류 반환 청구는 14일 이내", ["유의사항"], "document_return", True),
    "double-circle": ("◎ 전형일정\n- 면접 일시: 2026년 8월 20일", ["일정"], "schedule", True),
}

# Titles of the example sentences in data/posting_templates.yaml.
TEMPLATE_TITLES = {
    "평가 기준": "전형절차",
    "결과 안내": "일정",
    "이의제기 안내": "유의사항",
    "채용서류 반환ㆍ파기 안내": "유의사항",
    "채용서류 반환·파기 안내": "유의사항",
    "인공지능 활용 안내": "전형절차",
    "자격요건의 직무 관련성": "자격요건",
}
TEMPLATE_SLOTS_LOST_AFTER_DUTIES = [
    "evaluation_criteria", "result_notice", "appeal_channel", "document_return", "ai_disclosure",
]
DUTY_ENDINGS = {
    "bulleted-duties": "행정직 채용\n주요 업무\n- 인사 행정 지원",
    "plain-duties": "행정직 채용\n주요 업무\n인사 행정 지원",
}
INSERTIONS = {"end": "\n\n", "cursor": "\n"}


def _slot(result: dict, slot_id: str) -> dict:
    return next(s for s in result["slots"] if s["slot"] == slot_id)


def _found(result: dict) -> set[str]:
    return {s["slot"] for s in result["slots"] if s["found"]}


def _question_ids(result: dict) -> set[str]:
    return {q["id"] for q in result["questions"]}


def _check(text: str) -> dict:
    return FairpostEngine().check(text).to_dict()


def _template_text(slot_id: str) -> str:
    return yaml.safe_load(TEMPLATES_PATH.read_text(encoding="utf-8"))["slots"][slot_id]["text"]


def _web_check_batch(texts: list[str], tmp_path: Path) -> list[dict]:
    batch = tmp_path / "web-batch.json"
    batch.write_text(
        json.dumps([base64.b64encode(t.encode("utf-8")).decode("ascii") for t in texts]),
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["node", "tests/js_runner.cjs", "--batch", str(batch)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    results = json.loads(completed.stdout)
    assert len(results) == len(texts)
    return results


def _assert_same_result(label: str, python: dict, web: dict) -> None:
    # Named assertions first so a failure says which part diverged.
    assert [(f["id"], f["section"], f["offset"]) for f in web["findings"]] == [
        (f["id"], f["section"], f["offset"]) for f in python["findings"]
    ], label
    assert [
        (s["slot"], s["found"], s["section"], s["evidence"], s["components_found"])
        for s in web["slots"]
    ] == [
        (s["slot"], s["found"], s["section"], s["evidence"], s["components_found"])
        for s in python["slots"]
    ], label
    assert [q["id"] for q in web["questions"]] == [q["id"] for q in python["questions"]], label
    assert web == python, label


# --- reported cases ------------------------------------------------------------


def test_duty_heading_no_longer_swallows_decorated_sections() -> None:
    result = _check(DUTY_THEN_DECORATED_SECTIONS)
    sections = {s["slot"]: s["section"] for s in result["slots"] if s["found"]}
    assert sections == {
        "selection_stages": "전형절차",
        "compensation": "근무조건",
        "schedule": "일정",
        "contact_point": "문의처",
    }
    assert {"document_stage", "interview_stage"} <= set(_slot(result, "selection_stages")["components_found"])
    assert "amount_or_range" in _slot(result, "compensation")["components_found"]
    assert _slot(result, "schedule")["components_found"] == ["application_date"]
    assert {"department", "phone"} <= set(_slot(result, "contact_point")["components_found"])
    asked = _question_ids(result)
    assert not {STAGES_QUESTION, PAY_QUESTION, SCHEDULE_QUESTION, CONTACT_QUESTION} & asked
    names = [s.name for s in split_sections(DUTY_THEN_DECORATED_SECTIONS)]
    assert names == ["개요", "전형절차", "근무조건", "일정", "문의처"]


def test_duty_list_ends_at_a_plain_title_after_its_items() -> None:
    result = _check(DUTY_THEN_PLAIN_TITLE)
    slot = _slot(result, "ai_disclosure")
    assert slot["found"] is True
    assert "AI 면접" in slot["evidence"]
    asked = _question_ids(result)
    assert AI_AND_INTERVIEW_QUESTIONS <= asked
    assert AI_NOTICE_QUESTION not in asked


@pytest.mark.parametrize("base_name", sorted(DUTY_ENDINGS))
@pytest.mark.parametrize("insertion", sorted(INSERTIONS))
@pytest.mark.parametrize("slot_id", TEMPLATE_SLOTS_LOST_AFTER_DUTIES)
def test_example_sentence_after_duties_is_found(slot_id: str, insertion: str, base_name: str) -> None:
    base = DUTY_ENDINGS[base_name]
    assert _found(_check(base)) == set()
    result = _check(base + INSERTIONS[insertion] + _template_text(slot_id))
    assert _found(result) == {slot_id}
    assert result["findings"] == []


@pytest.mark.parametrize("text", DUTY_NOT_STAGE.values(), ids=DUTY_NOT_STAGE.keys())
def test_duty_text_is_not_a_selection_stage(text: str) -> None:
    result = _check(text)
    slot = _slot(result, "selection_stages")
    assert slot["found"] is False
    assert slot["evidence"] is None
    assert slot["components_found"] == []
    assert STAGES_QUESTION in _question_ids(result)


def test_customer_interview_duty_raises_no_interview_question() -> None:
    assert not INTERVIEW_QUESTIONS & _question_ids(_check(DUTY_NOT_STAGE["customer-interview"]))


@pytest.mark.parametrize(("text", "slots"), STAGE_LISTS.values(), ids=STAGE_LISTS.keys())
def test_real_stage_lists_are_found(text: str, slots: set[str]) -> None:
    result = _check(text)
    assert slots <= _found(result)
    for slot_id in slots:
        assert _slot(result, slot_id)["evidence"]


def test_list_items_carry_their_stage_evidence() -> None:
    result = _check(STAGE_LISTS["numbered-review-and-ai-video"][0])
    assert _slot(result, "ai_disclosure")["evidence"] == "2. AI 영상면접"
    # "3. 최종 면접" is an interview stage in a 전형절차 section.
    assert _slot(result, "selection_stages")["components_found"] == ["document_stage", "interview_stage"]
    review = _check(STAGE_LISTS["bulleted-review-and-interview"][0])
    assert _slot(review, "selection_stages")["components_found"] == ["document_stage", "interview_stage"]
    assert _slot(review, "selection_stages")["section"] == "전형절차"


# --- look-alikes ---------------------------------------------------------------


@pytest.mark.parametrize("text", DUTY_BLOCK_KEPT.values(), ids=DUTY_BLOCK_KEPT.keys())
def test_duty_list_items_stay_duties(text: str) -> None:
    result = _check(text)
    assert _found(result) == set()
    assert not INTERVIEW_QUESTIONS & _question_ids(result)
    assert AI_NOTICE_QUESTION in _question_ids(result)


@pytest.mark.parametrize(("text", "section"), STAGE_CONTEXT_KEPT.values(), ids=STAGE_CONTEXT_KEPT.keys())
def test_stage_words_in_a_stage_context_are_still_stages(text: str, section: str) -> None:
    result = _check(text)
    slot = _slot(result, "selection_stages")
    assert slot["found"] is True
    assert slot["section"] == section
    assert STAGES_QUESTION not in _question_ids(result)


def test_duty_list_before_real_stages_keeps_its_ai_word() -> None:
    result = _check(STAGE_CONTEXT_KEPT["duty-then-real-stages"][0])
    assert _slot(result, "ai_disclosure")["found"] is False


@pytest.mark.parametrize("text", NOT_STAGE_KEPT.values(), ids=NOT_STAGE_KEPT.keys())
def test_labels_and_links_are_still_not_stages(text: str) -> None:
    assert _slot(_check(text), "selection_stages")["found"] is False


@pytest.mark.parametrize(
    ("text", "names", "slot_id", "found"), DECORATED_HEADINGS.values(), ids=DECORATED_HEADINGS.keys()
)
def test_decorated_heading_starts_its_section(text: str, names: list[str], slot_id: str, found: bool) -> None:
    assert [s.name for s in split_sections(text)] == names
    slot = _slot(_check(text), slot_id)
    assert slot["found"] is found
    if found:
        assert slot["section"] == names[0]


@pytest.mark.parametrize(("title", "section"), TEMPLATE_TITLES.items(), ids=TEMPLATE_TITLES.keys())
def test_example_titles_are_section_headings(title: str, section: str) -> None:
    for heading in (title, f"■ {title}", f"[{title}]"):
        assert [s.name for s in split_sections(f"담당업무\n인사 행정 지원\n{heading}\n본문")] == ["개요", section]
    # Only a whole line is a heading; the same words inside a sentence are not.
    assert [s.name for s in split_sections(f"{title}은 별도로 정합니다.")] == ["전체"]


@pytest.mark.parametrize(("text", "section"), INTERVIEW_NAME_STAGES.values(), ids=INTERVIEW_NAME_STAGES.keys())
def test_interview_names_in_a_stage_context_are_stages(text: str, section: str) -> None:
    result = _check(text)
    slot = _slot(result, "selection_stages")
    assert slot["found"] is True
    assert slot["section"] == section
    assert "interview_stage" in slot["components_found"]
    assert STAGES_QUESTION not in _question_ids(result)


@pytest.mark.parametrize("text", INTERVIEW_NAME_NOT_STAGE.values(), ids=INTERVIEW_NAME_NOT_STAGE.keys())
def test_interview_names_outside_a_stage_context_are_not_stages(text: str) -> None:
    result = _check(text)
    slot = _slot(result, "selection_stages")
    assert slot["found"] is False
    assert slot["evidence"] is None
    assert STAGES_QUESTION in _question_ids(result)


def test_interview_name_pattern_is_shared_with_the_slot_dictionary() -> None:
    slots = yaml.safe_load((ROOT / "data" / "slots.yaml").read_text(encoding="utf-8"))
    stages = slots["selection_stages"]
    interview = next(c for c in stages["components"] if c["id"] == "interview_stage")
    assert set(extractor._BARE_INTERVIEW_STAGE) <= set(stages["accept_patterns"])
    assert set(extractor._BARE_INTERVIEW_STAGE) <= set(interview["patterns"])
    contact = slots["contact_point"]
    phone = next(c for c in contact["components"] if c["id"] == "phone")
    assert set(extractor._CONTACT_CHANNEL[:2]) <= set(contact["accept_patterns"])
    assert set(extractor._CONTACT_CHANNEL[:2]) == set(phone["patterns"])


@pytest.mark.parametrize(("text", "section", "component"), CONTACT_FOUND.values(), ids=CONTACT_FOUND.keys())
def test_contact_heading_body_or_representative_number_is_a_contact_point(
    text: str, section: str, component: str
) -> None:
    result = _check(text)
    slot = _slot(result, "contact_point")
    assert slot["found"] is True
    assert slot["section"] == section
    assert component in slot["components_found"]
    assert CONTACT_QUESTION not in _question_ids(result)


@pytest.mark.parametrize("text", CONTACT_NOT_FOUND.values(), ids=CONTACT_NOT_FOUND.keys())
def test_tokens_deferrals_and_unrelated_numbers_are_not_contact_points(text: str) -> None:
    result = _check(text)
    slot = _slot(result, "contact_point")
    assert slot["found"] is False
    assert slot["evidence"] is None
    assert CONTACT_QUESTION in _question_ids(result)


def test_official_spaced_dates_are_dates_but_list_numbers_are_not() -> None:
    dated = _slot(_check("접수기간\n2026. 8. 1. ~ 8. 12."), "schedule")
    assert dated["found"] is True
    assert dated["components_found"] == ["application_date"]
    assert _slot(_check("접수기간\n1. 2명"), "schedule")["found"] is False


# --- Python / web parity ---------------------------------------------------------


def _js_constant(name: str) -> str:
    source = (ROOT / "web" / "engine.js").read_text(encoding="utf-8")
    declaration = re.search(rf"const {name} = (?:pythonRegex\(\s*)?(.*?)(?:,\s*\"[a-z]*\"\s*\))?;\r?\n", source, re.S)
    assert declaration is not None, name
    return declaration.group(1)


def test_web_engine_uses_the_same_patterns() -> None:
    assert json.loads(_js_constant("HEADING_DECORATION")) == extractor._HEADING_DECORATION.pattern
    assert json.loads(_js_constant("LINE_MARKER")) == extractor._LINE_MARKER.pattern
    assert json.loads(_js_constant("SENTENCE_END")) == extractor._SENTENCE_END
    for name in ("HIRING_CONTEXT", "DATE_CONTENT", "STAGE_EVENTS", "BARE_STAGE_TERMS",
                 "BARE_INTERVIEW_STAGE", "STAGE_MARKERS", "DUTY_HEADINGS", "CONTACT_CHANNEL"):
        assert json.loads(_js_constant(name)) == getattr(extractor, f"_{name}"), name
    assert sorted(json.loads(_js_constant("STAGE_SECTIONS"))) == sorted(extractor._STAGE_SECTIONS)


def _seeded_cases() -> list[str]:
    rng = random.Random(20261010)
    headings = ["담당업무", "주요 업무", "[담당업무]", "1. 담당업무", "□ 담당업무", "● 담당 업무", " 담당업무:"]
    items = ["- 사무 보조", "· 회계 결산", "1) 고객 인터뷰 기획", "○ 계약 서류 검토", "  세부 내용 정리",
             "민원 서류접수", "고객 인터뷰를 진행합니다.", "※ 상세 업무는 입사 후 결정", "－ 인사 행정 지원"]
    titles = ["채용 안내", "● 전형 안내", "2. 채용 안내", "□ 채용 안내", "[채용 안내]", "【전형절차】",
              "<근무조건>", "▶ 문의", "평가 기준", "결과 안내", "◆ 접수기간", "※ 유의사항"]
    bodies = ["- 1차 AI 면접 실시", "- 서류 검토 → 인터뷰 → 최종 합격", "- 인터뷰", "- 연봉 4,000만원",
              "- 2026. 8. 1. ~ 8. 12.", "- 인사팀 02-1234-5678", "합격자에게 개별 통보합니다.",
              "서류전형 결과는 이메일로 안내합니다.", "- 이의신청은 7일 이내", "2. AI 영상면접"]
    separators = ["\n", "\n", "\n", "\n\n", "\r\n", "\r"]
    texts = []
    for _ in range(240):
        parts = [rng.choice(headings)]
        for _ in range(rng.randint(0, 3)):
            parts.append(rng.choice(items))
        for _ in range(rng.randint(0, 2)):
            parts.append(rng.choice(titles))
            for _ in range(rng.randint(0, 2)):
                parts.append(rng.choice(bodies))
        text = parts[0]
        for part in parts[1:]:
            text += rng.choice(separators) + part
        texts.append(text)
    return texts


def _parity_cases() -> dict[str, str]:
    cases = {
        "reported:decorated-sections": DUTY_THEN_DECORATED_SECTIONS,
        "reported:plain-title": DUTY_THEN_PLAIN_TITLE,
    }
    cases.update({f"duty-not-stage:{k}": v for k, v in DUTY_NOT_STAGE.items()})
    cases.update({f"stage-list:{k}": v[0] for k, v in STAGE_LISTS.items()})
    cases.update({f"duty-kept:{k}": v for k, v in DUTY_BLOCK_KEPT.items()})
    cases.update({f"stage-kept:{k}": v[0] for k, v in STAGE_CONTEXT_KEPT.items()})
    cases.update({f"not-stage-kept:{k}": v for k, v in NOT_STAGE_KEPT.items()})
    cases.update({f"decorated:{k}": v[0] for k, v in DECORATED_HEADINGS.items()})
    cases.update({f"interview-stage:{k}": v[0] for k, v in INTERVIEW_NAME_STAGES.items()})
    cases.update({f"interview-not-stage:{k}": v for k, v in INTERVIEW_NAME_NOT_STAGE.items()})
    cases.update({f"contact-found:{k}": v[0] for k, v in CONTACT_FOUND.items()})
    cases.update({f"contact-not-found:{k}": v for k, v in CONTACT_NOT_FOUND.items()})
    for key, (text, _section, _component) in CONTACT_FOUND.items():
        # Inside a posting, after a decorated duty list and with CRLF lines.
        cases[f"contact-in-posting:{key}"] = f"● 담당업무\n- 회계 결산\n● 근무조건\n- 연봉 4,000만원\n{text}"
        cases[f"contact-crlf:{key}"] = text.replace("\n", "\r\n")
    for title in TEMPLATE_TITLES:
        cases[f"title:{title}"] = f"담당업무\n인사 행정 지원\n{title}\n본문"
    for slot_id in TEMPLATE_SLOTS_LOST_AFTER_DUTIES:
        for base_name, base in DUTY_ENDINGS.items():
            for insertion, separator in INSERTIONS.items():
                cases[f"template:{slot_id}:{base_name}:{insertion}"] = base + separator + _template_text(slot_id)
    cases["date:spaced"] = "접수기간\n2026. 8. 1. ~ 8. 12."
    cases["date:list-number"] = "접수기간\n1. 2명"
    for index, text in enumerate(_seeded_cases()):
        cases[f"seeded:{index}"] = text
    return cases


@pytest.mark.requires_node
def test_python_and_web_agree_on_every_case(tmp_path: Path) -> None:
    cases = _parity_cases()
    labels = list(cases)
    texts = [cases[label] for label in labels]
    web_results = _web_check_batch(texts, tmp_path)
    engine = FairpostEngine()
    for label, text, web in zip(labels, texts, web_results, strict=True):
        _assert_same_result(f"{label}: {text!r}", engine.check(text).to_dict(), web)


@pytest.mark.requires_node
def test_reported_cases_reach_the_same_outcome_in_the_web_engine(tmp_path: Path) -> None:
    decorated, plain_title, customer = _web_check_batch(
        [DUTY_THEN_DECORATED_SECTIONS, DUTY_THEN_PLAIN_TITLE, DUTY_NOT_STAGE["customer-interview"]], tmp_path
    )
    assert {"selection_stages", "compensation", "schedule", "contact_point"} <= _found(decorated)
    assert _slot(plain_title, "ai_disclosure")["found"] is True
    assert AI_AND_INTERVIEW_QUESTIONS <= _question_ids(plain_title)
    assert _slot(customer, "selection_stages")["found"] is False
    assert not INTERVIEW_QUESTIONS & _question_ids(customer)
