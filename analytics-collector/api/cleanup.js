import { RAW_RETENTION_DAYS } from "../lib/constants.js";
import { cleanupExpiredEvents } from "../lib/db.js";

function authorized(req) {
  const cronSecret = (process.env.CRON_SECRET || "").trim();
  const authHeader = String(req.headers.authorization || "");
  const hasCronHeader = Boolean(req.headers["x-vercel-cron"]);

  if (cronSecret && authHeader === `Bearer ${cronSecret}`) {
    return true;
  }
  return hasCronHeader;
}

export default async function handler(req, res) {
  if (req.method !== "GET") {
    res.setHeader("Allow", "GET");
    return res.status(405).json({ error: "Method not allowed." });
  }
  if (!authorized(req)) {
    return res.status(401).json({ error: "Unauthorized." });
  }

  try {
    const deleted = await cleanupExpiredEvents(RAW_RETENTION_DAYS);
    return res.status(200).json({ deleted });
  } catch (error) {
    console.error("analytics_cleanup_failed", {
      message: error instanceof Error ? error.message : String(error),
    });
    return res.status(500).json({ error: "Cleanup failed." });
  }
}
