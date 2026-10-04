const fs = require("fs");
const path = require("path");
const vm = require("vm");

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
    this.replaced = false;
    this.focused = false;
    this.attributes = new Map();
  }

  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }

  addEventListener(type, handler) {
    this.handlers.set(type, handler);
  }

  dispatchEvent(event) {
    const handler = this.handlers.get(event.type);
    return handler ? handler({ type: event.type, target: this }) : undefined;
  }

  trigger(type, event = {}) {
    const handler = this.handlers.get(type);
    return handler ? handler({ type, target: this, ...event }) : undefined;
  }

  focus() { this.focused = true; }
  select() {}
  remove() {}

  replaceChildren() {
    this.innerHTML = "";
    this.replaced = true;
  }
}

class FakeTextAreaElement extends FakeElement {}

const ids = [
  "posting-input",
  "check-button",
  "clear-button",
  "sample-button",
  "copy-button",
  "char-count",
  "empty-state",
  "result-content",
  "results-title",
  "toast",
  "ruleset-version",
  "answer-progress",
  "findings-list",
  "slots-list",
  "questions-list",
  "role-review-panel",
  "role-review-status",
  "role-review-notice",
  "role-review-role",
  "role-review-stage",
  "role-review-action",
  "role-review-resolve-label",
  "role-review-resolve-event",
  "role-review-note",
  "role-review-evidence",
  "role-review-record",
  "role-review-clear",
  "role-review-progress",
  "role-review-missing",
  "role-review-events",
  "finding-count",
  "missing-count",
  "question-count",
  "disclaimer",
];
const elements = new Map(ids.map((id) => [id, new FakeElement(id)]));
elements.set("posting-input", new FakeTextAreaElement("posting-input"));
let execCopyResult = true;

globalThis.window = globalThis;
globalThis.HTMLTextAreaElement = FakeTextAreaElement;
globalThis.Event = class Event {
  constructor(type) {
    this.type = type;
  }
};
globalThis.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  },
  createElement() {
    return new FakeTextAreaElement();
  },
  body: { appendChild() {} },
  execCommand() {
    return execCopyResult;
  },
};
window.setTimeout = () => 1;
window.clearTimeout = () => {};

const localReviewValues = new Map();
const workingStorage = {
  getItem(key) { return localReviewValues.has(key) ? localReviewValues.get(key) : null; },
  setItem(key, value) { localReviewValues.set(key, String(value)); },
  removeItem(key) { localReviewValues.delete(key); },
};
const lockedStorage = {
  getItem() { throw new Error("SecurityError: storage disabled"); },
  setItem() { throw new Error("SecurityError: storage disabled"); },
  removeItem() { throw new Error("SecurityError: storage disabled"); },
};
globalThis.localStorage = workingStorage;
let confirmAnswer = true;
const confirmPrompts = [];
window.confirm = (message) => {
  confirmPrompts.push(message);
  return confirmAnswer;
};
const STORE_KEY = "fairpost.role-review.v2";
const LEGACY_KEY = "fairpost.role-review.v1";
const readStore = () => JSON.parse(localReviewValues.get(STORE_KEY) || "null");
const tick = () => new Promise((resolve) => setImmediate(resolve));
Object.defineProperty(globalThis, "crypto", {
  configurable: true,
  value: {
    subtle: {
      async digest(_algorithm, bytes) {
        let hash = 2166136261;
        for (const byte of new Uint8Array(bytes)) {
          hash = Math.imul(hash ^ byte, 16777619);
        }
        const output = new Uint8Array(32);
        for (let index = 0; index < output.length; index += 1) {
          hash = Math.imul(hash ^ index, 16777619);
          output[index] = (hash >>> ((index % 4) * 8)) & 255;
        }
        return output.buffer;
      },
    },
  },
});

