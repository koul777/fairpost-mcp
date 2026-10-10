// Boots web/app.js in isolated contexts with a small fake DOM and checks the
// posting-improvement flow: book entry, example sentences, line rewrites,
// undo, the unfilled-blank boundary and the change summary.
// Uses web/posting-templates.js when it exists; otherwise a small fixture
// that follows the same data contract. Assertions read the template object
// from window.FAIRPOST_POSTING_TEMPLATES instead of hard-coding its wording.
// Prints one JSON summary on stdout; any failed check exits non-zero.
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..");
const TEMPLATE_FILE = "web/posting-templates.js";
const hasTemplateFile = fs.existsSync(path.join(root, TEMPLATE_FILE));
const sources = Object.fromEntries(
  ["web/data.js", "web/engine.js", "web/app.js", ...(hasTemplateFile ? [TEMPLATE_FILE] : [])]
    .map((relative) => [relative, fs.readFileSync(path.join(root, relative), "utf8")])
);
const VIEW_MODE_KEY = "fairpost.view-mode.v1";
const ROLE_REVIEW_KEY = "fairpost.role-review.v2";

const FIXTURE_TEMPLATES = {
  schema_version: "fairpost-posting-templates-v1",
  version: "abcdefabcdef",
  notice: "예시 문장입니다. ○○ 빈칸을 조직의 실제 사실로 바꾸고, 운영하지 않는 내용은 넣지 마세요.",
  placeholder: "○○",
  slots: {
    contact_point: {
      label: "문의 창구",
      text: "문의처\n- 담당 부서: ○○팀\n- 문의 방법과 시간: ○○ (평일 ○○)\n",
      condition: null,
      components: { hours: "문의 가능 시간: 평일 ○○부터 ○○까지" },
      sources: [{ title: "채용절차의 공정화에 관한 법률", locator: "" }],
    },
    result_notice: {
      label: "결과 안내",
      text: "결과 안내\n- 전형별 결과는 ○○까지 지원자 전원에게 ○○로 알립니다.",
      condition: null,
      components: {
        notice_time: "전형별 결과는 각 전형이 끝난 뒤 ○○일 이내에 알립니다.",
        notice_audience: "합격 여부와 관계없이 지원자 전원에게 결과를 알립니다.",
      },
      sources: [{ title: "채용절차의 공정화에 관한 법률", locator: "제10조" }],
    },
    appeal_channel: {
      label: "이의제기 경로",
      // CRLF on purpose: inserted text must use the textarea's "\n".
      text: "이의제기\r\n- 전형 결과에 이의가 있으면 결과 안내 후 ○○일 이내에 ○○로 신청할 수 있습니다.\r\n",
      condition: "",
      components: {},
      sources: [],
    },
    ai_disclosure: {
      label: "AI 활용 안내",
      text: "AI 활용 안내\n- ○○ 단계에서 ○○ 도구를 씁니다. 최종 결정은 사람이 합니다.",
      condition: "AI·자동화 도구를 실제로 쓰는 경우에만 넣으세요.",
      components: {},
      sources: [{ title: "<script>출처 확인</script>", locator: "" }],
    },
  },
};

