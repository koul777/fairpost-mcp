"""Synthetic regressions for the 2026-10-17 real-posting fixes.

Hand-written cases, not human-labelled gold data (docs/detection-fixes-2026-10-17.md).
A run over published public-sector postings showed four false findings and a
whitespace backtracking hot spot:

* AGE-002 on a correction notice that drops the age limit ("연령제한 삭제").
* AGE-002 on the 고령자ㆍ준고령자 bands that the 고령자고용법 itself defines,
  named to prefer those applicants.
* FAMILY-001 on a blind-hiring warning that penalises writing the item in
  ("부모 직업 등을 기재해 제출하면 감점"). The exclusion is anchored at the item;
  false, omitted or otherwise requested entries keep the finding.
* SEX-001 on a document note for men's military records ("주민등록초본(남성만)").
* Rule regexes with two whitespace quantifiers around an optional token (or a
  leading optional whitespace run) backtracked quadratically on long blank runs.

The KEPT cases include the shapes two rounds of independent review found earlier
drafts of these exclusions suppressing: false or omitted entries, refusals of
the older worker bands, preferences that do not name the bands, and applicant
restrictions written next to a military record.

Every case also runs through the browser engine and must match Python exactly.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

import pytest
import yaml

from core import FairpostEngine
from web_engine import web_check_batch


ROOT = Path(__file__).resolve().parents[1]


def _findings(text: str) -> set[str]:
    return {finding["id"] for finding in FairpostEngine().check(text).to_dict()["findings"]}


# (text, finding that must not appear)
FIXED = {
    "age-limit-removed-notice": ("응시자격\n※ 수정 내용: 응시자격 중 연령제한 삭제", "AGE-002"),
    "age-limit-abolished": ("지원자격\n이번 채용부터 연령 제한을 폐지하였습니다.", "AGE-002"),
    "older-worker-band-preferred": (
        "우대사항\n고령자 및 준고령자 우대\n고령자(55세 이상)\n준고령자(50세 이상)", "AGE-002"),
    "older-worker-band-inline": ("우대사항\n고령자(만 55세 이상) 서류전형 가점 부여", "AGE-002"),
    "older-worker-band-range": ("우대사항\n준고령자(50세 이상 55세 미만) 우대", "AGE-002"),
    "older-worker-bands-listed": ("우대사항\n○ 준고령자(50세 이상 55세 미만) 및 고령자(55세 이상) 우대", "AGE-002"),
    "older-worker-bands-named-in-list": (
        "2) 고령자 및 준고령자, 장애인, 저소득층 등 우대\n고령자(55세 이상)\n준고령자(50세 이상)", "AGE-002"),
    "older-worker-band-priority-hiring": ("우대사항\n고령자 및 준고령자 우선 채용\n고령자(55세 이상)", "AGE-002"),
    "family-written-in-penalised": (
        "유의사항\n출신학교, 부모 직업, 가족관계 등을 기재해 제출하면 감점 등 불이익이 있을 수 있습니다.",
        "FAMILY-001"),
    "family-written-in-fails": ("유의사항\n부모 직업을 기재할 경우 서류전형에서 불합격 처리합니다.", "FAMILY-001"),
    "family-written-in-if": ("유의사항\n지원서에 부모의 직업을 기재하면 감점합니다.", "FAMILY-001"),
    "military-record-copy": ("제출서류\n주민등록등본 1부, 주민등록초본(남성만) 1부", "SEX-001"),
    "military-record-copy-note": ("제출서류\n※ 주민등록초본은 남성에 한함(병역사항 기재)", "SEX-001"),
    "military-record-field": ("제출서류\n병역사항(남성만 해당) 증빙 1부", "SEX-001"),
}

# (text, finding that must still appear)
KEPT = {
    "age-limit-removal-planned": ("지원자격\n연령제한 삭제 예정이며 현재는 연령 제한 있음", "AGE-002"),
    "age-limit-not-removed": ("지원자격\n연령 제한은 삭제하지 않습니다.", "AGE-002"),
    "older-worker-band-refused": ("지원자격\n고령자(55세 이상) 지원 불가", "AGE-002"),
    "bare-age-floor": ("지원자격\n만 55세 이상인 자", "AGE-002"),
    "family-false-entry": ("유의사항\n부모 직업을 허위 기재 시 불이익이 있습니다.", "FAMILY-001"),
    "family-wrong-entry": ("유의사항\n부모 직업을 사실과 다르게 기재한 경우 불이익", "FAMILY-001"),
    "family-omission-penalty": ("유의사항\n부모 직업을 기재하지 않으면 감점합니다.", "FAMILY-001"),
    "family-missing-penalty": ("유의사항\n부모 직업 미기재 시 불이익", "FAMILY-001"),
    "family-no-penalty": ("유의사항\n부모 직업 기재 시 불이익 없음", "FAMILY-001"),
    "men-only-with-military-service": ("지원자격\n병역필 남성만 지원 가능", "SEX-001"),
    "men-only-then-record": ("지원자격\n남성만 지원 가능하며 주민등록초본 제출", "SEX-001"),
    # Shapes the first draft suppressed (independent review, 2026-10-17).
    "family-false-entry-adverb": ("유의사항\n부모 직업을 허위로 기재할 경우 불합격 처리됩니다.", "FAMILY-001"),
    "family-not-entered": ("유의사항\n입사지원서 작성 시 부모 직업을 입력하지 않으면 불합격 처리됩니다.", "FAMILY-001"),
    "family-purpose-clause": ("유의사항\n부모 직업 기재 시 불이익이 없도록 정확히 기재 바랍니다.", "FAMILY-001"),
    "family-penalty-on-other-item": ("유의사항\n출신학교 기재 시 감점, 부모 직업은 반드시 기재 바랍니다.", "FAMILY-001"),
    "body-false-entry": ("지원자격\n키 175cm 이상 (거짓 기재 시 불합격)", "BODY-001"),
    "photo-form-boilerplate": ("제출서류\n입사지원서(사진 부착) 작성 시 허위 기재 사실이 발견되면 불합격 처리", "PHOTO-001"),
    "older-band-cannot-apply": ("지원자격\n고령자(55세 이상)는 응시할 수 없습니다.", "AGE-002"),
    "older-band-excluded-list": ("응시결격사유\n지원 제외 대상: 준고령자(50세 이상 55세 미만)", "AGE-002"),
    "older-band-refused-next-line": ("지원자격\n고령자(55세 이상)\n※ 위 해당자는 지원 불가", "AGE-002"),
    "older-band-only": ("지원자격\n준고령자(50세 이상 55세 미만)만 지원 가능", "AGE-002"),
    "older-bands-listed-refused": ("지원자격\n준고령자(50세 이상) 및 고령자(55세 이상)는 지원 불가", "AGE-002"),
    "military-row-men-only": ("지원자격\n병역: 남성만 지원 가능", "SEX-001"),
    "military-field-men-only": ("지원자격\n병역사항(남성만 지원 가능)", "SEX-001"),
    "record-men-only-apply": ("제출서류\n주민등록초본: 남성만 응시 가능", "SEX-001"),
    "age-limit-release-refused": ("지원자격\n연령 제한 해제 불가", "AGE-002"),
    "age-limit-not-dropped": ("지원자격\n연령제한 삭제는 하지 않습니다.", "AGE-002"),
    # Shapes the second draft still suppressed (independent re-review).
    "family-false-entry-as-list-item": ("유의사항\n부모 직업, 경력 허위 기재 시 불합격", "FAMILY-001"),
    "family-false-entry-joined": ("유의사항\n부모 직업, 학력사항 허위기재 시 합격 취소 및 불이익", "FAMILY-001"),
    "family-gap-differs": ("유의사항\n부모 직업 기재 시 사실과 다를 경우 불합격", "FAMILY-001"),
    "family-gap-mistake": ("유의사항\n부모 직업 기재 시 착오로 인한 불이익", "FAMILY-001"),
    "family-gap-missing-proof": ("유의사항\n부모 직업 기재 시 증빙 미제출하면 불이익", "FAMILY-001"),
    "older-band-other-preference": (
        "우대사항: 취업지원대상자 및 장애인 우대\n응시자격: 고령자(55세 이상)는 응시 대상이 아님", "AGE-002"),
    "older-band-under-unrelated-heading": ("우대사항\n- 관련 자격증 소지자\n지원자격\n- 준고령자(50세 이상 55세 미만)", "AGE-002"),
    "older-band-dropped-out": ("취업지원대상자 우대\n※ 고령자(55세 이상)는 이번 채용 대상에서 빠집니다", "AGE-002"),
    "older-band-failed": ("장애인 우대, 고령자(55세 이상)는 서류전형 탈락", "AGE-002"),
    "older-band-not-preferred": ("우대사항\n고령자(55세 이상) 우대 대상 아님", "AGE-002"),
    "older-band-lowest-priority": ("우대사항\n고령자(55세 이상) 우선 순위 최하", "AGE-002"),
    "older-band-penalised": ("우대사항\n고령자(55세 이상) 서류전형 감점", "AGE-002"),
    "military-men-only-women-refused": ("지원자격\n병역 : 남성만 해당, 여성은 응시 불가", "SEX-001"),
    "military-men-only-veterans": ("자격요건\n병역: (남성만) 군필자", "SEX-001"),
    "military-men-only-then-apply": ("지원자격\n병역사항(남성만) 지원 가능", "SEX-001"),
    "age-limit-release-not-possible": ("지원자격\n연령 제한 해제는 불가", "AGE-002"),
    "age-limit-abolition-hard": ("지원자격\n연령 제한 폐지는 어렵습니다", "AGE-002"),
}


@pytest.mark.parametrize("text, rule_id", FIXED.values(), ids=FIXED.keys())
def test_real_posting_false_findings_are_gone(text: str, rule_id: str) -> None:
    assert rule_id not in _findings(text)


@pytest.mark.parametrize("text, rule_id", KEPT.values(), ids=KEPT.keys())
def test_real_restrictions_still_fire(text: str, rule_id: str) -> None:
    assert rule_id in _findings(text)


# --- whitespace backtracking -----------------------------------------------------

_WS = r"(?:\[ \\t\][*+]|\\s[*+])"
# One optional or repeated token, allowing one level of nested groups.
_OPTIONAL_TOKEN = r"(?:\[[^\]]*\]|\(\?:(?:[^()]|\([^()]*\))*\)|[^\\()\[\]*?+{}|])[?*]"
_ADJACENT_WHITESPACE = re.compile(_WS + r"(?:" + _OPTIONAL_TOKEN + r")+" + _WS)
_LEADING_WHITESPACE = re.compile(r"^re:(?:\(\?:[^()]*\)\?)?" + _WS)
_EMPTY_ALTERNATIVE = re.compile(r"\|\)|\(\?:\|")


def _rule_regexes() -> list[tuple[str, str]]:
    """Every dictionary regex: rule triggers, slots and guidance alike."""
    found: list[tuple[str, str]] = []

    def walk(value, where: str) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item, where)
        elif isinstance(value, list):
            for item in value:
                walk(item, where)
        elif isinstance(value, str) and value.startswith("re:"):
            found.append((where, value))

    for path in sorted((ROOT / "data").rglob("*.yaml")):
        walk(yaml.safe_load(path.read_text(encoding="utf-8")), path.name)
    return found


def test_whitespace_lint_recognises_the_known_shapes() -> None:
    for shape in (r"\s*(?:을|를)?\s*", r"[ \t]*[:：-]?[ \t]*", r"\s*(?:(?:을|를))?\s*",
                  r"\s*(?:직원|사원)?(?:을|를)?\s*", r"\s+X?\s*", r"\s*(?:을|를)*\s*"):
        assert _ADJACENT_WHITESPACE.search(shape), shape
    for safe in (r"\s*(?:(?:을|를)\s*)?", r"[ \t]*(?:[:：-][ \t]*)?"):
        assert not _ADJACENT_WHITESPACE.search(safe), safe
    assert _EMPTY_ALTERNATIVE.search("(?:가|나|)") and _EMPTY_ALTERNATIVE.search("(?:|가)")


def test_rule_regexes_have_no_ambiguous_whitespace_runs() -> None:
    # "\s*(?:을|를)?\s*" lets two quantifiers split one blank run in every way;
    # write "\s*(?:(?:을|를)\s*)?" instead, which accepts the same strings.
    offenders = [(rule_id, pattern) for rule_id, pattern in _rule_regexes()
                 if _ADJACENT_WHITESPACE.search(pattern)]
    assert offenders == []


def test_rule_regexes_do_not_start_with_an_optional_whitespace_run() -> None:
    # A leading optional run restarts at every blank of a long run.
    offenders = [(rule_id, pattern) for rule_id, pattern in _rule_regexes()
                 if _LEADING_WHITESPACE.search(pattern)]
    assert offenders == []


def test_rule_regexes_have_no_empty_alternative() -> None:
    # "(?:가|나|)" is an optional token in disguise and hides from the lint above.
    offenders = [(where, pattern) for where, pattern in _rule_regexes()
                 if _EMPTY_ALTERNATIVE.search(pattern)]
    assert offenders == []


# Inputs that reach the rewritten regexes inside the engine (each of the first
# four took 4-5 s in Python before the fix and well under 0.1 s after). The dictionary patterns for
# 최종학력ㆍ학력사항ㆍ전공명 only run inside a 600-character context window, so a
# blank run after those words was never an engine hot spot.
LONG_BLANK_RUNS = {
    "result-notice": "합격자 발표\n불합격자에게는 별도 통보하지 않음\n결과 안내" + " " * 20000 + "예정",
    "result-notice-tabs": "탈락 시 연락 없음\n" + "\t" * 20000 + "x",
    "staff-wanted": "모집\n남성 직원" + " " * 20000 + "x",
    "gender-headcount": "모집\n여성" + " " * 20000 + "x",
    # An earlier draft of the FAMILY-001 exclusion split each Hangul run of an
    # item list in many ways (about 55 s in Python for this input).
    "item-list-backtracking": "유의사항\n" + (
        "가족 직업/" + "/".join(["가나다라마바사아"] * 6) + " 기재\n부모 직업 기재 시 감점\n") * 80,
}


def test_long_blank_runs_finish_quickly() -> None:
    # A process-level bound, not a millisecond assertion: the four inputs took
    # about 20 s together before the fix, so 5 s fails the old regexes on any
    # machine and leaves a wide margin for the fixed ones.
    script = (
        "import json, sys\n"
        "from core import FairpostEngine\n"
        "engine = FairpostEngine()\n"
        "for text in json.loads(sys.stdin.read()):\n"
        "    engine.check(text)\n"
    )
    import json

    subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        input=json.dumps(list(LONG_BLANK_RUNS.values())),
        text=True,
        encoding="utf-8",
        check=True,
        timeout=5,
        capture_output=True,
    )


# --- browser parity ---------------------------------------------------------------

def _parity_cases() -> dict[str, str]:
    cases: dict[str, str] = {}
    for prefix, table in (("fixed", FIXED), ("kept", KEPT)):
        for key, (text, _) in table.items():
            cases[f"{prefix}:{key}"] = text
            cases[f"{prefix}:{key}:crlf"] = text.replace("\n", "\r\n")
            cases[f"{prefix}:{key}:in-posting"] = f"2026년 공무직 채용 공고\n전형절차\n서류전형 → 면접전형\n{text}"
    for key, text in LONG_BLANK_RUNS.items():
        cases[f"blank-run:{key}"] = text
    return cases


@pytest.mark.requires_node
def test_python_and_web_agree_on_every_case(tmp_path: Path) -> None:
    cases = _parity_cases()
    labels = list(cases)
    web_results = web_check_batch([cases[label] for label in labels], tmp_path)
    engine = FairpostEngine()
    for label, web in zip(labels, web_results, strict=True):
        assert web == engine.check(cases[label]).to_dict(), label
