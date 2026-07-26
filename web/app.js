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

  function renderSlots(slots) {
    const missing = slots.filter((slot) => !slot.found);
    const container = document.getElementById("slots-list");
    if (!missing.length) {
      container.innerHTML =
        '<div class="none-item">11개 안내 항목이 공고문에서 모두 확인되었습니다.</div>';
      return;
    }
    container.innerHTML = missing
      .map(
        (slot) => `<div class="slot-item">
          <strong>${escapeHtml(slot.label)}</strong>
          <span>공고문에서 확인되지 않았습니다</span>
        </div>`
      )
      .join("");
  }

  function renderQuestions(questions) {
    const container = document.getElementById("questions-list");
    if (!questions.length) {
      container.innerHTML =
        '<div class="none-item">현재 규칙에 연결된 추가 검토 질문이 확인되지 않았습니다.</div>';
      return;
    }
    container.innerHTML = questions
      .map((question) => {
        const followUp = question.follow_up.length
          ? `<p class="follow-up">${question.follow_up
              .map((item) => `· ${escapeHtml(item)}`)
              .join("<br>")}</p>`
          : "";
        return `<article class="question-item">
          <div class="item-main">
            <div class="item-meta">
              <span class="id-tag">${escapeHtml(question.id)}</span>
              <span class="dimension-tag">${escapeHtml(question.dimension)}</span>
              <span>${escapeHtml(question.book_ref)}</span>
            </div>
            <p class="item-title">${escapeHtml(question.question)}</p>
            ${followUp}
          </div>
        </article>`;
      })
      .join("");
  }

  function render(result) {
    latestResult = result;
    document.getElementById("finding-count").textContent = result.counts.findings;
    document.getElementById("missing-count").textContent = result.counts.not_found;
    document.getElementById("question-count").textContent = result.counts.questions;
    document.getElementById("disclaimer").textContent =
      `${result.statute_notice} ${result.disclaimer}`;
    renderFindings(result.findings);
    renderSlots(result.slots);
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
    lines.push("", `[확인되지 않은 항목 ${result.counts.not_found}건]`);
    result.slots
      .filter((slot) => !slot.found)
      .forEach((slot) =>
        lines.push(`- ${slot.label}: 공고문에서 확인되지 않았습니다.`)
      );
    lines.push("", `[함께 생각해 볼 질문 ${result.counts.questions}건]`);
    result.questions.forEach((question) => {
      lines.push(`- ${question.id} ${question.question}`);
      question.follow_up.forEach((item) => lines.push(`  · ${item}`));
    });
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
