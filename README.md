# Private Credit Monitor

A GitHub-native SEC filing monitor for private credit, direct lending, and BDC coverage. The repo scans recent EDGAR filings, filters by tracked names plus keyword hits, stores the results in JSON, and serves a newsroom-style static dashboard from the repository itself.

## What It Does

- Polls recent SEC daily index files and fetches the underlying filing text from EDGAR.
- Caches the SEC CIK lookup file locally in [`data/cik_lookup_cache.txt`](/C:/Users/6113101/Private-credit-monitor/data/cik_lookup_cache.txt) and refreshes it once a week.
- Filters by target forms such as `8-K` and `D` by default.
- Matches filings against a configurable watchlist of public and private credit entities in [`config/tracked_entities.csv`](/C:/Users/6113101/Private-credit-monitor/config/tracked_entities.csv).
- Searches filing text for keywords from [`config/keywords.txt`](/C:/Users/6113101/Private-credit-monitor/config/keywords.txt) such as `private credit`.
- Prints filing dates, company names, matched keywords, and an OpenArena-driven editorial preview.
- Writes dashboard-ready output to [`data/alerts.json`](/C:/Users/6113101/Private-credit-monitor/data/alerts.json) and [`data/status.json`](/C:/Users/6113101/Private-credit-monitor/data/status.json).

## Project Layout

- [`scripts/poll_filings.py`](/C:/Users/6113101/Private-credit-monitor/scripts/poll_filings.py): command-line entrypoint for the SEC poller.
- [`private_credit_monitor/monitor.py`](/C:/Users/6113101/Private-credit-monitor/private_credit_monitor/monitor.py): matching, EDGAR fetch, keyword filtering, state, and email logic.
- [`index.html`](/C:/Users/6113101/Private-credit-monitor/index.html): static dashboard shell.
- [`static/styles.css`](/C:/Users/6113101/Private-credit-monitor/static/styles.css): subtle editorial styling.
- [`static/app.js`](/C:/Users/6113101/Private-credit-monitor/static/app.js): JSON-driven dashboard rendering.
- [`.github/workflows/poll-filings.yml`](/C:/Users/6113101/Private-credit-monitor/.github/workflows/poll-filings.yml): scheduled GitHub Action that refreshes dashboard data.
- [`.github/workflows/refresh-cik-lookup.yml`](/C:/Users/6113101/Private-credit-monitor/.github/workflows/refresh-cik-lookup.yml): dedicated weekly/manual refresh for the SEC CIK cache.

## Local Run

Set a descriptive SEC user agent first:

```powershell
$env:SEC_USER_AGENT="Private-Credit-Monitor/1.0 your-email@example.com"
python scripts/poll_filings.py --hours-lookback 3 --forms "8-K,D,SC TO-I,SC TO-I/A"
```

Optional flags:

```powershell
python scripts/poll_filings.py --hours-lookback 3 --forms "8-K,D,SC TO-I,SC TO-I/A" --max-results 40
python scripts/poll_filings.py --days 14 --forms "8-K,D,SC TO-I,SC TO-I/A,10-Q" --keywords "private credit,direct lending" --max-results 40
```

## GitHub Hosting

This repo is designed for static hosting.

1. Push the repository to GitHub.
2. Enable GitHub Pages for the repository root.
3. Add a repository secret named `SEC_USER_AGENT`.
4. Run the `Poll SEC Filings` workflow once from the Actions tab.
5. The action will refresh `data/*.json`, commit those changes, and the dashboard will reflect the latest matches.

The scheduled poll workflow now scans a rolling `3`-hour SEC current-feed window instead of rescanning full days every run.
The SEC CIK lookup file is cached in the repo. The high-frequency `Poll SEC Filings` and `Backfill SEC Filings` workflows only read the cached copy; the dedicated `Refresh CIK Lookup` workflow is the job that refreshes it weekly or on demand.

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

Use it from `Actions -> Backfill SEC Filings -> Run workflow`, then enter the number of days you want scanned.

## Email Alert Integration

The script already includes optional SMTP delivery for new matches. To enable it in GitHub Actions, add these secrets:

- `ENABLE_EMAIL_ALERTS=true`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `FROM_EMAIL`
- `ALERT_EMAIL_TO`
- `OPENARENA_BEARER_TOKEN`

How it works:

1. Each workflow run compares fresh matches against [`data/state.json`](/C:/Users/6113101/Private-credit-monitor/data/state.json).
2. Only newly seen accession numbers are included in the email digest.
3. The email only includes the `Relevance Verdict`, `One-Line Takeaway`, and `What's New` sections, plus a direct filing link button.
4. After a successful run, the new accession numbers are stored so later runs do not re-alert on the same filing.

If you want a richer alert layer later, the clean next step is to swap SMTP for:

- AWS SES or Resend for deliverability and better analytics.
- A daily digest plus immediate alerts split by severity.
- A second workflow that fans out alerts to Slack, Teams, or other editorial channels.

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
