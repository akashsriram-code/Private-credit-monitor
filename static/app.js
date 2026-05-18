const SECTION_LABELS = {
  most_important_points: "Most Important Points",
  why_it_matters_now: "Why It Matters Now",
  filing_details_extracted: "Filing Details Extracted",
  signals_reporters_should_notice: "Signals Reporters Should Notice",
  routine_vs_non_routine: "Routine vs. Non-Routine",
  questions_for_follow_up: "Questions for Follow-Up",
  evidence_from_the_filing: "Evidence from the Filing",
  final_newsroom_brief: "Final Newsroom Brief",
};

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderInlineMarkdown(text) {
  let html = escapeHtml(text || "");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  return html;
}

function renderMarkdown(value) {
  const normalized = String(value || "").replace(/\r\n/g, "\n").trim();
  if (!normalized) {
    return "<p>No output yet. The request may still be queued or processing.</p>";
  }

  const lines = normalized.split("\n");
  const blocks = [];
  let paragraph = [];
  let listItems = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    blocks.push(`<p>${renderInlineMarkdown(paragraph.join(" "))}</p>`);
    paragraph = [];
  };

  const flushList = () => {
    if (!listItems.length) return;
    blocks.push(`<ul>${listItems.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
    listItems = [];
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushParagraph();
      flushList();
      continue;
    }

    const headingMatch = line.match(/^(#{1,6})\s+(.*)$/);
    if (headingMatch) {
      flushParagraph();
      flushList();
      const level = Math.min(4, headingMatch[1].length + 1);
      blocks.push(`<h${level}>${renderInlineMarkdown(headingMatch[2])}</h${level}>`);
      continue;
    }

    const listMatch = line.match(/^[-*]\s+(.*)$/);
    if (listMatch) {
      flushParagraph();
      listItems.push(listMatch[1]);
      continue;
    }

    paragraph.push(line);
  }

  flushParagraph();
  flushList();
  return blocks.join("");
}

async function loadJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load ${path}`);
  }
  return response.json();
}

