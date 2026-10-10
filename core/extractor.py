from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any

from .morph import ZERO_WIDTH, find_first, find_matches
from .schema import SlotStatus


SECTION_VERSION = "sections-v5-ranked-evidence-negation-context"

_SENTENCE_BOUNDARIES = ".!?。！？"
_EVIDENCE_WINDOW_CODEPOINTS = 238
_HEADING_DECORATION = re.compile(r"^[\s#>*\-–—\d.()①-⑳\[\]■]+|[\s:：\[\]]+$")
_WHITESPACE = re.compile(r"\s+")


SECTION_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("개요", ("채용개요", "모집개요", "공고개요", "담당업무", "주요업무", "주요 업무", "담당 업무", "업무내용", "What You'll Do", "What You’ll Do", "합류하면 함께할 업무에요", "이런 일을 해요",)),
    ("자격요건", ("자격요건", "지원자격", "응시자격", "필수요건", "자격조건", "필요지식", "필요 지식 및 기술", "자격조건 및 필요지식", "Required Skills", "이런 분과 함께하고 싶어요",)),
    ("우대사항", ("우대사항", "우대내용", "가점사항", "우대조건", "Preferred Skills", "이런 분이면 더 좋아요!", "이런 경험이 있으면 더 좋아요",)),
    ("전형절차", ("전형절차/방법", "전형절차", "전형방법", "선발절차", "채용절차", "합류 여정", "이렇게 합류해요", "전형절차 및 일정", "전형 절차 및 안내 사항", "전형절차 및 기타사항",)),
    ("일정", ("전형일정", "채용일정", "일정", "접수기간", "모집기간", "공고기간", "접수 마감일",)),
    ("근무조건", ("근무조건", "근로조건", "보수", "급여", "고용형태", "근무형태",)),
    ("제출서류", ("제출서류", "지원서류", "구비서류",)),
    ("유의사항", ("유의사항", "주의사항", "꼭 확인해 주세요",)),
    ("문의처", ("문의처", "문의", "연락처",)),
    ("기타", ("기타",)),
)
_SECTION_BY_COMPACT_ALIAS: dict[str, str] = {}
for _canonical, _aliases in SECTION_ALIASES:
    for _alias in _aliases:
        # setdefault preserves the original first-match behavior if aliases
        # ever overlap across canonical sections.
        _SECTION_BY_COMPACT_ALIAS.setdefault(
            _WHITESPACE.sub("", _alias).casefold(), _canonical
        )


@dataclass(frozen=True)
class Section:
    name: str
    start: int
    end: int
    text: str


def _normalized_heading(line: str) -> str:
    return "".join(
        unicodedata.normalize("NFKC", char) for char in line if char not in ZERO_WIDTH
    )


def _heading_name(line: str) -> str | None:
    cleaned = _HEADING_DECORATION.sub("", _normalized_heading(line).strip())
    if not cleaned or len(cleaned) > 30:
        return None
    compact = _WHITESPACE.sub("", cleaned).casefold()
    if compact.endswith("합류여정"):
        return "전형절차"
    return _SECTION_BY_COMPACT_ALIAS.get(compact)


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


def _sentence_marks(text: str, left: int, right: int) -> list[int]:
    # Numeric enumeration and dotted dates are part of the evidence, not sentences.
    return [i for i in range(left, right) if text[i] in _SENTENCE_BOUNDARIES
            and not (text[i] == "." and i > 0 and (text[i - 1].isdecimal()
                     or (i + 1 < len(text) and re.match(r"[A-Za-z0-9]", text[i - 1]) and re.match(r"[A-Za-z0-9]", text[i + 1]))))]


def _evidence_line(text: str, start: int, end: int) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)

    before = _sentence_marks(text, line_start, start)
    segment_start = before[-1] + 1 if before else line_start
    after = _sentence_marks(text, end, line_end)
    segment_end = after[0] + 1 if after else line_end

    segment = text[segment_start:segment_end]
    if len(segment) <= _EVIDENCE_WINDOW_CODEPOINTS:
        return segment.strip()

    relative_start = start - segment_start
    relative_end = end - segment_start
    window_start = max(0, relative_start - 96)
    window_end = min(
        len(segment),
        max(relative_end + 96, window_start + _EVIDENCE_WINDOW_CODEPOINTS),
    )
    window_start = max(0, window_end - _EVIDENCE_WINDOW_CODEPOINTS)
    evidence = segment[window_start:window_end].strip()
    return (
        ("…" if window_start else "")
        + evidence
        + ("…" if window_end < len(segment) else "")
    )


