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
  }

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
globalThis.localStorage = {
  getItem(key) { return localReviewValues.has(key) ? localReviewValues.get(key) : null; },
  setItem(key, value) { localReviewValues.set(key, String(value)); },
  removeItem(key) { localReviewValues.delete(key); },
};
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
globalThis.fetch = async (url, options = {}) => {
  assistedFetchCalls.push({ url, options });
  if ((options.method || "GET") === "GET") {
    return {
      ok: true,
      async json() {
        return {
          ready: true,
          privacy: "선별된 근거만 외부 서비스로 전달합니다.",
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
  const assistedToggle = elements.get("assisted-review-toggle");
  assistedToggle.checked = true;
  assistedToggle.trigger("change");
  await new Promise((resolve) => setImmediate(resolve));
  elements.get("check-button").trigger("click");
  await new Promise((resolve) => setImmediate(resolve));
  const assistedPost = assistedFetchCalls.find(
    (call) => call.options.method === "POST"
  );
  const assisted = {
    badge: elements.get("assisted-review-badge").textContent,
    resultStatus: elements.get("assisted-review-result-status").textContent,
    output: elements.get("assisted-review-output").textContent,
    panelHidden: elements.get("assisted-review-panel").hidden,
    postBody: assistedPost ? JSON.parse(assistedPost.options.body) : null,
    organizationMarkup: elements.get("questions-list").innerHTML,
  };

  await new Promise((resolve) => setImmediate(resolve));
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
    eventCount: JSON.parse(localReviewValues.get("fairpost.role-review.v1")).events.length,
  };
  elements.get("role-review-action").value = "edit_requested";
  elements.get("role-review-note").value = "직무 요건 근거를 보강해야 합니다.";
  elements.get("role-review-record").trigger("click");
  const roleReviewWithIssue = JSON.parse(
    localReviewValues.get("fairpost.role-review.v1")
  );
  const issueEventId = roleReviewWithIssue.events.find(
    (event) => event.action === "edit_requested"
  ).event_id;
  elements.get("role-review-role").value = "hr_owner";
  elements.get("role-review-action").value = "resolve";
  elements.get("role-review-action").trigger("change");
  elements.get("role-review-resolve-event").value = issueEventId;
  elements.get("role-review-note").value = "직무 요건 근거를 보강했습니다.";
  elements.get("role-review-record").trigger("click");
  const roleReviewBeforeVersionDrift = {
    status: elements.get("role-review-status").textContent,
    progress: elements.get("role-review-progress").textContent,
    missingRoles: elements.get("role-review-missing").textContent,
    eventsMarkup: elements.get("role-review-events").innerHTML,
    storage: localReviewValues.get("fairpost.role-review.v1") || "",
  };
  const priorRoleReview = JSON.parse(
    localReviewValues.get("fairpost.role-review.v1")
  );
  const priorPacketId = priorRoleReview.packet_id;
  priorRoleReview.ruleset_version = "stale-ruleset";
  localReviewValues.set(
    "fairpost.role-review.v1",
    JSON.stringify(priorRoleReview)
  );
  elements.get("check-button").trigger("click");
  await new Promise((resolve) => setImmediate(resolve));
  const rotatedRoleReview = JSON.parse(
    localReviewValues.get("fairpost.role-review.v1")
  );
  const roleReviewAfterVersionDrift = {
    newPacket: rotatedRoleReview.packet_id !== priorPacketId,
    notice: elements.get("role-review-notice").textContent,
  };
  const roleReview = {
    ...roleReviewBeforeVersionDrift,
    afterSensitiveNote: roleReviewAfterSensitiveNote,
    afterVersionDrift: roleReviewAfterVersionDrift,
  };

  console.log(JSON.stringify({
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
    roleReview,
  }));
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
