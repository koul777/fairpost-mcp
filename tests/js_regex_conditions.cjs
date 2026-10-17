// Test-only IIFE probe, as in js_tables.cjs. No production exports are added.
// With no argument, checks bundled rules against rule-derived sample texts.
// An optional JSON file adds { patterns, texts } (e.g. Python sweep samples).
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
  "\n global.__regexProbe = { regexRequiredConditions, satisfiesRegexConditions, patternPlan, findMatches, pythonRegex, normalizedTextWithOffsets };\n" +
  source.slice(closing), context, { filename: "web/engine.js" });
const probe = context.window.__regexProbe;
const plain = (value) => JSON.parse(JSON.stringify(value));
const conditions = (expression) => plain(probe.regexRequiredConditions(expression));
const satisfies = (text, plan) => probe.satisfiesRegexConditions(text, plan);
const empty = { required: [], requiredAnyOf: [] };

const syntaxCases = [
  ["가나다", ["가나다"], []],
  ["가나?다", ["가", "다"], []],
  ["가나+다", ["가", "다"], []],
  ["가나{2,3}?다", ["가", "다"], []],
  ["가(?:나){0,2}다", ["가", "다"], []],
  ["가(?:나){,2}+다", ["가", "다"], []],
  ["가(?:나)*?다", ["가", "다"], []],
  ["가(?:나)++다", ["가", "나", "다"], []],
  ["가(?:나){1,}다", ["가", "나", "다"], []],
  ["가(나)(?P<word>다)", ["가", "나", "다"], []],
  ["가(?:나|다)라", ["가", "라"], [["나", "다"]]],
  ["가나|다라", [], [["가나", "다라"]]],
  ["가(?:나|)다", ["가", "다"], []],
  ["가(?:나|다)?라", ["가", "라"], []],
  ["(?:나(?:다|라)|마바)", [], [["나", "마바"]]],
  ["(?:(?:가|나)|다)", [], []],
  ["(?:가나[ ]다|마바)", [], [["가나", "마바"]]],
  ["가(?=나(?:다|라))나", ["가", "나"], []],
  ["가(?!나(?:다|라))마", ["가", "마"], []],
  ["(?<=가(?:나|다))라(?<!마)", ["라"], []],
  ["가(?=[]()^]+)나", ["가", "나"], []],
  ["가(?![^]()]*(?:다|라))나", ["가", "나"], []],
  [String.raw`가(?=[\]()]*(?:다|라))나`, ["가", "나"], []],
  ["가[]()^]나", ["가", "나"], []],
  ["가[^]()]나", ["가", "나"], []],
  [String.raw`가\d나\s다\b라\.마\x31바\uAC00사`, ["가", "나", "다", "라", "마", "바", "사"], []],
  [String.raw`(가)\1나`, ["가", "나"], []],
  ["가.^$나", ["가", "나"], []],
  ["가AſKİıÉéΩω中😀나", ["가", "나"], []],
  ["ㄱᄀ가12!:#-_/", ["ㄱᄀ가12!:#-_/"], []],
];
for (const [expression, required, requiredAnyOf] of syntaxCases) {
  assert.deepEqual(conditions(expression), { required, requiredAnyOf }, expression);
}

const unsupported = [
  "가(?i)나", "가(?i:나)", "가(?P=name)나", "가(?#comment)나", "가(?(1)나|다)",
  "가(?>나)다", "가(?<name>나)다", "가{wat}나", "가{1,2", "가{2,1}나",
  "가{9007199254740992}나", "가**나", "가(나", "가)나", "가[나", "가[]나",
  "가\\", String.raw`가\x1나`, String.raw`가\u123나`, String.raw`가\N{HANGUL}나`,
  String.raw`가\p{L}나`, "가(?=(?i)나)다", "가(?:(?i)나)?다",
];
for (const expression of unsupported) assert.deepEqual(conditions(expression), empty, expression);

// Verify the whitelist has neither case mappings nor iu aliases from outside
// it, including Unicode's non-ASCII equivalents of ASCII letters.
const caseless = /^[\x21-\x40\x5b-\x60\x7b-\x7e\u1100-\u11ff\u3131-\u318e\uac00-\ud7a3]$/u;
const folded = new RegExp(caseless.source, "iu");
for (let code = 0; code <= 0x10ffff; code += 1) {
  const character = String.fromCodePoint(code);
  const included = caseless.test(character);
  assert.equal(folded.test(character), included, `iu whitelist U+${code.toString(16)}`);
  if (included) {
    assert.equal(character.toLowerCase(), character);
    assert.equal(character.toUpperCase(), character);
  }
}

