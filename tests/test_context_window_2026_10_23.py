"""Context-group patterns read the posting itself, not a copy cut at the window.

Synthetic sentences only. Until engine-v7 _matches_context_groups ran each
group's patterns on source[left:right], so ``$`` held at the window end and a
negative lookahead missed the "무관"/"해당 없음" wording just past it, as
is_excluded did before engine-v6 (docs/exclusion-window-2026-10-23.md). A
pattern match must still start and end inside the window.

Every case also runs through the browser engine and must match Python exactly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import FairpostEngine
from core.morph import EXCLUDE_LOOKAHEAD, find_first, find_matches, normalize
from web_engine import web_check_batch


RULE_ID = "Q-INFO-012"
GROUP = "requested_information"
# The candidate line of Q-INFO-012, with an application-form mention in front.
PREFIX = "입사지원서 양식 안내\n근무분야\n일반직, 연구직 채용"
FILLER = "지원 안내 문구를 확인하세요. " * 50


def _posting(term: str, after: str, gap: int = 0) -> str:
    """PREFIX, then ``term`` ending ``gap`` characters before the 600-character
    window of the requested-information group closes, then ``after``."""
    return PREFIX + "\n" + FILLER[: 600 - len(term) - 1 - gap] + term + after


def _fires(text: str) -> bool:
    return RULE_ID in {q["id"] for q in FairpostEngine().check(text).to_dict()["questions"]}


def _sliced(text: str) -> bool:
    """The group test before engine-v7: patterns ran on the cut copy."""
    trigger = next(r for r in FairpostEngine().ruleset.rules if r["id"] == RULE_ID)["trigger"]
    source = normalize(text)
    candidate = find_matches(source, trigger["patterns"])[0]
    group = trigger["context_groups"][GROUP]
    window = int(group["window"])
    context = source[max(0, candidate.start() - window) : candidate.end() + window]
    return find_first(context, group["patterns"]) is not None


# name: (posting, fires now, the group matched on the cut copy)
CASES = {
    # "학력사항" ends at the window end and ": 무관" follows past it: the
    # posting does not ask for education, but `$` used to hold at the cut.
    "not-required-past-window": (_posting("학력사항", ": 무관\n"), False, True),
    "not-required-one-before-end": (_posting("학력사항", ": 무관\n", gap=1), False, True),
    # The cut used to fall right after "무관"/"해당 없음"/"불필요" although
    # the line goes on, so the wording looked like a whole "not required" line.
    "line-goes-on-after-cut": (_posting("최종학력 무관", "한 직무입니다\n"), True, False),
    "major-line-goes-on": (_posting("전공명 해당 없음", "인 경우 비워 두세요\n"), True, False),
    "certificate-line-goes-on": (_posting("졸업증명서 불필요", "한 서류 목록\n"), True, False),
    # Unchanged: a plain request inside the window, and one that runs past it.
    "plain-request-inside": (_posting("학력사항", "\n"), True, True),
    "request-crosses-window-end": (_posting("학력사항", "\n", gap=-1), False, False),
    # Known limit: the search for a start reads EXCLUDE_LOOKAHEAD characters past
    # the window, so "무관" ending at that cut looks like a whole "무관" line
    # although the line goes on ("한 직무입니다"). One blank fewer or more and
    # the real line is seen.
    "line-goes-on-past-lookahead-limit": (
        _posting("학력사항", " " * (EXCLUDE_LOOKAHEAD - 3) + "무관 한 직무입니다\n"), False, True),
    "line-goes-on-before-lookahead-limit": (
        _posting("학력사항", " " * (EXCLUDE_LOOKAHEAD - 4) + "무관 한 직무입니다\n"), True, True),
}


@pytest.mark.parametrize("text, fires, sliced", CASES.values(), ids=CASES.keys())
def test_context_patterns_read_the_text_around_the_window(text: str, fires: bool, sliced: bool) -> None:
    assert _sliced(text) is sliced
    assert _fires(text) is fires


@pytest.mark.requires_node
def test_python_and_web_agree_on_context_cases(tmp_path: Path) -> None:
    texts = [text for text, _, _ in CASES.values()]
    texts += [text.replace("\n", "\r\n") for text in texts]
    web_results = web_check_batch(texts, tmp_path)
    engine = FairpostEngine()
    for text, web in zip(texts, web_results, strict=True):
        assert web == engine.check(text).to_dict()