async function loadCsv(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load ${path}`);
  }
  return response.text();
}

function formatList(values) {
  return values && values.length ? values.join(", ") : "n/a";
}

function renderList(items, className) {
  if (!Array.isArray(items) || !items.length) {
    return "<p class=\"modal-copy\">N/A</p>";
  }
  return `<ul class="${className}">${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function parseTrackedEntitiesCsv(raw) {
  const lines = raw.trim().split(/\r?\n/);
  const [, ...rows] = lines;
  return rows
    .map((row) => row.split(","))
    .filter((parts) => parts.length >= 3)
    .map(([ticker, name, type]) => ({
      ticker: (ticker || "").trim(),
      name: (name || "").trim(),
      type: (type || "").trim(),
    }))
    .filter((entity) => entity.name);
}

function buildCard(filing) {
  return `
    <article class="filing-card">
      <div class="filing-top">
        <div>
          <h3 class="filing-title">${escapeHtml(filing.company_name)}</h3>
          <div class="tag-row">
            <span class="tag">${escapeHtml(filing.form_type)}</span>
            <span class="tag type">${escapeHtml(filing.tracked_type)}</span>
            <span class="tag type">${escapeHtml(filing.wire_recommendation || "UNKNOWN")}</span>
            <span class="tag type">${escapeHtml((filing.analysis_source || "unknown").toUpperCase())}</span>
          </div>
        </div>
        <div class="filing-meta">
          ${escapeHtml(filing.filed_date)}<br />
          Tracking: ${escapeHtml(filing.tracked_name)}
        </div>
      </div>
      <p class="section-label">Relevance Verdict</p>
      <p class="filing-description">${escapeHtml(filing.relevance_verdict || "N/A")}</p>
      <p class="section-label">One-Line Takeaway</p>
      <p class="filing-description">${escapeHtml(filing.one_line_takeaway || filing.description || "N/A")}</p>
      <p class="section-label">What's New</p>
      ${renderList(filing.whats_new || [], "preview-list")}
      <p class="filing-meta">Keywords: ${escapeHtml(formatList(filing.matched_keywords))}</p>
      ${filing.analysis_source === "fallback" && filing.openarena_error
        ? `<p class="filing-meta">OpenArena fallback: ${escapeHtml(filing.openarena_error)}</p>`
        : ""}
      <div class="link-row">
        <button class="story-link buttonish" data-open-analysis="${escapeHtml(filing.accession_number)}">Open Analysis</button>
        <a class="story-link" href="${escapeHtml(filing.index_url)}" target="_blank" rel="noreferrer">Open Filing</a>
      </div>
    </article>
  `;
}

function applyFilters(filings) {
  const search = document.getElementById("searchInput").value.toLowerCase().trim();
  const form = document.getElementById("formFilter").value;
  const visible = filings.filter((filing) => {
    const matchesForm = form === "ALL" || filing.form_type === form;
    const haystack = [
      filing.company_name,
      filing.tracked_name,
      filing.one_line_takeaway,
      filing.relevance_verdict,
      (filing.whats_new || []).join(" "),
      (filing.matched_keywords || []).join(" "),
    ]
      .join(" ")
      .toLowerCase();
    const matchesSearch = !search || haystack.includes(search);
    return matchesForm && matchesSearch;
  });

  document.getElementById("visibleCount").textContent = String(visible.length);
  const filingsEl = document.getElementById("filings");
  filingsEl.innerHTML = visible.length
    ? visible.map(buildCard).join("")
    : '<div class="empty-state">No filings match the current filters.</div>';
}

function renderModalSection(key, value) {
  const title = SECTION_LABELS[key] || key;
  const content = Array.isArray(value)
    ? renderList(value, "modal-list")
    : `<p class="modal-copy">${escapeHtml(value || "N/A")}</p>`;
  return `
    <section class="modal-section">
      <h3>${escapeHtml(title)}</h3>
      ${content}
    </section>
  `;
}

function openAnalysisModal(accessionNumber) {
  const filing = (window.__filings || []).find((item) => item.accession_number === accessionNumber);
  if (!filing) return;

  document.getElementById("modalTitle").textContent = filing.openarena_title || filing.company_name;
  const remainingSections = filing.remaining_sections || {};
  const renderedSections = Object.entries(remainingSections).map(([key, value]) => renderModalSection(key, value)).join("");
  document.getElementById("modalBody").innerHTML = `
    <section class="modal-section">
      <h3>Relevance Verdict</h3>
      <p class="modal-copy">${escapeHtml(filing.relevance_verdict || "N/A")}</p>
    </section>
    <section class="modal-section">
      <h3>One-Line Takeaway</h3>
      <p class="modal-copy">${escapeHtml(filing.one_line_takeaway || "N/A")}</p>
    </section>
    <section class="modal-section">
      <h3>What's New</h3>
      ${renderList(filing.whats_new || [], "modal-list")}
    </section>
    ${renderedSections}
    <section class="modal-section">
      <a class="story-link buttonish" href="${escapeHtml(filing.index_url)}" target="_blank" rel="noreferrer">Open Filing</a>
    </section>
  `;

  const modal = document.getElementById("analysisModal");
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
}

function closeAnalysisModal() {
  const modal = document.getElementById("analysisModal");
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
}

function switchTab(targetId) {
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tabTarget === targetId);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    const active = panel.id === targetId;
    panel.classList.toggle("active", active);
    panel.classList.toggle("hidden", !active);
  });
}

function renderEntityOptions() {
  const search = (document.getElementById("entitySearchInput").value || "").toLowerCase().trim();
  const selected = new Set(window.__analysisSelection || []);
  const entities = (window.__trackedEntities || []).filter((entity) => {
    const haystack = `${entity.name} ${entity.ticker} ${entity.type}`.toLowerCase();
    return !search || haystack.includes(search);
  });
  const container = document.getElementById("entityMultiSelect");
  container.innerHTML = entities.length
    ? entities.map((entity) => `
      <label class="entity-option">
        <input type="checkbox" data-entity-name="${escapeHtml(entity.name)}" ${selected.has(entity.name) ? "checked" : ""} />
        <p class="entity-copy">
          <strong>${escapeHtml(entity.name)}</strong><br />
          ${escapeHtml(entity.type)}${entity.ticker ? ` · ${escapeHtml(entity.ticker)}` : ""}
        </p>
      </label>
    `).join("")
    : '<div class="empty-state">No tracked entities match the current filter.</div>';
  document.getElementById("entitySelectionSummary").textContent = selected.size
    ? `${selected.size} tracked entit${selected.size === 1 ? "y" : "ies"} selected.`
    : "No entities selected.";
}

