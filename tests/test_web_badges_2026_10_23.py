"""Pins the badge and jump bar follow-ups of 2026-10-23: the number badges of the marks stay in the gap
between text lines (V3, P8), a badge that the field edge would cut in half is hidden (V4, P9), and the
expert jump bar wraps instead of scrolling sideways on phones up to 420px (V5, P7).

The rules are read from web/styles.css and web/app.js; nothing here needs a browser."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")


def _block(css: str, opener: str, start: int = 0) -> str:
    """Body of the first `opener { ... }` at or after `start`, braces balanced."""
    at = css.index(opener, start)
    open_at = css.index("{", at)
    depth = 0
    for index in range(open_at, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[open_at + 1 : index]
    raise AssertionError(f"unbalanced block for {opener}")


def _rule(css: str, selector: str) -> str:
    return _block(css, selector + " {")


def _px(body: str, prop: str) -> float:
    match = re.search(rf"(?:^|[\s;]){re.escape(prop)}:\s*(-?[\d.]+)px;", body)
    assert match, f"{prop} in px not found"
    return float(match.group(1))


def _function(name: str) -> str:
    at = APP.index(f"function {name}(")
    return _block(APP, f"function {name}(", at)


def test_badge_is_small_and_sits_in_the_gap_between_two_text_lines():
    badge = _rule(CSS, ".posting-mark::before")
    top, left = _px(badge, "top"), _px(badge, "left")
    height = _px(badge, "height")
    assert height == 13 and _px(badge, "min-width") == 13
    assert _px(badge, "font-size") >= 12, "digits stay readable"
    # Gap model at the field's own sizes: the text body (ink) is about 0.93 em tall and starts 0.2 em
    # below the top of the mark box (measured: 14px ink and a 3px offset at 15px text).
    field = _rule(CSS, "#posting-input")
    line_height = float(re.search(r"line-height:\s*([\d.]+);", field).group(1))
    for font in (15, 16):
        pitch = font * line_height
        ink, offset = font * 0.93, font * 0.2
        gap = pitch - ink
        previous_ink_bottom = offset - gap  # relative to the top of the mark box
        clearance = top - previous_ink_bottom
        assert clearance >= 1, f"badge top is {clearance:.1f}px below the line above at {font}px text"
        # The badge stays out of the leading dash: it ends before the middle of the line.
        assert top + height < offset + ink * 0.5
    assert left > -8, "the badge no longer reaches over the dash and space before the mark"


def test_badge_colors_keep_their_contrast():
    badge = _rule(CSS, ".posting-mark::before")
    assert "background: var(--yellow);" in badge and "color: #2b2410;" in badge
    assert "visibility: hidden;" in _rule(CSS, ".posting-mark.badge-clipped::before")


def test_badges_cut_by_the_field_edge_are_hidden_instead_of_shown_in_part():
    body = _function("clipMarkBadges")
    assert "badge-clipped" in body and 'getComputedStyle(mark, "::before")' in body
    assert "getClientRects()[0]" in body and "box.bottom" in body and "box.top" in body
    # Every place that moves or rebuilds the layer runs the pass.
    sync = _function("syncPostingHighlights")
    render = _function("renderPostingHighlights")
    assert "clipMarkBadges();" in sync and "clipMarkBadges();" in render
    assert sync.index("translateY") < sync.index("clipMarkBadges();")
    assert render.rindex("translateY") < render.index("clipMarkBadges();")


def test_phone_jump_bar_wraps_up_to_420px_and_stays_one_row_above():
    first = CSS.index("@media (max-width: 600px) {")
    wide = _block(CSS, ".review-nav {", CSS.index("One swipeable row"))
    assert "flex-wrap: nowrap;" in wide and "overflow-x: auto;" in wide
    start = CSS.rindex("@media (max-width: 420px) {")
    assert start > first, "the wrapping rule comes after the swipeable row so it wins"
    phone = _block(CSS, "@media (max-width: 420px) {", start)
    nav = _rule(phone, ".review-nav")
    assert "flex-wrap: wrap;" in nav and "overflow-x: visible;" in nav
    # The taller pinned bar needs more room above the jump targets.
    margin = re.search(r"scroll-margin-top:\s*(\d+)px;", phone)
    assert margin and int(margin.group(1)) >= 130
    for target in ("#findings-heading", "#slots-heading", "#questions-heading", "#human-review-heading", ".question-item"):
        assert target in phone
    # Short screens (a 320px phone, 400 percent zoom) do not pin the bar.
    short = _block(CSS, "@media (max-width: 420px) and (max-height: 600px) {")
    assert "position: static;" in short and "scroll-margin-top: 16px;" in short
