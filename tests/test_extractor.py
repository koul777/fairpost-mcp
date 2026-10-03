"""Behavior-pinning tests for core.extractor section and slot extraction."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess

import pytest

from core import FairpostEngine, load_ruleset
from core.extractor import Section, extract_slots, section_at, split_sections


ROOT = Path(__file__).resolve().parents[1]


def _spans(text: str) -> list[tuple[str, int, int]]:
    return [(section.name, section.start, section.end) for section in split_sections(text)]


def _assert_tiles(text: str) -> None:
    sections = split_sections(text)
    assert "".join(section.text for section in sections) == text
    assert all(text[s.start : s.end] == s.text for s in sections)
    assert all(left.end == right.start for left, right in zip(sections, sections[1:]))


def test_empty_text_is_one_empty_whole_section() -> None:
    assert split_sections("") == [Section("전체", 0, 0, "")]


def test_text_without_headings_is_one_whole_section() -> None:
    text = "남성만 지원 가능합니다."

    assert split_sections(text) == [Section("전체", 0, len(text), text)]


def test_preamble_and_korean_headings_tile_the_text() -> None:
    text = "채용 안내\n자격요건\n남성만\n전형절차\n서류"

    assert _spans(text) == [("전체", 0, 6), ("자격요건", 6, 15), ("전형절차", 15, 22)]
    _assert_tiles(text)


@pytest.mark.parametrize(
    ("heading", "canonical"),
    [
        ("1. 지원 자격 :", "자격요건"),
        ("② 전형 방법", "전형절차"),
        ("## 우대사항", "우대사항"),
        ("> 근무 조건：", "근무조건"),
        ("- 문의", "문의처"),
        ("(3) 제출 서류", "제출서류"),
        ("　자격요건　", "자격요건"),
        ("１. 자격요건", "자격요건"),
        ("채용 개요", "개요"),
        ("기타", "기타"),
    ],
)
def test_decorated_korean_heading_aliases(heading: str, canonical: str) -> None:
    text = f"{heading}\n본문"

    assert _spans(text) == [(canonical, 0, len(text))]


@pytest.mark.parametrize(
    "line",
    [
        "지원자격은 별도로 안내합니다",
        "자격요건 및 우대사항 안내 사항입니다 정말로 긴 제목입니다",
        "자격​요건",
        "■ 자격요건",
        "[자격요건]",
        "１．자격요건",
    ],
)
def test_lines_that_are_not_headings(line: str) -> None:
    text = f"{line}\n남성만"

    assert _spans(text) == [("전체", 0, len(text))]


def test_crlf_offsets_count_both_characters() -> None:
    text = "자격요건\r\n남성만\r\n전형절차\r\n서류"

    assert _spans(text) == [("자격요건", 0, 11), ("전형절차", 11, 19)]
    _assert_tiles(text)


def test_repeated_canonical_sections_stay_separate() -> None:
    text = "문의\n02-1234-5678\n문의처\n인사팀"

    assert _spans(text) == [("문의처", 0, 16), ("문의처", 16, 23)]


def test_section_at_uses_half_open_ranges() -> None:
    sections = split_sections("안내\n자격요건\n남성만")

    assert [section_at(sections, offset) for offset in (0, 2, 3, 10, 11)] == [
        "전체",
        "전체",
        "자격요건",
        "자격요건",
        "전체",
    ]
    assert section_at(sections, -1) == "전체"
    assert section_at([], 0) == "전체"


PAY_SLOT = {
    "label": "보수",
    "accept_patterns": ["급여"],
    "search_sections": ["근무조건"],
    "components": [
        {"id": "cycle", "patterns": ["월"]},
        {"id": "amount", "patterns": ["만원"]},
        {"id": "bonus", "patterns": ["상여"]},
    ],
}
CONTACT_SLOT = {"label": "문의", "accept_patterns": ["전화"]}


def test_slots_prefer_search_sections_and_trim_evidence_to_the_sentence() -> None:
    text = "개요\n급여는 내규에 따름\n근무조건\n안내입니다. 급여 월 300만원! 기타 안내\n"

    statuses = extract_slots(
        text, split_sections(text), {"pay": PAY_SLOT, "contact": CONTACT_SLOT}
    )
    pay = {status.slot: status for status in statuses}["pay"]

    assert pay.found is True
    assert pay.section == "근무조건"
    assert pay.evidence == "급여 월 300만원!"
    assert pay.components_found == ["amount", "cycle"]
    assert pay.components_total == 3


def test_slots_fall_back_to_other_sections_and_report_missing_slots() -> None:
    text = "개요\n급여는 내규에 따름\n"

    statuses = extract_slots(
        text, split_sections(text), {"pay": PAY_SLOT, "contact": CONTACT_SLOT}
    )

    assert [status.slot for status in statuses] == ["contact", "pay"]
    contact, pay = statuses
    assert (contact.found, contact.evidence, contact.section) == (False, None, None)
    assert contact.components_total == 0
    assert (pay.found, pay.section, pay.evidence) == (True, "전체", "급여는 내규에 따름")


def test_slot_evidence_keeps_source_characters_across_crlf_and_zero_width() -> None:
    text = "근무조건\r\n급​여 월 300만원\r\n"

    [pay] = extract_slots(text, split_sections(text), {"pay": PAY_SLOT})

    assert pay.found is True
    assert pay.section == "근무조건"
    assert pay.evidence == "급​여 월 300만원"


def test_long_slot_evidence_is_windowed_with_ellipses() -> None:
    text = ("가" * 200) + " 급여 " + ("나" * 200)

    [pay] = extract_slots(text, split_sections(text), {"pay": PAY_SLOT})

    assert pay.evidence is not None
    assert len(pay.evidence) == 240
    assert pay.evidence.startswith("…") and pay.evidence.endswith("…")
    assert pay.evidence.index("급여") == 97


def test_empty_text_finds_no_slots() -> None:
    statuses = extract_slots("", split_sections(""), {"pay": PAY_SLOT})

    assert [(s.found, s.components_found, s.evidence) for s in statuses] == [
        (False, [], None)
    ]


def test_shipped_contact_slot_detects_every_component() -> None:
    ruleset = load_ruleset()
    text = (
        "채용 안내\n자격요건\n학력 무관\n문의처\n"
        "인사팀 02-1234-5678 recruit@example.com 평일 09:00~18:00\n"
    )

    statuses = {
        status.slot: status
        for status in extract_slots(text, split_sections(text), ruleset.slots)
    }

    assert sorted(statuses) == sorted(ruleset.slots)
    contact = statuses["contact_point"]
    assert contact.found is True
    assert contact.section == "문의처"
    assert contact.components_found == ["department", "email", "hours", "phone"]
    assert contact.components_total == 4


# Known Python/JS parity gaps in section detection, reported to the owner.
# Python splits lines with str.splitlines() and strips str.isspace()
# characters; web/engine.js splits on "\n" only and uses ECMAScript
# whitespace (which includes U+FEFF but not U+001C-U+001F or U+0085).
# strict=True turns a fix on either side into a failure so this list is
# revisited instead of silently going stale.
@pytest.mark.requires_node
@pytest.mark.xfail(strict=True, reason="section split/whitespace parity gap")
@pytest.mark.parametrize(
    "text",
    [
        "3쪽\x0c자격요건\n남성만 지원 가능",
        "안내\r자격요건\r남성만 지원 가능",
        "안내 자격요건 남성만 지원 가능",
        "﻿자격요건\n남성만 지원 가능",
        "안내\n\x1f자격요건\n남성만 지원 가능",
    ],
    ids=["form-feed", "cr-only", "line-separator", "bom-heading", "unit-separator"],
)
def test_section_detection_parity_gaps_with_web_engine(text: str) -> None:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    completed = subprocess.run(
        ["node", "tests/js_runner.cjs", encoded],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert json.loads(completed.stdout) == FairpostEngine().check(text).to_dict()
