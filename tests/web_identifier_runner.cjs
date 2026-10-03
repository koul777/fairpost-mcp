// Runs the browser copy of the direct-identifier checks (web/app.js reading
// patterns from web/data.js) over the shared fixture and prints the results
// so tests/test_direct_identifiers.py can compare them with core Python.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..");
const fixturePath = path.resolve(
  process.argv[2] || path.join(root, "tests", "fixtures", "direct_identifier_cases.json")
);

class FakeElement {
  constructor(id = "") {
    this.id = id;
    this.value = "";
    this.textContent = "";
    this.innerHTML = "";
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.dataset = {};
    this.classList = { add() {}, remove() {}, toggle() {} };
  }

  addEventListener() {}
  setAttribute() {}
  removeAttribute() {}
  replaceChildren() {}
  focus() {}
}

const elements = new Map();
globalThis.window = globalThis;
globalThis.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  },
  createElement() {
    return new FakeElement();
  },
  body: { appendChild() {} },
};
window.setTimeout = () => 1;
window.clearTimeout = () => {};
window.location = { protocol: "file:" };

for (const relative of ["web/data.js", "web/engine.js", "web/app.js"]) {
  vm.runInThisContext(fs.readFileSync(path.join(root, relative), "utf8"), {
    filename: relative,
  });
}

const fixture = JSON.parse(fs.readFileSync(fixturePath, "utf8"));
const helper = window.FairpostDirectIdentifiers;
const results = {};
for (const item of fixture.cases) {
  const text = item.parts.join("");
  results[item.id] = { kinds: helper.kinds(text), masked: helper.mask(text) };
}
console.log(JSON.stringify({ available: helper.available, results }));
