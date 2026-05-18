# Private Credit Monitor

A Vercel-ready SEC filing monitor for private credit, direct lending, and BDC coverage. The repo scans recent EDGAR filings, filters by tracked names plus keyword hits, stores the results in JSON, and now also supports live `10-K` / `10-Q` ask-your-document analysis through OpenArena-backed Python API routes.

## What It Does

- Polls recent SEC daily index files and fetches the underlying filing text from EDGAR.
- Caches the SEC CIK lookup file locally in [`data/cik_lookup_cache.txt`](/C:/Users/6113101/Private-credit-monitor/data/cik_lookup_cache.txt) and refreshes it once a week.
- Filters by target forms such as `8-K`, `D`, and `10-Q` by default.
- Matches filings against a configurable watchlist of public and private credit entities in [`config/tracked_entities.csv`](/C:/Users/6113101/Private-credit-monitor/config/tracked_entities.csv).
- Searches filing text for keywords from [`config/keywords.txt`](/C:/Users/6113101/Private-credit-monitor/config/keywords.txt) such as `private credit`.
- Prints filing dates, company names, matched keywords, and an OpenArena-driven editorial preview.
- Writes dashboard-ready output to [`data/alerts.json`](/C:/Users/6113101/Private-credit-monitor/data/alerts.json) and [`data/status.json`](/C:/Users/6113101/Private-credit-monitor/data/status.json).
- Runs live 10-K / 10-Q ask-your-document requests through Vercel API routes and stores session outputs in JSON-backed session history.

## Project Layout

- [`scripts/poll_filings.py`](/C:/Users/6113101/Private-credit-monitor/scripts/poll_filings.py): command-line entrypoint for the SEC poller.
- [`private_credit_monitor/monitor.py`](/C:/Users/6113101/Private-credit-monitor/private_credit_monitor/monitor.py): matching, EDGAR fetch, keyword filtering, state, and email logic.
- [`index.html`](/C:/Users/6113101/Private-credit-monitor/index.html): dashboard shell served by Vercel as a static asset.
- [`static/styles.css`](/C:/Users/6113101/Private-credit-monitor/static/styles.css): subtle editorial styling.
- [`static/app.js`](/C:/Users/6113101/Private-credit-monitor/static/app.js): dashboard rendering plus live filings-analysis requests to the backend API.
- [`private_credit_monitor/filings_analysis.py`](/C:/Users/6113101/Private-credit-monitor/private_credit_monitor/filings_analysis.py): filing selection, OpenArena request packaging, live analysis execution, and JSON session persistence for 10-K / 10-Q analysis.
- [`api/filings_analysis.py`](/C:/Users/6113101/Private-credit-monitor/api/filings_analysis.py): live `POST` endpoint that fetches filings, calls OpenArena, and returns the answer.
- [`api/filings_analysis_sessions.py`](/C:/Users/6113101/Private-credit-monitor/api/filings_analysis_sessions.py): live `GET` endpoint that returns stored analysis sessions.
- [`vercel.json`](/C:/Users/6113101/Private-credit-monitor/vercel.json): Vercel rewrites and Python function duration settings.
- [`.github/workflows/poll-filings.yml`](/C:/Users/6113101/Private-credit-monitor/.github/workflows/poll-filings.yml): scheduled GitHub Action that refreshes dashboard data.
- [`.github/workflows/refresh-cik-lookup.yml`](/C:/Users/6113101/Private-credit-monitor/.github/workflows/refresh-cik-lookup.yml): dedicated weekly/manual refresh for the SEC CIK cache.
- [`.github/workflows/send-test-email.yml`](/C:/Users/6113101/Private-credit-monitor/.github/workflows/send-test-email.yml): manual SMTP health-check email workflow.

## Local Run

Set a descriptive SEC user agent first:

```powershell
$env:SEC_USER_AGENT="Private-Credit-Monitor/1.0 your-email@example.com"
python scripts/poll_filings.py --hours-lookback 3 --forms "8-K,D,SC TO-I,SC TO-I/A,SC TO-T,SC TO-T/A,10-Q,10-Q/A"
```

