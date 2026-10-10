"""Browser posting-improvement flow: check -> fix -> check again.

The runner boots web/app.js in a fake DOM with web/posting-templates.js when
it exists, otherwise with a small fixture that follows the same contract.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
CSP = (
    "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "style-src 'self'; script-src 'self'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'"
)
NO_TEMPLATE_STATE = {
    "buttons": 0,
    "rewriteOffered": True,
    "blankNotice": True,
    "hintMentionsTemplates": False,
}


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _opening_tag(html: str, element_id: str) -> str:
    match = re.search(rf'<[a-z0-9]+\s[^>]*\bid="{re.escape(element_id)}"[^>]*>', html)
    assert match is not None, element_id
    return match.group(0)


@pytest.mark.requires_node
def test_posting_improvement_flow_runs_locally() -> None:
    completed = subprocess.run(
        ["node", "tests/web_posting_improvement_runner.cjs"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    result = json.loads(completed.stdout)
    assert result["usingTemplateFile"] is (ROOT / "web" / "posting-templates.js").is_file()

    assert result["bookEntry"] == {
        "shown": True,
        "mode": "easy",
        "storedModeKept": True,
        "modeWrites": 0,
        "plainHidden": True,
        "plainMode": "expert",
        "otherEntryHidden": True,
        "noLocationHidden": True,
        "noLocationMode": "easy",
        "blockedSearchHidden": True,
        "blockedSearchMode": "expert",
    }

    templates = result["templates"]
    assert templates["missingWithTemplate"] >= 1
    assert templates["linkedQuestionCardsChecked"] >= 1
    assert templates["easyIdCount"] > 0
    assert templates["expertIdCount"] > 0
    stale = templates["staleAfterInsert"]
    assert stale["copyDisabled"] is True
    assert stale["recordDisabled"] is True
    assert stale["undoShown"] is True
    assert stale["undoUsable"] is True
    assert "공고문이 바뀌었습니다" in stale["status"]
    assert templates["staleImprovement"] == {
        "hidden": True,
        "copyDisabled": True,
        "undoUsable": False,
        "copyUnchanged": True,
    }
    assert templates["postingCopy"]["equalsPosting"] is True
    if templates["blanksAfterInsert"]:
        assert "채우지 않은 빈칸" in templates["postingCopy"]["toast"]
        # Blank locations are reported per posting line, not per slot.
        assert templates["blankLines"].endswith("번째 줄") or "줄 외" in templates["blankLines"]
    undo = templates["undoBlocked"]
    assert undo["shown"] is True
    assert undo["ariaDisabled"] == "true"
    assert undo["valueKept"] is True
    assert "직접 고쳐서 되돌릴 수 없습니다" in undo["title"]
    assert templates["cleared"] == {
        "undoHidden": True,
        "improvementHidden": True,
        "noticeHidden": True,
        "postingCopyDisabled": True,
    }
    assert templates["memoHasChanges"] is True
    assert templates["rewriteMemoHasChanges"] is True

    assert result["components"] == {
        "noticeTimeButton": True,
        "noticeAudienceButton": True,
        "posts": 0,
    }
    assert set(result["noTemplates"]) == {
        "missing",
        "wrongSchema",
        "arraySlots",
        "noPlaceholder",
        "nonStringText",
        "stringBundle",
    }
    for label, state in result["noTemplates"].items():
        assert state == NO_TEMPLATE_STATE, label

    privacy = result["privacy"]
    assert privacy["posts"] == 0
    assert privacy["postingStored"] is False
    assert set(privacy["storageKeys"]) <= {
        "fairpost.view-mode.v1",
        "fairpost.role-review.v2",
    }


def test_index_loads_templates_after_data_and_before_app() -> None:
    html = _read("web/index.html")
    scripts = re.findall(r'<script src="([^"]+)"></script>', html)
    assert scripts == ["data.js", "posting-templates.js", "engine.js", "app.js"]
    assert html.count("Content-Security-Policy") == 1
    assert f'content="{CSP}"' in html

    for element_id in (
        "book-entry",
        "posting-undo",
        "placeholder-notice",
        "improvement-panel",
        "improvement-status",
        "improvement-content",
        "improvement-remaining",
        "copy-posting-button",
    ):
        assert html.count(f'id="{element_id}"') == 1, element_id
    for element_id in ("book-entry", "posting-undo", "placeholder-notice", "improvement-panel"):
        assert re.search(r"\shidden(?=[\s>])", _opening_tag(html, element_id)), element_id
    assert "disabled" in _opening_tag(html, "copy-posting-button")
    assert "aria-label=" in _opening_tag(html, "posting-undo")

    editor_actions = html[html.index('class="editor-actions"'):html.index('id="sample-button"')]
    assert 'id="posting-undo"' in editor_actions
    assert html.index('id="comparison-panel"') < html.index('id="improvement-panel"')
    assert html.index('class="review-boundary"') < html.index('id="placeholder-notice"')
    assert html.index('id="placeholder-notice"') < html.index('id="easy-result"')

    book = html[html.index('id="book-entry"'):html.index("</section>", html.index('id="book-entry"'))]
    for phrase in (
        "‘채용 공고문 공정성 검토 MCP’",
        "MCP 연결이나 설치 없이",
        "공고문을 서버로 보내지 않습니다",
        "점수나 판정 대신",
        "공고 붙여 넣기",
        "검토 메모 만들기",
        "고쳐서 다시 검토",
    ):
        assert phrase in book, phrase
    improvement = html[html.index('id="improvement-panel"'):]
    improvement = improvement[:improvement.index("</section>")]
    assert "고친 공고문 복사" in improvement
    assert "표시가 사라져도 검토 완료를 뜻하지 않으며" in improvement
    for judgement in ("개선 완료", "통과", "점수"):
        assert judgement not in improvement


def test_posting_improvement_code_keeps_local_boundaries() -> None:
    app = _read("web/app.js")
    # Storage writes stay the role-review store and the view-mode preference.
    assert app.count(".setItem(") == 2
    assert "sessionStorage" not in app
    assert "indexedDB" not in app
    # Network stays the availability GET and the explicit assisted-review POST.
    assert app.count("fetch(") == 2
    assert 'new URLSearchParams(search).get("entry") === "book"' in app
    assert 'setViewMode(bookEntry ? "easy" : storedViewMode() || "easy", false)' in app
    assert 'raw.schema_version !== "fairpost-posting-templates-v1"' in app
    assert "window.FAIRPOST_POSTING_TEMPLATES" in app
    assert 'input.dispatchEvent(new Event("input"))' in app
    assert "개선 완료" not in app


def test_posting_improvement_styles_wrap_and_stay_readable() -> None:
    css = _read("web/styles.css")
    marker = "/* Posting improvement:"
    assert marker in css
    section = css[css.index(marker):]
    sizes = [int(size) for size in re.findall(r"font-size:\s*(\d+)px", section)]
    assert sizes and min(sizes) >= 12
    compact = "".join(section.split())
    assert ".book-entry{grid-column:1/-1;" in compact
    assert ".template-actions{display:flex;flex-wrap:wrap;" in compact
    assert ".template-actions.button{max-width:100%;white-space:normal;" in compact
    assert "overflow-wrap:anywhere;" in compact
    assert ".button[aria-disabled=\"true\"]{" in compact
    assert "font-size:16px;" in compact[compact.index(".rewrite-contenttextarea{"):]