function buildAnalysisRequestPayload(options = {}) {
  const includeEmail = options.includeEmail !== false;
  const selected = Array.from(window.__analysisSelection || []);
  const payload = {
    entities: selected,
    filing_type: document.getElementById("analysisFormType").value,
    lookback_count: Number(document.getElementById("analysisLookbackCount").value || 1),
    question: document.getElementById("analysisQuestion").value.trim(),
  };
  if (includeEmail) {
    payload.email = document.getElementById("analysisEmail").value.trim();
  }
  return payload;
}

function filingTypeLabel(value) {
  if (value === "10-Q+10-K") {
    return "Current 10-Q + latest 10-K";
  }
  return value || "N/A";
}

function updateFilingTypeHelper() {
  const helper = document.getElementById("analysisFilingTypeHelp");
  const select = document.getElementById("analysisFormType");
  if (!helper || !select) return;
  helper.textContent = select.value === "10-Q+10-K"
    ? "Includes the selected number of recent 10-Q periods plus the latest 10-K, useful for comparing a current quarter against the December annual filing."
    : "Uses only the selected filing type across the calendar lookback.";
}

function updateIssuePreview() {
  const previewEl = document.getElementById("issuePreview");
  if (previewEl) {
    previewEl.textContent = JSON.stringify(buildAnalysisRequestPayload(), null, 2);
  }
  updateFilingTypeHelper();
}

function renderProgressLog(lines) {
  const logEl = document.getElementById("analysisProgressLog");
  const safeLines = Array.isArray(lines) ? lines.filter(Boolean) : [];
  logEl.textContent = safeLines.length ? safeLines.join("\n") : "No live analysis run yet.";
}

function buildPendingProgressLog(payload) {
  const entityLabel = payload.entities.length === 1 ? payload.entities[0] : `${payload.entities.length} tracked entities`;
  return [
    `Starting run for ${entityLabel}.`,
    `Fetching matching ${filingTypeLabel(payload.filing_type)} filings from SEC for the selected lookback.`,
    "Converting fetched filings into uploadable PDFs.",
    "Requesting OpenArena upload URLs.",
    "Uploading filing PDFs to OpenArena.",
    "Parsing uploaded documents for workflow use.",
    "Waiting for OpenArena inference output.",
  ];
}

async function submitLiveAnalysisRequest() {
  const payload = buildAnalysisRequestPayload();
  if (!payload.entities.length) {
    document.getElementById("analysisStatusLine").textContent = "Select at least one tracked entity before running analysis.";
    renderProgressLog(["Run blocked: select at least one tracked entity."]);
    return;
  }
  if (!payload.question) {
    document.getElementById("analysisStatusLine").textContent = "Add a free-text question before running analysis.";
    renderProgressLog(["Run blocked: add a free-text question before starting analysis."]);
    return;
  }
  if (payload.email && !document.getElementById("analysisEmail").checkValidity()) {
    document.getElementById("analysisStatusLine").textContent = "Enter a valid email address or leave it blank.";
    renderProgressLog(["Run blocked: the email address is not valid."]);
    return;
  }

  document.getElementById("analysisStatusLine").textContent = "Running live analysis. The server is fetching filings and calling OpenArena now...";
  renderProgressLog(buildPendingProgressLog(payload));
  const response = await fetch("/api/filings-analysis", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Live analysis request failed.");
  }
  const session = data.session;
  window.__analysisSessions = [session, ...(window.__analysisSessions || []).filter((item) => item.id !== session.id)];
  renderAnalysisSessions(window.__analysisSessions);
  if (data.email_status === "sent") {
    document.getElementById("analysisStatusLine").textContent = "Analysis complete. The latest result has been added to the session history and emailed.";
  } else if (data.email_status === "failed") {
    document.getElementById("analysisStatusLine").textContent = `Analysis complete, but email delivery failed: ${data.email_error || "Unknown email error."}`;
  } else {
    document.getElementById("analysisStatusLine").textContent = "Analysis complete. The latest result has been added to the session history.";
  }
  renderProgressLog(session.progress_log || []);
}

async function copyIssueBody() {
  await navigator.clipboard.writeText(JSON.stringify(buildAnalysisRequestPayload({ includeEmail: false }), null, 2));
  document.getElementById("analysisStatusLine").textContent = "Request JSON copied.";
}

