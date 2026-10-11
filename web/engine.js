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
      "이런 분과 함께하고 싶어요",
      "자격요건의 직무 관련성"
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
      "전형절차 및 기타사항",
      "평가 기준",
      "인공지능 활용 안내"
    ]
  ],
  [
    "일정",
    [
      "전형일정",
      "채용일정",
      "일정", "접수기간", "모집기간", "공고기간", "접수 마감일",
      "결과 안내"
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
      "꼭 확인해 주세요",
      "이의제기 안내",
      "채용서류 반환ㆍ파기 안내",
      "채용서류 반환·파기 안내"
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
  // PYTHON_WHITESPACE as a code-unit table (every member is one BMP unit).
  const PYTHON_WHITESPACE_CODES = (() => {
    const table = new Uint8Array(0x3001);
    PYTHON_WHITESPACE.forEach((character) => {
      table[character.charCodeAt(0)] = 1;
    });
    return table;
  })();

  function isPythonWhitespace(character) {
    return character.length === 1 && isPythonWhitespaceCode(character.charCodeAt(0));
  }

  function isPythonWhitespaceCode(code) {
    return code <= 0x3000 && PYTHON_WHITESPACE_CODES[code] === 1;
  }

  // --- per-character normalization tables -----------------------------------
  // The Python core applies NFKC to one code point at a time (never to the
  // whole string, which would compose decomposed jamo or combining marks), so
  // each code point's result is fixed and can be memoized. CHAR_STATE caches,
  // per BMP code unit (lone surrogates included), whether the two per-character
  // rewrites below leave the character as it is.
  const NFKC_CHAR_CACHE = new Map();
  const CHAR_STATE = new Uint8Array(0x10000);
  const CHAR_KNOWN = 1;
  // normalizedTextWithOffsets keeps it: not zero-width, and "\r"/"\n"/"\t", a
  // plain space, or a non-whitespace character that NFKC leaves unchanged.
  const CHAR_KEEP_TEXT = 2;
  // normalizedHeading keeps it: not zero-width and NFKC leaves it unchanged.
  const CHAR_KEEP_HEADING = 4;

  // character.normalize("NFKC") for one code point (or one lone surrogate).
  function nfkcChar(character) {
    let value = NFKC_CHAR_CACHE.get(character);
    if (value === undefined) {
      value = character.normalize("NFKC");
      if (NFKC_CHAR_CACHE.size >= 65536) NFKC_CHAR_CACHE.clear();
      NFKC_CHAR_CACHE.set(character, value);
    }
    return value;
  }

  // The per-character rewrite of normalizedTextWithOffsets for a one-unit
  // character: "" for zero-width, "\r"/"\n"/"\t" kept, other Python whitespace
  // becomes " ", everything else is its own NFKC form.
  function textReplacement(character) {
    if (ZERO_WIDTH.has(character)) return "";
    if (character === "\r" || character === "\n" || character === "\t") return character;
    if (isPythonWhitespace(character)) return " ";
    return nfkcChar(character);
  }

  function charState(code) {
    let state = CHAR_STATE[code];
    if (state === 0) {
      const character = String.fromCharCode(code);
      state = CHAR_KNOWN;
      if (textReplacement(character) === character) state |= CHAR_KEEP_TEXT;
      if (!ZERO_WIDTH.has(character) && nfkcChar(character) === character) {
        state |= CHAR_KEEP_HEADING;
      }
      CHAR_STATE[code] = state;
    }
    return state;
  }

  // Width in code units of the code point starting at `index`, as the string
  // iterator (for...of, Array.from) splits it: 2 for a surrogate pair, else 1.
  function codePointWidth(text, index, code) {
    if (code >= 0xd800 && code <= 0xdbff && index + 1 < text.length) {
      const next = text.charCodeAt(index + 1);
      if (next >= 0xdc00 && next <= 0xdfff) return 2;
    }
    return 1;
  }

  // Array.from(text).length without building the array.
  function codePointLength(text) {
    let count = text.length;
    for (let index = 0; index + 1 < text.length; index += 1) {
      const code = text.charCodeAt(index);
      if (code >= 0xd800 && code <= 0xdbff) {
        const next = text.charCodeAt(index + 1);
        if (next >= 0xdc00 && next <= 0xdfff) {
          count -= 1;
          index += 1;
        }
      }
    }
    return count;
  }

  // Map-based LRU helpers shared by the per-string view caches.
  function lruGet(cache, key) {
    const cached = cache.get(key);
    if (cached !== undefined) {
      cache.delete(key);
      cache.set(key, cached);
    }
    return cached;
  }

  function lruSet(cache, key, value, limit) {
    if (cache.size >= limit) cache.delete(cache.keys().next().value);
    cache.set(key, value);
  }

  function trimPythonWhitespace(value) {
    let start = 0;
    let end = value.length;
    while (start < end && isPythonWhitespaceCode(value.charCodeAt(start))) {
      start += 1;
    }
    while (end > start && isPythonWhitespaceCode(value.charCodeAt(end - 1))) {
      end -= 1;
    }
    return start === 0 && end === value.length ? value : value.slice(start, end);
  }

  // PYTHON_LINE_BREAKS membership for one code unit (every member is one unit).
  const PYTHON_LINE_BREAK_CODES = new Set(
    [...PYTHON_LINE_BREAKS].map((character) => character.charCodeAt(0))
  );

  function isPythonLineBreakCode(code) {
    return PYTHON_LINE_BREAK_CODES.has(code);
  }

  // Mirrors str.splitlines(keepends=True).
  function splitPythonLines(text) {
    const lines = [];
    let start = 0;
    for (let index = 0; index < text.length; index += 1) {
      if (!isPythonLineBreakCode(text.charCodeAt(index))) continue;
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

  // A text view is { normalized, starts, ends }: for each code unit k of
  // `normalized`, the source code units [starts[k], ends[k]) it came from.
  // An identity view (normalized === source, every unit maps to itself) keeps
  // starts/ends null; findMatches only reads offsets of a view whose text
  // differs from its source, and matchTextWithOffsets composes identity views
  // without reading them.

  // MORPH_REWRITES as one alternation in table order. At each position the
  // first listed source that starts there wins and scanning resumes after it,
  // exactly as the leftmost, first-in-table loop it replaces. All sources are
  // BMP Hangul, so code-unit positions (no "u" flag) are the same.
  const MORPH_SOURCE = new RegExp(
    MORPH_REWRITES.map(([source]) => escapeRegex(source)).join("|"),
    "g"
  );
  const MORPH_TARGET = new Map(MORPH_REWRITES);

  function morphTextWithOffsets(text) {
    const cached = lruGet(MORPH_CACHE, text);
    if (cached !== undefined) return cached;
    MORPH_SOURCE.lastIndex = 0;
    let match = MORPH_SOURCE.exec(text);
    let result;
    if (match === null) {
      result = { normalized: text, starts: null, ends: null, identity: true };
    } else {
      const pieces = [];
      const rewrites = [];
      let cursor = 0;
      while (match !== null) {
        const target = MORPH_TARGET.get(match[0]);
        pieces.push(text.slice(cursor, match.index), target);
        rewrites.push(match.index, match.index + match[0].length, target.length);
        cursor = match.index + match[0].length;
        match = MORPH_SOURCE.exec(text);
      }
      pieces.push(text.slice(cursor));
      const normalized = pieces.join("");
      const starts = new Int32Array(normalized.length);
      const ends = new Int32Array(normalized.length);
      let out = 0;
      let position = 0;
      for (let index = 0; index < rewrites.length; index += 3) {
        const sourceStart = rewrites[index];
        const sourceEnd = rewrites[index + 1];
        for (; position < sourceStart; position += 1, out += 1) {
          starts[out] = position;
          ends[out] = position + 1;
        }
        for (let unit = 0; unit < rewrites[index + 2]; unit += 1, out += 1) {
          starts[out] = sourceStart;
          ends[out] = sourceEnd;
        }
        position = sourceEnd;
      }
      for (; position < text.length; position += 1, out += 1) {
        starts[out] = position;
        ends[out] = position + 1;
      }
      result = { normalized, starts, ends, identity: false };
    }
    lruSet(MORPH_CACHE, text, result, 512);
    return result;
  }

  function morphPlain(text) {
    return morphTextWithOffsets(text).normalized;
  }

  const NORMALIZED_CACHE = new Map();

  // Code points (pairs whole, lone surrogates alone, as for...of yields
  // them) are rewritten one at a time: zero-width characters are dropped,
  // "\r"/"\n"/"\t" kept, other Python whitespace becomes " ", and everything
  // else becomes its own per-character NFKC form.
  function normalizedTextWithOffsets(text) {
    const cached = lruGet(NORMALIZED_CACHE, text);
    if (cached !== undefined) return cached;
    const length = text.length;
    // Fast scan: stop at the first code point the rewrite would change.
    let index = 0;
    let astral = false;
    while (index < length) {
      const code = text.charCodeAt(index);
      const width = codePointWidth(text, index, code);
      if (width === 2) {
        const character = text.slice(index, index + 2);
        if (nfkcChar(character) !== character) break;
        astral = true;
      } else if (!(charState(code) & CHAR_KEEP_TEXT)) {
        break;
      }
      index += width;
    }
    let result;
    if (index === length && !astral) {
      result = { normalized: text, starts: null, ends: null, identity: true };
    } else {
      // Build the rewritten text, then the offsets of each of its code units.
      // A surrogate pair is one code point whose units both map to [i, i + 2).
      const pieces = [];
      let runStart = 0;
      for (let cursor = index; cursor < length; ) {
        const code = text.charCodeAt(cursor);
        const width = codePointWidth(text, cursor, code);
        const replacement = pointReplacement(text, cursor, code, width);
        if (replacement !== null) {
          pieces.push(text.slice(runStart, cursor), replacement);
          runStart = cursor + width;
        }
        cursor += width;
      }
      pieces.push(text.slice(runStart));
      const normalized = pieces.length === 1 ? text : pieces.join("");
      const starts = new Int32Array(normalized.length);
      const ends = new Int32Array(normalized.length);
      let out = 0;
      for (let cursor = 0; cursor < length; ) {
        const code = text.charCodeAt(cursor);
        const width = codePointWidth(text, cursor, code);
        const replacement =
          cursor < index ? null : pointReplacement(text, cursor, code, width);
        const units = replacement === null ? width : replacement.length;
        for (let unit = 0; unit < units; unit += 1, out += 1) {
          starts[out] = cursor;
          ends[out] = cursor + width;
        }
        cursor += width;
      }
      result = { normalized, starts, ends, identity: false };
    }
    lruSet(NORMALIZED_CACHE, text, result, 512);
    return result;
  }

  // The rewrite of the code point at `cursor`, or null when it stays as is.
  function pointReplacement(text, cursor, code, width) {
    if (width === 2) {
      // A pair is never zero-width, a line break or Python whitespace.
      const character = text.slice(cursor, cursor + 2);
      const replacement = nfkcChar(character);
      return replacement === character ? null : replacement;
    }
    if (charState(code) & CHAR_KEEP_TEXT) return null;
    return textReplacement(String.fromCharCode(code));
  }

  function matchTextWithOffsets(text) {
    const cached = lruGet(MATCH_CACHE, text);
    if (cached !== undefined) return cached;
    const base = normalizedTextWithOffsets(text);
    const morph = morphTextWithOffsets(base.normalized);
    let result;
    if (morph.normalized === base.normalized) {
      result = base;
    } else if (base.identity) {
      // base.starts[i] === i and base.ends[i - 1] === i, so composing with
      // an identity base leaves the morph offsets as they are.
      result = { normalized: morph.normalized, starts: morph.starts, ends: morph.ends, identity: false };
    } else {
      const starts = new Int32Array(morph.starts.length);
      const ends = new Int32Array(morph.ends.length);
      for (let index = 0; index < starts.length; index += 1) {
        starts[index] = base.starts[morph.starts[index]];
        ends[index] = base.ends[morph.ends[index] - 1];
      }
      result = { normalized: morph.normalized, starts, ends, identity: false };
    }
    lruSet(MATCH_CACHE, text, result, 512);
    return result;
  }

  function matchPlain(text) {
    return morphPlain(normalizedTextWithOffsets(text).normalized);
  }

  // value.toLocaleLowerCase("ko"), memoized (the locale-aware call costs about
  // ten times a Map lookup and the same matched words recur constantly).
  const LOWER_KO_CACHE = new Map();

  function lowerKo(value) {
    let lowered = LOWER_KO_CACHE.get(value);
    if (lowered === undefined) {
      lowered = value.toLocaleLowerCase("ko");
      if (LOWER_KO_CACHE.size >= 8192) LOWER_KO_CACHE.clear();
      LOWER_KO_CACHE.set(value, lowered);
    }
    return lowered;
  }

  // Per pattern: the regex for the source text and the one for the
  // normalized view (the same object when the pattern does not change). Both
  // are global, so every use starts by resetting lastIndex; findMatches never
  // re-enters itself while a loop is running.
  const PATTERN_PLAN_CACHE = new Map();

  function patternPlan(pattern) {
    let plan = PATTERN_PLAN_CACHE.get(pattern);
    if (plan === undefined) {
      const isRegex = pattern.startsWith("re:");
      const regex = patternToRegex(pattern);
      const normalizedPattern = isRegex ? pattern : matchPlain(pattern);
      const normalizedRegex =
        normalizedPattern === pattern ? regex : patternToRegex(normalizedPattern);
      plan = { isRegex, regex, normalizedRegex };
      if (PATTERN_PLAN_CACHE.size >= 4096) PATTERN_PLAN_CACHE.clear();
      PATTERN_PLAN_CACHE.set(pattern, plan);
    }
    return plan;
  }

  function findMatches(text, patterns) {
    const matches = [];
    const seen = new Set();
    const normalizedView = normalizedTextWithOffsets(text);
    const matchView = matchTextWithOffsets(text);
    const needsMatchView = matchView.normalized !== text;
    const normalizedChanged = normalizedView.normalized !== text;
    patterns.forEach((pattern) => {
      const plan = patternPlan(pattern);
      const isRegex = plan.isRegex;
      const regex = plan.regex;
      regex.lastIndex = 0;
      let match;
      const exactText = isRegex && normalizedChanged ? null : text;
      while (exactText !== null && (match = regex.exec(exactText)) !== null) {
        const item = {
          start: match.index,
          end: match.index + match[0].length,
          text: match[0],
        };
        const key = `${item.start}:${item.end}:${lowerKo(item.text)}`;
        if (!seen.has(key)) {
          matches.push(item);
          seen.add(key);
        }
        if (match[0].length === 0) regex.lastIndex += 1;
      }
      let searchView;
      if (isRegex) {
        if (!normalizedChanged) return;
        searchView = normalizedView;
      } else {
        if (!needsMatchView) return;
        searchView = matchView;
      }
      // Keep regex exclusions on the same NFKC/zero-width-normalized view as
      // literal candidates so protective wording cannot become a false
      // positive merely because it contains compatibility or invisible text.
      // (plan.normalizedRegex is built from matchPlain(pattern) for literals.)
      const normalizedRegex = plan.normalizedRegex;
      normalizedRegex.lastIndex = 0;
      while ((match = normalizedRegex.exec(searchView.normalized)) !== null) {
        if (match[0].length === 0) {
          normalizedRegex.lastIndex += 1;
          continue;
        }
        const start = searchView.starts[match.index];
        const end = searchView.ends[match.index + match[0].length - 1];
        const item = { start, end, text: text.slice(start, end) };
        const key = `${start}:${end}:${lowerKo(item.text)}`;
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
    "^[\\s#>*\\-–—\\d.()①-⑳\\[\\]■●○◆◇▶▷□◎※【】<]+|[\\s:：\\[\\]】>]+$",
    "gu"
  );
  const WHITESPACE_RUN = pythonRegex("\\s+", "gu");

  // Drops zero-width code points and applies NFKC to each remaining code
  // point on its own (as Array.from(line) splits them).
  function normalizedHeading(line) {
    const length = line.length;
    let index = 0;
    while (index < length) {
      const code = line.charCodeAt(index);
      const width = codePointWidth(line, index, code);
      if (width === 2) {
        const character = line.slice(index, index + 2);
        if (nfkcChar(character) !== character) break;
      } else if (!(charState(code) & CHAR_KEEP_HEADING)) {
        break;
      }
      index += width;
    }
    if (index === length) return line;
    const pieces = [line.slice(0, index)];
    while (index < length) {
      const code = line.charCodeAt(index);
      const width = codePointWidth(line, index, code);
      const character = width === 2 ? line.slice(index, index + 2) : line[index];
      if (!ZERO_WIDTH.has(character)) pieces.push(nfkcChar(character));
      index += width;
    }
    return pieces.join("");
  }

  // Pure functions of their argument, memoized because every line of every
  // posting goes through them, often more than once.
  const STRIPPED_HEADING_CACHE = new Map();
  const COMPACT_HEADING_CACHE = new Map();
  const HEADING_NAME_CACHE = new Map();
  let aliasCanonical = null;

  function memoSet(cache, key, value) {
    if (cache.size >= 4096) cache.clear();
    cache.set(key, value);
    return value;
  }

  function stripHeadingDecoration(value) {
    const cached = STRIPPED_HEADING_CACHE.get(value);
    if (cached !== undefined) return cached;
    return memoSet(
      STRIPPED_HEADING_CACHE,
      value,
      trimPythonWhitespace(normalizedHeading(value)).replace(HEADING_DECORATION, "")
    );
  }

  function compactHeading(value) {
    const cached = COMPACT_HEADING_CACHE.get(value);
    if (cached !== undefined) return cached;
    return memoSet(
      COMPACT_HEADING_CACHE,
      value,
      stripHeadingDecoration(value).replace(WHITESPACE_RUN, "").toLowerCase()
    );
  }

  // compactHeading(alias) -> the first canonical section (in SECTION_ALIASES
  // order) that lists an alias with that compact form.
  function sectionForCompactHeading(compact) {
    if (aliasCanonical === null) {
      aliasCanonical = new Map();
      for (const [canonical, aliases] of SECTION_ALIASES) {
        for (const alias of aliases) {
          const key = compactHeading(alias);
          if (!aliasCanonical.has(key)) aliasCanonical.set(key, canonical);
        }
      }
    }
    return aliasCanonical.has(compact) ? aliasCanonical.get(compact) : null;
  }

  function headingName(line) {
    const cached = HEADING_NAME_CACHE.get(line);
    if (cached !== undefined) return cached;
    return memoSet(HEADING_NAME_CACHE, line, uncachedHeadingName(line));
  }

  function uncachedHeadingName(line) {
    const cleaned = stripHeadingDecoration(line);
    if (!cleaned || codePointLength(cleaned) > 30) return null;
    const compact = compactHeading(cleaned);
    if (compact.endsWith("합류여정")) return "전형절차";
    return sectionForCompactHeading(compact);
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
    return codePointLength(text.slice(0, codeUnitOffset));
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

  // A sentence boundary at code unit i: one of . ! ? 。 ！ ？, except a "."
  // after a digit or between two ASCII letters/digits.
  function isSentenceMark(text, i) {
    const character = text[i];
    if (
      character !== "." && character !== "!" && character !== "?" &&
      character !== "。" && character !== "！" && character !== "？"
    ) {
      return false;
    }
    // Python reads text[i - 1] as a whole code point, so an astral digit
    // (e.g. U+1D7CF) before "." must be tested as its surrogate pair.
    return !(character === "." && i > 0 && (/^\p{Nd}$/u.test(codePointEndingAt(text, i)) || (/[A-Za-z0-9]/u.test(text[i - 1]) && /[A-Za-z0-9]/u.test(text[i + 1] || ""))));
  }

  // The sentence around [start, end) within its line: from just after the
  // last mark before `start` to just after the first mark at or after `end`
  // (marks inside the span do not count). Scanning outwards from the span
  // finds the same two marks as listing every mark on the line.
  function sentenceBounds(text, start, end) {
    const lineStart = lastNewlineBefore(text, start) + 1;
    const next = text.indexOf("\n", end);
    const lineEnd = next === -1 ? text.length : next;
    let segmentStart = lineStart;
    for (let i = Math.min(start, lineEnd) - 1; i >= lineStart; i -= 1) {
      if (isSentenceMark(text, i)) {
        segmentStart = i + 1;
        break;
      }
    }
    let segmentEnd = lineEnd;
    for (let i = Math.max(end, lineStart); i < lineEnd; i += 1) {
      if (isSentenceMark(text, i)) {
        segmentEnd = i + 1;
        break;
      }
    }
    return [segmentStart, segmentEnd];
  }

  function evidenceLine(text, start, end) {
    const [segmentStart, segmentEnd] = sentenceBounds(text, start, end);
    const segment = text.slice(segmentStart, segmentEnd);
    const limit = 238;
    if (codePointLength(segment) <= limit) {
      return trimPythonWhitespace(segment);
    }
    const codePoints = Array.from(segment);
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
  // Same as _HIRING_CONTEXT: a bare "인터뷰" counts only through hiringContext.
  const HIRING_CONTEXT = ["지원자","응시자","서류전형","면접","역량검사","전형절차","채용과정","직무 인터뷰","실무 인터뷰","화상 인터뷰","문화적합성 인터뷰"];
  const DUTY_CONTEXT = ["re:(?:알고리즘|임직원|직원|제품|서비스|고객).{0,80}(?:연구|개발|설계|운영|개선|평가)"];
  const CONTACT_CONTEXT = ["문의","연락","인사팀","채용팀","인사부","담당 부서","전화","전자우편","re:[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}","re:0\\d{1,2}[- )]\\d{3,4}[- ]\\d{4}"];
  const CONTACT_NON_CHANNEL = ["결과","통보","기재","등록","발송","지원서","모집"];
  // Keep identical to _CONTACT_CHANNEL: a phone (incl. 15xx/16xx/18xx) or an e-mail.
  const CONTACT_CHANNEL = ["re:0\\d{1,2}[- )]\\d{3,4}[- ]\\d{4}", "re:(?<![\\d-])1[568]\\d{2}-\\d{4}(?![\\d-])", "re:[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}"];
  // Keep identical to _DEFERRED_BODY in core/extractor.py (see the comment there).
  const DEFERRED_BODY = ["re:^(?:(?:추후|차후|나중에|미정)[^\\n]*|(?![^\\n]*(?:(?:19|20)\\d{2}\\s*[./-]\\s*\\d{1,2}|\\d{1,2}\\s*[./]\\s*\\d{1,2}|\\d{1,4}\\s*(?:년|월|일|시|주)|\\d[\\d,.]{0,15}\\s*(?:[천백만억]\\s*)*원))(?:(?:별도|상세|예정)[^\\n]*(?:안내|공지|협의|예정|미정|문의|통보|참조|확인)[^\\n]*|예정[^\\n]{0,12}|별도|상세))$"];
  const DATE_CONTENT = ["re:\\d{1,4}[./-]\\d{1,2}|(?:19|20)\\d{2}\\s*[./-]\\s*\\d{1,2}","re:\\d+\\s*(?:년|월|일|시|주)","상시","수시", "채용시까지", "채용 시까지"];
  const STAGE_EVENTS = ["서류전형","면접전형","필기전형","서류접수","직무 인터뷰","실무 인터뷰","화상 인터뷰","문화적합성 인터뷰","코딩테스트","코딩 테스트","AI 역량검사","AI 면접","서류 전형","면접 전형","필기 전형", "AI 사전면접", "AI 자기소개서 평가", "서류 검토", "AI 영상면접"];
  // Keep identical to _BARE_STAGE_TERMS, _STAGE_SECTIONS, _STAGE_MARKERS,
  // _LINE_MARKER and _SENTENCE_END in core/extractor.py.
  const BARE_STAGE_TERMS = ["인터뷰", "서류접수", "서류 검토", "서류 리뷰"];
  // Keep identical to _BARE_INTERVIEW_STAGE ("최종 면접", "1차 면접" ...).
  const BARE_INTERVIEW_STAGE = ["re:(?:최종|임원|대면|비대면|화상|온라인|전화|실무진|실무|경영진|대표이사|대표|인성|역량|직무|기술|심층|PT|발표|토론|\\d\\s*차)\\s*면접(?!관|비|장)"];
  const STAGE_SECTIONS = ["전형절차", "일정"];
  const STAGE_MARKERS = ["re:→|⇒|▶|▷|->|=>|>", "re:\\d\\s*단계", "re:\\d\\s*차(?![가-힣])", "re:\\d\\s*차\\s*(?:인터뷰|면접|전형|심사|평가|합격|과제|테스트|서류)", "전형", "re:(?:채용|선발|합류)\\s*(?:절차|과정|프로세스)", "re:최종\\s*합격"];
  const LINE_MARKER = pythonRegex(
    "^\\s*(?:([-–—‐−*•·ㆍ∙‧▪◦+－＊・･])|(\\d{1,2})\\s*([.)．）])(?!\\d)|([(（]\\s*\\d{1,2}\\s*[)）])|([①-⑳])|([가나다라마바사아자차카타파하])\\s*[.)．）]|([●○◆◇▶▷□■◎])|(※)|([\\[【<#［〈《]))",
    "u"
  );
  const SENTENCE_END = ".!?。다요음함임";
  const DUTY_BLOCK_CACHE = new Map();
  const ATTACHMENT_SUFFIX = pythonRegex("\\.(?:pdf|hwpx?|hml|docx?|zip)\\s*$", "iu");

  function lineBounds(text, start, end) {
    const left = lastNewlineBefore(text, start) + 1;
    const right = text.indexOf("\n", end);
    return [left, right === -1 ? text.length : right];
  }

  function stripTags(value) {
    return value.replace(/<[^>]+>/gu, "");
  }

  // Zero-width characters are single BMP units that are never surrogates, so
  // deleting them unit by unit equals filtering the line's code points.
  const ZERO_WIDTH_RUN = /[\u200b\u200c\u200d\ufeff]/g;

  function stripZeroWidth(line) {
    ZERO_WIDTH_RUN.lastIndex = 0;
    return ZERO_WIDTH_RUN.test(line) ? line.replace(ZERO_WIDTH_RUN, "") : line;
  }

  // Mirrors _line_marker: [kind, style] of a line's leading bullet, number or mark.
  function lineMarker(line) {
    const match = LINE_MARKER.exec(stripZeroWidth(line));
    if (!match) return ["", ""];
    const [, bullet, number, numberEnd, paren, circled, hangul, mark, note, bracket] = match;
    if (bullet) return ["item", bullet];
    if (number) return ["item", ".．".includes(numberEnd) ? "1." : "1)"];
    if (paren) return ["item", "(1)"];
    if (circled) return ["item", "①"];
    if (hangul) return ["item", "가."];
    if (mark) return ["mark", mark];
    if (note) return ["note", note];
    return ["bracket", bracket];
  }

  function indentOf(line) {
    let count = 0;
    while (count < line.length && isPythonWhitespaceCode(line.charCodeAt(count))) count += 1;
    return count;
  }

  function headingLike(line) {
    const cleaned = Array.from(stripHeadingDecoration(line));
    return cleaned.length > 0 && cleaned.length <= 30 && !SENTENCE_END.includes(cleaned[cleaned.length - 1]);
  }

  // Mirrors _duty_block_end_in: where the duty list under a duty heading ends.
  function dutyBlockEnd(text) {
    if (DUTY_BLOCK_CACHE.has(text)) return DUTY_BLOCK_CACHE.get(text);
    const lines = text.split("\n");
    let result = text.length;
    if (!DUTY_HEADINGS.some((h) => compactHeading(h) === compactHeading(lines[0]))) {
      result = 0;
    } else {
      const [headingKind, headingStyle] = lineMarker(lines[0]);
      const headingIndent = indentOf(stripTags(lines[0]));
      let itemStyle = null;
      let offset = lines[0].length + 1;
      for (let index = 1; index < lines.length; index += 1) {
        const line = lines[index];
        const raw = stripTags(line);
        const plain = trimPythonWhitespace(raw);
        if (!plain) {
          if (itemStyle !== null) {
            const following = lines.slice(index + 1).map((rest) => trimPythonWhitespace(stripTags(rest))).find((rest) => rest) ?? null;
            if (!(following !== null && itemStyle && lineMarker(following)[1] === itemStyle)) {
              result = offset;
              break;
            }
          }
          offset += line.length + 1;
          continue;
        }
        const [kind, style] = lineMarker(raw);
        if (itemStyle === null) {
          itemStyle = style;
        } else if (style !== itemStyle && indentOf(raw) <= headingIndent && headingLike(plain)) {
          if (kind === "mark" || kind === "bracket" || (kind === "" && itemStyle) || (headingKind && style === headingStyle)) {
            result = offset;
            break;
          }
        }
        offset += line.length + 1;
      }
    }
    if (DUTY_BLOCK_CACHE.size > 256) DUTY_BLOCK_CACHE.clear();
    DUTY_BLOCK_CACHE.set(text, result);
    return result;
  }

  function inDutyBlock(section, offset) {
    return offset < dutyBlockEnd(section.text);
  }

  // Constant lists derived from the stage tables (built once, on first use).
  let bareStageCompact = null;
  let namedStageEvents = null;
  let stageContextPatterns = null;

  function stageTables() {
    if (bareStageCompact === null) {
      bareStageCompact = BARE_STAGE_TERMS.map((term) => compactHeading(term));
      namedStageEvents = STAGE_EVENTS.filter((event) => !bareStageCompact.includes(compactHeading(event)));
      stageContextPatterns = [...STAGE_MARKERS, ...namedStageEvents, "면접"];
    }
  }

  // Mirrors _stage_context.
  function stageContext(section, sentence) {
    if (section && STAGE_SECTIONS.includes(section.name)) return true;
    stageTables();
    return Boolean(findFirst(stripTags(sentence), stageContextPatterns));
  }

  // Mirrors _bare_stage_match.
  function bareStageMatch(text, start, end) {
    const candidate = text.slice(start, end);
    const interview = findFirst(candidate, BARE_INTERVIEW_STAGE);
    if (interview && interview.start === 0 && interview.end === candidate.length) return true;
    stageTables();
    const bare = bareStageCompact;
    if (!bare.includes(compactHeading(candidate))) return false;
    const named = namedStageEvents;
    const [left, right] = lineBounds(text, start, end);
    return !findMatches(text.slice(left, right), named).some((m) => left + m.start <= start && end <= left + m.end);
  }

  // Mirrors _bare_stage_sentence: the term's own 면접 is not its stage context.
  function bareStageSentence(text, start, end) {
    const masked = text.slice(0, start) + text.slice(start, end).replaceAll("면접", "  ") + text.slice(end);
    return evidenceLine(masked, start, end);
  }

  function hiringContext(line, section) {
    if (findFirst(line, HIRING_CONTEXT)) return true;
    return Boolean(findFirst(line, ["인터뷰"])) && stageContext(section, line);
  }

  function contextAllowed(slotId, line, section, offset) {
    if (slotId !== "qualification_rationale" && inDutyBlock(section, offset)) return false;
    if (slotId === "selection_stages" && findFirst(line, ["인터뷰 자세히", "인터뷰 보기", "팀원 인터뷰", "현직자 인터뷰"])) return false;
    if (["ai_disclosure", "evaluation_criteria", "selection_stages"].includes(slotId)) {
      if (["자격요건", "우대사항"].includes(section.name) && findFirst(line, ["경험", "경력", "설계", "연구", "개발", "운영"]) && !findFirst(line, ["지원자", "응시자", "채용 과정", "전형 절차", "실시", "참여", "진행"])) return false;
      if (findFirst(line, DUTY_CONTEXT) && !hiringContext(line, section)) return false;
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

  // Mirrors _label_body_unit: the body a label line anchors, or null.
  function labelBodyUnit(section, definition, slotId, line, right, offset) {
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
    if (!bodyParts.length) return null;
    const body = bodyParts.join("\n");
    const support = [...(definition.accept_patterns || []), ...(definition.components || []).flatMap((c) => c.patterns || [])];
    if (slotId === "schedule") support.push(...DATE_CONTENT);
    if (!findFirst(body, support) && !(["preference_items", "evaluation_criteria"].includes(slotId) && Array.from(trimPythonWhitespace(body)).length >= 4)) return null;
    if (!contextAllowed(slotId, body, section, offset)) return null;
    return {start:bodyStart, end, context:line + "\n" + body};
  }

  function candidateUnits(section, definition, slotId) {
    const units = [];
    for (const match of findMatches(section.text, definition.accept_patterns || [])) {
      const [left, right] = lineBounds(section.text, match.start, match.end);
      const line = section.text.slice(left, right);
      if (slotId === "selection_stages" && bareStageMatch(section.text, match.start, match.end) && !stageContext(section, bareStageSentence(section.text, match.start, match.end))) continue;
      let heading = headingName(line) !== null || compactHeading(line) === compactHeading(match.text);
      if (["selection_stages", "ai_disclosure"].includes(slotId) && (findFirst(line, STAGE_EVENTS) || (headingName(line) === null && ["item", "mark", "note"].includes(lineMarker(line)[0])))) heading = false;
      if (slotId === "contact_point" && findFirst(line, CONTACT_CHANNEL)) heading = false;
      if (heading) {
        if (["selection_stages", "ai_disclosure", "qualification_rationale"].includes(slotId)) continue;
        const unit = labelBodyUnit(section, definition, slotId, line, right, match.start);
        if (unit) units.push(unit);
      } else if (contextAllowed(slotId, line, section, match.start) && contextAllowed(slotId, evidenceLine(section.text, match.start, match.end), section, match.start)) {
        units.push({start:match.start, end:match.end, context:line});
      }
    }
    if (slotId === "contact_point" && section.name === "문의처" && !units.length) {
      // The 문의처 heading itself labels its body (a phone, e-mail or department).
      const right = lineBounds(section.text, 0, 0)[1];
      const unit = labelBodyUnit(section, definition, slotId, section.text.slice(0, right), right, 0);
      if (unit) units.push(unit);
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

  // --- protective clauses: same patterns and steps as core.extractor -----------
  // A protective verb silences a sensitive candidate only inside the candidate's
  // own clause (sentence mark, ";", a connective ending, or a comma that neither
  // follows a conditional nor joins one noun list). An omission penalty
  // ("미기재 시 탈락") is a requirement, not a protection.
  const SENSITIVE_CANDIDATE = ["출신학교", "출신 학교", "신체 조건", "혼인", "부모", "형제자매", "가족"];
  const NOT_AN_INFORMATION_ITEM = ["re:가족\\s*같은"];
  const PROTECTIVE = [
    "re:(?:수집|기재|작성)\\s*(?:금지|불가|불필요|하지|받지)",
    "re:(?:포함|제공)\\s*하지",
    "re:(?:기재|표현)[^.!?\\n]{0,100}(?:평가대상에서\\s*제외|부적합|탈락처리)",
    "re:블라인드[^.!?\\n]{0,80}미준수",
    "re:(?:미기재|미작성|미제출|누락)\\s*(?:시|하면|할\\s*경우|된\\s*경우)[^.!?\\n]{0,60}?(?:불이익|탈락|제외|부적합|감점)[^.!?\\n,]{0,12}(?:(?<=[\\s은는이가도을를지익점락외합])(?:없|않)(?!도록|게)|아니|아닙|아님)",
  ];
  const PROTECTIVE_INDIFFERENT = ["re:^\\s*(?:(?:과|와|은|는|의|에)\\s*)?(?:무관|관계\\s*없|상관\\s*없|불문|제한\\s*없)"];
  const PROTECTIVE_CONTRAST = [
    "re:(?:^|[\\s,(（\\[])(?:단|다만|하지만|그러나|그렇지만)(?=[\\s,:：)）\\]]|$)",
    "re:^\\S*(?:지만|으나|하나|는데)(?=[\\s,]|$)",
  ];
  const PROTECTIVE_OMISSION_PENALTY = [
    "re:(?:(?:미기재|미작성|미제출|누락)\\s*(?:시|하면|할\\s*경우|된\\s*경우)|(?:기재|작성|제출)\\s*하지\\s*않(?:으면|을\\s*경우|는\\s*경우|은))[^.!?\\n]{0,60}?(?:불이익|탈락|제외|부적합|감점)(?![^.!?\\n,]{0,12}(?:(?<=[\\s은는이가도을를지익점락외합])(?:없|않)(?!도록|게)|아니|아닙|아님))",
  ];
  const PROTECTIVE_REQUIREMENT = [
    "re:(?:필수|요구|제출해야|제출\\s*필수)",
    "re:(?:기재|작성)해야|기재하(?:십시오|세요)|기재해\\s*주(?:세요|십시오)|제출하(?:십시오|세요)",
    "re:(?:수집|기재|작성|포함|제공)\\s*(?:하지|받지)\\s*(?:않는\\s*것(?:은|이|을)?\\s*(?:허용되지\\s*않|허용하지\\s*않|금지|불가)|않을\\s*수\\s*없)",
  ];
  const PROTECTIVE_CLAUSE_END = [
    "re:;",
    "re:(?:(?:하|되|않|마시|말|주시|하시|받|있|없|이)고|(?<=[가-힣])며|(?:되|으)나|(?<=[가-힣])하나|지만)(?=[\\s,]|$)",
  ];
  const PROTECTIVE_COMMA = ["re:(?<!\\d),|,(?!\\d)"];
  const PROTECTIVE_CONDITIONAL_TAIL = [
    "re:(?:경우|때|으면|하면|되면|이면|라면|다면|(?:기재|작성|제출|포함|표기|노출|기입|명시|지원|위반|적발|확인|발견|누락)\\s*시)[)\\]）】」』\\s]*$",
  ];
  const PROTECTIVE_PREDICATE_TAIL = [
    "re:(?:[다요음함됨임]|시오|며|고|나|지만|되|아도|어도|해도|여도|이상|이하|미만|초과|이내|우대|필수|가능|불가|금지|불필요|무관|환영|제외|요망|바람|선호|필요|요구|탈락|처리|부적합|기재|작성|제출|첨부|입력|명시|표기|확인|진행|실시|모집|채용|접수|포함|요청|\\d\\S*)[)\\]）】」』\\s]*$",
  ];
  const PROTECTIVE_NON_LIST_HEAD = [
    "re:^(?:\\S*(?:에|에서|에게|께|께서|으로|로|부터|까지|보다|에는|에도|에선|에서는|으로는|로는)|반드시|꼭|단|다만|또한|그리고|하지만|그러나|특히|함께|바로)$",
  ];
  const PROTECTIVE_DIRECT_GAP = ["re:^\\S*(?:\\s+(?:일체|절대|절대로|모두|전혀|별도로|따로|굳이))*\\s*$"];
  const PROTECTIVE_TOKEN = ["re:\\S+"];
  const PROTECTIVE_LIST_ITEM_TOKENS = 4;

  function protectiveCommaJoins(left, right, item) {
    if (findFirst(left, PROTECTIVE_CONDITIONAL_TAIL)) return true;
    if (findFirst(left, PROTECTIVE_PREDICATE_TAIL)) return false;
    const head = findFirst(right, PROTECTIVE_TOKEN);
    if (!head || findFirst(head.text, PROTECTIVE_NON_LIST_HEAD)) return false;
    return item === null || findMatches(item, PROTECTIVE_TOKEN).length <= PROTECTIVE_LIST_ITEM_TOKENS;
  }

  // Mirrors _protective_sentence: the sentence span inside the evidence window.
  function protectiveSentence(source, start, end) {
    let [left, right] = sentenceBounds(source, start, end);
    if (codePointLength(source.slice(left, right)) > 238) {
      const codePoints = Array.from(source.slice(left, right));
      // Never look further than the evidence window the reviewer is shown.
      const relativeStart = Array.from(source.slice(left, start)).length;
      const relativeEnd = Array.from(source.slice(left, end)).length;
      let windowStart = Math.max(0, relativeStart - 96);
      const windowEnd = Math.min(codePoints.length, Math.max(relativeEnd + 96, windowStart + 238));
      windowStart = Math.max(0, windowEnd - 238);
      const base = left;
      left = base + codePoints.slice(0, windowStart).join("").length;
      right = base + codePoints.slice(0, windowEnd).join("").length;
    }
    return [left, right];
  }

  function protectiveClause(source, start, end) {
    const [left, right] = protectiveSentence(source, start, end);
    const sentence = source.slice(left, right);
    const s = start - left;
    const e = end - left;
    const ends = findMatches(sentence, PROTECTIVE_CLAUSE_END);
    let lo = ends.filter((m) => m.end <= s).reduce((best, m) => Math.max(best, m.end), 0);
    let hi = ends.filter((m) => m.start >= e).reduce((best, m) => Math.min(best, m.end), sentence.length);
    const commas = findMatches(sentence, PROTECTIVE_COMMA).map((m) => m.start).filter((c) => lo <= c && c < hi && !(s <= c && c < e));
    const following = commas.filter((c) => c >= e);
    let previous = null;
    for (let index = 0; index < following.length; index += 1) {
      const comma = following[index];
      const nextCut = index + 1 < following.length ? following[index + 1] : hi;
      const segmentStart = previous === null ? s : previous + 1;
      const itemStart = previous === null ? e : previous + 1;
      if (!protectiveCommaJoins(sentence.slice(segmentStart, comma), sentence.slice(comma + 1, nextCut), sentence.slice(itemStart, comma))) {
        hi = comma;
        break;
      }
      previous = comma;
    }
    const preceding = commas.filter((c) => c < s);
    for (let index = preceding.length - 1; index >= 0; index -= 1) {
      const comma = preceding[index];
      const later = commas.filter((c) => c > comma);
      const nextCut = later.length ? later[0] : hi;
      const earlier = index ? preceding[index - 1] : null;
      const segment = sentence.slice(earlier === null ? lo : earlier + 1, comma);
      // The first list item may carry a lead-in ("수집 금지 항목: 부모 직업").
      if (!protectiveCommaJoins(segment, sentence.slice(comma + 1, nextCut), earlier === null ? null : segment)) {
        lo = comma + 1;
        break;
      }
    }
    return [left + lo, left + hi];
  }

  function protectiveClauseGoverns(source, match) {
    const [clauseStart, clauseEnd] = protectiveClause(source, match.start, match.end);
    const indifferent = findFirst(source.slice(match.end, clauseEnd), PROTECTIVE_INDIFFERENT);
    if (indifferent) {
      // An indifference stops protecting once a contrast follows it.
      const sentenceEnd = protectiveSentence(source, match.start, match.end)[1];
      return !findFirst(source.slice(match.end + indifferent.end, sentenceEnd), PROTECTIVE_CONTRAST);
    }
    const clause = source.slice(clauseStart, clauseEnd);
    if (findFirst(clause, PROTECTIVE_OMISSION_PENALTY)) return false;
    if (!findFirst(clause, PROTECTIVE)) return false;
    const tail = Array.from(source.slice(match.end, clauseEnd)).slice(0, 60).join("");
    const requirement = findFirst(tail, PROTECTIVE_REQUIREMENT);
    if (!requirement) return true;
    // A protection directly after the candidate governs it; a later
    // requirement then belongs to another item.
    const following = findFirst(tail, PROTECTIVE);
    return Boolean(following) && following.start < requirement.start && Boolean(findFirst(tail.slice(0, following.start), PROTECTIVE_DIRECT_GAP));
  }

  function sourceCandidateAllowed(source, match, sections, layer) {
    const line = evidenceLine(source, match.start, match.end);
    const section = sections.find((s) => s.start <= match.start && match.start < s.end);
    if (findFirst(match.text, SENSITIVE_CANDIDATE) && !findFirst(match.text, NOT_AN_INFORMATION_ITEM) && protectiveClauseGoverns(source, match)) return false;
    if (layer === "question" && findFirst(match.text, ["면접", "인터뷰"])) {
      if (section && inDutyBlock(section, match.start - section.start)) return false;
      if (findFirst(line, ["인터뷰 자세히", "인터뷰 보기", "팀원 인터뷰", "현직자 인터뷰"])) return false;
      if (bareStageMatch(source, match.start, match.end) && findFirst(line, DUTY_CONTEXT) && !hiringContext(line, section)) return false;
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
