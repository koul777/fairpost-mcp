// Test-only IIFE probe, as in js_match_fast_paths.cjs. No production exports are added.
// Compares the 2026-10-20 speed shortcuts of web/engine.js with plain reference versions:
// kept-run identity check, heading letter bound, pattern-list gates and shared scans,
// slot accept patterns restricted to the posting, and the candidate trait memo.
// Special characters are built from code points: this file holds no escape sequences
// that an editor or a tool could turn into raw characters.
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
  "\n global.__perfProbe = { findMatches, findFirst, extractSlots, splitSections, headingName, uncachedHeadingName," +
  " normalizedTextWithOffsets, textViews, patternToRegex, matchPlain, KEPT_RUN, sourceCandidateAllowed, evidenceLine," +
  " protectiveClauseGoverns, inDutyBlock, bareStageMatch, hiringContext, codePointLength, SENSITIVE_CANDIDATE," +
  " NOT_AN_INFORMATION_ITEM, DUTY_CONTEXT };\n" +
  source.slice(closing), context, { filename: "web/engine.js" });
const probe = context.window.__perfProbe;
const engine = context.window.FairpostEngine;
const data = context.window.FAIRPOST_DATA;
const plain = (value) => JSON.parse(JSON.stringify(value));
const chr = (...codes) => String.fromCodePoint(...codes);

// Deterministic pseudo-random numbers, so a failure can be replayed.
let seed = 20261020;
const random = () => {
  seed = (seed * 1664525 + 1013904223) >>> 0;
  return seed / 4294967296;
};
const pick = (list) => list[Math.floor(random() * list.length)];

const ZERO_WIDTH = chr(0x200b, 0x200c, 0x200d, 0xfeff);
const pythonSpaceCodes = [0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680,
  0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a,
  0x2028, 0x2029, 0x202f, 0x205f, 0x3000];
const PY_SPACE = new RegExp("^[" + chr(...pythonSpaceCodes) + "]$", "u");

// --- kept-run identity check ------------------------------------------------
// Every code unit the run accepts is one the per-character rewrite keeps, so
// a text made of such units is returned as its own identity view.
function referenceNormalized(text) {
  let normalized = "";
  const starts = [];
  const ends = [];
  let cursor = 0;
  for (const character of text) {
    let replacement;
    if (ZERO_WIDTH.includes(character)) replacement = "";
    else if (character === "\r" || character === "\n" || character === "\t") replacement = character;
    else if (PY_SPACE.test(character)) replacement = " ";
    else replacement = character.normalize("NFKC");
    normalized += replacement;
    for (let unit = 0; unit < replacement.length; unit += 1) {
      starts.push(cursor);
      ends.push(cursor + character.length);
    }
    cursor += character.length;
  }
  return { normalized, starts, ends };
}

let keptUnits = 0;
for (let code = 0; code <= 0xffff; code += 1) {
  const character = String.fromCharCode(code);
  probe.KEPT_RUN.lastIndex = 0;
  probe.KEPT_RUN.test(character);
  if (probe.KEPT_RUN.lastIndex !== 1) continue;
  keptUnits += 1;
  assert.equal(character.normalize("NFKC"), character, `kept run accepts U+${code.toString(16)}`);
  assert.ok(!ZERO_WIDTH.includes(character));
  assert.ok(!PY_SPACE.test(character) || " \r\n\t".includes(character), `U+${code.toString(16)} is whitespace`);
  const view = probe.normalizedTextWithOffsets(character);
  assert.equal(view.normalized, character, `kept run accepts U+${code.toString(16)}`);
}
assert.ok(keptUnits > 20000, `${keptUnits} code units are in the kept run`);

const alphabet = [
  "가", "힣", "a", "Z", "0", " ", "\n", "\r\n", "\t", chr(0xb7), chr(0x2013), chr(0x201c), chr(0x2022), chr(0x203b),
  chr(0x25a0), chr(0x2192), chr(0x3002), chr(0x3010), chr(0x4e00), "漢",
  chr(0x2026), chr(0x2460), chr(0xff08), chr(0xff21), chr(0xff11), chr(0xa0), chr(0x3000), chr(0x200b), chr(0xfeff),
  chr(0x2028), "\v", chr(0x3131), chr(0x318d), chr(0x321c), chr(0xfb01),
  chr(0x1f600), chr(0x1d7cf), String.fromCharCode(0xd800), String.fromCharCode(0xdc00),
  "합니다", "하시는", "않습니다",
];
let normalizedChecks = 0;
for (let round = 0; round < 3000; round += 1) {
  let text = "";
  const length = Math.floor(random() * 40);
  for (let index = 0; index < length; index += 1) text += pick(alphabet);
  // A long safe prefix before the first changed unit exercises the native skip.
  if (round % 3 === 0) text = "가나다 abc 123.\n".repeat(1 + (round % 7)) + text;
  const expected = referenceNormalized(text);
  const view = probe.normalizedTextWithOffsets(text);
  assert.equal(view.normalized, expected.normalized, JSON.stringify(text));
  if (!view.identity) {
    assert.deepEqual(Array.from(view.starts), expected.starts, JSON.stringify(text));
    assert.deepEqual(Array.from(view.ends), expected.ends, JSON.stringify(text));
  } else {
    assert.equal(expected.normalized, text);
  }
  normalizedChecks += 1;
}

