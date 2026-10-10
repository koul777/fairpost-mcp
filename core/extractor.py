from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
import unicodedata
from typing import Any

from .morph import ZERO_WIDTH, find_first, find_matches
from .schema import SlotStatus


SECTION_VERSION = "sections-v6-clause-scoped-protection-duty-block"

_SENTENCE_BOUNDARIES = ".!?。！？"
_EVIDENCE_WINDOW_CODEPOINTS = 238
# Leading bullets, numbering and heading marks (●, ◆, ▶, □, ※, 【, < ...) and
# trailing colons or closing brackets are decoration around a heading name.
# web/engine.js HEADING_DECORATION must stay identical.
_HEADING_DECORATION = re.compile(r"^[\s#>*\-–—\d.()①-⑳\[\]■●○◆◇▶▷□◎※【】<]+|[\s:：\[\]】>]+$")
_WHITESPACE = re.compile(r"\s+")


SECTION_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("개요", ("채용개요", "모집개요", "공고개요", "담당업무", "주요업무", "주요 업무", "담당 업무", "업무내용", "What You'll Do", "What You’ll Do", "합류하면 함께할 업무에요", "이런 일을 해요",)),
    # 자격요건의 직무 관련성, 평가 기준, 인공지능 활용 안내, 결과 안내, 이의제기 안내 and
    # 채용서류 반환ㆍ파기 안내 are the titles of the example sentences in
    # data/posting_templates.yaml, so a pasted example forms its own section
    # instead of joining the block above it (e.g. a duty list).
    ("자격요건", ("자격요건", "지원자격", "응시자격", "필수요건", "자격조건", "필요지식", "필요 지식 및 기술", "자격조건 및 필요지식", "Required Skills", "이런 분과 함께하고 싶어요", "자격요건의 직무 관련성",)),
    ("우대사항", ("우대사항", "우대내용", "가점사항", "우대조건", "Preferred Skills", "이런 분이면 더 좋아요!", "이런 경험이 있으면 더 좋아요",)),
    ("전형절차", ("전형절차/방법", "전형절차", "전형방법", "선발절차", "채용절차", "합류 여정", "이렇게 합류해요", "전형절차 및 일정", "전형 절차 및 안내 사항", "전형절차 및 기타사항", "평가 기준", "인공지능 활용 안내",)),
    ("일정", ("전형일정", "채용일정", "일정", "접수기간", "모집기간", "공고기간", "접수 마감일", "결과 안내",)),
    ("근무조건", ("근무조건", "근로조건", "보수", "급여", "고용형태", "근무형태",)),
    ("제출서류", ("제출서류", "지원서류", "구비서류",)),
    ("유의사항", ("유의사항", "주의사항", "꼭 확인해 주세요", "이의제기 안내", "채용서류 반환ㆍ파기 안내", "채용서류 반환·파기 안내",)),
    ("문의처", ("문의처", "문의", "연락처",)),
    ("기타", ("기타",)),
)


def _normalized_heading(line: str) -> str:
    return "".join(
        unicodedata.normalize("NFKC", char) for char in line if char not in ZERO_WIDTH
    )


_SECTION_BY_COMPACT_ALIAS: dict[str, str] = {}
for _canonical, _aliases in SECTION_ALIASES:
    for _alias in _aliases:
        # setdefault preserves the original first-match behavior if aliases
        # ever overlap across canonical sections. Keys are NFKC-normalized like
        # the heading lines they are compared with (NFKC maps U+318D "ㆍ").
        _SECTION_BY_COMPACT_ALIAS.setdefault(
            _WHITESPACE.sub("", _normalized_heading(_alias)).casefold(), _canonical
        )


