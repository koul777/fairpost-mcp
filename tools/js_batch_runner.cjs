const crypto = require("crypto");
const fs = require("fs");
const vm = require("vm");

const root = process.cwd();
const inputPath = process.argv[2];
if (!inputPath) {
  throw new Error("training records JSONL path is required");
}

const context = { window: {} };
context.globalThis = context.window;
vm.createContext(context);
vm.runInContext(fs.readFileSync(`${root}/web/data.js`, "utf8"), context);
vm.runInContext(fs.readFileSync(`${root}/web/engine.js`, "utf8"), context);

function canonical(value) {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(canonical).join(",")}]`;
  }
  return `{${Object.keys(value)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`)
    .join(",")}}`;
}

const rows = fs
  .readFileSync(inputPath, "utf8")
  .split(/\r?\n/u)
  .filter(Boolean)
  .map((line) => JSON.parse(line))
  .map((record) => {
    const result = context.window.FairpostEngine.check(record.text);
    return {
      id: record.id,
      result_sha256: crypto
        .createHash("sha256")
        .update(canonical(result), "utf8")
        .digest("hex"),
    };
  });

process.stdout.write(
  JSON.stringify({
    ruleset_version: context.window.FAIRPOST_DATA.version,
    records: rows,
  })
);
