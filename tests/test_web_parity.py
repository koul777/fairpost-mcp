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
    assert 'class="question-detail"' in app
    assert "후속 질문 ${question.follow_up.length}개 보기" in app


def test_web_bundle_version_matches_core() -> None:
    bundle = (ROOT / "web" / "data.js").read_text(encoding="utf-8")
    assert FairpostEngine().ruleset.version in bundle


def test_web_css_preserves_hidden_state_and_mobile_width() -> None:
    css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    compact = "".join(css.split())
    assert "[hidden]{display:none!important;}" in compact
    assert "html,body{width:100%;max-width:100%;overflow-x:hidden;}" in compact
    assert ".editor-pane,.results-pane{width:100%;max-width:100%;min-width:0;" in compact
