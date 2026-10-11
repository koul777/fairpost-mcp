"""Rule and question IDs that the static web app hard-codes must exist.

web/app.js names a few question IDs directly: the slot questions embedded in the
result, the questions shown to the public, and so on. Nothing ties those
strings to data/rules, so deleting or renumbering a question there would leave
the app silently skipping it. The earlier guard was a substring check on one
literal; this one reads every ID the web sources mention.
"""
from __future__ import annotations

from pathlib import Path
import re

import pytest

from core import load_ruleset

ROOT = Path(__file__).resolve().parents[1]
# Hand-written web sources. web/data.js and web/posting-templates.js are
# generated from the ruleset, so they cannot drift from it.
WEB_SOURCES = ("web/app.js", "web/engine.js", "web/index.html")

# An ID looks like Q-INFO-001 (question) or SEX-001 (law finding): upper-case
# words joined by hyphens, ending in three digits.
ID_TOKEN = re.compile(r"(?<![A-Za-z0-9_-])[A-Z][A-Z0-9]*(?:-[A-Z][A-Z0-9]*)*-\d{3}(?![A-Za-z0-9_-])")


@pytest.fixture(scope="module")
def ruleset_ids() -> dict[str, set[str]]:
    rules = load_ruleset().rules
    return {
        "question": {rule["id"] for rule in rules if rule["layer"] == "question"},
        "law": {rule["id"] for rule in rules if rule["layer"] == "law"},
    }


def _tokens(relative: str) -> set[str]:
    return set(ID_TOKEN.findall((ROOT / relative).read_text(encoding="utf-8")))


def test_app_references_a_known_set_of_question_ids() -> None:
    # Guards the scan itself: if app.js stopped naming any question ID the
    # test below would pass without checking anything.
    questions = {token for token in _tokens("web/app.js") if token.startswith("Q-")}
    assert len(questions) >= 3, sorted(questions)


@pytest.mark.parametrize("relative", WEB_SOURCES)
def test_every_question_id_in_the_web_sources_exists(
    relative: str, ruleset_ids: dict[str, set[str]]
) -> None:
    questions = {token for token in _tokens(relative) if token.startswith("Q-")}
    unknown = sorted(questions - ruleset_ids["question"])
    assert not unknown, (
        f"{relative} names question IDs that are not in data/rules/questions.yaml: "
        f"{unknown}"
    )


@pytest.mark.parametrize("relative", WEB_SOURCES)
def test_every_law_rule_id_in_the_web_sources_exists(
    relative: str, ruleset_ids: dict[str, set[str]]
) -> None:
    # Only tokens that share a prefix with a real law rule count as references;
    # "SHA-256" and similar words are not rule IDs.
    prefixes = {rule_id.rsplit("-", 1)[0] for rule_id in ruleset_ids["law"]}
    tokens = {
        token
        for token in _tokens(relative)
        if not token.startswith("Q-") and token.rsplit("-", 1)[0] in prefixes
    }
    unknown = sorted(tokens - ruleset_ids["law"])
    assert not unknown, (
        f"{relative} names law rule IDs that are not in data/rules/law.yaml: {unknown}"
    )


def test_id_scan_ignores_unrelated_words() -> None:
    sample = "SHA-256 UTF-8 Q-INFO-001, (SEX-001) 'Q-DIST-014' ABC-1234 q-info-002"
    assert set(ID_TOKEN.findall(sample)) == {
        "SHA-256", "Q-INFO-001", "SEX-001", "Q-DIST-014",
    }