let copied = "";
let clipboardShouldFail = false;
const assistedFetchCalls = [];
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: {
    clipboard: {
      async writeText(value) {
        if (clipboardShouldFail) {
          throw new Error("simulated Clipboard API failure");
        }
        copied = value;
      },
    },
  },
});
let assistedPostMode = "ok";
globalThis.location = { protocol: "https:" };
globalThis.fetch = async (url, options = {}) => {
  assistedFetchCalls.push({ url, options });
  if ((options.method || "GET") === "POST" && assistedPostMode === "unavailable") {
    return {
      ok: false,
      status: 503,
      async json() {
        return {
          error: "Assisted review is not configured",
          ready: false,
          reason: "설정 필요: AI API",
        };
      },
    };
  }
  if ((options.method || "GET") === "GET") {
    return {
      ok: true,
      async json() {
        return {
          ready: true,
          privacy: "선별된 근거만 외부 서비스로 전달합니다.",
          available_providers: [{ id: "anthropic", label: "Claude", model: "claude-model" }],
          default_provider: "anthropic",
        };
      },
    };
  }
  return {
    ok: true,
    async json() {
      return {
        schema_version: "fairpost-assisted-review-v1",
        status: "completed",
        ai_provider: "anthropic",
        summary: "현행 조문을 바탕으로 사람의 적용 범위 확인이 필요합니다.",
        notice: "AI 보강 메모는 법률 자문이 아닙니다.",
        current_articles_retrieved: 1,
      };
    },
  };
};

for (const relative of ["web/data.js", "web/engine.js", "web/app.js"]) {
  vm.runInThisContext(
    fs.readFileSync(path.join(root, relative), "utf8"),
    { filename: relative }
  );
}