@dataclass(frozen=True)
class Section:
    name: str
    start: int
    end: int
    text: str


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
# A bare "인터뷰" is not hiring context by itself ("고객 인터뷰를 통해 서비스를 개선"); it
# counts through _hiring_context only in a stage context. Named interview stages do.
_HIRING_CONTEXT = ["지원자","응시자","서류전형","면접","역량검사","전형절차","채용과정","직무 인터뷰","실무 인터뷰","화상 인터뷰","문화적합성 인터뷰"]
_DUTY_CONTEXT = ["re:(?:알고리즘|임직원|직원|제품|서비스|고객).{0,80}(?:연구|개발|설계|운영|개선|평가)"]
_CONTACT_CONTEXT = ["문의","연락","인사팀","채용팀","인사부","담당 부서","전화","전자우편","re:[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}","re:0\\d{1,2}[- )]\\d{3,4}[- ]\\d{4}"]
_CONTACT_NON_CHANNEL = ["결과","통보","기재","등록","발송","지원서","모집"]
# A label body is "announced later" when it starts with 추후/차후/나중에/미정, or with 별도/상세/예정
# as a bare marker or a statement that names a deferral (안내/공지/협의/예정/미정/문의/통보/참조/
# 확인) and carries no date or amount. Real content that merely starts with an ambiguous word
# ("상세 일정: 8월 1일 ~", "별도 협의 없이 4,000만원") is kept.
# web/engine.js DEFERRED_BODY must stay identical.
_DEFERRED_BODY = ["re:^(?:(?:추후|차후|나중에|미정)[^\\n]*|(?![^\\n]*(?:(?:19|20)\\d{2}\\s*[./-]\\s*\\d{1,2}|\\d{1,2}\\s*[./]\\s*\\d{1,2}|\\d{1,4}\\s*(?:년|월|일|시|주)|\\d[\\d,.]{0,15}\\s*(?:[천백만억]\\s*)*원))(?:(?:별도|상세|예정)[^\\n]*(?:안내|공지|협의|예정|미정|문의|통보|참조|확인)[^\\n]*|예정[^\\n]{0,12}|별도|상세))$"]
# The first pattern also reads the official spaced form "2026. 8. 1." (as
# _DEFERRED_BODY does). web/engine.js DATE_CONTENT must stay identical.
_DATE_CONTENT = ["re:\\d{1,4}[./-]\\d{1,2}|(?:19|20)\\d{2}\\s*[./-]\\s*\\d{1,2}","re:\\d+\\s*(?:년|월|일|시|주)","상시","수시", "채용시까지", "채용 시까지"]
_STAGE_EVENTS = ["서류전형","면접전형","필기전형","서류접수","직무 인터뷰","실무 인터뷰","화상 인터뷰","문화적합성 인터뷰","코딩테스트","코딩 테스트","AI 역량검사","AI 면접","서류 전형","면접 전형","필기 전형", "AI 사전면접", "AI 자기소개서 평가", "서류 검토", "AI 영상면접"]
# Words that also name ordinary work ("고객 인터뷰", "민원 서류접수", "계약 서류 검토").
# They count as a selection stage only in a stage context (_stage_context) unless
# they are part of a named stage such as "실무 인터뷰".
_BARE_STAGE_TERMS = ["인터뷰", "서류접수", "서류 검토", "서류 리뷰"]
_STAGE_SECTIONS = {"전형절차", "일정"}
# Order and step markers that make a sentence a list of stages.
_STAGE_MARKERS = ["re:→|⇒|▶|▷|->|=>|>", "re:\\d\\s*단계", "re:\\d\\s*차(?![가-힣])", "re:\\d\\s*차\\s*(?:인터뷰|면접|전형|심사|평가|합격|과제|테스트|서류)", "전형", "re:(?:채용|선발|합류)\\s*(?:절차|과정|프로세스)", "re:최종\\s*합격"]
# Line markers, read on the raw line: a bullet or a list number starts an item;
# a heading mark (●, ○, □ ...) starts a heading or an item; ※ starts a note; a
# bracket or a hash starts a heading. web/engine.js LINE_MARKER must stay identical.
_LINE_MARKER = re.compile(r"^\s*(?:([-–—‐−*•·ㆍ∙‧▪◦+－＊・･])|(\d{1,2})\s*([.)．）])(?!\d)|([(（]\s*\d{1,2}\s*[)）])|([①-⑳])|([가나다라마바사아자차카타파하])\s*[.)．）]|([●○◆◇▶▷□■◎])|(※)|([\[【<#［〈《]))")
_SENTENCE_END = ".!?。다요음함임"

