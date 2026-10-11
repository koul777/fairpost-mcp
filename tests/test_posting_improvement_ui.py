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
    # Locked but explained: aria-disabled (still focusable), described by the
    # banner under the results toolbar, which offers the re-review button.
    assert stale["copyDisabled"] is True
    assert stale["copyAriaDisabled"] == "true"
    assert stale["copyNativelyDisabled"] is False
    assert stale["copyDescribedBy"] == "stale-banner-text"
    assert stale["bannerText"].startswith("공고문이 바뀌었습니다")
    assert stale["bannerBoxed"] is True
    assert stale["recheckShown"] is True
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
    # "×" resets every improvement surface but keeps the cleared posting one
    # click away.
    assert templates["cleared"] == {
        "undoHidden": False,
        "undoLabel": "방금 지운 공고문 되돌리기",
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

    # Accessibility regressions found by the real-browser audit (axe, keyboard,
    # screen-reader status text); the runner asserts the behavior itself.
    a11y = result["a11y"]
    assert a11y["reviewAnnouncement"].startswith("검토 메모를 새로 만들었습니다. 표현 검토 후보 ")
    assert not re.search(r"완료|통과|점수|합격|불합격", a11y["reviewAnnouncement"])
    assert a11y["labelledControlsChecked"] >= 3
    assert a11y["staleStatusWrites"] == ["comparison-status", "human-review-status"]
    assert a11y["baselineResetFocus"] == "comparison-title"
    for key in ("toastFirst", "scrollIntoView", "legacyCopyFocus", "toastCleared"):
        assert a11y[key] is True, key
    assert a11y["jumpBar"] == {"expertShown": True, "easyHiddenWithoutSummary": True}

    # Cursor inserts keep examples on their own lines (also after a script-set
    # selection and for a caret inside a line); line-based paths use the same
    # boundaries as the engine; the easy headline reports blanks without a verdict.
    follow_up = result["followUp"]
    assert follow_up["cursorInsert"] == {
        "secondInsertBelowFirst": True,
        "midLineKeptWhole": True,
        "undoRestoresCaret": True,
        "boundariesChecked": 8,
    }
    assert follow_up["lineBoundaries"]["checked"] == [
        "form feed",
        "lone CR",
        "CRLF",
        "U+2028",
        "U+2029",
        "vertical tab",
        "U+001C",
        "U+0085",
    ]
    headlines = follow_up["headline"]
    for text in headlines.values():
        assert not re.search(r"발견되지 않|모두 찾|완료|통과|점수|합격|불합격", text), text
    assert re.search(r"채우지 않은 빈칸\(○○\) <strong>\d+곳</strong>이 남아 있습니다\.$", headlines["withBlanks"])
    assert re.search(r"채우지 않은 빈칸\(○○\) <strong>2곳</strong>이 남아 있습니다\.$", headlines["allFoundWithBlanks"])
    assert headlines["allFoundClean"].endswith("내용의 충분성과 실제 운영은 별도 확인이 필요합니다.")
    assert "채우지 않은 빈칸" not in headlines["allFoundClean"]

    privacy = result["privacy"]
    assert privacy["posts"] == 0
    assert privacy["postingStored"] is False
    assert set(privacy["storageKeys"]) <= {
        "fairpost.view-mode.v1",
        "fairpost.role-review.v2",
    }

    # Web UX follow-up; the runner asserts the details, this pins the scenarios.
    ux = result["ux"]
    assert ux["discard"] == {
        "declinedKeepsPosting": True,
        "sampleUndoRestores": True,
        "typedTextNeverOverwritten": True,
        "manualEmptyingIsStale": True,
        "questionsAsked": 3,
    }
    assert ux["loadGuard"] == {
        "dataMissing": True,
        "engineMissing": True,
        "bothMissing": True,
        "engineThrows": True,
        "renderThrows": True,
        "busyPaint": True,
        "softSizeHint": True,
        "reloadButton": 1,
    }
    assert ux["staleExplained"] == {"copy": True, "rewrite": True, "recheckButton": True}
    assert ux["toastDelays"]["shortDelay"] == 4000
    assert 4000 < ux["toastDelays"]["longDelay"] <= 12000
    assert ux["bookEntry"] == {
        "phoneFolded": True,
        "desktopOpen": True,
        "compactAfterCheck": True,
    }
    assert ux["easyCore"]["contextLines"] >= 1
    assert ux["easyCore"]["openAtSix"] is True and ux["easyCore"]["foldedAtSeven"] is True
    assert ux["landmarks"] is True


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


def test_improvement_flow_html_is_navigable_and_announced() -> None:
    html = _read("web/index.html")
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids)), "duplicate ids"

    # A polite status outside every hidden container carries the review outcome.
    live = _opening_tag(html, "review-live")
    assert 'role="status"' in live and 'aria-live="polite"' in live
    assert "visually-hidden" in live
    assert html.index('id="review-live"') > html.index("</main>")
    assert "hidden" not in re.findall(r'\s(hidden)(?=[\s>])', live)

    # Headings that take focus after a button disables itself or hides.
    for element_id in ("comparison-title", "improvement-title", "assisted-review-heading", "results-title"):
        assert 'tabindex="-1"' in _opening_tag(html, element_id), element_id

    # The jump bar is a labelled navigation whose targets all exist.
    nav = html[html.index('<nav id="result-jump"'):]
    nav = nav[:nav.index("</nav>")]
    assert 'aria-label="검토 결과 안에서 이동"' in nav
    assert re.search(r"\shidden(?=[\s>])", _opening_tag(html, "result-jump"))
    assert re.search(r"\shidden(?=[\s>])", _opening_tag(html, "improvement-jump"))
    targets = re.findall(r'href="#([^"]+)"', nav)
    assert targets == [
        "findings-heading", "slots-heading", "questions-heading",
        "human-review-heading", "improvement-title",
    ]
    for target in targets:
        assert f'id="{target}"' in html, target
    # Easy mode shows only the change-summary link; expert sections are advanced-only.
    assert nav.count('class="advanced-only"') == 4
    assert html.index('id="placeholder-notice"') < html.index('id="result-jump"') < html.index('id="easy-result"')

    # Label in name (WCAG 2.5.3): the visible words are inside the accessible name.
    undo = _opening_tag(html, "posting-undo")
    assert "되돌리기" in re.search(r'aria-label="([^"]*)"', undo).group(1)
    for tag, name, visible in re.findall(
        r'<(button|summary)\b[^>]*\baria-label="([^"]*)"[^>]*>\s*([^<]*?)\s*<', html
    ):
        if visible and not set(visible) <= set("×⧉"):
            assert visible.replace(" ", "") in name.replace(" ", ""), (tag, name, visible)


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
    # The review outcome is announced as counts of work items, never a verdict.
    announcement = app[app.index("function reviewAnnouncement("):]
    announcement = announcement[:announcement.index("\n  }\n")]
    assert not re.search(r"완료|통과|점수|합격", announcement)
    # Rewrite summary: accessible name starts with the visible words.
    assert 'aria-label="이 줄 고쳐 쓰기, ${number}번 표현이 있는 줄">이 줄 고쳐 쓰기</summary>' in app
    # Posting lines are split at the engine's boundaries (str.splitlines), not
    # at "\n" alone: rewrite blocks, blank-line numbers, the change summary and
    # the cursor insert all go through the shared helpers.
    assert 'const LINE_BREAK_PATTERN = /\\r\\n|[\\n\\r\\v\\f\\x1c\\x1d\\x1e\\x85\\u2028\\u2029]/u;' in app
    for name in ("findingLineRange", "placeholderLinesLabel", "postingLineDiff", "insertTemplate"):
        body = app[app.index(f"function {name}("):]
        body = body[: body.index("\n  }\n")]
        assert not re.search(r'\.split\("\\n"\)|indexOf\("\\n"|lastIndexOf\("\\n"|endsWith\("\\n"\)|startsWith\("\\n"\)|split\(/\\r\?\\n/', body), name
    # Script-set selections are not the reader's caret.
    assert "programSelection" in app


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

    # Focus visibility: rings of summaries stay inside the clipping pane or card,
    # and keyboard focus scrolling keeps controls clear of the fixed toast.
    flat = "".join(css.split())
    assert "summary:focus-visible{outline-offset:-3px;}" in flat
    assert "html{scroll-padding-bottom:" in flat
    for heading in ("#comparison-title", "#improvement-title", "#assisted-review-heading"):
        assert f"{heading}:focus-visible" in css, heading
    assert ".result-jump{margin:" in compact


