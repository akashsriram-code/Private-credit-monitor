import { insertEvent } from "../lib/db.js";
import { consumeRateLimit, rateLimitKey } from "../lib/rate-limit.js";
import { bodySizeOkay, isAllowedOrigin, resolveAllowedOrigin, validatePayload } from "../lib/validation.js";

function json(res, status, payload) {
  res.status(status).json(payload);
}

function applyCors(req, res) {
  const allowedOrigin = resolveAllowedOrigin(req);
  if (allowedOrigin) {
    res.setHeader("Access-Control-Allow-Origin", allowedOrigin);
    res.setHeader("Vary", "Origin");
  }
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
}

function userAgentFamily(req) {
  const userAgent = String(req.headers["user-agent"] || "").toLowerCase();
  if (!userAgent) return "unknown";
  if (userAgent.includes("chrome")) return "chrome";
  if (userAgent.includes("safari") && !userAgent.includes("chrome")) return "safari";
  if (userAgent.includes("firefox")) return "firefox";
  if (userAgent.includes("edg")) return "edge";
  return "other";
}

export default async function handler(req, res) {
  applyCors(req, res);

  if (req.method === "OPTIONS") {
    return res.status(204).end();
  }

  if (req.method !== "POST") {
    res.setHeader("Allow", "POST, OPTIONS");
    return json(res, 405, { error: "Method not allowed." });
  }

  if (!isAllowedOrigin(req)) {
    return json(res, 403, { error: "Origin not allowed." });
  }

  const bodyText = typeof req.body === "string" ? req.body : JSON.stringify(req.body || {});
  if (!bodySizeOkay(bodyText)) {
    return json(res, 413, { error: "Payload too large." });
  }

  let payload;
  try {
    payload = typeof req.body === "string" ? JSON.parse(req.body || "{}") : (req.body || {});
  } catch (error) {
    return json(res, 400, { error: "Invalid JSON payload." });
  }
  const validation = validatePayload(payload);
  if (!validation.valid) {
    return json(res, 400, { error: validation.error });
  }

  const limit = consumeRateLimit(rateLimitKey(req, payload));
  if (!limit.allowed) {
    return json(res, 429, { error: "Rate limit exceeded." });
  }

  try {
    payload.meta = {
      ...(payload.meta || {}),
      user_agent_family: userAgentFamily(req),
    };
    await insertEvent(payload);
    res.status(204).end();
  } catch (error) {
    console.error("analytics_insert_failed", {
      event_name: payload.event_name,
      page_path: payload.page_path,
      accession_number: payload.meta?.accession_number || null,
      message: error instanceof Error ? error.message : String(error),
    });
    json(res, 500, { error: "Failed to record event." });
  }
}
