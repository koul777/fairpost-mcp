(function (global) {
  "use strict";

  const DISCLAIMER =
    "이 결과는 점검 참고자료이며 공정성 여부에 대한 판정이나 법률 자문이 아닙니다. " +
    "확인되지 않은 항목은 해당 절차가 없다는 뜻이 아니라 이 공고문에서 발견되지 않았다는 뜻입니다.";

  const SECTION_ALIASES = [
  [
    "개요",
    [
      "채용개요",
      "모집개요",
      "공고개요",
      "담당업무",
      "주요업무",
      "주요 업무",
      "담당 업무",
      "업무내용",
      "What You'll Do",
      "What You’ll Do",
      "합류하면 함께할 업무에요",
      "이런 일을 해요"
    ]
  ],
  [
    "자격요건",
    [
      "자격요건",
      "지원자격",
      "응시자격",
      "필수요건",
      "자격조건",
      "필요지식",
      "필요 지식 및 기술",
      "자격조건 및 필요지식",
      "Required Skills",
      "이런 분과 함께하고 싶어요"
    ]
  ],
  [
    "우대사항",
    [
      "우대사항",
      "우대내용",
      "가점사항",
      "우대조건",
      "Preferred Skills",
      "이런 분이면 더 좋아요!",
      "이런 경험이 있으면 더 좋아요"
    ]
  ],
  [
    "전형절차",
    [
      "전형절차/방법",
      "전형절차",
      "전형방법",
      "선발절차",
      "채용절차",
      "합류 여정",
      "이렇게 합류해요",
      "전형절차 및 일정",
      "전형 절차 및 안내 사항",
      "전형절차 및 기타사항"
    ]
  ],
  [
    "일정",
    [
      "전형일정",
      "채용일정",
      "일정", "접수기간", "모집기간", "공고기간", "접수 마감일"
    ]
  ],
  [
    "근무조건",
    [
      "근무조건",
      "근로조건",
      "보수",
      "급여",
      "고용형태",
      "근무형태"
    ]
  ],
  [
    "제출서류",
    [
      "제출서류",
      "지원서류",
      "구비서류"
    ]
  ],
  [
    "유의사항",
    [
      "유의사항",
      "주의사항",
      "꼭 확인해 주세요"
    ]
  ],
  [
    "문의처",
    [
      "문의처",
      "문의",
      "연락처"
    ]
  ],
  [
    "기타",
    [
      "기타"
    ]
  ]
];
  const MORPH_REWRITES = [
    ["않습니다", "않음"],
    ["있으신", "있는"],
    ["하시는", "하는"],
    ["합니다", "함"],
    ["하신", "한"],
    ["이신", "인"],
    ["으신", "은"],
  ];
  const MORPH_CACHE = new Map();
  const MATCH_CACHE = new Map();
  const ZERO_WIDTH = new Set(["\u200b", "\u200c", "\u200d", "\ufeff"]);

  // The Python core is the reference. Its str.isspace(), str.strip() and re
  // "\s" agree on this set, which differs from ECMAScript whitespace: it adds
  // U+001C-U+001F and U+0085 and leaves out U+FEFF.
  const PY_SPACE_CLASS =
    "\\t\\n\\v\\f\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
  // The exact complement, for "\S" inside a character class such as [\s\S].
  const PY_NON_SPACE_CLASS =
    "\\0-\\x08\\x0e-\\x1b\\x21-\\x84\\x86-\\x9f\\xa1-\\u167f\\u1681-\\u1fff" +
    "\\u200b-\\u2027\\u202a-\\u202e\\u2030-\\u205e\\u2060-\\u2fff\\u3001-\\u{10ffff}";
  const PYTHON_WHITESPACE_RUN = new RegExp(`([${PY_SPACE_CLASS}]+)`, "u");
  // Every member is below U+3001, so a one-off scan builds an exact lookup.
  const PYTHON_WHITESPACE = (() => {
    const member = new RegExp(`^[${PY_SPACE_CLASS}]$`, "u");
    const characters = new Set();
    for (let code = 0; code <= 0x3000; code += 1) {
      const character = String.fromCharCode(code);
      if (member.test(character)) characters.add(character);
    }
    return characters;
  })();
  // str.splitlines() boundaries; "\r\n" counts as a single boundary.
  const PYTHON_LINE_BREAKS = new Set([
    "\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029",
  ]);
  const PYTHON_REGEX_CACHE = new Map();

  function isPythonWhitespace(character) {
    return PYTHON_WHITESPACE.has(character);
  }

  function trimPythonWhitespace(value) {
    let start = 0;
    let end = value.length;
    while (start < end && isPythonWhitespace(value[start])) {
      start += 1;
    }
    while (end > start && isPythonWhitespace(value[end - 1])) {
      end -= 1;
    }
    return value.slice(start, end);
  }

  // Mirrors str.splitlines(keepends=True).
  function splitPythonLines(text) {
    const lines = [];
    let start = 0;
    for (let index = 0; index < text.length; index += 1) {
      if (!PYTHON_LINE_BREAKS.has(text[index])) continue;
      if (text[index] === "\r" && text[index + 1] === "\n") index += 1;
      lines.push(text.slice(start, index + 1));
      start = index + 1;
    }
    if (start < text.length) lines.push(text.slice(start));
    return lines;
  }

  // The whole code point that ends just before code-unit index `index`.
  function codePointEndingAt(text, index) {
    const low = text.charCodeAt(index - 1);
    if (low >= 0xdc00 && low <= 0xdfff && index >= 2) {
      const high = text.charCodeAt(index - 2);
      if (high >= 0xd800 && high <= 0xdbff) return text.slice(index - 2, index);
    }
    return text[index - 1];
  }

  // Mirrors text.rfind("\n", 0, offset): only newlines strictly before offset.
  function lastNewlineBefore(text, offset) {
    return offset > 0 ? text.lastIndexOf("\n", offset - 1) : -1;
  }

  // Rewrites a Python `re` pattern (no flags other than IGNORECASE) into an
  // ECMAScript "u" pattern with the same meaning for the constructs the
  // dictionary uses: "\s"/"\S" use Python whitespace, "\d"/"\D" are Unicode
  // decimal digits, "." stops only at "\n" (not "\r", U+2028 or U+2029), and
  // "$" also matches before a final "\n".
  function pythonRegexSource(source) {
    if (PYTHON_REGEX_CACHE.has(source)) return PYTHON_REGEX_CACHE.get(source);
    let out = "";
    let inClass = false;
    for (let index = 0; index < source.length; index += 1) {
      const character = source[index];
      if (character === "\\" && index + 1 < source.length) {
        const escaped = source[index + 1];
        index += 1;
        if (escaped === "s") {
          out += inClass ? PY_SPACE_CLASS : `[${PY_SPACE_CLASS}]`;
        } else if (escaped === "S") {
          out += inClass ? PY_NON_SPACE_CLASS : `[^${PY_SPACE_CLASS}]`;
        } else if (escaped === "d") {
          out += "\\p{Nd}";
        } else if (escaped === "D") {
          out += "\\P{Nd}";
        } else {
          out += character + escaped;
        }
        continue;
      }
      if (inClass) {
        if (character === "]") inClass = false;
        out += character;
        continue;
      }
      if (character === "[") {
        inClass = true;
        out += character;
        if (source[index + 1] === "^") {
          out += "^";
          index += 1;
        }
        // Python reads a leading "]" as a literal member, not an empty class.
        if (source[index + 1] === "]") {
          out += "\\]";
          index += 1;
        }
        continue;
      }
      if (character === ".") out += "[^\\n]";
      else if (character === "$") out += "(?=\\n?$)";
      else out += character;
    }
    if (PYTHON_REGEX_CACHE.size >= 1024) {
      PYTHON_REGEX_CACHE.delete(PYTHON_REGEX_CACHE.keys().next().value);
    }
    PYTHON_REGEX_CACHE.set(source, out);
    return out;
  }

  function pythonRegex(source, flags) {
    return new RegExp(pythonRegexSource(source), flags);
  }

  function normalize(text) {
    return String(text);
  }

  function escapeRegex(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function patternToRegex(pattern) {
    if (pattern.startsWith("re:")) {
      return pythonRegex(pattern.slice(3), "giu");
    }
    const expression = pattern
      .normalize("NFC")
      .split(PYTHON_WHITESPACE_RUN)
      .map((part) =>
        part && trimPythonWhitespace(part) === "" ? "\\s*" : escapeRegex(part)
      )
      .join("");
    return pythonRegex(expression, "giu");
  }

  function morphTextWithOffsets(text) {
    if (MORPH_CACHE.has(text)) {
      const cached = MORPH_CACHE.get(text);
      MORPH_CACHE.delete(text);
      MORPH_CACHE.set(text, cached);
      return cached;
    }
    let normalized = "";
    const starts = [];
    const ends = [];
    let cursor = 0;
    while (cursor < text.length) {
      const rewrite = MORPH_REWRITES.find(([source]) =>
        text.startsWith(source, cursor)
      );
      if (!rewrite) {
        normalized += text[cursor];
        starts.push(cursor);
        ends.push(cursor + 1);
        cursor += 1;
        continue;
      }
      const [source, target] = rewrite;
      const sourceEnd = cursor + source.length;
      normalized += target;
      for (let index = 0; index < target.length; index += 1) {
        starts.push(cursor);
        ends.push(sourceEnd);
      }
      cursor = sourceEnd;
    }
    const result = { normalized, starts, ends };
    if (MORPH_CACHE.size >= 512) {
      MORPH_CACHE.delete(MORPH_CACHE.keys().next().value);
    }
    MORPH_CACHE.set(text, result);
    return result;
  }

  function morphPlain(text) {
    return morphTextWithOffsets(text).normalized;
  }

  function normalizedTextWithOffsets(text) {
    let normalized = "";
    const starts = [];
    const ends = [];
    let cursor = 0;
    for (const character of text) {
      if (!ZERO_WIDTH.has(character)) {
        const replacement = /[\r\n\t]/u.test(character)
          ? character
          : isPythonWhitespace(character)
            ? " "
            : character.normalize("NFKC");
        normalized += replacement;
        for (let index = 0; index < replacement.length; index += 1) {
          starts.push(cursor);
          ends.push(cursor + character.length);
        }
      }
      cursor += character.length;
    }
    return { normalized, starts, ends };
  }

  function matchTextWithOffsets(text) {
    if (MATCH_CACHE.has(text)) {
      const cached = MATCH_CACHE.get(text);
      MATCH_CACHE.delete(text);
      MATCH_CACHE.set(text, cached);
      return cached;
    }
    const base = normalizedTextWithOffsets(text);
    const morph = morphTextWithOffsets(base.normalized);
    const result =
      morph.normalized === base.normalized
        ? base
        : {
            normalized: morph.normalized,
            starts: morph.starts.map((index) => base.starts[index]),
            ends: morph.ends.map((index) => base.ends[index - 1]),
          };
    if (MATCH_CACHE.size >= 512) {
      MATCH_CACHE.delete(MATCH_CACHE.keys().next().value);
    }
    MATCH_CACHE.set(text, result);
    return result;
  }

  function matchPlain(text) {
    return morphPlain(normalizedTextWithOffsets(text).normalized);
  }

  function findMatches(text, patterns) {
    const matches = [];
    const seen = new Set();
    const normalizedView = normalizedTextWithOffsets(text);
    const matchView = matchTextWithOffsets(text);
    const needsMatchView = matchView.normalized !== text;
    patterns.forEach((pattern) => {
      const isRegex = pattern.startsWith("re:");
      const regex = patternToRegex(pattern);
      let match;
      const exactText = isRegex && normalizedView.normalized !== text ? null : text;
      while (exactText !== null && (match = regex.exec(exactText)) !== null) {
        const item = {
          start: match.index,
          end: match.index + match[0].length,
          text: match[0],
        };
        const key = `${item.start}:${item.end}:${item.text.toLocaleLowerCase("ko")}`;
        if (!seen.has(key)) {
          matches.push(item);
          seen.add(key);
        }
        if (match[0].length === 0) regex.lastIndex += 1;
      }
      let searchView;
      if (isRegex) {
        if (normalizedView.normalized === text) return;
        searchView = normalizedView;
      } else {
        if (!needsMatchView) return;
        searchView = matchView;
      }
      // Keep regex exclusions on the same NFKC/zero-width-normalized view as
      // literal candidates so protective wording cannot become a false
      // positive merely because it contains compatibility or invisible text.
      const normalizedPattern = isRegex ? pattern : matchPlain(pattern);
      const normalizedRegex = patternToRegex(normalizedPattern);
      while ((match = normalizedRegex.exec(searchView.normalized)) !== null) {
        if (match[0].length === 0) {
          normalizedRegex.lastIndex += 1;
          continue;
        }
        const start = searchView.starts[match.index];
        const end = searchView.ends[match.index + match[0].length - 1];
        const item = { start, end, text: text.slice(start, end) };
        const key = `${start}:${end}:${item.text.toLocaleLowerCase("ko")}`;
        if (!seen.has(key)) {
          matches.push(item);
          seen.add(key);
        }
      }
    });
    return matches.sort(
      (a, b) =>
        a.start - b.start ||
        a.end - b.end ||
        a.text.localeCompare(b.text, "ko")
    );
  }

  function findFirst(text, patterns) {
    return findMatches(text, patterns)[0] || null;
  }

  function isExcluded(text, match, excludes) {
    return excludes.some((exclusion) => {
      if (
        exclusion.candidate &&
        !findFirst(match.text, [String(exclusion.candidate)])
      ) {
        return false;
      }
      const window = Number(exclusion.window || 0);
      const left = moveCodePointsLeft(text, match.start, window);
      const right = moveCodePointsRight(text, match.end, window);
      const termMatches = findMatches(text.slice(left, right), [
        String(exclusion.term),
      ]);
      if (exclusion.overlap_candidate) {
        const relativeStart = match.start - left;
        const relativeEnd = match.end - left;
        return termMatches.some(
          (item) => item.start < relativeEnd && item.end > relativeStart
        );
      }
      return termMatches.length > 0;
    });
  }

  // Same pattern as core.extractor._HEADING_DECORATION, with Python semantics.
  const HEADING_DECORATION = pythonRegex(
    "^[\\s#>*\\-–—\\d.()①-⑳\\[\\]■]+|[\\s:：\\[\\]]+$",
    "gu"
  );
  const WHITESPACE_RUN = pythonRegex("\\s+", "gu");

  function normalizedHeading(line) {
    return Array.from(line).filter((ch) => !ZERO_WIDTH.has(ch)).map((ch) => ch.normalize("NFKC")).join("");
  }

  function stripHeadingDecoration(value) {
    return trimPythonWhitespace(normalizedHeading(value)).replace(
      HEADING_DECORATION,
      ""
    );
  }

  function compactHeading(value) {
    return stripHeadingDecoration(value).replace(WHITESPACE_RUN, "").toLowerCase();
  }

  function headingName(line) {
    const cleaned = stripHeadingDecoration(line);
    if (!cleaned || Array.from(cleaned).length > 30) return null;
    const compact = compactHeading(cleaned);
    if (compact.endsWith("합류여정")) return "전형절차";
    for (const [canonical, aliases] of SECTION_ALIASES) {
      if (aliases.some((alias) => compact === compactHeading(alias))) return canonical;
    }
    return null;
  }

  function splitSections(text) {
    const headings = [];
    let cursor = 0;
    // Python splits headings with str.splitlines(), so a form feed, lone CR,
    // U+2028 and the other Unicode line boundaries also end a heading line.
    splitPythonLines(text).forEach((line) => {
      const name = headingName(line);
      if (name) headings.push([name, cursor]);
      cursor += line.length;
    });
    if (!headings.length) {
      return [{ name: "전체", start: 0, end: text.length, text }];
    }
    const sections = [];
    if (headings[0][1] > 0) {
      sections.push({
        name: "전체",
        start: 0,
        end: headings[0][1],
        text: text.slice(0, headings[0][1]),
      });
    }
    headings.forEach(([name, start], index) => {
      const end =
        index + 1 < headings.length ? headings[index + 1][1] : text.length;
      sections.push({ name, start, end, text: text.slice(start, end) });
    });
    return sections;
  }

  function sectionAt(sections, offset) {
    const section = sections.find(
      (candidate) => candidate.start <= offset && offset < candidate.end
    );
    return section ? section.name : "전체";
  }

  function codePointOffset(text, codeUnitOffset) {
    return Array.from(text.slice(0, codeUnitOffset)).length;
  }

  function moveCodePointsLeft(text, offset, count) {
    let cursor = offset;
    for (let moved = 0; moved < count && cursor > 0; moved += 1) {
      cursor -= 1;
      const current = text.charCodeAt(cursor);
      if (current >= 0xdc00 && current <= 0xdfff && cursor > 0) {
        const previous = text.charCodeAt(cursor - 1);
        if (previous >= 0xd800 && previous <= 0xdbff) cursor -= 1;
      }
    }
    return cursor;
  }

  function moveCodePointsRight(text, offset, count) {
    let cursor = offset;
    for (let moved = 0; moved < count && cursor < text.length; moved += 1) {
      const current = text.charCodeAt(cursor);
      if (
        current >= 0xd800 &&
        current <= 0xdbff &&
        cursor + 1 < text.length
      ) {
        const next = text.charCodeAt(cursor + 1);
        cursor += next >= 0xdc00 && next <= 0xdfff ? 2 : 1;
      } else {
        cursor += 1;
      }
    }
    return cursor;
  }

  function codePointWindow(text, match, window) {
    const left = moveCodePointsLeft(text, match.start, window);
    const right = moveCodePointsRight(text, match.end, window);
    return text.slice(left, right);
  }

  function evidenceLine(text, start, end) {
    const lineStart = lastNewlineBefore(text, start) + 1;
    const next = text.indexOf("\n", end);
    const lineEnd = next === -1 ? text.length : next;
    const boundaries = [".", "!", "?", "。", "！", "？"];
    const marks = [];
    for (let i = lineStart; i < lineEnd; i += 1) {
      // Python reads text[i - 1] as a whole code point, so an astral digit
      // (e.g. U+1D7CF) before "." must be tested as its surrogate pair.
      if (boundaries.includes(text[i]) && !(text[i] === "." && i > 0 && (/^\p{Nd}$/u.test(codePointEndingAt(text, i)) || (/[A-Za-z0-9]/u.test(text[i - 1]) && /[A-Za-z0-9]/u.test(text[i + 1] || ""))))) marks.push(i);
    }
    const before = marks.filter((i) => i < start);
    const after = marks.filter((i) => i >= end);
    const segmentStart = before.length ? before[before.length - 1] + 1 : lineStart;
    const segmentEnd = after.length ? after[0] + 1 : lineEnd;
    const segment = text.slice(segmentStart, segmentEnd);
    const codePoints = Array.from(segment);
    const limit = 238;
    if (codePoints.length <= limit) {
      return trimPythonWhitespace(segment);
    }
    const relativeStart = Array.from(text.slice(segmentStart, start)).length;
    const relativeEnd = Array.from(text.slice(segmentStart, end)).length;
    let windowStart = Math.max(0, relativeStart - 96);
    const windowEnd = Math.min(
      codePoints.length,
      Math.max(relativeEnd + 96, windowStart + limit)
    );
    windowStart = Math.max(0, windowEnd - limit);
    const evidence = trimPythonWhitespace(
      codePoints.slice(windowStart, windowEnd).join("")
    );
    return `${windowStart ? "…" : ""}${evidence}${
      windowEnd < codePoints.length ? "…" : ""
    }`;
  }

  const DUTY_HEADINGS = ["담당업무","주요업무","업무내용","What You'll Do","What You’ll Do","합류하면 함께할 업무에요","이런 일을 해요"];
  const HIRING_CONTEXT = ["지원자","응시자","서류전형","면접","인터뷰","역량검사","전형절차","채용과정"];
  const DUTY_CONTEXT = ["re:(?:알고리즘|임직원|직원|제품|서비스|고객).{0,80}(?:연구|개발|설계|운영|개선|평가)"];
  const CONTACT_CONTEXT = ["문의","연락","인사팀","채용팀","인사부","담당 부서","전화","전자우편","re:[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}","re:0\\d{1,2}[- )]\\d{3,4}[- ]\\d{4}"];
  const CONTACT_NON_CHANNEL = ["결과","통보","기재","등록","발송","지원서","모집"];
  // Keep identical to _DEFERRED_BODY in core/extractor.py (see the comment there).
  const DEFERRED_BODY = ["re:^(?:(?:추후|차후|나중에|미정)[^\\n]*|(?![^\\n]*(?:(?:19|20)\\d{2}\\s*[./-]\\s*\\d{1,2}|\\d{1,2}\\s*[./]\\s*\\d{1,2}|\\d{1,4}\\s*(?:년|월|일|시|주)|\\d[\\d,.]{0,15}\\s*(?:[천백만억]\\s*)*원))(?:(?:별도|상세|예정)[^\\n]*(?:안내|공지|협의|예정|미정|문의|통보|참조|확인)[^\\n]*|예정[^\\n]{0,12}|별도|상세))$"];
  const DATE_CONTENT = ["re:\\d{1,4}[./-]\\d{1,2}","re:\\d+\\s*(?:년|월|일|시|주)","상시","수시", "채용시까지", "채용 시까지"];
  const STAGE_EVENTS = ["서류전형","면접전형","필기전형","서류접수","직무 인터뷰","실무 인터뷰","화상 인터뷰","문화적합성 인터뷰","코딩테스트","코딩 테스트","AI 역량검사","AI 면접","서류 전형","면접 전형","필기 전형", "AI 사전면접", "AI 자기소개서 평가", "서류 검토"];
  const ATTACHMENT_SUFFIX = pythonRegex("\\.(?:pdf|hwpx?|hml|docx?|zip)\\s*$", "iu");

  function lineBounds(text, start, end) {
    const left = lastNewlineBefore(text, start) + 1;
    const right = text.indexOf("\n", end);
    return [left, right === -1 ? text.length : right];
  }

  function isDutySection(section) {
    const first = section.text.split("\n", 1)[0];
    return DUTY_HEADINGS.some((h) => compactHeading(h) === compactHeading(first));
  }

  function contextAllowed(slotId, line, section) {
    if (slotId !== "qualification_rationale" && isDutySection(section)) return false;
    if (slotId === "selection_stages" && findFirst(line, ["인터뷰 자세히", "인터뷰 보기", "팀원 인터뷰", "현직자 인터뷰"])) return false;
    if (["ai_disclosure", "evaluation_criteria", "selection_stages"].includes(slotId)) {
      if (["자격요건", "우대사항"].includes(section.name) && findFirst(line, ["경험", "경력", "설계", "연구", "개발", "운영"]) && !findFirst(line, ["지원자", "응시자", "채용 과정", "전형 절차", "실시", "참여", "진행"])) return false;
      if (findFirst(line, DUTY_CONTEXT) && !findFirst(line, HIRING_CONTEXT)) return false;
    }
    if (slotId === "qualification_rationale" && ATTACHMENT_SUFFIX.test(line)) return false;
    if (slotId === "compensation" && line.includes("유지보수") && !findFirst(line.replaceAll("유지보수", ""), ["급여", "보수", "연봉", "월급", "시급", "임금"])) return false;
    if (slotId === "contact_point") {
      if (findFirst(line, ["비상연락처", "본인휴대폰", "본인 연락처"])) return false;
      if (findFirst(line, ["지원서", "입사지원", "개인정보", "인적사항", "기재", "등록"]) && !findFirst(line, ["문의", "담당 부서", "채용팀", "인사팀"])) return false;
    }
    if (slotId === "contact_point" && section.name !== "문의처") {
      if (findFirst(line, CONTACT_NON_CHANNEL) && !findFirst(line, ["문의", "연락", "전화", "담당 부서"])) return false;
      if (!findFirst(line, CONTACT_CONTEXT)) return false;
    }
    return true;
  }

  function candidateUnits(section, definition, slotId) {
    const units = [];
    for (const match of findMatches(section.text, definition.accept_patterns || [])) {
      const [left, right] = lineBounds(section.text, match.start, match.end);
      const line = section.text.slice(left, right);
      let heading = headingName(line) !== null || compactHeading(line) === compactHeading(match.text);
      if (["selection_stages", "ai_disclosure"].includes(slotId) && findFirst(line, STAGE_EVENTS)) heading = false;
      if (slotId === "contact_point" && findFirst(line, ["re:0\\d{1,2}[- )]\\d{3,4}[- ]\\d{4}", "re:[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}"])) heading = false;
      if (heading) {
        if (["selection_stages", "ai_disclosure", "qualification_rationale"].includes(slotId)) continue;
        let cursor = right + 1;
        let end = cursor;
        let bodyStart = cursor;
        const bodyParts = [];
        while (cursor < section.text.length && Array.from(section.text.slice(right, cursor)).length < 600) {
          end = section.text.indexOf("\n", cursor);
          if (end === -1) end = section.text.length;
          const body = section.text.slice(cursor, end);
          const plain = trimPythonWhitespace(body.replace(/<[^>]+>/gu, ""));
          if (plain) {
            if (headingName(plain) || findFirst(plain, DEFERRED_BODY)) break;
            if (!bodyParts.length) bodyStart = cursor;
            bodyParts.push(body);
            if (slotId !== "schedule" || bodyParts.length === 2 || !/[~～-]$/u.test(plain)) break;
          }
          cursor = end + 1;
        }
        if (!bodyParts.length) continue;
        const body = bodyParts.join("\n");
        const support = [...(definition.accept_patterns || []), ...(definition.components || []).flatMap((c) => c.patterns || [])];
        if (slotId === "schedule") support.push(...DATE_CONTENT);
        if (!findFirst(body, support) && !(["preference_items", "evaluation_criteria"].includes(slotId) && Array.from(trimPythonWhitespace(body)).length >= 4)) continue;
        if (!contextAllowed(slotId, body, section)) continue;
        units.push({start:bodyStart, end, context:line + "\n" + body});
      } else if (contextAllowed(slotId, line, section) && contextAllowed(slotId, evidenceLine(section.text, match.start, match.end), section)) {
        units.push({start:match.start, end:match.end, context:line});
      }
    }
    return units;
  }

  function componentPresent(slotId, component, contexts) {
    return contexts.some((context) => findMatches(context, component.patterns || []).some((match) => {
      const evidence = evidenceLine(context, match.start, match.end);
      if (slotId === "schedule" && ["application_date", "assessment_date"].includes(component.id) && !findFirst(evidence, DATE_CONTENT)) {
        const [left, right] = lineBounds(context, match.start, match.end);
        const header = context.slice(left, right);
        const body = context.slice(right + 1).split("\n")[0];
        if (compactHeading(header) !== compactHeading(match.text) || !findFirst(body, DATE_CONTENT) || findFirst(trimPythonWhitespace(body), DEFERRED_BODY)) return false;
      }
      if (slotId === "compensation" && component.id === "amount_or_range") {
        const benefits = findMatches(context, ["re:(?:복지|지원비|지원금|포상|식대|경조|실비|숙박)[^.!?\\n]{0,40}\\d[\\d,]*(?:만)?\\s*원"]);
        if (benefits.some((b) => b.start <= match.start && match.end <= b.end && !b.text.includes("포함"))) return false;
      }
      return true;
    }));
  }

  function sourceCandidateAllowed(source, match, sections, layer) {
    const line = evidenceLine(source, match.start, match.end);
    const section = sections.find((s) => s.start <= match.start && match.start < s.end);
    if (findFirst(match.text, ["출신학교", "출신 학교", "신체 조건", "혼인", "부모", "형제자매", "가족"])) {
      const protective = [
        "re:(?:수집|기재|작성)\\s*(?:금지|불가|불필요|하지|받지)",
        "re:(?:포함|제공)\\s*하지",
        "re:(?:기재|표현)[^.!?\\n]{0,100}(?:평가대상에서\\s*제외|부적합|탈락처리)",
        "re:블라인드[^.!?\\n]{0,80}미준수",
      ];
      const localTail = Array.from(source.slice(match.end).split("\n", 1)[0].split(".", 1)[0].split(";", 1)[0].split(",", 1)[0]).slice(0, 60).join("");
      const directRequirement = findFirst(localTail, ["re:(?:필수|요구|제출해야|제출\\s*필수)"]);
      const indirectRequirement = findFirst(localTail, ["re:(?:수집|기재|작성|포함|제공)\\s*(?:하지|받지)\\s*(?:않는\\s*것(?:은|이|을)?\\s*(?:허용되지\\s*않|허용하지\\s*않|금지|불가)|않을\\s*수\\s*없)"]);
      if (findFirst(line, protective) && !directRequirement && !indirectRequirement) return false;
    }
    if (layer === "question" && findFirst(match.text, ["면접", "인터뷰"])) {
      if (section && isDutySection(section)) return false;
      if (findFirst(line, ["인터뷰 자세히", "인터뷰 보기", "팀원 인터뷰", "현직자 인터뷰"])) return false;
    }
    if (layer === "question" && findFirst(match.text, ["북한이탈주민", "북한이탈 주민", "탈북자"])) {
      const rowStart = source.lastIndexOf("<tr", match.start);
      const rowEnd = source.indexOf("</tr>", match.end);
      let tablePreference = false;
      if (rowStart >= 0 && rowEnd >= 0 && !source.slice(rowStart, match.start).includes("</tr>") && Array.from(source.slice(rowStart, rowEnd)).length < 1800) {
        const row = source.slice(rowStart, rowEnd).replace(/<[^>]+>/gu, "");
        tablePreference = Boolean(findFirst(row, ["가산점", "가점", "re:만점의\\s*\\d+%", "re:점수\\s*만점의\\s*\\d+%"])) && !findFirst(row, ["지원 불가", "채용 제외", "신원조회"]);
      }
      const proofOnly = findFirst(line, ["등록확인서", "증명서"]) && !findFirst(line, ["지원 불가", "채용 제외", "신원조회"]);
      if ((tablePreference || proofOnly || section?.name === "우대사항") && !findFirst(line, ["제외", "불가", "제한", "신원조회"])) return false;
    }
    if (layer === "question" && findFirst(match.text, ["재직 기간", "근속 기간"]) && findFirst(line, ["형법", "범한 자", "벌금형", "선고받"])) return false;
    return true;
  }

  function candidateRank(slotId, context) {
    const plain = context.replace(/<[^>]+>/gu, "");
    if (slotId === "schedule") {
      const numeric = Boolean(findFirst(context, DATE_CONTENT.slice(0, 2)));
      const period = Boolean(findFirst(context, ["접수 기간", "공고 기간", "모집 기간"]));
      return [(numeric && period) || findFirst(context, ["채용시까지", "채용 시까지"]) ? 2 : Number(numeric), Number(Boolean(findFirst(context, ["채용시까지", "채용 시까지", "상시모집", "상시 모집"])) )];
    }
    if (slotId === "selection_stages") return [Number(/[→>▶]/u.test(plain)), new Set(findMatches(plain, STAGE_EVENTS).map((m) => m.text)).size];
    if (slotId === "contact_point") return [Number(Boolean(findFirst(context, ["문의", "담당 부서", "인사팀", "채용팀"]))), 0];
    if (slotId === "evaluation_criteria") return [Number(Boolean(findFirst(context, ["평가 항목", "배점", "평가 기준"]))), 0];
    return [0, 0];
  }

  function extractSlots(text, sections, definitions) {
    return Object.keys(definitions).sort().map((slotId) => {
      const definition = definitions[slotId];
      const preferred = sections.filter((s) => (definition.search_sections || []).includes(s.name));
      const searchOrder = preferred.concat(sections.filter((s) => !preferred.includes(s)));
      const candidates = searchOrder.flatMap((section) => candidateUnits(section, definition, slotId).map((unit) => ({section, unit})));
      const contexts = candidates.map((c) => c.unit.context);
      const componentsFound = (definition.components || []).filter((c) => componentPresent(slotId, c, contexts)).map((c) => c.id).sort();
      const first = candidates.reduce((best, candidate) => {
        if (!best) return candidate;
        const a = candidateRank(slotId, candidate.unit.context);
        const b = candidateRank(slotId, best.unit.context);
        return a[0] > b[0] || (a[0] === b[0] && a[1] > b[1]) ? candidate : best;
      }, null);
      return {
        slot:slotId, label:definition.label, found:Boolean(first),
        components_found:componentsFound, components_total:(definition.components || []).length,
        evidence:first ? evidenceLine(text, first.section.start + first.unit.start, first.section.start + first.unit.end) : null,
        section:first ? first.section.name : null,
      };
    });
  }

  function makeBasis(rule, data) {
    if (rule.basis.type !== "statute") return { type: rule.basis.type };
    const statute = data.statutes[rule.basis.statute_id];
    const article = statute.articles[rule.basis.article];
    return {
      type: "statute",
      law: rule.basis.law,
      article: rule.basis.article,
      statute_id: rule.basis.statute_id,
      snapshot_date: statute.snapshot_date,
      effective_date: article.effective_date,
      title: article.title,
      text: article.text,
    };
  }

  function makeQuestionReference(rule) {
    const basis = rule.basis || {};
    const provenance = rule.provenance || {};
    const sections = basis.sections ||
      (provenance.source_section ? [String(provenance.source_section)] : null);
    return {
      type: basis.type,
      title: basis.title || provenance.source_document || null,
      publisher: basis.publisher || null,
      year: Number.isInteger(basis.year) ? basis.year : null,
      pages: Array.isArray(basis.pages) ? [...basis.pages] : null,
      source_url: basis.source_url || null,
      accessed_at: basis.accessed_at || null,
      sections: Array.isArray(sections) ? [...sections] : null,
    };
  }

  function questionPriority(linkedFindings, reviewScope, triggerReason) {
    if (linkedFindings.length) return 1;
    if (reviewScope === "common") return 4;
    return triggerReason === "presence" ? 2 : 3;
  }

  function matchesContextGroups(source, match, trigger) {
    const contextGroups = trigger.context_groups;
    if (!contextGroups) return true;
    return Object.values(contextGroups).every((group) => {
      const context = codePointWindow(source, match, group.window);
      return Boolean(findFirst(context, group.patterns));
    });
  }

  function check(text, savedAnswers, providedData) {
    const data = providedData || global.FAIRPOST_DATA;
    if (!data) throw new Error("fairpost 사전 번들을 찾을 수 없습니다.");
    const source = normalize(text);
    const sections = splitSections(source);
    const slots = extractSlots(source, sections, data.slots);
    const slotsById = Object.fromEntries(slots.map((slot) => [slot.slot, slot]));
    const answers = savedAnswers || {};
    const findings = [];
    const questions = [];

    const fired = new Map();
    const suppressed = new Set();
    data.rules.forEach((rule) => {
      const trigger = rule.trigger;
      if (trigger.type === "absence") {
        if (!slotsById[trigger.field].found) fired.set(rule.id, null);
        return;
      }
      const candidates = findMatches(source, trigger.patterns);
      const match =
        candidates.find(
          (candidate) =>
            !isExcluded(source, candidate, trigger.exclude || []) &&
            (!trigger.section_scope ||
              sectionAt(sections, candidate.start) === trigger.section_scope) &&
            matchesContextGroups(source, candidate, trigger) &&
            sourceCandidateAllowed(source, candidate, sections, rule.layer)
        ) || null;
      if (match) fired.set(rule.id, match);
      else if (candidates.length) suppressed.add(rule.id);
    });

    // Findings are resolved before questions so that related_questions links
    // do not depend on the order rule files happen to be concatenated in.
    data.rules.forEach((rule) => {
      if (rule.layer !== "law" || !fired.has(rule.id)) return;
      const match = fired.get(rule.id);
      findings.push({
        id: rule.id,
        dimension: rule.dimension,
        message: rule.message,
        matched_text: match.text,
        offset: [
          codePointOffset(source, match.start),
          codePointOffset(source, match.end),
        ],
        section: sectionAt(sections, match.start),
        severity: rule.severity,
        basis: makeBasis(rule, data),
        alternatives: [...(rule.alternatives || [])],
        provenance_method: rule.provenance.method,
        book_ref: rule.book_ref,
      });
    });

    const linked = new Map();
    data.rules.forEach((rule) => {
      if (rule.layer !== "law" || !fired.has(rule.id)) return;
      (rule.related_questions || []).forEach((questionId) => {
        if (!linked.has(questionId)) linked.set(questionId, []);
        linked.get(questionId).push(rule.id);
      });
    });

    data.rules.forEach((rule) => {
      if (rule.layer !== "question") return;
      const ownTrigger = fired.has(rule.id);
      // A finding link never overrides the question's own protective
      // exclusions: if the wording was found and deliberately rejected,
      // pulling it back in would undo that false-positive work.
      const linkedFindings =
        !ownTrigger && suppressed.has(rule.id)
          ? []
          : (linked.get(rule.id) || []).slice().sort();
      if (!ownTrigger && !linkedFindings.length) return;
      const match = ownTrigger ? fired.get(rule.id) : null;
      const reviewScope = rule.review_scope || "posting";
      const triggerReason = ownTrigger ? rule.trigger.type : "finding";
      questions.push({
        id: rule.id,
        dimension: rule.dimension,
        question: rule.question,
        follow_up: [...(rule.follow_up || [])],
        basis_type: rule.basis.type,
        book_ref: rule.book_ref,
        review_scope: reviewScope,
        saved_answer: answers[rule.id] ?? null,
        matched_text: match ? match.text : null,
        offset: match
          ? [codePointOffset(source, match.start), codePointOffset(source, match.end)]
          : null,
        section: match ? sectionAt(sections, match.start) : null,
        reference: makeQuestionReference(rule),
        trigger_reason: triggerReason,
        linked_findings: linkedFindings,
        priority: questionPriority(linkedFindings, reviewScope, triggerReason),
      });
    });

    findings.sort((a, b) => a.id.localeCompare(b.id));
    questions.sort(
      (a, b) => a.priority - b.priority || a.id.localeCompare(b.id)
    );
    const statuteSnapshotDate = Object.values(data.statutes)
      .map((statute) => statute.snapshot_date)
      .sort()[0];
    return {
      findings,
      slots,
      questions,
      counts: {
        findings: findings.length,
        not_found: slots.filter((slot) => !slot.found).length,
        questions: questions.length,
      },
      ruleset_version: data.version,
      statute_snapshot_date: statuteSnapshotDate,
      statute_notice:
        `법령 원문은 ${statuteSnapshotDate} 공식 대조 스냅샷을 기준으로 합니다. ` +
        "그 이후 개정은 배포본의 법령 감사 기록을 확인해야 합니다.",
      disclaimer: DISCLAIMER,
    };
  }

  global.FairpostEngine = {
    check,
    normalize,
    splitSections,
    extractSlots,
  };
})(typeof window !== "undefined" ? window : globalThis);