def _rule(css: str, selector: str) -> str:
    """The declarations of every rule whose selector list is exactly `selector`."""
    compact = "".join(re.sub(r"/\*.*?\*/", "", css, flags=re.S).split())
    matches = re.findall(rf"(?:^|[{{}}]){re.escape(''.join(selector.split()))}\{{([^}}]*)\}}", compact)
    assert matches, selector
    return "".join(matches)


def test_web_ux_followup_html_explains_locks_and_load_failures() -> None:
    html = _read("web/index.html")

    # WEB-3: a status banner under the results toolbar, wired to the re-review.
    assert html.index('id="results-title"') < html.index('id="stale-banner"') < html.index('id="empty-state"')
    banner = _opening_tag(html, "stale-banner-text")
    assert 'role="status"' in banner
    assert "hidden" not in re.findall(r"\s(hidden)(?=[\s>])", banner), "a live region must exist before it is filled"
    assert re.search(r"\shidden(?=[\s>])", _opening_tag(html, "stale-recheck"))
    assert re.search(r">\s*다시 검토\s*</button>", html[html.index('id="stale-recheck"'):])
    assert "disabled" in _opening_tag(html, "copy-button")

    # WEB-2: a noscript message, a loading notice that only shows late, and a
    # hidden alert the script fills when a file did not load.
    notices = html[html.index('class="load-notices"'):html.index("<main")]
    assert "<noscript>" in notices and "JavaScript" in notices
    assert "boot-notice" in notices
    assert 'role="alert"' in _opening_tag(html, "load-error")
    assert re.search(r"\shidden(?=[\s>])", _opening_tag(html, "load-error"))
    assert "id=\"load-error-reload\"" in html
    assert html.index('class="load-notices"') < html.index("<main")
    assert re.search(r"\shidden(?=[\s>])", _opening_tag(html, "posting-size-hint"))
    assert "posting-size-hint" in _opening_tag(html, "posting-input")

    # WEB-4: two ways to begin sit above the folded introduction.
    book = html[html.index('id="book-entry"'):html.index("</section>", html.index('id="book-entry"'))]
    assert book.index("바로 입력하기") < book.index('id="book-entry-more"')
    assert book.index("예시로 먼저 해보기") < book.index('id="book-entry-more"')
    assert re.search(r"<details id=\"book-entry-more\"[^>]*\sopen", book)
    assert "<summary>" in book[book.index('id="book-entry-more"'):]

    # WEB-5 / WEB-6 / WEB-8 copy.
    assert "fingerprint" not in html
    assert "공고문 요약값(해시)" in html
    assert 'id="role-review-clear-all"' in html
    assert "이 브라우저의 모든 역할 기록 삭제" in html
    assert "현재 결과를 새 비교 기준으로 삼기" in html
    assert "현재 결과를 새 기준으로<" not in html


