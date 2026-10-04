(function () {
  "use strict";

  // Direct-identifier patterns are exported from core/direct_identifiers.py
  // into web/data.js; the browser keeps no copy of its own so role-review note
  // checks, the Python packet contract and AI-bound masking cannot drift.
  const bundle =
    (window.FAIRPOST_DATA && window.FAIRPOST_DATA.direct_identifiers) || null;
  let compiled = [];
  try {
    if (bundle && Array.isArray(bundle.patterns) && bundle.patterns.length) {
      const flags = typeof bundle.flags === "string" ? bundle.flags : "u";
      compiled = bundle.patterns.map((pattern) => ({
        kind: pattern.kind,
        label: pattern.label,
        mask: pattern.mask,
        test: new RegExp(pattern.source, flags),
        replace: new RegExp(pattern.source, `g${flags}`),
      }));
    }
  } catch (_error) {
    compiled = [];
  }

  function kinds(text) {
    if (typeof text !== "string" || !text) return [];
    return compiled
      .filter((pattern) => pattern.test.test(text))
      .map((pattern) => pattern.kind);
  }

  function mask(text) {
    let masked = String(text);
    compiled.forEach((pattern) => {
      masked = masked.replace(pattern.replace, () => pattern.mask);
    });
    return masked;
  }

  function labels(kindList) {
    return kindList.map(
      (kind) =>
        (compiled.find((pattern) => pattern.kind === kind) || { label: kind })
          .label
    );
  }

  window.FairpostDirectIdentifiers = Object.freeze({
    available: compiled.length > 0,
    kinds,
    contains: (text) => kinds(text).length > 0,
    mask,
    labels,
  });
})();