Optional flags:

```powershell
python scripts/poll_filings.py --hours-lookback 3 --forms "8-K,D,SC TO-I,SC TO-I/A,SC TO-T,SC TO-T/A,10-Q,10-Q/A" --max-results 40
python scripts/poll_filings.py --days 14 --forms "8-K,D,SC TO-I,SC TO-I/A,SC TO-T,SC TO-T/A,10-Q,10-Q/A" --keywords "private credit,direct lending" --max-results 40
```

## Vercel Hosting

This repo is now designed to work well on Vercel.

1. Push the repository to GitHub.
2. Import the repo into Vercel.
3. Add these environment variables in Vercel:
   - `SEC_USER_AGENT`
   - `OPENARENA_BEARER_TOKEN`
   - `OPENARENA_FILINGS_WORKFLOW_ID` if you want to override the pinned default
   - `BLOB_READ_WRITE_TOKEN` if you want persistent live session storage in Vercel Blob
4. Deploy.
5. The static dashboard will load normally, and the filings-analysis tab will call the live Python API routes on submit.

The scheduled poll workflow now scans a rolling `3`-hour SEC current-feed window instead of rescanning full days every run.
The SEC CIK lookup file is cached in the repo. The high-frequency `Poll SEC Filings` and `Backfill SEC Filings` workflows only read the cached copy; the dedicated `Refresh CIK Lookup` workflow is the job that refreshes it weekly or on demand.

## Filings Analysis Tab

The dashboard now includes a second tab for live `10-K` / `10-Q` document analysis.

How it works:

1. Select one or more tracked entities from the existing watchlist.
2. Choose `10-K`, `10-Q`, or `Current 10-Q + latest 10-K` plus a calendar lookback count. The mixed option is useful for comparing the current quarterly filing against the latest annual filing, such as a March/May `10-Q` versus the December-year-end `10-K`.
3. Enter a free-text question.
4. The frontend `POST`s the request to `/api/filings-analysis`.
5. The backend fetches the matching filings, sends them to the OpenArena ask-your-document workflow, and returns the answer plus citations.
6. The backend stores the session in JSON history. On Vercel, this is persistent only when `BLOB_READ_WRITE_TOKEN` is configured; otherwise it falls back to the local archive for non-Vercel/local use.

The filings-analysis workflow is pinned by default to:

- `a1781c17-d09d-4ed5-b11c-032fe42052ae`

You can optionally override it with this repository secret:

- `OPENARENA_FILINGS_WORKFLOW_ID`

The live analysis backend also uses:

- `OPENARENA_BEARER_TOKEN`
- `SEC_USER_AGENT`

## Anonymous Usage Logging

The dashboard now supports an invisible Cloudflare Web Analytics integration.

- No analytics UI is shown to end users
- The frontend remains static even when served by Vercel
- No custom collector or database is required

To enable it:

1. Create a Cloudflare Web Analytics site/token in your Cloudflare dashboard.
2. Open [`index.html`](/C:/Users/6113101/Private-credit-monitor/index.html).
3. Add the Cloudflare snippet before the closing `</body>` tag:

```html
<!-- Cloudflare Web Analytics --><script defer src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{"token": "YOUR_CLOUDFLARE_TOKEN"}'></script><!-- End Cloudflare Web Analytics -->
```

4. Push to `main` and let Vercel redeploy.

## CIK Refresh Action

There is also a dedicated `Refresh CIK Lookup` GitHub Action.

- Runs weekly on Mondays
- Can also be launched manually from the Actions tab
- Only updates and commits [`data/cik_lookup_cache.txt`](/C:/Users/6113101/Private-credit-monitor/data/cik_lookup_cache.txt)

Use it from `Actions -> Refresh CIK Lookup -> Run workflow` if you want to refresh the SEC CIK mapping outside the normal polling cycle.

## Backfill Action

