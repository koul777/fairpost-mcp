// Reads tables out of web/engine.js for tests/test_web_tables_parity.py.
//
// engine.js keeps its tables in the scope of one IIFE, so they cannot be read
// from outside. This script loads the engine the way tests/js_runner.cjs does,
// after appending a one-line probe to the end of that IIFE
// (`global.__fairpostProbe = (expression) => eval(expression)`), and then
// evaluates the requested bindings in place. Nothing is parsed out of the
// source text except the pure string-array literals reported in `literals`,
// so reformatting a table (quotes, line breaks, trailing commas) changes nothing.
//
// `node tests/js_tables.cjs <request.json>` prints one JSON object:
//   tables:     { NAME: { value } | { error } } for every name in request.names
//   regexes:    { NAME: { actual, actualFlags, expected, expectedFlags } } for
//               request.regexes ({ name, source, flags }): `expected` is the
//               engine's own pythonRegex(source, flags), `actual` the binding
//   literals:   [{ line, values }] for every array literal made only of strings
//   whitespace: code points the engine treats as Python whitespace (str.isspace)
//   regexSpace: code points matched by the engine's translation of Python `\s`
const fs = require("fs");
const vm = require("vm");

const root = process.cwd();
const request = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const source = fs.readFileSync(`${root}/web/engine.js`, "utf8");

const closing = source.lastIndexOf("})(");
if (closing < 0) {
  throw new Error("web/engine.js: the closing call of the engine IIFE was not found");
}
const probed =
  source.slice(0, closing) +
  "\n  global.__fairpostProbe = (expression) => eval(expression);\n" +
  source.slice(closing);
const context = { window: {} };
context.globalThis = context.window;
vm.createContext(context);
vm.runInContext(probed, context, { filename: "web/engine.js" });
const probe = context.window.__fairpostProbe;

const IDENTIFIER = /^[A-Za-z_$][\w$]*$/;
const tagOf = (value) => Object.prototype.toString.call(value);

function serialize(value) {
  const tag = tagOf(value);
  if (tag === "[object Set]") return { $set: Array.from(value).map(serialize).sort() };
  if (tag === "[object RegExp]") return { $regex: value.source, $flags: value.flags };
  if (tag === "[object Array]") return value.map(serialize);
  if (value === null || ["string", "number", "boolean"].includes(typeof value)) return value;
  throw new Error(`unsupported table value (${tag})`);
}

const out = { tables: {}, regexes: {}, literals: [], whitespace: [], regexSpace: [] };

for (const name of request.names || []) {
  try {
    if (!IDENTIFIER.test(name)) throw new Error("not an identifier");
    out.tables[name] = { value: serialize(probe(name)) };
  } catch (error) {
    out.tables[name] = { error: String(error && error.message ? error.message : error) };
  }
}

const pythonRegex = probe("pythonRegex");
for (const entry of request.regexes || []) {
  try {
    if (!IDENTIFIER.test(entry.name)) throw new Error("not an identifier");
    const actual = probe(entry.name);
    const expected = pythonRegex(entry.source, entry.flags);
    out.regexes[entry.name] = {
      actual: actual.source,
      actualFlags: Array.from(actual.flags).sort().join(""),
      expected: expected.source,
      expectedFlags: Array.from(expected.flags).sort().join(""),
    };
  } catch (error) {
    out.regexes[entry.name] = { error: String(error && error.message ? error.message : error) };
  }
}

// Pure string-array literals, wherever they sit in the file (named tables and
// the inline lists inside functions alike).
const STRING = String.raw`"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'`;
const ARRAY = new RegExp(`\\[\\s*(?:(?:${STRING})\\s*,\\s*)*(?:${STRING})\\s*,?\\s*\\]`, "gu");
for (const match of source.matchAll(ARRAY)) {
  const values = vm.runInNewContext(`(${match[0]})`);
  const line = source.slice(0, match.index).split("\n").length;
  out.literals.push({ line, values: Array.from(values) });
}

if (request.whitespace) {
  const isSpace = probe("isPythonWhitespaceCode");
  const spaceRegex = pythonRegex("\\s", "u");
  for (let code = 0; code < 0x110000; code += 1) {
    if (code >= 0xd800 && code <= 0xdfff) continue;
    if (isSpace(code)) out.whitespace.push(code);
    if (spaceRegex.test(String.fromCodePoint(code))) out.regexSpace.push(code);
  }
}

process.stdout.write(JSON.stringify(out));
