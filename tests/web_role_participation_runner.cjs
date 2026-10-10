// Cross-check helper for tests/test_role_participation_parity.py.
//
// Loads the real web bundle (data.js, engine.js, app.js) in the same fake-DOM
// style as web_app_review_runner.cjs, seeds the browser role-review store with
// the events of each case, runs a review of the posting and reads what a
// reviewer actually sees in the role-review panel.  Nothing here reimplements
// the participation rule: the rule under test is the one in web/app.js
// (isSystemRoleReviewEvent / roleReviewUserEvents / renderRoleReview).
//
// Input (stdin, JSON): {"posting": "...", "cases": [{"name": "...", "events": [...]}]}
// Output (stdout, JSON): {"constants": {...}, "cases": [{...per case result...}]}
const fs = require("fs");
const nodeCrypto = require("crypto");
const path = require("path");
const vm = require("vm");
const { setTimeout: sleep } = require("timers/promises");

const root = path.resolve(__dirname, "..");

class FakeElement {
  constructor(id = "") {
    this.id = id;
    this.value = "";
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.innerHTML = "";
    this.dataset = {};
    this.handlers = new Map();
    this.classList = { add() {}, remove() {} };
    this.attributes = new Map();
  }

  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  addEventListener(type, handler) { this.handlers.set(type, handler); }
  dispatchEvent(event) {
    const handler = this.handlers.get(event.type);
    return handler ? handler({ type: event.type, target: this }) : undefined;
  }
  trigger(type, event = {}) {
    const handler = this.handlers.get(type);
    return handler ? handler({ type, target: this, ...event }) : undefined;
  }
  focus() {}
  select() {}
  remove() {}
  replaceChildren() { this.innerHTML = ""; }
}

class FakeTextAreaElement extends FakeElement {}

const elements = new Map();
elements.set("posting-input", new FakeTextAreaElement("posting-input"));

globalThis.window = globalThis;
globalThis.HTMLTextAreaElement = FakeTextAreaElement;
globalThis.Event = class Event {
  constructor(type) { this.type = type; }
};
globalThis.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  },
  createElement() { return new FakeTextAreaElement(); },
  body: { appendChild() {} },
  execCommand() { return true; },
};
// App timers (toasts) must not keep the process alive; this runner sleeps with
// timers/promises, which is unaffected by this override.
window.setTimeout = () => 1;
window.clearTimeout = () => {};

const localReviewValues = new Map();
globalThis.localStorage = {
  getItem(key) { return localReviewValues.has(key) ? localReviewValues.get(key) : null; },
  setItem(key, value) { localReviewValues.set(key, String(value)); },
  removeItem(key) { localReviewValues.delete(key); },
};
window.confirm = () => true;
globalThis.location = { protocol: "https:" };
// The assisted-review probe is irrelevant here; keep it off the network.
globalThis.fetch = async () => ({ ok: false, status: 503, async json() { return {}; } });
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: { clipboard: { async writeText() {} } },
});

const STORE_KEY = "fairpost.role-review.v2";
const STORE_SCHEMA = "fairpost-browser-role-review-store-v2";

const appPath = path.join(root, "web/app.js");
const appSource = fs.readFileSync(appPath, "utf8");
const appSources = ["web/data.js", "web/posting-templates.js", "web/engine.js", "web/app.js"]
  .filter((relative) => relative !== "web/posting-templates.js" ||
    fs.existsSync(path.join(root, relative)));
for (const relative of appSources) {
  vm.runInThisContext(
    relative === "web/app.js" ? appSource : fs.readFileSync(path.join(root, relative), "utf8"),
    { filename: relative }
  );
}

// Facts about the web bundle that the Python side must stay aligned with,
// read from the source instead of copied into this helper.
function sourceString(name) {
  const match = appSource.match(new RegExp(`const ${name} =\\s*"([^"]*)";`));
  if (!match) throw new Error(`web/app.js no longer defines ${name}`);
  return match[1];
}