// --- heading letter bound -----------------------------------------------------
// headingName() answers null for a line with more than 30 letters; it must
// agree with the version without that shortcut on lines of any mix.
const headingParts = [
  "채용", "공고", "가", "나", "다", "A", "b", "한자", "漢", " ", "  ", "-", "1", "2.", "(", ")", "[", "]", "#",
  chr(0x203b), chr(0x25a0), chr(0xff1a), ":", chr(0x200b), chr(0xff22), chr(0xff11), "합류 여정", "전형절차", "담당업무",
  "자격요건", "우대사항", "문의처", "\n",
];
let headingChecks = 0;
let longRejected = 0;
for (let round = 0; round < 4000; round += 1) {
  let line = "";
  const length = 1 + Math.floor(random() * 22);
  for (let index = 0; index < length; index += 1) line += pick(headingParts);
  const expected = probe.uncachedHeadingName(line);
  assert.equal(probe.headingName(line), expected, JSON.stringify(line));
  // Twice, so the cached answer is compared as well.
  assert.equal(probe.headingName(line), expected, JSON.stringify(line));
  headingChecks += 1;
  if (line.length > 30 && expected === null) longRejected += 1;
}
for (const heading of ["전형절차", "[전형절차]", "1. 담당업무", chr(0x25a0) + " 문의처 :"]) {
  assert.equal(probe.headingName(heading), probe.uncachedHeadingName(heading), heading);
}
assert.ok(longRejected > 100, `${longRejected} long lines were rejected`);