// Read the actual rule line, rather than duplicating the expression under test.
// It is found by its prefix, not a line number, so unrelated edits keep it.
const targetLines = fs.readFileSync(path.join(root, "data/rules/questions.yaml"), "utf8")
  .split(/\r?\n/)
  .filter((line) => line.trim().startsWith("- re:(?:별도|개별)?[ \\t]*(?:통보|통지|안내|연락)"));
assert.equal(targetLines.length, 1, "the whitespace-backtracking notice pattern");
const targetLine = targetLines[0];
assert.match(targetLine, /^\s*- re:/);
const target = targetLine.trim().slice(2);
const targetConditions = conditions(target.slice(3));
assert.deepEqual(targetConditions, {
  required: [],
  requiredAnyOf: [["통보", "통지", "안내", "연락"], ["없이", "하지", "없"], ["불합격", "미합격", "탈락"]],
});
assert.equal(satisfies("모집공고\n" + " ".repeat(99990), targetConditions), false);
for (const text of ["통지 불합격", "없이 탈락", "안내 없이"]) assert.equal(satisfies(text, targetConditions), false);
for (const text of ["통지 없이 불합격", "안내 하지 않으면 미합격", "연락 없습니다 탈락"]) {
  assert.equal(satisfies(text, targetConditions), true);
  assert.ok(probe.pythonRegex(target.slice(3), "iu").test(text));
}

const plan = probe.patternPlan(target);
assert.strictEqual(plan, probe.patternPlan(target), "conditions share the pattern cache");
assert.deepEqual(plain({ required: plan.required, requiredAnyOf: plan.requiredAnyOf }), targetConditions);
const literal = probe.patternPlan("가 나");
assert.deepEqual(plain({ required: literal.required, requiredAnyOf: literal.requiredAnyOf }), empty);

// Count executions, not time: both rejected paths must avoid the costly scan.
let executions = 0;
const originalExec = plan.regex.exec;
plan.regex.exec = function (text) {
  executions += 1;
  return originalExec.call(this, text);
};
try {
  for (const text of ["모집공고\n" + " ".repeat(99990), "모집공고\u200b\n" + " ".repeat(30000)]) {
    plan.regex.lastIndex = 7;
    assert.deepEqual(plain(probe.findMatches(text, [target])), []);
    assert.equal(plan.regex.lastIndex, 0);
    assert.equal(executions, 0, "rejected exact/normalized view must not execute regex");
  }
  for (const text of ["통지 없이 불합격", "통\u200b지 없이 불합격", "통지 없이 불합격"]) {
    plan.regex.lastIndex = 7;
    assert.equal(probe.findMatches(text, [target]).length, 1);
    assert.equal(plan.regex.lastIndex, 0);
  }
  assert.ok(executions > 0);
} finally {
  plan.regex.exec = originalExec;
}
for (const [pattern, text, expected] of [
  ["re:가나", "가\u200b나", [{ start: 0, end: 3, text: "가\u200b나" }]],
  ["re:가12나", "가１２나", [{ start: 0, end: 4, text: "가１２나" }]],
  ["re:채용1", "채용①", [{ start: 0, end: 3, text: "채용①" }]],
  ["re:ㄱ", "ㄱ", []], // raw satisfies, but the searched NFKC view does not
  ["re:ᄀ", "ㄱ", [{ start: 0, end: 1, text: "ㄱ" }]],
  // Unknown-to-the-filter syntax must still reach the existing regex engine.
  ["re:(?<word>가)나", "가나", [{ start: 0, end: 2, text: "가나" }]],
  [String.raw`re:가\u{B098}`, "가나", [{ start: 0, end: 2, text: "가나" }]],
]) {
  assert.deepEqual(plain(probe.findMatches(text, [pattern])), expected, pattern);
}

function* strings(value) {
  if (typeof value === "string") yield value;
  else if (value && typeof value === "object") {
    for (const child of Object.values(value)) yield* strings(child);
  }
}

