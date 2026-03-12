from __future__ import annotations

import argparse
import csv
import json
import os
import re
import smtplib
import ssl
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from private_credit_monitor.synopsis_output import format_email_html, format_email_text, parse_openarena_output


ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT_DIR / "config"
DATA_DIR = ROOT_DIR / "data"
STATE_PATH = DATA_DIR / "state.json"
ALERTS_PATH = DATA_DIR / "alerts.json"
STATUS_PATH = DATA_DIR / "status.json"
TRACKED_ENTITIES_PATH = CONFIG_DIR / "tracked_entities.csv"
KEYWORDS_PATH = CONFIG_DIR / "keywords.txt"

DEFAULT_FORMS = ["8-K", "D"]
DEFAULT_DAYS = 7
DEFAULT_MAX_RESULTS = 80
DEFAULT_OPENARENA_BASE_URL = "https://aiopenarena.thomsonreuters.com"
DEFAULT_OPENARENA_WORKFLOW_ID = "9214a226-9866-4f29-abd3-0eb3cd235f8e"
COMMON_SUFFIXES = {
    "inc",
    "corp",
    "corporation",
    "company",
    "co",
    "limited",
    "ltd",
    "llc",
    "lp",
    "fund",
    "trust",
    "class",
    "series",
}


@dataclass
class TrackedEntity:
    ticker: str
    name: str
    entity_type: str
    normalized_name: str
    reduced_name: str
    ciks: set[str] = field(default_factory=set)


