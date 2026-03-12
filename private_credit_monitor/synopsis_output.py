from __future__ import annotations

import html
import re
from typing import Any

SECTION_NAME_MAP = {
    "relevance verdict": "relevance_verdict",
    "one-line takeaway": "one_line_takeaway",
    "what's new": "whats_new",
    "most important points": "most_important_points",
    "why it matters now": "why_it_matters_now",
    "filing details extracted": "filing_details_extracted",
    "signals reporters should notice": "signals_reporters_should_notice",
    "routine vs. non-routine": "routine_vs_non_routine",
    "questions for follow-up": "questions_for_follow_up",
    "evidence from the filing": "evidence_from_the_filing",
    "final newsroom brief": "final_newsroom_brief",
}

SECTION_LABELS = {
    value: key.title().replace("Vs.", "vs.")
    for key, value in SECTION_NAME_MAP.items()
}


def _clean(value: str) -> str:
    return " ".join((value or "").strip().split())


def normalize_alert_level(value: str) -> str:
    text = _clean(value).upper()
    if "HIGHLY RELEVANT" in text or "HIGH" in text or "CRITICAL" in text:
        return "HIGH"
    if "MEDIUM" in text:
        return "MEDIUM"
    if "LOW" in text:
        return "LOW"
    return "UNKNOWN"


def _split_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    raw = (text or "").strip()
    matches = list(re.finditer(r"(?m)^(?P<letter>[A-Z])\.\s+(?P<title>.+?)\s*$", raw))
    if not matches:
        return raw, []

    title = raw[: matches[0].start()].strip()
    sections: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
        section_title = _clean(match.group("title"))
        section_body = raw[start:end].strip()
        sections.append((section_title, section_body))
    return title, sections


def _split_bullets_or_paragraphs(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    bullets = [re.sub(r"^\s*[-*]\s+", "", line).strip() for line in lines if re.match(r"^\s*[-*]\s+", line)]
    if bullets:
        return bullets

    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]
    if chunks:
        return [_clean(chunk) for chunk in chunks]

    single_lines = [_clean(line) for line in lines]
    return single_lines


def _parse_section_content(key: str, body: str) -> Any:
    if key in {
        "whats_new",
        "most_important_points",
        "signals_reporters_should_notice",
        "questions_for_follow_up",
        "evidence_from_the_filing",
    }:
        return _split_bullets_or_paragraphs(body)
    return body.strip()


def parse_openarena_output(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    title, section_pairs = _split_sections(raw)
    sections: dict[str, Any] = {}

    for label, body in section_pairs:
        key = SECTION_NAME_MAP.get(label.strip().lower())
        if key:
            sections[key] = _parse_section_content(key, body)

    relevance_verdict = _clean(str(sections.get("relevance_verdict", "")))
    one_line_takeaway = _clean(str(sections.get("one_line_takeaway", "")))
    whats_new = sections.get("whats_new", [])
    if isinstance(whats_new, str):
        whats_new = _split_bullets_or_paragraphs(whats_new)

    remaining_sections = {
        key: value
        for key, value in sections.items()
        if key not in {"relevance_verdict", "one_line_takeaway", "whats_new"}
    }

    return {
        "title": title,
        "relevance_verdict": relevance_verdict,
        "one_line_takeaway": one_line_takeaway,
        "whats_new": whats_new,
        "sections": sections,
        "remaining_sections": remaining_sections,
        "wire_recommendation": normalize_alert_level(relevance_verdict),
        "raw_output": raw,
    }


def _render_list_html(items: list[str]) -> str:
    if not items:
        return ""
    rendered = "".join(f"<li>{html.escape(item)}</li>" for item in items)
    return f"<ul>{rendered}</ul>"


def format_email_text(parsed: dict[str, Any], filing_url: str) -> str:
    lines = []
    if parsed.get("title"):
        lines.append(parsed["title"])
        lines.append("")
    lines.append(f"Relevance Verdict: {parsed.get('relevance_verdict') or 'N/A'}")
    lines.append("")
    lines.append("One-Line Takeaway:")
    lines.append(parsed.get("one_line_takeaway") or "N/A")
    lines.append("")
    lines.append("What's New:")
    whats_new = parsed.get("whats_new") or []
    if whats_new:
        lines.extend(f"- {item}" for item in whats_new)
    else:
        lines.append("- N/A")
    lines.append("")
    lines.append(f"Open filing: {filing_url}")
    return "\n".join(lines).strip()


def format_email_html(parsed: dict[str, Any], filing_url: str) -> str:
    whats_new_html = _render_list_html(parsed.get("whats_new") or []) or "<p>N/A</p>"
    title_html = f"<p style='margin:0 0 12px;font-weight:600'>{html.escape(parsed['title'])}</p>" if parsed.get("title") else ""
    return f"""
<html>
  <body style="margin:0;padding:24px;background:#f4efe6;color:#16212b;font-family:Georgia,serif;">
    <div style="max-width:680px;margin:0 auto;background:#fffdf9;border:1px solid rgba(22,33,43,0.12);border-radius:18px;padding:24px;">
      {title_html}
      <p style="margin:0 0 8px;font:12px 'IBM Plex Mono', monospace, monospace;text-transform:uppercase;letter-spacing:0.08em;color:#6d4c34;">Relevance Verdict</p>
      <p style="margin:0 0 18px;font-size:20px;line-height:1.35;">{html.escape(parsed.get("relevance_verdict") or "N/A")}</p>
      <p style="margin:0 0 8px;font:12px 'IBM Plex Mono', monospace, monospace;text-transform:uppercase;letter-spacing:0.08em;color:#6d4c34;">One-Line Takeaway</p>
      <p style="margin:0 0 18px;font-size:17px;line-height:1.5;">{html.escape(parsed.get("one_line_takeaway") or "N/A")}</p>
      <p style="margin:0 0 8px;font:12px 'IBM Plex Mono', monospace, monospace;text-transform:uppercase;letter-spacing:0.08em;color:#6d4c34;">What's New</p>
      <div style="margin:0 0 22px;font-size:15px;line-height:1.55;">{whats_new_html}</div>
      <a href="{html.escape(filing_url)}" style="display:inline-block;padding:12px 16px;border-radius:999px;background:#56675d;color:#ffffff;text-decoration:none;font:12px 'IBM Plex Mono', monospace, monospace;">Open Filing</a>
    </div>
  </body>
</html>
""".strip()