(function () {
  "use strict";

  const input = document.getElementById("posting-input");
  const checkButton = document.getElementById("check-button");
  const clearButton = document.getElementById("clear-button");
  const sampleButton = document.getElementById("sample-button");
  const copyButton = document.getElementById("copy-button");
  const comparisonPanel = document.getElementById("comparison-panel");
  const comparisonStatus = document.getElementById("comparison-status");
  const comparisonGroups = document.getElementById("comparison-groups");
  const comparisonReset = document.getElementById("comparison-reset");
  const assistedToggle = document.getElementById("assisted-review-toggle");
  const assistedStatus = document.getElementById("assisted-review-status");
  const assistedBadge = document.getElementById("assisted-review-badge");
  const assistedProvider = document.getElementById("assisted-review-provider");
  const assistedPanel = document.getElementById("assisted-review-panel");
  const assistedResultStatus = document.getElementById(
    "assisted-review-result-status"
  );
  const assistedNotice = document.getElementById("assisted-review-notice");
  const assistedOutput = document.getElementById("assisted-review-output");
  const assistedConsent = document.getElementById("assisted-review-consent");
  const assistedRun = document.getElementById("assisted-review-run");
  const assistedRunHint = document.getElementById("assisted-review-run-hint");
  const assistedLive = document.getElementById("assisted-review-live");
  const resultsNote = document.getElementById("results-note");
  const postingInputError = document.getElementById("posting-input-error");
  const assistedProviderLabels = {
    anthropic: "Claude",
    openai: "GPT",
    gemini: "Gemini",
    openai_compatible: "OpenAI 호환 API",
  };
  const roleReviewPanel = document.getElementById("role-review-panel");
  const roleReviewStatus = document.getElementById("role-review-status");
  const roleReviewNotice = document.getElementById("role-review-notice");
  const roleReviewRole = document.getElementById("role-review-role");
  const roleReviewStage = document.getElementById("role-review-stage");
  const roleReviewAction = document.getElementById("role-review-action");
  const roleReviewResolveLabel = document.getElementById(
    "role-review-resolve-label"
  );
  const roleReviewResolveEvent = document.getElementById(
    "role-review-resolve-event"
  );
  const roleReviewNote = document.getElementById("role-review-note");
  const roleReviewNoteError = document.getElementById("role-review-note-error");
  const roleReviewEvidence = document.getElementById("role-review-evidence");
  const roleReviewEvidenceError = document.getElementById(
    "role-review-evidence-error"
  );
  const roleReviewResolveError = document.getElementById(
    "role-review-resolve-error"
  );
  const roleReviewRecord = document.getElementById("role-review-record");
  const roleReviewClear = document.getElementById("role-review-clear");
  const roleReviewProgress = document.getElementById("role-review-progress");
  const roleReviewMissing = document.getElementById("role-review-missing");
  const roleReviewEvents = document.getElementById("role-review-events");
  const privacyMessage = document.getElementById("privacy-message");
  const organizationSector = document.getElementById("organization-sector");
  const organizationPublicType = document.getElementById(
    "organization-public-type"
  );
  const organizationSize = document.getElementById("organization-size");
  const charCount = document.getElementById("char-count");
  const emptyState = document.getElementById("empty-state");
  const resultContent = document.getElementById("result-content");
  const resultsTitle = document.getElementById("results-title");
  const toast = document.getElementById("toast");
  const appBody = document.getElementById("app-body");
  const modeEasyButton = document.getElementById("mode-easy");
  const modeExpertButton = document.getElementById("mode-expert");
  const easyResult = document.getElementById("easy-result");
  const VIEW_MODE_STORAGE_KEY = "fairpost.view-mode.v1";
  const VIEW_MODES = new Set(["easy", "expert"]);
  const SLOT_EMBEDDED_QUESTION_ALLOWLIST = new Set(
    ["Q-INFO-001", "Q-INFO-004", "Q-PROC-002"]
  );
  const SLOT_QUESTION_IDS = Object.freeze(
    Object.fromEntries(
      window.FAIRPOST_DATA.rules
        .filter(
          (rule) =>
            SLOT_EMBEDDED_QUESTION_ALLOWLIST.has(rule.id) &&
            rule.layer === "question" &&
            rule.trigger &&
            rule.trigger.type === "absence" &&
            typeof rule.trigger.field === "string"
        )
        .map((rule) => [rule.trigger.field, rule.id])
    )
  );
  const SLOT_EMBEDDED_QUESTION_IDS = new Set(
    Object.values(SLOT_QUESTION_IDS)
  );
  const PUBLIC_GUIDANCE_QUESTION_IDS = new Set([
    "Q-DIST-014",
    "Q-INFO-011",
    "Q-INFO-012",
  ]);
  const ORGANIZATION_GUIDANCE = window.FAIRPOST_DATA.organization_guidance || {
    profiles: [],
    sources: [],
  };
  const ORGANIZATION_PROFILES = new Map(
    ORGANIZATION_GUIDANCE.profiles.map((profile) => [profile.id, profile])
  );
  const ORGANIZATION_SOURCES = new Map(
    ORGANIZATION_GUIDANCE.sources.map((source) => [source.id, source])
  );
  const AX_QUESTION_IDS = new Set(
    window.FAIRPOST_DATA.rules
      .filter(
        (rule) =>
          rule.layer === "question" &&
          rule.trigger &&
          Array.isArray(rule.trigger.patterns) &&
          rule.trigger.patterns.some((pattern) =>
            /AI|인공지능|자동화|알고리즘/i.test(pattern)
          )
      )
      .map((rule) => rule.id)
  );
  // Role-review packets live under one versioned key, one entry per posting
  // fingerprint + ruleset version + guidance catalog version. Older packets
  // are kept (bounded) instead of being overwritten by the next review.
  const ROLE_REVIEW_STORE_KEY = "fairpost.role-review.v2";
  const ROLE_REVIEW_LEGACY_KEY = "fairpost.role-review.v1";
  const ROLE_REVIEW_STORE_SCHEMA_VERSION = "fairpost-browser-role-review-store-v2";
  const ROLE_REVIEW_MAX_PACKETS = 20;
  const ROLE_REVIEW_MAX_EVENTS = 256;
  const ROLE_REVIEW_SYSTEM_ACTOR = "system-chair";
  const ROLE_REVIEW_SYSTEM_NOTE =
    "위원장 조정 흐름이 생성되었습니다. 각 역할의 독립 검토를 추가하십시오.";
  const ROLE_REVIEW_SELF_REPORT = "이 브라우저의 자기 기록 · 결재 아님";
  const ROLE_REVIEW_PRIVACY_LINE =
    "fingerprint와 역할 이벤트만 이 브라우저에 저장합니다. 원문·지원자 정보는 저장하지 않습니다.";
  const GUIDANCE_CATALOG_VERSION =
    typeof window.FAIRPOST_DATA.guidance_catalog_version === "string" &&
    window.FAIRPOST_DATA.guidance_catalog_version
      ? window.FAIRPOST_DATA.guidance_catalog_version
      : "guidance-unknown";
  const ROLE_LABELS = Object.freeze({
    chair: "위원장",
    hr_owner: "인사 운영책임자",
    job_sme: "직무전문가",
    interviewer: "면접위원",
    policy_reviewer: "정책검토자",
    auditor: "감사자",
    candidate_advocate: "지원자 대변인",
  });
  const STAGE_LABELS = Object.freeze({
    analysis: "분석",
    design: "설계",
    development: "개발",
    implementation: "운영",
    evaluation: "평가",
  });
  const ACTION_LABELS = Object.freeze({
    note: "메모",
    confirm: "확인",
    edit_requested: "수정 요청",
    escalate: "위원장에게 이관",
    resolve: "해결 기록",
  });
  const ROLE_EVENT_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
  const FINGERPRINT_PATTERN = /^[0-9a-f]{64}$/;
  const DIRECT_IDENTIFIERS = window.FairpostDirectIdentifiers;
  const ROLE_REVIEW_STAGES = new Set(Object.keys(STAGE_LABELS));
  const ROLE_REVIEW_ACTIONS = new Set(Object.keys(ACTION_LABELS));
  const ROLE_REVIEW_SCHEMA_VERSION = "fairpost-browser-role-review-v1";
  let latestResult = null;
  let latestCheckedText = null;
  // Session memory only: never persist a posting or its comparison snapshot.
  let comparisonBaseline = null;
  let comparison = null;
  let latestAssistedReview = null;
  let assistedRequestSequence = 0;
  let assistedAvailable = false;
  let assistedInFlight = false;
  let assistedResultSignature = null;
  let assistedResultView = null;
  let assistedResultViewCurrent = false;
  let roleReviewSequence = 0;
  let roleReviewState = null;
  let roleReviewPersistent = false;
  const reviewAnswers = new Map();
  let toastTimer = null;

  const sample = `2026년 행정직 채용

자격요건
- 관련 행정 업무 경력 2년 이상
- 용모 단정한 20대 지원자 우대

전형절차
- 서류전형: 직무경력과 자기소개서 검토
- 면접전형: 의사소통과 문제해결 사례 검토
- 우대사항은 서류전형 가점으로 반영

전형일정
- 접수 기간: 2026. 8. 1. ~ 8. 12.
- 면접 일정: 2026. 8. 20.
- 결과는 이메일로 개별 통보 예정

근무조건
- 연봉 4,000만원

문의처
- 인사팀 02-1234-5678 / recruit@example.com
- 평일 09:00~18:00`;

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function setFieldError(field, errorElement, message) {
    if (!field || !errorElement) return;
    if (message) {
      errorElement.textContent = message;
      errorElement.hidden = false;
      field.setAttribute("aria-invalid", "true");
      return;
    }
    errorElement.textContent = "";
    errorElement.hidden = true;
    field.removeAttribute("aria-invalid");
  }

  function roleReviewStorage() {
    try {
      return window.localStorage || null;
    } catch (_error) {
      return null;
    }
  }

  function isSystemRoleReviewEvent(event) {
    return Boolean(event) && event.actor_ref === ROLE_REVIEW_SYSTEM_ACTOR;
  }

  function roleReviewUserEvents(packet = roleReviewState) {
    return packet
      ? packet.events.filter((event) => !isSystemRoleReviewEvent(event))
      : [];
  }

  function roleReviewPacketKey(packet) {
    return [
      packet.posting_fingerprint,
      packet.ruleset_version,
      packet.guidance_catalog_version,
    ].join("|");
  }

  function validRoleReviewEvent(event, eventIds) {
    if (!event || typeof event !== "object") return false;
    if (
      typeof event.event_id !== "string" ||
      !ROLE_EVENT_ID_PATTERN.test(event.event_id) ||
      eventIds.has(event.event_id) ||
      !ROLE_REVIEW_STAGES.has(event.stage) ||
      !Object.prototype.hasOwnProperty.call(ROLE_LABELS, event.role) ||
      !ROLE_REVIEW_ACTIONS.has(event.action) ||
      typeof event.occurred_at !== "string" ||
      !event.occurred_at.trim() ||
      event.occurred_at.length > 128 ||
      typeof event.note !== "string" ||
      event.note.length > 4000 ||
      (event.resolves_event_id !== null &&
        event.resolves_event_id !== undefined &&
        (typeof event.resolves_event_id !== "string" ||
          !ROLE_EVENT_ID_PATTERN.test(event.resolves_event_id) ||
          event.action !== "resolve")) ||
      (event.actor_ref !== null &&
        event.actor_ref !== undefined &&
        (typeof event.actor_ref !== "string" ||
          !ROLE_EVENT_ID_PATTERN.test(event.actor_ref))) ||
      DIRECT_IDENTIFIERS.contains(event.note) ||
      !Array.isArray(event.evidence_refs) ||
      event.evidence_refs.length > 32
    ) {
      return false;
    }
    if (
      event.evidence_refs.some(
        (item) =>
          typeof item !== "string" || !ROLE_EVENT_ID_PATTERN.test(item)
      )
    ) {
      return false;
    }
    eventIds.add(event.event_id);
    return true;
  }

  // Keeps every valid event and reports how many stored events were dropped,
  // instead of discarding the whole packet because one event is malformed.
  function sanitizeRoleReviewEvents(rawEvents) {
    const eventIds = new Set();
    const candidates = [];
    let dropped = 0;
    rawEvents.forEach((rawEvent, index) => {
      if (index >= ROLE_REVIEW_MAX_EVENTS || !validRoleReviewEvent(rawEvent, eventIds)) {
        dropped += 1;
        return;
      }
      const event = { ...rawEvent };
      if (
        !event.actor_ref &&
        event.role === "chair" &&
        event.action === "note" &&
        event.note === ROLE_REVIEW_SYSTEM_NOTE
      ) {
        // v1 packets created this chair note automatically without marking it.
        event.actor_ref = ROLE_REVIEW_SYSTEM_ACTOR;
      }
      candidates.push(event);
    });
    const eventsById = new Map(candidates.map((event) => [event.event_id, event]));
    const events = candidates.filter((event) => {
      if (event.resolves_event_id == null) return event.action !== "resolve";
      const target = eventsById.get(event.resolves_event_id);
      return Boolean(target) && ["edit_requested", "escalate"].includes(target.action);
    });
    return { events, dropped: dropped + candidates.length - events.length };
  }

  function sanitizeRoleReviewPacket(raw) {
    if (
      !raw ||
      typeof raw !== "object" ||
      raw.schema_version !== ROLE_REVIEW_SCHEMA_VERSION ||
      typeof raw.packet_id !== "string" ||
      !ROLE_EVENT_ID_PATTERN.test(raw.packet_id) ||
      typeof raw.posting_fingerprint !== "string" ||
      !FINGERPRINT_PATTERN.test(raw.posting_fingerprint) ||
      typeof raw.ruleset_version !== "string" ||
      raw.ruleset_version.length > 128 ||
      typeof raw.guidance_catalog_version !== "string" ||
      raw.guidance_catalog_version.length > 128 ||
      !Array.isArray(raw.events)
    ) {
      return null;
    }
    const { events, dropped } = sanitizeRoleReviewEvents(raw.events);
    const lastEvent = events[events.length - 1];
    const timestamp = (value, fallback) =>
      typeof value === "string" && value.length <= 128 ? value : fallback;
    return {
      packet: {
        schema_version: ROLE_REVIEW_SCHEMA_VERSION,
        packet_id: raw.packet_id,
        posting_fingerprint: raw.posting_fingerprint,
        ruleset_version: raw.ruleset_version,
        guidance_catalog_version: raw.guidance_catalog_version,
        created_at: timestamp(raw.created_at, events[0] ? events[0].occurred_at : ""),
        updated_at: timestamp(raw.updated_at, lastEvent ? lastEvent.occurred_at : ""),
        events,
      },
      dropped,
    };
  }

  // Reads the packet store fresh from localStorage. A store that cannot be
  // parsed is never overwritten: the page falls back to session-only records.
  function readRoleReviewStore() {
    const store = {
      writable: false,
      problem: "",
      packets: [],
      unreadable: [],
      dropped: new Map(),
      legacyPending: false,
      legacyUnreadable: false,
    };
    const storage = roleReviewStorage();
    if (!storage) {
      store.problem = "unavailable";
      return store;
    }
    let raw;
    let legacyRaw;
    try {
      raw = storage.getItem(ROLE_REVIEW_STORE_KEY);
      legacyRaw = storage.getItem(ROLE_REVIEW_LEGACY_KEY);
    } catch (_error) {
      store.problem = "unavailable";
      return store;
    }
    const addPacket = (sanitized) => {
      const key = roleReviewPacketKey(sanitized.packet);
      if (store.packets.some((packet) => roleReviewPacketKey(packet) === key)) {
        return false;
      }
      store.packets.push(sanitized.packet);
      if (sanitized.dropped) store.dropped.set(key, sanitized.dropped);
      return true;
    };
    if (raw) {
      let parsed = null;
      try {
        parsed = JSON.parse(raw);
      } catch (_error) {
        parsed = null;
      }
      if (
        !parsed ||
        parsed.schema_version !== ROLE_REVIEW_STORE_SCHEMA_VERSION ||
        !Array.isArray(parsed.packets)
      ) {
        store.problem = "corrupt";
        return store;
      }
      parsed.packets.forEach((item) => {
        const sanitized = sanitizeRoleReviewPacket(item);
        if (!sanitized || !addPacket(sanitized)) store.unreadable.push(item);
      });
    }
    if (legacyRaw) {
      let legacy = null;
      try {
        legacy = JSON.parse(legacyRaw);
      } catch (_error) {
        legacy = null;
      }
      const sanitized = sanitizeRoleReviewPacket(legacy);
      if (sanitized) {
        addPacket(sanitized);
        // The v1 key is removed only after the v2 store is written.
        store.legacyPending = true;
      } else {
        store.legacyUnreadable = true;
      }
    }
    store.writable = true;
    return store;
  }

  function quotaExceeded(error) {
    return Boolean(
      error &&
        (error.name === "QuotaExceededError" ||
          error.name === "NS_ERROR_DOM_QUOTA_REACHED" ||
          error.code === 22 ||
          error.code === 1014)
    );
  }

  function evictOldestRoleReviewPacket(store, protectedKey, evicted) {
    if (store.unreadable.length) {
      store.unreadable.shift();
      evicted.push(null);
      return true;
    }
    const candidates = store.packets
      .filter((packet) => roleReviewPacketKey(packet) !== protectedKey)
      .sort((left, right) =>
        String(left.updated_at).localeCompare(String(right.updated_at))
      );
    if (!candidates.length) return false;
    store.packets = store.packets.filter((packet) => packet !== candidates[0]);
    evicted.push(candidates[0]);
    return true;
  }

  // Writes the store; `mutate` edits the freshly read store first so packets
  // saved by another tab are merged instead of overwritten.
  function writeRoleReviewStore(mutate, protectedKey) {
    const outcome = { ok: false, problem: "", evicted: [], storedCount: 0, store: null };
    const store = readRoleReviewStore();
    outcome.store = store;
    if (!store.writable) {
      outcome.problem = store.problem;
      return outcome;
    }
    mutate(store);
    const snapshot = { packets: store.packets.slice(), unreadable: store.unreadable.slice() };
    while (store.packets.length + store.unreadable.length > ROLE_REVIEW_MAX_PACKETS) {
      if (!evictOldestRoleReviewPacket(store, protectedKey, outcome.evicted)) break;
    }
    const storage = roleReviewStorage();
    for (let attempt = 0; storage && attempt <= ROLE_REVIEW_MAX_PACKETS; attempt += 1) {
      const ordered = store.packets
        .slice()
        .sort((left, right) =>
          String(right.updated_at).localeCompare(String(left.updated_at))
        );
      try {
        storage.setItem(
          ROLE_REVIEW_STORE_KEY,
          JSON.stringify({
            schema_version: ROLE_REVIEW_STORE_SCHEMA_VERSION,
            packets: [...ordered, ...store.unreadable],
          })
        );
        outcome.ok = true;
        break;
      } catch (error) {
        if (
          !quotaExceeded(error) ||
          !evictOldestRoleReviewPacket(store, protectedKey, outcome.evicted)
        ) {
          break;
        }
      }
    }
    if (!outcome.ok) {
      store.packets = snapshot.packets;
      store.unreadable = snapshot.unreadable;
      outcome.evicted = [];
      outcome.problem = "write_failed";
      return outcome;
    }
    if (store.legacyPending) {
      try {
        storage.removeItem(ROLE_REVIEW_LEGACY_KEY);
        store.legacyPending = false;
      } catch (_error) {
        // The v1 copy stays; it is merged again (not duplicated) next time.
      }
    }
    outcome.storedCount = store.packets.length + store.unreadable.length;
    return outcome;
  }

  function mergeRoleReviewPacket(store, packet) {
    const key = roleReviewPacketKey(packet);
    const existing = store.packets.find(
      (candidate) => roleReviewPacketKey(candidate) === key
    );
    if (existing) {
      const knownIds = new Set(packet.events.map((event) => event.event_id));
      const additions = existing.events.filter((event) => !knownIds.has(event.event_id));
      if (additions.length) {
        const merged = sanitizeRoleReviewEvents(
          [...packet.events, ...additions]
            .slice()
            .sort((left, right) =>
              String(left.occurred_at).localeCompare(String(right.occurred_at))
            )
        );
        packet.events = merged.events;
      }
      store.packets = store.packets.filter((candidate) => candidate !== existing);
    }
    store.packets.push(packet);
  }

  function roleReviewTimeLabel(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value || "");
    const pad = (number) => String(number).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
      date.getDate()
    )} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function roleReviewEvictionMessage(evicted) {
    if (!evicted.length) return "";
    const packets = evicted.filter(Boolean);
    const unreadable = evicted.length - packets.length;
    const parts = [];
    if (packets.length) {
      const newest = packets
        .map((packet) => packet.updated_at)
        .sort()
        .slice(-1)[0];
      parts.push(
        `가장 오래된 역할 기록 패킷 ${packets.length}개(마지막 기록 ${roleReviewTimeLabel(
          newest
        )}${packets.length > 1 ? " 이하" : ""})`
      );
    }
    if (unreadable) parts.push(`읽을 수 없던 패킷 ${unreadable}개`);
    return `보관 한도 ${ROLE_REVIEW_MAX_PACKETS}개 또는 저장 공간을 넘어 ${parts.join(
      "와 "
    )}를 이 브라우저에서 삭제했습니다.`;
  }

  function roleReviewStorageMessage(outcome) {
    if (outcome.ok) {
      const messages = [];
      const eviction = roleReviewEvictionMessage(outcome.evicted);
      if (eviction) messages.push(eviction);
      messages.push(
        `${ROLE_REVIEW_PRIVACY_LINE} 이 브라우저에 패킷 ${outcome.storedCount}/${ROLE_REVIEW_MAX_PACKETS}개 보관 중입니다.`
      );
      if (outcome.store && outcome.store.unreadable.length) {
        messages.push(
          `형식을 읽을 수 없는 패킷 ${outcome.store.unreadable.length}개는 지우지 않고 그대로 두었습니다.`
        );
      }
      if (outcome.store && outcome.store.legacyUnreadable) {
        messages.push("이전 형식(v1) 역할 기록은 읽을 수 없어 그대로 두었습니다.");
      }
      return messages.join(" ");
    }
    if (outcome.problem === "corrupt") {
      return "브라우저에 저장된 역할 기록을 읽을 수 없어 덮어쓰지 않고 현재 세션에만 기록합니다. 페이지를 닫으면 이번 기록은 사라집니다.";
    }
    if (outcome.problem === "write_failed") {
      return "브라우저 저장소에 쓰지 못해 현재 세션에만 기록합니다. 페이지를 닫으면 이번 기록은 사라집니다.";
    }
    return "브라우저 저장소를 사용할 수 없어 현재 세션에만 기록합니다. 페이지를 닫으면 이번 기록은 사라집니다.";
  }

  function persistRoleReviewState(leadMessages = []) {
    if (!roleReviewState) return null;
    const key = roleReviewPacketKey(roleReviewState);
    const outcome = writeRoleReviewStore(
      (store) => mergeRoleReviewPacket(store, roleReviewState),
      key
    );
    roleReviewPersistent = outcome.ok;
    roleReviewNotice.textContent = [...leadMessages, roleReviewStorageMessage(outcome)]
      .filter(Boolean)
      .join(" ");
    setAssistBadge(
      roleReviewStatus,
      outcome.ok ? "로컬 기록" : "세션 기록",
      outcome.ok ? "active" : "error"
    );
    if (outcome.evicted.length) showToast(roleReviewEvictionMessage(outcome.evicted));
    return outcome;
  }

  async function sha256Hex(value) {
    if (
      !window.crypto ||
      !window.crypto.subtle ||
      typeof TextEncoder === "undefined"
    ) {
      return null;
    }
    const bytes = new TextEncoder().encode(value);
    const digest = await window.crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest), (item) =>
      item.toString(16).padStart(2, "0")
    ).join("");
  }

  function roleReviewId(prefix) {
    return `${prefix}-${Date.now().toString(36)}-${Math.random()
      .toString(36)
      .slice(2, 10)}`;
  }

  function roleReviewIssueStatuses() {
    if (!roleReviewState) return [];
    const resolved = new Set(
      roleReviewState.events
        .filter(
          (event) => event.action === "resolve" && event.resolves_event_id
        )
        .map((event) => event.resolves_event_id)
    );
    return roleReviewState.events
      .filter((event) => ["edit_requested", "escalate"].includes(event.action))
      .map((event) => ({
        event_id: event.event_id,
        action: event.action,
        resolved: resolved.has(event.event_id),
      }));
  }

  function roleReviewEventNumber(eventId) {
    if (!roleReviewState) return 0;
    return roleReviewState.events.findIndex((event) => event.event_id === eventId) + 1;
  }

  function roleReviewIssueLabel(event) {
    const note = event.note.length > 40 ? `${event.note.slice(0, 40)}…` : event.note;
    return `${roleReviewEventNumber(event.event_id)}번 · ${
      ROLE_LABELS[event.role] || event.role
    } · ${ACTION_LABELS[event.action] || event.action} · ${roleReviewTimeLabel(
      event.occurred_at
    )} — ${note || "메모 없음"}`;
  }

  function roleReviewProgressSummary() {
    const userEvents = roleReviewUserEvents();
    const roles = new Set(userEvents.map((event) => event.role));
    const openIssues = roleReviewIssueStatuses().filter((issue) => !issue.resolved);
    return {
      roles,
      userEvents,
      openIssues,
      text: `자기 기록 역할 ${roles.size}/7 · 이벤트 ${userEvents.length}개 · 미해결 이슈 ${openIssues.length}건 (${ROLE_REVIEW_SELF_REPORT})`,
    };
  }

  function updateRoleReviewResolutionControl() {
    const resolving = roleReviewAction.value === "resolve";
    roleReviewResolveLabel.hidden = !resolving;
    roleReviewResolveEvent.disabled = !resolving;
    if (!resolving) {
      roleReviewResolveEvent.value = "";
      setFieldError(roleReviewResolveEvent, roleReviewResolveError, "");
    }
  }

  function renderRoleReview() {
    if (!roleReviewState) return;
    const summary = roleReviewProgressSummary();
    const missingRoles = Object.keys(ROLE_LABELS).filter(
      (role) => !summary.roles.has(role)
    );
    roleReviewProgress.textContent = summary.text;
    roleReviewMissing.textContent = missingRoles.length
      ? `아직 기록이 없는 역할: ${missingRoles
          .map((role) => ROLE_LABELS[role])
          .join(", ")}`
      : "7개 역할 모두 이 브라우저에 자기 기록을 남겼습니다. 본인 확인·위원회 승인·결재를 뜻하지 않습니다.";
    const issues = roleReviewIssueStatuses();
    const issueById = new Map(issues.map((issue) => [issue.event_id, issue]));
    const eventsById = new Map(
      roleReviewState.events.map((event) => [event.event_id, event])
    );
    roleReviewResolveEvent.innerHTML =
      '<option value="">해결할 수정 요청 또는 이관을 선택하세요.</option>' +
      summary.openIssues
        .map(
          (issue) =>
            `<option value="${escapeHtml(issue.event_id)}">${escapeHtml(
              roleReviewIssueLabel(eventsById.get(issue.event_id))
            )}</option>`
        )
        .join("");
    updateRoleReviewResolutionControl();
    roleReviewEvents.innerHTML = roleReviewState.events
      .map((event) => {
        const evidence = Array.isArray(event.evidence_refs)
          ? event.evidence_refs.filter(Boolean).join(", ")
          : "";
        if (isSystemRoleReviewEvent(event)) {
          return `<li class="role-review-system-event"><strong>시스템 · ${escapeHtml(
            STAGE_LABELS[event.stage] || event.stage
          )} · 검토 흐름 생성 (참여로 세지 않음)</strong><br>${escapeHtml(
            event.note || ""
          )}<small>${escapeHtml(roleReviewTimeLabel(event.occurred_at))}${
            evidence ? ` · 근거 ${escapeHtml(evidence)}` : ""
          }</small></li>`;
        }
        const issue = issueById.get(event.event_id);
        const issueLabel = issue
          ? ` · ${issue.resolved ? "해결됨" : "미해결"}`
          : event.action === "resolve" && event.resolves_event_id
          ? ` · ${roleReviewEventNumber(event.resolves_event_id)}번 이슈 해결`
          : "";
        return `<li><strong>${escapeHtml(
          ROLE_LABELS[event.role] || event.role
        )} · ${escapeHtml(STAGE_LABELS[event.stage] || event.stage)} · ${escapeHtml(
          ACTION_LABELS[event.action] || event.action
        )}${escapeHtml(issueLabel)}</strong><br>${escapeHtml(event.note || "메모 없음")}<small>${escapeHtml(
          roleReviewTimeLabel(event.occurred_at)
        )} · 자기 기록${evidence ? ` · 근거 ${escapeHtml(evidence)}` : ""}</small></li>`;
      })
      .join("");
  }

  async function initializeRoleReview(result, text) {
    const sequence = ++roleReviewSequence;
    roleReviewState = null;
    roleReviewPanel.hidden = false;
    roleReviewNotice.textContent =
      "공고 fingerprint를 만드는 중입니다. 원문·지원자 정보는 역할 기록에 저장하지 않습니다.";
    setAssistBadge(roleReviewStatus, "준비 중");
    const fingerprint = await sha256Hex(text);
    if (
      sequence !== roleReviewSequence ||
      text !== input.value ||
      !text.trim()
    ) {
      return;
    }
    if (!fingerprint) {
      roleReviewNotice.textContent =
        "브라우저 암호화 API를 사용할 수 없어 역할 기록을 시작하지 못했습니다.";
      setAssistBadge(roleReviewStatus, "사용 불가", "error");
      return;
    }
    const store = readRoleReviewStore();
    const key = [fingerprint, result.ruleset_version, GUIDANCE_CATALOG_VERSION].join("|");
    const existing = store.packets.find(
      (packet) => roleReviewPacketKey(packet) === key
    );
    const lead = [];
    if (existing) {
      roleReviewState = existing;
      const userEvents = roleReviewUserEvents(existing).length;
      lead.push(`같은 공고문·규칙셋·지침 버전의 역할 기록(이벤트 ${userEvents}개)을 불러왔습니다.`);
      const dropped = store.dropped.get(key) || 0;
      if (dropped) {
        lead.push(
          `저장된 이벤트 ${dropped}개는 형식 검사(직접 식별정보 포함 여부 등)를 통과하지 못해 제외했습니다. 나머지 이벤트는 그대로 유지합니다.`
        );
      }
    } else {
      const now = new Date().toISOString();
      roleReviewState = {
        schema_version: ROLE_REVIEW_SCHEMA_VERSION,
        packet_id: roleReviewId("browser-packet"),
        posting_fingerprint: fingerprint,
        ruleset_version: result.ruleset_version,
        guidance_catalog_version: GUIDANCE_CATALOG_VERSION,
        created_at: now,
        updated_at: now,
        events: [
          {
            event_id: roleReviewId("browser-event"),
            stage: "analysis",
            role: "chair",
            action: "note",
            occurred_at: now,
            note: ROLE_REVIEW_SYSTEM_NOTE,
            evidence_refs: [
              ...new Set([
                ...result.findings.map((finding) => finding.id),
                ...result.questions.map((question) => question.id),
              ]),
            ].slice(0, 32),
            actor_ref: ROLE_REVIEW_SYSTEM_ACTOR,
            resolves_event_id: null,
          },
        ],
      };
      const olderVersions = store.packets.filter(
        (packet) => packet.posting_fingerprint === fingerprint
      ).length;
      lead.push(
        olderVersions
          ? `규칙셋 또는 지침 버전이 바뀌어 새 패킷을 시작했습니다. 같은 공고문의 이전 버전 패킷 ${olderVersions}개는 이 브라우저에 그대로 보관합니다.`
          : "이 공고문의 새 역할 기록 패킷을 시작했습니다. 원문이 한 글자라도 다르면 별도 패킷으로 보관하며 이전 패킷은 지우지 않습니다."
      );
    }
    if (!existing || store.legacyPending) {
      persistRoleReviewState(lead);
    } else {
      roleReviewPersistent = store.writable;
      roleReviewNotice.textContent = [
        ...lead,
        roleReviewStorageMessage({
          ok: store.writable,
          problem: store.problem,
          evicted: [],
          storedCount: store.packets.length + store.unreadable.length,
          store,
        }),
      ].join(" ");
      setAssistBadge(
        roleReviewStatus,
        store.writable ? "로컬 기록" : "세션 기록",
        store.writable ? "active" : "error"
      );
    }
    renderRoleReview();
  }

  function recordRoleReviewEvent() {
    setFieldError(roleReviewNote, roleReviewNoteError, "");
    setFieldError(roleReviewEvidence, roleReviewEvidenceError, "");
    setFieldError(roleReviewResolveEvent, roleReviewResolveError, "");
    if (!roleReviewState) {
      showToast("먼저 공고를 검토해 역할 큐를 준비하세요.");
      return;
    }
    if (roleReviewState.events.length >= ROLE_REVIEW_MAX_EVENTS) {
      showToast("역할 이벤트는 256개까지 기록할 수 있습니다.");
      return;
    }
    const failField = (field, errorElement, message) => {
      setFieldError(field, errorElement, message);
      showToast(message);
      field.focus();
    };
    const note = roleReviewNote.value.trim();
    if (!note) {
      failField(roleReviewNote, roleReviewNoteError, "검토 메모를 입력하세요.");
      return;
    }
    if (note.length > 4000) {
      failField(
        roleReviewNote,
        roleReviewNoteError,
        "검토 메모는 4,000자 이내로 입력하세요."
      );
      return;
    }
    if (!DIRECT_IDENTIFIERS.available) {
      failField(
        roleReviewNote,
        roleReviewNoteError,
        "직접 식별정보 검사 규칙을 불러오지 못해 메모를 기록하지 않았습니다. 페이지를 새로 고치세요."
      );
      return;
    }
    const identifierKinds = DIRECT_IDENTIFIERS.kinds(note);
    if (identifierKinds.length) {
      failField(
        roleReviewNote,
        roleReviewNoteError,
        `메모에 ${DIRECT_IDENTIFIERS.labels(identifierKinds).join(
          "·"
        )} 형식이 있어 기록하지 않았습니다. 원문이나 직접 식별정보 대신 요약과 근거 ID를 사용하세요.`
      );
      return;
    }
    const resolvesEventId =
      roleReviewAction.value === "resolve"
        ? roleReviewResolveEvent.value || null
        : null;
    if (roleReviewAction.value === "resolve" && !resolvesEventId) {
      failField(
        roleReviewResolveEvent,
        roleReviewResolveError,
        "해결할 수정 요청 또는 이관을 선택하세요."
      );
      return;
    }
    const evidenceRefs = roleReviewEvidence.value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    if (
      evidenceRefs.some(
        (item) =>
          item.length > 128 || !ROLE_EVENT_ID_PATTERN.test(item)
      )
    ) {
      failField(
        roleReviewEvidence,
        roleReviewEvidenceError,
        "근거 ID는 영문·숫자로 시작하고 영문·숫자와 . _ : -만 사용할 수 있습니다."
      );
      return;
    }
    if (new Set(evidenceRefs).size !== evidenceRefs.length) {
      failField(
        roleReviewEvidence,
        roleReviewEvidenceError,
        "근거 ID는 중복해서 입력할 수 없습니다."
      );
      return;
    }
    if (evidenceRefs.length > 32) {
      failField(
        roleReviewEvidence,
        roleReviewEvidenceError,
        "근거 ID는 최대 32개까지 입력할 수 있습니다."
      );
      return;
    }
    const now = new Date().toISOString();
    roleReviewState.events.push({
      event_id: roleReviewId("browser-event"),
      stage: roleReviewStage.value,
      role: roleReviewRole.value,
      action: roleReviewAction.value,
      occurred_at: now,
      note: note.slice(0, 4000),
      evidence_refs: evidenceRefs.slice(0, 32),
      actor_ref: null,
      resolves_event_id: resolvesEventId,
    });
    roleReviewState.updated_at = now;
    persistRoleReviewState();
    roleReviewNote.value = "";
    roleReviewEvidence.value = "";
    renderRoleReview();
    showToast("역할 검토 이벤트를 기록했습니다(자기 기록).");
  }

  function clearRoleReview() {
    if (!roleReviewState) return;
    const count = roleReviewUserEvents().length;
    let confirmed = false;
    try {
      confirmed =
        typeof window.confirm === "function" &&
        window.confirm(
          `이 공고의 역할 기록(이벤트 ${count}개)을 이 브라우저에서 삭제할까요? 되돌릴 수 없습니다. 다른 공고의 패킷은 유지됩니다.`
        ) === true;
    } catch (_error) {
      confirmed = false;
    }
    if (!confirmed) {
      roleReviewNotice.textContent =
        "삭제를 취소했습니다. 이 공고의 역할 기록은 그대로 있습니다.";
      return;
    }
    const key = roleReviewPacketKey(roleReviewState);
    let removedFromStorage = false;
    let storedCount = 0;
    if (roleReviewPersistent) {
      const outcome = writeRoleReviewStore((store) => {
        store.packets = store.packets.filter(
          (packet) => roleReviewPacketKey(packet) !== key
        );
      }, null);
      removedFromStorage = outcome.ok;
      storedCount = outcome.storedCount;
    }
    roleReviewSequence += 1;
    roleReviewState = null;
    roleReviewPersistent = false;
    roleReviewPanel.hidden = true;
    roleReviewEvents.replaceChildren();
    roleReviewMissing.textContent = "";
    setAssistBadge(roleReviewStatus, "삭제됨");
    roleReviewProgress.textContent = `자기 기록 역할 0/7 · 이벤트 0개 (${ROLE_REVIEW_SELF_REPORT})`;
    showToast(
      removedFromStorage
        ? `이 공고의 역할 기록을 삭제했습니다. 다른 패킷 ${storedCount}개는 유지됩니다.`
        : "이 공고의 현재 세션 역할 기록을 지웠습니다."
    );
  }

  function storedViewMode() {
    const storage = roleReviewStorage();
    if (!storage) return null;
    try {
      const value = storage.getItem(VIEW_MODE_STORAGE_KEY);
      return VIEW_MODES.has(value) ? value : null;
    } catch (_error) {
      return null;
    }
  }

  function setViewMode(mode, persist) {
    const nextMode = VIEW_MODES.has(mode) ? mode : "easy";
    appBody.dataset.mode = nextMode;
    // Easy mode hides the assist controls, so assist must not stay on unseen.
    if (nextMode === "easy" && assistedToggle.checked) {
      deactivateAssistedReview();
      if (persist) showToast("쉬운 모드에서는 AI·현행 법령 보강을 끕니다.");
    }
    [
      [modeEasyButton, nextMode === "easy"],
      [modeExpertButton, nextMode === "expert"],
    ].forEach(([button, pressed]) => {
      if (typeof button.setAttribute === "function") {
        button.setAttribute("aria-pressed", String(pressed));
      }
    });
    if (!persist) return;
    const storage = roleReviewStorage();
    if (!storage) return;
    try {
      storage.setItem(VIEW_MODE_STORAGE_KEY, nextMode);
    } catch (_error) {
      // The mode still applies for this page view without storage.
    }
  }

  function codePointsToCodeUnits(text, codePointOffset) {
    return Array.from(text).slice(0, codePointOffset).join("").length;
  }

  function highlightedPosting(text, orderedFindings) {
    const codePoints = Array.from(text);
    let cursor = 0;
    let markup = "";
    orderedFindings.forEach((finding, index) => {
      const [start, end] = finding.offset;
      // Overlapping candidates keep the first highlight; the card still lists both.
      if (start < cursor) return;
      markup += escapeHtml(codePoints.slice(cursor, start).join(""));
      markup += `<mark class="easy-mark">${escapeHtml(
        codePoints.slice(start, end).join("")
      )}<sup>${index + 1}</sup></mark>`;
      cursor = end;
    });
    return markup + escapeHtml(codePoints.slice(cursor).join(""));
  }

  function renderEasy(result, text) {
    const orderedFindings = [...result.findings].sort(
      (left, right) =>
        left.offset[0] - right.offset[0] || left.offset[1] - right.offset[1]
    );
    const missing = result.slots.filter((slot) => !slot.found);
    const questionCount = result.questions.length;
    const headline = orderedFindings.length
      ? `다시 살펴볼 표현 <strong>${orderedFindings.length}개</strong>, 공고문에서 찾지 못한 안내 <strong>${missing.length}개</strong>가 있습니다.`
      : missing.length
      ? `법 조항과 연결된 표현은 발견되지 않았습니다. 공고문에서 찾지 못한 안내 <strong>${missing.length}개</strong>를 확인해 보세요.`
      : "법 조항과 연결된 표현은 발견되지 않았고, 점검하는 안내 항목도 공고문에서 모두 찾았습니다.";
    const findingCards = orderedFindings
      .map((finding, index) => {
        const alternatives = finding.alternatives.length
          ? `<div class="easy-fix"><strong>이렇게 바꿔 보세요</strong><ul>${finding.alternatives
              .map((alternative) => `<li>${escapeHtml(alternative)}</li>`)
              .join("")}</ul></div>`
          : "";
        return `<li class="easy-finding">
          <div class="easy-finding-head">
            <span class="easy-number" aria-hidden="true">${index + 1}</span>
            <q class="easy-quote">${escapeHtml(finding.matched_text)}</q>
            <span class="severity-tag severity-${escapeHtml(finding.severity)}">${escapeHtml(reviewPriorityLabel(finding.severity))}</span>
          </div>
          <p class="easy-why"><strong>왜 다시 볼까요?</strong> ${escapeHtml(finding.message)}</p>
          ${alternatives}
          <div class="easy-finding-foot">
            <span>관련 법: ${escapeHtml(finding.basis.law)} ${escapeHtml(finding.basis.article)}</span>
            <button type="button" class="button button-quiet" data-select-start="${codePointsToCodeUnits(text, finding.offset[0])}" data-select-end="${codePointsToCodeUnits(text, finding.offset[1])}">공고문에서 이 부분 선택</button>
          </div>
        </li>`;
      })
      .join("");
    const findingsSection = orderedFindings.length
      ? `<section class="easy-section" aria-labelledby="easy-findings-heading">
          <h3 id="easy-findings-heading">1. 다시 살펴볼 표현</h3>
          <p class="easy-hint">노란색 표시는 법 조항과 함께 다시 볼 만한 표현입니다. 고쳐야 한다는 판정이 아니라, 직무에 꼭 필요한 조건인지 확인해 보라는 뜻입니다.</p>
          <pre class="easy-posting" tabindex="0" role="region" aria-label="표시된 공고문">${highlightedPosting(text, orderedFindings)}</pre>
          <ol class="easy-findings">${findingCards}</ol>
        </section>`
      : "";
    const missingSection = missing.length
      ? `<section class="easy-section" aria-labelledby="easy-missing-heading">
          <h3 id="easy-missing-heading">${orderedFindings.length ? "2" : "1"}. 공고문에 추가하면 좋은 안내</h3>
          <p class="easy-hint">공고문에서 아래 안내를 찾지 못했습니다. 절차가 없다는 뜻이 아니므로, 실제로 운영 중이면 공고문에 적어 주세요.</p>
          <ul class="easy-missing">${missing
            .map((slot) => `<li>${escapeHtml(slot.label)}</li>`)
            .join("")}</ul>
        </section>`
      : `<section class="easy-section"><p class="easy-hint">점검하는 안내 항목 ${result.slots.length}개를 공고문에서 모두 찾았습니다.</p></section>`;
    const expertPointer = questionCount
      ? `<div class="easy-expert-pointer">
          <span>담당자가 함께 확인할 질문 ${questionCount}개와 법 조항 원문은 전문가 모드에 있습니다.</span>
          <button type="button" class="button button-secondary" data-switch-mode="expert">전문가 모드로 보기</button>
        </div>`
      : "";
    easyResult.innerHTML = `
      <p class="easy-headline">${headline}</p>
      ${findingsSection}
      ${missingSection}
      <p class="easy-hint easy-rerun">공고문을 고친 뒤 '검토 메모 만들기'를 다시 누르면 결과가 새로 나옵니다.</p>
      ${expertPointer}
    `;
  }

  function showToast(message) {
    toast.textContent = message;
    toast.classList.add("visible");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("visible"), 4000);
  }

  function reviewPriorityLabel(value) {
    return {
      high: "우선 검토",
      medium: "검토",
      low: "참고",
    }[value] || "검토";
  }

  function currentOrganizationProfile() {
    const sector = organizationSector.value || "unspecified";
    const size = organizationSize.value || "unspecified";
    const publicEntityType =
      sector === "public"
        ? organizationPublicType.value || "unspecified"
        : "unspecified";
    const publicProfile = ORGANIZATION_PROFILES.get(publicEntityType);
    return {
      sector,
      size,
      public_entity_type: publicEntityType,
      sector_label: {
        unspecified: "기관 유형 미선택",
        public: "공공기관",
        private: "민간기업",
      }[sector],
      public_entity_type_label:
        sector === "public"
          ? publicProfile
            ? publicProfile.label
            : "공공 세부유형 미선택"
          : "해당 없음",
      size_label: {
        unspecified: "규모 미선택",
        under_30: "상시근로자 1~29명",
        "30_to_299": "상시근로자 30~299명",
        "300_plus": "상시근로자 300명 이상",
      }[size],
    };
  }

  function organizationPolicy(profile) {
    if (profile.sector === "private") {
      return {
        applicability: "민간기업 기준",
        statement:
          "공공기관 경영·혁신 지침을 직접 적용하지 않고 일반 고용·개인정보 법령, 취업규칙, 단체협약과 내부 인사규정을 확인합니다.",
        sources: [],
      };
    }
    if (profile.sector !== "public") {
      return {
        applicability: "적용 범위 확인",
        statement:
          "공공·민간과 공공기관 지정 유형을 선택하면 직접 검토할 지침과 참고 기준을 구분합니다.",
        sources: [],
      };
    }
    const publicProfile = ORGANIZATION_PROFILES.get(
      profile.public_entity_type
    );
    if (!publicProfile) {
      const classification = ORGANIZATION_SOURCES.get(
        "public-institutions-act-article-5"
      );
      return {
        applicability: "공공기관 지정 유형 확인 필요",
        statement:
          "공기업·준정부기관·기타공공기관·지방공공기관은 적용 체계가 다릅니다. 기관의 공식 지정 유형을 먼저 확인합니다.",
        sources: classification ? [classification] : [],
      };
    }
    return {
      applicability: publicProfile.applicability_label,
      statement: publicProfile.statement,
      sources: publicProfile.source_ids
        .map((sourceId) => ORGANIZATION_SOURCES.get(sourceId))
        .filter(Boolean),
    };
  }

  function organizationContext(question) {
    const profile = currentOrganizationProfile();
    const policy = organizationPolicy(profile);
    const publicGuidance = PUBLIC_GUIDANCE_QUESTION_IDS.has(question.id);
    const axQuestion =
      AX_QUESTION_IDS.has(question.id) || question.id === "Q-INFO-005";
    let sectorNote;
    if (profile.sector === "public") {
      sectorNote = axQuestion
        ? "공공기관은 도입 근거·조달 기준·평가위원 독립성·이의제기와 감사 추적을 명확히 문서화합니다."
        : "공공기관은 공개된 기준, 평가위원 독립성, 이의제기와 감사 가능한 기록을 우선 확인합니다.";
    } else if (profile.sector === "private") {
      sectorNote = axQuestion
        ? "민간기업은 공급자 계약·데이터 처리·직무관련성·사람의 최종 결정 권한과 운영 효과를 명확히 문서화합니다."
        : "민간기업은 직무관련성, 비례적인 절차, 최소수집과 실제 운영 증거를 우선 확인합니다.";
      if (publicGuidance) {
        sectorNote +=
          " 이 문항은 공공기관 지침 중심이므로 민간에는 법적 의무로 단정하지 않고 참고 적용합니다.";
      }
    } else {
      sectorNote = publicGuidance
        ? "공공기관 지침 중심 문항입니다. 기관 유형을 선택하면 민간 참고 적용 여부를 구분합니다."
        : "기관 유형을 선택하면 공공의 감사·공개 책임과 민간의 계약·운영 책임을 구분합니다.";
    }
    const sizeNote = {
      unspecified: "규모를 선택하면 문서화와 검토 체계의 깊이를 조정합니다.",
      under_30:
        "소규모 운영: 책임자 1인을 지정하고 핵심 판단·예외·수정 이력을 간단한 양식으로 남깁니다.",
      "30_to_299":
        "중규모 운영: HR과 현업의 이중 검토, 승인권자와 변경 이력을 분리해 남깁니다.",
      "300_plus":
        "대규모 운영: HR·법무·개인정보·보안 책임을 분리하고 위원회 검토와 정기 감사를 운영합니다.",
    }[profile.size];
    return {
      label: `${profile.sector_label}${profile.sector === "public" ? ` · ${profile.public_entity_type_label}` : ""} · ${profile.size_label}`,
      applicability:
        profile.sector === "private" && publicGuidance
          ? "공공기관 중심 문항·민간 참고 적용"
          : policy.applicability,
      note: `${policy.statement} ${sectorNote} ${sizeNote}`,
      sources: policy.sources,
    };
  }

  function updateAnswerProgress() {
    const total = latestResult ? latestResult.questions.length : 0;
    const answered = latestResult
      ? latestResult.questions.filter((question) =>
          Boolean((reviewAnswers.get(question.id) || "").trim())
        ).length
      : 0;
    document.getElementById("answer-progress").textContent =
      `담당자 답변 ${answered}/${total}`;
  }

  function setAssistBadge(element, label, state = "") {
    element.textContent = label;
    element.classList.remove("active", "error");
    if (state) element.classList.add(state);
  }

  function announceAssisted(message) {
    // Polite live region for AI progress, results and toggle changes.
    assistedLive.textContent = "";
    assistedLive.textContent = message;
  }

  function updateResultsNote() {
    if (latestAssistedReview || !assistedPanel.hidden) {
      resultsNote.textContent =
        "판정이 아니라 수정·확인 질문을 정리한 검토 메모입니다. 기본 결과는 브라우저에서 만들었고, AI·현행 법령 보강 메모는 FairPost 서버와 선택한 AI 제공자를 거친 초안입니다.";
      return;
    }
    resultsNote.textContent = assistedToggle.checked
      ? "판정이 아니라 수정·확인 질문을 정리한 로컬 검토 메모입니다. AI 보강은 '보강 실행'을 누를 때만 전송합니다."
      : "판정이 아니라 수정·확인 질문을 정리한 로컬 검토 메모입니다.";
  }

  // Everything that changes what an AI request would contain. A result is
  // current only while this matches the request that produced it.
  function assistedRequestSignature() {
    return JSON.stringify([
      input.value,
      assistedProvider.value || "",
      currentOrganizationProfile(),
    ]);
  }

  function assistedResultIsCurrent() {
    return Boolean(
      latestAssistedReview &&
        assistedResultSignature !== null &&
        assistedResultSignature === assistedRequestSignature()
    );
  }

  function resetAssistedReviewPanel() {
    assistedRequestSequence += 1;
    assistedInFlight = false;
    latestAssistedReview = null;
    assistedResultSignature = null;
    assistedResultView = null;
    assistedPanel.hidden = true;
    assistedNotice.textContent = "";
    assistedOutput.textContent = "";
    setAssistBadge(assistedResultStatus, "대기");
    updateResultsNote();
    updateAssistedRunState();
  }

  function deactivateAssistedReview() {
    assistedToggle.checked = false;
    resetAssistedReviewPanel();
    assistedConsent.hidden = true;
    assistedProvider.disabled = true;
    assistedStatus.textContent =
      "꺼짐 · 기본 검사는 브라우저에서만 실행됩니다. 켜도 '보강 실행'을 누르기 전에는 전송하지 않습니다.";
    setAssistBadge(assistedBadge, "꺼짐");
    setLocalPrivacyNotice();
    announceAssisted("AI·현행 법령 보강을 껐습니다.");
  }

  function setLocalPrivacyNotice() {
    privacyMessage.textContent =
      "기본 검사는 브라우저 안에서 처리 · 역할 기록은 브라우저에만 저장";
  }

  function setAssistedPrivacyNotice() {
    privacyMessage.textContent =
      "AI 보강 켜짐 · '보강 실행'을 누를 때만 공고문을 FairPost 서버로 전송(AI 제공자에게는 마스킹된 탐지 표현과 근거만)";
  }

  async function responseJson(response) {
    try {
      return await response.json();
    } catch (_error) {
      return {};
    }
  }

  function availableAssistedProviders(capability) {
    return Array.isArray(capability.available_providers)
      ? capability.available_providers
          .map((item) => item && item.id)
          .filter((id) => Object.prototype.hasOwnProperty.call(assistedProviderLabels, id))
      : [];
  }

  function configureAssistedProviders(capability) {
    const available = availableAssistedProviders(capability);
    assistedProvider.innerHTML = available
      .map((id) => `<option value="${id}">${assistedProviderLabels[id]}</option>`)
      .join("");
    const preferred = available.includes(capability.default_provider)
      ? capability.default_provider
      : available[0] || "";
    assistedProvider.value = preferred;
    assistedProvider.disabled = available.length < 2;
    return preferred ? assistedProviderLabels[preferred] : "설정된 AI";
  }

  function selectedProviderLabel() {
    return assistedProviderLabels[assistedProvider.value] || "설정된 AI";
  }

  function setAssistedUnavailable(reason, { announce = false } = {}) {
    assistedAvailable = false;
    assistedToggle.checked = false;
    assistedToggle.disabled = true;
    assistedProvider.disabled = true;
    assistedConsent.hidden = true;
    setAssistBadge(assistedBadge, "사용 불가", "error");
    assistedStatus.textContent = reason;
    setLocalPrivacyNotice();
    updateAssistedRunState();
    updateResultsNote();
    if (announce) announceAssisted(`AI·현행 법령 보강이 자동으로 꺼졌습니다. ${reason}`);
  }

  // Only asks whether the server offers assisted review; no posting content
  // is sent. Without a server (file://) the toggle stays disabled.
  async function checkAssistedAvailability() {
    const protocol = window.location && window.location.protocol;
    if (protocol !== "http:" && protocol !== "https:") {
      setAssistedUnavailable(
        "사용할 수 없음: 파일로 연 화면에는 FairPost 서버가 없어 AI·현행 법령 보강을 쓸 수 없습니다. 기본 검사는 그대로 사용할 수 있습니다."
      );
      return;
    }
    if (typeof fetch !== "function") {
      setAssistedUnavailable("사용할 수 없음: 이 브라우저에서 서버 요청을 보낼 수 없습니다.");
      return;
    }
    assistedToggle.disabled = true;
    setAssistBadge(assistedBadge, "확인 중");
    try {
      const response = await fetch("/api/assisted-review", {
        method: "GET",
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      const capability = await responseJson(response);
      if (!response.ok) {
        throw new Error(
          capability.reason ||
            capability.error ||
            `이 서버에는 보강 검토 API가 없습니다(응답 ${response.status}).`
        );
      }
      if (capability.ready !== true || !availableAssistedProviders(capability).length) {
        throw new Error(
          capability.reason || "서버에 사용할 수 있는 AI 제공자가 설정되지 않았습니다."
        );
      }
      assistedAvailable = true;
      configureAssistedProviders(capability);
      assistedProvider.disabled = true;
      assistedToggle.disabled = false;
      setAssistBadge(assistedBadge, "꺼짐");
      assistedStatus.textContent =
        "사용할 수 있습니다. 켜도 바로 전송하지 않으며, 전송 범위를 확인한 뒤 '보강 실행'을 눌러야 요청합니다.";
    } catch (error) {
      const message =
        error instanceof Error && error.message
          ? error.message
          : "서버 설정을 확인하지 못했습니다.";
      setAssistedUnavailable(`사용할 수 없음: ${message}`);
    }
  }

  function updateAssistedRunState() {
    const enabled = assistedAvailable && assistedToggle.checked;
    let hint = "";
    if (!enabled) {
      hint = "";
    } else if (assistedInFlight) {
      hint = "요청 중입니다. 결과를 기다리고 있습니다.";
    } else if (!latestResult || !input.value.trim()) {
      hint = "먼저 '검토 메모 만들기'로 기본 검토를 실행하세요.";
    } else if (latestCheckedText !== input.value) {
      hint = "공고문이 바뀌었습니다. 기본 검토를 다시 실행한 뒤 보강을 실행하세요.";
    } else {
      hint = `누르면 현재 공고문과 설정(${selectedProviderLabel()})으로 한 번 요청합니다.`;
    }
    assistedRun.disabled =
      !enabled ||
      assistedInFlight ||
      !latestResult ||
      !input.value.trim() ||
      latestCheckedText !== input.value;
    assistedRunHint.textContent = hint;
  }

  function showAssistedResultView() {
    if (!assistedResultView) return;
    if (assistedResultIsCurrent()) {
      setAssistBadge(
        assistedResultStatus,
        assistedResultView.badge,
        assistedResultView.state
      );
      assistedNotice.textContent = assistedResultView.notice;
      return;
    }
    setAssistBadge(assistedResultStatus, "이전 입력 기준", "error");
    assistedNotice.textContent =
      "공고문·AI 제공자·조직 조건 중 하나가 바뀌어 아래 AI 메모는 현재 내용과 다를 수 있습니다. 새 결과가 필요하면 '보강 실행'을 다시 누르세요. 메모 복사에는 포함하지 않습니다.";
  }

  function markAssistedSettingsChanged(reason) {
    updateAssistedRunState();
    if (!assistedResultView) return;
    const wasCurrent = assistedResultViewCurrent;
    showAssistedResultView();
    assistedResultViewCurrent = assistedResultIsCurrent();
    if (wasCurrent && !assistedResultViewCurrent) {
      announceAssisted(`${reason} 이전 AI 보강 메모는 현재 내용과 다를 수 있습니다.`);
    }
  }

  async function runAssistedReview(text) {
    const requestId = ++assistedRequestSequence;
    const signature = assistedRequestSignature();
    const providerLabel = selectedProviderLabel();
    assistedInFlight = true;
    latestAssistedReview = null;
    assistedResultSignature = null;
    assistedResultView = null;
    assistedPanel.hidden = false;
    assistedNotice.textContent =
      "Korean Law MCP에서 현행 조문을 확인한 뒤 AI 검토 메모를 작성하고 있습니다.";
    assistedOutput.textContent = "";
    setAssistBadge(assistedResultStatus, "검토 중", "active");
    setAssistBadge(assistedBadge, "켜짐", "active");
    assistedStatus.textContent = `${providerLabel}로 요청했습니다. 설정을 바꾸면 이 결과는 '이전 입력 기준'으로 표시되며 자동으로 다시 보내지 않습니다.`;
    updateResultsNote();
    updateAssistedRunState();
    announceAssisted(`${providerLabel}로 AI·현행 법령 보강을 요청했습니다. 결과를 기다리는 중입니다.`);
    try {
      const response = await fetch("/api/assisted-review", {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        cache: "no-store",
        body: JSON.stringify({
          assist_enabled: true,
          ai_provider: assistedProvider.value || null,
          text,
          organization_profile: currentOrganizationProfile(),
        }),
      });
      const result = await responseJson(response);
      if (requestId !== assistedRequestSequence || !assistedToggle.checked) return;
      if (response.status === 503 || (result && result.ready === false)) {
        assistedInFlight = false;
        assistedOutput.textContent =
          "서버에서 AI·현행 법령 보강을 사용할 수 없어 요청을 처리하지 않았습니다. 로컬 검토 결과는 그대로 사용할 수 있습니다.";
        assistedNotice.textContent = "";
        setAssistBadge(assistedResultStatus, "사용 불가", "error");
        setAssistedUnavailable(
          `사용할 수 없음: ${result.reason || "서버 설정이 바뀌었습니다."}`,
          { announce: true }
        );
        return;
      }
      if (!response.ok) {
        throw new Error(result.reason || result.error || "보강 검토 요청이 실패했습니다.");
      }
      const resultProvider = assistedProviderLabels[result.ai_provider] || "AI";
      let view;
      if (result.status === "completed" && result.summary) {
        assistedOutput.textContent = result.summary;
        view = { badge: "완료", state: "active" };
        showToast("AI·현행 법령 보강 검토를 완료했습니다.");
      } else if (result.status === "no_findings") {
        assistedOutput.textContent =
          "로컬 규칙에서 현행 법령을 추가 조회할 표현 후보가 확인되지 않아 AI API를 호출하지 않았습니다.";
        view = { badge: "호출 안 함", state: "" };
      } else if (result.status === "law_lookup_unavailable") {
        assistedOutput.textContent =
          "Korean Law MCP에서 현행 조문을 확보하지 못해 AI API를 호출하지 않았습니다.";
        view = { badge: "법령 확인 실패", state: "error" };
      } else {
        assistedOutput.textContent = result.notice || "보강 검토를 완료하지 못했습니다.";
        view = { badge: "AI 확인 실패", state: "error" };
      }
      latestAssistedReview = result;
      assistedResultSignature = signature;
      assistedResultView = {
        ...view,
        notice: `${result.notice || ""} ${resultProvider}와 현행 조문 ${result.current_articles_retrieved || 0}건을 사용했습니다.`.trim(),
      };
      assistedInFlight = false;
      showAssistedResultView();
      assistedResultViewCurrent = assistedResultIsCurrent();
      updateResultsNote();
      updateAssistedRunState();
      announceAssisted(
        assistedResultViewCurrent
          ? `AI·현행 법령 보강 결과: ${view.badge}.`
          : `AI·현행 법령 보강 결과가 도착했지만 그사이 공고문이나 설정이 바뀌어 이전 입력 기준으로 표시합니다.`
      );
    } catch (error) {
      if (requestId !== assistedRequestSequence) return;
      assistedInFlight = false;
      const message = error instanceof Error ? error.message : "보강 검토를 완료하지 못했습니다.";
      assistedNotice.textContent = "로컬 검토 결과는 그대로 사용할 수 있습니다.";
      assistedOutput.textContent = message;
      setAssistBadge(assistedResultStatus, "연결 실패", "error");
      updateAssistedRunState();
      announceAssisted(`AI·현행 법령 보강 요청이 실패했습니다. ${message}`);
    }
  }

  function resetReview() {
    latestResult = null;
    latestCheckedText = null;
    comparisonBaseline = null;
    comparison = null;
    comparisonPanel.hidden = true;
    comparisonStatus.textContent = "";
    comparisonGroups.replaceChildren();
    comparisonReset.disabled = true;
    resetAssistedReviewPanel();
    roleReviewSequence += 1;
    roleReviewState = null;
    roleReviewPanel.hidden = true;
    roleReviewEvents.replaceChildren();
    setAssistBadge(roleReviewStatus, "대기");
    roleReviewProgress.textContent = `자기 기록 역할 0/7 · 이벤트 0개 (${ROLE_REVIEW_SELF_REPORT})`;
    roleReviewMissing.textContent = "";
    reviewAnswers.clear();
    ["findings-list", "slots-list", "questions-list"].forEach((id) =>
      document.getElementById(id).replaceChildren()
    );
    easyResult.replaceChildren();
    document.getElementById("disclaimer").textContent = "";
    document.getElementById("finding-count").textContent = "0";
    document.getElementById("missing-count").textContent = "0";
    document.getElementById("question-count").textContent = "0";
    emptyState.hidden = false;
    resultContent.hidden = true;
    copyButton.disabled = true;
    updateAnswerProgress();
  }

  function renderFindings(findings) {
    const container = document.getElementById("findings-list");
    if (!findings.length) {
      container.innerHTML =
        '<div class="none-item">법령 조항과 함께 표시할 표현이 확인되지 않았습니다.</div>';
      return;
    }
    container.innerHTML = findings
      .map((finding) => {
        const alternatives = finding.alternatives.length
          ? `<p class="alternative"><strong>대안 표현</strong> ${finding.alternatives
              .map(escapeHtml)
              .join(" · ")}</p>`
          : "";
        return `<article class="finding-item">
          <div class="item-main">
            <div class="item-meta">
              <span class="id-tag">${escapeHtml(finding.id)}</span>
              <span class="dimension-tag">${escapeHtml(finding.dimension)}</span>
              <span class="severity-tag severity-${escapeHtml(finding.severity)}"><span class="visually-hidden">검토 우선도 </span>${escapeHtml(reviewPriorityLabel(finding.severity))}</span>
              <span>${escapeHtml(finding.section)} · ${finding.offset[0]}–${finding.offset[1]}</span>
            </div>
            <p class="item-title">${escapeHtml(finding.message)}</p>
            <p class="match-row">원문 <mark class="matched-text">${escapeHtml(finding.matched_text)}</mark></p>
            ${alternatives}
          </div>
          <details class="basis-detail">
            <summary>근거 조항 원문</summary>
            <div class="basis-content">
              <strong>${escapeHtml(finding.basis.law)} ${escapeHtml(finding.basis.article)}</strong>
              <span>${escapeHtml(finding.basis.title)} · 시행 ${escapeHtml(finding.basis.effective_date)} · 스냅샷 ${escapeHtml(finding.basis.snapshot_date)}</span>
              <pre tabindex="0">${escapeHtml(finding.basis.text)}</pre>
            </div>
          </details>
        </article>`;
      })
      .join("");
  }

  function renderSlots(slots, questions) {
    const missing = slots.filter((slot) => !slot.found);
    const container = document.getElementById("slots-list");
    if (!missing.length) {
      container.innerHTML =
        '<div class="none-item">11개 안내 항목이 공고문에서 모두 확인되었습니다.</div>';
      return;
    }
    const questionsById = new Map(
      questions.map((question) => [question.id, question])
    );
    container.innerHTML = missing
      .map((slot) => {
        const question = questionsById.get(SLOT_QUESTION_IDS[slot.slot]);
        const questionDetail = question
          ? `<details class="slot-question-detail">
              <summary aria-label="${escapeHtml(slot.label)} 관련 확인 질문 보기">확인 질문 보기</summary>
              <div class="slot-question-content">${questionCard(question)}</div>
            </details>`
          : "";
        return `<div class="slot-item">
          <strong>${escapeHtml(slot.label)}</strong>
          <span>공고문에서 확인되지 않았습니다</span>
          ${questionDetail}
        </div>`;
      })
      .join("");
  }

  function questionCard(question) {
    const followUp = question.follow_up.length
      ? `<p class="follow-up">${question.follow_up
          .map((item) => `· ${escapeHtml(item)}`)
          .join("<br>")}</p>`
      : "";
    const evidence = question.matched_text
      ? `<p class="match-row">발동 문맥 <mark class="matched-text">${escapeHtml(question.matched_text)}</mark> <span>${escapeHtml(question.section || "")} · ${question.offset ? `${question.offset[0]}–${question.offset[1]}` : ""}</span></p>`
      : "";
    const referenceMeta = question.reference
      ? [
          question.reference.publisher,
          Number.isInteger(question.reference.year)
            ? `${question.reference.year}년`
            : null,
          question.reference.pages
            ? `${question.reference.pages.join(", ")}쪽`
            : null,
          question.reference.accessed_at
            ? `확인 ${question.reference.accessed_at}`
            : null,
          question.reference.sections
            ? question.reference.sections.join(", ")
            : null,
        ]
          .filter(Boolean)
          .map(escapeHtml)
          .join(" · ")
      : "";
    const reference = question.reference && question.reference.title
      ? `<p class="reference-row">근거 ${question.reference.source_url ? `<a href="${escapeHtml(question.reference.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(question.reference.title)}</a>` : escapeHtml(question.reference.title)}${referenceMeta ? ` · ${referenceMeta}` : ""}</p>`
      : "";
    const detail = followUp
      ? `<details class="question-detail">
          <summary>후속 질문 ${question.follow_up.length}개 보기</summary>
          <div class="question-detail-content">${followUp}</div>
        </details>`
      : "";
    const scopeLabel =
      question.review_scope === "common" ? "공통 기본" : "공고별";
    const linkedFindings = question.linked_findings || [];
    const linkTag = linkedFindings.length
      ? `<span class="link-tag">관련 표현 ${linkedFindings
          .map((id) => escapeHtml(id))
          .join(", ")}</span>`
      : "";
    const answerId = `answer-${question.id}`;
    const organization = organizationContext(question);
    const organizationReferences = organization.sources.length
      ? `<span class="organization-reference">기관 적용 근거 ${organization.sources
          .map(
            (source) =>
              `<a href="${escapeHtml(source.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(source.title)}</a>`
          )
          .join(" · ")}</span>`
      : "";
    const answer = reviewAnswers.get(question.id) || "";
    return `<article class="question-item">
      <div class="item-main">
        <div class="item-meta">
          <span class="id-tag">${escapeHtml(question.id)}</span>
          <span class="dimension-tag">${escapeHtml(question.dimension)}</span>
          <span class="scope-tag">${scopeLabel}</span>
          ${linkTag}
          <span>${escapeHtml(question.book_ref)}</span>
        </div>
        <p class="item-title">${escapeHtml(question.question)}</p>
        ${evidence}
        ${reference}
        <p class="organization-context"><span class="applicability-tag">${escapeHtml(organization.applicability)}</span><br><strong>${escapeHtml(organization.label)}</strong><br>${escapeHtml(organization.note)}${organizationReferences ? `<br>${organizationReferences}` : ""}</p>
      </div>
      ${detail}
      <details class="review-answer-detail">
        <summary>담당자 답변 남기기</summary>
        <div class="review-answer-content">
          <label for="${escapeHtml(answerId)}">${escapeHtml(question.id)} 사람 검토 답변</label>
          <textarea id="${escapeHtml(answerId)}" data-question-answer="${escapeHtml(question.id)}" rows="3" placeholder="근거를 확인한 뒤 수정 여부, 확인한 사실, 후속 조치를 기록하세요.">${escapeHtml(answer)}</textarea>
          <small>이 답변은 현재 분석 메모리에만 보관되며 서버나 브라우저 저장소로 전송·저장되지 않습니다.</small>
        </div>
      </details>
    </article>`;
  }

  function renderQuestions(questions) {
    const container = document.getElementById("questions-list");
    if (!questions.length) {
      container.innerHTML =
        '<div class="none-item">현재 규칙에 연결된 추가 검토 질문이 확인되지 않았습니다.</div>';
      return;
    }

    const postingQuestions = questions.filter(
      (question) => question.review_scope !== "common"
    );
    const commonQuestions = questions.filter(
      (question) => question.review_scope === "common"
    );
    const visiblePostingQuestions = postingQuestions.filter(
      (question) => !SLOT_EMBEDDED_QUESTION_IDS.has(question.id)
    );
    const postingMarkup = visiblePostingQuestions.length
      ? visiblePostingQuestions.map(questionCard).join("")
      : '<div class="none-item">이 공고에서 먼저 확인할 추가 질문이 없습니다.</div>';
    const commonMarkup = commonQuestions.length
      ? `<details id="common-checklist" class="common-checklist">
          <summary>
            <span>공통 기본 체크리스트 <strong>${commonQuestions.length}개</strong></span>
            <small>대부분의 공고에서 반복되는 기본 질문을 접어 두었습니다.</small>
          </summary>
          <div class="common-question-list">
            ${commonQuestions.map(questionCard).join("")}
          </div>
        </details>`
      : "";
    container.innerHTML = `
      <div class="question-group-heading">
        <strong>이 공고에서 먼저 볼 질문 ${visiblePostingQuestions.length}개</strong>
        <span>누락 확인 질문은 관련 항목 카드 안에 접어 두었습니다.</span>
      </div>
      ${postingMarkup}
      ${commonMarkup}
    `;
  }

  function comparisonItems(result) {
    const items = new Map();
    result.findings.forEach((finding) => items.set(`finding:${finding.id}`, {
      label: `표현 · ${finding.message}`,
      evidence: finding.matched_text,
      id: finding.id,
    }));
    result.slots.filter((slot) => !slot.found).forEach((slot) =>
      items.set(`slot:${slot.slot}`, {
        label: `누락 안내 · ${slot.label}`, evidence: "", id: slot.slot,
      })
    );
    result.questions.forEach((question) => {
      // A missing slot and its embedded question are one review task.
      if (SLOT_EMBEDDED_QUESTION_IDS.has(question.id) &&
          Object.entries(SLOT_QUESTION_IDS).some(([slot, id]) =>
            id === question.id && items.has(`slot:${slot}`))) return;
      items.set(`question:${question.id}`, {
        label: `질문 · ${question.question}`,
        evidence: question.matched_text || "",
        id: question.id,
      });
    });
    return items;
  }

  function updateComparison(result, text) {
    const changedVersion = comparisonBaseline &&
      comparisonBaseline.result.ruleset_version !== result.ruleset_version;
    if (!comparisonBaseline || changedVersion) {
      comparisonBaseline = { result, text };
      comparison = null;
      renderComparison(changedVersion
        ? "검토 기준 버전이 바뀌어 현재 결과를 새 비교 기준으로 삼았습니다."
        : "첫 검토를 비교 기준으로 삼았습니다. 공고문을 고친 뒤 다시 검토해 보세요.");
      return;
    }
    const before = comparisonItems(comparisonBaseline.result);
    const after = comparisonItems(result);
    comparison = [
      { key: "removed", label: "이번에 표시되지 않음", items: [...before].filter(([key]) => !after.has(key)).map(([, item]) => item) },
      { key: "remaining", label: "계속 확인", items: [...after].filter(([key]) => before.has(key)).map(([, item]) => item) },
      { key: "added", label: "새로 표시", items: [...after].filter(([key]) => !before.has(key)).map(([, item]) => item) },
    ];
    renderComparison(text === comparisonBaseline.text
      ? "비교 기준과 같은 공고문입니다. 항목 변화가 없습니다."
      : "비교 기준 이후 항목 변화입니다. 같은 항목의 문구나 위치가 바뀐 경우에는 ‘계속 확인’에 포함됩니다.");
  }

  function renderComparison(message) {
    comparisonPanel.hidden = !comparisonBaseline;
    const stale = latestCheckedText !== input.value;
    comparisonReset.disabled = stale || !comparison ||
      comparisonBaseline.text === latestCheckedText;
    comparisonStatus.textContent = message;
    if (!comparison) {
      comparisonGroups.replaceChildren();
      return;
    }
    comparisonGroups.innerHTML = comparison.map((group) => `
      <details class="comparison-group comparison-${group.key}">
        <summary>${group.label} <strong>${group.items.length}개</strong></summary>
        ${group.items.length ? `<ul>${group.items.map((item) => `
          <li><span>${escapeHtml(item.label)}</span>
            ${item.evidence ? `<q>${escapeHtml(item.evidence)}</q>` : ""}
          </li>`).join("")}</ul>` : '<p class="comparison-empty">해당 항목이 없습니다.</p>'}
      </details>`).join("");
  }

  function render(result) {
    latestResult = result;
    document.getElementById("finding-count").textContent = result.counts.findings;
    document.getElementById("missing-count").textContent = result.counts.not_found;
    document.getElementById("question-count").textContent =
      result.questions.filter(
        (question) =>
          question.review_scope !== "common" &&
          !SLOT_EMBEDDED_QUESTION_IDS.has(question.id)
      ).length;
    document.getElementById("disclaimer").textContent =
      `${result.statute_notice} ${result.disclaimer}`;
    renderFindings(result.findings);
    renderSlots(result.slots, result.questions);
    renderQuestions(result.questions);
    renderEasy(result, input.value);
    updateAnswerProgress();
    emptyState.hidden = true;
    resultContent.hidden = false;
    copyButton.disabled = false;
    updateComparison(result, input.value);
    void initializeRoleReview(result, input.value);
  }

  function makeReport(result) {
    const answeredCount = result.questions.filter((question) =>
      Boolean((reviewAnswers.get(question.id) || "").trim())
    ).length;
    const lines = [
      "fairpost 채용공고문 검토 메모",
      result.disclaimer,
      "개수는 검토할 작업량이며 점수·등급·합격/불합격 또는 공정성 판정이 아닙니다.",
      "",
      `규칙 사전: ${result.ruleset_version}`,
      `법령 기준일: ${result.statute_snapshot_date}`,
      result.statute_notice,
      `담당자 답변 진행: ${answeredCount}/${result.questions.length}`,
      `조직 조건: ${currentOrganizationProfile().sector_label}${currentOrganizationProfile().sector === "public" ? ` · ${currentOrganizationProfile().public_entity_type_label}` : ""} · ${currentOrganizationProfile().size_label}`,
      "",
      `[확인된 사항 ${result.counts.findings}건]`,
    ];
    if (!result.findings.length) {
      lines.push("법령 조항과 함께 표시할 표현이 확인되지 않았습니다.");
    }
    result.findings.forEach((finding) => {
      lines.push(
        `- ${finding.id} ${finding.message}`,
        `  원문: "${finding.matched_text}" (${finding.section}, ${finding.offset[0]}–${finding.offset[1]})`,
        `  근거: ${finding.basis.law} ${finding.basis.article} ${finding.basis.title}`,
        `  시행일: ${finding.basis.effective_date} · 스냅샷: ${finding.basis.snapshot_date}`
      );
      finding.alternatives.forEach((alternative) =>
        lines.push(`  대안: ${alternative}`)
      );
    });
    const questionsById = new Map(
      result.questions.map((question) => [question.id, question])
    );
    const postingQuestions = result.questions.filter(
      (question) => question.review_scope !== "common"
    );
    const visiblePostingQuestions = postingQuestions.filter(
      (question) => !SLOT_EMBEDDED_QUESTION_IDS.has(question.id)
    );
    const commonQuestions = result.questions.filter(
      (question) => question.review_scope === "common"
    );
    const appendQuestion = (question, prefix = "-") => {
      lines.push(`${prefix} ${question.id} ${question.question}`);
      const organization = organizationContext(question);
      lines.push(
        `  적용 구분: ${organization.applicability}`,
        `  조직 맥락: ${organization.label} · ${organization.note}`
      );
      organization.sources.forEach((source) => {
        lines.push(
          `  기관 적용 근거: ${source.title} (${source.source_url}) · ${source.publisher} · ${source.published_or_updated_at}`
        );
      });
      if (question.matched_text) {
        lines.push(
          `  발동 문맥: "${question.matched_text}" (${question.section || ""}, ${question.offset ? `${question.offset[0]}–${question.offset[1]}` : ""})`
        );
      }
      if (question.reference && question.reference.title) {
        const referenceMeta = [
          question.reference.publisher,
          Number.isInteger(question.reference.year)
            ? `${question.reference.year}년`
            : null,
          question.reference.pages
            ? `${question.reference.pages.join(", ")}쪽`
            : null,
          question.reference.accessed_at
            ? `확인 ${question.reference.accessed_at}`
            : null,
        ]
          .filter(Boolean)
          .join(" · ");
        lines.push(
          `  근거: ${question.reference.title}${question.reference.source_url ? ` (${question.reference.source_url})` : ""}${referenceMeta ? ` · ${referenceMeta}` : ""}`
        );
      }
      question.follow_up.forEach((item) => lines.push(`  · ${item}`));
      const answer = (reviewAnswers.get(question.id) || "").trim();
      if (answer) {
        answer.split(/\r?\n/).forEach((line, index) => {
          lines.push(index === 0 ? `  담당자 답변: ${line}` : `    ${line}`);
        });
      }
    };
    lines.push("", `[확인되지 않은 항목 ${result.counts.not_found}건]`);
    result.slots
      .filter((slot) => !slot.found)
      .forEach((slot) => {
        lines.push(`- ${slot.label}: 공고문에서 확인되지 않았습니다.`)
        const slotQuestion = questionsById.get(SLOT_QUESTION_IDS[slot.slot]);
        if (slotQuestion) {
          appendQuestion(slotQuestion, "  확인 질문:");
        }
    });
    lines.push("", `[공고별 추가 검토 질문 ${visiblePostingQuestions.length}건]`);
    visiblePostingQuestions.forEach((question) => appendQuestion(question));
    lines.push("", `[공통 기본 체크리스트 ${commonQuestions.length}건]`);
    commonQuestions.forEach((question) => appendQuestion(question));
    if (latestAssistedReview && latestAssistedReview.summary) {
      if (assistedResultIsCurrent()) {
        lines.push(
          "",
          "[AI·현행 법령 보강 검토 — FairPost 서버와 선택한 AI 제공자를 거친 초안]",
          latestAssistedReview.notice,
          latestAssistedReview.summary
        );
      } else {
        lines.push(
          "",
          "[AI·현행 법령 보강 검토]",
          "이전 공고문·설정으로 받은 AI 메모라 포함하지 않았습니다. 필요하면 '보강 실행'을 다시 누르세요."
        );
      }
    }
    if (roleReviewState && roleReviewUserEvents().length) {
      const summary = roleReviewProgressSummary();
      lines.push(
        "",
        `[다중 역할 검토 기록 — ${ROLE_REVIEW_SELF_REPORT}]`,
        `자기 기록 역할: ${summary.roles.size}/7 · 이벤트: ${summary.userEvents.length}개 · 미해결 이슈: ${summary.openIssues.length}건`,
        "이 기록은 이 브라우저에서 입력한 자기 기록이며 본인 확인·위원회 승인·결재가 아닙니다."
      );
      summary.userEvents.forEach((event) => {
        const resolves = event.resolves_event_id
          ? ` (${roleReviewEventNumber(event.resolves_event_id)}번 이슈 해결)`
          : "";
        lines.push(
          `- ${roleReviewEventNumber(event.event_id)}번 ${ROLE_LABELS[event.role] || event.role} · ${STAGE_LABELS[event.stage] || event.stage} · ${ACTION_LABELS[event.action] || event.action}${resolves}: ${event.note || "메모 없음"}`
        );
      });
    }
    if (comparison) {
      lines.push("", "[수정 전후 비교]", "같은 검토 기준에서 항목별로 비교합니다. 표시가 사라져도 검토 완료를 뜻하지 않습니다.",
        `비교 기준 버전: ${comparisonBaseline.result.ruleset_version}`);
      comparison.forEach((group) => {
        lines.push(`${group.label}: ${group.items.length}개`);
        group.items.forEach((item) => lines.push(`- ${item.id} ${item.label}`));
      });
    }
    return lines.join("\n");
  }

  function runCheck() {
    if (!input.value.trim()) {
      setFieldError(input, postingInputError, "검토할 공고문을 입력하세요.");
      showToast("검토할 공고문을 입력하세요.");
      input.focus();
      return;
    }
    setFieldError(input, postingInputError, "");
    reviewAnswers.clear();
    latestCheckedText = input.value;
    render(window.FairpostEngine.check(input.value));
    resultsTitle.focus();
    if (
      typeof window.matchMedia === "function" &&
      window.matchMedia("(max-width: 1100px)").matches &&
      typeof resultsTitle.scrollIntoView === "function"
    ) {
      resultsTitle.scrollIntoView({ behavior: "auto", block: "start" });
    }
    // No automatic AI request: the user presses "보강 실행" explicitly.
    markAssistedSettingsChanged("공고문을 다시 검토했습니다.");
  }

  input.addEventListener("input", () => {
    charCount.textContent = `${Array.from(input.value).length.toLocaleString("ko-KR")}자`;
    if (input.value.trim()) setFieldError(input, postingInputError, "");
    if (!input.value.trim()) {
      resetReview();
    } else if (latestResult) {
      const stale = latestCheckedText !== input.value;
      copyButton.disabled = stale;
      renderComparison(stale
        ? "공고문이 바뀌었습니다. 다시 검토하면 비교 결과와 메모가 갱신됩니다."
        : "마지막으로 검토한 공고문입니다. 아래 결과와 메모를 사용할 수 있습니다.");
    }
    markAssistedSettingsChanged("공고문이 바뀌었습니다.");
  });
  checkButton.addEventListener("click", runCheck);
  assistedToggle.addEventListener("change", () => {
    if (assistedToggle.checked) {
      if (!assistedAvailable) {
        assistedToggle.checked = false;
        return;
      }
      assistedConsent.hidden = false;
      assistedProvider.disabled = assistedProvider.options
        ? assistedProvider.options.length < 2
        : false;
      setAssistBadge(assistedBadge, "켜짐 · 전송 전", "active");
      assistedStatus.textContent =
        "켜졌지만 아직 아무것도 전송하지 않았습니다. 아래 전송 범위를 확인하고 '보강 실행'을 누르면 현재 공고문과 설정으로 한 번 요청합니다.";
      setAssistedPrivacyNotice();
      updateAssistedRunState();
      updateResultsNote();
      announceAssisted(
        "AI·현행 법령 보강을 켰습니다. 보강 실행을 누르기 전에는 아무것도 전송하지 않습니다."
      );
      return;
    }
    deactivateAssistedReview();
  });
  assistedProvider.addEventListener("change", () => {
    if (!assistedToggle.checked) return;
    assistedStatus.textContent = `AI 제공자를 ${selectedProviderLabel()}(으)로 바꿨습니다. 아직 전송하지 않았습니다. '보강 실행'을 눌러야 이 설정으로 요청합니다.`;
    markAssistedSettingsChanged("AI 제공자가 바뀌었습니다.");
  });
  assistedRun.addEventListener("click", () => {
    updateAssistedRunState();
    if (assistedRun.disabled) return;
    void runAssistedReview(input.value);
  });
  organizationSector.addEventListener("change", () => {
    const isPublic = organizationSector.value === "public";
    organizationPublicType.disabled = !isPublic;
    if (!isPublic) organizationPublicType.value = "unspecified";
  });
  [organizationSector, organizationPublicType, organizationSize].forEach((control) => {
    control.addEventListener("change", () => {
      if (latestResult) {
        renderSlots(latestResult.slots, latestResult.questions);
        renderQuestions(latestResult.questions);
      }
      // Never re-sends: an earlier AI result is only marked as outdated.
      markAssistedSettingsChanged("조직 조건이 바뀌었습니다.");
    });
  });
  clearButton.addEventListener("click", () => {
    input.value = "";
    input.dispatchEvent(new Event("input"));
    input.focus();
  });
  sampleButton.addEventListener("click", () => {
    resetReview();
    input.value = sample;
    input.dispatchEvent(new Event("input"));
    input.focus();
  });
  copyButton.addEventListener("click", async () => {
    if (!latestResult || latestCheckedText !== input.value) return;
    try {
      await navigator.clipboard.writeText(makeReport(latestResult));
      showToast("검토 메모를 복사했습니다.");
    } catch (_error) {
      const temporary = document.createElement("textarea");
      try {
        temporary.value = makeReport(latestResult);
        temporary.style.position = "fixed";
        temporary.style.opacity = "0";
        document.body.appendChild(temporary);
        temporary.select();
        if (!document.execCommand("copy")) {
          throw new Error("copy command was rejected");
        }
        showToast("검토 메모를 복사했습니다.");
      } catch (_fallbackError) {
        showToast("브라우저에서 메모를 복사하지 못했습니다.");
      } finally {
        temporary.remove();
      }
    }
  });

  comparisonReset.addEventListener("click", () => {
    if (!latestResult || latestCheckedText !== input.value) return;
    comparisonBaseline = { result: latestResult, text: latestCheckedText };
    comparison = null;
    renderComparison("현재 검토 결과를 새 비교 기준으로 삼았습니다. 다음 수정부터 이 결과와 비교합니다.");
  });

  modeEasyButton.addEventListener("click", () => setViewMode("easy", true));
  modeExpertButton.addEventListener("click", () => setViewMode("expert", true));
  easyResult.addEventListener("click", (event) => {
    const target = event.target;
    if (!target || typeof target.closest !== "function") return;
    const modeButton = target.closest("[data-switch-mode]");
    if (modeButton) {
      setViewMode(modeButton.dataset.switchMode, true);
      resultsTitle.focus();
      return;
    }
    const selectButton = target.closest("[data-select-start]");
    if (!selectButton) return;
    // Offsets belong to the checked text; after an edit they point elsewhere.
    if (latestCheckedText !== input.value) {
      showToast("공고문이 바뀌었습니다. '검토 메모 만들기'를 다시 누르면 위치가 새로 계산됩니다.");
      return;
    }
    const start = Number(selectButton.dataset.selectStart);
    const end = Number(selectButton.dataset.selectEnd);
    input.focus();
    if (typeof input.setSelectionRange === "function") {
      input.setSelectionRange(start, end);
    }
  });

  roleReviewRecord.addEventListener("click", recordRoleReviewEvent);
  roleReviewClear.addEventListener("click", clearRoleReview);
  roleReviewAction.addEventListener("change", updateRoleReviewResolutionControl);
  roleReviewNote.addEventListener("input", () =>
    setFieldError(roleReviewNote, roleReviewNoteError, "")
  );
  roleReviewEvidence.addEventListener("input", () =>
    setFieldError(roleReviewEvidence, roleReviewEvidenceError, "")
  );
  roleReviewResolveEvent.addEventListener("change", () =>
    setFieldError(roleReviewResolveEvent, roleReviewResolveError, "")
  );

  resultContent.addEventListener("input", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLTextAreaElement)) return;
    const questionId = target.dataset.questionAnswer;
    if (!questionId) return;
    reviewAnswers.set(questionId, target.value);
    updateAnswerProgress();
  });

  document.getElementById("ruleset-version").textContent =
    window.FAIRPOST_DATA.version;
  updateAssistedRunState();
  void checkAssistedAvailability();
  setViewMode(storedViewMode() || "easy", false);
})();
