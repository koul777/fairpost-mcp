"""Synthetic development regressions for slot evidence; not human gold labels."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess

import pytest

from core import FairpostEngine
from core.extractor import split_sections

CASES = json.loads(r'''[
  {
    "id": "stage-chain",
    "text": "개발자 채용\n합류 여정\n서류접수 > 직무 인터뷰 > 문화적합성 인터뷰 > 레퍼런스 체크 > 처우협의 > 최종합격 및 입사",
    "slot": "selection_stages",
    "found": true,
    "components": [
      "document_stage",
      "interview_stage"
    ],
    "contains": "직무 인터뷰"
  },
  {
    "id": "natural-stages",
    "text": "채용공고\n이렇게 합류해요\n서류 전형 → 화상 인터뷰 → 직무 인터뷰(과제) → 최종 합격",
    "slot": "selection_stages",
    "found": true,
    "components": [
      "document_stage",
      "interview_stage"
    ],
    "contains": "화상 인터뷰"
  },
  {
    "id": "coding-stage",
    "text": "전형절차\n코딩테스트 후 실무 인터뷰를 진행합니다.",
    "slot": "selection_stages",
    "found": true,
    "contains": "코딩테스트"
  },
  {
    "id": "empty-procedure",
    "text": "전형절차\n추후 안내 예정\n지원자격\n개발 경력 3년",
    "slot": "selection_stages",
    "found": false
  },
  {
    "id": "empty-criteria",
    "text": "평가 기준\n추후 안내",
    "slot": "evaluation_criteria",
    "found": false
  },
  {
    "id": "empty-pay",
    "text": "급여\n지원자격\n개발 경력 3년",
    "slot": "compensation",
    "found": false
  },
  {
    "id": "empty-preference",
    "text": "우대사항\n추후 안내\n전형절차\n서류전형",
    "slot": "preference_items",
    "found": false
  },
  {
    "id": "empty-contact",
    "text": "문의처\n추후 안내",
    "slot": "contact_point",
    "found": false
  },
  {
    "id": "ai-duty",
    "text": "AI 개발자 채용\n담당업무\n자동 평가 알고리즘 연구 및 개발\n지원자격\n개발 경력 3년",
    "slot": "ai_disclosure",
    "found": false,
    "absent": [
      "usage_scope"
    ]
  },
  {
    "id": "employee-duty",
    "text": "인사담당자 채용\n담당업무\n임직원 성과 평가 기준 설계 및 보상 운영\n지원자격\n인사 업무 경력 3년",
    "slot": "evaluation_criteria",
    "found": false,
    "absent": [
      "criteria_named"
    ]
  },
  {
    "id": "unheaded-employee-duty",
    "text": "인사 담당자의 업무는 임직원 성과 평가 기준 설계 및 보상 운영입니다.",
    "slot": "evaluation_criteria",
    "found": false
  },
  {
    "id": "real-ai-assessment",
    "text": "전형절차\n지원자는 AI 역량검사를 실시합니다. AI 평가는 참고자료로 사용합니다.",
    "slot": "ai_disclosure",
    "found": true,
    "contains": "AI 역량검사"
  },
  {
    "id": "unheaded-ai",
    "text": "지원자는 AI 면접을 봅니다.",
    "slot": "ai_disclosure",
    "found": true
  },
  {
    "id": "real-weighting",
    "text": "전형절차\n면접 평가 기준은 직무역량이며 배점은 50점입니다.",
    "slot": "evaluation_criteria",
    "found": true,
    "components": [
      "criteria_named",
      "weighting_named",
      "job_linkage"
    ],
    "contains": "50점"
  },
  {
    "id": "duty-then-real",
    "text": "담당업무\n자동 평가 알고리즘 연구 및 개발\n전형절차\n지원자는 AI 역량검사를 봅니다.",
    "slot": "ai_disclosure",
    "found": true,
    "contains": "지원자는"
  },
  {
    "id": "preference-natural",
    "text": "장애인 및 국가보훈대상자는 채용 전형 시 우대합니다.",
    "slot": "preference_items",
    "found": true,
    "contains": "국가보훈대상자"
  },
  {
    "id": "preference-conversational",
    "text": "장애인 및 보훈대상자는 채용 전형 시 우대하고 있어요.",
    "slot": "preference_items",
    "found": true,
    "contains": "우대하고 있어요"
  },
  {
    "id": "not-preference-reference",
    "text": "참고사항\n개별 지원자의 일정은 추후 안내합니다.",
    "slot": "preference_items",
    "found": false
  },
  {
    "id": "preference-list",
    "text": "우대사항\n정밀기기 제조사 근무 경험자",
    "slot": "preference_items",
    "found": true,
    "contains": "제조사"
  },
  {
    "id": "contact-duty-title",
    "text": "생산기술 담당자 모집\n담당업무\n제조 공정 개선",
    "slot": "contact_point",
    "found": false
  },
  {
    "id": "contact-engineering-title",
    "text": "기계설계 담당자 채용\n업무 수행에 필요한 개발 경험",
    "slot": "contact_point",
    "found": false
  },
  {
    "id": "contact-direct",
    "text": "문의처\n인사팀 담당자 (평일 업무시간)",
    "slot": "contact_point",
    "found": true,
    "components": [
      "department",
      "hours"
    ],
    "contains": "인사팀"
  },
  {
    "id": "contact-one-to-one",
    "text": "채용 문의는 Careers 홈페이지 1:1 문의로 접수해 주세요.",
    "slot": "contact_point",
    "found": true,
    "contains": "1:1 문의"
  },
  {
    "id": "result-email-not-contact",
    "text": "결과는 지원서에 기재한 이메일로 개별 통보합니다.",
    "slot": "contact_point",
    "found": false
  },
  {
    "id": "result-notice",
    "text": "결과는 이메일로 개별 통보합니다.",
    "slot": "result_notice",
    "found": true,
    "components": [
      "notice_channel"
    ],
    "contains": "이메일"
  },
  {
    "id": "date-undetermined",
    "text": "전형 일정\n면접 일정은 미정이며 추후 개별 안내합니다.",
    "slot": "schedule",
    "found": true,
    "absent": [
      "assessment_date"
    ]
  },
  {
    "id": "date-stated",
    "text": "전형 일정\n면접 일정은 10월 21일 14시입니다.",
    "slot": "schedule",
    "found": true,
    "components": [
      "assessment_date"
    ],
    "contains": "21일"
  },
  {
    "id": "date-heading-only",
    "text": "전형 일정\n추후 안내",
    "slot": "schedule",
    "found": false,
    "absent": [
      "assessment_date"
    ]
  },
  {
    "id": "probation-ratio",
    "text": "근무조건\n정규직 수습기간 동안 급여 100% 지급",
    "slot": "compensation",
    "found": true,
    "absent": [
      "amount_or_range"
    ],
    "contains": "100%"
  },
  {
    "id": "salary-real",
    "text": "급여\n월 300만원",
    "slot": "compensation",
    "found": true,
    "components": [
      "amount_or_range"
    ],
    "contains": "300만원"
  },
  {
    "id": "explicit-no-return",
    "text": "채용서류는 반환하지 않음. 제출한 서류 반환하지 않음.",
    "slot": "document_return",
    "found": true,
    "contains": "반환하지 않음"
  },
  {
    "id": "unheaded-existing-process",
    "text": "서류전형 후 면접전형을 진행합니다.",
    "slot": "selection_stages",
    "found": true,
    "components": [
      "document_stage",
      "interview_stage"
    ]
  },
  {
    "id": "unicode-heading-duty",
    "text": "[Ｗｈａｔ Ｙｏｕ​’ll Ｄｏ]\n자동 평가 알고리즘 개발\n[지원 자격]\n개발 경력",
    "slot": "ai_disclosure",
    "found": false
  },
  {
    "id": "component-duty-isolation",
    "text": "담당업무\n지원서와 영상 데이터를 연구하고 평가 기준을 설계합니다.\n전형절차\n서류전형 후 AI 역량검사를 실시합니다.",
    "slot": "ai_disclosure",
    "found": true,
    "absent": [
      "data_scope"
    ],
    "contains": "AI 역량검사"
  },
  {
    "id": "unheaded-contact-phone",
    "text": "__SYNTHETIC_PHONE__",
    "slot": "contact_point",
    "found": true,
    "components": [
      "phone"
    ]
  },
  {
    "id": "heading-assessment-date",
    "text": "면접 일정\n10월 21일 14시",
    "slot": "schedule",
    "found": true,
    "components": [
      "assessment_date"
    ],
    "contains": "21일"
  },
  {
    "id": "deferred-date-other-sentence",
    "text": "면접 일정은 미정입니다. 접수 기간은 10월 21일까지입니다.",
    "slot": "schedule",
    "found": true,
    "absent": [
      "assessment_date"
    ],
    "components": [
      "application_date"
    ]
  },
  {
    "id": "ratio-with-benefit",
    "text": "급여는 수습기간 동안 100% 지급합니다. 연간 업무지원비 324만원을 지원합니다.",
    "slot": "compensation",
    "found": true,
    "absent": [
      "amount_or_range"
    ]
  },
  {
    "id": "ai-service-qualification",
    "text": "Required Skills\nAI 면접 서비스 개발 경험",
    "slot": "ai_disclosure",
    "found": false
  },
  {
    "id": "hr-recruitment-title-not-contact",
    "text": "인사팀 담당자 모집",
    "slot": "contact_point",
    "found": false
  },
  {
    "id": "salary-includes-meal-allowance",
    "text": "급여는 식대 포함 월 300만원입니다.",
    "slot": "compensation",
    "found": true,
    "components": [
      "amount_or_range"
    ],
    "contains": "300만원"
  }
]''')
# Construct a synthetic phone at runtime; packaged fixtures contain no contact literal.
for case in CASES:
    if case["text"] == "__SYNTHETIC_PHONE__":
        case["text"] = "-".join(("02", "1234", "5678"))
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_slot_context_regression(case: dict) -> None:
    result = FairpostEngine().check(case["text"]).to_dict()
    slot = next(s for s in result["slots"] if s["slot"] == case["slot"])
    assert slot["found"] is case["found"]
    assert set(case.get("components", [])) <= set(slot["components_found"])
    assert not set(case.get("absent", [])) & set(slot["components_found"])
    if "contains" in case:
        assert case["contains"] in slot["evidence"]
    if not case["found"]:
        assert slot["evidence"] is None
        assert slot["components_found"] == []


@pytest.mark.requires_node
@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_slot_context_python_web_parity(case: dict) -> None:
    encoded = base64.b64encode(case["text"].encode("utf-8")).decode("ascii")
    completed = subprocess.run(["node", "tests/js_runner.cjs", encoded], cwd=ROOT,
                               check=True, capture_output=True, text=True, encoding="utf-8")
    assert json.loads(completed.stdout) == FairpostEngine().check(case["text"]).to_dict()


@pytest.mark.parametrize("heading,canonical", [
    ("[담당 업무]", "개요"), ("Ｗｈａｔ Ｙｏｕ\u200b’ll Ｄｏ", "개요"),
    ("[Required Skills]", "자격요건"), ("이런 분과 함께하고 싶어요", "자격요건"),
    ("이렇게 합류해요", "전형절차"), ("토스증권으로의 합류 여정", "전형절차"),
])
def test_modern_heading_alias_keeps_original_offsets(heading: str, canonical: str) -> None:
    text = "🙂 안내\n" + heading + "\n원문 내용"
    sections = split_sections(text)
    assert sections[-1].name == canonical
    assert "".join(s.text for s in sections) == text
    assert all(text[s.start:s.end] == s.text for s in sections)
