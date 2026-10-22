// Test-only IIFE probe, as in js_empty_match_2026_10_20.cjs. No production exports are added.
// node js_exclusion_window_2026_10_23.cjs <cases.json>
// For every case {text, start, end, excludes} (start and end in code points, as
// Python counts them) prints isExcluded of web/engine.js, and the slowest call.
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
  "\n global.__exclusionProbe = { isExcluded };\n" +
  source.slice(closing), context, { filename: "web/engine.js" });
const probe = context.window.__exclusionProbe;

const units = (text, codePoints) => Array.from(text).slice(0, codePoints).join("").length;
const cases = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const results = [];
let slowest = 0;
for (const item of cases) {
  const start = units(item.text, item.start);
  const end = units(item.text, item.end);
  const match = { start, end, text: item.text.slice(start, end) };
  const started = process.hrtime.bigint();
  results.push(probe.isExcluded(item.text, match, item.excludes));
  slowest = Math.max(slowest, Number(process.hrtime.bigint() - started) / 1e9);
}
console.log(JSON.stringify({ results, slowest }));
