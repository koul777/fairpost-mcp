"""Exclusion terms read the posting itself, not a copy cut at the window.

Synthetic sentences only (docs/exclusion-window-2026-10-23.md). Until
2026-10-23 is_excluded ran each exclusion term on text[left:right], so ``$``
held at the window end, a lookahead missed a refusal just past it, and ``^``
and lookbehinds took the window start for the start of the posting. A term
match must still start and end inside the window; its assertions now read the
text around it.

Every case also runs through the browser engine and must match Python exactly.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from core import FairpostEngine
from core.morph import EXCLUDE_LOOKAHEAD, find_matches, is_excluded
from web_engine import web_check_batch


ROOT = Path(__file__).resolve().parents[1]
JS_PROBE = ROOT / "tests" / "js_exclusion_window_2026_10_23.cjs"


def _sliced(text: str, start: int, end: int, exclusion: dict) -> bool:
    """The check before 2026-10-23: the term ran on a copy cut at the window."""
    window = int(exclusion.get("window", 0))
    left = max(0, start - window)
    right = min(len(text), end + window)
    matches = find_matches(text[left:right], [str(exclusion["term"])])
    if exclusion.get("overlap_candidate"):
        return any(m.start() < end - left and m.end() > start - left for m in matches)
    return bool(matches)


def _case(text: str, candidate: str, exclusion: dict, occurrence: int = 0) -> dict:
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(candidate, start + 1)
    return {"text": text, "start": start, "end": start + len(candidate), "excludes": [exclusion]}


LINE_END = {"term": "re:우대(?=[ \\t]*(?:\\r?\\n|$))", "window": 10}
NOT_PLANNED = {"term": "re:삭제(?![ \\t]*예정)", "window": 6}
NO_DIGIT_BEFORE = {"term": "re:(?<!\\d)5세", "window": 0}
AT_START = {"term": "re:^지원", "window": 0}
GREEDY = {"term": "re:남성[^\\n]{0,20}지원", "window": 6}
GREEDY_NOT_REFUSED = {"term": "re:남성[^\\n]{0,30}지원(?![ \\t]*불가)", "window": 6}
OVERLAP = {"term": "re:남성만(?= 지원)", "window": 20, "overlap_candidate": True}
CLOSING = {"term": "re:남성만(?=[ \\t]*\\))", "window": 5}

# name: (case, excluded now, excluded when the term ran on the cut copy)
UNIT_CASES = {
    # `$` used to hold at the window end although the line goes on.
    "dollar-at-window-end": (_case("우대사항\n고령자 우대" + " " * 30 + "대상 아님", "고령자", LINE_END), False, True),
    "dollar-at-text-end": (_case("우대사항\n고령자 우대" + " " * 30, "고령자", LINE_END), True, True),
    # A negative lookahead used to miss the refusal past the window end.
    "lookahead-past-window": (_case("연령 제한 삭제" + " " * 40 + "예정", "연령 제한", NOT_PLANNED), False, True),
    "lookahead-far-past-window": (
        _case("연령 제한 삭제" + " " * (EXCLUDE_LOOKAHEAD + 500) + "예정", "연령 제한", NOT_PLANNED), False, True),
    "lookahead-nothing-follows": (_case("연령 제한 삭제" + " " * 40 + "완료", "연령 제한", NOT_PLANNED), True, True),
    # A lookbehind used to see nothing before the window start.
    "lookbehind-at-window-start": (_case("만 115세 이상", "5세", NO_DIGIT_BEFORE), False, True),
    "lookbehind-at-text-start": (_case("만 5세 이상", "5세", NO_DIGIT_BEFORE), True, True),
    # `^` used to hold at the window start.
    "caret-at-window-start": (_case("자격 지원", "지원", AT_START), False, True),
    "caret-at-text-start": (_case("지원 자격", "지원", AT_START), True, True),
    # The match itself still has to lie inside the window. A greedy term
    # whose first match runs past it is tried again with an end inside.
    "greedy-match-fits-again": (_case("남성 지원 가능 및 지원", "남성", GREEDY), True, True),
    "match-outside-window": (_case("남성은 무관하며 지원", "남성", {**GREEDY, "window": 3}), False, False),
    # The retried match keeps its lookahead past the window.
    "greedy-retry-sees-refusal": (
        _case("남성 지원" + " " * 10 + "불가, 지원", "남성", GREEDY_NOT_REFUSED), False, True),
    "overlap-required": (_case("남성만 지원 남성만", "남성만", OVERLAP, occurrence=1), False, False),
    "overlap-found": (_case("남성만 지원 남성만", "남성만", OVERLAP), True, True),
    # Views: regex terms read the normalized text, offsets count source characters.
    "zero-width-past-window": (_case("연령 제한 삭제" + " " * 40 + "예​정", "연령 제한", NOT_PLANNED), False, True),
    "astral-before-window": (_case("😀😀 연령 제한 삭제" + " " * 40 + "예정", "연령 제한", NOT_PLANNED), False, True),
    "fullwidth-past-window": (_case("연령 제한 삭제" + " " * 40 + "예정（안）", "연령 제한", NOT_PLANNED), False, True),
    # A lookahead that has to succeed is searched only EXCLUDE_LOOKAHEAD
    # characters past the window (the documented limit).
    "positive-lookahead-within-limit": (_case("병역(남성만" + " " * 300 + ")", "남성만", CLOSING), True, False),
    "positive-lookahead-past-limit": (
        _case("병역(남성만" + " " * (EXCLUDE_LOOKAHEAD + 300) + ")", "남성만", CLOSING), False, False),
}


@pytest.mark.parametrize("case, expected, sliced", UNIT_CASES.values(), ids=UNIT_CASES.keys())
def test_terms_read_the_text_around_the_window(case: dict, expected: bool, sliced: bool) -> None:
    exclusion = case["excludes"][0]
    assert is_excluded(case["text"], case["start"], case["end"], case["excludes"]) is expected
    assert _sliced(case["text"], case["start"], case["end"], exclusion) is sliced


@pytest.mark.requires_node
def test_web_exclusion_check_matches_python(tmp_path: Path) -> None:
    cases = [case for case, _, _ in UNIT_CASES.values()]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    done = subprocess.run(
        ["node", str(JS_PROBE), str(path)], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", check=True, timeout=60,
    )
    web = json.loads(done.stdout)["results"]
    assert web == [is_excluded(c["text"], c["start"], c["end"], c["excludes"]) for c in cases]
    assert web == [expected for _, expected, _ in UNIT_CASES.values()]


# --- rules of data/ ---------------------------------------------------------------

BLANKS = (150, 300, 1000, 5000)
# (text, rule, fires): real restrictions a blank-padded table cell away from
# the refusal used to vanish once the refusal sat past the 120-character window.
ENGINE_CASES: dict[str, tuple[str, str, bool]] = {}
for n in BLANKS:
    ENGINE_CASES[f"military-paren-women-refused-{n}"] = (
        "제출서류\n병역(남성만)" + " " * n + "여성 지원 불가", "SEX-001", True)
    ENGINE_CASES[f"older-band-not-preferred-{n}"] = (
        "우대사항\n고령자(55세 이상) 우대" + " " * n + "대상 아님", "AGE-002", True)
    ENGINE_CASES[f"age-limit-removal-planned-{n}"] = ("지원자격\n연령제한 삭제" + " " * n + "예정", "AGE-002", True)
    ENGINE_CASES[f"family-no-penalty-{n}"] = ("유의사항\n부모 직업 기재 시 불이익" + " " * n + "없음", "FAMILY-001", True)
    # The older-worker exclusion requires the line to end after the
    # preference. A line that goes on after a blank run is not one that ends.
    ENGINE_CASES[f"older-band-line-goes-on-{n}"] = (
        "우대사항\n고령자(55세 이상) 우대" + " " * n + "우대사항", "AGE-002", True)
# Blanks inside the excluded wording itself (2026-10-20: 66 ladder inputs).
for n in (117, 118):
    ENGINE_CASES[f"military-blanks-inside-women-refused-{n}"] = (
        "제출서류\n병역사항(남성만" + " " * n + "해당)\n여성 지원 불가", "SEX-001", True)
    ENGINE_CASES[f"age-limit-blanks-inside-planned-{n}"] = ("지원자격\n연령제한" + " " * n + "삭제 예정", "AGE-002", True)
# The fixed false findings stay hidden, now also with blanks inside the
# military-record note, up to EXCLUDE_LOOKAHEAD characters past the window.
ENGINE_CASES.update({
    "fixed-military-record-field": ("제출서류\n병역사항(남성만 해당) 증빙 1부", "SEX-001", False),
    "fixed-older-worker-band": ("우대사항\n고령자(55세 이상) 우대", "AGE-002", False),
    "fixed-age-limit-removed": ("응시자격\n※ 수정 내용: 응시자격 중 연령제한 삭제", "AGE-002", False),
    "fixed-family-penalty": ("유의사항\n출신학교, 부모 직업, 가족관계 등을 기재해 제출하면 감점", "FAMILY-001", False),
    "fixed-military-blanks-inside-300": ("제출서류\n병역사항(남성만" + " " * 300 + "해당) 증빙 1부", "SEX-001", False),
    "military-blanks-inside-past-limit": (
        "제출서류\n병역사항(남성만" + " " * (EXCLUDE_LOOKAHEAD + 300) + "해당) 증빙 1부", "SEX-001", True),
})


@pytest.mark.parametrize("text, rule_id, fires", ENGINE_CASES.values(), ids=ENGINE_CASES.keys())
def test_rules_see_refusals_past_the_window(text: str, rule_id: str, fires: bool) -> None:
    found = {finding["id"] for finding in FairpostEngine().check(text).to_dict()["findings"]}
    assert (rule_id in found) is fires


@pytest.mark.requires_node
def test_python_and_web_agree_on_engine_cases(tmp_path: Path) -> None:
    texts = [text for text, _, _ in ENGINE_CASES.values()]
    texts += [text.replace("\n", "\r\n") for text in texts]
    web_results = web_check_batch(texts, tmp_path)
    engine = FairpostEngine()
    for text, web in zip(texts, web_results, strict=True):
        assert web == engine.check(text).to_dict()


# Many candidates in front of one long blank run, and refusals after 20,000
# blanks: the assertions of each kept match now read past its window, across
# the whole run.
PACKED = {
    "age-limit-removal": "연령 제한 삭제 " * 400 + " " * 100_000 + "예정",
    "military-record": "병역사항(남성만 " * 400 + " " * 100_000 + "해당) 여성 지원 불가",
    "family-penalty": "부모 직업 기재 시 감점 " * 400 + " " * 100_000 + "없음",
    "refusal-after-20000-blanks": "제출서류\n병역(남성만)" + " " * 20_000 + "여성 지원 불가",
    "older-band-after-20000-blanks": "우대사항\n고령자(55세 이상) 우대" + " " * 20_000 + "대상 아님",
    "packed-then-tail": " " * 100_000 + "면접 후 급여 협의 " * 200 + "\n",
}


def test_long_blank_runs_after_many_candidates_finish_quickly() -> None:
    # A process-level bound with a wide margin: about 2 s on a busy PC,
    # interpreter start and rule loading included.
    script = (
        "import json, sys\n"
        "from core import FairpostEngine\n"
        "engine = FairpostEngine()\n"
        "for text in json.loads(sys.stdin.read()):\n"
        "    engine.check(text)\n"
    )
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        input=json.dumps(list(PACKED.values())),
        text=True,
        encoding="utf-8",
        check=True,
        timeout=20,
        capture_output=True,
    )


@pytest.mark.requires_node
def test_web_long_blank_runs_after_many_candidates(tmp_path: Path) -> None:
    texts = list(PACKED.values())
    web_results = web_check_batch(texts, tmp_path)
    engine = FairpostEngine()
    for text, web in zip(texts, web_results, strict=True):
        assert web == engine.check(text).to_dict()