function buildCitationCard(citation) {
  return `
    <article class="citation-card">
      <div class="tag-row">
        <span class="tag">${escapeHtml(citation.filing_type)}</span>
        <span class="tag type">${escapeHtml(citation.entity_name)}</span>
        <span class="tag type">${escapeHtml(citation.filed_date)}</span>
      </div>
      <p>${escapeHtml(citation.excerpt || "No excerpt captured.")}</p>
      <div class="link-row">
        <a class="story-link" href="${escapeHtml(citation.index_url)}" target="_blank" rel="noreferrer">Open Filing</a>
      </div>
    </article>
  `;
}

function buildSessionCard(session) {
  const citations = Array.isArray(session.citations) ? session.citations : [];
  const filings = Array.isArray(session.filings) ? session.filings : [];
  return `
    <article class="analysis-card">
      <div class="filing-top">
        <div>
          <h3 class="filing-title">${escapeHtml(session.issue_title || session.id)}</h3>
          <div class="tag-row">
            <span class="status-pill ${escapeHtml(session.status || "queued")}">${escapeHtml(session.status || "queued")}</span>
            <span class="tag">${escapeHtml(filingTypeLabel(session.filing_type))}</span>
            <span class="tag type">${escapeHtml(`${session.lookback_count || 0} period(s)`)}</span>
            <span class="tag type">${escapeHtml((session.model || "unknown").toUpperCase())}</span>
          </div>
        </div>
        <div class="filing-meta">
          Created ${escapeHtml(session.created_at || "N/A")}<br />
          ${session.completed_at ? `Completed ${escapeHtml(session.completed_at)}` : "Awaiting workflow result"}
        </div>
      </div>
      <p class="section-label">Entities</p>
      <p class="filing-description">${escapeHtml((session.entities || []).join(", ") || "N/A")}</p>
      <p class="section-label">Question</p>
      <p class="filing-description">${escapeHtml(session.question || "N/A")}</p>
      <p class="section-label">Workflow Result</p>
      <div class="analysis-answer markdown-output">${renderMarkdown(session.answer || session.error || "No output yet. The request may still be queued or processing.")}</div>
      <p class="filing-meta">${filings.length} filing(s) attached${session.workflow_id ? ` · Workflow ${escapeHtml(session.workflow_id)}` : ""}</p>
      ${citations.length ? `<div class="citation-list">${citations.map(buildCitationCard).join("")}</div>` : ""}
      <div class="link-row">
        ${session.issue_url ? `<a class="story-link" href="${escapeHtml(session.issue_url)}" target="_blank" rel="noreferrer">Open Issue</a>` : ""}
      </div>
    </article>
  `;
}

function renderAnalysisSessions(sessions) {
  window.__analysisSessions = sessions;
  document.getElementById("analysisStatusLine").textContent = sessions.length
    ? `Loaded ${sessions.length} analysis session(s) from the live API session store.`
    : "No analysis sessions found yet. Run the first live filings analysis from the form.";
  document.getElementById("analysisSessions").innerHTML = sessions.length
    ? sessions.map(buildSessionCard).join("")
    : '<div class="empty-state">No filing analysis sessions have been created yet.</div>';
  renderProgressLog(sessions.length ? sessions[0].progress_log || [] : []);
}

async function renderMonitor() {
  const [status, filings] = await Promise.all([
    loadJson("data/status.json"),
    loadJson("data/alerts.json"),
  ]);

  window.__filings = filings;
  document.getElementById("trackedCount").textContent = String(status.entities_tracked || 0);
  document.getElementById("filingCount").textContent = String(status.total_alerts || 0);
  document.getElementById("newCount").textContent = String(status.new_alerts || 0);
  document.getElementById("formCount").textContent = String((status.forms || []).length);
  document.getElementById("statusChip").textContent = status.last_run
    ? `Updated ${new Date(status.last_run).toLocaleString()}`
    : "Awaiting data";
  document.getElementById("statusLine").textContent =
    `Scanned ${status.recent_entries_scanned || 0} recent SEC entries${status.hours_lookback ? ` across the last ${status.hours_lookback} hour(s)` : ` across ${status.days_scanned || 0} day(s)`}. `
    + `Forms: ${formatList(status.forms || [])}. `
    + `Keywords: ${formatList(status.keywords || [])}. `
    + `Archive: ${filings.length} matched filing(s) shown. `
    + `CIK lookup: ${escapeHtml(status.cik_lookup_source || "unknown")}${status.cik_lookup_age_days != null ? ` (${status.cik_lookup_age_days} day(s) old)` : ""}. `
    + `OpenArena: ${status.openarena_enabled ? `on (${status.openarena_workflow_id}); generated=${status.openarena_generated || 0}, fallback=${status.fallback_generated || 0}` : "fallback mode"}. `
    + (status.last_error ? `Last error: ${status.last_error}` : "System healthy.");

  const formFilter = document.getElementById("formFilter");
  const uniqueForms = ["ALL", ...new Set(filings.map((filing) => filing.form_type))];
  formFilter.innerHTML = uniqueForms
    .map((form) => `<option value="${form}">${form === "ALL" ? "All forms" : form}</option>`)
    .join("");
  applyFilters(filings);
}

