export const MAX_BODY_BYTES = 4096;
export const RAW_RETENTION_DAYS = 90;
export const ALLOWED_EVENT_NAMES = new Set([
  "page_view",
  "refresh_click",
  "search_used",
  "form_filter_changed",
  "filing_card_viewed",
  "analysis_modal_opened",
  "filing_link_clicked",
]);

export const ALLOWED_META_KEYS = new Set([
  "accession_number",
  "form_type",
  "tracked_name",
  "referrer_domain",
  "viewport_class",
  "search_length_bucket",
  "user_agent_family",
]);
