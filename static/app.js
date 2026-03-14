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

async function loadJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load ${path}`);
  }
  return response.json();
}

function formatList(values) {
  return values && values.length ? values.join(", ") : "n/a";
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderList(items, className) {
  if (!Array.isArray(items) || !items.length) {
    return "<p class=\"modal-copy\">N/A</p>";
  }
  return `<ul class="${className}">${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function buildCard(filing) {
  return `
    <article
      class="filing-card"
      data-filing-card="true"
      data-accession-number="${escapeHtml(filing.accession_number)}"
      data-form-type="${escapeHtml(filing.form_type)}"
      data-tracked-name="${escapeHtml(filing.tracked_name)}"
    >
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
        <a
          class="story-link"
          href="${escapeHtml(filing.index_url)}"
          target="_blank"
          rel="noreferrer"
          data-open-filing="${escapeHtml(filing.accession_number)}"
        >Open Filing</a>
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
  window.__PCMAnalytics?.trackAnalysisModalOpen(filing);

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

async function render() {
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
  window.__PCMAnalytics?.observeFilingCards();
  window.__PCMAnalytics?.trackPageView();
}

document.getElementById("refreshButton").addEventListener("click", () => {
  window.__PCMAnalytics?.trackRefreshClick();
  render();
});
document.getElementById("searchInput").addEventListener("input", (event) => {
  const value = event.target instanceof HTMLInputElement ? event.target.value.trim() : "";
  window.__PCMAnalytics?.trackSearchUsage(value.length);
  applyFilters(window.__filings || []);
});
document.getElementById("formFilter").addEventListener("change", (event) => {
  const value = event.target instanceof HTMLSelectElement ? event.target.value : "";
  if (value) {
    window.__PCMAnalytics?.trackFormFilterChange(value);
  }
  applyFilters(window.__filings || []);
});
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
  if (target instanceof HTMLAnchorElement && target.dataset.openFiling) {
    const filing = (window.__filings || []).find((item) => item.accession_number === target.dataset.openFiling);
    if (filing) {
      window.__PCMAnalytics?.trackFilingLinkClick(filing);
    }
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeAnalysisModal();
  }
});

render().catch((error) => {
  document.getElementById("statusLine").textContent = error.message;
});