def test_web_ux_followup_app_keeps_boundaries_and_plain_korean() -> None:
    app = _read("web/app.js")
    # Readers never see an English technical term for the stored packet's key.
    shown = [
        line for line in app.splitlines()
        if "fingerprint" in line.lower() and re.search("[가-힣]", line)
    ]
    assert shown == []
    # One deadline per request, both requests carry it, and the timeout is Korean.
    assert "const ASSISTED_REQUEST_TIMEOUT_MS = 60000;" in app
    assert app.count("signal: deadline.signal") == 2
    assert "60초 안에 오지 않아" in app or "초 안에 오지 않아 요청을 멈췄습니다" in app
    # The server's own reason text never reaches the page.
    assert "capability.reason" not in app
    assert "result.reason ||" not in app
    # Answers stay in memory; only the two documented storage writes exist, and
    # "모든 역할 기록 삭제" only removes.
    assert app.count(".setItem(") == 2
    assert app.count(".removeItem(") >= 3
    # The stale-result locks are explained, not silent, and never act.
    assert 'copyButton.setAttribute("aria-disabled", "true")' in app
    assert "copyButton.disabled = stale" not in app
    assert 'setAttribute("aria-describedby", "stale-banner-text")' in app
    # An emptied field is stale, not a reset: only the × handler resets.
    handler = app[app.index('input.addEventListener("input"'):]
    handler = handler[:handler.index("\n  });")]
    assert "resetReview()" not in handler
    # Example sentences and the posting view are groups, not landmark regions.
    assert 'role="region"' not in app


def test_web_ux_followup_styles_cover_phones_toast_and_touch_targets() -> None:
    css = _read("web/styles.css")
    compact = "".join(css.split())

    # WEB-4: the toast is pinned to both edges instead of shrinking to half.
    toast = _rule(css, ".toast")
    assert "left:16px;" in toast and "right:16px;" in toast
    assert "width:max-content;" in toast
    assert "left:50%" not in toast
    assert "translate(-50%" not in compact
    assert "max-width:min(460px,calc(100vw-32px));" in toast

    # 44px touch targets: section chips and the footer disclosure.
    assert "min-height:44px;" in _rule(css, ".review-nav a")
    assert "min-height:44px;" in _rule(css, ".integration-details > summary")
    assert "min-height:44px;" in _rule(css, ".book-entry-more > summary")

    # The introduction folds to its heading after the first review and the
    # two ways to begin stay reachable.
    assert ".book-entry.is-compact.book-entry-actions" in compact
    assert "display:none;" in _rule(css, ".book-entry.is-compact .book-entry-lead,.book-entry.is-compact .book-entry-actions")
    assert (
        ".mode-option,.button,.icon-button,.role-review-panelselect,.role-review-panelinput,"
        ".organization-profileselect{min-height:44px;}"
    ) in compact

    # The stale banner only gets a box while stale; the loading notice is
    # collapsed for its first seconds and revealed by animation, not script.
    assert ".stale-banner:not(.is-stale){display:block;}" in compact
    assert "border:1pxsolidvar(--amber-line)" in _rule(css, ".stale-banner.is-stale")
    boot = _rule(css, ".boot-notice")
    assert "max-height:0;" in boot and "animation:boot-notice-reveal" in boot
    assert "@keyframesboot-notice-reveal" in compact
    assert ".rewrite-contenttextarea[readonly]{" in compact
    assert ".easy-context{" in compact

    # Nothing in the new section is smaller than 12px.
    section = css[css.index("/* Web UX follow-up:"):]
    sizes = [int(size) for size in re.findall(r"font-size:\s*(\d+)px", section)]
    assert sizes and min(sizes) >= 12
