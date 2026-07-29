(function () {
  "use strict";

  const input = document.getElementById("posting-input");
  const checkButton = document.getElementById("check-button");
  const clearButton = document.getElementById("clear-button");
  const sampleButton = document.getElementById("sample-button");
  const copyButton = document.getElementById("copy-button");
  const charCount = document.getElementById("char-count");
  const emptyState = document.getElementById("empty-state");
  const resultContent = document.getElementById("result-content");
  const toast = document.getElementById("toast");
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
  let latestResult = null;
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

  function showToast(message) {
    toast.textContent = message;
    toast.classList.add("visible");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("visible"), 1800);
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
              <span class="severity-tag severity-${escapeHtml(finding.severity)}">${escapeHtml(finding.severity)}</span>
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
              <pre>${escapeHtml(finding.basis.text)}</pre>
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
    return `<article class="question-item">
      <div class="item-main">
        <div class="item-meta">
          <span class="id-tag">${escapeHtml(question.id)}</span>
          <span class="dimension-tag">${escapeHtml(question.dimension)}</span>
          <span class="scope-tag">${scopeLabel}</span>
          <span>${escapeHtml(question.book_ref)}</span>
        </div>
        <p class="item-title">${escapeHtml(question.question)}</p>
        ${evidence}
        ${reference}
      </div>
      ${detail}
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
    emptyState.hidden = true;
    resultContent.hidden = false;
    copyButton.disabled = false;
  }

  function makeReport(result) {
    const lines = [
      "fairpost 채용공고문 검토 의견서",
      `규칙 사전: ${result.ruleset_version}`,
      `법령 기준일: ${result.statute_snapshot_date}`,
      result.statute_notice,
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
    visiblePostingQuestions.forEach(appendQuestion);
    lines.push("", `[공통 기본 체크리스트 ${commonQuestions.length}건]`);
    commonQuestions.forEach(appendQuestion);
    lines.push("", result.disclaimer);
    return lines.join("\n");
  }

  function runCheck() {
    if (!input.value.trim()) {
      showToast("점검할 공고문을 입력하세요.");
      input.focus();
      return;
    }
    render(window.FairpostEngine.check(input.value));
  }

  input.addEventListener("input", () => {
    charCount.textContent = `${Array.from(input.value).length.toLocaleString("ko-KR")}자`;
  });
  checkButton.addEventListener("click", runCheck);
  clearButton.addEventListener("click", () => {
    input.value = "";
    input.dispatchEvent(new Event("input"));
    input.focus();
  });
  sampleButton.addEventListener("click", () => {
    input.value = sample;
    input.dispatchEvent(new Event("input"));
    input.focus();
  });
  copyButton.addEventListener("click", async () => {
    if (!latestResult) return;
    try {
      await navigator.clipboard.writeText(makeReport(latestResult));
      showToast("검토 의견서를 복사했습니다.");
    } catch (_error) {
      const temporary = document.createElement("textarea");
      temporary.value = makeReport(latestResult);
      temporary.style.position = "fixed";
      temporary.style.opacity = "0";
      document.body.appendChild(temporary);
      temporary.select();
      document.execCommand("copy");
      temporary.remove();
      showToast("검토 의견서를 복사했습니다.");
    }
  });

  document.getElementById("ruleset-version").textContent =
    window.FAIRPOST_DATA.version;
})();
