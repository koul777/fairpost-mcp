from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .morph import find_first
from .schema import SlotStatus


SECTION_VERSION = "sections-v1-heading-alias-preferred-fallback"


SECTION_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("개요", ("채용개요", "모집개요", "공고개요")),
    ("자격요건", ("자격요건", "지원자격", "응시자격", "필수요건")),
    ("우대사항", ("우대사항", "가점사항", "우대조건")),
    ("전형절차", ("전형절차", "전형방법", "선발절차", "채용절차")),
    ("일정", ("전형일정", "채용일정", "일정")),
    ("근무조건", ("근무조건", "근로조건", "보수", "급여")),
    ("제출서류", ("제출서류", "지원서류", "구비서류")),
    ("유의사항", ("유의사항", "주의사항")),
    ("문의처", ("문의처", "문의", "연락처")),
    ("기타", ("기타",)),
)


@dataclass(frozen=True)
class Section:
    name: str
    start: int
    end: int
    text: str


def _heading_name(line: str) -> str | None:
    cleaned = re.sub(r"^[\s#>*\-–—\d.()①-⑳]+|[\s:：]+$", "", line.strip())
    if not cleaned or len(cleaned) > 30:
        return None
    compact = re.sub(r"\s+", "", cleaned)
    for canonical, aliases in SECTION_ALIASES:
        if any(compact == re.sub(r"\s+", "", alias) for alias in aliases):
            return canonical
    return None


def split_sections(text: str) -> list[Section]:
    headings: list[tuple[str, int]] = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        name = _heading_name(line)
        if name:
            headings.append((name, cursor))
        cursor += len(line)

    if not headings:
        return [Section("전체", 0, len(text), text)]

    sections: list[Section] = []
    if headings[0][1] > 0:
        sections.append(Section("전체", 0, headings[0][1], text[: headings[0][1]]))
    for index, (name, start) in enumerate(headings):
        end = headings[index + 1][1] if index + 1 < len(headings) else len(text)
        sections.append(Section(name, start, end, text[start:end]))
    return sections


def section_at(sections: list[Section], offset: int) -> str:
    for section in sections:
        if section.start <= offset < section.end:
            return section.name
    return "전체"


def _evidence_line(text: str, start: int, end: int) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end].strip()[:240]


def extract_slots(
    text: str, sections: list[Section], slot_definitions: dict[str, Any]
) -> list[SlotStatus]:
    statuses: list[SlotStatus] = []
    for slot_id in sorted(slot_definitions):
        definition = slot_definitions[slot_id]
        preferred = [
            section
            for section in sections
            if section.name in definition.get("search_sections", [])
        ]
        search_order = preferred + [section for section in sections if section not in preferred]
        matched_section: Section | None = None
        matched = None
        for section in search_order:
            matched = find_first(section.text, definition.get("accept_patterns", []))
            if matched:
                matched_section = section
                break

        components_found: list[str] = []
        for component in definition.get("components", []):
            if find_first(text, component.get("patterns", [])):
                components_found.append(component["id"])

        if matched and matched_section:
            absolute_start = matched_section.start + matched.start()
            absolute_end = matched_section.start + matched.end()
            evidence = _evidence_line(text, absolute_start, absolute_end)
            section_name = matched_section.name
        else:
            evidence = None
            section_name = None

        statuses.append(
            SlotStatus(
                slot=slot_id,
                label=definition["label"],
                found=matched is not None,
                components_found=sorted(components_found),
                components_total=len(definition.get("components", [])),
                evidence=evidence,
                section=section_name,
            )
        )
    return statuses