_DUTY_HEADINGS = ["담당업무","주요업무","업무내용","What You'll Do","What You’ll Do","합류하면 함께할 업무에요","이런 일을 해요"]
_HIRING_CONTEXT = ["지원자","응시자","서류전형","면접","인터뷰","역량검사","전형절차","채용과정"]
_DUTY_CONTEXT = ["re:(?:알고리즘|임직원|직원|제품|서비스|고객).{0,80}(?:연구|개발|설계|운영|개선|평가)"]
_CONTACT_CONTEXT = ["문의","연락","인사팀","채용팀","인사부","담당 부서","전화","전자우편","re:[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}","re:0\\d{1,2}[- )]\\d{3,4}[- ]\\d{4}"]
_CONTACT_NON_CHANNEL = ["결과","통보","기재","등록","발송","지원서","모집"]
# A label body is "announced later" when it starts with 추후/차후/나중에/미정, or with 별도/상세/예정
# as a bare marker or a statement that names a deferral (안내/공지/협의/예정/미정/문의/통보/참조/
# 확인) and carries no date or amount. Real content that merely starts with an ambiguous word
# ("상세 일정: 8월 1일 ~", "별도 협의 없이 4,000만원") is kept.
# web/engine.js DEFERRED_BODY must stay identical.
_DEFERRED_BODY = ["re:^(?:(?:추후|차후|나중에|미정)[^\\n]*|(?![^\\n]*(?:(?:19|20)\\d{2}\\s*[./-]\\s*\\d{1,2}|\\d{1,2}\\s*[./]\\s*\\d{1,2}|\\d{1,4}\\s*(?:년|월|일|시|주)|\\d[\\d,.]{0,15}\\s*(?:[천백만억]\\s*)*원))(?:(?:별도|상세|예정)[^\\n]*(?:안내|공지|협의|예정|미정|문의|통보|참조|확인)[^\\n]*|예정[^\\n]{0,12}|별도|상세))$"]
_DATE_CONTENT = ["re:\\d{1,4}[./-]\\d{1,2}","re:\\d+\\s*(?:년|월|일|시|주)","상시","수시", "채용시까지", "채용 시까지"]
_STAGE_EVENTS = ["서류전형","면접전형","필기전형","서류접수","직무 인터뷰","실무 인터뷰","화상 인터뷰","문화적합성 인터뷰","코딩테스트","코딩 테스트","AI 역량검사","AI 면접","서류 전형","면접 전형","필기 전형", "AI 사전면접", "AI 자기소개서 평가", "서류 검토"]

def _line_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    left = text.rfind("\n", 0, start) + 1
    right = text.find("\n", end)
    return left, len(text) if right == -1 else right


def _compact(value: str) -> str:
    return _WHITESPACE.sub("", _HEADING_DECORATION.sub("", _normalized_heading(value).strip())).casefold()


def _is_duty_section(section: Section) -> bool:
    first = section.text.split("\n", 1)[0]
    return _compact(first) in {_compact(h) for h in _DUTY_HEADINGS}


def _context_allowed(slot_id: str, line: str, section: Section) -> bool:
    # Duties describe work, not the employer's own applicant assessment.
    if slot_id != "qualification_rationale" and _is_duty_section(section):
        return False
    if slot_id == "selection_stages" and find_first(line, ["인터뷰 자세히", "인터뷰 보기", "팀원 인터뷰", "현직자 인터뷰"]):
        return False
    if slot_id in {"ai_disclosure", "evaluation_criteria", "selection_stages"}:
        if section.name in {"자격요건", "우대사항"} and find_first(line, ["경험", "경력", "설계", "연구", "개발", "운영"]):
            if not find_first(line, ["지원자", "응시자", "채용 과정", "전형 절차", "실시", "참여", "진행"]):
                return False
        if find_first(line, _DUTY_CONTEXT) and not find_first(line, _HIRING_CONTEXT):
            return False
    if slot_id == "qualification_rationale" and re.search(r"\.(?:pdf|hwpx?|hml|docx?|zip)\s*$", line, re.I):
        return False  # An attachment filename does not disclose its body.
    if slot_id == "compensation" and "유지보수" in line and not find_first(line.replace("유지보수", ""), ["급여", "보수", "연봉", "월급", "시급", "임금"]):
        return False
    if slot_id == "contact_point":
        if find_first(line, ["비상연락처", "본인휴대폰", "본인 연락처"]):
            return False
        if find_first(line, ["지원서", "입사지원", "개인정보", "인적사항", "기재", "등록"]) and not find_first(line, ["문의", "담당 부서", "채용팀", "인사팀"]):
            return False
    if slot_id == "contact_point" and section.name != "문의처":
        if find_first(line, _CONTACT_NON_CHANNEL) and not find_first(line, ["문의", "연락", "전화", "담당 부서"]):
            return False
        if not find_first(line, _CONTACT_CONTEXT):
            return False
    return True


