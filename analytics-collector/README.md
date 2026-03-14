# Analytics Collector

This folder is a separate Vercel project for invisible anonymous usage logging from the GitHub Pages dashboard.

## What It Does

- Accepts anonymous event payloads at `POST /api/collect`
- Stores events in Vercel Postgres
- Runs a daily cleanup job at `GET /api/cleanup`
- Keeps all analytics backend-facing only

## Required Environment Variables

- `POSTGRES_URL`
- `POSTGRES_PRISMA_URL`
- `POSTGRES_URL_NON_POOLING`
- `POSTGRES_USER`
- `POSTGRES_HOST`
- `POSTGRES_PASSWORD`
- `POSTGRES_DATABASE`
- `ALLOWED_ORIGINS`
  - Example: `https://akashsriram-code.github.io`
- `CRON_SECRET`
  - Optional but recommended for authenticated cleanup calls

## Setup

1. Create a new Vercel project with the root directory set to `analytics-collector`.
2. Attach a Vercel Postgres database.
3. Add the environment variables listed above.
4. Run the SQL in [`sql/schema.sql`](C:/Users/6113101/Private-credit-monitor/analytics-collector/sql/schema.sql).
5. Optionally create views from [`sql/reporting.sql`](C:/Users/6113101/Private-credit-monitor/analytics-collector/sql/reporting.sql).
6. Set the GitHub Pages frontend endpoint in the main site's hidden meta tag or global:
   - `https://<your-vercel-project>.vercel.app/api/collect`

## Payload Contract

```json
{
  "event_name": "page_view",
  "session_id": "anonymous-session-id",
  "page_path": "/Private-credit-monitor/",
  "occurred_at": "2026-03-14T18:30:00.000Z",
  "meta": {
    "referrer_domain": "www.google.com",
    "viewport_class": "desktop"
  }
}
```

## Notes

- The collector intentionally does not store named users, raw search text, or fingerprinting data.
- Origin checks and lightweight in-memory rate limiting are included for basic abuse protection.
- The frontend should fail open if the collector is unreachable.
