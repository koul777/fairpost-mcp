const fs = require("fs");
const vm = require("vm");

const root = process.cwd();
const context = { window: {} };
context.globalThis = context.window;
vm.createContext(context);
vm.runInContext(fs.readFileSync(`${root}/web/data.js`, "utf8"), context);
vm.runInContext(fs.readFileSync(`${root}/web/engine.js`, "utf8"), context);
const decode = (encoded) => Buffer.from(encoded, "base64").toString("utf8");
const check = (text) => context.window.FairpostEngine.check(text);
// `node tests/js_runner.cjs <base64>` prints one result object.
// `node tests/js_runner.cjs --batch <file>` reads a JSON array of base64
// inputs from <file> and prints the results in the same order, so a seeded
// sweep loads the bundle once. A file (not stdin) keeps Windows CI reliable,
// as in tools/js_batch_runner.cjs.
const output =
  process.argv[2] === "--batch"
    ? JSON.parse(fs.readFileSync(process.argv[3], "utf8")).map((encoded) =>
        check(decode(encoded))
      )
    : check(decode(process.argv[2]));
process.stdout.write(JSON.stringify(output));