@dataclass
class FilingMatch:
    accession_number: str
    cik: str
    company_name: str
    form_type: str
    filed_date: str
    filing_url: str
    index_url: str
    tracked_name: str
    tracked_type: str
    matched_keywords: list[str]
    description: str
    openarena_output: str
    openarena_title: str
    relevance_verdict: str
    one_line_takeaway: str
    whats_new: list[str]
    remaining_sections: dict[str, Any]
    wire_recommendation: str
    analysis_source: str
    openarena_error: str | None
    source: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_name(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return normalize_whitespace(cleaned)


def reduce_name(value: str) -> str:
    tokens = [token for token in normalize_name(value).split() if token not in COMMON_SUFFIXES]
    return " ".join(tokens)


def normalize_form(value: str) -> str:
    return value.upper().replace("FORM ", "").strip()


def normalize_filed_date(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value.replace("/", "-")


def quarter_for_day(day: date) -> int:
    return ((day.month - 1) // 3) + 1


def master_index_url(day: date) -> str:
    return f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{quarter_for_day(day)}/master.{day:%Y%m%d}.idx"


def sec_archive_url(filename: str) -> str:
    return f"https://www.sec.gov/Archives/{filename.lstrip('/')}"


def build_index_url(cik: str, accession_number: str) -> str:
    clean_cik = str(int(cik))
    flat_accession = accession_number.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{clean_cik}/{flat_accession}/{accession_number}-index.html"


def build_request(url: str, user_agent: str) -> Request:
    return Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})


def fetch_text(url: str, user_agent: str, timeout: int = 30) -> str:
    with urlopen(build_request(url, user_agent), timeout=timeout) as response:
        return response.read().decode("utf-8", "ignore")


def load_keywords(path: Path = KEYWORDS_PATH) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_tracked_entities(path: Path = TRACKED_ENTITIES_PATH) -> list[TrackedEntity]:
    entities: list[TrackedEntity] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            name = normalize_whitespace(row["name"])
            entities.append(
                TrackedEntity(
                    ticker=normalize_whitespace(row.get("ticker", "")),
                    name=name,
                    entity_type=normalize_whitespace(row.get("type", "Unknown")),
                    normalized_name=normalize_name(name),
                    reduced_name=reduce_name(name),
                )
            )
    return entities


def parse_cik_lookup(raw_text: str) -> dict[str, set[str]]:
    lookup: dict[str, set[str]] = {}
    pattern = re.compile(r"([^:\n][^:\n]*?):(\d{10}):")
    for match in pattern.finditer(raw_text):
        name = normalize_whitespace(match.group(1))
        cik = match.group(2)
        for key in {normalize_name(name), reduce_name(name)}:
            if key:
                lookup.setdefault(key, set()).add(cik)
    return lookup


def hydrate_entity_ciks(entities: list[TrackedEntity], cik_lookup: dict[str, set[str]]) -> None:
    for entity in entities:
        entity.ciks.update(cik_lookup.get(entity.normalized_name, set()))
        entity.ciks.update(cik_lookup.get(entity.reduced_name, set()))


def parse_master_index(raw_text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line in raw_text.splitlines():
        if "|" not in line:
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 5 or parts[0] == "CIK":
            continue
        cik, company_name, form_type, filed_date, filename = parts[:5]
        accession_match = re.search(r"(\d{10}-\d{2}-\d{6})\.(?:txt|nc|htm|html|xml)$", filename, re.IGNORECASE)
        if not accession_match:
            continue
        entries.append(
            {
                "cik": cik,
                "company_name": company_name,
                "form_type": normalize_form(form_type),
                "filed_date": normalize_filed_date(filed_date),
                "filename": filename,
                "filing_url": sec_archive_url(filename),
                "accession_number": accession_match.group(1),
            }
        )
    return entries


def fetch_recent_entries(user_agent: str, days: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    today = datetime.now(timezone.utc).date()
    for offset in range(max(days, 1)):
        day = today - timedelta(days=offset)
        try:
            results.extend(parse_master_index(fetch_text(master_index_url(day), user_agent)))
            time.sleep(0.2)
        except HTTPError as exc:
            if exc.code not in {403, 404}:
                raise
        except URLError:
            continue
    return results


def choose_entity(entry: dict[str, str], entities: list[TrackedEntity]) -> TrackedEntity | None:
    entry_name = normalize_name(entry["company_name"])
    entry_reduced = reduce_name(entry["company_name"])
    cik = entry["cik"]

    for entity in entities:
        if cik in entity.ciks:
            return entity

    for entity in entities:
        if not entity.reduced_name:
            continue
        if entry_reduced == entity.reduced_name:
            return entity
        if len(entity.reduced_name) >= 10 and entity.reduced_name in entry_reduced:
            return entity
        if len(entry_reduced) >= 10 and entry_reduced in entity.reduced_name:
            return entity
        shared = set(entry_reduced.split()) & set(entity.reduced_name.split())
        if len(shared) >= 3 and entry_name.startswith(entity.reduced_name.split()[0]):
            return entity
    return None


def text_from_filing(raw_text: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw_text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&#160;", " ")
    return normalize_whitespace(text)


def find_keywords(text: str, keywords: Iterable[str]) -> list[str]:
    lower_text = text.lower()
    matches = [keyword for keyword in keywords if keyword.lower() in lower_text]
    return sorted(set(matches), key=str.lower)


def extract_items_summary(text: str) -> str | None:
    item_hits = re.findall(r"\bITEM\s+(\d+\.\d+)\b", text, flags=re.IGNORECASE)
    if not item_hits:
        return None
    unique_hits: list[str] = []
    for item in item_hits:
        if item not in unique_hits:
            unique_hits.append(item)
        if len(unique_hits) == 3:
            break
    return "8-K item sections referenced: " + ", ".join(unique_hits)


def extract_snippet(text: str, keywords: list[str]) -> str:
    item_summary = extract_items_summary(text)
    if item_summary:
        return item_summary

    lower_text = text.lower()
    for keyword in keywords:
        idx = lower_text.find(keyword.lower())
        if idx >= 0:
            start = max(0, idx - 120)
            end = min(len(text), idx + 220)
            return normalize_whitespace(text[start:end])[:320]

    sentences = re.split(r"(?<=[.!?])\s+", normalize_whitespace(text[:3000]))
    return (" ".join(sentences[:2]).strip() if sentences else text[:240])[:320]


def _extract_openarena_answer(payload: dict) -> str:
    result = payload.get("result") or {}
    answer = result.get("answer")
    if isinstance(answer, str):
        return answer.strip()
    if isinstance(answer, dict):
        for value in answer.values():
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _call_openarena(
    base_url: str,
    bearer_token: str,
    workflow_id: str,
    prompt: str,
    timeout_seconds: int,
) -> str:
    payload = json.dumps(
        {
            "query": prompt,
            "workflow_id": workflow_id,
            "is_persistence_allowed": False,
        }
    ).encode("utf-8")
    request = Request(
        f"{base_url.rstrip('/')}/v2/inference",
        data=payload,
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        response_json = json.loads(response.read().decode("utf-8", "ignore"))
    answer = _extract_openarena_answer(response_json if isinstance(response_json, dict) else {})
    if not answer:
        raise RuntimeError("OpenArena returned an empty answer.")
    return answer


def is_low_quality_summary(summary: str) -> bool:
    lower = (summary or "").lower()
    if len(lower.strip()) < 80:
        return True
    if "an official website of the united states government" in lower:
        return True
    required = ["relevance verdict", "one-line takeaway", "what's new"]
    return not all(label in lower for label in required)


def fallback_synopsis(
    company_name: str,
    tracked_name: str,
    form_type: str,
    snippet: str,
    keywords: list[str],
) -> str:
    relevance = "HIGHLY RELEVANT TO PRIVATE CREDIT" if any("private credit" == k.lower() for k in keywords) else "RELEVANT TO PRIVATE CREDIT"
    whats_new_lines = [
        f"Matched keywords: {', '.join(keywords[:5])}.",
        snippet or f"{form_type} filing matched the configured watchlist and keyword filters.",
    ]
    lines = [
        f"SEC Filing Analysis: {company_name} - {form_type}",
        "A. Relevance Verdict",
        relevance,
        "",
        "B. One-Line Takeaway",
        snippet or f"{tracked_name or company_name} triggered the private credit monitor based on filing text and configured keywords.",
        "",
        "C. What's New",
        *whats_new_lines,
        "",
        "D. Most Important Points",
        f"{tracked_name or company_name} is on the watchlist and matched a recent {form_type} filing.",
        "",
        "E. Why It Matters Now",
        "This filing matched the configured private credit watchlist and warrants editorial review.",
    ]
    return "\n".join(lines).strip()


def generate_synopsis(
    filing_text: str,
    company_name: str,
    tracked_name: str,
    form_type: str,
    snippet: str,
    keywords: list[str],
    openarena_base_url: str,
    openarena_bearer_token: str,
    openarena_workflow_id: str,
    openarena_timeout_seconds: int,
) -> tuple[str, str, str | None]:
    if not filing_text.strip():
        return fallback_synopsis(company_name, tracked_name, form_type, snippet, keywords), "fallback", "No filing text available."
    if not openarena_bearer_token or not openarena_workflow_id:
        return fallback_synopsis(company_name, tracked_name, form_type, snippet, keywords), "fallback", "Missing OpenArena credentials."

    excerpt = filing_text[:12000]
    prompt = (
        "You are assisting a financial reporter covering private credit.\n"
        "Return a consistent structured analysis using these exact section headings and lettering:\n"
        "A. Relevance Verdict\n"
        "B. One-Line Takeaway\n"
        "C. What's New\n"
        "D. Most Important Points\n"
        "E. Why It Matters Now\n"
        "F. Filing Details Extracted\n"
        "G. Signals Reporters Should Notice\n"
        "H. Routine vs. Non-Routine\n"
        "I. Questions for Follow-Up\n"
        "J. Evidence from the Filing\n"
        "K. Final Newsroom Brief\n"
        "Start with a title line in the form: SEC Filing Analysis: <entity> - <form/date cue>.\n"
        "Do not include SEC boilerplate or navigation text.\n"
        "In section A, use a clear relevance verdict such as HIGHLY RELEVANT TO PRIVATE CREDIT, RELEVANT TO PRIVATE CREDIT, or LOW RELEVANCE.\n\n"
        f"Form Type: {form_type}\n"
        f"Company Name: {company_name}\n"
        f"Tracked Entity: {tracked_name}\n"
        f"Matched Keywords: {', '.join(keywords)}\n"
        f"Snippet Hint: {snippet}\n\n"
        "Filing Text Excerpt:\n"
        f"{excerpt}"
    )
    try:
        summary = _call_openarena(
            openarena_base_url,
            openarena_bearer_token,
            openarena_workflow_id,
            prompt,
            openarena_timeout_seconds,
        )
        if is_low_quality_summary(summary):
            summary = _call_openarena(
                openarena_base_url,
                openarena_bearer_token,
                openarena_workflow_id,
                prompt + "\n\nRetry with cleaner structured output only.",
                openarena_timeout_seconds,
            )
        if not summary or is_low_quality_summary(summary):
            return (
                fallback_synopsis(company_name, tracked_name, form_type, snippet, keywords),
                "fallback",
                "OpenArena returned low-quality output.",
            )
        return summary.strip(), "openarena", None
    except Exception as exc:
        return fallback_synopsis(company_name, tracked_name, form_type, snippet, keywords), "fallback", str(exc)


def send_email_alert(matches: list[FilingMatch]) -> tuple[bool, str | None]:
    enabled = os.getenv("ENABLE_EMAIL_ALERTS", "false").strip().lower() == "true"
    if not enabled or not matches:
        return False, None

    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587").strip())
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    from_email = os.getenv("FROM_EMAIL", smtp_username).strip()
    to_email = os.getenv("ALERT_EMAIL_TO", "").strip()
    if not all([smtp_host, smtp_username, smtp_password, from_email, to_email]):
        return False, "Email alert skipped because SMTP settings are incomplete."

    if len(matches) == 1:
        match = matches[0]
        message = EmailMessage()
        message["Subject"] = f"[Private Credit Monitor] {match.company_name} | {match.relevance_verdict or match.form_type}"
        message["From"] = from_email
        message["To"] = to_email
        parsed = {
            "title": match.openarena_title,
            "relevance_verdict": match.relevance_verdict,
            "one_line_takeaway": match.one_line_takeaway,
            "whats_new": match.whats_new,
        }
        message.set_content(format_email_text(parsed, match.index_url))
        message.add_alternative(format_email_html(parsed, match.index_url), subtype="html")
        messages = [message]
    else:
        message = EmailMessage()
        message["Subject"] = f"[Private Credit Monitor] {len(matches)} new filing alert(s)"
        message["From"] = from_email
        message["To"] = to_email
        text_blocks = []
        html_blocks = []
        for match in matches[:20]:
            parsed = {
                "title": match.openarena_title,
                "relevance_verdict": match.relevance_verdict,
                "one_line_takeaway": match.one_line_takeaway,
                "whats_new": match.whats_new,
            }
            text_blocks.append(format_email_text(parsed, match.index_url))
            html_blocks.append(format_email_html(parsed, match.index_url))
        message.set_content("\n\n---\n\n".join(text_blocks))
        message.add_alternative("<html><body>" + "<hr/>".join(html_blocks) + "</body></html>", subtype="html")
        messages = [message]

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(smtp_username, smtp_password)
        for message in messages:
            server.send_message(message)
    return True, None


def run_monitor(
    user_agent: str,
    days: int = DEFAULT_DAYS,
    forms: list[str] | None = None,
    keywords: list[str] | None = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    print_matches: bool = True,
) -> list[FilingMatch]:
    active_forms = [normalize_form(form) for form in (forms or DEFAULT_FORMS)]
    active_keywords = keywords or load_keywords()
    openarena_base_url = os.getenv("OPENARENA_BASE_URL", DEFAULT_OPENARENA_BASE_URL).strip()
    openarena_bearer_token = os.getenv("OPENARENA_BEARER_TOKEN", "").strip()
    openarena_workflow_id = os.getenv("OPENARENA_WORKFLOW_ID", DEFAULT_OPENARENA_WORKFLOW_ID).strip()
    openarena_timeout_seconds = int((os.getenv("OPENARENA_TIMEOUT_SECONDS", "60") or "60").strip())
    state = load_json(STATE_PATH, {"seen_accessions": [], "last_run": None, "last_error": None})
    seen_accessions = set(state.get("seen_accessions", []))

    entities = load_tracked_entities()
    cik_lookup = parse_cik_lookup(fetch_text("https://www.sec.gov/Archives/edgar/cik-lookup-data.txt", user_agent))
    hydrate_entity_ciks(entities, cik_lookup)

    recent_entries = fetch_recent_entries(user_agent, days)
    matches: list[FilingMatch] = []
    last_error = None
    openarena_generated = 0
    fallback_generated = 0

    for entry in recent_entries:
        if entry["form_type"] not in active_forms:
            continue
        entity = choose_entity(entry, entities)
        if not entity:
            continue
        try:
            filing_text = text_from_filing(fetch_text(entry["filing_url"], user_agent))
            keyword_hits = find_keywords(filing_text, active_keywords)
            if not keyword_hits:
                continue
            snippet = extract_snippet(filing_text, keyword_hits)
            synopsis, analysis_source, openarena_error = generate_synopsis(
                filing_text=filing_text,
                company_name=entry["company_name"],
                tracked_name=entity.name,
                form_type=entry["form_type"],
                snippet=snippet,
                keywords=keyword_hits,
                openarena_base_url=openarena_base_url,
                openarena_bearer_token=openarena_bearer_token,
                openarena_workflow_id=openarena_workflow_id,
                openarena_timeout_seconds=openarena_timeout_seconds,
            )
            if analysis_source == "openarena":
                openarena_generated += 1
            else:
                fallback_generated += 1
            parsed_synopsis = parse_openarena_output(synopsis)
            matches.append(
                FilingMatch(
                    accession_number=entry["accession_number"],
                    cik=entry["cik"],
                    company_name=entry["company_name"],
                    form_type=entry["form_type"],
                    filed_date=entry["filed_date"],
                    filing_url=entry["filing_url"],
                    index_url=build_index_url(entry["cik"], entry["accession_number"]),
                    tracked_name=entity.name,
                    tracked_type=entity.entity_type,
                    matched_keywords=keyword_hits,
                    description=snippet,
                    openarena_output=synopsis,
                    openarena_title=parsed_synopsis["title"],
                    relevance_verdict=parsed_synopsis["relevance_verdict"],
                    one_line_takeaway=parsed_synopsis["one_line_takeaway"],
                    whats_new=parsed_synopsis["whats_new"],
                    remaining_sections=parsed_synopsis["remaining_sections"],
                    wire_recommendation=parsed_synopsis["wire_recommendation"],
                    analysis_source=analysis_source,
                    openarena_error=openarena_error,
                    source="sec-daily-index",
                )
            )
            time.sleep(0.2)
        except Exception as exc:  # pragma: no cover
            last_error = str(exc)

    unique_matches: list[FilingMatch] = []
    seen_in_run: set[str] = set()
    new_matches: list[FilingMatch] = []
    for match in sorted(matches, key=lambda item: (item.filed_date, item.company_name), reverse=True):
        if match.accession_number in seen_in_run:
            continue
        seen_in_run.add(match.accession_number)
        unique_matches.append(match)
        if match.accession_number not in seen_accessions:
            new_matches.append(match)
        if len(unique_matches) >= max_results:
            break

    email_sent, email_error = send_email_alert(new_matches)
    seen_accessions.update(match.accession_number for match in new_matches)

    save_json(ALERTS_PATH, [asdict(match) for match in unique_matches])
    save_json(
        STATUS_PATH,
        {
            "mode": "github-pages-scheduled-poller",
            "last_run": utc_now_iso(),
            "last_error": email_error or last_error,
            "days_scanned": days,
            "forms": active_forms,
            "keywords": active_keywords,
            "new_alerts": len(new_matches),
            "total_alerts": len(unique_matches),
            "email_sent": email_sent,
            "entities_tracked": len(entities),
            "recent_entries_scanned": len(recent_entries),
            "openarena_enabled": bool(openarena_bearer_token),
            "openarena_workflow_id": openarena_workflow_id,
            "openarena_generated": openarena_generated,
            "fallback_generated": fallback_generated,
        },
    )
    save_json(
        STATE_PATH,
        {
            "seen_accessions": sorted(seen_accessions)[-5000:],
            "last_run": utc_now_iso(),
            "last_error": email_error or last_error,
        },
    )

    if print_matches:
        for match in unique_matches:
            print(
                f"{match.filed_date} | {match.company_name} | {match.form_type} | "
                f"{match.relevance_verdict or ', '.join(match.matched_keywords)}"
            )
            print(f"  {match.one_line_takeaway or match.description}")
            print(f"  {match.index_url}")
    return unique_matches


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search recent SEC filings for private credit signals.")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Number of calendar days to scan.")
    parser.add_argument("--forms", default=",".join(DEFAULT_FORMS), help="Comma-separated SEC form types.")
    parser.add_argument("--keywords", default="", help="Optional comma-separated keywords.")
    parser.add_argument("--max-results", type=int, default=DEFAULT_MAX_RESULTS, help="Maximum matches to keep.")
    parser.add_argument("--quiet", action="store_true", help="Skip console printing.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    user_agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not user_agent:
        raise SystemExit("SEC_USER_AGENT is required. Example: Private-Credit-Monitor/1.0 your-email@example.com")

    keywords = [item.strip() for item in args.keywords.split(",") if item.strip()] or None
    forms = [item.strip() for item in args.forms.split(",") if item.strip()]
    run_monitor(
        user_agent=user_agent,
        days=max(args.days, 1),
        forms=forms,
        keywords=keywords,
        max_results=max(args.max_results, 1),
        print_matches=not args.quiet,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
