(function () {
  const endpointMeta = document.querySelector('meta[name="pcm-analytics-endpoint"]');
  const endpoint = (window.__PCM_ANALYTICS_ENDPOINT__ || endpointMeta?.content || "").trim();
  const sessionKey = "pcm_analytics_session_id";
  const viewedKey = "pcm_analytics_viewed_accessions";
  const viewedAccessions = new Set();
  let pageViewTracked = false;
  let searchTimer = null;
  let observer = null;

  if (!endpoint || !window.sessionStorage) {
    window.__PCMAnalytics = createNoopAnalytics();
    return;
  }

  try {
    const existingViewed = sessionStorage.getItem(viewedKey);
    if (existingViewed) {
      JSON.parse(existingViewed).forEach((value) => viewedAccessions.add(value));
    }
  } catch (error) {
    // Fail open: analytics state should never break page rendering.
  }

  function createNoopAnalytics() {
    const noop = () => {};
    return {
      trackPageView: noop,
      trackRefreshClick: noop,
      trackSearchUsage: noop,
      trackFormFilterChange: noop,
      trackAnalysisModalOpen: noop,
      trackFilingLinkClick: noop,
      observeFilingCards: noop,
    };
  }

  function generateSessionId() {
    if (window.crypto?.randomUUID) {
      return window.crypto.randomUUID();
    }
    return `pcm-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function getSessionId() {
    const existing = sessionStorage.getItem(sessionKey);
    if (existing) {
      return existing;
    }
    const next = generateSessionId();
    sessionStorage.setItem(sessionKey, next);
    return next;
  }

  function viewportClass() {
    return window.innerWidth < 768 ? "mobile" : "desktop";
  }

  function referrerDomain() {
    try {
      return document.referrer ? new URL(document.referrer).hostname : "";
    } catch (error) {
      return "";
    }
  }

  function persistViewedAccessions() {
    try {
      sessionStorage.setItem(viewedKey, JSON.stringify([...viewedAccessions]));
    } catch (error) {
      // Ignore storage failures.
    }
  }

  function sanitizeMeta(meta) {
    const cleaned = {};
    Object.entries(meta || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === "") {
        return;
      }
      cleaned[key] = value;
    });
    return cleaned;
  }

  function sendPayload(payload) {
    const body = JSON.stringify(payload);
    try {
      if (navigator.sendBeacon) {
        const blob = new Blob([body], { type: "application/json" });
        const sent = navigator.sendBeacon(endpoint, blob);
        if (sent) {
          return;
        }
      }
    } catch (error) {
      // Fall back to keepalive fetch below.
    }

    fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
      mode: "cors",
    }).catch(() => {
      // Fail open.
    });
  }

  function track(eventName, meta) {
    sendPayload({
      event_name: eventName,
      session_id: getSessionId(),
      page_path: window.location.pathname,
      occurred_at: new Date().toISOString(),
      meta: sanitizeMeta({
        ...meta,
        referrer_domain: referrerDomain(),
        viewport_class: viewportClass(),
      }),
    });
  }

  function searchLengthBucket(length) {
    if (length <= 0) return "0";
    if (length <= 3) return "1-3";
    if (length <= 10) return "4-10";
    return "10+";
  }

  function trackPageView() {
    if (pageViewTracked) {
      return;
    }
    pageViewTracked = true;
    track("page_view", {});
  }

  function trackRefreshClick() {
    track("refresh_click", {});
  }

  function trackSearchUsage(length) {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      track("search_used", { search_length_bucket: searchLengthBucket(length) });
    }, 500);
  }

  function trackFormFilterChange(formType) {
    track("form_filter_changed", { form_type: formType });
  }

  function trackAnalysisModalOpen(filing) {
    track("analysis_modal_opened", {
      accession_number: filing?.accession_number,
      form_type: filing?.form_type,
      tracked_name: filing?.tracked_name,
    });
  }

  function trackFilingLinkClick(filing) {
    track("filing_link_clicked", {
      accession_number: filing?.accession_number,
      form_type: filing?.form_type,
      tracked_name: filing?.tracked_name,
    });
  }

  function observeFilingCards() {
    if (observer) {
      observer.disconnect();
    }
    observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) {
            return;
          }
          const target = entry.target;
          const accessionNumber = target.getAttribute("data-accession-number");
          if (!accessionNumber || viewedAccessions.has(accessionNumber)) {
            observer.unobserve(target);
            return;
          }
          viewedAccessions.add(accessionNumber);
          persistViewedAccessions();
          track("filing_card_viewed", {
            accession_number: accessionNumber,
            form_type: target.getAttribute("data-form-type") || "",
            tracked_name: target.getAttribute("data-tracked-name") || "",
          });
          observer.unobserve(target);
        });
      },
      { threshold: 0.5 }
    );

    document.querySelectorAll("[data-filing-card='true']").forEach((card) => observer.observe(card));
  }

  window.__PCMAnalytics = {
    trackPageView,
    trackRefreshClick,
    trackSearchUsage,
    trackFormFilterChange,
    trackAnalysisModalOpen,
    trackFilingLinkClick,
    observeFilingCards,
  };

  window.addEventListener("load", trackPageView, { once: true });
})();
