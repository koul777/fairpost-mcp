(function () {
  "use strict";

  const input = document.getElementById("posting-input");
  const checkButton = document.getElementById("check-button");
  const clearButton = document.getElementById("clear-button");
  const sampleButton = document.getElementById("sample-button");
  const copyButton = document.getElementById("copy-button");
  const assistedToggle = document.getElementById("assisted-review-toggle");
  const assistedStatus = document.getElementById("assisted-review-status");
  const assistedBadge = document.getElementById("assisted-review-badge");
  const assistedPanel = document.getElementById("assisted-review-panel");
  const assistedResultStatus = document.getElementById(
    "assisted-review-result-status"
  );
  const assistedNotice = document.getElementById("assisted-review-notice");
  const assistedOutput = document.getElementById("assisted-review-output");
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
  let latestResult = null;
  let latestAssistedReview = null;
  let assistedRequestSequence = 0;
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

  function showToast(message) {
    toast.textContent = message;
    toast.classList.add("visible");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("visible"), 1800);
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

  function resetAssistedReviewPanel() {
    assistedRequestSequence += 1;
    latestAssistedReview = null;
    assistedPanel.hidden = true;
    assistedNotice.textContent = "";
    assistedOutput.textContent = "";
    setAssistBadge(assistedResultStatus, "대기");
  }

  function setLocalPrivacyNotice() {
    privacyMessage.textContent =
      "AI·현행 법령 보강을 켜기 전에는 입력ㆍ답변이 이 브라우저 밖으로 전송되지 않습니다";
  }

  function setAssistedPrivacyNotice() {
    privacyMessage.textContent =
      "AI·현행 법령 보강 켜짐: 실행 시 공고문이 FairPost 서버에서 처리되고 선별 근거가 설정된 외부 서비스로 전송됩니다";
  }

  async function responseJson(response) {
    try {
      return await response.json();
    } catch (_error) {
      return {};
    }
  }

  async function activateAssistedReview() {
    const requestId = ++assistedRequestSequence;
    assistedStatus.textContent = "서버의 AI API와 Korean Law MCP 설정을 확인하고 있습니다.";
    setAssistBadge(assistedBadge, "확인 중");
    try {
      const response = await fetch("/api/assisted-review", {
        method: "GET",
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      const capability = await responseJson(response);
      if (requestId !== assistedRequestSequence || !assistedToggle.checked) return;
      if (!response.ok || capability.ready !== true) {
        throw new Error(capability.reason || "보강 검토가 설정되지 않았습니다.");
      }
      setAssistBadge(assistedBadge, "켜짐", "active");
      assistedStatus.textContent = capability.privacy;
      setAssistedPrivacyNotice();
      showToast("AI·현행 법령 보강을 활성화했습니다.");
      if (latestResult && input.value.trim()) {
        void runAssistedReview(input.value);
      }
    } catch (error) {
      if (requestId !== assistedRequestSequence) return;
      assistedToggle.checked = false;
      const message = error instanceof Error ? error.message : "연결을 확인하지 못했습니다.";
      assistedStatus.textContent = `사용할 수 없음: ${message}`;
      setAssistBadge(assistedBadge, "미설정", "error");
      setLocalPrivacyNotice();
      showToast("AI·현행 법령 보강 설정이 필요합니다.");
    }
  }

  async function runAssistedReview(text) {
    const requestId = ++assistedRequestSequence;
    latestAssistedReview = null;
    assistedPanel.hidden = false;
    assistedNotice.textContent =
      "Korean Law MCP에서 현행 조문을 확인한 뒤 AI 검토 메모를 작성하고 있습니다.";
    assistedOutput.textContent = "";
    setAssistBadge(assistedResultStatus, "검토 중", "active");
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
          text,
          organization_profile: currentOrganizationProfile(),
        }),
      });
      const result = await responseJson(response);
      if (requestId !== assistedRequestSequence || !assistedToggle.checked) return;
      if (!response.ok) {
        throw new Error(result.reason || result.error || "보강 검토 요청이 실패했습니다.");
      }
      latestAssistedReview = result;
      assistedNotice.textContent = `${result.notice} 현행 조문 ${result.current_articles_retrieved || 0}건을 확인했습니다.`;
      if (result.status === "completed" && result.summary) {
        assistedOutput.textContent = result.summary;
        setAssistBadge(assistedResultStatus, "완료", "active");
        showToast("AI·현행 법령 보강 검토를 완료했습니다.");
      } else if (result.status === "no_findings") {
        assistedOutput.textContent =
          "로컬 규칙에서 현행 법령을 추가 조회할 표현 후보가 확인되지 않아 AI API를 호출하지 않았습니다.";
        setAssistBadge(assistedResultStatus, "호출 안 함");
      } else if (result.status === "law_lookup_unavailable") {
        assistedOutput.textContent =
          "Korean Law MCP에서 현행 조문을 확보하지 못해 AI API를 호출하지 않았습니다.";
        setAssistBadge(assistedResultStatus, "법령 확인 실패", "error");
      } else {
        assistedOutput.textContent = result.notice || "보강 검토를 완료하지 못했습니다.";
        setAssistBadge(assistedResultStatus, "AI 확인 실패", "error");
      }
    } catch (error) {
      if (requestId !== assistedRequestSequence) return;
      const message = error instanceof Error ? error.message : "보강 검토를 완료하지 못했습니다.";
      assistedNotice.textContent = "로컬 검토 결과는 그대로 사용할 수 있습니다.";
      assistedOutput.textContent = message;
      setAssistBadge(assistedResultStatus, "연결 실패", "error");
    }
  }

  function resetReview() {
    latestResult = null;
    resetAssistedReviewPanel();
    reviewAnswers.clear();
    ["findings-list", "slots-list", "questions-list"].forEach((id) =>
      document.getElementById(id).replaceChildren()
    );
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
              <span class="severity-tag severity-${escapeHtml(finding.severity)}" aria-label="검토 우선도 ${escapeHtml(reviewPriorityLabel(finding.severity))}">${escapeHtml(reviewPriorityLabel(finding.severity))}</span>
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
    updateAnswerProgress();
    emptyState.hidden = true;
    resultContent.hidden = false;
    copyButton.disabled = false;
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
      lines.push(
        "",
        "[AI·현행 법령 보강 검토]",
        latestAssistedReview.notice,
        latestAssistedReview.summary
      );
    }
    return lines.join("\n");
  }

  function runCheck() {
    if (!input.value.trim()) {
      showToast("검토할 공고문을 입력하세요.");
      input.focus();
      return;
    }
    reviewAnswers.clear();
    resetAssistedReviewPanel();
    render(window.FairpostEngine.check(input.value));
    resultsTitle.focus();
    if (assistedToggle.checked) {
      void runAssistedReview(input.value);
    }
  }

  input.addEventListener("input", () => {
    charCount.textContent = `${Array.from(input.value).length.toLocaleString("ko-KR")}자`;
    if (!input.value.trim()) {
      resetReview();
    }
  });
  checkButton.addEventListener("click", runCheck);
  assistedToggle.addEventListener("change", () => {
    if (assistedToggle.checked) {
      void activateAssistedReview();
      return;
    }
    resetAssistedReviewPanel();
    assistedStatus.textContent =
      "기본 검사는 브라우저에서만 실행됩니다. 켜면 다음 검사부터 설정된 Korean Law MCP와 AI API를 함께 사용합니다.";
    setAssistBadge(assistedBadge, "꺼짐");
    setLocalPrivacyNotice();
  });
  organizationSector.addEventListener("change", () => {
    const isPublic = organizationSector.value === "public";
    organizationPublicType.disabled = !isPublic;
    if (!isPublic) organizationPublicType.value = "unspecified";
  });
  [organizationSector, organizationPublicType, organizationSize].forEach((control) => {
    control.addEventListener("change", () => {
      if (!latestResult) return;
      renderSlots(latestResult.slots, latestResult.questions);
      renderQuestions(latestResult.questions);
      if (assistedToggle.checked) {
        resetAssistedReviewPanel();
        void runAssistedReview(input.value);
      }
    });
  });
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
})();
