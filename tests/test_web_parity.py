from __future__ import annotations

import base64
import json
from pathlib import Path
import random
import re
import shutil
import subprocess

import pytest

from core import FairpostEngine


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.requires_node
@pytest.mark.parametrize(
    "text",
    [
        "청년인턴 채용",
        "２０대 청년\u200b인턴 채용",
        "자격요건\n남성에 한합니다.",
        "자격요건\n２０대 지원자 우대",
        "자격요건\n남성에 한합\u200b니다.",
        "전형절차\nＡＩ가 최종 결정",
        "책임감 있으신 분을 찾습니다.",
        "자격요건\r\n남성만 지원 가능\r\n전형절차\r\n서류전형",
        "📌 채용 안내\n자격요건\n여성만 지원 가능",
        (
            "지원자격은 별도로 안내합니다. 급여는 면접 후 협의합니다. "
            "개인정보 보관기간은 추후 안내합니다."
        ),
        (("😀" * 180) + " 급여는 협의합니다 " + ("가" * 180)),
        (
            "AI 면접으로 최종 결정\n일정\n접수 기간 8월 1일까지\n"
            "문의처\n인사팀 02-1234-5678"
        ),
        (
            "📌 채용공고\r\n근무분야\r\n행정직, 연구직, 기술직\r\n"
            "학력정보\r\n학력무관, 대졸(4년), 석사\r\n"
            + ("가" * 4000)
            + "\r\n입사\u200b지원서"
        ),
        (
            "근무분야\n행정직, 연구직\n학력사항\n"
            + ("가" * 5100)
            + "\n입사지원서"
        ),
        (
            "근무분야\n행정직, 연구직\n학력사항\n"
            + ("😀" * 1000)
            + ("가" * 3800)
            + "\n입사지원서"
        ),
        (
            "근무분야\n행정직, 연구직\n학력사항\n입사지원서\n"
            + ("😀" * 1000)
            + ("가" * 3800)
            + "\n직렬별로 각각 별도 입사지원서"
        ),
        (
            "입사지원서\n"
            + ("😀" * 1000)
            + ("가" * 3800)
            + "\n학력사항\n근무분야\n행정직, 연구직"
        ),
        "근무분야\n행정직, 연구직\n학력사항\n무관\n입사지원서",
        "지원자격\r\n다음 요건을 모두 충족한 자\r\nOPIc IH 이상",
        "지원자격\n장애인 지원 불가",
        "장애가 없는 사람과 장애인 모두 지원할 수 있습니다.",
        "지원자격\n세례교인에 한함",
        "근무지\n지역 교회 부설 복지센터",
        "\ufeff문의처와 이의신청 절차를 안내합니다.",
        "입사지원서 항목\n혼인 여부",
        "블라인드 안내\n임신 여부는 지원서에 기재하지 마세요.",
        "복리후생: 재직 기간별 장기근속 포상",
        "자격요건: 이전 직장 재직 기간 3년 이상",
        "복리후생: 재직 기간별 장기근속 포상\n지원자격: 군필 또는 면제자",
        "블라인드 채용을 위해 과거 병력은 기재하지 마세요.",
        "과거 병력은 기재하지 마세요. 다만 치료 이력은 지원서에 제출해야 합니다.",
        "AI가 최종 결정하지 않으며 채용담당자가 최종 판단합니다.",
        "AI가 최종 결정하지 않고 사람이 판단합니다. 다만 1차 전형은 AI 자동 탈락을 적용합니다.",
        "지원서에 치료 이력을 제출해야 합니다. 단, 과거 병력은 기재하지 마세요.",
        "1차는 AI 자동 탈락을 적용합니다. 단, AI가 최종 결정하지 않고 최종은 사람이 판단합니다.",
    ],
    ids=[
        "plain-age-term",
        "normalized-age-term",
        "gender-rule",
        "normalized-age-rule",
        "zero-width-gender-rule",
        "normalized-ai-process",
        "benign-personality",
        "crlf-sections",
        "emoji-prefix",
        "single-line-sentence-evidence",
        "long-unicode-evidence-window",
        "multiple-sections",
        "multitrack-long-context",
        "multitrack-outside-context",
        "multitrack-unicode-context",
        "multitrack-unicode-exclusion",
        "multitrack-unicode-left-context",
        "multitrack-null-cell",
        "language-score-requirement",
        "disability-direct-exclusion",
        "disability-inclusive-context",
        "religion-explicit-qualification",
        "religious-workplace-context",
        "bom-prefixed-slot-evidence",
        "marital-screening-question",
        "pregnancy-blind-guidance",
        "proxy-benefit-candidate-exclusion",
        "proxy-duration-qualification",
        "proxy-later-candidate-preserved",
        "health-history-protective-exclusion",
        "health-history-later-candidate-preserved",
        "ai-human-final-decision-protective-exclusion",
        "ai-later-automated-decision-preserved",
        "health-earlier-candidate-preserved-before-protection",
        "ai-earlier-candidate-preserved-before-protection",
    ],
)
def test_web_engine_matches_python_core(text: str) -> None:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    completed = subprocess.run(
        ["node", "tests/js_runner.cjs", encoded],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    web_result = json.loads(completed.stdout)
    python_result = FairpostEngine().check(text).to_dict()
    assert web_result == python_result


@pytest.mark.requires_node
@pytest.mark.parametrize(
    "text",
    [
        "과거\u200b 병력은\u200b 기재하지\u200b 마세요.",
        "질병\u200b 이력\u200b 및\u200b 치료\u200b 이력은\u200b 작성하지\u200b 마세요.",
        "ＡＩ가 최종 결정하지 않으며 사람이 판단합니다.",
        "AI\u200b 자동\u200b 탈락은\u200b 없습니다.",
    ],
)
def test_normalized_regex_exclusions_match_python_core(text: str) -> None:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    completed = subprocess.run(
        ["node", "tests/js_runner.cjs", encoded],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    web_result = json.loads(completed.stdout)
    python_result = FairpostEngine().check(text).to_dict()
    assert web_result == python_result
    assert "AI-001" not in {item["id"] for item in python_result["findings"]}
    assert "HEALTH-001" not in {
        item["id"] for item in python_result["findings"]
    }


@pytest.mark.requires_node
def test_seeded_unicode_combinations_match_python_core() -> None:
    rng = random.Random(20260830)
    fragments = [
        "지원자격\n",
        "근무분야\r\n",
        "여성만 지원 가능합니다.",
        "과거 병력은 기재하지 마세요.",
        "AI가 최종 결정하지 않고 사람이 판단합니다.",
        "ＡＩ 자동 탈락",
        "청년\u200b인턴 채용",
        "질병\u2060 이력 및 치료 이력",
        "장애가 없는 사람",
        "종교인만 지원 가능",
        "혼인 여부",
        "병역 면제자 제외",
        "🙂📋",
        "\u00a0\t",
        "!@#$%^&*()[]{}",
        "가" * 257,
    ]

    for case_index in range(64):
        text = "".join(
            rng.choice(fragments) for _ in range(rng.randint(1, 12))
        )
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        completed = subprocess.run(
            ["node", "tests/js_runner.cjs", encoded],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        web_result = json.loads(completed.stdout)
        python_result = FairpostEngine().check(text).to_dict()
        assert web_result == python_result, f"seeded case {case_index}"


def test_static_web_keeps_optional_assisted_review_off_by_default() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    engine = (ROOT / "web" / "engine.js").read_text(encoding="utf-8")
    assert "connect-src 'self'" in html
    assert '<link rel="icon" href="/favicon.svg"' in html
    assert "기본 검사는 브라우저 안에서 처리" in html
    assert "역할 기록은 브라우저에만 저장" in html
    assert 'id="deploy-button"' in html
    assert "https://vercel.com/new/clone?repository-url=" in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html
    assert "GitHub 저장소 접근 권한 필요" in html
    assert "'보강 실행'을 누른 요청에서만 동작" in html
    assert 'id="assisted-review-run"' in html
    assert 'id="assisted-review-disclosure"' in html
    assert 'aria-describedby="assisted-review-status assisted-review-disclosure"' in html
    assert 'id="assisted-review-live" class="visually-hidden" role="status" aria-live="polite"' in html
    # Exactly one place may start an AI request: the explicit run button.
    assert app.count("void runAssistedReview(input.value)") == 1
    assert "assistedRun.addEventListener(\"click\"" in app
    assert "판정이 아니라 수정·확인 질문을 정리한 로컬 검토 메모입니다." in html
    assert "fairpost | 채용공고 검토 메모" in html
    assert "검토 메모 만들기" in html
    assert 'id="results-title" tabindex="-1"' in html
    assert 'window.matchMedia("(max-width: 1100px)").matches' in app
    assert 'resultsTitle.scrollIntoView({ behavior: "auto", block: "start" })' in app
    assert "개수는 검토할 작업량입니다." in html
    assert "점수·등급·합격/불합격 또는 공정성 판정" in html
    assert html.index('id="disclaimer"') < html.index('class="summary-strip"')
    assert 'class="summary-strip" role="group"' in html
    assert "공고별 질문" in html
    assert 'class="next-step-strip" role="group"' in html
    assert "1. 확인된 표현의 근거와 대체 문구를 검토합니다." in html
    assert 'id="answer-progress"' in html
    assert "질문별 답변은 현재 분석 세션에만 남고" in html
    assert 'id="assisted-review-toggle"' in html
    assert 'id="assisted-review-provider"' in html
    assert 'role="switch"' in html
    assert 'id="assisted-review-panel"' in html
    assert 'id="organization-sector"' in html
    assert 'id="organization-public-type"' in html
    assert 'id="organization-size"' in html
    assert "공공기관" in html and "민간기업" in html
    assert "공기업" in html and "준정부기관" in html
    assert "지방공기업·지방출자출연기관" in html
    assert 'fetch("/api/assisted-review"' in app
    assert "assist_enabled: true" in app
    assert "if (assistedToggle.checked)" in app
    assert "function organizationContext(question)" in app
    assert 'class="organization-context"' in app
    assert 'class="applicability-tag"' in app
    assert "공공기관 지정 유형 확인 필요" in app
    assert "공공기관 경영·혁신 지침을 직접 적용하지 않고" in app
    assert "공공기관 지침 중심" in app
    assert "소규모 운영" in app
    assert "XMLHttpRequest" not in app + engine
    assert "localStorage" in app
    assert "sessionStorage" not in app
    assert 'id="role-review-panel"' in html
    assert 'id="role-review-record"' in html
    assert 'id="role-review-clear"' in html
    assert 'id="role-review-resolve-event"' in html
    assert 'id="role-review-missing"' in html
    assert "function initializeRoleReview(result, text)" in app
    assert "function recordRoleReviewEvent()" in app
    assert "function roleReviewIssueStatuses()" in app
    assert "미해결 이슈" in app
    assert "posting_fingerprint" in app
    assert "원문·지원자 정보는 역할 기록에 저장하지 않습니다." in html
    assert "공고 원문·지원자 개인정보·연락처" in html
    assert "fairpost 채용공고문 검토 메모" in app
    assert "검토 메모를 복사했습니다." in app
    assert 'high: "우선 검토"' in app
    assert 'medium: "검토"' in app
    assert '<span class="visually-hidden">검토 우선도 </span>' in app
    assert 'aria-label="검토 우선도' not in app
    assert "개수는 검토할 작업량이며 점수·등급·합격/불합격 또는 공정성 판정이 아닙니다." in app
    assert 'id="common-checklist"' in app
    assert "<details" in app
    assert "공통 기본 체크리스트" in app
    assert '["Q-INFO-001", "Q-INFO-004", "Q-PROC-002"]' in app
    assert "SLOT_EMBEDDED_QUESTION_ALLOWLIST.has(rule.id)" in app
    assert "Object.values(SLOT_QUESTION_IDS)" in app
    assert "rule.trigger.field" in app
    assert 'class="slot-question-detail"' in app
    assert ">확인 질문 보기</summary>" in app
    assert '<details class="slot-question-detail" open' not in app
    assert "!SLOT_EMBEDDED_QUESTION_IDS.has(question.id)" in app
    assert "Boolean(question.matched_text)" not in app
    assert "renderSlots(result.slots, result.questions)" in app
    assert (
        "question.review_scope !== \"common\" &&\n"
        "          !SLOT_EMBEDDED_QUESTION_IDS.has(question.id)"
    ) in app
    assert "visiblePostingQuestions.forEach((question) => appendQuestion(question))" in app
    assert 'appendQuestion(slotQuestion, "  확인 질문:")' in app
    assert 'class="question-detail"' in app
    assert "후속 질문 ${question.follow_up.length}개 보기" in app
    assert "question.reference.publisher" in app
    assert "question.reference.accessed_at" in app
    assert "확인 ${question.reference.accessed_at}" in app
    assert 'data-question-answer="${escapeHtml(question.id)}"' in app
    assert "reviewAnswers.set(questionId, target.value)" in app
    assert "담당자 답변 진행: ${answeredCount}/${result.questions.length}" in app
    assert "서버나 브라우저 저장소로 전송·저장되지 않습니다." in app
    assert "reviewAnswers.clear()" in app
    assert 'document.getElementById(id).replaceChildren()' in app
    assert "function moveCodePointsLeft" in engine
    assert "function moveCodePointsRight" in engine
    assert "const codePoints = Array.from(text)" not in engine


@pytest.mark.requires_node
def test_web_review_answers_are_copied_and_cleared_locally() -> None:
    completed = subprocess.run(
        ["node", "tests/web_app_review_runner.cjs"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    result = json.loads(completed.stdout)
    assert result["resultsTitleFocused"] is True
    reader = result["readerFlow"]
    assert reader["defaultExternalPosts"] == 0
    for key in (
        "easyQuestionsAccessible", "uniqueAnswerIds", "modeRoundtripPreserved",
        "sameInputRecheckPreserved", "staleCopyBlocked",
        "priorRecordsSeparated", "escapedPriorRecords",
    ):
        assert reader[key] is True, key
    assert "[현재 공고 검토 기록 — 자기 기록]" in reader["recordMemo"]
    assert "[수정 전 검토 기록 — 현재 공고에 자동 적용되지 않음]" in reader["afterEditMemo"]
    comparison = result["comparisonFlow"]
    assert comparison["firstComparison"]["hidden"] is False
    assert comparison["firstComparison"]["groups"] == ""
    assert "첫 검토" in comparison["firstComparison"]["status"]
    assert comparison["staleComparison"]["copyDisabled"] is True
    assert comparison["staleComparison"]["resetDisabled"] is True
    assert comparison["staleComparison"]["copyUnchanged"] is True
    assert "공고문이 바뀌었습니다" in comparison["staleComparison"]["status"]

    def group(markup: str, name: str) -> str:
        return markup.split(f'comparison-{name}">', 1)[1].split("</details>", 1)[0]

    assert "여성만" in group(comparison["changedComparison"], "removed")
    assert "30세" in group(comparison["changedComparison"], "added")
    assert "여성만" in group(comparison["thirdComparison"], "removed")
    assert comparison["unchangedComparison"] == comparison["thirdComparison"]
    assert "[수정 전후 비교]" in comparison["comparisonReport"]
    assert "표시가 사라져도 검토 완료를 뜻하지 않습니다" in comparison["comparisonReport"]
    assert "SEX-001" in comparison["comparisonReport"]
    assert comparison["resetComparison"] == ""
    assert "여성만" in group(comparison["rebasedComparison"], "added")
    assert comparison["versionComparison"]["groups"] == ""
    assert "버전이 바뀌어" in comparison["versionComparison"]["status"]
    assert comparison["comparisonStored"] is False
    assert comparison["postsAdded"] == 0
    assert comparison["sampleComparisonHidden"] is True
    assert comparison["sampleComparisonGroups"] == ""
    assert comparison["clearedComparisonHidden"] is True
    assert result["progressAfterAnswer"] == (
        f"담당자 답변 1/{result['questionCount']}"
    )
    assert result["questionId"] in result["copiedWithAnswer"]
    assert "담당자 답변: 원문을 직무 요건 중심으로 수정합니다." in result[
        "copiedWithAnswer"
    ]
    assert "담당자 재확인 완료" in result["copiedWithAnswer"]
    assert f"- {result['questionId']}" in result["copiedWithAnswer"]
    assert f"0 {result['questionId']}" not in result["copiedWithAnswer"]
    assert result["manuallyCleared"] == {
        "progress": "담당자 답변 0/0",
        "resultHidden": True,
        "copyDisabled": True,
    }
    assert result["progressAfterRerun"] == (
        f"담당자 답변 0/{result['questionCount']}"
    )
    assert "담당자 재확인 완료" not in result["copiedAfterRerun"]
    assert result["copyFailureToast"] == "브라우저에서 메모를 복사하지 못했습니다."
    assert result["cleared"] == {
        "input": "",
        "progress": "담당자 답변 0/0",
        "resultHidden": True,
        "copyDisabled": True,
        "dynamicContainersCleared": True,
    }
    assert result["easySelection"] == {
        "selections": [[0, 6]],
        "toast": "공고문이 바뀌었습니다. '검토 메모 만들기'를 다시 누르면 위치가 새로 계산됩니다.",
    }
    assert result["easyModeAssist"] == {
        "assistOnInExpert": True,
        "toggleChecked": False,
        "badge": "꺼짐",
        "privacy": "기본 검사는 브라우저 안에서 처리 · 역할 기록은 브라우저에만 저장",
        "runDisabled": True,
        "postsAfterEasyCheck": 0,
    }
    assisted = result["assisted"]
    assert assisted["badge"] == "켜짐"
    assert assisted["resultStatus"] == "완료"
    assert assisted["output"] == (
        "현행 조문을 바탕으로 사람의 적용 범위 확인이 필요합니다."
    )
    assert assisted["panelHidden"] is False
    assert "경영·혁신 지침 직접 검토" in assisted["organizationMarkup"]
    assert "공기업·준정부기관의 경영에 관한 지침" in assisted[
        "organizationMarkup"
    ]
    assert assisted["postBody"] == {
        "assist_enabled": True,
        "ai_provider": "anthropic",
        "text": "여성만 지원 가능",
        "organization_profile": {
            "sector": "public",
            "size": "300_plus",
            "public_entity_type": "public_corporation",
            "sector_label": "공공기관",
            "public_entity_type_label": "공기업",
            "size_label": "상시근로자 300명 이상",
        },
    }
    assert set(assisted) == {
        "badge",
        "resultStatus",
        "output",
        "panelHidden",
        "postBody",
        "organizationMarkup",
    }
    flow = result["assistedFlow"]
    # Page load only asks whether the feature is available; nothing is posted.
    assert flow["availability"] == {
        "gets": [{"url": "/api/assisted-review", "body": None}],
        "toggleDisabled": False,
        "badge": "꺼짐",
    }
    # Turning the toggle on or re-running the local check never sends.
    assert flow["afterToggle"]["posts"] == 0
    assert flow["afterToggle"]["badge"] == "켜짐 · 전송 전"
    assert flow["afterToggle"]["consentHidden"] is False
    assert flow["afterToggle"]["runDisabled"] is True
    assert "보강 실행을 누르기 전에는 아무것도 전송하지 않습니다" in flow[
        "afterToggle"
    ]["live"]
    assert "'보강 실행'을 누를 때만" in flow["afterToggle"]["privacy"]
    assert flow["afterCheck"]["posts"] == 0
    assert flow["afterCheck"]["runDisabled"] is False
    assert flow["afterCheck"]["panelHidden"] is True
    assert flow["whileRunning"]["runDisabled"] is True
    assert "결과를 기다리는 중" in flow["whileRunning"]["live"]
    assert "로컬 검토 메모" not in flow["whileRunning"]["resultsNote"]
    assert "선택한 AI 제공자를 거친 초안" in flow["afterResult"]["resultsNote"]
    assert flow["afterResult"]["posts"] == 1
    assert flow["afterResult"]["live"] == "AI·현행 법령 보강 결과: 완료."
    assert "현행 조문을 바탕으로 사람의 적용 범위 확인이 필요합니다." in flow[
        "memoWithCurrentAi"
    ]
    # Provider and organization changes mark the result outdated, no re-send.
    changed = flow["afterProviderChange"]
    assert changed["posts"] == 1
    assert changed["resultStatus"] == "이전 입력 기준"
    assert "메모 복사에는 포함하지 않습니다" in changed["notice"]
    assert "AI 제공자가 바뀌었습니다" in changed["live"]
    assert "아직 전송하지 않았습니다" in changed["status"]
    assert flow["afterProviderRevert"] == "완료"
    organization_change = flow["afterOrganizationChange"]
    assert organization_change["posts"] == 1
    assert organization_change["resultStatus"] == "이전 입력 기준"
    assert "이전 공고문·설정으로 받은 AI 메모라 포함하지 않았습니다" in (
        organization_change["memo"]
    )
    assert "현행 조문을 바탕으로 사람의" not in organization_change["memo"]
    assert flow["afterOff"] == {
        "panelHidden": True,
        "consentHidden": True,
        "live": "AI·현행 법령 보강을 껐습니다.",
    }
    # A 503 from the server turns the feature off visibly and announces it.
    disabled = flow["autoDisabled"]
    assert disabled["posts"] == 1
    assert disabled["toggleChecked"] is False
    assert disabled["toggleDisabled"] is True
    assert disabled["badge"] == "사용 불가"
    assert "설정 필요: AI API" in disabled["status"]
    assert "자동으로 꺼졌습니다" in disabled["live"]
    assert disabled["runDisabled"] is True
    assert flow["emptyInput"] == {
        "error": "검토할 공고문을 입력하세요.",
        "errorHidden": False,
        "invalid": "true",
        "clearedOnInput": True,
    }
    role_review = result["roleReview"]
    self_report = "(이 브라우저의 자기 기록 · 결재 아님)"
    assert role_review["status"] == "로컬 기록"
    # The auto-created chair event is not participation.
    assert role_review["initialProgress"] == (
        f"자기 기록 역할 0/7 · 이벤트 0개 · 미해결 이슈 0건 {self_report}"
    )
    assert role_review["progress"] == (
        f"자기 기록 역할 2/7 · 이벤트 3개 · 미해결 이슈 0건 {self_report}"
    )
    assert "위원장" in role_review["missingRoles"]
    assert "아직 기록이 없는 역할" in role_review["missingRoles"]
    assert "참여로 세지 않음" in role_review["eventsMarkup"]
    assert "감사자 · 평가 · 메모" in role_review["eventsMarkup"]
    assert "릴리스 전 재현성 근거" in role_review["eventsMarkup"]
    assert "해결됨" in role_review["eventsMarkup"]
    assert "3번 이슈 해결" in role_review["eventsMarkup"]
    assert "여성만 지원 가능" not in role_review["storage"]
    assert "posting_text" not in role_review["storage"]
    memo = role_review["copiedMemo"]
    assert "[다중 역할 검토 기록 — 이 브라우저의 자기 기록 · 결재 아님]" in memo
    assert "자기 기록 역할: 2/7 · 이벤트: 3개 · 미해결 이슈: 0건" in memo
    assert "위원회 승인·결재가 아닙니다" in memo
    assert "/7 · 이벤트: 4개" not in memo
    assert role_review["afterSensitiveNote"] == {
        "progress": f"자기 기록 역할 1/7 · 이벤트 1개 · 미해결 이슈 0건 {self_report}",
        "eventCount": 2,
        "noteError": (
            "메모에 이메일 형식이 있어 기록하지 않았습니다. "
            "원문이나 직접 식별정보 대신 요약과 근거 ID를 사용하세요."
        ),
        "noteErrorHidden": False,
        "noteInvalid": "true",
    }
    assert role_review["noteErrorClearedOnInput"] == {"hidden": True, "invalid": None}
    assert role_review["evidenceError"]["invalid"] == "true"
    assert "근거 ID는" in role_review["evidenceError"]["message"]
    assert role_review["resolveError"] == "해결할 수정 요청 또는 이관을 선택하세요."
    visible_options = re.sub(r'value="[^"]*"', "", role_review["resolveOptions"])
    assert "browser-event" not in visible_options
    assert "3번 · 감사자 · 수정 요청 · " in visible_options
    assert "직무 요건 근거를 보강해야 합니다." in visible_options
    assert role_review["reloaded"]["packetCount"] == 1
    assert role_review["reloaded"]["progress"] == role_review["progress"]
    assert "역할 기록(이벤트 3개)을 불러왔습니다" in role_review["reloaded"]["notice"]
    drift = role_review["afterVersionDrift"]
    assert drift["newPacket"] is True
    assert drift["oldPacketKept"] is True
    assert drift["packetCount"] == 2
    assert "규칙셋 또는 지침 버전이 바뀌어 새 패킷을 시작했습니다" in drift["notice"]
    assert "그대로 보관합니다" in drift["notice"]
    assert "분리했습니다" not in drift["notice"]
    assert role_review["otherPosting"]["packetCount"] == 3
    assert "이전 패킷은 지우지 않습니다" in role_review["otherPosting"]["notice"]
    assert "저장된 이벤트 1개는 형식 검사" in role_review["invalidEvent"]["notice"]
    assert role_review["invalidEvent"]["progress"] == role_review["progress"]
    assert role_review["migration"] == {
        "legacyRemoved": True,
        "migratedEvents": 4,
        "systemMarked": "system-chair",
        "progress": role_review["progress"],
    }
    assert role_review["cancelledDelete"] == {
        "packetCount": 2,
        "notice": "삭제를 취소했습니다. 이 공고의 역할 기록은 그대로 있습니다.",
        "panelHidden": False,
    }
    assert role_review["confirmedDelete"] == {
        "prompts": 2,
        "packetCount": 1,
        "currentRemoved": True,
        "panelHidden": True,
    }
    retention = role_review["retention"]
    assert retention["packetsBefore"] == 20
    assert retention["packetCount"] == 20
    assert retention["oldestEvicted"] is True
    assert "보관 한도 20개" in retention["notice"]
    assert "가장 오래된 역할 기록 패킷 1개" in retention["notice"]
    assert role_review["corrupt"]["untouched"] is True
    assert role_review["corrupt"]["status"] == "세션 기록"
    assert "덮어쓰지 않고 현재 세션에만 기록합니다" in role_review["corrupt"]["notice"]
    assert role_review["unavailable"]["status"] == "세션 기록"
    assert "브라우저 저장소를 사용할 수 없어" in role_review["unavailable"]["notice"]
    assert role_review["unavailable"]["progress"].startswith("자기 기록 역할 1/7 · 이벤트 1개")


def test_assisted_disclosure_matches_server_behavior() -> None:
    from core.direct_identifiers import DIRECT_IDENTIFIER_PATTERNS
    from mcp_server.assisted_review import MAX_MATCHED_TEXT_CHARS

    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    match = re.search(
        r'<p id="assisted-review-disclosure"[^>]*>(.*?)</p>', html, re.S
    )
    assert match is not None
    disclosure = " ".join(re.sub(r"<[^>]+>", "", match.group(1)).split())
    assert disclosure.startswith("보강 실행을 누를 때만 전송합니다.")
    assert "공고문 전문 → 이 FairPost 서버" in disclosure
    assert f"항목당 최대 {MAX_MATCHED_TEXT_CHARS:,}자" in disclosure
    for pattern in DIRECT_IDENTIFIER_PATTERNS:
        assert pattern.label in disclosure
    assert "공고문 전문은 AI 제공자에게 보내지 않습니다." in disclosure
    assert "조직 조건" in disclosure
    assert "Korean Law MCP: 법령명과 조문번호만" in disclosure
    # The disclosure is visible text next to the run button, not a tooltip.
    consent = html[html.index('id="assisted-review-consent"') :]
    assert consent.index('id="assisted-review-disclosure"') < consent.index(
        'id="assisted-review-run"'
    )


def test_web_bundle_version_matches_core() -> None:
    bundle = (ROOT / "web" / "data.js").read_text(encoding="utf-8")
    assert FairpostEngine().ruleset.version in bundle


def test_web_css_preserves_hidden_state_and_mobile_width() -> None:
    css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    compact = "".join(css.split())
    assert "[hidden]{display:none!important;}" in compact
    assert "html,body{width:100%;max-width:100%;overflow-x:hidden;}" in compact
    assert ".editor-pane,.results-pane{width:100%;max-width:100%;min-width:0;" in compact
    assert ".button-deploy{" in compact
    assert ".results-note{" in compact
    assert ".next-step-strip{" in compact
    assert "#results-title:focus-visible{" in compact
    assert ".review-progress-strip{" in compact
    assert ".review-answer-contenttextarea{" in compact
    assert "a:focus-visible{" in compact
    assert "@media(prefers-reduced-motion:reduce)" in compact
    assert "grid-template-columns:minmax(0,1fr);" in compact
    assert ".editor-pane>*{min-width:0;}" in compact
    assert "grid-template-columns:repeat(3,minmax(0,1fr));" in compact
    assert ".organization-profilelabel{min-width:0;" in compact
    assert ".organization-profileselect{width:100%;min-width:0;max-width:100%;" in compact
    assert "@media(max-width:1100px)" in compact
    assert "@media(max-width:820px)" in compact


def test_web_css_focus_and_text_size_are_accessible() -> None:
    css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    compact = "".join(css.split())
    focus_rule = re.search(r"button:focus-visible,([^{]*)\{([^}]*)\}", compact)
    assert focus_rule is not None
    for selector in ("select:focus-visible", "input:focus-visible", "textarea:focus-visible"):
        assert selector in focus_rule.group(1)
    assert "outline:3pxsolid#1d5c80;" in focus_rule.group(2)
    assert "rgba(53,106,138,0.3)" not in compact
    # Information-bearing text is at least 12px; nothing is 9-10px any more.
    assert not re.search(r"font-size:\s*(?:[0-9]|10)px", css)
    assert ".visually-hidden{" in compact
    assert ".field-error{" in compact
    assert '[aria-invalid="true"]{' in compact


def test_book_companion_reader_flow_and_storage_boundaries() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    header = html[html.index("<header"):html.index("</header>")]
    footer = html[html.index("<footer"):]
    assert "deploy-button" not in header
    assert "https://vercel.com/new/clone" in footer
    assert 'class="result-block shared-questions"' in html
    assert 'class="review-progress-strip"' in html
    assert 'class="organization-options"' in html
    assert 'class="role-review-disclosure"' in html
    for field in ("human-review-evidence", "human-review-reason", "human-review-next"):
        assert f'id="{field}"' in html
    assert "기록이 있는 검토를 최근 최대 10회" in html
    assert "더 오래된 기록은 제거" in html
    assert "새로고침·지우기·예시 입력" in html
    for title in ("기회와 요건", "기준과 재검토", "존중과 목소리", "설명과 접근"):
        assert title in app
    assert "담당자가 실제 AI 사용 여부와 안내 적용 여부를 확인" in app
    assert "현재 공고에 자동 적용되지 않음" in app
    assert "sameReview" in app
    assert "previousReviewRecords.slice(-10)" in app
    assert "expertPointer" not in app
