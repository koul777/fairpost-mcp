"""Synthetic development regressions for result-notice and deferred-body detection.

These are hand-written cases, not human-labelled gold data. They pin two
defects found in review of the slot extractor:

* a bare "결과 ... 안내" sentence that has nothing to do with hiring (a
  customer-service duty, a background-check consequence) was counted as the
  posting's result notice and silenced the Q-INFO-002 question;
* the "announced later" rule for a label body treated *any* line that starts
  with 상세, 별도 or 예정 as deferred, so a heading followed by real content
  ("접수기간 / 상세 일정: 2026년 8월 1일 ~ 8월 12일") reported the slot missing.

Every case also runs through the browser engine and must match Python exactly.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from core import FairpostEngine
from core.extractor import _DEFERRED_BODY
from core.morph import find_first


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_PATH = ROOT / "data" / "posting_templates.yaml"

RESULT_QUESTION = "Q-INFO-002"
COMPENSATION_QUESTION = "Q-INFO-006"
SCHEDULE_QUESTION = "Q-PROC-003"
CONTACT_QUESTION = "Q-INFO-003"

# A sentence that happens to contain "결과 ... 안내/통보/발표" but is not a
# notice of the applicant's hiring result.
NOT_A_RESULT_NOTICE = {
    "customer-consultation-result": "고객 상담 결과를 정리하여 담당 부서에 안내합니다.",
    "background-check-consequence": (
        "신원조회 결과에 따라 합격이 취소될 수 있으며 세부 사항은 별도 안내합니다."
    ),
    "background-check-consequence-formal": (
        "신원조회 결과에 의해 채용이 취소될 수 있으며 세부 사항은 별도 안내합니다."
    ),
    "deliverable-not-result": "최종 결과물을 고객에게 안내합니다.",
    "performance-review-result": "성과평가 결과를 직원에게 안내합니다.",
    "research-result-presentation": "연구 결과를 발표합니다.",
    "duty-section": "담당업무\n서비스 개발 결과를 고객에게 안내합니다.",
    "under-notice-section": "유의사항\n고객 상담 결과를 정리하여 담당 부서에 안내합니다.",
}

# Genuine result notices that must keep being found.
RESULT_NOTICES = {
    "stage-result-by-text-message": "서류전형 결과는 8월 20일 개별 문자로 안내합니다",
    "stage-result-by-text-message-period": "서류전형 결과는 8월 20일 개별 문자로 안내합니다.",
    "final-pass-announcement": "최종 합격자 발표: 홈페이지 공지",
    "labelled-notice-with-bullet": "결과 안내\n- 전형별 결과는 지원자 전원에게 이메일로 통보",
    "schedule-and-result-by-email": (
        "전형 일정 및 결과는 지원서에 등록한 이메일로 개별 안내합니다."
    ),
    "interview-result-by-email": "면접 결과는 이메일로 안내합니다.",
    "written-exam-result-on-homepage": "필기시험 결과는 홈페이지에서 발표합니다.",
    "coding-test-result": "코딩테스트 결과는 이메일로 안내합니다.",
    "result-date-sentence": "각 전형의 결과 발표일은 8월 20일입니다.",
    "result-to-all-applicants": "결과는 지원자 전원에게 안내합니다.",
    "inside-selection-section": "전형절차\n서류 전형 결과를 개별 안내합니다.",
}

# Body lines that start with 상세 / 별도 / 예정 but carry real content: the
# heading must keep its body. (heading, body, slot, component)
REAL_CONTENT = {
    "detailed-schedule-with-dates": (
        "접수기간", "상세 일정: 2026년 8월 1일 ~ 8월 12일", "schedule", "application_date"),
    "pay-fixed-without-negotiation": (
        "연봉", "별도 협의 없이 4,000만원 고정", "compensation", "amount_or_range"),
    "expected-date": ("접수기간", "예정일: 2026.08.20", "schedule", "application_date"),
    "detailed-pay-amount": ("연봉", "상세 내용: 연 4,000만원", "compensation", "amount_or_range"),
    "dates-then-change-notice": (
        "접수기간", "상세 일정: 8월 1일~8월 12일, 변경 시 별도 공지", "schedule", "application_date"),
    "no-separate-notice-deadline": (
        "접수기간", "별도 안내 없이 접수 마감 8월 12일", "schedule", "application_date"),
    "negotiable-range": (
        "연봉", "별도 협의 가능 (3,000만원 ~ 4,000만원)", "compensation", "amount_or_range"),
    "rolling-schedule": ("접수기간", "상세 일정 상시", "schedule", None),
}

# Genuinely deferred bodies: behaviour at the base commit must be unchanged
# (the slot stays missing and its question is still asked).
DEFERRED = {
    "negotiation-later": ("연봉", "별도 협의", "compensation", COMPENSATION_QUESTION),
    "negotiation-then-decision": ("연봉", "별도 협의 후 결정", "compensation", COMPENSATION_QUESTION),
    "announce-later-expected": ("접수기간", "추후 안내 예정", "schedule", SCHEDULE_QUESTION),
    "announce-later-polite": ("접수기간", "추후 안내드리겠습니다.", "schedule", SCHEDULE_QUESTION),
    "detail-announced-later": ("접수기간", "상세 내용은 추후 안내", "schedule", SCHEDULE_QUESTION),
    "separate-notice-expected": ("접수기간", "별도 공지 예정", "schedule", SCHEDULE_QUESTION),
    "undetermined": ("접수기간", "미정", "schedule", SCHEDULE_QUESTION),
    "bare-expected": ("접수기간", "예정", "schedule", SCHEDULE_QUESTION),
    "later-with-month-hint": ("접수기간", "추후 안내 예정 (8월 중)", "schedule", SCHEDULE_QUESTION),
    "later-with-amount-hint": ("연봉", "추후 협의 (3,000만원 내외)", "compensation", COMPENSATION_QUESTION),
    "separate-inquiry": ("문의처", "별도 문의", "contact_point", CONTACT_QUESTION),
    "see-attachment": ("접수기간", "상세 내용은 첨부파일 참조", "schedule", SCHEDULE_QUESTION),
    "deferred-with-contact-number": (
        "접수기간", "상세 내용은 인사팀 02-1234-5678 로 문의", "schedule", SCHEDULE_QUESTION),
}

# A single "상세 일정은 별도 공지" line names a notice rule rather than an empty
# label body; the schedule slot is found at the base commit and must stay so.
UNCHANGED_FOUND = {
    "standalone-detail-notice": ("상세 일정은 별도 공지", "schedule", ["change_notice"]),
    "labelled-detail-notice": ("접수기간\n상세 일정은 별도 공지", "schedule", ["change_notice"]),
}

# Odd encodings of the same content; only Python/web parity is asserted, with
# the slot outcome pinned for the ones whose meaning is unambiguous.
ENCODING_VARIANTS = {
    "fullwidth-digits-and-comma": ("연봉\n별도 협의 없이 ４，０００만원 고정", "compensation", True),
    "zero-width-inside-body": ("접수기간\n상세 일정:​ 2026년 8월 1일 ~ 8월 12일", "schedule", True),
    "crlf-lines": ("접수기간\r\n상세 일정: 2026년 8월 1일 ~ 8월 12일\r\n", "schedule", True),
    "fullwidth-dates": ("접수기간\n상세 일정: ２０２６년 ８월 １일 ~ ８월 １２일", "schedule", True),
    "no-break-space-deferred": ("연봉\n별도 협의", "compensation", False),
    "lone-cr-in-strong-deferral": ("일정\n접수기간\n추후\r8월 1일 안내\n", "schedule", False),
    "lone-cr-after-ambiguous-marker": ("접수기간\n별도\r8월 1일", "schedule", True),
    "notice-with-zero-width": ("서류전형​ 결과는 8월 20일 개별 문자로 안내합니다", "result_notice", True),
    "notice-fullwidth-space": ("면접　결과는　이메일로 안내합니다.", "result_notice", True),
    "background-check-with-zero-width": (
        "신원조회 결과​에 따라 합격이 취소될 수 있으며 세부 사항은 별도 안내합니다.",
        "result_notice",
        False,
    ),
}


def _slot(result: dict, slot_id: str) -> dict:
    return next(s for s in result["slots"] if s["slot"] == slot_id)


def _question_ids(result: dict) -> set[str]:
    return {q["id"] for q in result["questions"]}


def _check(text: str) -> dict:
    return FairpostEngine().check(text).to_dict()


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


# --- result notice -------------------------------------------------------------


@pytest.mark.parametrize("text", NOT_A_RESULT_NOTICE.values(), ids=NOT_A_RESULT_NOTICE.keys())
def test_non_hiring_result_sentence_is_not_a_result_notice(text: str) -> None:
    result = _check(text)
    slot = _slot(result, "result_notice")
    assert slot["found"] is False
    assert slot["evidence"] is None
    assert slot["components_found"] == []
    assert RESULT_QUESTION in _question_ids(result)


@pytest.mark.parametrize("text", RESULT_NOTICES.values(), ids=RESULT_NOTICES.keys())
def test_genuine_result_notice_is_still_found(text: str) -> None:
    result = _check(text)
    slot = _slot(result, "result_notice")
    assert slot["found"] is True
    assert slot["evidence"]
    assert RESULT_QUESTION not in _question_ids(result)


def test_bullet_notice_reports_channel_and_audience_components() -> None:
    result = _check("결과 안내\n- 전형별 결과는 지원자 전원에게 이메일로 통보")
    slot = _slot(result, "result_notice")
    assert {"notice_channel", "notice_audience"} <= set(slot["components_found"])
    assert "지원자 전원에게 이메일로 통보" in slot["evidence"]


def test_text_message_notice_keeps_its_sentence_as_evidence() -> None:
    result = _check("서류전형 결과는 8월 20일 개별 문자로 안내합니다")
    slot = _slot(result, "result_notice")
    assert slot["evidence"] == "서류전형 결과는 8월 20일 개별 문자로 안내합니다"
    assert "notice_channel" in slot["components_found"]


def _template_sentences() -> dict[str, tuple[str, str | None]]:
    document = yaml.safe_load(TEMPLATES_PATH.read_text(encoding="utf-8"))
    placeholder = document["placeholder"]
    entry = document["slots"]["result_notice"]
    sentences = {"text": (entry["text"], None)}
    for component_id, sentence in entry["components"].items():
        sentences[f"component:{component_id}"] = (sentence, component_id)
    variants: dict[str, tuple[str, str | None]] = {}
    for name, (sentence, component_id) in sentences.items():
        # The blank is kept as written, then filled with neutral values.
        fills = [("blank", placeholder)]
        if placeholder in sentence:
            fills += [("neutral-number", "10"), ("neutral-word", "홈페이지")]
        for fill_name, fill in fills:
            variants[f"{name}:{fill_name}"] = (sentence.replace(placeholder, fill), component_id)
    return variants


TEMPLATE_SENTENCES = _template_sentences()
TEMPLATE_BASE = "2026년 사무직 채용\n담당 업무: 문서 관리"


@pytest.mark.parametrize(
    ("sentence", "component_id"), TEMPLATE_SENTENCES.values(), ids=TEMPLATE_SENTENCES.keys()
)
def test_template_result_notice_sentences_are_still_detected(
    sentence: str, component_id: str | None
) -> None:
    for text in (sentence, f"{TEMPLATE_BASE}\n\n{sentence}"):
        result = _check(text)
        slot = _slot(result, "result_notice")
        assert slot["found"] is True, text
        assert RESULT_QUESTION not in _question_ids(result)
        if component_id is not None:
            assert component_id in slot["components_found"], text
        other = {s["slot"] for s in result["slots"] if s["found"]} - {"result_notice"}
        assert other == set(), f"template leaked into {sorted(other)}"


# --- deferred body -------------------------------------------------------------


@pytest.mark.parametrize(
    ("heading", "body", "slot_id", "component_id"), REAL_CONTENT.values(), ids=REAL_CONTENT.keys()
)
def test_real_content_after_a_label_is_not_treated_as_deferred(
    heading: str, body: str, slot_id: str, component_id: str | None
) -> None:
    slot = _slot(_check(f"{heading}\n{body}"), slot_id)
    assert slot["found"] is True
    assert body in slot["evidence"]
    if component_id is not None:
        assert component_id in slot["components_found"]


def test_reported_content_cases_clear_their_absence_questions() -> None:
    schedule = _check("접수기간\n상세 일정: 2026년 8월 1일 ~ 8월 12일")
    assert _slot(schedule, "schedule")["found"] is True
    assert SCHEDULE_QUESTION not in _question_ids(schedule)
    assert _slot(schedule, "schedule")["components_found"] == ["application_date"]

    pay = _check("연봉\n별도 협의 없이 4,000만원 고정")
    assert _slot(pay, "compensation")["found"] is True
    assert COMPENSATION_QUESTION not in _question_ids(pay)
    assert "amount_or_range" in _slot(pay, "compensation")["components_found"]


@pytest.mark.parametrize(
    ("heading", "body", "slot_id", "question_id"), DEFERRED.values(), ids=DEFERRED.keys()
)
def test_genuinely_deferred_body_still_leaves_the_slot_missing(
    heading: str, body: str, slot_id: str, question_id: str
) -> None:
    result = _check(f"{heading}\n{body}")
    slot = _slot(result, slot_id)
    assert slot["found"] is False
    assert slot["evidence"] is None
    assert slot["components_found"] == []
    assert question_id in _question_ids(result)


@pytest.mark.parametrize(
    ("text", "slot_id", "components"), UNCHANGED_FOUND.values(), ids=UNCHANGED_FOUND.keys()
)
def test_single_line_detail_notice_is_still_found(
    text: str, slot_id: str, components: list[str]
) -> None:
    slot = _slot(_check(text), slot_id)
    assert slot["found"] is True
    assert slot["components_found"] == components
    assert slot["evidence"] == "상세 일정은 별도 공지"


DEFERRED_LINES = [
    "추후", "차후", "나중에", "미정", "예정", "별도", "상세",
    "추후 안내", "추후 안내 예정", "추후 안내 예정입니다.", "추후 공지합니다.", "추후 결정",
    "차후 공지", "나중에 안내", "미정입니다", "미정이며 추후 개별 안내합니다.",
    "별도 협의", "별도 협의 후 결정", "별도 안내 예정", "별도 공지", "별도 문의", "별도 통보",
    "상세 내용은 추후 안내", "상세 일정은 별도 공지", "상세 내용 참조", "예정입니다",
    "추후 안내 예정 (8월 중)", "추후\r8월 1일 안내",
]
CONTENT_LINES = [
    "상세 일정: 2026년 8월 1일 ~ 8월 12일",
    "별도 협의 없이 4,000만원 고정",
    "예정일: 2026.08.20",
    "예정 연봉 4,000만원",
    "상세 일정: 8월 1일~8월 12일, 변경 시 별도 공지",
    "별도 안내 없이 접수 마감 8월 12일",
    "별도 공지 없이 2026.8.1 마감",
    "상세 내용: 연 4,000만원",
    "별도 협의 없음 (연봉 3천만원)",
    "상세 일정 상시",
    "별도 상여 연 400%",
]


@pytest.mark.parametrize("line", DEFERRED_LINES)
def test_deferred_pattern_matches_deferral_statements(line: str) -> None:
    assert find_first(line, _DEFERRED_BODY) is not None


@pytest.mark.parametrize("line", CONTENT_LINES)
def test_deferred_pattern_leaves_content_alone(line: str) -> None:
    assert find_first(line, _DEFERRED_BODY) is None


def test_web_engine_uses_the_same_deferred_pattern() -> None:
    source = (ROOT / "web" / "engine.js").read_text(encoding="utf-8")
    declaration = re.search(r"const DEFERRED_BODY = (\[.*?\]);", source)
    assert declaration is not None
    assert json.loads(declaration.group(1)) == _DEFERRED_BODY


# --- Python / web parity ---------------------------------------------------------


def _parity_cases() -> dict[str, str]:
    cases: dict[str, str] = {}
    cases.update({f"not-notice:{k}": v for k, v in NOT_A_RESULT_NOTICE.items()})
    cases.update({f"notice:{k}": v for k, v in RESULT_NOTICES.items()})
    cases.update({f"template:{k}": v[0] for k, v in TEMPLATE_SENTENCES.items()})
    cases.update(
        {f"template-in-posting:{k}": f"{TEMPLATE_BASE}\n\n{v[0]}" for k, v in TEMPLATE_SENTENCES.items()}
    )
    cases.update({f"content:{k}": f"{v[0]}\n{v[1]}" for k, v in REAL_CONTENT.items()})
    cases.update({f"deferred:{k}": f"{v[0]}\n{v[1]}" for k, v in DEFERRED.items()})
    cases.update({f"unchanged:{k}": v[0] for k, v in UNCHANGED_FOUND.items()})
    cases.update({f"encoding:{k}": v[0] for k, v in ENCODING_VARIANTS.items()})
    for index, line in enumerate(DEFERRED_LINES + CONTENT_LINES):
        for heading in ("접수기간", "연봉", "결과 안내", "문의처"):
            cases[f"line:{heading}:{index}"] = f"{heading}\n{line}"
        cases[f"line:in-section:{index}"] = f"일정\n접수기간\n{line}\n근무조건\n근무지 본사"
        cases[f"line:crlf:{index}"] = f"접수기간\r\n{line}\r\n"
    return cases


@pytest.mark.requires_node
def test_python_and_web_agree_on_every_case(tmp_path: Path) -> None:
    cases = _parity_cases()
    labels = list(cases)
    texts = [cases[label] for label in labels]
    web_results = _web_check_batch(texts, tmp_path)
    engine = FairpostEngine()
    for label, text, web in zip(labels, texts, web_results, strict=True):
        _assert_same_result(label, engine.check(text).to_dict(), web)


@pytest.mark.requires_node
@pytest.mark.parametrize(
    ("text", "slot_id", "found"), ENCODING_VARIANTS.values(), ids=ENCODING_VARIANTS.keys()
)
def test_encoding_variants_reach_the_same_outcome_in_both_engines(
    text: str, slot_id: str, found: bool, tmp_path: Path
) -> None:
    python = _check(text)
    web = _web_check_batch([text], tmp_path)[0]
    assert _slot(python, slot_id)["found"] is found
    assert _slot(web, slot_id)["found"] is found
    _assert_same_result(text, python, web)


@pytest.mark.requires_node
def test_reported_cases_have_identical_slots_questions_and_evidence(tmp_path: Path) -> None:
    texts = [
        NOT_A_RESULT_NOTICE["customer-consultation-result"],
        NOT_A_RESULT_NOTICE["background-check-consequence"],
        "접수기간\n상세 일정: 2026년 8월 1일 ~ 8월 12일",
        "연봉\n별도 협의 없이 4,000만원 고정",
    ]
    web_results = _web_check_batch(texts, tmp_path)
    for text, web in zip(texts, web_results, strict=True):
        python = _check(text)
        assert [(s["slot"], s["found"], s["evidence"]) for s in web["slots"]] == [
            (s["slot"], s["found"], s["evidence"]) for s in python["slots"]
        ]
        assert [q["id"] for q in web["questions"]] == [q["id"] for q in python["questions"]]
    web_by_text = dict(zip(texts, web_results, strict=True))
    assert _slot(web_by_text[texts[0]], "result_notice")["found"] is False
    assert _slot(web_by_text[texts[1]], "result_notice")["found"] is False
    assert _slot(web_by_text[texts[2]], "schedule")["found"] is True
    assert _slot(web_by_text[texts[3]], "compensation")["found"] is True
