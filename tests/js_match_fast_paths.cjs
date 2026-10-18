// Test-only IIFE probe, as in js_regex_conditions.cjs. No production exports are added.
// Checks the matching shortcuts of web/engine.js against plain reference versions:
// findMatches (literal prefilter, code-unit pair filter, shared text views, span
// dedupe) against running every regex on freshly built views, targetIncludes
// against String.prototype.includes, and the code-point window cuts of
// evidenceLine, protectiveSentence and headingLike against Array.from.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "web/engine.js"), "utf8");
const closing = source.lastIndexOf("})(");
assert.ok(closing >= 0, "engine IIFE closing call");
const context = { window: {} };
context.globalThis = context.window;
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(root, "web/data.js"), "utf8"), context);
vm.runInContext(source.slice(0, closing) +
  "\n global.__fastPathProbe = { findMatches, patternToRegex, matchPlain, normalizedTextWithOffsets," +
  " matchTextWithOffsets, scanTarget, targetIncludes, evidenceLine, sentenceBounds," +
  " protectiveSentence, headingLike, stripHeadingDecoration, trimPythonWhitespace, SENTENCE_END };\n" +
  source.slice(closing), context, { filename: "web/engine.js" });
const probe = context.window.__fastPathProbe;
const plain = (value) => JSON.parse(JSON.stringify(value));

// --- findMatches -------------------------------------------------------------
// Every regex runs on views built from scratch, with no filter of any kind.
function referenceFindMatches(text, patterns) {
  const normalized = probe.normalizedTextWithOffsets(text);
  const matched = probe.matchTextWithOffsets(text);
  const spans = new Map();
  const add = (start, end) => {
    const key = `${start}:${end}`;
    if (!spans.has(key)) spans.set(key, { start, end, text: text.slice(start, end) });
  };
  for (const pattern of patterns) {
    const isRegex = pattern.startsWith("re:");
    const regex = probe.patternToRegex(pattern);
    const viewRegex = probe.patternToRegex(isRegex ? pattern : probe.matchPlain(pattern));
    let match;
    if (!(isRegex && normalized.normalized !== text)) {
      while ((match = regex.exec(text)) !== null) {
        add(match.index, match.index + match[0].length);
        if (match[0].length === 0) regex.lastIndex += 1;
      }
    }
    const view = isRegex
      ? (normalized.normalized !== text ? normalized : null)
      : (matched.normalized !== text ? matched : null);
    if (view === null) continue;
    while ((match = viewRegex.exec(view.normalized)) !== null) {
      if (match[0].length === 0) {
        viewRegex.lastIndex += 1;
        continue;
      }
      add(view.starts[match.index], view.ends[match.index + match[0].length - 1]);
    }
  }
  return [...spans.values()].sort((a, b) => a.start - b.start || a.end - b.end);
}

function* strings(value) {
  if (typeof value === "string") yield value;
  else if (value && typeof value === "object") {
    for (const child of Object.values(value)) yield* strings(child);
  }
}

const data = context.window.FAIRPOST_DATA;
const patternLists = [];
for (const rule of data.rules) {
  if (rule.trigger.patterns) patternLists.push(rule.trigger.patterns);
  for (const exclusion of rule.trigger.exclude || []) patternLists.push([String(exclusion.term)]);
}
for (const slot of Object.values(data.slots)) {
  if (slot.accept_patterns) patternLists.push(slot.accept_patterns);
  for (const component of slot.components || []) if (component.patterns) patternLists.push(component.patterns);
}
const allPatterns = [...new Set(patternLists.flat())];
const literals = allPatterns.filter((pattern) => !pattern.startsWith("re:"));
assert.ok(literals.length > 50 && allPatterns.length > literals.length);

