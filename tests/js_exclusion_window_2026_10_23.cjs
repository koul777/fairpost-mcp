// Test-only IIFE probe, as in js_empty_match_2026_10_20.cjs. No production exports are added.
// node js_exclusion_window_2026_10_23.cjs <request.json>
// request.cases: {text, start, end, excludes} (start and end in code points, as
//   Python counts them); prints isExcluded of web/engine.js for each.
// request.admits: [text, left, right, required, requiredAnyOf] (BMP text, so
//   code units and code points agree); prints windowAdmits for each.
// request.admitsLoop: {length, calls, window, literal}: calls windowAdmits that
//   many times on a posting of `length` characters without the literal, inside
//   the vm with a 2 second limit (a search that reads past the window to the
//   end of the posting ends in a timeout error).
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
  "\n global.__exclusionProbe = { isExcluded, windowAdmits };\n" +
  source.slice(closing), context, { filename: "web/engine.js" });
const probe = context.window.__exclusionProbe;

const units = (text, codePoints) => Array.from(text).slice(0, codePoints).join("").length;
const request = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const results = [];
let slowest = 0;
for (const item of request.cases || []) {
  const start = units(item.text, item.start);
  const end = units(item.text, item.end);
  const match = { start, end, text: item.text.slice(start, end) };
  const started = process.hrtime.bigint();
  results.push(probe.isExcluded(item.text, match, item.excludes));
  slowest = Math.max(slowest, Number(process.hrtime.bigint() - started) / 1e9);
}
const admits = (request.admits || []).map(([text, left, right, required, anyOf]) =>
  probe.windowAdmits(text, left, right, required, anyOf));
let loopSeconds = null;
if (request.admitsLoop) {
  context.window.__loop = request.admitsLoop;
  const started = process.hrtime.bigint();
  vm.runInContext(`(() => {
    const { length, calls, window: size, literal } = window.__loop;
    const text = "가".repeat(length);
    let found = 0;
    for (let index = 0; index < calls; index += 1) {
      const left = index % 100;
      if (window.__exclusionProbe.windowAdmits(text, left, left + size, [literal], [])) found += 1;
    }
    if (found !== 0) throw new Error("literal found");
  })()`, context, { timeout: 2000 });
  loopSeconds = Number(process.hrtime.bigint() - started) / 1e9;
}
console.log(JSON.stringify({ results, admits, loopSeconds, slowest }));