// Independent, deliberately approximate inversion for the standalone Node
// check. Only actual regex matches count as evidence. The Python regression
// also supplies samples from test_web_rule_sweep's re parse-tree generator.
function derivedSamples(expression) {
  const results = [];
  for (let variant = 0; variant < 3; variant += 1) {
    let sample = expression
      .replace(/\[(?:\\.|[^\]\\])*\]/g, (part) => part.startsWith("[^") ? "가" : /\\d|0-9/.test(part) ? "1" : / |\\[st]/.test(part) ? " " : part[1])
      .replace(/\\[sS]/g, " ").replace(/\\[dD]/g, "1")
      .replace(/\\[wW]/g, "a").replace(/\\[bBAZ]/g, "")
      .replace(/\\[nrt]/g, " ").replace(/\\(.)/g, "$1")
      .replace(/\{\d*(?:,\d*)?\}/g, "");
    let previous;
    do {
      previous = sample;
      sample = sample.replace(/\((\?:|\?P<[^>]+>|\?<?[=!])?([^()]*)\)/g, (_whole, prefix, body) => {
        if (prefix && /[=!]/.test(prefix)) return "";
        const options = body.split("|");
        return options[variant % options.length];
      });
    } while (sample !== previous);
    results.push(sample.replace(/[?*+^$]/g, "").replace(/\./g, "가"));
  }
  return results;
}

// A finite grammar cross-product exercises optional single atoms, nested
// branches, assertions, escapes and class boundaries independently of rules.
const grammarTexts = new Set(["", "1", "a", "A", "ſ", "K", "]", "(", ")"]);
let frontier = [""];
for (let length = 1; length <= 4; length += 1) {
  frontier = frontier.flatMap((prefix) => ["가", "나", "다", "라", "1"].map((letter) => prefix + letter));
  for (const text of frontier) grammarTexts.add(text);
}
const grammarAtoms = [
  "가", "가나", "가나?", "(?:가|나)", "(?:가|)", "(?:가(?:나|다)|라)",
  "[가나]", "[]()^]", String.raw`[\]()]`, String.raw`\d`, String.raw`\x31`,
  "(?=가)", "(?!나)", "(?<=가)", "(?<!나)", "a", "s", "k",
];
let grammarComparisons = 0;
let grammarMatches = 0;
for (const atom of grammarAtoms) {
  for (const quantifier of ["", "?", "*", "+", "{0,2}", "{1,2}?", "{2}"]) {
    for (const expression of [`라(?:${atom})${quantifier}다`, `(?:${atom})${quantifier}|라나`]) {
      const condition = conditions(expression);
      const regex = probe.pythonRegex(expression, "iu");
      for (const text of grammarTexts) {
        grammarComparisons += 1;
        const match = regex.exec(text);
        if (!match) continue;
        grammarMatches += 1;
        assert.ok(satisfies(match[0], condition), JSON.stringify({ expression, match: match[0], condition }));
      }
    }
  }
}
assert.ok(grammarMatches > 0);

const request = process.argv[2] ? JSON.parse(fs.readFileSync(process.argv[2], "utf8")) : {};
const dataStrings = [...strings(context.window.FAIRPOST_DATA)];
const patterns = [...new Set([...dataStrings.filter((text) => text.startsWith("re:")), ...(request.patterns || [])])];
assert.ok(patterns.includes(target));
const texts = new Set(["", " ".repeat(64), "통지 없이 불합격", "안내 하지 않으면 미합격", "연락 없습니다 탈락", ...(request.texts || [])]);
for (const text of dataStrings) {
  if (!text.startsWith("re:") && text.length <= 160) texts.add(text);
}
for (const pattern of patterns) {
  for (const sample of derivedSamples(pattern.slice(3))) texts.add(sample);
}
for (const text of [...texts]) {
  texts.add(probe.normalizedTextWithOffsets(text).normalized);
}

let comparisons = 0;
let matchingPairs = 0;
let filteredPatterns = 0;
const matchedPatterns = new Set();
for (const pattern of patterns) {
  const expression = pattern.slice(3);
  const condition = conditions(expression);
  if (condition.required.length || condition.requiredAnyOf.length) filteredPatterns += 1;
  const regex = probe.pythonRegex(expression, "giu");
  for (const text of texts) {
    comparisons += 1;
    regex.lastIndex = 0;
    const match = regex.exec(text);
    if (match === null) continue;
    matchingPairs += 1;
    matchedPatterns.add(pattern);
    assert.ok(satisfies(match[0], condition), JSON.stringify({ pattern, match: match[0], condition }));
    assert.ok(satisfies(text, condition), JSON.stringify({ pattern, text, condition }));
  }
}
assert.ok(matchingPairs > 0 && matchedPatterns.has(target));
assert.ok(filteredPatterns > 0);
process.stdout.write(JSON.stringify({
  syntaxCases: syntaxCases.length, unsupportedCases: unsupported.length,
  unicodeCodePoints: 0x110000, patterns: patterns.length, texts: texts.size,
  comparisons, matchingPairs, matchedPatterns: matchedPatterns.size,
  filteredPatterns, grammarComparisons, grammarMatches, targetConditions, rejectedExecutions: 0,
}));