async function renderAnalysis() {
  const [sessions, trackedEntityCsv] = await Promise.all([
    fetch("/api/filings-analysis-sessions", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("Failed to load live analysis sessions.");
        }
        return response.json();
      })
      .then((payload) => payload.sessions || [])
      .catch(() => loadJson("data/filing_analysis_sessions.json")),
    loadCsv("config/tracked_entities.csv"),
  ]);
  window.__trackedEntities = parseTrackedEntitiesCsv(trackedEntityCsv);
  window.__analysisSelection = window.__analysisSelection || [];
  renderEntityOptions();
  updateIssuePreview();
  renderAnalysisSessions(Array.isArray(sessions) ? sessions : []);
}

async function renderAll() {
  await Promise.all([renderMonitor(), renderAnalysis()]);
}

document.getElementById("refreshButton").addEventListener("click", renderMonitor);
document.getElementById("refreshAnalysisButton").addEventListener("click", renderAnalysis);
document.getElementById("searchInput").addEventListener("input", () => applyFilters(window.__filings || []));
document.getElementById("formFilter").addEventListener("change", () => applyFilters(window.__filings || []));
document.getElementById("closeModalButton").addEventListener("click", closeAnalysisModal);
document.getElementById("analysisModal").addEventListener("click", (event) => {
  const target = event.target;
  if (target instanceof HTMLElement && target.dataset.closeModal === "true") {
    closeAnalysisModal();
  }
});
document.getElementById("filings").addEventListener("click", (event) => {
  const target = event.target;
  if (target instanceof HTMLElement && target.dataset.openAnalysis) {
    openAnalysisModal(target.dataset.openAnalysis);
  }
});
document.getElementById("entitySearchInput").addEventListener("input", renderEntityOptions);
document.getElementById("entityMultiSelect").addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLInputElement) || !target.dataset.entityName) return;
  const selection = new Set(window.__analysisSelection || []);
  if (target.checked) {
    selection.add(target.dataset.entityName);
  } else {
    selection.delete(target.dataset.entityName);
  }
  window.__analysisSelection = Array.from(selection).sort();
  renderEntityOptions();
  updateIssuePreview();
});
document.getElementById("analysisRequestForm").addEventListener("submit", (event) => {
  event.preventDefault();
  submitLiveAnalysisRequest().catch((error) => {
    document.getElementById("analysisStatusLine").textContent = error.message;
    renderProgressLog([`Run failed: ${error.message}`]);
  });
});
document.getElementById("copyIssueButton").addEventListener("click", () => {
  copyIssueBody().catch((error) => {
    document.getElementById("analysisStatusLine").textContent = error.message;
    renderProgressLog([`Copy failed: ${error.message}`]);
  });
});
document.getElementById("analysisFormType").addEventListener("change", () => {
  updateIssuePreview();
  updateFilingTypeHelper();
});
document.getElementById("analysisLookbackCount").addEventListener("input", updateIssuePreview);
document.getElementById("analysisQuestion").addEventListener("input", updateIssuePreview);
document.querySelectorAll(".tab-button").forEach((button) => {
  button.addEventListener("click", () => switchTab(button.dataset.tabTarget));
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeAnalysisModal();
  }
});

renderAll().catch((error) => {
  document.getElementById("statusLine").textContent = error.message;
  document.getElementById("analysisStatusLine").textContent = error.message;
  renderProgressLog([`Page load failed: ${error.message}`]);
});