class FakeElement {
  constructor(id = "") {
    this.id = id;
    this.value = "";
    this.textContent = "";
    this.innerHTML = "";
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.focused = false;
    this.dataset = {};
    this.handlers = new Map();
    this.attributes = new Map();
    this.classList = { add() {}, remove() {}, toggle() {} };
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
  focus() { this.focused = true; }
  select() {}
  remove() {}
  replaceChildren() { this.innerHTML = ""; }
}

class FakeTextAreaElement extends FakeElement {
  setSelectionRange(start, end) {
    this.selectionStart = start;
    this.selectionEnd = end;
  }
}

const clone = (value) => JSON.parse(JSON.stringify(value));
const tick = () => new Promise((resolve) => setImmediate(resolve));
const escapeHtml = (value) => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");
const count = (text, needle) => (needle ? String(text).split(needle).length - 1 : 0);

function boot({ search = "", templates = "file", storedMode = null, location = "present" } = {}) {
  const elements = new Map();
  const el = (id) => {
    if (!elements.has(id)) {
      elements.set(id, id === "posting-input" ? new FakeTextAreaElement(id) : new FakeElement(id));
    }
    return elements.get(id);
  };
  const storage = new Map();
  if (storedMode) storage.set(VIEW_MODE_KEY, storedMode);
  const storageWrites = [];
  const fetchCalls = [];
  const clipboard = { text: null };
  const sandbox = {
    console,
    URL,
    URLSearchParams,
    TextEncoder,
    setImmediate,
    setTimeout: () => 1,
    clearTimeout: () => {},
    HTMLTextAreaElement: FakeTextAreaElement,
    Event: class Event { constructor(type) { this.type = type; } },
    document: {
      getElementById: el,
      createElement: () => new FakeTextAreaElement(),
      body: { appendChild() {} },
      execCommand: () => false,
    },
    localStorage: {
      getItem: (key) => (storage.has(key) ? storage.get(key) : null),
      setItem: (key, value) => {
        storageWrites.push([key, String(value)]);
        storage.set(key, String(value));
      },
      removeItem: (key) => storage.delete(key),
    },
    navigator: { clipboard: { async writeText(value) { clipboard.text = value; } } },
    fetch: async (url, options = {}) => {
      fetchCalls.push({ url, method: options.method || "GET" });
      return {
        ok: true,
        status: 200,
        async json() {
          return { ready: false, available_providers: [], reason: "test server" };
        },
      };
    },
    crypto: {
      subtle: {
        async digest(_algorithm, bytes) {
          let hash = 2166136261;
          for (const byte of new Uint8Array(bytes)) hash = Math.imul(hash ^ byte, 16777619);
          const output = new Uint8Array(32);
          for (let index = 0; index < output.length; index += 1) {
            hash = Math.imul(hash ^ index, 16777619);
            output[index] = (hash >>> ((index % 4) * 8)) & 255;
          }
          return output.buffer;
        },
      },
    },
  };
  if (location === "present") {
    sandbox.location = { protocol: "https:", search };
  } else if (location === "throwing") {
    sandbox.location = {
      protocol: "https:",
      get search() { throw new Error("SecurityError: location blocked"); },
    };
  }
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  const run = (relative) => vm.runInContext(sources[relative], sandbox, { filename: relative });
  run("web/data.js");
  if (templates === "file" && hasTemplateFile) {
    run(TEMPLATE_FILE);
  } else if (templates === "file" || templates === "fixture") {
    sandbox.FAIRPOST_POSTING_TEMPLATES = clone(FIXTURE_TEMPLATES);
  } else if (templates !== "none") {
    sandbox.FAIRPOST_POSTING_TEMPLATES = templates;
  }
  run("web/engine.js");
  run("web/app.js");
  const posting = el("posting-input");
  const helpers = {
    sandbox, el, posting, storage, storageWrites, fetchCalls, clipboard,
    type(value) {
      // A reader edit: value, caret at the end, then the input event.
      posting.value = value;
      posting.selectionStart = value.length;
      posting.selectionEnd = value.length;
      posting.dispatchEvent({ type: "input" });
    },
    check() { el("check-button").trigger("click"); },
    click(selector, dataset) {
      const button = { dataset, closest: (wanted) => (wanted === selector ? button : null) };
      return el("result-content").trigger("click", { target: button });
    },
    insert(slot, where, component = "") {
      return helpers.click("[data-template-insert]", {
        templateInsert: where, templateSlot: slot, templateComponent: component,
      });
    },
    draft(index, value) {
      const field = new FakeTextAreaElement(`rewrite-${index}`);
      field.dataset.rewriteDraft = String(index);
      field.value = value;
      el("result-content").trigger("input", { target: field });
    },
    apply(index) { return helpers.click("[data-rewrite-apply]", { rewriteApply: String(index) }); },
    async copyMemo() {
      await el("copy-button").trigger("click");
      return clipboard.text;
    },
    markup() {
      return ["easy-result", "findings-list", "slots-list", "questions-list",
        "improvement-content", "improvement-remaining"]
        .map((id) => el(id).innerHTML).join("\n");
    },
    toast() { return el("toast").textContent; },
    posts() { return fetchCalls.filter((call) => call.method === "POST").length; },
  };
  return helpers;
}

// Mirrors the app's template validation so expectations follow the data.
function templateText(templates, slotId, componentId = "") {
  const slot = templates && templates.slots && Object.prototype.hasOwnProperty.call(templates.slots, slotId)
    ? templates.slots[slotId]
    : null;
  const clean = (value) => value.replace(/\r\n?/gu, "\n").replace(/\s+$/u, "");
  if (!slot || typeof slot.text !== "string" || !slot.text.trim()) return "";
  if (!componentId) return clean(slot.text);
  const sentence = slot.components && slot.components[componentId];
  return typeof sentence === "string" && sentence.trim() ? clean(sentence) : "";
}

const insertMarker = (where, slot, component = "") =>
  `data-template-insert="${where}" data-template-slot="${slot}" data-template-component="${component}"`;

function assertUniqueIds(app, label) {
  const ids = [...app.markup().matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(ids).size, ids.length, `${label}: duplicate ids ${ids}`);
  return ids.length;
}

function blankToast(placeholder, blanks) {
  return blanks
    ? `예시 문장을 넣었습니다. ${placeholder} 빈칸 ${blanks}곳을 실제 내용으로 바꾼 뒤 '검토 메모 만들기'를 다시 누르세요.`
    : "예시 문장을 넣었습니다. 조직의 실제 내용과 맞게 고친 뒤 '검토 메모 만들기'를 다시 누르세요.";
}

const BASE_POSTING = [
  "행정직 채용 공고",
  "",
  "자격요건",
  "- 용모 단정한 20대 지원자 우대",
  "- 관련 행정 업무 경험",
  "",
  "전형절차",
  "- 서류전형과 면접",
  "",
  "결과는 이메일로 개별 통보",
  "전형일정 추후 안내",
].join("\n");
const PRIVATE_MARK = "개선흐름-PRIVATE-DRAFT";

(async () => {
  const summary = { usingTemplateFile: hasTemplateFile };

  // 1. Book entry: easy mode for this page view, stored mode untouched.
  const book = boot({ search: "?entry=book", storedMode: "expert" });
  const plain = boot({ search: "", storedMode: "expert" });
  const otherEntry = boot({ search: "?entry=other" });
  const noLocation = boot({ location: "missing" });
  const blockedSearch = boot({ location: "throwing", storedMode: "expert" });
  summary.bookEntry = {
    shown: book.el("book-entry").hidden === false,
    mode: book.el("app-body").dataset.mode,
    storedModeKept: book.storage.get(VIEW_MODE_KEY) === "expert",
    modeWrites: book.storageWrites.filter(([key]) => key === VIEW_MODE_KEY).length,
    plainHidden: plain.el("book-entry").hidden === true,
    plainMode: plain.el("app-body").dataset.mode,
    otherEntryHidden: otherEntry.el("book-entry").hidden === true,
    noLocationHidden: noLocation.el("book-entry").hidden === true,
    noLocationMode: noLocation.el("app-body").dataset.mode,
    blockedSearchHidden: blockedSearch.el("book-entry").hidden === true,
    blockedSearchMode: blockedSearch.el("app-body").dataset.mode,
  };
  assert.equal(summary.bookEntry.shown, true);
  assert.equal(summary.bookEntry.mode, "easy");
  assert.equal(summary.bookEntry.storedModeKept, true);
  assert.equal(summary.bookEntry.modeWrites, 0);
  assert.equal(summary.bookEntry.plainMode, "expert");

  // 2. Example sentences for missing guidance (real bundle or fixture).
  const app = boot({ templates: "file" });
  const templates = app.sandbox.FAIRPOST_POSTING_TEMPLATES;
  const placeholder = templates.placeholder;
  app.type(BASE_POSTING);
  app.check();
  await tick();
  const firstResult = app.sandbox.FairpostEngine.check(BASE_POSTING);
  const missing = firstResult.slots.filter((slot) => !slot.found);
  const withTemplate = missing.filter((slot) => templateText(templates, slot.slot));
  assert.ok(withTemplate.length > 0, "at least one missing slot has an example sentence");
  let easy = app.el("easy-result").innerHTML;
  assert.ok(easy.includes(escapeHtml(templates.notice)));
  withTemplate.forEach((slot) => {
    const template = templates.slots[slot.slot];
    assert.ok(easy.includes(insertMarker("end", slot.slot)), `end button ${slot.slot}`);
    assert.ok(easy.includes(insertMarker("cursor", slot.slot)), `cursor button ${slot.slot}`);
    assert.ok(easy.includes(escapeHtml(templateText(templates, slot.slot))), `text ${slot.slot}`);
    if (typeof template.condition === "string" && template.condition.trim()) {
      assert.ok(easy.includes(escapeHtml(template.condition.trim())), `condition ${slot.slot}`);
    }
    (template.sources || []).forEach((source) => {
      assert.ok(easy.includes(escapeHtml(source.title)), `source ${slot.slot}`);
    });
  });
  missing.filter((slot) => !templateText(templates, slot.slot)).forEach((slot) => {
    assert.ok(!easy.includes(insertMarker("end", slot.slot)), `no button without template ${slot.slot}`);
  });
  assert.ok(!easy.includes("<script>"), "template text is escaped");

  // Question cards linked to a missing slot offer the same sentence (easy mode).
  const linkedQuestions = { "Q-INFO-001": "appeal_channel", "Q-INFO-004": "document_return", "Q-PROC-002": "evaluation_criteria" };
  const easyQuestions = app.el("questions-list").innerHTML;
  let linkedChecked = 0;
  let linkedWithButtons = 0;
  Object.entries(linkedQuestions).forEach(([questionId, slotId]) => {
    const fired = firstResult.questions.some((question) => question.id === questionId);
    const slotMissing = missing.some((slot) => slot.slot === slotId);
    const marker = `<article id="question-${questionId}"`;
    if (!fired || !easyQuestions.includes(marker)) return;
    const card = easyQuestions.split(marker)[1].split("</article>")[0];
    const expected = slotMissing && Boolean(templateText(templates, slotId));
    assert.equal(card.includes(insertMarker("end", slotId)), expected, questionId);
    linkedChecked += 1;
    if (expected) linkedWithButtons += 1;
  });
  if (!hasTemplateFile) assert.ok(linkedWithButtons >= 1, "fixture covers a linked question card");
  const easyIdCount = assertUniqueIds(app, "easy");

  // Expert mode: slot cards carry exactly one set; embedded questions add none.
  app.el("mode-expert").trigger("click");
  const slotsMarkup = app.el("slots-list").innerHTML;
  withTemplate.forEach((slot) => {
    assert.equal(count(slotsMarkup, insertMarker("end", slot.slot)), 1, `expert slot ${slot.slot}`);
    assert.equal(count(slotsMarkup, insertMarker("cursor", slot.slot)), 1, `expert slot ${slot.slot}`);
  });
  assert.equal(count(app.el("questions-list").innerHTML, "data-template-insert"), 0);
  const expertIdCount = assertUniqueIds(app, "expert");
  app.el("mode-easy").trigger("click");
  assertUniqueIds(app, "easy after roundtrip");

  // Insert at the end: one blank line, stale result, memo copy blocked.
  const memoBeforeInsert = await app.copyMemo();
  const endSlot = withTemplate[0].slot;
  const endText = templateText(templates, endSlot);
  const endBlanks = count(endText, placeholder);
  app.insert(endSlot, "end");
  const afterEnd = `${BASE_POSTING.replace(/\s+$/u, "")}\n\n${endText}`;
  assert.equal(app.posting.value, afterEnd);
  assert.equal(app.toast(), blankToast(placeholder, endBlanks));
  if (endBlanks) {
    const first = afterEnd.indexOf(placeholder, BASE_POSTING.length + 2);
    assert.deepEqual([app.posting.selectionStart, app.posting.selectionEnd], [first, first + placeholder.length]);
  }
  const staleAfterInsert = {
    copyDisabled: app.el("copy-button").disabled,
    recordDisabled: app.el("human-review-evidence").disabled,
    status: app.el("human-review-status").textContent,
    undoShown: app.el("posting-undo").hidden === false,
    undoUsable: app.el("posting-undo").getAttribute("aria-disabled") === "false",
  };
  assert.equal(staleAfterInsert.copyDisabled, true);
  assert.equal(staleAfterInsert.recordDisabled, true);
  assert.ok(staleAfterInsert.status.includes("공고문이 바뀌었습니다"));
  assert.equal(await app.copyMemo(), memoBeforeInsert, "stale memo copy is blocked");
  assert.equal(staleAfterInsert.undoShown && staleAfterInsert.undoUsable, true);

  // Re-check: blank boundary, change summary and posting copy.
  app.check();
  await tick();
  const blanks = count(app.posting.value, placeholder);
  // Blanks are located by posting line; slot evidence holds only a first line.
  const blankLineNumbers = app.posting.value
    .split("\n")
    .map((line, index) => (line.includes(placeholder) ? index + 1 : 0))
    .filter(Boolean);
  const blankLines = blankLineNumbers.length > 12
    ? `${blankLineNumbers.slice(0, 12).join(", ")}번째 줄 외 ${blankLineNumbers.length - 12}줄`
    : `${blankLineNumbers.join(", ")}번째 줄`;
  const notice = app.el("placeholder-notice");
  if (blanks) {
    assert.equal(notice.hidden, false);
    assert.equal(notice.textContent,
      `공고문에 채우지 않은 빈칸(${placeholder})이 ${blanks}곳 있습니다(${blankLines}). 빈칸이 남은 안내는 찾은 것으로 표시되어도 아직 완성된 안내가 아닙니다.`);
  } else {
    assert.equal(notice.hidden, true);
  }
  easy = app.el("easy-result").innerHTML;
  assert.ok(!easy.includes("placeholder-tag"), "no per-slot blank tag from first-line evidence");
  if (blanks && easy.includes('id="easy-found-heading"')) {
    assert.ok(easy.includes(`빈칸(${escapeHtml(placeholder)})이 남은 줄이 있습니다: ${escapeHtml(blankLines)}.`));
  }
  const improvementShown = app.el("improvement-panel").hidden === false;
  assert.equal(improvementShown, true);
  assert.equal(app.el("copy-posting-button").disabled, false);
  const firstTemplateLine = endText.split("\n").map((line) => line.trim()).find(Boolean);
  const improvementMarkup = app.el("improvement-content").innerHTML;
  assert.ok(improvementMarkup.includes("바꾸거나 추가한 줄"));
  assert.ok(improvementMarkup.includes(escapeHtml(firstTemplateLine)));
  const remainingMarkup = app.el("improvement-remaining").innerHTML;
  assert.ok(remainingMarkup.includes(`채우지 않은 빈칸(${escapeHtml(placeholder)}) <strong>${blanks}곳</strong>`));
  assert.ok(remainingMarkup.includes("답하지 않은 질문"));
  // Posting lines may say anything; the program's own wording never judges.
  assert.ok(!/개선 완료|통과|점수|✓|✔/u.test(remainingMarkup + app.el("improvement-status").textContent));
  const memoAfterInsert = await app.copyMemo();
  if (blanks) assert.ok(memoAfterInsert.includes(`[채우지 않은 빈칸] ${placeholder} ${blanks}곳 (${blankLines})`));
  assert.ok(!memoAfterInsert.includes(" · 빈칸 남음"));
  assert.ok(memoAfterInsert.includes("[공고문 변경 내역]"));
  assert.ok(memoAfterInsert.includes(`+ ${firstTemplateLine}`));
  assert.ok(memoAfterInsert.includes("삭제한 줄 0"));
  await app.el("copy-posting-button").trigger("click");
  const postingCopy = { equalsPosting: app.clipboard.text === app.posting.value, toast: app.toast() };
  assert.equal(postingCopy.equalsPosting, true);
  if (blanks) {
    assert.equal(postingCopy.toast,
      `고친 공고문을 복사했습니다. 채우지 않은 빈칸(${placeholder})이 ${blanks}곳 남아 있습니다. 게시 전에 실제 내용으로 바꾸세요.`);
  }

  // A stale result hides the summary and blocks posting copy.
  const checkedText = app.posting.value;
  app.type(`${checkedText}\n${PRIVATE_MARK}`);
  const staleImprovement = {
    hidden: app.el("improvement-panel").hidden,
    copyDisabled: app.el("copy-posting-button").disabled,
    undoUsable: app.el("posting-undo").getAttribute("aria-disabled") === "false",
  };
  const clipboardBeforeStale = app.clipboard.text;
  await app.el("copy-posting-button").trigger("click");
  staleImprovement.copyUnchanged = app.clipboard.text === clipboardBeforeStale;
  assert.deepEqual(staleImprovement, { hidden: true, copyDisabled: true, undoUsable: false, copyUnchanged: true });
  app.type(checkedText);
  assert.equal(app.el("improvement-panel").hidden, false, "fresh again after typing back");

  // Undo restores the posting before the insert and then disappears.
  app.el("posting-undo").trigger("click");
  assert.equal(app.posting.value, BASE_POSTING);
  assert.equal(app.toast(), "되돌렸습니다. '검토 메모 만들기'를 다시 눌러 확인하세요.");
  assert.equal(app.el("posting-undo").hidden, true);
  app.check();
  assert.equal(app.el("improvement-panel").hidden, true, "no summary when posting equals baseline");

  // Insert at the remembered caret (start of a line): nothing is deleted.
  const cursorSlot = (withTemplate[1] || withTemplate[0]).slot;
  const cursorText = templateText(templates, cursorSlot);
  const caret = BASE_POSTING.indexOf("전형절차");
  app.posting.selectionStart = caret;
  app.posting.selectionEnd = caret;
  app.posting.trigger("select");
  app.insert(cursorSlot, "cursor");
  assert.equal(app.posting.value, `${BASE_POSTING.slice(0, caret)}${cursorText}\n${BASE_POSTING.slice(caret)}`);
  assert.equal(app.toast(), blankToast(placeholder, count(cursorText, placeholder)));
  app.el("posting-undo").trigger("click");
  assert.equal(app.posting.value, BASE_POSTING);
  assert.equal(app.toast(), "되돌렸습니다. 마지막으로 검토한 공고문과 같아 현재 결과를 그대로 볼 수 있습니다.");

  // Unknown caret: falls back to the end and says so.
  app.insert(cursorSlot, "cursor");
  assert.equal(app.posting.value, `${BASE_POSTING}\n\n${cursorText}`);
  assert.ok(app.toast().startsWith("커서 위치를 알 수 없어 공고 끝에 넣었습니다. "));

  // A reader edit after the insert disables undo and keeps the edit.
  const editedByReader = `${app.posting.value}\n담당자가 직접 덧붙인 줄`;
  app.type(editedByReader);
  const undo = app.el("posting-undo");
  const undoBlocked = {
    shown: undo.hidden === false,
    ariaDisabled: undo.getAttribute("aria-disabled"),
    title: undo.getAttribute("title"),
  };
  undo.trigger("click");
  undoBlocked.valueKept = app.posting.value === editedByReader;
  undoBlocked.toast = app.toast();
  assert.equal(undoBlocked.ariaDisabled, "true");
  assert.ok(undoBlocked.title.includes("직접 고쳐서 되돌릴 수 없습니다"));
  assert.equal(undoBlocked.valueKept, true);
  assert.ok(undoBlocked.toast.includes("직접 고쳐서 되돌리지 않습니다"));

  // Inserting is an edit, so it works on a stale result and becomes the one undo step.
  app.insert(endSlot, "end");
  assert.equal(app.posting.value, `${editedByReader}\n\n${endText}`);
  assert.equal(undo.getAttribute("aria-disabled"), "false");
  undo.trigger("click");
  assert.equal(app.posting.value, editedByReader);

  // 3. Line rewrite on a finding card.
  app.type(BASE_POSTING);
  app.check();
  await tick();
  const targetLine = "- 용모 단정한 20대 지원자 우대";
  easy = app.el("easy-result").innerHTML;
  assert.ok(easy.includes('id="rewrite-0"'));
  assert.ok(easy.includes(`>${escapeHtml(targetLine)}</textarea>`), "draft starts as the whole line");
  const draft = `- 직무 수행에 필요한 행정 경력 <b>3년</b> 이상 ${PRIVATE_MARK}`;
  app.draft(0, draft);
  app.check(); // same text: the draft survives the re-render
  easy = app.el("easy-result").innerHTML;
  assert.ok(easy.includes(escapeHtml(draft)));
  assert.ok(!easy.includes("<b>3년"));
  app.apply(0);
  const rewritten = BASE_POSTING.replace(targetLine, draft);
  assert.equal(app.posting.value, rewritten);
  assert.equal(app.toast(), "반영했습니다. '검토 메모 만들기'를 다시 눌러 확인하세요.");
  assert.equal(app.el("posting-undo").getAttribute("aria-disabled"), "false");
  app.apply(0);
  assert.equal(app.posting.value, rewritten, "stale apply never edits");
  assert.equal(app.toast(), "공고문이 바뀌었습니다. 다시 검토한 뒤 반영하세요.");
  app.check();
  await tick();
  const rewriteImprovement = app.el("improvement-content").innerHTML;
  assert.ok(rewriteImprovement.includes(escapeHtml(draft)));
  assert.ok(!rewriteImprovement.includes("<b>"));
  assert.ok(rewriteImprovement.includes(escapeHtml(targetLine)));
  const rewriteMemo = await app.copyMemo();
  assert.ok(rewriteMemo.includes(`+ ${draft}`));
  assert.ok(rewriteMemo.includes(`- ${targetLine}`));
  assert.ok(rewriteMemo.includes("바꾸거나 추가한 줄 1"));
  assert.ok(rewriteMemo.includes("삭제한 줄 1"));

  // A new review starts from the new line (old drafts are gone); markup is
  // escaped; apply needs a real change and a non-blank line.
  const xssLine = "- <img src=x onerror=alert(1)> 20대 우대";
  app.type(`행정직 채용\n${xssLine}\n문의는 인사팀`);
  app.check();
  easy = app.el("easy-result").innerHTML;
  assert.ok(easy.includes(`>${escapeHtml(xssLine)}</textarea>`));
  assert.ok(!easy.includes(escapeHtml(draft)));
  assert.ok(!easy.includes("<img"));
  const beforeNoop = app.posting.value;
  app.el("rewrite-0").value = xssLine; // what the browser shows in the field
  app.apply(0);
  assert.equal(app.posting.value, beforeNoop);
  assert.equal(app.toast(), "고쳐 쓴 내용이 지금 줄과 같습니다. 바꿀 내용을 직접 쓴 뒤 반영하세요.");
  app.draft(0, "   ");
  app.apply(0);
  assert.equal(app.posting.value, beforeNoop);
  assert.equal(app.toast(), "고쳐 쓸 내용을 입력하세요. 줄을 지우려면 공고문에서 직접 지우세요.");
  assertUniqueIds(app, "rewrite easy");
  app.el("mode-expert").trigger("click");
  assertUniqueIds(app, "rewrite expert");
  app.el("mode-easy").trigger("click");

  // Clearing resets every improvement surface.
  app.el("clear-button").trigger("click");
  const cleared = {
    undoHidden: app.el("posting-undo").hidden,
    improvementHidden: app.el("improvement-panel").hidden,
    noticeHidden: app.el("placeholder-notice").hidden,
    postingCopyDisabled: app.el("copy-posting-button").disabled,
  };
  assert.deepEqual(cleared, { undoHidden: true, improvementHidden: true, noticeHidden: true, postingCopyDisabled: true });
  app.el("sample-button").trigger("click");
  assert.equal(app.el("posting-undo").hidden, true);
  app.insert(endSlot, "cursor");
  assert.ok(app.toast().startsWith("커서 위치를 알 수 없어"), "sample fill is not a reader caret");
  await tick();
  await tick();

  // Privacy: no POST, no posting text or drafts in storage, no new keys.
  const storedValues = [...app.storage.values()].join("\n");
  summary.privacy = {
    posts: app.posts(),
    storageKeys: [...new Set(app.storageWrites.map(([key]) => key))].sort(),
    postingStored: ["용모 단정", PRIVATE_MARK, firstTemplateLine, "직무 수행에 필요한 행정 경력"]
      .some((fragment) => storedValues.includes(fragment)),
  };
  assert.equal(summary.privacy.posts, 0);
  assert.equal(summary.privacy.postingStored, false);
  summary.privacy.storageKeys.forEach((key) => assert.ok([VIEW_MODE_KEY, ROLE_REVIEW_KEY].includes(key), key));

  summary.templates = {
    missingWithTemplate: withTemplate.length,
    linkedQuestionCardsChecked: linkedChecked,
    linkedQuestionCardsWithButtons: linkedWithButtons,
    insertedSlot: endSlot,
    cursorSlot,
    endBlanks,
    blanksAfterInsert: blanks,
    blankLines: blanks ? blankLines : "",
    easyIdCount,
    expertIdCount,
    staleAfterInsert,
    staleImprovement,
    postingCopy,
    undoBlocked,
    cleared,
    memoHasChanges: memoAfterInsert.includes("[공고문 변경 내역]"),
    rewriteMemoHasChanges: rewriteMemo.includes("[공고문 변경 내역]"),
  };

  // 4. Partial-guidance sentences (components) with the contract fixture.
  const parts = boot({ templates: "fixture" });
  parts.type(BASE_POSTING);
  parts.check();
  const partsResult = parts.sandbox.FairpostEngine.check(BASE_POSTING);
  const resultNotice = partsResult.slots.find((slot) => slot.slot === "result_notice");
  assert.equal(resultNotice.found, true);
  assert.ok(!resultNotice.components_found.includes("notice_time"));
  const partsEasy = parts.el("easy-result").innerHTML;
  assert.ok(partsEasy.includes(insertMarker("end", "result_notice", "notice_time")));
  assert.ok(partsEasy.includes(insertMarker("cursor", "result_notice", "notice_audience")));
  assert.ok(!partsEasy.includes('data-template-component="hours"'), "only follow-up components");
  assert.ok(partsEasy.includes(escapeHtml(FIXTURE_TEMPLATES.slots.ai_disclosure.condition)));
  assert.ok(partsEasy.includes("&lt;script&gt;출처 확인&lt;/script&gt;"));
  const noticeTime = FIXTURE_TEMPLATES.slots.result_notice.components.notice_time;
  parts.insert("result_notice", "end", "notice_time");
  assert.equal(parts.posting.value, `${BASE_POSTING}\n\n${noticeTime}`);
  assert.equal(parts.toast(), blankToast("○○", count(noticeTime, "○○")));
  parts.el("posting-undo").trigger("click");
  const audience = FIXTURE_TEMPLATES.slots.result_notice.components.notice_audience;
  parts.insert("result_notice", "end", "notice_audience");
  assert.equal(parts.toast(), blankToast("○○", 0));
  assert.ok(parts.posting.value.endsWith(audience));
  summary.components = {
    noticeTimeButton: true,
    noticeAudienceButton: true,
    posts: parts.posts(),
  };
  assert.equal(summary.components.posts, 0);

  // 5. No template bundle (or a malformed one): no insert controls anywhere.
  const withoutTemplates = (templatesOption) => {
    const bare = boot({ templates: templatesOption });
    bare.type(`${BASE_POSTING}\n문의: ${"○○"}팀`);
    bare.check();
    const easyMarkup = bare.markup();
    bare.el("mode-expert").trigger("click");
    const expertMarkup = bare.markup();
    return {
      buttons: count(easyMarkup + expertMarkup, "data-template-insert"),
      rewriteOffered: easyMarkup.includes('data-rewrite-apply="0"'),
      blankNotice: bare.el("placeholder-notice").hidden === false,
      hintMentionsTemplates: easyMarkup.includes("'예시 문장 보기'"),
    };
  };
  const brokenSlots = clone(FIXTURE_TEMPLATES);
  brokenSlots.slots = [];
  const wrongSchema = { ...clone(FIXTURE_TEMPLATES), schema_version: "fairpost-posting-templates-v0" };
  const noPlaceholder = { ...clone(FIXTURE_TEMPLATES), placeholder: "" };
  const badTexts = clone(FIXTURE_TEMPLATES);
  Object.values(badTexts.slots).forEach((slot) => { slot.text = 42; });
  summary.noTemplates = {
    missing: withoutTemplates("none"),
    wrongSchema: withoutTemplates(wrongSchema),
    arraySlots: withoutTemplates(brokenSlots),
    noPlaceholder: withoutTemplates(noPlaceholder),
    nonStringText: withoutTemplates(badTexts),
    stringBundle: withoutTemplates("not an object"),
  };
  Object.entries(summary.noTemplates).forEach(([label, state]) => {
    assert.equal(state.buttons, 0, label);
    assert.equal(state.rewriteOffered, true, label);
    assert.equal(state.blankNotice, true, label);
    assert.equal(state.hintMentionsTemplates, false, label);
  });

  console.log(JSON.stringify(summary));
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
