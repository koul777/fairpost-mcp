"""Synthetic second-pass development evidence, never human gold or validation."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess

import pytest

from core import FairpostEngine

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads(r'''[
  {
    "id": "numeric-chain",
    "text": "합류 여정\n1. 서류 전형 → 2. 직무 인터뷰 → 3. 최종 합격",
    "slot": "selection_stages",
    "found": true,
    "contains": "3. 최종 합격"
  },
  {
    "id": "dotted-dates",
    "text": "접수 기간\n2027.01.02 ~\n2027.01.20",
    "slot": "schedule",
    "found": true,
    "contains": "2027.01.20",
    "components": [
      "application_date"
    ]
  },
  {
    "id": "known-deadline-over-change-notice",
    "text": "전형절차\n전형 일정은 상황에 따라 변경될 수 있습니다.\n모집기간\n2027.01.02 ~ 2027.01.20",
    "slot": "schedule",
    "found": true,
    "contains": "2027.01.20"
  },
  {
    "id": "until-filled-over-clock",
    "text": "접수 마감일\n채용시까지\n(마감시간 : 24시)",
    "slot": "schedule",
    "found": true,
    "contains": "채용시까지"
  },
  {
    "id": "website-contact",
    "text": "문의사항은 채용 홈페이지 > 지원문의로 접수해 주세요.",
    "slot": "contact_point",
    "found": true,
    "contains": "지원문의",
    "absentQuestions": [
      "Q-INFO-003"
    ]
  },
  {
    "id": "contact-url-not-truncated",
    "text": "문의사항은 careers.example.org 채용Q&A 게시판으로 접수해 주세요.",
    "slot": "contact_point",
    "found": true,
    "contains": "example.org"
  },
  {
    "id": "applicant-emergency-not-channel",
    "text": "입사지원서\n(비상연락처)\n이메일\n본인휴대폰",
    "slot": "contact_point",
    "found": false
  },
  {
    "id": "privacy-field-not-channel",
    "text": "입사지원서에 기재한 전화번호와 이메일은 평가위원에게 제공하지 않습니다.",
    "slot": "contact_point",
    "found": false
  },
  {
    "id": "privacy-then-real-channel",
    "text": "입사지원서에 기재한 전화번호는 평가위원에게 제공하지 않습니다.\n채용 문의는 홈페이지 1:1 문의로 접수합니다.",
    "slot": "contact_point",
    "found": true,
    "contains": "채용 문의"
  },
  {
    "id": "result-schedule-natural",
    "text": "전형 일정 및 결과는 지원서에 등록한 이메일로 개별 안내합니다.",
    "slot": "result_notice",
    "found": true,
    "absentQuestions": [
      "Q-INFO-002"
    ]
  },
  {
    "id": "successful-applicants-notice",
    "text": "전형 절차: 서류 검토 후 합격자에 한해 개별 연락드립니다.",
    "slot": "result_notice",
    "found": true
  },
  {
    "id": "business-result-not-notice",
    "text": "담당업무\n서비스 개발 결과를 고객에게 안내합니다.",
    "slot": "result_notice",
    "found": false
  },
  {
    "id": "pre-ai-interview",
    "text": "전형절차\nAI 사전면접을 진행합니다.",
    "slot": "ai_disclosure",
    "found": true,
    "absentQuestions": [
      "Q-INFO-005"
    ]
  },
  {
    "id": "ai-essay-assessment",
    "text": "전형절차\nAI 자기소개서 평가 : 적/부",
    "slot": "ai_disclosure",
    "found": true
  },
  {
    "id": "ai-product-experience",
    "text": "지원자격\nAI 사전면접 서비스 개발 경험",
    "slot": "ai_disclosure",
    "found": false
  },
  {
    "id": "pay-html-table",
    "text": "<table>\n<tr>\n<td>\n보수\n</td>\n<td>\n약 275만원/월 수준(세전)\n</td>\n</tr>\n</table>",
    "slot": "compensation",
    "found": true,
    "components": [
      "amount_or_range"
    ],
    "contains": "275만원"
  },
  {
    "id": "salary-heading-empty-markup",
    "text": "보수\n</td>\n</tr>",
    "slot": "compensation",
    "found": false
  },
  {
    "id": "maintenance-then-pay",
    "text": "담당업무 건물 유지보수 및 안전점검. 급여 회사 내규에 따라 산정합니다.",
    "slot": "compensation",
    "found": true,
    "contains": "급여 회사",
    "excludes": "유지보수"
  },
  {
    "id": "maintenance-alone",
    "text": "담당업무 건물 유지보수 및 안전점검.",
    "slot": "compensation",
    "found": false
  },
  {
    "id": "attachment-filename-not-content",
    "text": "2. 직무기술서(기간제 채용).hwpx",
    "slot": "qualification_rationale",
    "found": false
  },
  {
    "id": "job-rationale-body",
    "text": "자격요건\n직무 수행에 필요한 전기설비 점검 기술을 요구합니다.",
    "slot": "qualification_rationale",
    "found": true
  },
  {
    "id": "criteria-html-body",
    "text": "평가 기준\n</th>\n<td>\n직무역량 50점, 문제해결 50점\n</td>",
    "slot": "evaluation_criteria",
    "found": true,
    "contains": "직무역량"
  },
  {
    "id": "criteria-markup-only",
    "text": "평가 기준\n</th>\n</tr>",
    "slot": "evaluation_criteria",
    "found": false
  },
  {
    "id": "prohibited-family-list",
    "text": "본인의 성별·신체조건·출신학교, 부모의 직업 등 인적사항 기재 시 평가대상에서 제외(부적합 처리)",
    "absentFindings": [
      "FAMILY-001"
    ],
    "absentQuestions": [
      "Q-DIST-007",
      "Q-DIST-005"
    ]
  },
  {
    "id": "prohibited-family-school-list",
    "text": "형제자매의 학력·직업·재산 등 개인정보 수집 금지",
    "absentFindings": [
      "SCHOOL-001"
    ]
  },
  {
    "id": "marital-protection",
    "text": "혼인여부·재산 등 개인정보 수집 금지",
    "absentQuestions": [
      "Q-DIST-017"
    ]
  },
  {
    "id": "positive-family-requirement",
    "text": "부모의 직업을 필수 기재해야 합니다.",
    "presentFindings": [
      "FAMILY-001"
    ]
  },
  {
    "id": "positive-after-other-protection",
    "text": "출신학교 기재 금지; 부모의 직업 제출 필수.",
    "presentFindings": [
      "FAMILY-001"
    ]
  },
  {
    "id": "positive-on-next-sentence",
    "text": "혼인여부 수집 금지. 형제자매의 학력 필수 제출.",
    "presentFindings": [
      "SCHOOL-001"
    ]
  },
  {
    "id": "duty-interview-then-real",
    "text": "담당업무\n팀원 인터뷰를 통한 요구사항 조사\n전형절차\n서류 전형 → 직무 인터뷰",
    "presentQuestions": [
      "Q-INTER-007",
      "Q-PROC-011"
    ],
    "questionAfter": "전형절차"
  },
  {
    "id": "employee-story-then-real",
    "text": "직원의 이야기\n→ 인터뷰 자세히 보기 (클릭)\n전형절차\n서류전형 > 면접전형",
    "presentQuestions": [
      "Q-INTER-007",
      "Q-PROC-011"
    ],
    "questionAfter": "전형절차"
  },
  {
    "id": "only-duty-interview",
    "text": "담당업무\n팀원 인터뷰를 통한 요구사항 조사",
    "absentQuestions": [
      "Q-INTER-007",
      "Q-PROC-011"
    ]
  },
  {
    "id": "table-preference",
    "text": "<table>\n<tr><td>북한이탈주민</td><td>필기전형 점수 만점의 2%</td></tr>\n</table>",
    "absentQuestions": [
      "Q-DIST-013"
    ]
  },
  {
    "id": "other-row-not-preference",
    "text": "<table><tr><td>장애인</td><td>가산점 2%</td></tr><tr><td>북한이탈주민 지원 불가</td></tr></table>",
    "presentQuestions": [
      "Q-DIST-013"
    ]
  },
  {
    "id": "real-check-after-preference",
    "text": "우대사항\n북한이탈주민: 서류전형 만점의 2%\n유의사항\n신원조회 결과의 직무 관련성을 검토합니다.",
    "presentQuestions": [
      "Q-DIST-013"
    ],
    "questionAfter": "유의사항"
  },
  {
    "id": "sanction-period-not-proxy",
    "text": "공공기관에서 재직기간 중 형법에 규정된 죄를 범한 자로서 벌금형을 선고받은 자",
    "absentQuestions": [
      "Q-DIST-010"
    ]
  },
  {
    "id": "tenure-requirement-kept",
    "text": "지원자격\n재직 기간 3년 이상인 자",
    "presentQuestions": [
      "Q-DIST-010"
    ]
  },
  {
    "id": "preference-proof-not-exclusion",
    "text": "제출서류\n북한이탈주민 등록확인서(가점 대상자에 한함)",
    "absentQuestions": [
      "Q-DIST-013"
    ]
  },
  {
    "id": "actual-degree-after-protection",
    "text": "유의사항\n출신학교 기재 금지\n지원자격\n석사학위 취득자 또는 전문학사 학위 취득 후 관련 경력이 있는 자",
    "presentQuestions": [
      "Q-DIST-005"
    ],
    "questionAfter": "지원자격"
  },
{
  "id": "double-negative-family-information-required",
  "text": "형제자매의 학력을 기재하지 않는 것은 허용되지 않습니다.",
  "presentFindings": [
    "SCHOOL-001"
  ]
}
]''')


def assert_case(case: dict, result: dict) -> None:
    if "slot" in case:
        slot = next(s for s in result["slots"] if s["slot"] == case["slot"])
        assert slot["found"] is case["found"]
        assert set(case.get("components", [])) <= set(slot["components_found"])
        if "contains" in case:
            assert case["contains"] in slot["evidence"]
        if "excludes" in case:
            assert case["excludes"] not in slot["evidence"]
        if not case["found"]:
            assert slot["evidence"] is None
            assert slot["components_found"] == []
    for field, absent_key, present_key in (
        ("questions", "absentQuestions", "presentQuestions"),
        ("findings", "absentFindings", "presentFindings"),
    ):
        by_id = {item["id"]: item for item in result[field]}
        assert not set(case.get(absent_key, [])) & set(by_id)
        assert set(case.get(present_key, [])) <= set(by_id)
        if field == "questions" and "questionAfter" in case:
            for question_id in case.get(present_key, []):
                start, end = by_id[question_id]["offset"]
                assert start >= case["text"].index(case["questionAfter"])
                assert case["text"][start:end] == by_id[question_id]["matched_text"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_second_context_regression(case: dict) -> None:
    assert_case(case, FairpostEngine().check(case["text"]).to_dict())


@pytest.mark.requires_node
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_second_context_python_web_parity(case: dict) -> None:
    encoded = base64.b64encode(case["text"].encode("utf-8")).decode("ascii")
    completed = subprocess.run(
        ["node", "tests/js_runner.cjs", encoded], cwd=ROOT,
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    result = json.loads(completed.stdout)
    assert_case(case, result)
    assert result == FairpostEngine().check(case["text"]).to_dict()
