from __future__ import annotations

import base64
import json
from pathlib import Path
import random
import shutil
import subprocess

import pytest

from core import FairpostEngine


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js가 필요합니다")
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


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js가 필요합니다")
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


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js가 필요합니다")
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


def test_static_web_has_no_network_capability_and_shows_version() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    engine = (ROOT / "web" / "engine.js").read_text(encoding="utf-8")
    assert "connect-src 'none'" in html
    assert '<link rel="icon" href="/favicon.svg"' in html
    assert "입력ㆍ답변은 이 브라우저 밖으로 전송되지 않으며" in html
    assert "배포ㆍ근거 링크만 새 외부 페이지를 엽니다" in html
    assert 'id="deploy-button"' in html
    assert "https://vercel.com/new/clone?repository-url=" in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html
    assert "GitHub 저장소 접근 권한 필요" in html
    assert "현재 입력값은 전달하지 않음" in html
    assert "판정이 아니라 수정·확인 질문을 정리한 로컬 검토 메모입니다." in html
    assert "fairpost | 채용공고 검토 메모" in html
    assert "검토 메모 만들기" in html
    assert 'id="results-title" tabindex="-1"' in html
    assert "개수는 검토할 작업량입니다." in html
    assert "점수·등급·합격/불합격 또는 공정성 판정" in html
    assert html.index('id="disclaimer"') < html.index('class="summary-strip"')
    assert 'class="summary-strip" role="group"' in html
    assert "공고별 질문" in html
    assert 'class="next-step-strip" role="group"' in html
    assert "1. 확인된 표현의 근거와 대체 문구를 검토합니다." in html
    assert 'id="answer-progress"' in html
    assert "질문별 답변은 현재 분석 세션에만 남고" in html
    assert "fetch(" not in app + engine
    assert "XMLHttpRequest" not in app + engine
    assert "localStorage" not in app
    assert "sessionStorage" not in app
    assert "fairpost 채용공고문 검토 메모" in app
    assert "검토 메모를 복사했습니다." in app
    assert 'high: "우선 검토"' in app
    assert 'medium: "검토"' in app
    assert 'aria-label="검토 우선도' in app
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


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js가 필요합니다")
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