// --- reference find over a pattern list ---------------------------------------
// Every regex runs on views built from scratch, with no gate and no sharing.
function referenceFindMatches(text, patterns) {
  const normalized = probe.normalizedTextWithOffsets(text);
  const matched = probe.textViews(text).matched;
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
    // The searched view: normalized for a regex, normalized and morphed for a literal.
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

const lists = [];
for (const rule of data.rules) {
  if (rule.trigger.patterns) lists.push(rule.trigger.patterns);
}
for (const slot of Object.values(data.slots)) {
  if (slot.accept_patterns) lists.push(slot.accept_patterns);
  for (const component of slot.components || []) if (component.patterns) lists.push(component.patterns);
}
const allPatterns = [...new Set(lists.flat())];
const literals = allPatterns.filter((pattern) => !pattern.startsWith("re:"));

// Texts from the rules' own wording, plain and rewritten by the views, long
// enough (>= 256 units) that scans are shared between lists.
const wordings = literals.filter((value) => value.length <= 30);
function wordingText(count, glue, decorate) {
  const pieces = [];
  for (let index = 0; index < count; index += 1) pieces.push(decorate(pick(wordings)));
  return pieces.join(glue);
}
const decorations = [
  (value) => value,
  (value) => `${value}합니다`,
  (value) => value.replace(/ /g, chr(0x3000)),
  (value) => value.split("").join(chr(0x200b)),
  (value) => `${value} 않습니다`,
  (value) => value.toUpperCase(),
];
const texts = [];
for (let index = 0; index < 40; index += 1) {
  const decorate = decorations[index % decorations.length];
  texts.push(wordingText(5 + (index % 40), pick(["\n", " ", ". ", ", "]), decorate));
}
texts.push("", " ", "가", "채용".repeat(200), "AI 면접 ".repeat(80) + chr(0xd83d));

// Lists of every size: the stable ones are used repeatedly (their plans and gates
// are kept), the sampled ones are rebuilt arrays.
const sampled = [];
for (let index = 0; index < 60; index += 1) {
  const size = 1 + Math.floor(random() * 12);
  const list = [];
  for (let step = 0; step < size; step += 1) list.push(pick(allPatterns));
  sampled.push(list);
}
let listChecks = 0;
let gatedNonEmpty = 0;
for (let round = 0; round < 3; round += 1) {
  // Later rounds meet the kept gates, plans and shared scans.
  for (const text of texts) {
    for (const patterns of [...lists.slice(0, 80), ...sampled, allPatterns]) {
      const expected = referenceFindMatches(text, patterns);
      const found = plain(probe.findMatches(text, patterns));
      assert.deepEqual(found, expected, JSON.stringify({ text: text.slice(0, 60), patterns: patterns.slice(0, 3) }));
      listChecks += 1;
      if (expected.length) gatedNonEmpty += 1;
    }
  }
}
assert.ok(listChecks > 10000, `${listChecks} list checks`);
assert.ok(gatedNonEmpty > 2000, `${gatedNonEmpty} lists found something`);

// Lists whose patterns only the match view finds: the text holds the wording the
// view rewrites ("합니다" becomes "함"), the patterns are the rewritten forms.
const morphSources = ["않습니다", "있으신", "하시는", "합니다", "하신", "이신", "으신"];
const rewritten = [...literals.filter((value) => probe.matchPlain(value) !== value), ...morphSources];
const viewOnlyText = rewritten.join("\n") + "\n" + morphSources.join(" ").repeat(6) + "\n" + "가나다 라마바 ".repeat(30);
const viewOnlyLists = [];
for (let index = 0; index + 3 <= rewritten.length; index += 3) {
  viewOnlyLists.push(rewritten.slice(index, index + 3).map((value) => probe.matchPlain(value)));
}
viewOnlyLists.push(["함", "한", "인"], ["않음", "있는", "하는"], ["은", "함", "가나다 라마바"]);
let viewOnlyFound = 0;
for (let again = 0; again < 2; again += 1) {
  for (const patterns of viewOnlyLists) {
    const expected = referenceFindMatches(viewOnlyText, patterns);
    assert.deepEqual(plain(probe.findMatches(viewOnlyText, patterns)), expected, JSON.stringify(patterns));
    if (expected.length) viewOnlyFound += 1;
  }
}
assert.ok(viewOnlyFound > 0, "some list is found only through the match view");

// Literals that overlap themselves are matched without overlap, as a regex does.
const overlapping = ["가가", "나나나", "다다"];
const overlapText = "가가가가가 나나나나나나나 다다다다다 라".repeat(12);
assert.deepEqual(plain(probe.findMatches(overlapText, overlapping)), referenceFindMatches(overlapText, overlapping));

// A list changed after it was used is read again, not served from the kept plans.
const mutable = ["서류전형", "면접전형", "필기전형"];
const probeText = "서류전형과 필기시험 그리고 면접전형. ".repeat(20);
const before = plain(probe.findMatches(probeText, mutable));
mutable[1] = "필기시험";
const after = plain(probe.findMatches(probeText, mutable));
assert.deepEqual(after, referenceFindMatches(probeText, mutable));
assert.notDeepEqual(after, before);

// Scans are shared between lists on long texts: a caller changing a result does
// not change what the next call returns.
const sharedText = "채용 공고. ".repeat(60);
const first = probe.findMatches(sharedText, ["채용", "공고", "모집"]);
const firstJson = JSON.stringify(first);
first.length = 0;
first.push({ start: -1, end: -1, text: "x" });
assert.equal(JSON.stringify(probe.findMatches(sharedText, ["채용", "공고", "모집"])), firstJson);

// --- slot extraction: accept patterns restricted to the posting --------------------
// extractSlots() reads every accept pattern in every section; check() restricts
// them to the ones the whole posting can hold. Both must give the same slots.
const headings = ["담당업무", "자격요건", "우대사항", "전형절차", "전형일정", "근무조건", "제출서류", "유의사항", "문의처", "기타"];
let slotChecks = 0;
for (let round = 0; round < 250; round += 1) {
  const lines = [];
  const count = 4 + Math.floor(random() * 30);
  for (let index = 0; index < count; index += 1) {
    const kind = random();
    if (kind < 0.2) lines.push(pick(headings));
    else if (kind < 0.7) lines.push(`${pick(["-", chr(0xb7), chr(0x2460), "1."])} ${decorations[round % decorations.length](pick(wordings))}`);
    else lines.push(wordingText(2 + Math.floor(random() * 5), pick([" ", ", ", " / "]), pick(decorations)));
  }
  const text = lines.join(pick(["\n", "\r\n", "\n", chr(0x2028)]));
  const sections = probe.splitSections(text);
  const expected = plain(probe.extractSlots(text, sections, data.slots));
  assert.deepEqual(plain(engine.check(text).slots), expected, JSON.stringify(text.slice(0, 80)));
  slotChecks += 1;
}

// --- candidate checks: trait memo and lazy evidence line ---------------------------
// sourceCandidateAllowed as it was before: the evidence line and the section are
// read first and each wording test runs on its own.
function referenceSourceCandidateAllowed(source, match, sections, layer) {
  const find = probe.findFirst;
  const line = probe.evidenceLine(source, match.start, match.end);
  const section = sections.find((s) => s.start <= match.start && match.start < s.end);
  if (find(match.text, probe.SENSITIVE_CANDIDATE) && !find(match.text, probe.NOT_AN_INFORMATION_ITEM) && probe.protectiveClauseGoverns(source, match)) return false;
  if (layer === "question" && find(match.text, ["면접", "인터뷰"])) {
    if (section && probe.inDutyBlock(section, match.start - section.start)) return false;
    if (find(line, ["인터뷰 자세히", "인터뷰 보기", "팀원 인터뷰", "현직자 인터뷰"])) return false;
    if (probe.bareStageMatch(source, match.start, match.end) && find(line, probe.DUTY_CONTEXT) && !probe.hiringContext(line, section)) return false;
  }
  if (layer === "question" && find(match.text, ["북한이탈주민", "북한이탈 주민", "탈북자"])) {
    const rowStart = source.lastIndexOf("<tr", match.start);
    const rowEnd = source.indexOf("</tr>", match.end);
    let tablePreference = false;
    if (rowStart >= 0 && rowEnd >= 0 && !source.slice(rowStart, match.start).includes("</tr>") && probe.codePointLength(source.slice(rowStart, rowEnd)) < 1800) {
      const row = source.slice(rowStart, rowEnd).replace(/<[^>]+>/gu, "");
      tablePreference = Boolean(find(row, ["가산점", "가점", "re:만점의\\s*\\d+%", "re:점수\\s*만점의\\s*\\d+%"])) && !find(row, ["지원 불가", "채용 제외", "신원조회"]);
    }
    const proofOnly = find(line, ["등록확인서", "증명서"]) && !find(line, ["지원 불가", "채용 제외", "신원조회"]);
    if ((tablePreference || proofOnly || section?.name === "우대사항") && !find(line, ["제외", "불가", "제한", "신원조회"])) return false;
  }
  if (layer === "question" && find(match.text, ["재직 기간", "근속 기간"]) && find(line, ["형법", "범한 자", "벌금형", "선고받"])) return false;
  return true;
}

const traitTexts = [
  "면접 전형을 실시합니다. 인터뷰 자세히 보기",
  "<table><tr><td>북한이탈주민 가산점 5%</td></tr></table>",
  "재직 기간 중 형법 위반으로 벌금형을 선고받은 자",
  "출신학교 기재 금지, 가족관계 수집하지 않음",
  "가족 같은 분위기에서 근무",
  "우대사항\n- 북한이탈주민 등록확인서 제출자",
];
traitTexts.push(
  "<table><tr><td>북한이탈주민 가산점 5%</td><td>비고</td></tr><tr><td>신원조회 결과</td></tr></table>",
  "우대사항\n- 북한이탈주민 우대\n- 탈북자 증명서 제출자",
  "담당업무\n- 인터뷰 진행\n- 면접 설계\n지원자격\n- 지원자 면접 참여 가능",
  "전형절차\n서류 전형\n면접 전형\n인터뷰 자세히 보기\n팀원 인터뷰",
  "재직 기간 3년 이상\n근속 기간 2년 이상 우대",
  "재직 기간 중 형법 위반 범한 자 제외",
  "가족 같은 분위기, 가족관계 증명서 제출. 부모 직업과 형제자매 수집하지 않음. 사진은 부착하지 마세요."
);
const candidateTexts = [...traitTexts, ...texts.slice(0, 30)];
let candidateChecks = 0;
let candidateRejected = 0;
for (const text of candidateTexts) {
  const sections = probe.splitSections(text);
  for (const rule of data.rules) {
    if (rule.trigger.type === "absence") continue;
    for (const match of probe.findMatches(text, rule.trigger.patterns)) {
      const expected = referenceSourceCandidateAllowed(text, match, sections, rule.layer);
      assert.equal(probe.sourceCandidateAllowed(text, match, sections, rule.layer), expected, JSON.stringify({ rule: rule.id, match: match.text }));
      candidateChecks += 1;
      if (!expected) candidateRejected += 1;
    }
  }
}
assert.ok(candidateChecks > 200 && candidateRejected > 5, `${candidateChecks} candidate checks, ${candidateRejected} rejected`);
for (const text of traitTexts) {
  const firstResult = JSON.stringify(engine.check(text));
  for (let again = 0; again < 3; again += 1) assert.equal(JSON.stringify(engine.check(text)), firstResult);
}

console.log(JSON.stringify({
  keptUnits, normalizedChecks, headingChecks, longRejected, listChecks, gatedNonEmpty, viewOnlyFound, slotChecks,
  candidateChecks, candidateRejected,
}));
