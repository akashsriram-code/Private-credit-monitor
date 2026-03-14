import { ALLOWED_EVENT_NAMES, ALLOWED_META_KEYS, MAX_BODY_BYTES } from "./constants.js";

function allowedOrigins() {
  return (process.env.ALLOWED_ORIGINS || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
}

export function resolveAllowedOrigin(req) {
  const configured = allowedOrigins();
  const origin = String(req.headers.origin || "");
  if (!configured.length) {
    return origin || "*";
  }
  return configured.includes(origin) ? origin : "";
}

export function isAllowedOrigin(req) {
  return Boolean(resolveAllowedOrigin(req));
}

export function bodySizeOkay(bodyText) {
  return Buffer.byteLength(bodyText || "", "utf8") <= MAX_BODY_BYTES;
}

export function validatePayload(payload) {
  if (!payload || typeof payload !== "object") {
    return { valid: false, error: "Payload must be an object." };
  }

  const { event_name, session_id, page_path, occurred_at, meta } = payload;
  if (!ALLOWED_EVENT_NAMES.has(event_name)) {
    return { valid: false, error: "Unsupported event name." };
  }
  if (!session_id || typeof session_id !== "string" || session_id.length > 128) {
    return { valid: false, error: "Invalid session id." };
  }
  if (!page_path || typeof page_path !== "string" || !page_path.startsWith("/") || page_path.length > 256) {
    return { valid: false, error: "Invalid page path." };
  }
  const occurredAtDate = new Date(occurred_at);
  if (!occurred_at || Number.isNaN(occurredAtDate.getTime())) {
    return { valid: false, error: "Invalid occurred_at timestamp." };
  }
  if (meta === null || meta === undefined || typeof meta !== "object" || Array.isArray(meta)) {
    return { valid: false, error: "Invalid meta payload." };
  }

  for (const key of Object.keys(meta)) {
    if (!ALLOWED_META_KEYS.has(key)) {
      return { valid: false, error: `Unsupported meta key: ${key}` };
    }
    const value = meta[key];
    if (value !== null && value !== undefined && typeof value !== "string") {
      return { valid: false, error: `Meta value for ${key} must be a string.` };
    }
    if (typeof value === "string" && value.length > 256) {
      return { valid: false, error: `Meta value for ${key} is too long.` };
    }
  }

  return { valid: true };
}
