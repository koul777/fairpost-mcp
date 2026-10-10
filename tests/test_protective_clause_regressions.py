"""Synthetic development regressions for protective clauses around sensitive items.

These are hand-written cases, not human-labelled gold data. They pin a blocking
detection defect in ``core.extractor.source_candidate_allowed``: a candidate that
concerns 부모·형제자매·가족·출신학교·혼인·신체 조건 was dropped whenever a
protective verb appeared anywhere in the same sentence, even when that verb
governed another item ("사진은 기재하지 않아도 되나, 부모 직업은 반드시 기재해야
합니다."), and a penalty for leaving the item out ("미기재 시 탈락처리") was read
as protection. The SCHOOL-001 exclusion in data/rules/law.yaml had the same
reach across clauses.

A protective verb now silences only the candidate it governs (same clause), and
the protective notices that were silent before stay silent. Every case also runs
through the browser engine and must match Python exactly.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import re
import subprocess

import pytest

from core import FairpostEngine
from core import extractor
from core.morph import find_first


ROOT = Path(__file__).resolve().parents[1]

# A requirement in one clause that a protective verb in another clause used to
# hide. (text, finding that must be reported)
MUST_FLAG = {
    "optional-photo-then-required-parent-job": (
        "사진은 기재하지 않아도 되나, 부모 직업은 반드시 기재해야 합니다.", "FAMILY-001"),
    "id-not-collected-then-parent-job": (
        "주민등록번호는 수집하지 않으며 지원서에 부모 직업을 기재하십시오.", "FAMILY-001"),
    "school-omitted-then-parent-education": (
        "출신학교는 기재하지 마시고, 부모의 학력을 기재해 주세요.", "SCHOOL-001"),
    "school-omitted-malgo-then-parent-education": (
        "출신학교는 기재하지 말고, 부모의 학력을 기재해 주세요.", "SCHOOL-001"),
    "school-omitted-malgo-no-comma": (
        "출신학교는 기재하지 말고 부모의 학력을 기재해 주세요.", "SCHOOL-001"),
    "missing-parent-job-rejected": ("부모 직업 미기재 시 탈락처리", "FAMILY-001"),
    "missing-parent-job-penalised": (
        "부모 직업을 기재하지 않으면 서류전형에서 불이익이 있습니다.", "FAMILY-001"),
    # Sibling education is SCHOOL-001's item (FAMILY-001 covers occupation and assets).
    "missing-sibling-education-excluded": (
        "형제자매의 학력을 기재하지 않은 지원서는 평가대상에서 제외합니다.", "SCHOOL-001"),
    "missing-parent-job-deducted": ("부모 직업 누락 시 감점", "FAMILY-001"),
    "missing-sibling-job-unfit": (
        "형제자매 직업을 작성하지 않을 경우 부적합 처리합니다", "FAMILY-001"),
    "school-not-written-anhgo": ("출신학교는 기재하지 않고 부모 직업을 기재하십시오.", "FAMILY-001"),
    "school-not-written-jiman": (
        "출신학교는 기재하지 않지만 부모 직업은 기재해야 합니다.", "FAMILY-001"),
    "photo-not-written-euna": ("사진은 기재하지 않으나, 부모 직업은 기재하세요.", "FAMILY-001"),
    "protection-before-requirement-same-clause": ("사진 기재 불필요 부모 직업 기재하세요", "FAMILY-001"),
    "protection-instead-requirement": (
        "주민등록번호 수집 금지 대신 부모 직업을 작성해야 합니다", "FAMILY-001"),
    "school-ban-then-parent-education": (
        "출신학교 기재 금지 상태로 부모의 학력을 기재해 주세요", "SCHOOL-001"),
    "bracketed-ban-then-parent-job": ("출신학교(기재 금지), 부모 직업 기재", "FAMILY-001"),
    "ban-comma-then-parent-job-bonus": ("출신학교 기재 금지, 부모 직업 기재 시 가산점", "FAMILY-001"),
    "school-ban-then-required-parent-job": ("출신학교 기재 금지 부모 직업 필수", "FAMILY-001"),
}

# Linked or own-trigger questions the same defect removed.
# (text, question that must be asked, finding that must be reported or None)
LINKED_QUESTIONS = {
    "height-then-school-ban": ("지원자격: 신체 조건 키 175cm 이상, 출신학교 기재 금지", "Q-DIST-007", "BODY-001"),
    "height-then-education-indifferent": ("신체 조건 키 175cm 이상, 학력 무관", "Q-DIST-007", "BODY-001"),
    "family-culture-then-photo-optional": ("가족 같은 분위기, 이력서에 사진 기재 불필요", "Q-INTER-002", None),
    "family-culture-then-resume-optional": ("가족같은 회사, 이력서 작성 불필요", "Q-INTER-002", None),
    "id-omitted-then-family-certificate": (
        "주민등록번호 뒷자리는 기재하지 마시고 가족관계증명서를 제출하세요.", "Q-INFO-011", None),
    "certificate-with-masked-id": (
        "가족관계증명서는 주민등록번호 기재 금지 상태로 제출하십시오", "Q-INFO-011", None),
}

# Genuine protective notices: the protection governs the candidate, so the
# listed findings and questions stay absent. (text, ids that must be absent)
PROTECTIVE_SILENT = {
    "parent-job-do-not-write": ("부모 직업은 기재하지 마세요", {"FAMILY-001", "Q-DIST-010", "Q-INFO-010"}),
    "school-not-written": ("출신학교는 기재하지 않습니다", {"Q-DIST-005", "Q-DIST-010"}),
    # Q-DIST-007 was asked here at the base commit; "무관" attached to the
    # candidate is now read as protection too.
    "body-indifferent-hiring": ("신체 조건과 무관하게 채용합니다", {"Q-DIST-007"}),
    "body-indifferent-bare": ("신체 조건 무관", {"Q-DIST-007"}),
    "blind-school-ban-heading": ("블라인드 안내\n출신학교 기재 금지", {"Q-DIST-005", "Q-DIST-010"}),
    "school-family-list-ban": (
        "자기소개서에 출신학교, 가족관계 등 인적사항 관련 내용은 일체 기재 금지",
        {"Q-DIST-005", "Q-DIST-010"}),
    "marital-family-assets-list": (
        "블라인드 안내: 혼인 여부, 가족관계, 재산은 지원서에 기재하지 마세요.", {"Q-DIST-017"}),
    "sibling-list-do-not-write": (
        "형제자매의 학력, 직업, 재산내용을 일체 기재하지 말아주세요.", {"SCHOOL-001", "FAMILY-001"}),
    "sibling-education-not-received": (
        "개인정보 보호를 위해 형제자매의 학력은 기재받지 않습니다.", {"SCHOOL-001"}),
    "sibling-list-with-lead-in": (
        "개인정보 보호를 위해 형제자매의 학력, 직업, 재산은 기재받지 않습니다", {"SCHOOL-001"}),
    "sibling-education-ban": ("지원서에는 형제자매의 학력 기재 금지", {"SCHOOL-001"}),
    "sibling-education-and-job-list": (
        "형제자매의 학력 및 직업, 재산 등은 기재하지 마세요", {"SCHOOL-001"}),
    "personal-details-excluded-if-written": (
        "본인의 성별·신체조건·출신학교, 부모의 직업 등 인적사항 기재 시 평가대상에서 제외(부적합 처리)",
        {"FAMILY-001", "Q-DIST-007", "Q-DIST-005", "Q-DIST-010"}),
    "sibling-dot-list-not-collected": ("형제자매의 학력·직업·재산 등 개인정보 수집 금지", {"SCHOOL-001"}),
    "marital-dot-list-not-collected": ("혼인여부·재산 등 개인정보 수집 금지", {"Q-DIST-017"}),
    "marital-photo-list-optional": ("지원서에 혼인 여부, 사진 기재 불필요", {"Q-DIST-017"}),
    "colon-lead-in-list": ("다음 항목은 기재하지 마십시오: 부모 직업, 가족 재산", {"FAMILY-001"}),
    "banned-items-label": ("수집 금지 항목: 부모 직업, 가족 재산", {"FAMILY-001"}),
    "conditional-comma-excluded": (
        "출신학교 등을 기재할 경우, 평가대상에서 제외됩니다", {"Q-DIST-005", "Q-DIST-010"}),
    "conditional-si-comma-excluded": ("부모 직업 기재 시, 평가대상에서 제외", {"FAMILY-001"}),
    "ban-with-penalty-then-other-requirement": (
        "부모 직업 기재 금지(위반 시 탈락), 경력은 상세히 기재하세요", {"FAMILY-001"}),
    "thousands-comma-before-ban": ("연봉 3,000만원, 부모 직업 기재 금지", {"FAMILY-001"}),
    "optional-without-penalty": ("부모 직업을 기재하지 않아도 불이익은 없습니다", {"FAMILY-001"}),
    "omission-without-penalty": ("부모 직업을 기재하지 않은 경우에도 불이익은 없습니다", {"FAMILY-001"}),
    "omission-no-disadvantage-given": (
        "부모 직업을 기재하지 않은 지원서도 불이익을 주지 않습니다", {"FAMILY-001"}),
    # The ban right after 출신학교 governs it; the later request is for another item.
    "school-ban-not-overridden": ("출신학교 기재 금지 상태로 부모의 학력을 기재해 주세요", {"Q-DIST-005", "Q-DIST-010"}),
    "school-ban-adverb-not-overridden": (
        "출신학교는 일체 기재 금지 상태로 부모의 학력을 기재해 주세요", {"Q-DIST-005", "Q-DIST-010"}),
    "school-ban-before-required-parent-job": ("출신학교 기재 금지 부모 직업 필수", {"Q-DIST-010"}),
}

# 고/나/며 inside ordinary words (or the particle (이)나) between the candidate
# and its protective verb must not split the clause. (text, ids that must be absent)
LOOK_ALIKES = {
    "gogaek-in-list": ("부모 직업, 고객 정보 등은 기재하지 마세요", {"FAMILY-001"}),
    "gogaeksa-after-and": ("부모 직업 및 고객사 정보는 기재하지 마세요", {"FAMILY-001"}),
    "nai-in-list": ("부모 직업, 나이 등은 기재하지 마세요", {"FAMILY-001"}),
    "myeochil": ("부모 직업은 며칠 뒤 제출 서류에도 기재하지 마세요", {"FAMILY-001"}),
    "gonggo-word-final": ("부모 직업은 본 공고 지원서에 기재하지 마세요", {"FAMILY-001"}),
    "chamgo-word-final": ("부모 직업은 참고 자료로도 기재하지 마세요", {"FAMILY-001"}),
    "particle-ina": ("부모 직업이나 형제자매 직업은 기재하지 마세요", {"FAMILY-001"}),
    "particle-na": ("출신학교나 부모 직업은 기재하지 마세요", {"FAMILY-001", "Q-DIST-005", "Q-DIST-010"}),
    "hana-noun": ("부모 직업 등 하나의 항목도 기재하지 마세요", {"FAMILY-001"}),
}

CLAUSE_END_LOOK_ALIKES = ["고객 ", "공고 ", "참고 ", "최고 ", "나이 ", "하나 ", "며칠 ", "부모나 ", "직업이나 ", "고등학교 "]
CLAUSE_END_CONNECTIVES = [
    "되나 ", "되나,", "않으며 ", "이며 ", "하며,", "마시고 ", "마시고,", "말고 ", "않고 ",
    "하고 ", "주시고 ", "않지만 ", "하지만,", "않으나,", "가능하나 ",
]


def _check(text: str) -> dict:
    return FairpostEngine().check(text).to_dict()


def _ids(result: dict) -> set[str]:
    return {item["id"] for item in result["findings"]} | {item["id"] for item in result["questions"]}


@pytest.mark.parametrize(("text", "finding_id"), MUST_FLAG.values(), ids=MUST_FLAG.keys())
def test_requirement_in_another_clause_is_reported(text: str, finding_id: str) -> None:
    result = _check(text)
    finding = next(f for f in result["findings"] if f["id"] == finding_id)
    start, end = finding["offset"]
    assert text[start:end] == finding["matched_text"]


@pytest.mark.parametrize(
    ("text", "question_id", "finding_id"), LINKED_QUESTIONS.values(), ids=LINKED_QUESTIONS.keys()
)
def test_question_hidden_by_another_clause_is_asked(
    text: str, question_id: str, finding_id: str | None
) -> None:
    result = _check(text)
    assert question_id in {q["id"] for q in result["questions"]}
    if finding_id is not None:
        assert finding_id in {f["id"] for f in result["findings"]}


@pytest.mark.parametrize(("text", "absent"), PROTECTIVE_SILENT.values(), ids=PROTECTIVE_SILENT.keys())
def test_genuine_protective_notice_stays_silent(text: str, absent: set[str]) -> None:
    assert not absent & _ids(_check(text))


@pytest.mark.parametrize(("text", "absent"), LOOK_ALIKES.values(), ids=LOOK_ALIKES.keys())
def test_look_alike_syllables_do_not_split_a_clause(text: str, absent: set[str]) -> None:
    assert not absent & _ids(_check(text))


@pytest.mark.parametrize("word", CLAUSE_END_LOOK_ALIKES)
def test_clause_end_pattern_ignores_ordinary_words(word: str) -> None:
    assert find_first(word, extractor._PROTECTIVE_CLAUSE_END) is None


@pytest.mark.parametrize("ending", CLAUSE_END_CONNECTIVES)
def test_clause_end_pattern_matches_connective_endings(ending: str) -> None:
    assert find_first(f"기재하지 {ending}", extractor._PROTECTIVE_CLAUSE_END) is not None


def test_protective_clause_stops_at_the_connective() -> None:
    text = "사진은 기재하지 않아도 되나, 부모 직업은 반드시 기재해야 합니다."
    start = text.index("부모 직업")
    clause_start, clause_end = extractor._protective_clause(text, start, start + len("부모 직업"))
    assert text[clause_start:clause_end].strip(" ,") == "부모 직업은 반드시 기재해야 합니다."


def test_protective_clause_spans_a_noun_list() -> None:
    text = "자기소개서에 출신학교, 가족관계 등 인적사항 관련 내용은 일체 기재 금지"
    start = text.index("출신학교")
    assert extractor._protective_clause(text, start, start + len("출신학교")) == (0, len(text))


# --- the web engine mirrors the patterns -------------------------------------------

_MIRRORED = {
    "SENSITIVE_CANDIDATE": "_SENSITIVE_CANDIDATE",
    "NOT_AN_INFORMATION_ITEM": "_NOT_AN_INFORMATION_ITEM",
    "PROTECTIVE": "_PROTECTIVE",
    "PROTECTIVE_INDIFFERENT": "_PROTECTIVE_INDIFFERENT",
    "PROTECTIVE_OMISSION_PENALTY": "_PROTECTIVE_OMISSION_PENALTY",
    "PROTECTIVE_REQUIREMENT": "_PROTECTIVE_REQUIREMENT",
    "PROTECTIVE_CLAUSE_END": "_PROTECTIVE_CLAUSE_END",
    "PROTECTIVE_COMMA": "_PROTECTIVE_COMMA",
    "PROTECTIVE_CONDITIONAL_TAIL": "_PROTECTIVE_CONDITIONAL_TAIL",
    "PROTECTIVE_PREDICATE_TAIL": "_PROTECTIVE_PREDICATE_TAIL",
    "PROTECTIVE_NON_LIST_HEAD": "_PROTECTIVE_NON_LIST_HEAD",
    "PROTECTIVE_DIRECT_GAP": "_PROTECTIVE_DIRECT_GAP",
    "PROTECTIVE_TOKEN": "_PROTECTIVE_TOKEN",
}


@pytest.mark.parametrize(("js_name", "py_name"), _MIRRORED.items(), ids=_MIRRORED.keys())
def test_web_engine_uses_the_same_protective_patterns(js_name: str, py_name: str) -> None:
    source = (ROOT / "web" / "engine.js").read_text(encoding="utf-8")
    declaration = re.search(rf"const {js_name} = (\[.*?\]);", source, re.S)
    assert declaration is not None
    assert json.loads(re.sub(r",\s*\]$", "]", declaration.group(1))) == getattr(extractor, py_name)


def test_web_engine_uses_the_same_list_item_limit() -> None:
    source = (ROOT / "web" / "engine.js").read_text(encoding="utf-8")
    declaration = re.search(r"const PROTECTIVE_LIST_ITEM_TOKENS = (\d+);", source)
    assert declaration is not None
    assert int(declaration.group(1)) == extractor._PROTECTIVE_LIST_ITEM_TOKENS


# --- Python / web parity ---------------------------------------------------------


def _web_check_batch(texts: list[str], tmp_path: Path) -> list[dict]:
    batch = tmp_path / "web-batch.json"
    batch.write_text(
        json.dumps([base64.b64encode(t.encode("utf-8")).decode("ascii") for t in texts]),
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["node", "tests/js_runner.cjs", "--batch", str(batch)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    results = json.loads(completed.stdout)
    assert len(results) == len(texts)
    return results


def _assert_same_result(label: str, python: dict, web: dict) -> None:
    # Named assertions first so a failure says which part diverged.
    assert [(f["id"], f["section"], f["offset"]) for f in web["findings"]] == [
        (f["id"], f["section"], f["offset"]) for f in python["findings"]
    ], label
    assert [
        (s["slot"], s["found"], s["section"], s["evidence"], s["components_found"])
        for s in web["slots"]
    ] == [
        (s["slot"], s["found"], s["section"], s["evidence"], s["components_found"])
        for s in python["slots"]
    ], label
    assert [q["id"] for q in web["questions"]] == [q["id"] for q in python["questions"]], label
    assert web == python, label


def _parity_cases() -> dict[str, str]:
    cases: dict[str, str] = {}
    for prefix, table in (
        ("flag", MUST_FLAG), ("linked", LINKED_QUESTIONS),
        ("silent", PROTECTIVE_SILENT), ("look-alike", LOOK_ALIKES),
    ):
        for key, value in table.items():
            text = value[0]
            cases[f"{prefix}:{key}"] = text
            # The same sentence inside a posting, under a heading and with CRLF.
            cases[f"{prefix}:{key}:in-posting"] = f"2026년 사무직 채용\n지원자격\n{text}\n문의처\n인사팀 02-123-4567"
            cases[f"{prefix}:{key}:crlf"] = f"제출서류\r\n{text}\r\n"
    # Compatibility and invisible characters reach the normalized regex view.
    cases["encoding:fullwidth-comma"] = "사진은 기재하지 않아도 되나，부모 직업은 반드시 기재해야 합니다."
    cases["encoding:zero-width-in-connective"] = "주민등록번호는 수집하지 않으​며 지원서에 부모 직업을 기재하십시오."
    cases["encoding:fullwidth-space"] = "출신학교는　기재하지　마시고,　부모의 학력을 기재해 주세요."
    cases["encoding:fullwidth-list-comma"] = "자기소개서에 출신학교，가족관계 등 인적사항 관련 내용은 일체 기재 금지"
    # A long sentence: the protective search never leaves the evidence window.
    filler = "직무 수행에 필요한 경험과 역량을 중심으로 지원서를 작성해 주시기 바랍니다 " * 6
    cases["long:protection-far-before"] = f"출신학교 기재 금지 {filler}부모 직업을 기재해 주세요"
    cases["long:protection-far-after"] = f"부모 직업을 적어 주세요 {filler}출신학교 기재 금지"
    return cases


@pytest.mark.requires_node
def test_python_and_web_agree_on_every_case(tmp_path: Path) -> None:
    cases = _parity_cases()
    labels = list(cases)
    texts = [cases[label] for label in labels]
    web_results = _web_check_batch(texts, tmp_path)
    engine = FairpostEngine()
    for label, text, web in zip(labels, texts, web_results, strict=True):
        _assert_same_result(label, engine.check(text).to_dict(), web)


@pytest.mark.requires_node
def test_web_engine_reports_the_reported_cases(tmp_path: Path) -> None:
    flagged = list(MUST_FLAG.values())
    linked = list(LINKED_QUESTIONS.values())
    silent = list(PROTECTIVE_SILENT.values()) + list(LOOK_ALIKES.values())
    texts = [t for t, _ in flagged] + [t for t, _, _ in linked] + [t for t, _ in silent]
    web = iter(_web_check_batch(texts, tmp_path))
    for _text, finding_id in flagged:
        assert finding_id in {f["id"] for f in next(web)["findings"]}
    for _text, question_id, _finding_id in linked:
        assert question_id in {q["id"] for q in next(web)["questions"]}
    for _text, absent in silent:
        assert not absent & _ids(next(web))