def _candidate_units(section: Section, definition: dict[str, Any], slot_id: str) -> list[tuple[int, int, str]]:
    units: list[tuple[int, int, str]] = []
    for match in find_matches(section.text, definition.get("accept_patterns", [])):
        left, right = _line_bounds(section.text, match.start(), match.end())
        line = section.text[left:right]
        heading = _heading_name(line) is not None or _compact(line) == _compact(match.group(0))
        if slot_id in {"selection_stages", "ai_disclosure"} and find_first(line, _STAGE_EVENTS):
            heading = False  # A named stage is evidence even in an unheaded list.
        if slot_id == "contact_point" and find_first(line, [r"re:0\d{1,2}[- )]\d{3,4}[- ]\d{4}", r"re:[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}"]):
            heading = False
        if heading:
            if slot_id in {"selection_stages", "ai_disclosure", "qualification_rationale"}:
                continue
            # A label can anchor a body, but a deferred/empty heading cannot.
            cursor = right + 1
            body_parts: list[str] = []
            body_start = cursor
            end = cursor
            while cursor < len(section.text) and cursor - right < 600:
                end = section.text.find("\n", cursor)
                end = len(section.text) if end == -1 else end
                body = section.text[cursor:end]
                plain = re.sub(r"<[^>]+>", "", body).strip()
                if plain:
                    if _heading_name(plain) or find_first(plain, _DEFERRED_BODY):
                        break
                    if not body_parts:
                        body_start = cursor
                    body_parts.append(body)
                    # Keep a split date range together; other label bodies need one line.
                    if slot_id != "schedule" or len(body_parts) == 2 or not plain.endswith(("~", "～", "-")):
                        break
                cursor = end + 1
            if not body_parts:
                continue
            body = "\n".join(body_parts)
            support = definition.get("accept_patterns", []) + [
                p for c in definition.get("components", []) for p in c.get("patterns", [])
            ]
            if slot_id == "schedule":
                support += _DATE_CONTENT
            if not find_first(body, support) and not (slot_id in {"preference_items", "evaluation_criteria"} and len(body.strip()) >= 4):
                continue
            if not _context_allowed(slot_id, body, section):
                continue
            units.append((body_start, end, line + "\n" + body))
        elif (_context_allowed(slot_id, line, section)
              and _context_allowed(slot_id, _evidence_line(section.text, match.start(), match.end()), section)):
            units.append((match.start(), match.end(), line))
    return units


def _component_present(slot_id: str, component: dict[str, Any], contexts: list[str]) -> bool:
    for context in contexts:
        for match in find_matches(context, component.get("patterns", [])):
            evidence = _evidence_line(context, match.start(), match.end())
            if slot_id == "schedule" and component["id"] in {"application_date", "assessment_date"}:
                if not find_first(evidence, _DATE_CONTENT):
                    left, right = _line_bounds(context, match.start(), match.end())
                    header = context[left:right]
                    body = context[right + 1:].split("\n")[0]
                    if (_compact(header) != _compact(match.group(0))
                            or not find_first(body, _DATE_CONTENT)
                            or find_first(body.strip(), _DEFERRED_BODY)):
                        continue
            if slot_id == "compensation" and component["id"] == "amount_or_range":
                benefits = find_matches(context, [r"re:(?:복지|지원비|지원금|포상|식대|경조|실비|숙박)[^.!?\n]{0,40}\d[\d,]*(?:만)?\s*원"])
                if any(b.start() <= match.start() and match.end() <= b.end() and "포함" not in b.group(0) for b in benefits):
                    continue
            return True
    return False


