// Boots web/app.js in isolated contexts with a minimal fake DOM.
// 1. Runs the browser copy of the direct-identifier checks (patterns read
//    from web/data.js) over the shared fixture so
//    tests/test_direct_identifiers.py can compare them with core Python.
// 2. Reports how the AI toggle starts when there is no server (file://) or
//    the server says assisted review is unavailable.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..");
const fixturePath = path.resolve(
  process.argv[2] || path.join(root, "tests", "fixtures", "direct_identifier_cases.json")
);
const sources = ["web/data.js", "web/engine.js", "web/app.js"].map((relative) => ({
  relative,
  code: fs.readFileSync(path.join(root, relative), "utf8"),
}));

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

function boot({ protocol, fetchImpl }) {
  const elements = new Map();
  const fetchCalls = [];
  const sandbox = {
    console,
    URL,
    TextEncoder,
    setImmediate,
    location: { protocol },
    setTimeout: () => 1,
    clearTimeout: () => {},
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, new FakeElement(id));
        return elements.get(id);
      },
      createElement() {
        return new FakeElement();
      },
      body: { appendChild() {} },
    },
  };
  if (fetchImpl) {
    sandbox.fetch = async (url, options = {}) => {
      fetchCalls.push({ url, method: options.method || "GET" });
      return fetchImpl(url, options);
    };
  }
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  for (const source of sources) {
    vm.runInContext(source.code, sandbox, { filename: source.relative });
  }
  return { sandbox, elements, fetchCalls };
}

function toggleState(context) {
  const get = (id) => context.sandbox.document.getElementById(id);
  return {
    toggleDisabled: get("assisted-review-toggle").disabled,
    toggleChecked: get("assisted-review-toggle").checked,
    badge: get("assisted-review-badge").textContent,
    status: get("assisted-review-status").textContent,
    fetches: context.fetchCalls,
  };
}

(async () => {
  const fileContext = boot({ protocol: "file:", fetchImpl: null });
  const fixture = JSON.parse(fs.readFileSync(fixturePath, "utf8"));
  const helper = fileContext.sandbox.FairpostDirectIdentifiers;
  const results = {};
  for (const item of fixture.cases) {
    const text = item.parts.join("");
    results[item.id] = { kinds: [...helper.kinds(text)], masked: helper.mask(text) };
  }

  const notReady = boot({
    protocol: "https:",
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      async json() {
        return {
          ready: false,
          ai_configured: false,
          available_providers: [],
          reason: "설정 필요: AI API",
        };
      },
    }),
  });
  const noApi = boot({
    protocol: "http:",
    fetchImpl: async () => ({
      ok: false,
      status: 404,
      async json() {
        throw new Error("not json");
      },
    }),
  });
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  console.log(
    JSON.stringify({
      available: helper.available,
      results,
      assistedAvailability: {
        file: toggleState(fileContext),
        notReady: toggleState(notReady),
        noApi: toggleState(noApi),
      },
    })
  );
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