function sourceRoleLabels() {
  const block = appSource.match(/const ROLE_LABELS = Object\.freeze\(\{([\s\S]*?)\}\);/);
  if (!block) throw new Error("web/app.js no longer defines ROLE_LABELS");
  const labels = [...block[1].matchAll(/(\w+):\s*"([^"]+)"/g)].map((m) => [m[1], m[2]]);
  if (labels.length !== 7) throw new Error("ROLE_LABELS no longer lists 7 roles");
  return labels;
}

const roleLabels = sourceRoleLabels();
const labelToRole = new Map(roleLabels.map(([role, label]) => [label, role]));
const constants = {
  system_actor_ref: sourceString("ROLE_REVIEW_SYSTEM_ACTOR"),
  system_note: sourceString("ROLE_REVIEW_SYSTEM_NOTE"),
  role_order: roleLabels.map(([role]) => role),
};

function missingRolesFromText(text) {
  const prefix = "아직 기록이 없는 역할: ";
  if (!text.startsWith(prefix)) return [];
  return text.slice(prefix.length).split(", ").map((label) => {
    if (!labelToRole.has(label)) throw new Error(`unknown role label in panel: ${label}`);
    return labelToRole.get(label);
  });
}

async function readAll(stream) {
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

async function runCase(posting, testCase) {
  const input = elements.get("posting-input");
  const result = window.FairpostEngine.check(posting);
  const guidance =
    typeof window.FAIRPOST_DATA.guidance_catalog_version === "string" &&
    window.FAIRPOST_DATA.guidance_catalog_version
      ? window.FAIRPOST_DATA.guidance_catalog_version
      : "guidance-unknown";
  const stamp = "2026-10-10T00:00:00.000Z";
  localReviewValues.clear();
  localReviewValues.set(STORE_KEY, JSON.stringify({
    schema_version: STORE_SCHEMA,
    packets: [{
      schema_version: "fairpost-browser-role-review-v1",
      packet_id: "browser-packet-parity",
      posting_fingerprint: nodeCrypto.createHash("sha256").update(posting, "utf8").digest("hex"),
      ruleset_version: result.ruleset_version,
      guidance_catalog_version: guidance,
      created_at: stamp,
      updated_at: stamp,
      events: testCase.events,
    }],
  }));

  const notice = elements.get("role-review-notice");
  notice.textContent = "";
  input.value = posting;
  input.dispatchEvent(new Event("input"));
  elements.get("check-button").trigger("click");
  // The panel renders after an async SHA-256; wait for the "loaded" notice.
  let waited = 0;
  while (!notice.textContent.includes("불러왔습니다") && waited < 400) {
    await sleep(5);
    waited += 1;
  }
  const missingText = elements.get("role-review-missing").textContent;
  const missing = missingRolesFromText(missingText);
  return {
    name: testCase.name,
    // False means the packet was not loaded (fresh packet or dropped events),
    // in which case the numbers below say nothing about the seeded events.
    loaded: notice.textContent.includes("불러왔습니다"),
    droppedEvents: notice.textContent.includes("형식 검사"),
    packetCount: JSON.parse(localReviewValues.get(STORE_KEY)).packets.length,
    eventItems: (elements.get("role-review-events").innerHTML.match(/<li/g) || []).length,
    systemItems: (elements.get("role-review-events").innerHTML.match(/role-review-system-event/g) || []).length,
    progress: elements.get("role-review-progress").textContent,
    missingText,
    missingRoles: missing,
    participatingRoles: constants.role_order.filter((role) => !missing.includes(role)),
  };
}

(async () => {
  const request = JSON.parse(await readAll(process.stdin));
  const cases = [];
  for (const testCase of request.cases) {
    cases.push(await runCase(request.posting, testCase));
  }
  process.stdout.write(JSON.stringify({ constants, cases }));
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