def source_candidate_allowed(source: str, start: int, end: int, sections: list[Section], layer: str) -> bool:
    """Reject a context reversal while still considering later real candidates."""
    line = _evidence_line(source, start, end)
    candidate = source[start:end]
    section = next((s for s in sections if s.start <= start < s.end), None)
    sensitive = ["출신학교", "출신 학교", "신체 조건", "혼인", "부모", "형제자매", "가족"]
    if find_first(candidate, sensitive):
        protective = [
            r"re:(?:수집|기재|작성)\s*(?:금지|불가|불필요|하지|받지)",
            r"re:(?:포함|제공)\s*하지",
            r"re:(?:기재|표현)[^.!?\n]{0,100}(?:평가대상에서\s*제외|부적합|탈락처리)",
            r"re:블라인드[^.!?\n]{0,80}미준수",
        ]
        local_tail = source[end:].split("\n", 1)[0].split(".", 1)[0].split(";", 1)[0].split(",", 1)[0][:60]
        direct_requirement = find_first(local_tail, [r"re:(?:필수|요구|제출해야|제출\s*필수)"])
        indirect_requirement = find_first(local_tail, [r"re:(?:수집|기재|작성|포함|제공)\s*(?:하지|받지)\s*(?:않는\s*것(?:은|이|을)?\s*(?:허용되지\s*않|허용하지\s*않|금지|불가)|않을\s*수\s*없)"])
        if find_first(line, protective) and not direct_requirement and not indirect_requirement:
            return False
    if layer == "question" and find_first(candidate, ["면접", "인터뷰"]):
        if section is not None and _is_duty_section(section):
            return False
        if find_first(line, ["인터뷰 자세히", "인터뷰 보기", "팀원 인터뷰", "현직자 인터뷰"]):
            return False
    if layer == "question" and find_first(candidate, ["북한이탈주민", "북한이탈 주민", "탈북자"]):
        row_start = source.rfind("<tr", 0, start)
        row_end = source.find("</tr>", end)
        table_preference = False
        if row_start >= 0 and row_end >= 0 and source.find("</tr>", row_start, start) == -1 and row_end - row_start < 1800:
            row = re.sub(r"<[^>]+>", "", source[row_start:row_end])
            table_preference = bool(find_first(row, ["가산점", "가점", r"re:만점의\s*\d+%", r"re:점수\s*만점의\s*\d+%"])) and not find_first(row, ["지원 불가", "채용 제외", "신원조회"])
        proof_only = find_first(line, ["등록확인서", "증명서"]) and not find_first(line, ["지원 불가", "채용 제외", "신원조회"])
        if (table_preference or proof_only or (section is not None and section.name == "우대사항")) and not find_first(line, ["제외", "불가", "제한", "신원조회"]):
            return False
    if layer == "question" and find_first(candidate, ["재직 기간", "근속 기간"]):
        if find_first(line, ["형법", "범한 자", "벌금형", "선고받"]):
            return False
    return True


def _candidate_rank(slot_id: str, context: str) -> tuple[int, int]:
    # Prefer concrete applicant evidence; markup arrows are not stage separators.
    plain = re.sub(r"<[^>]+>", "", context)
    if slot_id == "schedule":
        numeric = bool(find_first(context, _DATE_CONTENT[:2]))
        period = bool(find_first(context, ["접수 기간", "공고 기간", "모집 기간"]))
        return (2 if (numeric and period) or find_first(context, ["채용시까지", "채용 시까지"]) else int(numeric), int(bool(find_first(context, ["채용시까지", "채용 시까지", "상시모집", "상시 모집"]))))
    if slot_id == "selection_stages":
        return (int(bool(re.search(r"[→>▶]", plain))), len(set(m.group(0) for m in find_matches(plain, _STAGE_EVENTS))))
    if slot_id == "contact_point":
        return (int(bool(find_first(context, ["문의", "담당 부서", "인사팀", "채용팀"]))), 0)
    if slot_id == "evaluation_criteria":
        return (int(bool(find_first(context, ["평가 항목", "배점", "평가 기준"]))), 0)
    return (0, 0)


def extract_slots(
    text: str, sections: list[Section], slot_definitions: dict[str, Any]
) -> list[SlotStatus]:
    statuses: list[SlotStatus] = []
    for slot_id in sorted(slot_definitions):
        definition = slot_definitions[slot_id]
        preferred = [s for s in sections if s.name in definition.get("search_sections", [])]
        search_order = preferred + [s for s in sections if s not in preferred]
        candidates = [(section, unit) for section in search_order
                      for unit in _candidate_units(section, definition, slot_id)]
        contexts = [unit[2] for _, unit in candidates]
        components_found = sorted(c["id"] for c in definition.get("components", [])
                                  if _component_present(slot_id, c, contexts))
        if candidates:
            section, (start, end, _) = max(candidates, key=lambda c: _candidate_rank(slot_id, c[1][2]))
            evidence = _evidence_line(text, section.start + start, section.start + end)
            section_name = section.name
        else:
            evidence = None
            section_name = None
        statuses.append(SlotStatus(
            slot=slot_id, label=definition["label"], found=bool(candidates),
            components_found=components_found,
            components_total=len(definition.get("components", [])),
            evidence=evidence, section=section_name,
        ))
    return statuses