There is also a manual `Backfill SEC Filings` GitHub Action.

- Default backfill window: `3` days
- The person running the workflow can choose a different day count at launch time
- Email alerts stay off by default for backfills, but the workflow can send emails for newly matched backfill filings when `send_email_alerts` is set to `true`

Use it from `Actions -> Backfill SEC Filings -> Run workflow`, then enter the number of days you want scanned.

## Email Alert Integration

The script already includes optional email delivery for new matches. The simplest setup for this repo is now password-based SMTP with Zoho Mail using an app-specific password.

For password-based SMTP, add these secrets:

- `ENABLE_EMAIL_ALERTS=true`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `FROM_EMAIL`
- `ALERT_EMAIL_TO`
  comma-separated recipient list is supported, for example `a@example.com, b@example.com`
- `OPENARENA_BEARER_TOKEN`

### Zoho Mail App Password

For Zoho Mail, use password-based SMTP with an app-specific password:

- `SMTP_USERNAME=<your Zoho email address>`
- `FROM_EMAIL=<your Zoho email address or alias>`
- `SMTP_PASSWORD=<your Zoho app password>`

Use one of these SMTP settings:

- Personal Zoho mailbox:
  `SMTP_HOST=smtp.zoho.com`
  `SMTP_PORT=587`
- Zoho-hosted custom-domain mailbox:
  `SMTP_HOST=smtppro.zoho.com`
  `SMTP_PORT=587`

Notes:

- If Zoho two-factor authentication is enabled, generate an app-specific password and use that value as `SMTP_PASSWORD`.
- Port `587` with STARTTLS is the cleanest default for this repo.
- `SMTP_AUTH_METHOD` can be omitted because the code defaults to password auth.

How it works:

1. Each workflow run compares fresh matches against [`data/state.json`](/C:/Users/6113101/Private-credit-monitor/data/state.json).
2. Only newly seen accession numbers are included in the email digest.
3. The email only includes the `Relevance Verdict`, `One-Line Takeaway`, and `What's New` sections, plus a direct filing link button.
4. After a successful run, the new accession numbers are stored so later runs do not re-alert on the same filing.

If you want a richer alert layer later, the clean next step is to swap SMTP for:

- AWS SES or a fully authenticated custom-domain provider for better deliverability and analytics.
- A daily digest plus immediate alerts split by severity.
- A second workflow that fans out alerts to Slack, Teams, or other editorial channels.

## Test Email Action

There is also a manual `Send Test Email` GitHub Action.

- Uses the configured SMTP secrets
- Sends a routine health-check email to `ALERT_EMAIL_TO`
- Helps verify the email component without waiting for a live filing alert

Use it from `Actions -> Send Test Email -> Run workflow`.

## OpenArena Wiring

This repo now mirrors the ETF monitor pattern for synopsis generation.

- `OPENARENA_BASE_URL` defaults to `https://aiopenarena.thomsonreuters.com`
- `OPENARENA_WORKFLOW_ID` is pinned in the workflow to `9214a226-9866-4f29-abd3-0eb3cd235f8e`
- `OPENARENA_TIMEOUT_SECONDS` defaults to `180`
- only `OPENARENA_BEARER_TOKEN` needs to be added as a GitHub secret

When the token is present, each matched filing gets:

- a full A-through-K structured analysis
- preview fields for the dashboard and email: `Relevance Verdict`, `One-Line Takeaway`, and `What's New`
- the remainder stored in JSON and shown in a click-through modal
- a wire-priority field derived from the relevance verdict

When the token is absent, the monitor falls back to a deterministic local synopsis so the dashboard still updates cleanly.

## Notes

- SEC access should always use a real descriptive `User-Agent` with contact information.
- The current implementation uses the SEC daily index because it works well for both public issuers and many private fund entities.
- Private fund naming in EDGAR can be messy, so the matcher uses both CIK resolution and normalized-name matching. You can tune the watchlist and keywords over time as you see which issuers produce useful hits.
