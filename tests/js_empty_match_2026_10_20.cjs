// Test-only IIFE probe, as in js_match_fast_paths.cjs. No production exports are added.
// node js_empty_match_2026_10_20.cjs <cases.json>
// For every (text, pattern list) pair of the file, runs findMatches of web/engine.js
// and prints the matches as [start, end, text] with offsets in code points, as
// Python counts them. Each call has a 2 second limit: an exec loop that never
// advances past an empty match inside a surrogate pair ends in a timeout error.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "web/engine.js"), "utf8");
const closing = source.lastIndexOf("})(");
if (closing < 0) throw new Error("engine IIFE closing call");
const context = { window: {} };
context.globalThis = context.window;
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(root, "web/data.js"), "utf8"), context);
vm.runInContext(source.slice(0, closing) +
  "\n global.__emptyProbe = { findMatches };\n" +
  source.slice(closing), context, { filename: "web/engine.js" });
const probe = context.window.__emptyProbe;

const cases = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const codePoints = (text, units) => Array.from(text.slice(0, units)).length;
const results = [];
let slowest = 0;
for (const text of cases.texts) {
  for (const patterns of cases.patternLists) {
    context.window.__text = text;
    context.window.__patterns = patterns;
    const started = process.hrtime.bigint();
    // The loop under test runs inside the vm, so its timeout can stop it.
    const found = vm.runInContext("window.__emptyProbe.findMatches(window.__text, window.__patterns)", context, { timeout: 2000 });
    slowest = Math.max(slowest, Number(process.hrtime.bigint() - started) / 1e9);
    results.push(found.map((match) => [codePoints(text, match.start), codePoints(text, match.end), match.text]));
  }
}
console.log(JSON.stringify({ results, slowest }));
