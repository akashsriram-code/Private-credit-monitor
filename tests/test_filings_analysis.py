import json
import smtplib
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from private_credit_monitor.filings_analysis import (
    EmailDeliveryResult,
    FilingAnalysisSession,
    OpenArenaRequestError,
    build_openarena_documents,
    build_live_session,
    build_session_from_issue,
    call_openarena_inference_with_retries,
    estimate_openarena_input_tokens,
    fetch_entity_filings,
    filing_text_to_pdf_bytes,
    load_persisted_sessions,
    parse_live_request_payload,
    prepare_openarena_uploads,
    persist_live_session,
    parse_issue_request,
    resolved_timeout_seconds,
    run_live_analysis,
    send_filings_analysis_email,
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

    def test_parse_live_request_payload_accepts_quarterly_annual_combo(self) -> None:
        parsed = parse_live_request_payload(
            {
                "entities": ["Ares Capital Corporation"],
                "filing_type": "10-Q + 10-K",
                "lookback_count": 1,
                "question": "Compare the current 10-Q with the annual filing.",
            }
        )
        self.assertEqual(parsed["filing_type"], "10-Q+10-K")

    def test_parse_live_request_payload_accepts_optional_email(self) -> None:
        parsed = parse_live_request_payload(
            {
                "entities": ["Ares Capital Corporation"],
                "filing_type": "10-K",
                "lookback_count": 2,
                "question": "Compare liquidity trends.",
                "email": "analyst@example.com",
            }
        )
        self.assertEqual(parsed["email"], "analyst@example.com")

    def test_parse_live_request_payload_rejects_invalid_email(self) -> None:
        with self.assertRaisesRegex(ValueError, "email must be a valid email address"):
            parse_live_request_payload(
                {
                    "entities": ["Ares Capital Corporation"],
                    "filing_type": "10-K",
                    "lookback_count": 2,
                    "question": "Compare liquidity trends.",
                    "email": "not-an-email",
                }
            )

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

    def test_fetch_entity_filings_combines_current_10q_with_latest_10k(self) -> None:
        entity = TrackedEntity(
            ticker="ARCC",
            name="Ares Capital Corporation",
            entity_type="Public",
            normalized_name="ares capital corporation",
            reduced_name="ares capital",
            ciks={"1000"},
        )
        submission = {
            "filings": {
                "recent": {
                    "form": ["10-Q", "10-K", "10-Q"],
                    "accessionNumber": ["0001-26-000010", "0001-26-000001", "0001-25-000011"],
                    "filingDate": ["2026-05-08", "2026-02-10", "2025-11-10"],
                    "primaryDocument": ["q1.htm", "annual.htm", "q3.htm"],
                    "primaryDocDescription": ["Quarterly report", "Annual report", "Quarterly report"],
                }
            }
        }

        with patch("private_credit_monitor.filings_analysis.fetch_submission_json", return_value=submission):
            filings = fetch_entity_filings(
                entity=entity,
                filing_type="10-Q+10-K",
                lookback_count=1,
                user_agent="Private-Credit-Monitor/1.0 user@example.com",
                reference_date=date(2026, 5, 18),
            )

        self.assertEqual([filing.filing_type for filing in filings], ["10-Q", "10-K"])
        self.assertEqual(filings[0].accession_number, "0001-26-000010")
        self.assertEqual(filings[1].accession_number, "0001-26-000001")

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

    def test_filing_text_to_pdf_bytes_generates_pdf_payload(self) -> None:
        pdf_bytes = filing_text_to_pdf_bytes(
            "Ares Capital Corporation 10-K filed 2026-02-10",
            "https://example.com/filing",
            "Liquidity improved year over year.\n\nCash balances increased.",
        )
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertGreater(len(pdf_bytes), 500)

    def test_prepare_openarena_uploads_keeps_small_filings_untrimmed(self) -> None:
        filing = select_filings_from_rows(
            rows=[
                {"form": "10-K", "accession_number": "0001-26-000001", "filed_date": "2026-02-10", "primary_document": "annual.htm", "description": ""}
            ],
            cik="1000",
            entity_name="Ares Capital Corporation",
            filing_type="10-K",
            lookback_count=1,
            reference_date=date(2026, 3, 16),
        )[0]
        filing.full_text = "Liquidity improved year over year. Cash balances increased."

        prepare_openarena_uploads([filing], "Compare liquidity trends.", token_budget=50_000)

        self.assertEqual(filing.upload_text, filing.full_text)
        self.assertTrue(filing.upload_bytes.startswith(b"%PDF"))

    def test_prepare_openarena_uploads_trims_large_filings_to_budget(self) -> None:
        filings = select_filings_from_rows(
            rows=[
                {"form": "10-K", "accession_number": "0001-26-000001", "filed_date": "2026-02-10", "primary_document": "annual.htm", "description": ""},
                {"form": "10-K", "accession_number": "0001-25-000002", "filed_date": "2025-02-10", "primary_document": "annual.htm", "description": ""},
            ],
            cik="1000",
            entity_name="Ares Capital Corporation",
            filing_type="10-K",
            lookback_count=2,
            reference_date=date(2026, 3, 16),
        )
        filings[0].full_text = ("portfolio leverage liquidity nonaccrual " * 3000).strip()
        filings[1].full_text = ("debt maturity covenant secured assets " * 3000).strip()

        prepare_openarena_uploads(filings, "Compare leverage and liquidity.", token_budget=30_000)

        total_upload_tokens = sum(estimate_openarena_input_tokens(filing.upload_text) for filing in filings)
        self.assertLess(total_upload_tokens, 30_000)
        self.assertTrue(all("shortened before OpenArena upload" in filing.upload_text for filing in filings))
        self.assertTrue(all(filing.upload_bytes.startswith(b"%PDF") for filing in filings))

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
            self.assertNotIn("email", payload[0])

    @patch("private_credit_monitor.filings_analysis.persist_live_session")
    @patch("private_credit_monitor.filings_analysis.process_single_session")
    @patch("private_credit_monitor.filings_analysis.hydrate_tracked_entities_with_ciks")
    def test_run_live_analysis_sends_email_when_requested(
        self,
        hydrate_tracked_entities_with_ciks_mock,
        process_single_session_mock,
        persist_live_session_mock,
    ) -> None:
        entities = [
            TrackedEntity(
                ticker="ARCC",
                name="Ares Capital Corporation",
                entity_type="Public",
                normalized_name="ares capital corporation",
                reduced_name="ares capital",
            )
        ]
        hydrate_tracked_entities_with_ciks_mock.return_value = entities
        process_single_session_mock.return_value = FilingAnalysisSession(
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
        with patch("private_credit_monitor.filings_analysis.send_filings_analysis_email", return_value=(True, None)) as email_mock:
            with patch.dict(
                "os.environ",
                {
                    "SEC_USER_AGENT": "Private-Credit-Monitor/1.0 user@example.com",
                    "OPENARENA_BEARER_TOKEN": "token",
                    "OPENARENA_FILINGS_WORKFLOW_ID": "workflow",
                },
                clear=False,
            ):
                session, email_delivery = run_live_analysis(
                    {
                        "entities": ["Ares Capital Corporation"],
                        "filing_type": "10-K",
                        "lookback_count": 1,
                        "question": "What changed?",
                        "email": "analyst@example.com",
                    }
                )
        self.assertEqual(session.status, "complete")
        self.assertEqual(email_delivery.status, "sent")
        email_mock.assert_called_once()
        sent_session, sent_email = email_mock.call_args[0]
        self.assertEqual(sent_session.id, "live-1")
        self.assertEqual(sent_email, "analyst@example.com")
        self.assertTrue(any("Sending one-off analysis email" in line for line in session.progress_log))
        self.assertTrue(any("Analysis email sent successfully." in line for line in session.progress_log))
        persisted_session = persist_live_session_mock.call_args[0][0]
        self.assertFalse(hasattr(persisted_session, "email"))

    @patch("private_credit_monitor.filings_analysis.persist_live_session")
    @patch("private_credit_monitor.filings_analysis.process_single_session")
    @patch("private_credit_monitor.filings_analysis.hydrate_tracked_entities_with_ciks")
    def test_run_live_analysis_email_failure_is_non_fatal(
        self,
        hydrate_tracked_entities_with_ciks_mock,
        process_single_session_mock,
        persist_live_session_mock,
    ) -> None:
        entities = [
            TrackedEntity(
                ticker="ARCC",
                name="Ares Capital Corporation",
                entity_type="Public",
                normalized_name="ares capital corporation",
                reduced_name="ares capital",
            )
        ]
        hydrate_tracked_entities_with_ciks_mock.return_value = entities
        process_single_session_mock.return_value = FilingAnalysisSession(
            id="live-2",
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
        with patch("private_credit_monitor.filings_analysis.send_filings_analysis_email", return_value=(False, "SMTP unavailable")):
            with patch.dict(
                "os.environ",
                {
                    "SEC_USER_AGENT": "Private-Credit-Monitor/1.0 user@example.com",
                    "OPENARENA_BEARER_TOKEN": "token",
                    "OPENARENA_FILINGS_WORKFLOW_ID": "workflow",
                },
                clear=False,
            ):
                session, email_delivery = run_live_analysis(
                    {
                        "entities": ["Ares Capital Corporation"],
                        "filing_type": "10-K",
                        "lookback_count": 1,
                        "question": "What changed?",
                        "email": "analyst@example.com",
                    }
                )
        self.assertEqual(session.status, "complete")
        self.assertEqual(email_delivery.status, "failed")
        self.assertEqual(email_delivery.error, "SMTP unavailable")
        self.assertTrue(any("Analysis email failed: SMTP unavailable" in line for line in session.progress_log))
        persist_live_session_mock.assert_called_once()

    def test_send_filings_analysis_email_handles_smtp_exception(self) -> None:
        session = FilingAnalysisSession(
            id="live-3",
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
        with patch.dict(
            "os.environ",
            {
                "SMTP_HOST": "smtp.example.com",
                "SMTP_PORT": "587",
                "SMTP_USERNAME": "reporter@example.com",
                "SMTP_PASSWORD": "secret",
                "FROM_EMAIL": "reporter@example.com",
                "ALERT_EMAIL_TO": "",
            },
            clear=False,
        ):
            with patch("private_credit_monitor.filings_analysis.send_messages", side_effect=smtplib.SMTPException("relay denied")):
                sent, error = send_filings_analysis_email(session, "analyst@example.com")

        self.assertFalse(sent)
        self.assertIn("relay denied", error)

    def test_resolved_timeout_seconds_uses_300_second_minimum(self) -> None:
        with patch.dict("os.environ", {"OPENARENA_TIMEOUT_SECONDS": "180"}, clear=False):
            self.assertEqual(resolved_timeout_seconds(), 300)

    def test_call_openarena_inference_with_retries_retries_http_504(self) -> None:
        session = FilingAnalysisSession(
            id="live-retry",
            issue_number=None,
            issue_title="Live filings analysis",
            issue_url="",
            status="processing",
            entities=["Ares Capital Corporation"],
            filing_type="10-K",
            lookback_count=1,
            question="What changed?",
            request_source="live-api",
        )
        responses = [
            OpenArenaRequestError("https://aiopenarena.thomsonreuters.com/v3/inference", 504, '{"message":"Endpoint request timed out"}'),
            {"result": {"answer": "Recovered answer"}},
        ]

        def fake_post_json(url, bearer_token, payload, timeout_seconds):
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with patch("private_credit_monitor.filings_analysis.post_json", side_effect=fake_post_json) as post_json_mock:
            with patch("private_credit_monitor.filings_analysis.time.sleep") as sleep_mock:
                response = call_openarena_inference_with_retries(
                    session=session,
                    base_url="https://aiopenarena.thomsonreuters.com",
                    bearer_token="token",
                    inference_payload={"query": "What changed?"},
                    timeout_seconds=300,
                )

        self.assertEqual(response, {"result": {"answer": "Recovered answer"}})
        self.assertEqual(post_json_mock.call_count, 2)
        sleep_mock.assert_called_once_with(5)
        self.assertTrue(any("attempt 1/3" in line for line in session.progress_log))
        self.assertTrue(any("retrying in 5 seconds" in line for line in session.progress_log))

    def test_call_openarena_inference_reports_token_limit_without_retrying(self) -> None:
        session = FilingAnalysisSession(
            id="live-token-limit",
            issue_number=None,
            issue_title="Live filings analysis",
            issue_url="",
            status="processing",
            entities=["Ares Capital Corporation"],
            filing_type="10-K",
            lookback_count=1,
            question="What changed?",
            request_source="live-api",
        )
        error = OpenArenaRequestError(
            "https://aiopenarena.thomsonreuters.com/v3/inference",
            500,
            "The input token count exceeds the maximum number of tokens allowed 1048576.",
        )

        with patch("private_credit_monitor.filings_analysis.post_json", side_effect=error) as post_json_mock:
            with self.assertRaisesRegex(RuntimeError, "uploaded filings still exceeded"):
                call_openarena_inference_with_retries(
                    session=session,
                    base_url="https://aiopenarena.thomsonreuters.com",
                    bearer_token="token",
                    inference_payload={"query": "What changed?"},
                    timeout_seconds=300,
                )

        post_json_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