def _line_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    left = text.rfind("\n", 0, start) + 1
    right = text.find("\n", end)
    return left, len(text) if right == -1 else right


def _compact(value: str) -> str:
    return _WHITESPACE.sub("", _HEADING_DECORATION.sub("", _normalized_heading(value).strip())).casefold()


def _strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value)


def _line_marker(line: str) -> tuple[str, str]:
    """Return (kind, style) of a line's leading bullet, number or heading mark.

    kind is "item" for a bullet or a list number, "mark" for a mark that starts a
    heading or an item (●, ○, □ ...), "note" for ※, "bracket" for [, 【, < or #,
    and "" for a plain line. style tells markers apart ("-" and "•", "1." and "1)").
    """
    match = _LINE_MARKER.match("".join(char for char in line if char not in ZERO_WIDTH))
    if match is None:
        return "", ""
    bullet, number, number_end, paren, circled, hangul, mark, note, bracket = match.groups()
    if bullet:
        return "item", bullet
    if number:
        return "item", "1." if number_end in ".．" else "1)"
    if paren:
        return "item", "(1)"
    if circled:
        return "item", "①"
    if hangul:
        return "item", "가."
    if mark:
        return "mark", mark
    if note:
        return "note", note
    return "bracket", bracket


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _heading_like(line: str) -> bool:
    """A short line that is not a sentence, so it can title the block below it."""
    cleaned = _HEADING_DECORATION.sub("", _normalized_heading(line).strip())
    return 0 < len(cleaned) <= 30 and cleaned[-1] not in _SENTENCE_END


@lru_cache(maxsize=256)
def _duty_block_end_in(text: str) -> int:
    """Offset in a section's text where its duty list ends (0: not a duty section).

    A duty heading (담당업무, 주요 업무 ...) covers only the block directly under
    it, not everything up to the next known heading. The block ends at a blank
    line (unless the same list continues after it), or at a short title line no
    more indented than the heading that does not continue the item list: one
    with a heading mark or bracket ("● 채용 안내", "[채용 안내]"), one numbered
    like the heading ("2. 채용 안내" after "1. 담당업무"), or a plain one after
    marked items ("채용 안내" after "- 사무 보조"). Items, sentences, indented
    lines and ※ notes stay in the block. web/engine.js dutyBlockEnd must stay
    identical.
    """
    lines = text.split("\n")
    if _compact(lines[0]) not in {_compact(h) for h in _DUTY_HEADINGS}:
        return 0
    heading_kind, heading_style = _line_marker(lines[0])
    heading_indent = _indent(_strip_tags(lines[0]))
    item_style: str | None = None
    offset = len(lines[0]) + 1
    for index in range(1, len(lines)):
        line = lines[index]
        raw = _strip_tags(line)
        plain = raw.strip()
        if not plain:
            if item_style is not None:
                following = next((rest for rest in (_strip_tags(r).strip() for r in lines[index + 1:]) if rest), None)
                if not (following is not None and item_style and _line_marker(following)[1] == item_style):
                    return offset
            offset += len(line) + 1
            continue
        kind, style = _line_marker(raw)
        if item_style is None:
            item_style = style
        elif style != item_style and _indent(raw) <= heading_indent and _heading_like(plain):
            if kind in {"mark", "bracket"} or (kind == "" and item_style) or (heading_kind and style == heading_style):
                return offset
        offset += len(line) + 1
    return len(text)


def _in_duty_block(section: Section, offset: int) -> bool:
    return offset < _duty_block_end_in(section.text)


def _stage_context(section: Section | None, sentence: str) -> bool:
    """A selection-procedure section, or a sentence that orders stages or names
    another one ("서류 검토 → 인터뷰", "1차 인터뷰", "서류 검토 후 면접")."""
    if section is not None and section.name in _STAGE_SECTIONS:
        return True
    bare = {_compact(term) for term in _BARE_STAGE_TERMS}
    named = [event for event in _STAGE_EVENTS if _compact(event) not in bare]
    return bool(find_first(_strip_tags(sentence), _STAGE_MARKERS + named + ["면접"]))


