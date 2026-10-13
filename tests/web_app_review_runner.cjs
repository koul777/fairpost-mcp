const nodeAssert = require("node:assert/strict");
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
// Timers are recorded and never run on their own; a test fires the one it
// needs (for example the assisted-review deadline).
const scheduledTimeouts = [];
window.setTimeout = (callback, delay) => {
  scheduledTimeouts.push({ callback, delay, cleared: false });
  return scheduledTimeouts.length;
};
window.clearTimeout = (id) => {
  if (scheduledTimeouts[id - 1]) scheduledTimeouts[id - 1].cleared = true;
};

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
  if ((options.method || "GET") === "POST" && assistedPostMode === "hang") {
    // The server never answers; only the request's own deadline ends it.
    return new Promise((_resolve, reject) => {
      options.signal.addEventListener("abort", () => {
        const error = new Error("This operation was aborted");
        error.name = "AbortError";
        reject(error);
      });
    });
  }
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

// The optional example-sentence bundle loads after data.js, as in index.html.
const appSources = ["web/data.js", "web/posting-templates.js", "web/engine.js", "web/app.js"]
  .filter((relative) => relative !== "web/posting-templates.js" ||
    fs.existsSync(path.join(root, relative)));
for (const relative of appSources) {
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
  // The count is split into this posting's questions and the common checklist.
  {
    const answered = result.questions.filter((question) => question.id === questionId);
    const isCommon = (question) => question.review_scope === "common";
    const split = (list) => `${answered.filter((q) => list.includes(q)).length}/${list.length}`;
    nodeAssert.equal(
      progressAfterAnswer,
      `담당자 답변 1/${result.questions.length} (공고별 ${split(result.questions.filter((q) => !isCommon(q)))} · 공통 ${split(result.questions.filter(isCommon))})`
    );
  }

  await elements.get("copy-button").trigger("click");
  const copiedWithAnswer = copied;

  // Emptying the field by hand is an edit: the result turns stale, but the
  // answers, the result and the comparison baseline are not wiped.
  posting.value = "";
  posting.dispatchEvent(new Event("input"));
  const manuallyCleared = {
    progress: elements.get("answer-progress").textContent,
    resultHidden: elements.get("result-content").hidden,
    copyAriaDisabled: elements.get("copy-button").getAttribute("aria-disabled"),
    comparisonHidden: elements.get("comparison-panel").hidden,
    bannerText: elements.get("stale-banner-text").textContent,
  };

  // Typing the same posting back makes the result current again, answers intact.
  posting.value = "여성만 지원 가능";
  posting.dispatchEvent(new Event("input"));
  const staleBannerAfterTypingBack = elements.get("stale-banner-text").textContent;
  elements.get("check-button").trigger("click");
  const progressAfterRerun = elements.get("answer-progress").textContent;
  await elements.get("copy-button").trigger("click");
  const copiedAfterRerun = copied;

  clipboardShouldFail = true;
  execCopyResult = false;
  await elements.get("copy-button").trigger("click");
  const copyFailureToast = elements.get("toast").textContent;

  // "×" is the one reset: it asks first because an answer exists.
  const promptsBeforeClear = confirmPrompts.length;
  elements.get("clear-button").trigger("click");
  const clearPrompt = confirmPrompts.slice(promptsBeforeClear);
  const cleared = {
    input: posting.value,
    progress: elements.get("answer-progress").textContent,
    resultHidden: elements.get("result-content").hidden,
    copyDisabled: elements.get("copy-button").disabled,
    dynamicContainersCleared: ["findings-list", "slots-list", "questions-list"]
      .every((id) => elements.get(id).replaced),
    prompts: clearPrompt.length,
    promptMentionsAnswers: clearPrompt.length === 1 && clearPrompt[0].includes("담당자 답변 1개"),
    undoOffered: elements.get("posting-undo").hidden === false &&
      elements.get("posting-undo").textContent === "방금 지운 공고문 되돌리기",
  };
  // The cleared posting comes back with one click; the answers do not.
  elements.get("posting-undo").trigger("click");
  cleared.undoRestoresPosting = posting.value === "여성만 지원 가능";
  cleared.answersStayCleared = elements.get("answer-progress").textContent.startsWith("담당자 답변 0/0");
  cleared.resultStillHidden = elements.get("result-content").hidden;

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
  // The run button disables itself while the request is in flight; focus must
  // move to the result panel instead of dropping to <body>.
  elements.get("assisted-review-heading").focused = false;
  runButton.trigger("click");
  nodeAssert.equal(elements.get("assisted-review-heading").focused, true);
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

  // A server that never answers: the request carries a 60 s deadline and
  // stops there with a Korean message; the local review stays usable.
  assistedPostMode = "hang";
  runButton.trigger("click");
  const hangingPost = assistedFetchCalls[assistedFetchCalls.length - 1];
  const deadlineTimer = scheduledTimeouts.find((entry) => entry.delay === 60000 && !entry.cleared);
  const timeoutFlow = {
    signalSent: Boolean(hangingPost.options.signal) && typeof hangingPost.options.signal.aborted === "boolean",
    deadlineMs: deadlineTimer ? deadlineTimer.delay : null,
    waitingBadge: elements.get("assisted-review-result-status").textContent,
  };
  deadlineTimer.callback();
  await tick();
  await tick();
  timeoutFlow.aborted = hangingPost.options.signal.aborted;
  timeoutFlow.badge = elements.get("assisted-review-result-status").textContent;
  timeoutFlow.output = elements.get("assisted-review-output").textContent;
  timeoutFlow.notice = elements.get("assisted-review-notice").textContent;
  timeoutFlow.live = live();
  timeoutFlow.runEnabledAgain = !runButton.disabled;
  timeoutFlow.deadlineCleared = scheduledTimeouts.filter((entry) => entry.delay === 60000).every(
    (entry) => entry.cleared || entry === deadlineTimer
  );

  // The server reporting "not configured" disables the toggle; readers are
  // told the operator has to turn it on, not the server's configuration hint.
  const postsBeforeUnavailable = posts().length;
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
    timeoutFlow,
  };

  await tick();
  // Reviews alone (the many checks above) save no role packet: the packet
  // stays in memory until the reader records a first event.
  const unsavedPacket = {
    stored: localReviewValues.has(STORE_KEY),
    status: elements.get("role-review-status").textContent,
    notice: elements.get("role-review-notice").textContent,
    panelHidden: elements.get("role-review-panel").hidden,
  };
  const initialRoleProgress = elements.get("role-review-progress").textContent;
  elements.get("role-review-role").value = "auditor";
  elements.get("role-review-stage").value = "evaluation";
  elements.get("role-review-action").value = "note";
  elements.get("role-review-note").value = "릴리스 전 재현성 근거와 사람 검토 이력을 확인합니다.";
  elements.get("role-review-evidence").value = "Q-DIST-002, ncs-process";
  // Editing or manually clearing the posting must not attach a role draft to
  // the previously checked fingerprint. Trigger the handler even while its
  // aria-disabled action is locked, as a keyboard user can still reach it.
  const checkedRolePosting = posting.value;
  const roleNoteDraft = elements.get("role-review-note").value;
  const roleEvidenceDraft = elements.get("role-review-evidence").value;
  const staleRoleWrites = [];
  for (const changed of [`${checkedRolePosting}\n추가 안내`, ""]) {
    posting.value = changed;
    posting.dispatchEvent(new Event("input"));
    const storeBefore = localReviewValues.get(STORE_KEY);
    const eventsBefore = elements.get("role-review-events").innerHTML;
    elements.get("role-review-record").trigger("click");
    staleRoleWrites.push({
      storeUnchanged: localReviewValues.get(STORE_KEY) === storeBefore,
      eventsUnchanged: elements.get("role-review-events").innerHTML === eventsBefore,
      notePreserved: elements.get("role-review-note").value === roleNoteDraft,
      evidencePreserved: elements.get("role-review-evidence").value === roleEvidenceDraft,
      ariaDisabled: elements.get("role-review-record").getAttribute("aria-disabled"),
      describedBy: elements.get("role-review-record").getAttribute("aria-describedby"),
      focusable: !elements.get("role-review-record").disabled,
      banner: elements.get("stale-banner-text").textContent,
      toast: elements.get("toast").textContent,
    });
  }
  posting.value = checkedRolePosting;
  posting.dispatchEvent(new Event("input"));
  const restoredRoleWrite = {
    enabled: !elements.get("role-review-record").disabled &&
      elements.get("role-review-record").getAttribute("aria-disabled") !== "true",
    describedBy: elements.get("role-review-record").getAttribute("aria-describedby"),
  };
  elements.get("role-review-record").trigger("click");
  restoredRoleWrite.savedDraft = readStore().packets[0].events.at(-1).note === roleNoteDraft;
  // The first event saves the packet.
  const roleFingerprint = readStore().packets[0].posting_fingerprint;
  const currentPacket = () =>
    readStore().packets.find(
      (packet) => packet.posting_fingerprint === roleFingerprint
    );
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
  const driftNotice = elements.get("role-review-notice").textContent;
  const storedBeforeFirstEvent = readStore().packets.length;
  // The new packet is saved with its first recorded event, not by the check.
  elements.get("role-review-action").value = "note";
  elements.get("role-review-action").trigger("change");
  elements.get("role-review-note").value = "새 규칙셋 기준으로 다시 확인했습니다.";
  elements.get("role-review-record").trigger("click");
  const afterDrift = readStore();
  const roleReviewAfterVersionDrift = {
    newPacket: afterDrift.packets.some(
      (packet) => packet.packet_id !== priorPacketId
    ),
    oldPacketKept: afterDrift.packets.some(
      (packet) => packet.packet_id === priorPacketId && packet.events.length === 4
    ),
    storedBeforeFirstEvent,
    packetCount: afterDrift.packets.length,
    notice: driftNotice,
    progress: elements.get("role-review-progress").textContent,
  };

  // A different posting (one character changed) gets its own packet, saved
  // once the reader records something in it.
  const packetsBeforeChangedPosting = readStore().packets;
  const changedPostingDraft = "다른 공고문의 첫 기록입니다.";
  elements.get("role-review-note").value = changedPostingDraft;
  elements.get("role-review-evidence").value = "ncs-process";
  posting.value = "여성만 지원 가능.";
  posting.dispatchEvent(new Event("input"));
  const storeBeforeRecheck = localReviewValues.get(STORE_KEY);
  elements.get("role-review-record").trigger("click");
  const changedPostingWrite = {
    staleStoreUnchanged: localReviewValues.get(STORE_KEY) === storeBeforeRecheck,
  };
  elements.get("check-button").trigger("click");
  changedPostingWrite.disabledWhilePreparing = elements.get("role-review-record").disabled;
  elements.get("role-review-record").trigger("click");
  changedPostingWrite.preparingStoreUnchanged = localReviewValues.get(STORE_KEY) === storeBeforeRecheck;
  await tick();
  changedPostingWrite.enabledAfterRecheck = !elements.get("role-review-record").disabled &&
    elements.get("role-review-record").getAttribute("aria-disabled") !== "true";
  changedPostingWrite.draftPreserved = elements.get("role-review-note").value === changedPostingDraft &&
    elements.get("role-review-evidence").value === "ncs-process";
  const otherPostingNotice = elements.get("role-review-notice").textContent;
  const storedBeforeOtherEvent = readStore().packets.length;
  elements.get("role-review-record").trigger("click");
  const packetsAfterChangedPosting = readStore().packets;
  changedPostingWrite.oldPacketsUnchanged = packetsBeforeChangedPosting.every((packet) =>
    JSON.stringify(packetsAfterChangedPosting.find((stored) => stored.packet_id === packet.packet_id)) ===
      JSON.stringify(packet)
  );
  const changedPostingPacket = packetsAfterChangedPosting.find(
    (packet) => packet.posting_fingerprint !== roleFingerprint
  );
  changedPostingWrite.savedInNewFingerprint = Boolean(changedPostingPacket &&
    changedPostingPacket.events.at(-1).note === changedPostingDraft);
  const otherPosting = {
    storedBeforeFirstEvent: storedBeforeOtherEvent,
    packetCount: readStore().packets.length,
    notice: otherPostingNotice,
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

  // A failed later save leaves an earlier storage copy in place. Deleting
  // must still attempt storage, and must keep the visible state on failure.
  const savedBeforeFailure = localReviewValues.get(STORE_KEY);
  globalThis.localStorage = {
    ...workingStorage,
    setItem() { throw new Error("SecurityError: simulated write failure"); },
  };
  elements.get("role-review-note").value = "현재 세션에만 남은 추가 기록입니다.";
  elements.get("role-review-record").trigger("click");
  nodeAssert.equal(localReviewValues.get(STORE_KEY), savedBeforeFailure);
  const eventsBeforeFailedDelete = elements.get("role-review-events").innerHTML;
  elements.get("role-review-note").value = "삭제 실패 중에도 작성 중인 메모를 보존합니다.";
  elements.get("role-review-evidence").value = "ncs-process";
  elements.get("role-review-clear").trigger("click");
  nodeAssert.equal(localReviewValues.get(STORE_KEY), savedBeforeFailure);
  nodeAssert.equal(elements.get("role-review-panel").hidden, false);
  nodeAssert.equal(elements.get("role-review-events").innerHTML, eventsBeforeFailedDelete);
  nodeAssert.equal(elements.get("role-review-note").value, "삭제 실패 중에도 작성 중인 메모를 보존합니다.");
  nodeAssert.equal(elements.get("role-review-evidence").value, "ncs-process");
  nodeAssert.match(elements.get("toast").textContent, /삭제하지 못했습니다/);
  nodeAssert.match(elements.get("role-review-notice").textContent, /다시 시도/);
  globalThis.localStorage = workingStorage;

  // Deleting asks first and removes only this posting's packet.
  const promptsBeforeDelete = confirmPrompts.length;
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
    prompts: confirmPrompts.length - promptsBeforeDelete,
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
  // Checking never writes, so a full store is untouched until a first event.
  const storeAfterCheck = localReviewValues.get(STORE_KEY);
  const toastAfterCheck = elements.get("toast").textContent;
  elements.get("role-review-note").value = "보관 한도 확인용 첫 기록입니다.";
  elements.get("role-review-record").trigger("click");
  const retention = {
    packetsBefore: fullStore.packets.length,
    storeUntouchedByCheck: storeAfterCheck === JSON.stringify(fullStore),
    noToastFromCheck: !toastAfterCheck.includes("보관 한도"),
    packetCount: readStore().packets.length,
    oldestEvicted: !readStore().packets.some(
      (packet) => packet.packet_id === oldestFiller
    ),
    notice: elements.get("role-review-notice").textContent,
  };

  // Packets nobody wrote in are evicted before any packet that holds a
  // reader's own events, even when those events are older.
  const userEventPacket = readStore().packets.find(
    (packet) => packet.events.some((event) => event.actor_ref !== "system-chair")
  );
  const keptStore = readStore();
  keptStore.packets.forEach((packet, index) => {
    packet.updated_at = packet === userEventPacket
      ? "2019-01-01T00:00:00.000Z"
      : `2021-01-01T00:00:${String(index).padStart(2, "0")}.000Z`;
  });
  const oldestOwnId = userEventPacket.packet_id;
  const emptyIdsBefore = keptStore.packets
    .filter((packet) => packet.events.every((event) => event.actor_ref === "system-chair"))
    .map((packet) => packet.packet_id);
  localReviewValues.set(STORE_KEY, JSON.stringify(keptStore));
  posting.value = "여성만 지원 가능 (보관 우선순위 확인)";
  posting.dispatchEvent(new Event("input"));
  elements.get("check-button").trigger("click");
  await tick();
  elements.get("role-review-note").value = "보관 우선순위 확인용 기록입니다.";
  elements.get("role-review-record").trigger("click");
  const afterPriority = readStore();
  const evictionPriority = {
    packetCount: afterPriority.packets.length,
    ownRecordsKept: afterPriority.packets.some((packet) => packet.packet_id === oldestOwnId),
    oneEmptyPacketEvicted: emptyIdsBefore.filter(
      (id) => !afterPriority.packets.some((packet) => packet.packet_id === id)
    ).length === 1,
  };

  // Many reviews in a row leave storage exactly as it was.
  const storeBeforeLoop = localReviewValues.get(STORE_KEY);
  const toastBeforeLoop = elements.get("toast").textContent;
  for (let index = 0; index < 25; index += 1) {
    posting.value = `여성만 지원 가능 (반복 검토 ${index})`;
    posting.dispatchEvent(new Event("input"));
    elements.get("check-button").trigger("click");
    await tick();
  }
  const repeatedChecks = {
    storeUnchanged: localReviewValues.get(STORE_KEY) === storeBeforeLoop,
    toastUnchanged: elements.get("toast").textContent === toastBeforeLoop ||
      !elements.get("toast").textContent.includes("보관 한도"),
  };

  // "이 브라우저의 모든 역할 기록 삭제": asks first, then removes every packet.
  confirmAnswer = false;
  const promptsBeforeAll = confirmPrompts.length;
  elements.get("role-review-clear-all").trigger("click");
  const clearAllCancelled = {
    stored: localReviewValues.has(STORE_KEY),
    prompt: confirmPrompts.slice(promptsBeforeAll)[0] || "",
    panelHidden: elements.get("role-review-panel").hidden,
  };
  confirmAnswer = true;
  elements.get("role-review-clear-all").trigger("click");
  const clearAllDone = {
    stored: localReviewValues.has(STORE_KEY),
    legacyStored: localReviewValues.has(LEGACY_KEY),
    panelHidden: elements.get("role-review-panel").hidden,
    toast: elements.get("toast").textContent,
  };
  elements.get("check-button").trigger("click");
  await tick();
  clearAllDone.nextReviewSavesNothing = !localReviewValues.has(STORE_KEY);

  // Model storage mutations from another tab without another VM: the write
  // handler must re-read storage, preserve concurrent additions, and reject
  // a deleted or replaced packet without writing old events back.
  elements.get("role-review-action").value = "note";
  elements.get("role-review-note").value = "다른 탭에서 삭제할 기존 기록입니다.";
  elements.get("role-review-evidence").value = "";
  elements.get("role-review-record").trigger("click");
  const beforeOtherTab = readStore();
  const activePacket = beforeOtherTab.packets[0];
  const concurrentNote = "다른 탭에서 먼저 추가한 기록입니다.";
  activePacket.events.push({
    ...activePacket.events.at(-1),
    event_id: "browser-event-concurrent-addition",
    note: concurrentNote,
  });
  localReviewValues.set(STORE_KEY, JSON.stringify(beforeOtherTab));
  elements.get("role-review-note").value = "현재 탭에서 추가하는 기록입니다.";
  elements.get("role-review-record").trigger("click");
  nodeAssert.ok(readStore().packets[0].events.some((event) => event.note === concurrentNote));
  nodeAssert.ok(readStore().packets[0].events.some((event) => event.note === "현재 탭에서 추가하는 기록입니다."));
  const currentTabDraft = "삭제 이후의 작성 중인 메모를 보존합니다.";
  localReviewValues.delete(STORE_KEY);
  elements.get("role-review-note").value = currentTabDraft;
  elements.get("role-review-evidence").value = "ncs-process";
  elements.get("role-review-record").trigger("click");
  nodeAssert.equal(localReviewValues.has(STORE_KEY), false);
  nodeAssert.equal(elements.get("role-review-note").value, currentTabDraft);
  nodeAssert.equal(elements.get("role-review-evidence").value, "ncs-process");
  nodeAssert.equal(elements.get("role-review-record").disabled, true);
  nodeAssert.equal(elements.get("role-review-panel").hidden, false);
  nodeAssert.match(elements.get("role-review-notice").textContent, /다른 탭/);
  nodeAssert.match(elements.get("role-review-status").textContent, /다시 검토 필요/);
  elements.get("check-button").trigger("click");
  await tick();
  elements.get("role-review-record").trigger("click");
  nodeAssert.deepEqual(readStore().packets[0].events.filter((event) => event.actor_ref !== "system-chair").map((event) => event.note), [currentTabDraft]);

  // Deletion followed by a fresh packet for the same posting has the same
  // fingerprint/key. Its packet ID still separates it from the old copy.
  const replacementStore = readStore();
  replacementStore.packets[0].packet_id = "browser-packet-recreated-in-other-tab";
  replacementStore.packets[0].events = [replacementStore.packets[0].events[0]];
  localReviewValues.set(STORE_KEY, JSON.stringify(replacementStore));
  const replacementRaw = localReviewValues.get(STORE_KEY);
  elements.get("role-review-note").value = "다시 생성된 패킷 확인 전 임시 메모입니다.";
  elements.get("role-review-record").trigger("click");
  nodeAssert.equal(localReviewValues.get(STORE_KEY), replacementRaw);
  nodeAssert.equal(elements.get("role-review-note").value, "다시 생성된 패킷 확인 전 임시 메모입니다.");
  elements.get("check-button").trigger("click");
  await tick();
  elements.get("role-review-record").trigger("click");
  nodeAssert.equal(readStore().packets[0].packet_id, "browser-packet-recreated-in-other-tab");
  nodeAssert.equal(readStore().packets[0].events.length, 2);

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
    unsavedPacket,
    evictionPriority,
    repeatedChecks,
    clearAllCancelled,
    clearAllDone,
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
    staleWrites: staleRoleWrites,
    restoredWrite: restoredRoleWrite,
    changedPostingWrite,
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
    // Locked but explained: aria-disabled, not a silent `disabled`.
    copyDisabled: elements.get("copy-button").disabled ||
      elements.get("copy-button").getAttribute("aria-disabled") === "true",
    copyAriaDisabled: elements.get("copy-button").getAttribute("aria-disabled"),
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
  // The reset button disables itself once the baseline is the current result;
  // focus moves to the panel heading instead of dropping to <body>.
  elements.get("comparison-title").focused = false;
  elements.get("comparison-reset").trigger("click");
  nodeAssert.equal(elements.get("comparison-reset").disabled, true);
  nodeAssert.equal(elements.get("comparison-title").focused, true);
  const resetComparison = elements.get("comparison-groups").innerHTML;
  posting.value += "\n여성만 지원 가능";
  posting.dispatchEvent(new Event("input"));
  elements.get("check-button").trigger("click");
  const rebasedComparison = elements.get("comparison-groups").innerHTML;

  // A short posting that is only edited keeps its comparison (one edited line
  // leaves no line in common, so characters decide).
  posting.value = "여성만 지원 가능";
  posting.dispatchEvent(new Event("input"));
  elements.get("check-button").trigger("click");
  posting.value = "여성만 지원 가능 (신입)";
  posting.dispatchEvent(new Event("input"));
  elements.get("check-button").trigger("click");
  const tinyEdit = {
    status: elements.get("comparison-status").textContent,
    kept: elements.get("comparison-groups").innerHTML !== "",
  };

  // A different posting pasted over the baseline starts a new baseline: no
  // before/after list between two unrelated postings, in the panel or the memo.
  posting.value = [
    "청소년 상담사 채용", "", "담당업무", "- 지역 청소년 상담과 사례관리",
    "- 상담 기록 작성", "", "근무조건", "- 주 5일 근무", "", "접수 방법", "- 이메일 접수",
  ].join("\n");
  posting.dispatchEvent(new Event("input"));
  elements.get("check-button").trigger("click");
  const unrelatedStatus = elements.get("comparison-status").textContent;
  const unrelatedPaste = {
    status: unrelatedStatus,
    groups: elements.get("comparison-groups").innerHTML,
    panelHidden: elements.get("comparison-panel").hidden,
    announcement: elements.get("review-live").textContent,
    improvementHidden: elements.get("improvement-panel").hidden,
  };
  await elements.get("copy-button").trigger("click");
  unrelatedPaste.memoHasComparison = copied.includes("[수정 전후 비교]");
  unrelatedPaste.memoHasChanges = copied.includes("[공고문 변경 내역]");
  // The new posting is compared from here on, as usual.
  posting.value += "\n- 상담 일지 작성";
  posting.dispatchEvent(new Event("input"));
  elements.get("check-button").trigger("click");
  unrelatedPaste.nextEditCompared = elements.get("comparison-status").textContent.includes("비교 기준 이후 항목 변화");
  unrelatedPaste.nextEditSummaryShown = elements.get("improvement-panel").hidden === false;

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
  // Emptying the field by hand only makes the result stale; "×" resets.
  posting.value = "";
  posting.dispatchEvent(new Event("input"));
  const manualEmptyComparisonHidden = elements.get("comparison-panel").hidden;
  elements.get("clear-button").trigger("click");
  const clearedComparisonHidden = elements.get("comparison-panel").hidden;
  const comparisonFlow = {
    firstComparison, staleComparison, changedComparison, comparisonReport,
    thirdComparison, unchangedComparison, resetComparison, rebasedComparison,
    tinyEdit, unrelatedPaste,
    versionComparison, comparisonStored, sampleComparisonHidden,
    sampleComparisonGroups, manualEmptyComparisonHidden, clearedComparisonHidden,
    postsAdded: posts().length - comparisonPostsBefore,
  };


  // Book companion reader flow shares answer state without duplicate controls.
  const assert = require("node:assert/strict");
  elements.get("mode-easy").trigger("click");
  const readerPostsBefore = posts().length;
  posting.value = "일반 채용공고\n지원자격: 관련 업무 경험\n서류전형과 면접";
  posting.dispatchEvent(new Event("input"));
  elements.get("check-button").trigger("click");
  await tick();
  const readerResult = window.FairpostEngine.check(posting.value);
  const readerId = readerResult.questions[0].id;
  const dynamicMarkup = () => ["easy-result", "findings-list", "slots-list", "questions-list"]
    .map((id) => elements.get(id).innerHTML).join("\n");
  const assertUniqueIds = (label) => {
    const foundIds = [...dynamicMarkup().matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
    assert.equal(new Set(foundIds).size, foundIds.length, label);
    readerResult.questions.forEach((question) => {
      assert.equal(foundIds.filter((id) => id === `answer-${question.id}`).length, 1,
        `${label}: exactly one answer for ${question.id}`);
    });
  };
  assertUniqueIds("easy");
  const easyQuestions = elements.get("questions-list").innerHTML;
  for (const meaning of ["분배 · 기회와 요건", "절차 · 기준과 재검토", "대인 · 존중과 목소리", "정보 · 설명과 접근"]) {
    assert.ok(easyQuestions.includes(meaning), meaning);
  }
  assert.equal((easyQuestions.match(/class="fairness-group"/g) || []).length, 4);
  assert.ok(elements.get("easy-result").innerHTML.includes("AI 사용 여부와 안내 적용 여부"));
  const readerAnswer = new FakeTextAreaElement(`answer-${readerId}`);
  readerAnswer.dataset.questionAnswer = readerId;
  readerAnswer.value = '근거 확인 <img src=x onerror=alert(1)>';
  elements.get("result-content").trigger("input", { target: readerAnswer });
  for (const [id, value] of [
    ["human-review-evidence", "직무기술서와 공고 요건 대조"],
    ["human-review-reason", "경력 조건 수정 이유"],
    ["human-review-next", "인사 담당자 · 운영 여부 확인 · 10월 12일"],
  ]) {
    elements.get(id).value = value;
    elements.get(id).trigger("input");
  }
  elements.get("mode-expert").trigger("click");
  assertUniqueIds("expert");
  const expertQuestions = dynamicMarkup();
  assert.ok(expertQuestions.includes("&lt;img src=x onerror=alert(1)&gt;"));
  elements.get("mode-easy").trigger("click");
  assertUniqueIds("easy after mode roundtrip");
  assert.ok(elements.get("questions-list").innerHTML.includes("&lt;img src=x onerror=alert(1)&gt;"));
  assert.equal(elements.get("human-review-reason").value, "경력 조건 수정 이유");
  await elements.get("copy-button").trigger("click");
  const recordMemo = copied;
  assert.ok(recordMemo.includes("[현재 공고 검토 기록 — 자기 기록]"));
  assert.ok(recordMemo.includes("인사 담당자 · 운영 여부 확인 · 10월 12일"));
  assert.ok(recordMemo.includes(readerAnswer.value));
  assert.ok(recordMemo.includes("입력은 실제 절차의 검증 완료나 공정성 보증을 뜻하지 않습니다."));
  elements.get("check-button").trigger("click");
  assert.ok(elements.get("questions-list").innerHTML.includes("&lt;img src=x onerror=alert(1)&gt;"));
  assert.equal(elements.get("human-review-reason").value, "경력 조건 수정 이유");
  // Draft records must never be persisted by the default review.
  assert.ok(![...localReviewValues.values()].some((value) => value.includes("경력 조건 수정 이유")));
  const memoBeforeEdit = copied;
  posting.value += "\n요건을 보완한 공고";
  posting.dispatchEvent(new Event("input"));
  assert.equal(elements.get("human-review-reason").disabled, true);
  // Locked but explained: the copy button stays focusable (aria-disabled) and
  // points at the banner that says the posting changed.
  assert.equal(elements.get("copy-button").getAttribute("aria-disabled"), "true");
  assert.equal(elements.get("copy-button").getAttribute("aria-describedby"), "stale-banner-text");
  assert.ok(elements.get("stale-banner-text").textContent.startsWith("공고문이 바뀌었습니다"));
  assert.equal(elements.get("stale-recheck").hidden, false);
  assert.ok(elements.get("human-review-status").textContent.includes("수정 전 공고 기준"));
  // Only the banner announces the stale state; the lower status lines go quiet.
  assert.equal(elements.get("human-review-status").getAttribute("aria-live"), "off");
  readerAnswer.value = "stale answer must not replace the prior answer";
  elements.get("result-content").trigger("input", { target: readerAnswer });
  elements.get("human-review-reason").value = "stale record must not replace the prior record";
  elements.get("human-review-reason").trigger("input");
  await elements.get("copy-button").trigger("click");
  assert.equal(copied, memoBeforeEdit);
  elements.get("check-button").trigger("click");
  await tick();
  assert.equal(elements.get("human-review-reason").value, "");
  assert.equal(elements.get("human-review-reason").disabled, false);
  assert.equal(elements.get("human-review-status").getAttribute("aria-live"), "polite");
  assert.equal(elements.get("previous-review-records").hidden, false);
  const previousMarkup = elements.get("previous-review-records-content").innerHTML;
  assert.ok(previousMarkup.includes("현재 공고에 자동 적용되지 않음"));
  assert.ok(previousMarkup.includes("&lt;img src=x onerror=alert(1)&gt;"));
  assert.ok(previousMarkup.includes("경력 조건 수정 이유"));
  assert.ok(!previousMarkup.includes("stale answer"));
  assert.ok(!previousMarkup.includes("<img"));
  await elements.get("copy-button").trigger("click");
  const afterEditMemo = copied;
  assert.ok(!afterEditMemo.includes("[현재 공고 검토 기록 — 자기 기록]"));
  assert.ok(afterEditMemo.includes("[수정 전 검토 기록 — 현재 공고에 자동 적용되지 않음]"));
  assert.ok(!afterEditMemo.includes("stale record"));
  assert.ok(!afterEditMemo.includes("담당자 답변: 근거 확인"));
  elements.get("mode-expert").trigger("click");
  elements.get("mode-easy").trigger("click");
  assert.equal(elements.get("previous-review-records-content").innerHTML, previousMarkup);
  const rulesBeforeReaderTest = window.FAIRPOST_DATA.version;
  readerAnswer.value = "버전 변경 전 답변";
  elements.get("result-content").trigger("input", { target: readerAnswer });
  window.FAIRPOST_DATA.version += "-reader-change";
  elements.get("check-button").trigger("click");
  assert.ok(!elements.get("questions-list").innerHTML.includes("버전 변경 전 답변"));
  assert.ok(elements.get("previous-review-records-content").innerHTML.includes("버전 변경 전 답변"));
  window.FAIRPOST_DATA.version = rulesBeforeReaderTest;
  const postsDuringReader = posts().length - readerPostsBefore;
  assert.equal(postsDuringReader, 0);

  // Only reviews with content consume the bounded archive; oldest drafts are evicted.
  for (let index = 0; index < 12; index += 1) {
    elements.get("human-review-evidence").value = `retained-review-${index}-END`;
    elements.get("human-review-evidence").trigger("input");
    posting.value += `\n수정 ${index}`;
    posting.dispatchEvent(new Event("input"));
    elements.get("check-button").trigger("click");
  }
  const boundedMarkup = elements.get("previous-review-records-content").innerHTML;
  assert.equal((boundedMarkup.match(/class="previous-record"/g) || []).length, 10);
  assert.ok(!boundedMarkup.includes("retained-review-0-END"));
  assert.ok(!boundedMarkup.includes("retained-review-1-END"));
  assert.ok(boundedMarkup.includes("retained-review-2-END"));
  assert.ok(boundedMarkup.includes("retained-review-11-END"));
  // Numbers follow the order records were made, so they do not shift when the
  // oldest ones are dropped at the cap; the counter shows the cap; times are
  // local and the rule-set label is short.
  const recordNumbers = [...boundedMarkup.matchAll(/수정 전 기록 (\d+)번/g)].map((match) => Number(match[1]));
  assert.equal(recordNumbers.length, 10);
  assert.ok(recordNumbers[0] > 1, "the oldest records were dropped, numbers kept");
  recordNumbers.slice(1).forEach((number, index) => assert.equal(number, recordNumbers[index] + 1));
  assert.ok(boundedMarkup.includes("보관 중 10/10개"));
  assert.ok(!/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(boundedMarkup), "no raw UTC time");
  assert.ok(!boundedMarkup.includes(window.FAIRPOST_DATA.version), "no full rule-set ID");
  assert.ok(/검토 기준 \d{4}\.\d{2} · [0-9a-f]{8}/.test(boundedMarkup));
  const recordTimes = [...boundedMarkup.matchAll(/(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) · 검토 기준/g)];
  assert.equal(recordTimes.length, 10);

  // "예시 입력" asks first because 10 earlier records would be dropped.
  const promptsBeforeSample = confirmPrompts.length;
  elements.get("sample-button").trigger("click");
  assert.equal(confirmPrompts.length, promptsBeforeSample + 1);
  assert.ok(confirmPrompts[promptsBeforeSample].includes("수정 전 기록 10개"));
  assert.equal(elements.get("previous-review-records").hidden, true);
  assert.equal(elements.get("human-review-evidence").value, "");
  elements.get("check-button").trigger("click");
  assert.equal(elements.get("previous-review-records").hidden, true);
  const sampleQuestionMarkup = elements.get("questions-list").innerHTML;
  posting.value = "AI 면접으로 최종 결정";
  posting.dispatchEvent(new Event("input"));
  elements.get("check-button").trigger("click");
  const aiQuestions = elements.get("questions-list").innerHTML;
  assert.ok(aiQuestions.includes("Q-PROC-004"));
  assert.ok(aiQuestions.includes("자료 위치: 제6장 결론"));
  assert.ok(aiQuestions.includes("책 관점 연결: 3부 3-1"));
  assert.ok(aiQuestions.includes("문맥·직접 근거·조직 적용 범위"));
  assert.ok(sampleQuestionMarkup.includes('class="fairness-group"'));
  // Static HTML must expose reader sections while keeping deployment secondary.
  const webHtml = fs.readFileSync(path.join(root, "web/index.html"), "utf8");
  assert.ok(webHtml.includes('class="result-block shared-questions"'));
  assert.ok(webHtml.includes('class="review-progress-strip"'));
  assert.ok(webHtml.includes("새로고침·지우기·예시 입력으로 없어"));
  assert.ok(webHtml.includes("기록이 있는 검토를 최근 최대 10회"));
  assert.ok(webHtml.includes("더 오래된 기록은 제거"));
  assert.ok(!webHtml.slice(webHtml.indexOf("<header"), webHtml.indexOf("</header>")).includes("vercel"));
  assert.ok(webHtml.slice(webHtml.indexOf("<footer")).includes("https://vercel.com/new/clone"));
  const readerFlow = { defaultExternalPosts: postsDuringReader, easyQuestionsAccessible: true,
    uniqueAnswerIds: true, modeRoundtripPreserved: true, sameInputRecheckPreserved: true,
    staleCopyBlocked: true, priorRecordsSeparated: true, escapedPriorRecords: true,
    recordMemo, afterEditMemo };

  console.log(JSON.stringify({
    readerFlow,
    comparisonFlow,
    easySelection,
    questionId,
    questionCount: result.questions.length,
    resultsTitleFocused,
    progressAfterAnswer,
    copiedWithAnswer,
    manuallyCleared,
    staleBannerAfterTypingBack,
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
