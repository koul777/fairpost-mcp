(function (global) {
  "use strict";

  const DISCLAIMER =
    "이 결과는 점검 참고자료이며 공정성 여부에 대한 판정이나 법률 자문이 아닙니다. " +
    "확인되지 않은 항목은 해당 절차가 없다는 뜻이 아니라 이 공고문에서 발견되지 않았다는 뜻입니다.";

  const SECTION_ALIASES = [
    ["개요", ["채용개요", "모집개요", "공고개요"]],
    ["자격요건", ["자격요건", "지원자격", "응시자격", "필수요건"]],
    ["우대사항", ["우대사항", "가점사항", "우대조건"]],
    ["전형절차", ["전형절차", "전형방법", "선발절차", "채용절차"]],
    ["일정", ["전형일정", "채용일정", "일정"]],
    ["근무조건", ["근무조건", "근로조건", "보수", "급여"]],
    ["제출서류", ["제출서류", "지원서류", "구비서류"]],
    ["유의사항", ["유의사항", "주의사항"]],
    ["문의처", ["문의처", "문의", "연락처"]],
    ["기타", ["기타"]],
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

  function normalize(text) {
    return String(text);
  }

  function escapeRegex(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function patternToRegex(pattern) {
    if (pattern.startsWith("re:")) {
      return new RegExp(pattern.slice(3), "giu");
    }
    const expression = pattern
      .normalize("NFC")
      .split(/(\s+)/u)
      .map((part) => (/\s/u.test(part) ? "\\s*" : escapeRegex(part)))
      .join("");
    return new RegExp(expression, "giu");
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
        const replacement = /\s/u.test(character)
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
    const matchView = matchTextWithOffsets(text);
    const needsMatchView = matchView.normalized !== text;
    patterns.forEach((pattern) => {
      const regex = patternToRegex(pattern);
      let match;
      while ((match = regex.exec(text)) !== null) {
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
      if (pattern.startsWith("re:")) return;
      if (!needsMatchView) return;
      const normalizedPattern = matchPlain(pattern);
      const normalizedRegex = patternToRegex(normalizedPattern);
      while ((match = normalizedRegex.exec(matchView.normalized)) !== null) {
        if (match[0].length === 0) {
          normalizedRegex.lastIndex += 1;
          continue;
        }
        const start = matchView.starts[match.index];
        const end = matchView.ends[match.index + match[0].length - 1];
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
      const window = Number(exclusion.window || 0);
      return Boolean(
        findFirst(codePointWindow(text, match, window), [String(exclusion.term)])
      );
    });
  }

  function headingName(line) {
    const cleaned = line
      .trim()
      .replace(/^[\s#>*\-–—\d.()①-⑳]+|[\s:：]+$/gu, "");
    if (!cleaned || cleaned.length > 30) return null;
    const compact = cleaned.replace(/\s+/gu, "");
    for (const [canonical, aliases] of SECTION_ALIASES) {
      if (aliases.some((alias) => compact === alias.replace(/\s+/gu, ""))) {
        return canonical;
      }
    }
    return null;
  }

  function splitSections(text) {
    const headings = [];
    let cursor = 0;
    const lines = text.match(/[^\n]*\n|[^\n]+$/gu) || [];
    lines.forEach((line) => {
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
    const prior = text.lastIndexOf("\n", start - 1);
    const lineStart = prior + 1;
    const next = text.indexOf("\n", end);
    const lineEnd = next === -1 ? text.length : next;
    return text.slice(lineStart, lineEnd).trim().slice(0, 240);
  }

  function extractSlots(text, sections, definitions) {
    return Object.keys(definitions)
      .sort()
      .map((slotId) => {
        const definition = definitions[slotId];
        const preferred = sections.filter((section) =>
          (definition.search_sections || []).includes(section.name)
        );
        const searchOrder = preferred.concat(
          sections.filter((section) => !preferred.includes(section))
        );
        let match = null;
        let matchedSection = null;
        for (const section of searchOrder) {
          match = findFirst(section.text, definition.accept_patterns || []);
          if (match) {
            matchedSection = section;
            break;
          }
        }
        const componentsFound = (definition.components || [])
          .filter((component) => findFirst(text, component.patterns || []))
          .map((component) => component.id)
          .sort();
        let evidence = null;
        let section = null;
        if (match && matchedSection) {
          const start = matchedSection.start + match.start;
          const end = matchedSection.start + match.end;
          evidence = evidenceLine(text, start, end);
          section = matchedSection.name;
        }
        return {
          slot: slotId,
          label: definition.label,
          found: Boolean(match),
          components_found: componentsFound,
          components_total: (definition.components || []).length,
          evidence,
          section,
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

    data.rules.forEach((rule) => {
      const trigger = rule.trigger;
      let match = null;
      let matched = false;
      if (trigger.type === "absence") {
        matched = !slotsById[trigger.field].found;
      } else {
        match =
          findMatches(source, trigger.patterns).find(
            (candidate) =>
              !isExcluded(source, candidate, trigger.exclude || []) &&
              (!trigger.section_scope ||
                sectionAt(sections, candidate.start) === trigger.section_scope) &&
              matchesContextGroups(source, candidate, trigger)
          ) || null;
        matched = Boolean(match);
      }
      if (!matched) return;

      if (rule.layer === "law") {
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
      } else {
        questions.push({
          id: rule.id,
          dimension: rule.dimension,
          question: rule.question,
          follow_up: [...(rule.follow_up || [])],
          basis_type: rule.basis.type,
          book_ref: rule.book_ref,
          review_scope: rule.review_scope || "posting",
          saved_answer: answers[rule.id] ?? null,
          matched_text: match ? match.text : null,
          offset: match
            ? [codePointOffset(source, match.start), codePointOffset(source, match.end)]
            : null,
          section: match ? sectionAt(sections, match.start) : null,
          reference: makeQuestionReference(rule),
        });
      }
    });

    findings.sort((a, b) => a.id.localeCompare(b.id));
    questions.sort((a, b) => a.id.localeCompare(b.id));
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