(async () => {
  const posting = elements.get("posting-input");
  posting.value = "여성만 지원 가능";
  elements.get("check-button").trigger("click");
  const resultsTitleFocused = elements.get("results-title").focused;

  const result = window.FairpostEngine.check(posting.value);
  const questionId = result.questions[0].id;
  const answer = new FakeTextAreaElement(`answer-${questionId}`);
  answer.dataset.questionAnswer = questionId;
  answer.value = "원문을 직무 요건 중심으로 수정합니다.\n담당자 재확인 완료";
  elements.get("result-content").trigger("input", { target: answer });
  const progressAfterAnswer = elements.get("answer-progress").textContent;

  await elements.get("copy-button").trigger("click");
  const copiedWithAnswer = copied;

  posting.value = "";
  posting.dispatchEvent(new Event("input"));
  const manuallyCleared = {
    progress: elements.get("answer-progress").textContent,
    resultHidden: elements.get("result-content").hidden,
    copyDisabled: elements.get("copy-button").disabled,
  };

  posting.value = "여성만 지원 가능";
  posting.dispatchEvent(new Event("input"));
  elements.get("check-button").trigger("click");
  const progressAfterRerun = elements.get("answer-progress").textContent;
  await elements.get("copy-button").trigger("click");
  const copiedAfterRerun = copied;

  clipboardShouldFail = true;
  execCopyResult = false;
  await elements.get("copy-button").trigger("click");
  const copyFailureToast = elements.get("toast").textContent;

  elements.get("clear-button").trigger("click");
  const cleared = {
    input: posting.value,
    progress: elements.get("answer-progress").textContent,
    resultHidden: elements.get("result-content").hidden,
    copyDisabled: elements.get("copy-button").disabled,
    dynamicContainersCleared: ["findings-list", "slots-list", "questions-list"]
      .every((id) => elements.get(id).replaced),
  };

  clipboardShouldFail = false;
  execCopyResult = true;
  posting.value = "여성만 지원 가능";
  posting.dispatchEvent(new Event("input"));
  elements.get("organization-sector").value = "public";
  elements.get("organization-sector").trigger("change");
  elements.get("organization-public-type").value = "public_corporation";
  elements.get("organization-public-type").trigger("change");
  elements.get("organization-size").value = "300_plus";
  elements.get("organization-size").trigger("change");
  const posts = () =>
    assistedFetchCalls.filter((call) => call.options.method === "POST");
  const assistedToggle = elements.get("assisted-review-toggle");
  const runButton = elements.get("assisted-review-run");
  const live = () => elements.get("assisted-review-live").textContent;
  const availability = {
    gets: assistedFetchCalls.filter((call) => (call.options.method || "GET") === "GET")
      .map((call) => ({ url: call.url, body: call.options.body || null })),
    toggleDisabled: assistedToggle.disabled,
    badge: elements.get("assisted-review-badge").textContent,
  };
  assistedToggle.checked = true;
  assistedToggle.trigger("change");
  await tick();
  const afterToggle = {
    posts: posts().length,
    badge: elements.get("assisted-review-badge").textContent,
    consentHidden: elements.get("assisted-review-consent").hidden,
    runDisabled: runButton.disabled,
    hint: elements.get("assisted-review-run-hint").textContent,
    live: live(),
    privacy: elements.get("privacy-message").textContent,
  };
  elements.get("check-button").trigger("click");
  await tick();
  const afterCheck = {
    posts: posts().length,
    runDisabled: runButton.disabled,
    hint: elements.get("assisted-review-run-hint").textContent,
    panelHidden: elements.get("assisted-review-panel").hidden,
  };
  runButton.trigger("click");
  const whileRunning = {
    live: live(),
    runDisabled: runButton.disabled,
    resultsNote: elements.get("results-note").textContent,
  };
  await tick();
  await tick();
  const assistedPost = posts()[0];
  const assisted = {
    badge: elements.get("assisted-review-badge").textContent,
    resultStatus: elements.get("assisted-review-result-status").textContent,
    output: elements.get("assisted-review-output").textContent,
    panelHidden: elements.get("assisted-review-panel").hidden,
    postBody: assistedPost ? JSON.parse(assistedPost.options.body) : null,
    organizationMarkup: elements.get("questions-list").innerHTML,
  };
  const afterResult = {
    posts: posts().length,
    live: live(),
    resultsNote: elements.get("results-note").textContent,
  };
  await elements.get("copy-button").trigger("click");
  const memoWithCurrentAi = copied;

  // Changing the provider or organization conditions never re-sends.
  const providerSelect = elements.get("assisted-review-provider");
  providerSelect.value = "openai";
  providerSelect.trigger("change");
  const afterProviderChange = {
    posts: posts().length,
    resultStatus: elements.get("assisted-review-result-status").textContent,
    notice: elements.get("assisted-review-notice").textContent,
    live: live(),
    status: elements.get("assisted-review-status").textContent,
  };
  providerSelect.value = "anthropic";
  providerSelect.trigger("change");
  const afterProviderRevert = elements.get("assisted-review-result-status").textContent;
  elements.get("organization-size").value = "30_to_299";
  elements.get("organization-size").trigger("change");
  await elements.get("copy-button").trigger("click");
  const afterOrganizationChange = {
    posts: posts().length,
    resultStatus: elements.get("assisted-review-result-status").textContent,
    memo: copied,
  };
  elements.get("organization-size").value = "300_plus";
  elements.get("organization-size").trigger("change");

  // Turning the toggle off and on again sends nothing either.
  assistedToggle.checked = false;
  assistedToggle.trigger("change");
  const afterOff = {
    panelHidden: elements.get("assisted-review-panel").hidden,
    consentHidden: elements.get("assisted-review-consent").hidden,
    live: live(),
  };
  assistedToggle.checked = true;
  assistedToggle.trigger("change");

  // Easy mode hides every assist control, so switching to it turns assist off.
  elements.get("mode-expert").trigger("click");
  const assistOnInExpert = assistedToggle.checked;
  const postsBeforeEasy = posts().length;
  elements.get("mode-easy").trigger("click");
  elements.get("check-button").trigger("click");
  await tick();
  const easyModeAssist = {
    assistOnInExpert,
    toggleChecked: assistedToggle.checked,
    badge: elements.get("assisted-review-badge").textContent,
    privacy: elements.get("privacy-message").textContent,
    runDisabled: runButton.disabled,
    postsAfterEasyCheck: posts().length - postsBeforeEasy,
  };
  elements.get("mode-expert").trigger("click");
  assistedToggle.checked = true;
  assistedToggle.trigger("change");
  const postsBeforeUnavailable = posts().length;

  // The server reporting "not configured" disables the toggle with a reason.
  assistedPostMode = "unavailable";
  runButton.trigger("click");
  await tick();
  await tick();
  const autoDisabled = {
    posts: posts().length - postsBeforeUnavailable,
    toggleChecked: assistedToggle.checked,
    toggleDisabled: assistedToggle.disabled,
    badge: elements.get("assisted-review-badge").textContent,
    status: elements.get("assisted-review-status").textContent,
    live: live(),
    runDisabled: runButton.disabled,
  };
  assistedPostMode = "ok";

  // Empty input shows an inline error next to the field.
  posting.value = "  ";
  elements.get("check-button").trigger("click");
  const emptyInput = {
    error: elements.get("posting-input-error").textContent,
    errorHidden: elements.get("posting-input-error").hidden,
    invalid: posting.getAttribute("aria-invalid"),
  };
  posting.value = "여성만 지원 가능";
  posting.dispatchEvent(new Event("input"));
  emptyInput.clearedOnInput = posting.getAttribute("aria-invalid") === null;
  elements.get("check-button").trigger("click");
  const assistedFlow = {
    availability,
    afterToggle,
    afterCheck,
    whileRunning,
    afterResult,
    memoWithCurrentAi,
    afterProviderChange,
    afterProviderRevert,
    afterOrganizationChange,
    afterOff,
    autoDisabled,
    emptyInput,
  };

  await tick();
  const roleFingerprint = readStore().packets[0].posting_fingerprint;
  const currentPacket = () =>
    readStore().packets.find(
      (packet) => packet.posting_fingerprint === roleFingerprint
    );
  const initialRoleProgress = elements.get("role-review-progress").textContent;
  elements.get("role-review-role").value = "auditor";
  elements.get("role-review-stage").value = "evaluation";
  elements.get("role-review-action").value = "note";
  elements.get("role-review-note").value = "릴리스 전 재현성 근거와 사람 검토 이력을 확인합니다.";
  elements.get("role-review-evidence").value = "Q-DIST-002, ncs-process";
  elements.get("role-review-record").trigger("click");
  elements.get("role-review-note").value = "reviewer@example.com";
  elements.get("role-review-record").trigger("click");
  const roleReviewAfterSensitiveNote = {
    progress: elements.get("role-review-progress").textContent,
    eventCount: currentPacket().events.length,
    noteError: elements.get("role-review-note-error").textContent,
    noteErrorHidden: elements.get("role-review-note-error").hidden,
    noteInvalid: elements.get("role-review-note").getAttribute("aria-invalid"),
  };
  elements.get("role-review-note").trigger("input");
  const noteErrorClearedOnInput = {
    hidden: elements.get("role-review-note-error").hidden,
    invalid: elements.get("role-review-note").getAttribute("aria-invalid"),
  };
  elements.get("role-review-note").value = "근거 ID 형식 확인";
  elements.get("role-review-evidence").value = "근거 1";
  elements.get("role-review-record").trigger("click");
  const evidenceError = {
    message: elements.get("role-review-evidence-error").textContent,
    invalid: elements.get("role-review-evidence").getAttribute("aria-invalid"),
  };
  elements.get("role-review-evidence").value = "";
  elements.get("role-review-action").value = "edit_requested";
  elements.get("role-review-note").value = "직무 요건 근거를 보강해야 합니다.";
  elements.get("role-review-record").trigger("click");
  const issueEventId = currentPacket().events.find(
    (event) => event.action === "edit_requested"
  ).event_id;
  const resolveOptions = elements.get("role-review-resolve-event").innerHTML;
  elements.get("role-review-role").value = "hr_owner";
  elements.get("role-review-action").value = "resolve";
  elements.get("role-review-action").trigger("change");
  elements.get("role-review-resolve-event").value = "";
  elements.get("role-review-note").value = "직무 요건 근거를 보강했습니다.";
  elements.get("role-review-record").trigger("click");
  const resolveError = elements.get("role-review-resolve-error").textContent;
  elements.get("role-review-resolve-event").value = issueEventId;
  elements.get("role-review-note").value = "직무 요건 근거를 보강했습니다.";
  elements.get("role-review-record").trigger("click");
  await elements.get("copy-button").trigger("click");
  const roleReviewBeforeVersionDrift = {
    status: elements.get("role-review-status").textContent,
    progress: elements.get("role-review-progress").textContent,
    missingRoles: elements.get("role-review-missing").textContent,
    eventsMarkup: elements.get("role-review-events").innerHTML,
    storage: localReviewValues.get(STORE_KEY) || "",
    copiedMemo: copied,
  };

  // Same posting, re-checked: the stored packet is loaded, not replaced.
  elements.get("check-button").trigger("click");
  await tick();
  const reloaded = {
    progress: elements.get("role-review-progress").textContent,
    notice: elements.get("role-review-notice").textContent,
    packetCount: readStore().packets.length,
  };

  // A ruleset change keeps the old packet and starts a new one.
  const driftStore = readStore();
  const priorPacketId = driftStore.packets[0].packet_id;
  const currentRuleset = driftStore.packets[0].ruleset_version;
  driftStore.packets[0].ruleset_version = "stale-ruleset";
  localReviewValues.set(STORE_KEY, JSON.stringify(driftStore));
  elements.get("check-button").trigger("click");
  await tick();
  const afterDrift = readStore();
  const roleReviewAfterVersionDrift = {
    newPacket: afterDrift.packets.some(
      (packet) => packet.packet_id !== priorPacketId
    ),
    oldPacketKept: afterDrift.packets.some(
      (packet) => packet.packet_id === priorPacketId && packet.events.length === 4
    ),
    packetCount: afterDrift.packets.length,
    notice: elements.get("role-review-notice").textContent,
    progress: elements.get("role-review-progress").textContent,
  };

  // A different posting (one character changed) gets its own packet.
  posting.value = "여성만 지원 가능.";
  posting.dispatchEvent(new Event("input"));
  elements.get("check-button").trigger("click");
  await tick();
  const otherPosting = {
    packetCount: readStore().packets.length,
    notice: elements.get("role-review-notice").textContent,
  };

  // One invalid stored event is dropped with a warning; valid ones stay.
  posting.value = "여성만 지원 가능";
  posting.dispatchEvent(new Event("input"));
  const invalidStore = readStore();
  invalidStore.packets = invalidStore.packets.filter(
    (packet) =>
      packet.posting_fingerprint !== roleFingerprint ||
      packet.packet_id === priorPacketId
  );
  const original = invalidStore.packets.find(
    (packet) => packet.packet_id === priorPacketId
  );
  original.ruleset_version = currentRuleset;
  original.events.push({
    event_id: "broken event id",
    stage: "analysis",
    role: "auditor",
    action: "note",
  });
  localReviewValues.set(STORE_KEY, JSON.stringify(invalidStore));
  elements.get("check-button").trigger("click");
  await tick();
  const invalidEvent = {
    notice: elements.get("role-review-notice").textContent,
    progress: elements.get("role-review-progress").textContent,
  };

  // v1 single-key data migrates into the v2 store without loss.
  const migrateStore = readStore();
  const legacyPacket = migrateStore.packets.find(
    (packet) => packet.packet_id === priorPacketId
  );
  delete legacyPacket.created_at;
  delete legacyPacket.updated_at;
  legacyPacket.events = legacyPacket.events
    .filter((event) => event.event_id !== "broken event id")
    .map((event) => {
      const copy = { ...event };
      delete copy.actor_ref;
      return copy;
    });
  migrateStore.packets = migrateStore.packets.filter(
    (packet) => packet.packet_id !== priorPacketId
  );
  localReviewValues.set(STORE_KEY, JSON.stringify(migrateStore));
  localReviewValues.set(LEGACY_KEY, JSON.stringify(legacyPacket));
  elements.get("check-button").trigger("click");
  await tick();
  const migrated = readStore().packets.find(
    (packet) => packet.packet_id === priorPacketId
  );
  const migration = {
    legacyRemoved: !localReviewValues.has(LEGACY_KEY),
    migratedEvents: migrated ? migrated.events.length : 0,
    systemMarked: migrated ? migrated.events[0].actor_ref : null,
    progress: elements.get("role-review-progress").textContent,
  };

  // Deleting asks first and removes only this posting's packet.
  confirmAnswer = false;
  elements.get("role-review-clear").trigger("click");
  const cancelledDelete = {
    packetCount: readStore().packets.length,
    notice: elements.get("role-review-notice").textContent,
    panelHidden: elements.get("role-review-panel").hidden,
  };
  confirmAnswer = true;
  elements.get("role-review-clear").trigger("click");
  const confirmedDelete = {
    prompts: confirmPrompts.length,
    packetCount: readStore().packets.length,
    currentRemoved: !readStore().packets.some(
      (packet) => packet.packet_id === priorPacketId
    ),
    panelHidden: elements.get("role-review-panel").hidden,
  };

  // Retention: at most 20 packets, the least recently updated is evicted.
  const fullStore = readStore();
  for (let index = fullStore.packets.length; index < 20; index += 1) {
    const second = String(index).padStart(2, "0");
    fullStore.packets.push({
      schema_version: "fairpost-browser-role-review-v1",
      packet_id: `browser-packet-filler-${index}`,
      posting_fingerprint: index.toString(16).padStart(64, "0"),
      ruleset_version: "filler",
      guidance_catalog_version: "filler",
      created_at: `2020-01-01T00:00:${second}.000Z`,
      updated_at: `2020-01-01T00:00:${second}.000Z`,
      events: [],
    });
  }
  const oldestFiller = fullStore.packets
    .filter((packet) => packet.ruleset_version === "filler")
    .sort((left, right) => left.updated_at.localeCompare(right.updated_at))[0]
    .packet_id;
  localReviewValues.set(STORE_KEY, JSON.stringify(fullStore));
  elements.get("check-button").trigger("click");
  await tick();
  const retention = {
    packetsBefore: fullStore.packets.length,
    packetCount: readStore().packets.length,
    oldestEvicted: !readStore().packets.some(
      (packet) => packet.packet_id === oldestFiller
    ),
    notice: elements.get("role-review-notice").textContent,
  };

  // Corrupt stored data is never overwritten.
  localReviewValues.set(STORE_KEY, "{not json");
  elements.get("check-button").trigger("click");
  await tick();
  elements.get("role-review-role").value = "job_sme";
  elements.get("role-review-action").value = "note";
  elements.get("role-review-action").trigger("change");
  elements.get("role-review-note").value = "세션 기록 확인";
  elements.get("role-review-record").trigger("click");
  const corrupt = {
    untouched: localReviewValues.get(STORE_KEY) === "{not json",
    status: elements.get("role-review-status").textContent,
    notice: elements.get("role-review-notice").textContent,
    progress: elements.get("role-review-progress").textContent,
  };

  // Storage that throws keeps the page working in session-only mode.
  globalThis.localStorage = lockedStorage;
  elements.get("check-button").trigger("click");
  await tick();
  elements.get("role-review-note").value = "저장소 없이 기록";
  elements.get("role-review-record").trigger("click");
  const unavailable = {
    status: elements.get("role-review-status").textContent,
    notice: elements.get("role-review-notice").textContent,
    progress: elements.get("role-review-progress").textContent,
  };
  globalThis.localStorage = workingStorage;

  const roleReview = {
    ...roleReviewBeforeVersionDrift,
    initialProgress: initialRoleProgress,
    afterSensitiveNote: roleReviewAfterSensitiveNote,
    noteErrorClearedOnInput,
    evidenceError,
    resolveError,
    resolveOptions,
    reloaded,
    afterVersionDrift: roleReviewAfterVersionDrift,
    otherPosting,
    invalidEvent,
    migration,
    cancelledDelete,
    confirmedDelete,
    retention,
    corrupt,
    unavailable,
  };

  // Easy-mode "select in posting" uses offsets of the checked text only.
  posting.value = "여직원 모집";
  posting.dispatchEvent(new Event("input"));
  elements.get("check-button").trigger("click");
  await tick();
  const selections = [];
  posting.setSelectionRange = (start, end) => selections.push([start, end]);
  const selectTarget = {
    dataset: { selectStart: "0", selectEnd: "6" },
    closest: (selector) => (selector === "[data-select-start]" ? selectTarget : null),
  };
  elements.get("easy-result").trigger("click", { target: selectTarget });
  posting.value = "📋 여직원 모집";
  posting.dispatchEvent(new Event("input"));
  elements.get("easy-result").trigger("click", { target: selectTarget });
  const easySelection = {
    selections,
    toast: elements.get("toast").textContent,
  };

  // A baseline survives repeated edits, but never clearing or version changes.
  elements.get("clear-button").trigger("click");
  const comparisonPostsBefore = posts().length;
  posting.value = "비교원문-PRIVATE-SESSION\n여성만 지원 가능";
  posting.dispatchEvent(new Event("input"));
  elements.get("check-button").trigger("click");
  await tick();
  const firstComparison = {
    status: elements.get("comparison-status").textContent,
    groups: elements.get("comparison-groups").innerHTML,
    hidden: elements.get("comparison-panel").hidden,
  };
  posting.value = "비교원문-PRIVATE-SESSION\n성별 무관\n만 30세 이하";
  posting.dispatchEvent(new Event("input"));
  const staleComparison = {
    copyDisabled: elements.get("copy-button").disabled,
    resetDisabled: elements.get("comparison-reset").disabled,
    status: elements.get("comparison-status").textContent,
  };
  const previousCopy = copied;
  await elements.get("copy-button").trigger("click");
  staleComparison.copyUnchanged = copied === previousCopy;
  elements.get("check-button").trigger("click");
  const changedComparison = elements.get("comparison-groups").innerHTML;
  await elements.get("copy-button").trigger("click");
  const comparisonReport = copied;
  posting.value += "\n모집인원: 2명";
  posting.dispatchEvent(new Event("input"));
  elements.get("check-button").trigger("click");
  const thirdComparison = elements.get("comparison-groups").innerHTML;
  elements.get("check-button").trigger("click");
  const unchangedComparison = elements.get("comparison-groups").innerHTML;
  elements.get("comparison-reset").trigger("click");
  const resetComparison = elements.get("comparison-groups").innerHTML;
  posting.value += "\n여성만 지원 가능";
  posting.dispatchEvent(new Event("input"));
  elements.get("check-button").trigger("click");
  const rebasedComparison = elements.get("comparison-groups").innerHTML;
  const comparisonVersion = window.FAIRPOST_DATA.version;
  window.FAIRPOST_DATA.version = `${comparisonVersion}-comparison-change`;
  elements.get("check-button").trigger("click");
  const versionComparison = {
    status: elements.get("comparison-status").textContent,
    groups: elements.get("comparison-groups").innerHTML,
  };
  window.FAIRPOST_DATA.version = comparisonVersion;
  await tick();
  const comparisonStored = [...localReviewValues.values()].some((value) =>
    value.includes("비교원문-PRIVATE-SESSION"));
  elements.get("sample-button").trigger("click");
  const sampleComparisonHidden = elements.get("comparison-panel").hidden;
  elements.get("check-button").trigger("click");
  const sampleComparisonGroups = elements.get("comparison-groups").innerHTML;
  posting.value = "";
  posting.dispatchEvent(new Event("input"));
  const clearedComparisonHidden = elements.get("comparison-panel").hidden;
  const comparisonFlow = {
    firstComparison, staleComparison, changedComparison, comparisonReport,
    thirdComparison, unchangedComparison, resetComparison, rebasedComparison,
    versionComparison, comparisonStored, sampleComparisonHidden,
    sampleComparisonGroups, clearedComparisonHidden,
    postsAdded: posts().length - comparisonPostsBefore,
  };

  console.log(JSON.stringify({
    comparisonFlow,
    easySelection,
    questionId,
    questionCount: result.questions.length,
    resultsTitleFocused,
    progressAfterAnswer,
    copiedWithAnswer,
    manuallyCleared,
    progressAfterRerun,
    copiedAfterRerun,
    copyFailureToast,
    cleared,
    assisted,
    assistedFlow,
    easyModeAssist,
    roleReview,
  }));
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