// Texts built from the rules' own wording, with the characters the views
// rewrite (compatibility forms, zero-width marks, odd whitespace, morph
// sources) and surrogate pairs mixed in.
const ZW = "​";
const EMOJI = "\u{1F600}";
const decorate = [
  (value) => value,
  (value) => value.replace(/\s/g, "　"),
  (value) => value.split("").join(ZW),
  (value) => value.replace(/1/g, "１").replace(/\(/g, "（"),
  (value) => `${EMOJI}${value}합니다${EMOJI}`,
  (value) => value.toUpperCase(),
  // Already rewritten ("합니다" as "함"): only the match view regex finds it.
  (value) => probe.matchPlain(value),
];
const wordings = literals.filter((value) => value.length <= 40);
const texts = [];
for (let index = 0; index < 60; index += 1) {
  const pieces = [];
  for (let step = 0; step < 6; step += 1) {
    const word = wordings[(index * 37 + step * 11) % wordings.length];
    pieces.push(decorate[(index + step) % decorate.length](word));
  }
  const glue = ["\n", " ", ". ", ", ", "\t", " 않습니다 "][index % 6];
  texts.push(pieces.join(glue));
}
// Literals the views rewrite, written as the view already reads them.
const rewritten = literals.filter((value) => probe.matchPlain(value) !== value);
assert.ok(rewritten.length > 0, "some literals change in the match view");
// A trailing "합니다" makes the text differ from its view, so the view is searched.
texts.push(rewritten.map((value) => `${probe.matchPlain(value)} 합니다`).join("\n"));
texts.push("모집공고\n" + " ".repeat(5000) + wordings.slice(0, 20).join(" "));
texts.push(wordings.slice(0, 80).join(" ").repeat(3));
texts.push("\ud800" + wordings.slice(0, 10).join("\udc00"));

let compared = 0;
let nonEmpty = 0;
let viewMatches = 0;
for (let round = 0; round < 2; round += 1) {
  // The second round meets cached views and built pair filters.
  for (const text of texts) {
    for (const patterns of [allPatterns, ...patternLists.slice(0, 40)]) {
      const expected = referenceFindMatches(text, patterns);
      assert.deepEqual(plain(probe.findMatches(text, patterns)), plain(expected), JSON.stringify({ text: text.slice(0, 80), patterns: patterns.slice(0, 3) }));
      compared += 1;
      if (expected.length) nonEmpty += 1;
      if (patterns === allPatterns && probe.normalizedTextWithOffsets(text).normalized !== text && expected.length) viewMatches += 1;
    }
  }
}
// Every text is built from rule wording, so the full list finds something in
// each, including texts whose normalized view differs from the source.
assert.ok(nonEmpty >= 2 * texts.length, `${nonEmpty} non-empty of ${compared}`);
assert.ok(viewMatches > 20, `${viewMatches} matches through a rewritten view`);

// --- targetIncludes -------------------------------------------------------------
let includesChecks = 0;
for (const text of texts) {
  const target = probe.scanTarget(text);
  // Enough lookups that the filter is built part-way through.
  for (const literal of [...literals.slice(0, 120), "\ud800", EMOJI, "가", "", text.slice(3, 9)]) {
    assert.equal(probe.targetIncludes(target, literal), text.includes(literal), literal);
    includesChecks += 1;
  }
}

// --- code-point windows ---------------------------------------------------------
function referenceEvidenceLine(text, start, end) {
  const [segmentStart, segmentEnd] = probe.sentenceBounds(text, start, end);
  const segment = text.slice(segmentStart, segmentEnd);
  const limit = 238;
  if (Array.from(segment).length <= limit) return probe.trimPythonWhitespace(segment);
  const codePoints = Array.from(segment);
  const relativeStart = Array.from(text.slice(segmentStart, start)).length;
  const relativeEnd = Array.from(text.slice(segmentStart, end)).length;
  let windowStart = Math.max(0, relativeStart - 96);
  const windowEnd = Math.min(codePoints.length, Math.max(relativeEnd + 96, windowStart + limit));
  windowStart = Math.max(0, windowEnd - limit);
  const evidence = probe.trimPythonWhitespace(codePoints.slice(windowStart, windowEnd).join(""));
  return `${windowStart ? "…" : ""}${evidence}${windowEnd < codePoints.length ? "…" : ""}`;
}

function referenceProtectiveSentence(text, start, end) {
  let [left, right] = probe.sentenceBounds(text, start, end);
  if (Array.from(text.slice(left, right)).length > 238) {
    const codePoints = Array.from(text.slice(left, right));
    const relativeStart = Array.from(text.slice(left, start)).length;
    const relativeEnd = Array.from(text.slice(left, end)).length;
    let windowStart = Math.max(0, relativeStart - 96);
    const windowEnd = Math.min(codePoints.length, Math.max(relativeEnd + 96, windowStart + 238));
    windowStart = Math.max(0, windowEnd - 238);
    const base = left;
    left = base + codePoints.slice(0, windowStart).join("").length;
    right = base + codePoints.slice(0, windowEnd).join("").length;
  }
  return [left, right];
}

function referenceHeadingLike(line) {
  const cleaned = Array.from(probe.stripHeadingDecoration(line));
  return cleaned.length > 0 && cleaned.length <= 30 && !probe.SENTENCE_END.includes(cleaned[cleaned.length - 1]);
}

const longTexts = [
  "가".repeat(400),
  `${EMOJI}가`.repeat(300),
  ` 가나 ${EMOJI} 다`.repeat(120) + "." + "라마".repeat(200),
  "\udc00가\ud800".repeat(200),
  ("자격요건 " + EMOJI + " 경력 3년 이상 우대 ").repeat(40),
  "짧은 문장. " + "가".repeat(500) + "! 끝",
];
let windowChecks = 0;
for (const text of longTexts) {
  for (let start = 0; start < text.length; start += 17) {
    for (const width of [0, 1, 2, 5, 40]) {
      const end = Math.min(text.length, start + width);
      assert.equal(probe.evidenceLine(text, start, end), referenceEvidenceLine(text, start, end), `evidence ${start}-${end}`);
      assert.deepEqual(plain(probe.protectiveSentence(text, start, end)), referenceProtectiveSentence(text, start, end), `protective ${start}-${end}`);
      windowChecks += 1;
    }
  }
}
let headingChecks = 0;
for (const line of [...texts.flatMap((text) => text.split("\n")), ...longTexts, "", " ", "■ 자격요건", `자격요건${EMOJI}`, "가\ud800", "다.", "모집 요강임"]) {
  assert.equal(probe.headingLike(line), referenceHeadingLike(line), line.slice(0, 60));
  headingChecks += 1;
}

process.stdout.write(JSON.stringify({ patterns: allPatterns.length, texts: texts.length, compared, nonEmpty, includesChecks, windowChecks, headingChecks }));
