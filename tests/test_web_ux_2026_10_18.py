"""Static checks for the 2026-10-18 web UX additions: editor highlights, the
finding stepper, linked questions, file save, dark mode and print."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")


def _function(name: str) -> str:
    start = APP.index(f"function {name}(")
    return APP[start:APP.index("\n  }\n", start)]


def test_highlight_layer_sits_behind_the_posting_field():
    field = HTML[HTML.index('<div class="posting-field">'):HTML.index("</textarea>")]
    layer = re.search(r'<div id="posting-highlights"[^>]*>', field).group(0)
    assert 'aria-hidden="true"' in layer and " hidden" in layer
    assert field.index('id="posting-highlights"') < field.index('id="posting-input"')
    compact = re.sub(r"\s+", "", CSS)
    assert ".posting-field#posting-input{position:relative;z-index:1;background:transparent;}" in compact
    assert "pointer-events:none;" in compact[compact.index(".posting-highlights{"):]


def test_highlights_only_show_for_the_checked_text():
    render = _function("renderPostingHighlights")
    assert "if (!findingRangesFresh())" in render
    assert "postingHighlights.hidden = true" in render
    fresh = _function("findingRangesFresh")
    assert "latestCheckedText === input.value" in fresh
    # Every edit re-evaluates the layer, so stale offsets never stay marked.
    handler = APP[APP.index('input.addEventListener("input", () => {'):APP.index("// Tracks the caret")]
    assert "renderPostingHighlights();" in handler
    # Mark text is escaped like the rest of the result.
    assert "escapeHtml(text.slice(range.start, range.end))" in render


def test_finding_stepper_refuses_stale_offsets():
    stepper = re.search(r'<div id="finding-stepper"[^>]*>', HTML).group(0)
    assert " hidden" in stepper and 'role="group"' in stepper
    step = _function("stepFinding")
    assert "if (!findingRangesFresh())" in step
    assert "showToast(" in step


def test_new_buttons_keep_label_in_name_and_start_disabled():
    download = re.search(r'<button id="download-button"[^>]*>', HTML).group(0)
    assert " disabled" in download and "aria-label" not in download
    for button_id in ("finding-prev", "finding-next", "empty-sample"):
        tag = re.search(rf'<button id="{button_id}"[^>]*>', HTML).group(0)
        assert "aria-label" not in tag


def test_file_save_is_local_and_blocked_while_stale():
    save = _function("downloadReport")
    assert "latestCheckedText !== input.value" in save
    assert "new Blob(" in save and "URL.createObjectURL" in save
    assert "fetch(" not in save and ".setItem(" not in save
    state = _function("updateStaleState")
    assert 'downloadButton.setAttribute("aria-disabled", "true")' in state


def test_linked_questions_come_from_engine_links_and_add_no_ids():
    block = _function("linkedQuestionsBlock")
    assert "question.linked_findings" in block
    assert "data-question-jump" in block and " id=" not in block
    jump = _function("jumpToQuestion")
    assert 'node.tagName === "DETAILS"' in jump


def test_expert_cards_show_lines_not_code_point_offsets():
    findings = _function("renderFindings")
    assert "finding.offset[0]}–${finding.offset[1]}" not in findings
    assert "번째 줄" in findings


def test_dark_mode_follows_the_system_without_storage():
    assert '<meta name="color-scheme" content="light dark">' in HTML
    assert "@media (prefers-color-scheme: dark)" in CSS
    # No stored theme toggle: storage writes stay exactly the two known keys.
    assert APP.count(".setItem(") == 2


def test_motion_respects_reduced_motion_and_print_hides_controls():
    ux = CSS[CSS.index("/* Web UX 2026-10-18:"):]
    for block in re.findall(r"animation:\s*fairpost-[a-z]+", ux):
        assert block
    rise = ux.index("animation: fairpost-rise")
    assert ux.rindex("@media (prefers-reduced-motion: no-preference)", 0, rise) > 0
    printing = ux[ux.index("@media print"):]
    assert ".editor-pane" in printing and ".toolbar-actions" in printing
    sizes = [int(size) for size in re.findall(r"font-size:\s*(\d+)px", ux)]
    assert sizes and min(sizes) >= 12
