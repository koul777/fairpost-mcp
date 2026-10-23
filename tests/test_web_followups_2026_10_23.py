"""Pins the web follow-ups of 2026-10-23: reduced motion (V6), the arrival mark of a linked
question card (P6), the contrast of the chosen mode tab (P10), the follow-up question dot (P5)
and the two small forced-colours and footer additions found by the widened visual audit.

The rules are read from web/styles.css and web/app.js; nothing here needs a browser."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
DARK_START = CSS.index("@media (prefers-color-scheme: dark) {")


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


def _luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _ratio(first: str, second: str) -> float:
    a, b = _luminance(first), _luminance(second)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def _token(scope: str, name: str) -> str:
    match = re.search(rf"--{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}})\s*;", scope)
    assert match, f"token --{name} not found"
    return match.group(1)


def _last_reduce_block() -> str:
    start = CSS.rindex("@media (prefers-reduced-motion: reduce) {")
    return _block(CSS, "@media (prefers-reduced-motion: reduce) {", start)


def test_reduced_motion_switches_off_every_transition_and_finishes_animations_at_once():
    block = _last_reduce_block()
    assert re.search(r"\*,\s*\*::before,\s*\*::after\s*\{[^}]*transition:\s*none !important;", block)
    assert "animation-duration: 0.01ms !important;" in block
    assert "animation-iteration-count: 1 !important;" in block
    # The delayed boot notice reveals through an animation delay, so the delay is left alone.
    assert "animation-delay" not in block
    assert "animation: boot-notice-reveal 0.01s linear 3s forwards;" in CSS


def test_reduced_motion_leaves_a_static_mark_on_the_card_reached_by_a_linked_question():
    block = _last_reduce_block()
    match = re.search(r"\.is-flash,\s*\.question-item:focus:not\(:focus-visible\)\s*\{([^}]*)\}", block)
    assert match, "static arrival mark missing in the reduced-motion block"
    body = match.group(1)
    assert "outline: 3px solid var(--green);" in body
    assert "outline-offset: 2px;" in body
    # The flash animation itself stays limited to no-preference.
    flash = _block(CSS, "@media (prefers-reduced-motion: no-preference) {", CSS.index(".easy-finding.is-linked,\n.finding-item.is-linked"))
    assert "animation: fairpost-flash" in flash
    # Both colour schemes draw the mark with --green, which has 3:1 or more against the card surface.
    light = CSS[: CSS.index("@media (prefers-color-scheme: dark)")]
    dark = _block(CSS, ":root {", DARK_START)
    assert _ratio(_token(light, "green"), _token(light, "panel")) >= 3
    assert _ratio(_token(dark, "green"), _token(dark, "panel")) >= 3


def test_chosen_mode_tab_is_at_least_three_to_one_against_the_unchosen_tab_in_both_schemes():
    selected = _rule(CSS, '.mode-option[aria-pressed="true"]')
    assert "background: var(--green);" in selected
    assert "color: #ffffff;" in selected
    light = CSS[: CSS.index("@media (prefers-color-scheme: dark)")]
    dark = _block(CSS, ":root {", DARK_START)
    dark_selected = _block(CSS, '.mode-option[aria-pressed="true"] {', DARK_START)
    assert "color: #0e1613;" in dark_selected
    # The unchosen tab has no fill of its own, so it shows the switch background (--soft-2).
    assert "background: var(--soft-2);" in _rule(CSS, ".mode-switch")
    assert "background: transparent;" in _rule(CSS, ".mode-option")
    assert _ratio(_token(light, "green"), _token(light, "soft-2")) >= 3
    assert _ratio(_token(dark, "green"), _token(dark, "soft-2")) >= 3
    # Text on the chosen tab keeps AA.
    assert _ratio("#ffffff", _token(light, "green")) >= 4.5
    assert _ratio("#0e1613", _token(dark, "green")) >= 4.5


def test_follow_up_dot_is_drawn_beside_the_text_so_it_cannot_be_left_alone_on_a_line():
    assert '<span class="follow-up-item">${escapeHtml(item)}</span>' in APP
    assert "`· ${escapeHtml(item)}`" not in APP
    item = _rule(CSS, ".follow-up .follow-up-item")
    assert "display: block;" in item and "position: relative;" in item and "padding-left: 0.9em;" in item
    dot = _rule(CSS, ".follow-up .follow-up-item::before")
    assert 'content: "\\00b7";' in dot and "position: absolute;" in dot


def test_forced_colours_and_footer_additions():
    forced = _block(CSS, "@media (forced-colors: active) {")
    mode = _rule(forced, '.mode-option[aria-pressed="true"]')
    assert "forced-color-adjust: none;" in mode
    assert "background: Highlight;" in mode and "color: HighlightText;" in mode
    assert "border-bottom: 2px solid CanvasText;" in _rule(forced, ".posting-mark")
    footer = _block(CSS, ".app-footer code {", CSS.index("/* The rule-set version in the footer"))
    assert "overflow-wrap: anywhere;" in footer
