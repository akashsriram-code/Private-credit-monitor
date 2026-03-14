const buckets = new Map();
const WINDOW_MS = 60_000;
const MAX_EVENTS_PER_WINDOW = 120;

export function rateLimitKey(req, payload) {
  const forwarded = req.headers["x-forwarded-for"];
  const ip = Array.isArray(forwarded) ? forwarded[0] : String(forwarded || "").split(",")[0].trim();
  return `${ip || "unknown"}:${payload.session_id || "unknown"}`;
}

export function consumeRateLimit(key, now = Date.now()) {
  const existing = buckets.get(key);
  if (!existing || now - existing.windowStart >= WINDOW_MS) {
    buckets.set(key, { windowStart: now, count: 1 });
    return { allowed: true, remaining: MAX_EVENTS_PER_WINDOW - 1 };
  }

  if (existing.count >= MAX_EVENTS_PER_WINDOW) {
    return { allowed: false, remaining: 0 };
  }

  existing.count += 1;
  buckets.set(key, existing);
  return { allowed: true, remaining: MAX_EVENTS_PER_WINDOW - existing.count };
}