def _bare_stage_match(text: str, start: int, end: int) -> bool:
    """text[start:end] is a bare stage term that is not part of a named stage."""
    bare = {_compact(term) for term in _BARE_STAGE_TERMS}
    if _compact(text[start:end]) not in bare:
        return False
    named = [event for event in _STAGE_EVENTS if _compact(event) not in bare]
    left, right = _line_bounds(text, start, end)
    return not any(left + m.start() <= start and end <= left + m.end()
                   for m in find_matches(text[left:right], named))


def _hiring_context(line: str, section: Section | None) -> bool:
    if find_first(line, _HIRING_CONTEXT):
        return True
    return bool(find_first(line, ["인터뷰"])) and _stage_context(section, line)


def _context_allowed(slot_id: str, line: str, section: Section, offset: int) -> bool:
    # Duties describe work, not the employer's own applicant assessment.
    if slot_id != "qualification_rationale" and _in_duty_block(section, offset):
        return False
    if slot_id == "selection_stages" and find_first(line, ["인터뷰 자세히", "인터뷰 보기", "팀원 인터뷰", "현직자 인터뷰"]):
        return False
    if slot_id in {"ai_disclosure", "evaluation_criteria", "selection_stages"}:
        if section.name in {"자격요건", "우대사항"} and find_first(line, ["경험", "경력", "설계", "연구", "개발", "운영"]):
            if not find_first(line, ["지원자", "응시자", "채용 과정", "전형 절차", "실시", "참여", "진행"]):
                return False
        if find_first(line, _DUTY_CONTEXT) and not _hiring_context(line, section):
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
        if (slot_id == "selection_stages"
                and _bare_stage_match(section.text, match.start(), match.end())
                and not _stage_context(section, _evidence_line(section.text, match.start(), match.end()))):
            continue  # "고객 인터뷰", "민원 서류접수": work, not an applicant stage.
        heading = _heading_name(line) is not None or _compact(line) == _compact(match.group(0))
        if slot_id in {"selection_stages", "ai_disclosure"} and (
            find_first(line, _STAGE_EVENTS)
            or (_heading_name(line) is None and _line_marker(line)[0] in {"item", "mark", "note"})
        ):
            heading = False  # A named stage, or a list item ("- 인터뷰"), is evidence.
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
            if not _context_allowed(slot_id, body, section, match.start()):
                continue
            units.append((body_start, end, line + "\n" + body))
        elif (_context_allowed(slot_id, line, section, match.start())
              and _context_allowed(slot_id, _evidence_line(section.text, match.start(), match.end()), section, match.start())):
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


