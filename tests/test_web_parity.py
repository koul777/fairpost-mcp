from __future__ import annotations

import base64
import json
from pathlib import Path
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
        "multiple-sections",
        "multitrack-long-context",
        "multitrack-outside-context",
        "multitrack-unicode-context",
        "multitrack-unicode-exclusion",
        "multitrack-unicode-left-context",
        "multitrack-null-cell",
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


def test_static_web_has_no_network_capability_and_shows_version() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    engine = (ROOT / "web" / "engine.js").read_text(encoding="utf-8")
    assert "connect-src 'none'" in html
    assert "브라우저 밖으로 전송되지 않습니다" in html
    assert "fetch(" not in app + engine
    assert "XMLHttpRequest" not in app + engine
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
    assert "visiblePostingQuestions.forEach(appendQuestion)" in app
    assert 'appendQuestion(slotQuestion, "  확인 질문:")' in app
    assert 'class="question-detail"' in app
    assert "후속 질문 ${question.follow_up.length}개 보기" in app
    assert "question.reference.publisher" in app
    assert "question.reference.accessed_at" in app
    assert "확인 ${question.reference.accessed_at}" in app
    assert "function moveCodePointsLeft" in engine
    assert "function moveCodePointsRight" in engine
    assert "const codePoints = Array.from(text)" not in engine


def test_web_bundle_version_matches_core() -> None:
    bundle = (ROOT / "web" / "data.js").read_text(encoding="utf-8")
    assert FairpostEngine().ruleset.version in bundle


def test_web_css_preserves_hidden_state_and_mobile_width() -> None:
    css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    compact = "".join(css.split())
    assert "[hidden]{display:none!important;}" in compact
    assert "html,body{width:100%;max-width:100%;overflow-x:hidden;}" in compact
    assert ".editor-pane,.results-pane{width:100%;max-width:100%;min-width:0;" in compact
