import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from private_credit_monitor.filings_analysis import (
    FilingAnalysisSession,
    build_openarena_documents,
    build_live_session,
    build_session_from_issue,
    load_persisted_sessions,
    parse_live_request_payload,
    persist_live_session,
    parse_issue_request,
    select_filings_from_rows,
    transition_session,
    upsert_session_archive,
)
from private_credit_monitor.monitor import TrackedEntity


class FilingsAnalysisTests(unittest.TestCase):
    def test_parse_issue_request_extracts_entities_and_question(self) -> None:
        parsed = parse_issue_request(
            "\n".join(
                [
                    "### Tracked Entities",
                    "- Ares Capital Corporation",
                    "- Apollo Debt Solutions BDC",
                    "",
                    "### Filing Type",
                    "10-K",
                    "",
                    "### Lookback Count",
                    "2",
                    "",
                    "### What are you looking for?",
                    "Compare leverage and liquidity trends.",
                ]
            )
        )
        self.assertEqual(parsed["filing_type"], "10-K")
        self.assertEqual(parsed["lookback_count"], 2)
        self.assertEqual(parsed["entities"][1], "Apollo Debt Solutions BDC")
        self.assertEqual(parsed["question"], "Compare leverage and liquidity trends.")

    def test_build_session_from_issue_resolves_entities(self) -> None:
        entities = [
            TrackedEntity(
                ticker="ARCC",
                name="Ares Capital Corporation",
                entity_type="Public",
                normalized_name="ares capital corporation",
                reduced_name="ares capital",
            )
        ]
        session = build_session_from_issue(
            {
                "number": 17,
                "title": "[Filing Analysis] ARCC",
                "html_url": "https://example.com/issues/17",
                "body": "\n".join(
                    [
                        "### Tracked Entities",
                        "- Ares Capital Corporation",
                        "",
                        "### Filing Type",
                        "10-Q",
                        "",
                        "### Lookback Count",
                        "3",
                        "",
                        "### What are you looking for?",
                        "Look for portfolio and non-accrual changes.",
                    ]
                ),
            },
            entities,
        )
        self.assertEqual(session.id, "issue-17")
        self.assertEqual(session.entities, ["Ares Capital Corporation"])
        self.assertEqual(session.status, "queued")

    def test_parse_live_request_payload_validates_core_fields(self) -> None:
        parsed = parse_live_request_payload(
            {
                "entities": ["Ares Capital Corporation", "Apollo Debt Solutions BDC"],
                "filing_type": "10-Q",
                "lookback_count": 4,
                "question": "Find changes in leverage and non-accruals.",
            }
        )
        self.assertEqual(parsed["filing_type"], "10-Q")
        self.assertEqual(parsed["lookback_count"], 4)
        self.assertIn("Apollo Debt Solutions BDC", parsed["entities"])

    def test_build_live_session_marks_request_source(self) -> None:
        entities = [
            TrackedEntity(
                ticker="ARCC",
                name="Ares Capital Corporation",
                entity_type="Public",
                normalized_name="ares capital corporation",
                reduced_name="ares capital",
            )
        ]
        session = build_live_session(
            {
                "entities": ["Ares Capital Corporation"],
                "filing_type": "10-K",
                "lookback_count": 2,
                "question": "Compare liquidity trends.",
            },
            entities,
        )
        self.assertEqual(session.request_source, "live-api")
        self.assertEqual(session.status, "queued")
        self.assertTrue(session.id.startswith("live-"))

    def test_select_filings_from_rows_uses_calendar_years_for_10k(self) -> None:
        rows = [
            {"form": "10-K", "accession_number": "0001-26-000001", "filed_date": "2026-02-10", "primary_document": "", "description": ""},
            {"form": "10-K/A", "accession_number": "0001-26-000002", "filed_date": "2026-03-10", "primary_document": "", "description": ""},
            {"form": "10-K", "accession_number": "0001-25-000003", "filed_date": "2025-02-10", "primary_document": "", "description": ""},
            {"form": "NT 10-K", "accession_number": "0001-25-000004", "filed_date": "2025-03-01", "primary_document": "", "description": ""},
        ]
        filings = select_filings_from_rows(
            rows=rows,
            cik="1000",
            entity_name="Ares Capital Corporation",
            filing_type="10-K",
            lookback_count=2,
            reference_date=date(2026, 3, 16),
        )
        self.assertEqual(len(filings), 2)
        self.assertEqual(filings[0].accession_number, "0001-26-000001")
        self.assertEqual(filings[1].period_key, "2025")

    def test_select_filings_from_rows_uses_calendar_quarters_for_10q(self) -> None:
        rows = [
            {"form": "10-Q", "accession_number": "0001-26-000010", "filed_date": "2026-02-15", "primary_document": "", "description": ""},
            {"form": "10-Q", "accession_number": "0001-25-000011", "filed_date": "2025-11-10", "primary_document": "", "description": ""},
            {"form": "10-Q/A", "accession_number": "0001-25-000012", "filed_date": "2025-11-15", "primary_document": "", "description": ""},
            {"form": "10-Q", "accession_number": "0001-25-000013", "filed_date": "2025-08-09", "primary_document": "", "description": ""},
        ]
        filings = select_filings_from_rows(
            rows=rows,
            cik="1000",
            entity_name="Ares Capital Corporation",
            filing_type="10-Q",
            lookback_count=2,
            reference_date=date(2026, 3, 16),
        )
        self.assertEqual(len(filings), 2)
        self.assertEqual(filings[0].period_key, "2026-Q1")
        self.assertEqual(filings[1].period_key, "2025-Q4")
        self.assertEqual(filings[1].accession_number, "0001-25-000011")

    def test_build_openarena_documents_keeps_question_payload_ready(self) -> None:
        filings = select_filings_from_rows(
            rows=[
                {"form": "10-K", "accession_number": "0001-26-000001", "filed_date": "2026-02-10", "primary_document": "annual.htm", "description": ""}
            ],
            cik="1000",
            entity_name="Ares Capital Corporation",
            filing_type="10-K",
            lookback_count=1,
            reference_date=date(2026, 3, 16),
        )
        filings[0].full_text = "This filing discusses leverage, liquidity, and non-accrual investments."
        docs = build_openarena_documents(filings)
        self.assertEqual(docs[0]["id"], "0001-26-000001")
        self.assertIn("leverage", docs[0]["text"])
        self.assertEqual(docs[0]["metadata"]["entity_name"], "Ares Capital Corporation")

    def test_upsert_session_archive_and_transition_persist_status(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sessions.json"
            session = FilingAnalysisSession(
                id="issue-1",
                issue_number=1,
                issue_title="[Filing Analysis] Test",
                issue_url="https://example.com/issues/1",
                status="queued",
                entities=["Ares Capital Corporation"],
                filing_type="10-K",
                lookback_count=1,
                question="What changed?",
            )
            upsert_session_archive(path, session)
            transition_session(session, "processing")
            upsert_session_archive(path, session)

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["status"], "processing")
            self.assertEqual(payload[0]["id"], "issue-1")

    def test_persist_live_session_falls_back_to_local_archive(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sessions.json"
            session = FilingAnalysisSession(
                id="live-1",
                issue_number=None,
                issue_title="Live filings analysis",
                issue_url="",
                status="complete",
                entities=["Ares Capital Corporation"],
                filing_type="10-K",
                lookback_count=1,
                question="What changed?",
                answer="Example answer",
                request_source="live-api",
            )
            persist_live_session(session, sessions_path=path)
            payload = load_persisted_sessions(sessions_path=path)
            self.assertEqual(payload[0]["id"], "live-1")
            self.assertEqual(payload[0]["answer"], "Example answer")


if __name__ == "__main__":
    unittest.main()