# --- protective clauses ---------------------------------------------------------
# A protective verb ("기재하지 마세요", "수집 금지") silences a sensitive candidate
# only when it governs that candidate, i.e. sits in the same clause. A clause
# ends at a sentence mark, ";" or a connective ending (되나, 지만, (으)며,
# 마시고, 않으나 ...). A comma ends it too, unless it follows a conditional
# ("기재할 경우,") or joins the items of one noun list ("출신학교, 가족관계 등
# 인적사항은 기재 금지"). A connective must end a word and 고/나 need a verb
# stem before them, so 고객, 공고, 나이, 며칠 or the particle (이)나 never split.
# A penalty for leaving the item out ("미기재 시 탈락") is a requirement, not a
# protection. web/engine.js keeps the same patterns (PROTECTIVE_* constants).
_SENSITIVE_CANDIDATE = ["출신학교", "출신 학교", "신체 조건", "혼인", "부모", "형제자매", "가족"]
_NOT_AN_INFORMATION_ITEM = [r"re:가족\s*같은"]
_PROTECTIVE = [
    r"re:(?:수집|기재|작성)\s*(?:금지|불가|불필요|하지|받지)",
    r"re:(?:포함|제공)\s*하지",
    r"re:(?:기재|표현)[^.!?\n]{0,100}(?:평가대상에서\s*제외|부적합|탈락처리)",
    r"re:블라인드[^.!?\n]{0,80}미준수",
]
_PROTECTIVE_INDIFFERENT = [r"re:^\s*(?:(?:과|와|은|는|의|에)\s*)?(?:무관|관계\s*없|상관\s*없|불문|제한\s*없)"]
_PROTECTIVE_OMISSION_PENALTY = [
    r"re:(?:(?:미기재|미작성|미제출|누락)\s*(?:시|하면|할\s*경우|된\s*경우)|(?:기재|작성|제출)\s*하지\s*않(?:으면|을\s*경우|는\s*경우|은))[^.!?\n]{0,60}?(?:불이익|탈락|제외|부적합|감점)(?![^.!?\n,]{0,12}(?:없|않|아니))"
]
_PROTECTIVE_REQUIREMENT = [
    r"re:(?:필수|요구|제출해야|제출\s*필수)",
    r"re:(?:기재|작성)해야|기재하(?:십시오|세요)|기재해\s*주(?:세요|십시오)|제출하(?:십시오|세요)",
    r"re:(?:수집|기재|작성|포함|제공)\s*(?:하지|받지)\s*(?:않는\s*것(?:은|이|을)?\s*(?:허용되지\s*않|허용하지\s*않|금지|불가)|않을\s*수\s*없)",
]
_PROTECTIVE_CLAUSE_END = [
    r"re:;",
    r"re:(?:(?:하|되|않|마시|말|주시|하시|받|있|없|이)고|(?<=[가-힣])며|(?:되|으)나|(?<=[가-힣])하나|지만)(?=[\s,]|$)",
]
_PROTECTIVE_COMMA = [r"re:(?<!\d),|,(?!\d)"]
_PROTECTIVE_CONDITIONAL_TAIL = [
    r"re:(?:경우|때|으면|하면|되면|이면|라면|다면|(?:기재|작성|제출|포함|표기|노출|기입|명시|지원|위반|적발|확인|발견|누락)\s*시)[)\]）】」』\s]*$"
]
_PROTECTIVE_PREDICATE_TAIL = [
    r"re:(?:[다요음함됨임]|시오|며|고|나|지만|되|아도|어도|해도|여도|이상|이하|미만|초과|이내|우대|필수|가능|불가|금지|불필요|무관|환영|제외|요망|바람|선호|필요|요구|탈락|처리|부적합|기재|작성|제출|첨부|입력|명시|표기|확인|진행|실시|모집|채용|접수|포함|요청|\d\S*)[)\]）】」』\s]*$"
]
_PROTECTIVE_NON_LIST_HEAD = [
    r"re:^(?:\S*(?:에|에서|에게|께|께서|으로|로|부터|까지|보다|에는|에도|에선|에서는|으로는|로는)|반드시|꼭|단|다만|또한|그리고|하지만|그러나|특히|함께|바로)$"
]
_PROTECTIVE_DIRECT_GAP = [r"re:^\S*(?:\s+(?:일체|절대|절대로|모두|전혀|별도로|따로|굳이))*\s*$"]
_PROTECTIVE_TOKEN = [r"re:\S+"]
_PROTECTIVE_LIST_ITEM_TOKENS = 4


def _protective_comma_joins(left: str, right: str, item: str | None) -> bool:
    """Whether a comma keeps `left` and `right` in one clause."""
    if find_first(left, _PROTECTIVE_CONDITIONAL_TAIL):
        return True
    if find_first(left, _PROTECTIVE_PREDICATE_TAIL):
        return False
    head = find_first(right, _PROTECTIVE_TOKEN)
    if head is None or find_first(head.group(0), _PROTECTIVE_NON_LIST_HEAD):
        return False
    return item is None or len(find_matches(item, _PROTECTIVE_TOKEN)) <= _PROTECTIVE_LIST_ITEM_TOKENS


