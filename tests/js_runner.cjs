const fs = require("fs");
const vm = require("vm");

const root = process.cwd();
const context = { window: {} };
context.globalThis = context.window;
vm.createContext(context);
vm.runInContext(fs.readFileSync(`${root}/web/data.js`, "utf8"), context);
vm.runInContext(fs.readFileSync(`${root}/web/engine.js`, "utf8"), context);
const input = Buffer.from(process.argv[2], "base64").toString("utf8");
process.stdout.write(
  JSON.stringify(context.window.FairpostEngine.check(input))
);
