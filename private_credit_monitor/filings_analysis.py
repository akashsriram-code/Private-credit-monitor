from __future__ import annotations

import argparse
import html
import io
import mimetypes
import json
import os
import re
import secrets
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from private_credit_monitor.monitor import (
    CONFIG_DIR,
    DATA_DIR,
    DEFAULT_CIK_CACHE_MAX_AGE_DAYS,
    DEFAULT_FETCH_RETRIES,
    DEFAULT_OPENARENA_BASE_URL,
    DEFAULT_OPENARENA_TIMEOUT_SECONDS,
    TrackedEntity,
    _extract_openarena_answer,
    fetch_text,
    hydrate_entity_ciks,
    load_cik_lookup_text,
    load_smtp_settings,
    load_tracked_entities,
    parse_cik_lookup,
    reduce_name,
    send_messages,
    text_from_filing,
    utc_now_iso,
)
from urllib.request import Request, urlopen
from urllib.error import HTTPError

try:
    from vercel.blob import list_objects, put
except ImportError:  # pragma: no cover
    list_objects = None
    put = None


FILINGS_ANALYSIS_SESSIONS_PATH = DATA_DIR / "filing_analysis_sessions.json"
DEFAULT_ANALYSIS_WORKFLOW_ID = "a1781c17-d09d-4ed5-b11c-032fe42052ae"
DEFAULT_ANALYSIS_MODEL = "gemini-3-flash"
DEFAULT_MAX_FILINGS_PER_ENTITY = 8
DEFAULT_PERSISTED_SESSION_LIMIT = 20
FILINGS_ANALYSIS_BLOB_PREFIX = "filing-analysis/sessions"
DEFAULT_OPENARENA_INPUT_TOKEN_BUDGET = 500_000
OPENARENA_ESTIMATED_CHARS_PER_TOKEN = 2
OPENARENA_UPLOAD_OVERHEAD_TOKENS = 20_000
MIN_OPENARENA_UPLOAD_CHARS_PER_FILING = 12_000
MIXED_QUARTERLY_ANNUAL_FILING_TYPE = "10-Q+10-K"
DEFAULT_ANALYSIS_SYSTEM_PROMPT = (
    "You are a private-credit SEC filings analyst. You will receive one or more 10-K or 10-Q filings plus a "
    "user question. Answer the question using only the provided filings, identify trends across filings where "
    "possible, and cite every material factual claim using the most specific locator available (page, section, "
    "note, table, item, line, or chunk ID). Structure the response as:\n\n"
    "1. Answer\n"
    "2. Key findings\n"
    "3. Trends / changes observed\n"
    "4. Evidence with citations\n"
    "5. Caveats / limits\n\n"
    "Rules:\n"
    "- Use filings as the source of truth.\n"
    "- Compare across periods and entities when relevant.\n"
    "- Label inferences clearly.\n"
    "- Do not invent facts.\n"
    "- If only one filing is provided, say trend analysis was not done since a singular filing was provided."
)
ISSUE_SECTION_PATTERN = re.compile(r"(?m)^###\s+(?P<name>.+?)\s*$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DEFAULT_OPENARENA_INFERENCE_RETRIES = 3
TOKEN_LIMIT_ERROR_MARKERS = (
    "input token count exceeds",
    "maximum number of tokens allowed",
)


class OpenArenaRequestError(RuntimeError):
    def __init__(self, url: str, status_code: int | None, detail: str):
        self.url = url
        self.status_code = status_code
        self.detail = detail
        status_text = status_code if status_code is not None else "unknown"
        super().__init__(f"OpenArena POST {url} failed with {status_text}: {detail}")


@dataclass
class FilingDocument:
    entity_name: str
    filing_type: str
    filed_date: str
    accession_number: str
    cik: str
    period_key: str
    filing_url: str
    index_url: str
    primary_document: str
    filing_label: str
    text_excerpt: str
    full_text: str = ""
    upload_text: str = ""
    upload_file_name: str = ""
    upload_bytes: bytes = b""


@dataclass
class FilingCitation:
    accession_number: str
    entity_name: str
    filing_type: str
    filed_date: str
    filing_label: str
    filing_url: str
    index_url: str
    excerpt: str


@dataclass
class FilingAnalysisSession:
    id: str
    issue_number: int | None
    issue_title: str
    issue_url: str
    status: str
    entities: list[str]
    filing_type: str
    lookback_count: int
    question: str
    filings: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    answer: str = ""
    workflow_id: str = ""
    model: str = DEFAULT_ANALYSIS_MODEL
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    completed_at: str | None = None
    error: str | None = None
    request_source: str = "github-issue"
    progress_log: list[str] = field(default_factory=list)


@dataclass
class EmailDeliveryResult:
    status: str = "not_requested"
    error: str | None = None


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


def normalize_form_type(value: str) -> str:
    normalized = re.sub(r"\s+", "", (value or "").strip().upper())
    aliases = {
        "10-Q+10-K": MIXED_QUARTERLY_ANNUAL_FILING_TYPE,
        "10-Q/10-K": MIXED_QUARTERLY_ANNUAL_FILING_TYPE,
        "10-Q&10-K": MIXED_QUARTERLY_ANNUAL_FILING_TYPE,
        "10-QAND10-K": MIXED_QUARTERLY_ANNUAL_FILING_TYPE,
        "10-QWITH10-K": MIXED_QUARTERLY_ANNUAL_FILING_TYPE,
        "MIXED": MIXED_QUARTERLY_ANNUAL_FILING_TYPE,
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"10-K", "10-Q", MIXED_QUARTERLY_ANNUAL_FILING_TYPE}:
        raise ValueError("filing_type must be 10-K, 10-Q, or 10-Q+10-K")
    return normalized


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def parse_issue_form(body: str) -> dict[str, str]:
    raw = (body or "").replace("\r\n", "\n")
    matches = list(ISSUE_SECTION_PATTERN.finditer(raw))
    result: dict[str, str] = {}
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
        label = _clean_text(match.group("name")).lower()
        result[label] = raw[start:end].strip()
    return result


def _parse_entities_block(value: str) -> list[str]:
    entities: list[str] = []
    for line in (value or "").splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        candidate = re.sub(r"^\s*[-*]\s*", "", candidate).strip()
        if candidate:
            entities.append(candidate)
    return entities


def parse_issue_request(body: str) -> dict[str, Any]:
    sections = parse_issue_form(body)
    entities = _parse_entities_block(sections.get("tracked entities", ""))
    filing_type = normalize_form_type(sections.get("filing type", ""))
    lookback_count = int(_clean_text(sections.get("lookback count", "1")) or "1")
    question = sections.get("what are you looking for?", "").strip()
    if not entities:
        raise ValueError("At least one tracked entity is required.")
    if lookback_count < 1:
        raise ValueError("Lookback count must be at least 1.")
    if not question:
        raise ValueError("Question is required.")
    return {
        "entities": entities,
        "filing_type": filing_type,
        "lookback_count": lookback_count,
        "question": question,
    }


def parse_live_request_payload(payload: dict[str, Any]) -> dict[str, Any]:
    entities = [str(item).strip() for item in payload.get("entities", []) if str(item).strip()]
    filing_type = normalize_form_type(str(payload.get("filing_type", "")))
    try:
        lookback_count = int(payload.get("lookback_count", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("lookback_count must be an integer.") from exc
    question = str(payload.get("question", "")).strip()
    email = str(payload.get("email", "")).strip()
    if not entities:
        raise ValueError("At least one tracked entity is required.")
    if lookback_count < 1:
        raise ValueError("Lookback count must be at least 1.")
    if not question:
        raise ValueError("Question is required.")
    if email and not EMAIL_PATTERN.match(email):
        raise ValueError("email must be a valid email address.")
    return {
        "entities": entities,
        "filing_type": filing_type,
        "lookback_count": lookback_count,
        "question": question,
        "email": email,
    }


def _entity_lookup(entities: list[TrackedEntity]) -> dict[str, TrackedEntity]:
    lookup: dict[str, TrackedEntity] = {}
    for entity in entities:
        for key in {
            entity.name.lower(),
            entity.normalized_name,
            reduce_name(entity.name),
        }:
            if key:
                lookup[key] = entity
    return lookup


def resolve_entities(requested_names: list[str], tracked_entities: list[TrackedEntity]) -> list[TrackedEntity]:
    lookup = _entity_lookup(tracked_entities)
    resolved: list[TrackedEntity] = []
    seen: set[str] = set()
    for requested in requested_names:
        key_options = {
            requested.lower(),
            re.sub(r"\s+", " ", requested.lower()).strip(),
            reduce_name(requested),
        }
        match = next((lookup[key] for key in key_options if key in lookup), None)
        if not match:
            raise ValueError(f"Tracked entity not found: {requested}")
        if match.name not in seen:
            resolved.append(match)
            seen.add(match.name)
    return resolved


def build_session_from_issue(issue_payload: dict[str, Any], tracked_entities: list[TrackedEntity]) -> FilingAnalysisSession:
    parsed = parse_issue_request(issue_payload.get("body", ""))
    resolved_entities = resolve_entities(parsed["entities"], tracked_entities)
    issue_number = issue_payload.get("number")
    issue_id = f"issue-{issue_number}" if issue_number is not None else f"manual-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    now = utc_now_iso()
    return FilingAnalysisSession(
        id=issue_id,
        issue_number=issue_number,
        issue_title=issue_payload.get("title", f"Filings analysis request #{issue_number or 'manual'}"),
        issue_url=issue_payload.get("html_url", ""),
        status="queued",
        entities=[entity.name for entity in resolved_entities],
        filing_type=parsed["filing_type"],
        lookback_count=parsed["lookback_count"],
        question=parsed["question"],
        created_at=now,
        updated_at=now,
    )


def build_live_session(request_payload: dict[str, Any], tracked_entities: list[TrackedEntity]) -> FilingAnalysisSession:
    parsed = parse_live_request_payload(request_payload)
    resolved_entities = resolve_entities(parsed["entities"], tracked_entities)
    now = utc_now_iso()
    session_id = f"live-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    return FilingAnalysisSession(
        id=session_id,
        issue_number=None,
        issue_title=f"Live filings analysis · {parsed['filing_type']}",
        issue_url="",
        status="queued",
        entities=[entity.name for entity in resolved_entities],
        filing_type=parsed["filing_type"],
        lookback_count=parsed["lookback_count"],
        question=parsed["question"],
        created_at=now,
        updated_at=now,
        request_source="live-api",
    )


def upsert_session_archive(path: Path, session: FilingAnalysisSession) -> list[dict[str, Any]]:
    archive = load_json(path, [])
    replaced = False
    for idx, existing in enumerate(archive):
        if existing.get("id") == session.id:
            archive[idx] = asdict(session)
            replaced = True
            break
    if not replaced:
        archive.insert(0, asdict(session))
    archive.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    save_json(path, archive)
    return archive


def load_session_archive(path: Path = FILINGS_ANALYSIS_SESSIONS_PATH) -> list[dict[str, Any]]:
    archive = load_json(path, [])
    return archive if isinstance(archive, list) else []


def _blob_enabled() -> bool:
    return put is not None and list_objects is not None and bool(os.getenv("BLOB_READ_WRITE_TOKEN", "").strip())


def _blob_items(result: Any) -> list[Any]:
    if isinstance(result, dict):
        return list(result.get("blobs", []))
    return list(getattr(result, "blobs", result or []))


def _blob_attr(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def persist_live_session(session: FilingAnalysisSession, sessions_path: Path = FILINGS_ANALYSIS_SESSIONS_PATH) -> bool:
    payload = asdict(session)
    if _blob_enabled():
        put(
            f"{FILINGS_ANALYSIS_BLOB_PREFIX}/{session.id}.json",
            json.dumps(payload, indent=2).encode("utf-8"),
            access="public",
            add_random_suffix=False,
            content_type="application/json",
        )
        return True
    try:
        upsert_session_archive(sessions_path, session)
        return True
    except OSError:
        return False


def load_persisted_sessions(
    limit: int = DEFAULT_PERSISTED_SESSION_LIMIT,
    sessions_path: Path = FILINGS_ANALYSIS_SESSIONS_PATH,
) -> list[dict[str, Any]]:
    if _blob_enabled():
        try:
            listed = list_objects(prefix=FILINGS_ANALYSIS_BLOB_PREFIX)
            payloads: list[dict[str, Any]] = []
            for item in _blob_items(listed):
                url = _blob_attr(item, "url")
                if not url:
                    continue
                try:
                    with urlopen(url, timeout=30) as response:
                        payloads.append(json.loads(response.read().decode("utf-8", "ignore")))
                except Exception:
                    continue
            payloads.sort(key=lambda item: item.get("created_at", ""), reverse=True)
            return payloads[:limit]
        except Exception:
            pass
    return load_session_archive(sessions_path)[:limit]


def transition_session(session: FilingAnalysisSession, status: str, error: str | None = None) -> FilingAnalysisSession:
    session.status = status
    session.error = error
    session.updated_at = utc_now_iso()
    if status == "complete":
        session.completed_at = session.updated_at
    return session


def append_progress(session: FilingAnalysisSession, message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    session.progress_log.append(f"{timestamp} - {message}")
    session.updated_at = utc_now_iso()


def format_filings_analysis_email_text(session: FilingAnalysisSession) -> str:
    lines = [
        f"Private Credit Monitor filings analysis: {session.filing_type}",
        "",
        f"Entities: {', '.join(session.entities) or 'N/A'}",
        f"Question: {session.question or 'N/A'}",
        "",
        "Analysis:",
        session.answer or "No analysis output was produced.",
        "",
    ]
    if session.filings:
        lines.append("Filings reviewed:")
        for filing in session.filings:
            lines.append(
                f"- {filing.get('entity_name', 'Unknown')} | {filing.get('filing_type', 'N/A')} | "
                f"{filing.get('filed_date', 'N/A')} | {filing.get('index_url', '')}"
            )
        lines.append("")
    lines.append("This was a one-off delivery. Your email address was not stored by the application.")
    return "\n".join(lines).strip()


def format_filings_analysis_email_html(session: FilingAnalysisSession) -> str:
    filing_rows = "".join(
        (
            "<li>"
            f"{html.escape(str(filing.get('entity_name', 'Unknown')))} | "
            f"{html.escape(str(filing.get('filing_type', 'N/A')))} | "
            f"{html.escape(str(filing.get('filed_date', 'N/A')))}"
            + (
                f" | <a href=\"{html.escape(str(filing.get('index_url', '')))}\">Open Filing</a>"
                if filing.get("index_url")
                else ""
            )
            + "</li>"
        )
        for filing in session.filings
    )
    filings_section = (
        "<p style='margin:24px 0 8px;font:12px IBM Plex Mono,monospace;text-transform:uppercase;letter-spacing:0.08em;color:#6d4c34;'>Filings Reviewed</p>"
        f"<ul style='margin:0 0 18px 18px;padding:0;line-height:1.6;color:#38434d;'>{filing_rows}</ul>"
        if filing_rows
        else ""
    )
    escaped_answer = html.escape(session.answer or "No analysis output was produced.").replace("\n", "<br />")
    return f"""
<html>
  <body style="margin:0;padding:24px;background:#f4efe6;color:#16212b;font-family:Georgia,serif;">
    <div style="max-width:760px;margin:0 auto;background:#fffdf9;border:1px solid rgba(22,33,43,0.12);border-radius:18px;padding:24px;">
      <p style="margin:0 0 8px;font:12px IBM Plex Mono,monospace;text-transform:uppercase;letter-spacing:0.08em;color:#6d4c34;">Filings Analysis</p>
      <h2 style="margin:0 0 16px;font-size:28px;line-height:1.2;">{html.escape(session.filing_type)} analysis</h2>
      <p style="margin:0 0 8px;font:12px IBM Plex Mono,monospace;text-transform:uppercase;letter-spacing:0.08em;color:#6d4c34;">Entities</p>
      <p style="margin:0 0 14px;font-size:18px;line-height:1.45;">{html.escape(', '.join(session.entities) or 'N/A')}</p>
      <p style="margin:0 0 8px;font:12px IBM Plex Mono,monospace;text-transform:uppercase;letter-spacing:0.08em;color:#6d4c34;">Question</p>
      <p style="margin:0 0 18px;font-size:17px;line-height:1.55;">{html.escape(session.question or 'N/A')}</p>
      <p style="margin:0 0 8px;font:12px IBM Plex Mono,monospace;text-transform:uppercase;letter-spacing:0.08em;color:#6d4c34;">Analysis</p>
      <div style="margin:0 0 18px;font-size:16px;line-height:1.7;color:#38434d;">{escaped_answer}</div>
      {filings_section}
      <p style="margin:18px 0 0;font-size:13px;line-height:1.6;color:#6e747b;">This was a one-off delivery. Your email address was not stored by the application.</p>
    </div>
  </body>
</html>
""".strip()


def send_filings_analysis_email(session: FilingAnalysisSession, email_address: str) -> tuple[bool, str | None]:
    smtp_settings, smtp_error = load_smtp_settings(require_to_email=False)
    if smtp_error:
        return False, smtp_error

    message = EmailMessage()
    entity_label = session.entities[0] if len(session.entities) == 1 else f"{len(session.entities)} entities"
    message["Subject"] = f"[Private Credit Monitor] {session.filing_type} analysis for {entity_label}"
    message["From"] = str(smtp_settings["from_email"])
    message["To"] = email_address
    message.set_content(format_filings_analysis_email_text(session))
    message.add_alternative(format_filings_analysis_email_html(session), subtype="html")
    try:
        return send_messages([message], smtp_settings)
    except Exception as exc:
        return False, str(exc)


def resolved_timeout_seconds() -> int:
    raw_value = os.getenv("OPENARENA_TIMEOUT_SECONDS", str(DEFAULT_OPENARENA_TIMEOUT_SECONDS)) or str(DEFAULT_OPENARENA_TIMEOUT_SECONDS)
    try:
        requested = int(str(raw_value).strip())
    except ValueError:
        requested = DEFAULT_OPENARENA_TIMEOUT_SECONDS
    return max(requested, DEFAULT_OPENARENA_TIMEOUT_SECONDS)


def run_live_analysis(
    request_payload: dict[str, Any],
    sessions_path: Path = FILINGS_ANALYSIS_SESSIONS_PATH,
) -> tuple[FilingAnalysisSession, EmailDeliveryResult]:
    user_agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not user_agent:
        raise RuntimeError("SEC_USER_AGENT is required.")
    workflow_id = os.getenv("OPENARENA_FILINGS_WORKFLOW_ID", os.getenv("OPENARENA_WORKFLOW_ID", DEFAULT_ANALYSIS_WORKFLOW_ID)).strip()
    bearer_token = os.getenv("OPENARENA_BEARER_TOKEN", "").strip()
    base_url = os.getenv("OPENARENA_BASE_URL", DEFAULT_OPENARENA_BASE_URL).strip()
    timeout_seconds = resolved_timeout_seconds()

    tracked_entities = hydrate_tracked_entities_with_ciks(user_agent)
    parsed_request = parse_live_request_payload(request_payload)
    live_session = build_live_session(parsed_request, tracked_entities)
    processed = process_single_session(
        asdict(live_session),
        tracked_entities=tracked_entities,
        user_agent=user_agent,
        workflow_id=workflow_id,
        bearer_token=bearer_token,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )
    email_delivery = EmailDeliveryResult()
    email_address = parsed_request.get("email", "")
    if email_address and processed.status == "complete":
        append_progress(processed, "Sending one-off analysis email to the requested recipient.")
        sent, email_error = send_filings_analysis_email(processed, email_address)
        email_delivery = EmailDeliveryResult(
            status="sent" if sent else "failed",
            error=email_error,
        )
        if sent:
            append_progress(processed, "Analysis email sent successfully.")
        else:
            append_progress(processed, f"Analysis email failed: {email_error or 'Unknown email error.'}")
    elif email_address:
        email_delivery = EmailDeliveryResult(status="skipped")
        append_progress(processed, "Analysis email was skipped because the analysis did not complete successfully.")
    else:
        append_progress(processed, "No analysis email requested for this run.")
    persist_live_session(processed, sessions_path=sessions_path)
    return processed, email_delivery


def fetch_submission_json(cik: str, user_agent: str) -> dict[str, Any]:
    url = f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"
    return json.loads(fetch_text(url, user_agent, timeout=30, retries=DEFAULT_FETCH_RETRIES))


def build_filing_txt_url(cik: str, accession_number: str) -> str:
    clean_cik = str(int(cik))
    flat_accession = accession_number.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{clean_cik}/{flat_accession}/{accession_number}.txt"


def build_filing_index_url(cik: str, accession_number: str) -> str:
    clean_cik = str(int(cik))
    flat_accession = accession_number.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{clean_cik}/{flat_accession}/{accession_number}-index.html"


def build_primary_document_url(cik: str, accession_number: str, primary_document: str) -> str:
    clean_cik = str(int(cik))
    flat_accession = accession_number.replace("-", "")
    if not primary_document:
        return build_filing_txt_url(cik, accession_number)
    return f"https://www.sec.gov/Archives/edgar/data/{clean_cik}/{flat_accession}/{primary_document}"


def filing_matches_type(form: str, filing_type: str) -> bool:
    upper = (form or "").upper().strip()
    if filing_type == "10-K":
        return upper in {"10-K", "10-K/A"}
    if filing_type == "10-Q":
        return upper in {"10-Q", "10-Q/A"}
    return False


def filing_period_key(filed_date: str, filing_type: str) -> str:
    day = datetime.fromisoformat(filed_date).date()
    if filing_type == "10-K":
        return f"{day.year}"
    quarter = ((day.month - 1) // 3) + 1
    return f"{day.year}-Q{quarter}"


def allowed_period_keys(filing_type: str, lookback_count: int, reference_date: date | None = None) -> set[str]:
    ref = reference_date or datetime.now(timezone.utc).date()
    keys: set[str] = set()
    if filing_type == "10-K":
        for offset in range(lookback_count):
            keys.add(str(ref.year - offset))
        return keys

    year = ref.year
    quarter = ((ref.month - 1) // 3) + 1
    for _ in range(lookback_count):
        keys.add(f"{year}-Q{quarter}")
        quarter -= 1
        if quarter == 0:
            quarter = 4
            year -= 1
    return keys


def _extract_filing_rows(source: dict[str, Any]) -> list[dict[str, str]]:
    recent = ((source or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form", []) or []
    accession_numbers = recent.get("accessionNumber", []) or []
    filing_dates = recent.get("filingDate", []) or []
    primary_documents = recent.get("primaryDocument", []) or []
    primary_descriptions = recent.get("primaryDocDescription", []) or []
    rows: list[dict[str, str]] = []
    for idx in range(len(forms)):
        rows.append(
            {
                "form": forms[idx],
                "accession_number": accession_numbers[idx],
                "filed_date": filing_dates[idx],
                "primary_document": primary_documents[idx] if idx < len(primary_documents) else "",
                "description": primary_descriptions[idx] if idx < len(primary_descriptions) else "",
            }
        )
    return rows


def collect_submission_rows(submission: dict[str, Any], user_agent: str) -> list[dict[str, str]]:
    rows = _extract_filing_rows(submission)
    for file_ref in ((submission.get("filings") or {}).get("files") or []):
        name = file_ref.get("name", "").strip()
        if not name:
            continue
        url = f"https://data.sec.gov/submissions/{name}"
        try:
            extra = json.loads(fetch_text(url, user_agent, timeout=30, retries=DEFAULT_FETCH_RETRIES))
        except Exception:
            continue
        rows.extend(_extract_filing_rows({"filings": {"recent": extra}}))
    return rows


def select_filings_from_rows(
    rows: list[dict[str, str]],
    cik: str,
    entity_name: str,
    filing_type: str,
    lookback_count: int,
    reference_date: date | None = None,
    max_filings: int = DEFAULT_MAX_FILINGS_PER_ENTITY,
) -> list[FilingDocument]:
    allowed_keys = allowed_period_keys(filing_type, lookback_count, reference_date)
    deduped: dict[str, FilingDocument] = {}
    for row in sorted(rows, key=lambda item: item.get("filed_date", ""), reverse=True):
        form = row.get("form", "")
        filed_date = row.get("filed_date", "")
        accession_number = row.get("accession_number", "")
        if not accession_number or not filed_date or not filing_matches_type(form, filing_type):
            continue
        try:
            period_key = filing_period_key(filed_date, filing_type)
        except ValueError:
            continue
        if period_key not in allowed_keys:
            continue
        existing = deduped.get(period_key)
        is_amendment = form.upper().endswith("/A")
        if existing and existing.filing_type == filing_type and is_amendment:
            continue
        deduped[period_key] = FilingDocument(
            entity_name=entity_name,
            filing_type=form.upper(),
            filed_date=filed_date,
            accession_number=accession_number,
            cik=cik,
            period_key=period_key,
            filing_url=build_filing_txt_url(cik, accession_number),
            index_url=build_filing_index_url(cik, accession_number),
            primary_document=row.get("primary_document", ""),
            filing_label=f"{entity_name} {form.upper()} filed {filed_date}",
            text_excerpt="",
            upload_file_name="",
        )
    selected = sorted(deduped.values(), key=lambda item: item.filed_date, reverse=True)
    return selected[:max_filings]


def fetch_entity_filings(
    entity: TrackedEntity,
    filing_type: str,
    lookback_count: int,
    user_agent: str,
    reference_date: date | None = None,
) -> list[FilingDocument]:
    filings: list[FilingDocument] = []
    for cik in sorted(entity.ciks):
        try:
            submission = fetch_submission_json(cik, user_agent)
        except Exception:
            continue
        rows = collect_submission_rows(submission, user_agent)
        if filing_type == MIXED_QUARTERLY_ANNUAL_FILING_TYPE:
            quarterly_filings = select_filings_from_rows(
                rows=rows,
                cik=cik,
                entity_name=entity.name,
                filing_type="10-Q",
                lookback_count=lookback_count,
                reference_date=reference_date,
            )
            annual_filings = select_filings_from_rows(
                rows=rows,
                cik=cik,
                entity_name=entity.name,
                filing_type="10-K",
                lookback_count=1,
                reference_date=reference_date,
                max_filings=1,
            )
            deduped = {filing.accession_number: filing for filing in [*quarterly_filings, *annual_filings]}
            filings = sorted(deduped.values(), key=lambda item: item.filed_date, reverse=True)
            if filings:
                return filings
            continue
        filings = select_filings_from_rows(
            rows=rows,
            cik=cik,
            entity_name=entity.name,
            filing_type=filing_type,
            lookback_count=lookback_count,
            reference_date=reference_date,
        )
        if filings:
            return filings
    return []


def score_excerpt(candidate: str, question_terms: set[str]) -> int:
    words = set(re.findall(r"[a-z0-9]{3,}", candidate.lower()))
    return len(question_terms & words)


def build_filing_excerpt(text: str, question: str, max_chars: int = 900) -> str:
    normalized = _clean_text(text)
    if not normalized:
        return ""
    terms = set(re.findall(r"[a-z0-9]{3,}", question.lower()))
    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    if not terms:
        return normalized[:max_chars]
    scored = sorted(sentences, key=lambda item: (score_excerpt(item, terms), len(item)), reverse=True)
    excerpt_parts: list[str] = []
    total = 0
    for sentence in scored:
        sentence = sentence.strip()
        if not sentence:
            continue
        excerpt_parts.append(sentence)
        total += len(sentence) + 1
        if total >= max_chars:
            break
    excerpt = " ".join(excerpt_parts).strip() or normalized[:max_chars]
    return excerpt[:max_chars]


def resolved_openarena_input_token_budget() -> int:
    raw_value = os.getenv("OPENARENA_INPUT_TOKEN_BUDGET", str(DEFAULT_OPENARENA_INPUT_TOKEN_BUDGET))
    try:
        requested = int(str(raw_value).strip())
    except ValueError:
        requested = DEFAULT_OPENARENA_INPUT_TOKEN_BUDGET
    return max(requested, 50_000)


def estimate_openarena_input_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + OPENARENA_ESTIMATED_CHARS_PER_TOKEN - 1) // OPENARENA_ESTIMATED_CHARS_PER_TOKEN)


def _allocate_upload_char_budgets(lengths: list[int], total_budget: int) -> list[int]:
    if not lengths:
        return []
    if sum(lengths) <= total_budget:
        return lengths[:]

    budgets = [0 for _ in lengths]
    remaining = max(total_budget, len(lengths))
    unsettled = set(range(len(lengths)))
    while unsettled:
        share = max(remaining // len(unsettled), 1)
        settled_this_round = False
        for idx in list(unsettled):
            if lengths[idx] <= share:
                budgets[idx] = lengths[idx]
                remaining -= lengths[idx]
                unsettled.remove(idx)
                settled_this_round = True
        if not settled_this_round:
            for idx in unsettled:
                budgets[idx] = min(lengths[idx], share)
            break
    return budgets


def _question_terms(question: str) -> set[str]:
    stop_words = {
        "about",
        "across",
        "after",
        "against",
        "also",
        "and",
        "are",
        "between",
        "compare",
        "does",
        "filing",
        "filings",
        "for",
        "from",
        "have",
        "how",
        "into",
        "look",
        "looking",
        "show",
        "that",
        "the",
        "their",
        "there",
        "these",
        "this",
        "what",
        "when",
        "where",
        "which",
        "with",
        "year",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]{4,}", question.lower())
        if token not in stop_words
    }


def _relevant_text_windows(text: str, question: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    lower_text = text.lower()
    terms = _question_terms(question) | {
        "assets",
        "borrowings",
        "commitments",
        "covenant",
        "debt",
        "default",
        "fair",
        "income",
        "interest",
        "leverage",
        "liabilities",
        "liquidity",
        "maturity",
        "nonaccrual",
        "portfolio",
        "redemption",
        "risk",
        "secured",
        "value",
    }
    windows: list[tuple[int, int, int]] = []
    window_radius = 1_800
    for term in sorted(terms):
        start = 0
        hits = 0
        while hits < 8:
            idx = lower_text.find(term, start)
            if idx < 0:
                break
            left = max(0, idx - window_radius)
            right = min(len(text), idx + len(term) + window_radius)
            snippet = lower_text[left:right]
            score = sum(1 for candidate in terms if candidate in snippet)
            windows.append((score, left, right))
            start = idx + len(term)
            hits += 1

    if not windows:
        return text[:max_chars]

    selected: list[tuple[int, int]] = []
    total = 0
    for _, left, right in sorted(windows, key=lambda item: (item[0], item[2] - item[1]), reverse=True):
        if any(not (right < existing_left or left > existing_right) for existing_left, existing_right in selected):
            continue
        snippet_len = right - left
        if total + snippet_len > max_chars and selected:
            continue
        selected.append((left, right))
        total += snippet_len
        if total >= max_chars:
            break

    selected.sort()
    pieces: list[str] = []
    used = 0
    for left, right in selected:
        if used >= max_chars:
            break
        remaining = max_chars - used
        snippet = text[left:right].strip()
        if len(snippet) > remaining:
            snippet = snippet[:remaining].rsplit(" ", 1)[0].strip() or snippet[:remaining]
        if snippet:
            pieces.append(snippet)
            used += len(snippet) + 2
    return "\n\n".join(pieces).strip()[:max_chars]


def fit_filing_text_to_upload_budget(text: str, question: str, max_chars: int) -> str:
    normalized = _clean_text(text)
    if len(normalized) <= max_chars:
        return normalized

    notice = (
        "NOTE: This filing text was shortened before OpenArena upload to stay within the model input limit. "
        "It preserves the beginning of the filing, question-relevant passages, and the end of the filing.\n\n"
    )
    available = max(max_chars - len(notice), 1_000)
    head_chars = max(1_000, int(available * 0.35))
    tail_chars = max(1_000, int(available * 0.15))
    if head_chars + tail_chars >= available:
        head_chars = max(1, int(available * 0.7))
        tail_chars = max(0, available - head_chars)
    middle_chars = max(0, available - head_chars - tail_chars)

    head = normalized[:head_chars].strip()
    tail = normalized[-tail_chars:].strip() if tail_chars else ""
    middle = _relevant_text_windows(normalized[head_chars: len(normalized) - tail_chars], question, middle_chars)
    parts = [notice.strip(), head]
    if middle:
        parts.extend(["[Question-relevant passages]", middle])
    if tail:
        parts.extend(["[End of filing]", tail])
    return "\n\n".join(part for part in parts if part).strip()[:max_chars]


def prepare_openarena_uploads(
    filings: list[FilingDocument],
    question: str,
    session: FilingAnalysisSession | None = None,
    token_budget: int | None = None,
) -> list[FilingDocument]:
    if not filings:
        return filings

    resolved_budget = token_budget or resolved_openarena_input_token_budget()
    overhead_tokens = estimate_openarena_input_tokens(question) + estimate_openarena_input_tokens(
        os.getenv("OPENARENA_FILINGS_SYSTEM_PROMPT", DEFAULT_ANALYSIS_SYSTEM_PROMPT)
    ) + OPENARENA_UPLOAD_OVERHEAD_TOKENS
    available_tokens = max(resolved_budget - overhead_tokens, 10_000)
    available_chars = available_tokens * OPENARENA_ESTIMATED_CHARS_PER_TOKEN
    upload_texts = [filing.full_text or filing.text_excerpt for filing in filings]
    estimated_tokens = overhead_tokens + sum(estimate_openarena_input_tokens(text) for text in upload_texts)

    if session:
        append_progress(
            session,
            f"Estimated OpenArena input at about {estimated_tokens:,} token(s) against a {resolved_budget:,}-token budget.",
        )

    if estimated_tokens > resolved_budget:
        budgets = _allocate_upload_char_budgets([len(text) for text in upload_texts], available_chars)
        if session:
            append_progress(
                session,
                "Compressing uploaded filing text before PDF generation to avoid the OpenArena/Gemini input limit.",
            )
    else:
        budgets = [len(text) for text in upload_texts]

    per_filing_floor = min(MIN_OPENARENA_UPLOAD_CHARS_PER_FILING, max(1, available_chars // len(filings)))
    for filing, source_text, char_budget in zip(filings, upload_texts, budgets):
        budget = max(char_budget, min(len(source_text), per_filing_floor))
        filing.upload_text = fit_filing_text_to_upload_budget(source_text, question, budget)
        if session and len(filing.upload_text) < len(source_text):
            append_progress(
                session,
                f"Shortened {filing.filing_label} upload text from {len(source_text):,} to {len(filing.upload_text):,} characters.",
            )
        if session:
            append_progress(session, f"Converting {filing.filing_label} into PDF for workflow upload.")
        filing.upload_bytes = filing_text_to_pdf_bytes(filing.filing_label, filing.index_url, filing.upload_text)
    return filings


def wrap_pdf_line(text: str, max_chars: int = 95) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_object(obj_num: int, content: bytes) -> bytes:
    return f"{obj_num} 0 obj\n".encode("utf-8") + content + b"\nendobj\n"


def filing_text_to_pdf_bytes(filing_label: str, source_url: str, text: str) -> bytes:
    lines: list[str] = []
    lines.extend(wrap_pdf_line(filing_label, max_chars=82))
    lines.append("")
    lines.extend(wrap_pdf_line(f"Source: {source_url}", max_chars=96))
    lines.append("")
    for paragraph in [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]:
        lines.extend(wrap_pdf_line(paragraph, max_chars=96))
        lines.append("")

    page_line_capacity = 42
    page_groups = [lines[idx: idx + page_line_capacity] for idx in range(0, len(lines), page_line_capacity)] or [["No filing text available."]]

    object_num = 1
    catalog_id = object_num
    object_num += 1
    pages_id = object_num
    object_num += 1
    font_id = object_num
    object_num += 1

    page_ids: list[int] = []
    content_ids: list[int] = []
    page_streams: list[bytes] = []

    for page_lines in page_groups:
        page_id = object_num
        object_num += 1
        content_id = object_num
        object_num += 1
        page_ids.append(page_id)
        content_ids.append(content_id)

        commands = ["BT", "/F1 11 Tf", "54 738 Td", "14 TL"]
        if page_lines:
            first_line = _pdf_escape(page_lines[0])
            commands.append(f"({first_line}) Tj")
            for line in page_lines[1:]:
                commands.append(f"T* ({_pdf_escape(line)}) Tj")
        commands.append("ET")
        stream_body = "\n".join(commands).encode("utf-8")
        page_streams.append(stream_body)

    objects: list[bytes] = []
    objects.append(_pdf_object(catalog_id, f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("utf-8")))
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects.append(_pdf_object(pages_id, f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("utf-8")))
    objects.append(_pdf_object(font_id, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))

    for page_id, content_id, stream_body in zip(page_ids, content_ids, page_streams):
        page_dict = (
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("utf-8")
        objects.append(_pdf_object(page_id, page_dict))
        content = (
            f"<< /Length {len(stream_body)} >>\nstream\n".encode("utf-8")
            + stream_body
            + b"\nendstream"
        )
        objects.append(_pdf_object(content_id, content))

    output = io.BytesIO()
    output.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for obj in objects:
        offsets.append(output.tell())
        output.write(obj)
    xref_start = output.tell()
    output.write(f"xref\n0 {len(offsets)}\n".encode("utf-8"))
    output.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode("utf-8"))
    output.write(
        (
            f"trailer\n<< /Size {len(offsets)} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref_start}\n%%EOF"
        ).encode("utf-8")
    )
    return output.getvalue()


def hydrate_filing_texts(filings: list[FilingDocument], user_agent: str, question: str) -> list[FilingDocument]:
    hydrated: list[FilingDocument] = []
    for filing in filings:
        preferred_url = build_primary_document_url(filing.cik, filing.accession_number, filing.primary_document)
        try:
            raw_text = fetch_text(preferred_url, user_agent, timeout=60, retries=DEFAULT_FETCH_RETRIES)
        except Exception:
            raw_text = fetch_text(filing.filing_url, user_agent, timeout=60, retries=DEFAULT_FETCH_RETRIES)
        full_text = text_from_filing(raw_text)
        excerpt = build_filing_excerpt(full_text, question)
        safe_entity = re.sub(r"[^a-zA-Z0-9]+", "-", filing.entity_name).strip("-").lower() or "entity"
        upload_file_name = f"{safe_entity}_{filing.filing_type}_{filing.filed_date}_{filing.accession_number}.pdf"
        hydrated.append(
            FilingDocument(
                entity_name=filing.entity_name,
                filing_type=filing.filing_type,
                filed_date=filing.filed_date,
                accession_number=filing.accession_number,
                cik=filing.cik,
                period_key=filing.period_key,
                filing_url=filing.filing_url,
                index_url=filing.index_url,
                primary_document=filing.primary_document,
                filing_label=filing.filing_label,
                text_excerpt=excerpt,
                full_text=full_text,
                upload_file_name=upload_file_name,
            )
        )
    return prepare_openarena_uploads(hydrated, question)


def hydrate_filing_texts_with_progress(
    session: FilingAnalysisSession,
    filings: list[FilingDocument],
    user_agent: str,
    question: str,
) -> list[FilingDocument]:
    hydrated: list[FilingDocument] = []
    for filing in filings:
        append_progress(session, f"Fetching filing HTML for {filing.filing_label}.")
        preferred_url = build_primary_document_url(filing.cik, filing.accession_number, filing.primary_document)
        try:
            raw_text = fetch_text(preferred_url, user_agent, timeout=60, retries=DEFAULT_FETCH_RETRIES)
        except Exception:
            raw_text = fetch_text(filing.filing_url, user_agent, timeout=60, retries=DEFAULT_FETCH_RETRIES)
        full_text = text_from_filing(raw_text)
        excerpt = build_filing_excerpt(full_text, question)
        safe_entity = re.sub(r"[^a-zA-Z0-9]+", "-", filing.entity_name).strip("-").lower() or "entity"
        upload_file_name = f"{safe_entity}_{filing.filing_type}_{filing.filed_date}_{filing.accession_number}.pdf"
        hydrated.append(
            FilingDocument(
                entity_name=filing.entity_name,
                filing_type=filing.filing_type,
                filed_date=filing.filed_date,
                accession_number=filing.accession_number,
                cik=filing.cik,
                period_key=filing.period_key,
                filing_url=filing.filing_url,
                index_url=filing.index_url,
                primary_document=filing.primary_document,
                filing_label=filing.filing_label,
                text_excerpt=excerpt,
                full_text=full_text,
                upload_file_name=upload_file_name,
            )
        )
    return prepare_openarena_uploads(hydrated, question, session=session)


def build_openarena_documents(filings: list[FilingDocument]) -> list[dict[str, Any]]:
    return [
        {
            "id": filing.accession_number,
            "title": filing.filing_label,
            "text": filing.full_text or filing.text_excerpt,
            "metadata": {
                "entity_name": filing.entity_name,
                "filing_type": filing.filing_type,
                "filed_date": filing.filed_date,
                "period_key": filing.period_key,
                "filing_url": filing.filing_url,
                "index_url": filing.index_url,
            },
        }
        for filing in filings
    ]


def build_model_params() -> dict[str, Any]:
    return {
        "vertexai_gemini-3-flash": {
            "top_p": "0.95",
            "thinking_level": "high",
            "top_k": "47",
            "temperature": "1.0",
            "media_resolution": "media_resolution_medium",
            "system_prompt": os.getenv("OPENARENA_FILINGS_SYSTEM_PROMPT", DEFAULT_ANALYSIS_SYSTEM_PROMPT),
            "enable_reasoning": "true",
            "max_output_tokens": "48531",
        }
    }


def post_json(url: str, bearer_token: str, payload: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8", "ignore"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")
        raise OpenArenaRequestError(url, exc.code, detail or str(exc.reason)) from exc


def is_openarena_token_limit_error(detail: str) -> bool:
    normalized = (detail or "").lower()
    return all(marker in normalized for marker in TOKEN_LIMIT_ERROR_MARKERS)


def call_openarena_inference_with_retries(
    session: FilingAnalysisSession,
    base_url: str,
    bearer_token: str,
    inference_payload: dict[str, Any],
    timeout_seconds: int,
    max_attempts: int = DEFAULT_OPENARENA_INFERENCE_RETRIES,
) -> dict[str, Any]:
    inference_url = f"{base_url.rstrip('/')}/v3/inference"
    attempts = max(max_attempts, 1)
    for attempt in range(1, attempts + 1):
        append_progress(
            session,
            f"Running OpenArena inference (attempt {attempt}/{attempts}) with a {timeout_seconds}-second timeout.",
        )
        try:
            return post_json(
                inference_url,
                bearer_token,
                inference_payload,
                timeout_seconds,
            )
        except OpenArenaRequestError as exc:
            if is_openarena_token_limit_error(exc.detail):
                raise RuntimeError(
                    "OpenArena/Gemini rejected the request because the uploaded filings still exceeded the model "
                    "input limit. Try fewer entities or periods, or lower OPENARENA_INPUT_TOKEN_BUDGET so the "
                    f"backend compresses uploads more aggressively. Original error: {exc.detail}"
                ) from exc
            is_retryable_timeout = exc.status_code == 504 and attempt < attempts
            if not is_retryable_timeout:
                raise
            backoff_seconds = attempt * 5
            append_progress(
                session,
                f"OpenArena inference timed out with HTTP 504 on attempt {attempt}/{attempts}; retrying in {backoff_seconds} seconds.",
            )
            time.sleep(backoff_seconds)
    raise RuntimeError("OpenArena inference retry loop exited unexpectedly.")


def build_multipart_form_data(
    fields: dict[str, str],
    file_name: str,
    file_bytes: bytes,
    content_type: str,
) -> tuple[bytes, str]:
    boundary = f"----OpenArenaupload{uuid.uuid4().hex}"
    body = bytearray()
    for key, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'.encode("utf-8")
    )
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    body.extend(file_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), boundary


def upload_presigned_file(
    target_url: str,
    fields: dict[str, str],
    file_name: str,
    file_bytes: bytes,
    timeout_seconds: int,
) -> None:
    content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    body, boundary = build_multipart_form_data(fields, file_name, file_bytes, content_type)
    request = Request(
        target_url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds):
            return
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"OpenArena file upload failed with {exc.code}: {detail or exc.reason}") from exc


def parse_uploaded_file(
    base_url: str,
    bearer_token: str,
    workflow_id: str,
    presigned_url: dict[str, Any],
    timeout_seconds: int,
) -> str:
    payload = {
        "workflow_id": workflow_id,
        "presigned_url": presigned_url,
    }
    response_json = post_json(
        f"{base_url.rstrip('/')}/v1/document/file_parsing",
        bearer_token,
        payload,
        timeout_seconds,
    )
    file_uuid = str(response_json.get("file_uuid", "")).strip()
    if not file_uuid:
        nested = response_json.get("file_parse") or {}
        file_uuid = str(nested.get("file_uuid", "")).strip()
    if not file_uuid:
        raise RuntimeError("OpenArena parsing response did not include file_uuid.")
    return file_uuid


def trigger_rag_population(
    base_url: str,
    bearer_token: str,
    workflow_id: str,
    timeout_seconds: int,
) -> None:
    request = Request(
        f"{base_url.rstrip('/')}/v1/rag/populate/{workflow_id}",
        headers={
            "Authorization": f"Bearer {bearer_token}",
        },
        method="PUT",
    )
    try:
        with urlopen(request, timeout=timeout_seconds):
            return
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"OpenArena RAG populate failed with {exc.code}: {detail or exc.reason}") from exc


def build_context_fallback(question: str, filings: list[FilingDocument]) -> str:
    doc_blocks = []
    for filing in filings:
        doc_blocks.append(
            "\n".join(
                [
                    f"Document: {filing.filing_label}",
                    f"Filed Date: {filing.filed_date}",
                    f"Entity: {filing.entity_name}",
                    f"Source: {filing.index_url}",
                    f"Excerpt: {filing.text_excerpt}",
                ]
            )
        )
    return (
        "You are answering a user's question using SEC filing excerpts.\n"
        "Answer directly and cite the relevant filings by entity and filed date.\n\n"
        f"Question: {question}\n\n"
        "Documents:\n"
        + "\n\n".join(doc_blocks)
    )


def call_openarena_ask_documents(
    session: FilingAnalysisSession,
    question: str,
    filings: list[FilingDocument],
    workflow_id: str,
    bearer_token: str,
    base_url: str,
    timeout_seconds: int,
) -> str:
    if not bearer_token:
        raise RuntimeError("Missing OpenArena credentials.")
    if not workflow_id:
        raise RuntimeError("Missing OpenArena workflow ID.")
    file_payload = {
        "files_names": [
            {
                "file_name": filing.upload_file_name or f"{filing.accession_number}.html",
                "file_id": filing.upload_file_name or f"{filing.accession_number}.html",
            }
            for filing in filings
        ],
        "is_rag_storage_request": False,
        "workflow_id": workflow_id,
    }
    append_progress(session, f"Requesting OpenArena upload URLs for {len(filings)} filing PDF(s).")
    upload_payload = post_json(
        f"{base_url.rstrip('/')}/v3/document/file_upload",
        bearer_token,
        file_payload,
        timeout_seconds,
    )
    upload_urls = upload_payload.get("url", [])
    if len(upload_urls) != len(filings):
        raise RuntimeError("OpenArena upload URL response did not match the number of filings.")

    file_uuids: list[str] = []
    for filing, file_obj in zip(filings, upload_urls):
        nested_url = file_obj.get("url") or {}
        target_url = nested_url.get("url")
        fields = nested_url.get("fields") or {}
        file_name = nested_url.get("file_name") or filing.upload_file_name or f"{filing.accession_number}.html"
        if not target_url or not fields:
            raise RuntimeError("OpenArena upload URL response was missing upload fields.")
        append_progress(session, f"Uploading PDF for {filing.filing_label}.")
        upload_presigned_file(
            target_url=target_url,
            fields=fields,
            file_name=file_name,
            file_bytes=filing.upload_bytes or (filing.upload_text or filing.full_text).encode("utf-8"),
            timeout_seconds=timeout_seconds,
        )
        append_progress(session, f"Parsing uploaded PDF for {filing.filing_label}.")
        file_uuids.append(
            parse_uploaded_file(
                base_url=base_url,
                bearer_token=bearer_token,
                workflow_id=workflow_id,
                presigned_url={
                    "url": target_url,
                    "fields": fields,
                    "file_name": file_name,
                },
                timeout_seconds=timeout_seconds,
            )
        )

    inference_payload = {
        "workflow_id": workflow_id,
        "query": question,
        "is_persistence_allowed": False,
        "modelparams": build_model_params(),
        "input_variables": {},
        "conversation_id": None,
        "context": {
            "input_type": "file_uuid",
            "value": file_uuids,
        },
    }
    response_json = call_openarena_inference_with_retries(
        session=session,
        base_url=base_url,
        bearer_token=bearer_token,
        inference_payload=inference_payload,
        timeout_seconds=timeout_seconds,
    )
    answer = _extract_openarena_answer(response_json if isinstance(response_json, dict) else {})
    if not answer:
        raise RuntimeError("OpenArena returned an empty answer.")
    append_progress(session, "Received OpenArena analysis output.")
    return answer


def build_citations(filings: list[FilingDocument]) -> list[FilingCitation]:
    citations: list[FilingCitation] = []
    for filing in filings:
        citations.append(
            FilingCitation(
                accession_number=filing.accession_number,
                entity_name=filing.entity_name,
                filing_type=filing.filing_type,
                filed_date=filing.filed_date,
                filing_label=filing.filing_label,
                filing_url=filing.filing_url,
                index_url=filing.index_url,
                excerpt=filing.text_excerpt,
            )
        )
    return citations


def hydrate_tracked_entities_with_ciks(user_agent: str) -> list[TrackedEntity]:
    entities = load_tracked_entities(CONFIG_DIR / "tracked_entities.csv")
    cik_lookup_text, _, _ = load_cik_lookup_text(
        user_agent=user_agent,
        max_age_days=DEFAULT_CIK_CACHE_MAX_AGE_DAYS,
        allow_refresh=os.getenv("REFRESH_CIK_LOOKUP", "true").strip().lower() not in {"false", "0", "no"},
    )
    cik_lookup = parse_cik_lookup(cik_lookup_text)
    hydrate_entity_ciks(entities, cik_lookup)
    return entities


def describe_filing_selection(filing_type: str, lookback_count: int) -> str:
    if filing_type == MIXED_QUARTERLY_ANNUAL_FILING_TYPE:
        return f"the latest 10-K plus {lookback_count} recent 10-Q period(s)"
    return f"{filing_type} filings across {lookback_count} calendar period(s)"


def process_single_session(
    session_payload: dict[str, Any],
    tracked_entities: list[TrackedEntity],
    user_agent: str,
    workflow_id: str,
    bearer_token: str,
    base_url: str = DEFAULT_OPENARENA_BASE_URL,
    timeout_seconds: int = DEFAULT_OPENARENA_TIMEOUT_SECONDS,
) -> FilingAnalysisSession:
    session = FilingAnalysisSession(**session_payload)
    transition_session(session, "processing")
    append_progress(session, "Starting live filings analysis.")
    resolved_entities = resolve_entities(session.entities, tracked_entities)
    filings: list[FilingDocument] = []
    for entity in resolved_entities:
        append_progress(session, f"Looking up {describe_filing_selection(session.filing_type, session.lookback_count)} for {entity.name}.")
        filings.extend(fetch_entity_filings(entity, session.filing_type, session.lookback_count, user_agent))
    if not filings:
        append_progress(session, "No matching filings were found for the selected entities and lookback window.")
        transition_session(session, "failed", "No matching filings were found for the requested entities and period.")
        return session

    append_progress(session, f"Matched {len(filings)} filing(s). Preparing documents for upload.")
    hydrated_filings = hydrate_filing_texts_with_progress(session, filings, user_agent, session.question)
    session.filings = [
        {
            "entity_name": filing.entity_name,
            "filing_type": filing.filing_type,
            "filed_date": filing.filed_date,
            "accession_number": filing.accession_number,
            "period_key": filing.period_key,
            "filing_url": filing.filing_url,
            "index_url": filing.index_url,
            "primary_document": filing.primary_document,
            "filing_label": filing.filing_label,
        }
        for filing in hydrated_filings
    ]
    session.citations = [asdict(item) for item in build_citations(hydrated_filings)]
    session.workflow_id = workflow_id
    session.model = DEFAULT_ANALYSIS_MODEL
    try:
        session.answer = call_openarena_ask_documents(
            session=session,
            question=session.question,
            filings=hydrated_filings,
            workflow_id=workflow_id,
            bearer_token=bearer_token,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
        append_progress(session, "Analysis run completed successfully.")
        transition_session(session, "complete")
    except Exception as exc:
        append_progress(session, f"Analysis failed: {exc}")
        transition_session(session, "failed", str(exc))
    return session


def process_pending_sessions(
    sessions_path: Path = FILINGS_ANALYSIS_SESSIONS_PATH,
    limit: int = 5,
) -> int:
    user_agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not user_agent:
        raise SystemExit("SEC_USER_AGENT is required.")
    workflow_id = os.getenv("OPENARENA_FILINGS_WORKFLOW_ID", os.getenv("OPENARENA_WORKFLOW_ID", DEFAULT_ANALYSIS_WORKFLOW_ID)).strip()
    bearer_token = os.getenv("OPENARENA_BEARER_TOKEN", "").strip()
    base_url = os.getenv("OPENARENA_BASE_URL", DEFAULT_OPENARENA_BASE_URL).strip()
    timeout_seconds = resolved_timeout_seconds()

    archive = load_session_archive(sessions_path)
    tracked_entities = hydrate_tracked_entities_with_ciks(user_agent)
    processed = 0
    updated_archive: list[dict[str, Any]] = []
    pending_ids = {item["id"] for item in archive if item.get("status") == "queued"} if limit else set()
    for payload in archive:
        if payload.get("status") == "queued" and processed < limit and payload.get("id") in pending_ids:
            processed_session = process_single_session(
                payload,
                tracked_entities=tracked_entities,
                user_agent=user_agent,
                workflow_id=workflow_id,
                bearer_token=bearer_token,
                base_url=base_url,
                timeout_seconds=timeout_seconds,
            )
            updated_archive.append(asdict(processed_session))
            processed += 1
        else:
            updated_archive.append(payload)
    save_json(sessions_path, updated_archive)
    return processed


def ingest_issue_event(
    event_path: Path,
    sessions_path: Path = FILINGS_ANALYSIS_SESSIONS_PATH,
) -> FilingAnalysisSession:
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    issue = payload.get("issue") or payload
    labels = {item.get("name", "") for item in issue.get("labels", [])}
    if "filing-analysis" not in labels:
        raise ValueError("Issue is not labeled filing-analysis.")
    tracked_entities = load_tracked_entities(CONFIG_DIR / "tracked_entities.csv")
    session = build_session_from_issue(issue, tracked_entities)
    upsert_session_archive(sessions_path, session)
    return session


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process queued filing analysis requests.")
    parser.add_argument("--event-path", default="", help="Optional GitHub event payload path for issue ingestion.")
    parser.add_argument("--sessions-path", default=str(FILINGS_ANALYSIS_SESSIONS_PATH), help="Session archive path.")
    parser.add_argument("--process", action="store_true", help="Process queued sessions.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum queued sessions to process.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sessions_path = Path(args.sessions_path)
    if args.event_path:
        session = ingest_issue_event(Path(args.event_path), sessions_path=sessions_path)
        print(f"Queued session {session.id}")
        return 0
    if args.process:
        processed = process_pending_sessions(sessions_path=sessions_path, limit=max(args.limit, 1))
        print(f"Processed {processed} session(s)")
        return 0
    raise SystemExit("Specify --event-path or --process.")


if __name__ == "__main__":
    raise SystemExit(main())