def _protective_clause(source: str, start: int, end: int) -> tuple[int, int]:
    """Source span of the clause around source[start:end], inside its evidence window."""
    line_start = source.rfind("\n", 0, start) + 1
    line_end = source.find("\n", end)
    line_end = len(source) if line_end == -1 else line_end
    before = _sentence_marks(source, line_start, start)
    after = _sentence_marks(source, end, line_end)
    left = before[-1] + 1 if before else line_start
    right = after[0] + 1 if after else line_end
    if right - left > _EVIDENCE_WINDOW_CODEPOINTS:
        # Never look further than the evidence window the reviewer is shown.
        window_start = max(0, start - left - 96)
        window_end = min(right - left, max(end - left + 96, window_start + _EVIDENCE_WINDOW_CODEPOINTS))
        window_start = max(0, window_end - _EVIDENCE_WINDOW_CODEPOINTS)
        left, right = left + window_start, left + window_end
    sentence = source[left:right]
    s, e = start - left, end - left
    ends = find_matches(sentence, _PROTECTIVE_CLAUSE_END)
    lo = max((m.end() for m in ends if m.end() <= s), default=0)
    hi = min((m.end() for m in ends if m.start() >= e), default=len(sentence))
    commas = [m.start() for m in find_matches(sentence, _PROTECTIVE_COMMA) if lo <= m.start() < hi and not s <= m.start() < e]
    following = [c for c in commas if c >= e]
    previous: int | None = None
    for index, comma in enumerate(following):
        next_cut = following[index + 1] if index + 1 < len(following) else hi
        segment_start = s if previous is None else previous + 1
        item_start = e if previous is None else previous + 1
        if not _protective_comma_joins(sentence[segment_start:comma], sentence[comma + 1:next_cut], sentence[item_start:comma]):
            hi = comma
            break
        previous = comma
    preceding = [c for c in commas if c < s]
    for index in range(len(preceding) - 1, -1, -1):
        comma = preceding[index]
        later = [c for c in commas if c > comma]
        next_cut = later[0] if later else hi
        earlier = preceding[index - 1] if index else None
        segment = sentence[(lo if earlier is None else earlier + 1):comma]
        # The first list item may carry a lead-in ("수집 금지 항목: 부모 직업").
        if not _protective_comma_joins(segment, sentence[comma + 1:next_cut], None if earlier is None else segment):
            lo = comma + 1
            break
    return left + lo, left + hi


def _protective_clause_governs(source: str, start: int, end: int) -> bool:
    """Whether a protective expression governs the sensitive candidate source[start:end]."""
    clause_start, clause_end = _protective_clause(source, start, end)
    if find_first(source[end:clause_end], _PROTECTIVE_INDIFFERENT):
        return True  # "신체 조건과 무관하게"
    clause = source[clause_start:clause_end]
    if find_first(clause, _PROTECTIVE_OMISSION_PENALTY):
        return False  # "부모 직업 미기재 시 탈락" requires the item.
    if not find_first(clause, _PROTECTIVE):
        return False
    tail = source[end:clause_end][:60]
    requirement = find_first(tail, _PROTECTIVE_REQUIREMENT)
    if requirement is None:
        return True
    # "출신학교 기재 금지 상태로 부모 학력을 기재해 주세요": a protection that
    # directly follows the candidate (only a particle or adverb between) governs
    # it, and the later requirement belongs to another item.
    following = find_first(tail, _PROTECTIVE)
    return (following is not None and following.start() < requirement.start()
            and find_first(tail[:following.start()], _PROTECTIVE_DIRECT_GAP) is not None)


def source_candidate_allowed(source: str, start: int, end: int, sections: list[Section], layer: str) -> bool:
    """Reject a context reversal while still considering later real candidates."""
    line = _evidence_line(source, start, end)
    candidate = source[start:end]
    section = next((s for s in sections if s.start <= start < s.end), None)
    if (find_first(candidate, _SENSITIVE_CANDIDATE) and not find_first(candidate, _NOT_AN_INFORMATION_ITEM)
            and _protective_clause_governs(source, start, end)):
        return False
    if layer == "question" and find_first(candidate, ["면접", "인터뷰"]):
        if section is not None and _in_duty_block(section, start - section.start):
            return False
        if find_first(line, ["인터뷰 자세히", "인터뷰 보기", "팀원 인터뷰", "현직자 인터뷰"]):
            return False
        # "고객 인터뷰를 통해 서비스를 개선": an unheaded duty, not an applicant interview.
        if (_bare_stage_match(source, start, end) and find_first(line, _DUTY_CONTEXT)
                and not _hiring_context(line, section)):
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
