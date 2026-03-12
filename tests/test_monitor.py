import unittest

from private_credit_monitor.monitor import (
    FilingMatch,
    TrackedEntity,
    choose_entity,
    merge_match_history,
    normalize_filed_date,
    parse_master_index,
)
from private_credit_monitor.synopsis_output import parse_openarena_output


class MonitorTests(unittest.TestCase):
    def test_normalize_filed_date_compacts_daily_index_dates(self) -> None:
        self.assertEqual(normalize_filed_date("20260311"), "2026-03-11")

    def test_parse_master_index_extracts_accession_number(self) -> None:
        raw_text = "\n".join(
            [
                "Description: Daily Index",
                "CIK|Company Name|Form Type|Date Filed|Filename",
                "1837532|Apollo Debt Solutions BDC|8-K|20260311|edgar/data/1837532/0001193125-26-102160.txt",
            ]
        )
        entries = parse_master_index(raw_text)
        self.assertEqual(entries[0]["accession_number"], "0001193125-26-102160")
        self.assertEqual(entries[0]["filed_date"], "2026-03-11")

    def test_choose_entity_matches_reduced_names(self) -> None:
        entity = TrackedEntity(
            ticker="",
            name="North Haven Private Income Fund LLC",
            entity_type="Private",
            normalized_name="north haven private income fund llc",
            reduced_name="north haven private income",
        )
        entry = {"company_name": "North Haven Private Income Fund A LLC", "cik": "0000000000"}
        self.assertIs(choose_entity(entry, [entity]), entity)

    def test_parse_openarena_output_extracts_preview_sections(self) -> None:
        parsed = parse_openarena_output(
            "\n".join(
                [
                    "SEC Filing Analysis: Apollo Debt Solutions BDC - 8-K",
                    "A. Relevance Verdict",
                    "HIGHLY RELEVANT TO PRIVATE CREDIT",
                    "",
                    "B. One-Line Takeaway",
                    "Apollo disclosed a material private-credit development.",
                    "",
                    "C. What's New",
                    "Tender activity disclosed for the quarter.",
                    "",
                    "D. Most Important Points",
                    "One important point.",
                ]
            )
        )
        self.assertEqual(parsed["wire_recommendation"], "HIGH")
        self.assertEqual(parsed["relevance_verdict"], "HIGHLY RELEVANT TO PRIVATE CREDIT")
        self.assertEqual(parsed["one_line_takeaway"], "Apollo disclosed a material private-credit development.")
        self.assertEqual(parsed["whats_new"][0], "Tender activity disclosed for the quarter.")

    def test_parse_openarena_output_handles_markdown_section_headers(self) -> None:
        parsed = parse_openarena_output(
            "\n".join(
                [
                    "SEC Filing Analysis: Example Fund - 8-K",
                    "**A. Relevance Verdict**",
                    "HIGHLY RELEVANT TO PRIVATE CREDIT",
                    "",
                    "**B. One-Line Takeaway:**",
                    "This is the takeaway.",
                    "",
                    "**C. What's New**",
                    "- New point one",
                    "- New point two",
                ]
            )
        )
        self.assertEqual(parsed["relevance_verdict"], "HIGHLY RELEVANT TO PRIVATE CREDIT")
        self.assertEqual(parsed["one_line_takeaway"], "This is the takeaway.")
        self.assertEqual(parsed["whats_new"][0], "New point one")

    def test_parse_openarena_output_handles_hash_markdown_headers(self) -> None:
        parsed = parse_openarena_output(
            "\n".join(
                [
                    "---",
                    "",
                    "## SEC Filing Analysis: Example Fund - 8-K / March 11, 2026",
                    "",
                    "---",
                    "",
                    "### A. Relevance Verdict",
                    "",
                    "**HIGHLY RELEVANT TO PRIVATE CREDIT**",
                    "",
                    "### B. One-Line Takeaway",
                    "",
                    "Example takeaway.",
                    "",
                    "### C. What's New",
                    "",
                    "- First point",
                    "- Second point",
                ]
            )
        )
        self.assertEqual(parsed["title"], "SEC Filing Analysis: Example Fund - 8-K / March 11, 2026")
        self.assertEqual(parsed["relevance_verdict"], "HIGHLY RELEVANT TO PRIVATE CREDIT")
        self.assertEqual(parsed["one_line_takeaway"], "Example takeaway.")
        self.assertEqual(parsed["whats_new"][0], "First point")

    def test_merge_match_history_preserves_existing_archive(self) -> None:
        existing_payloads = [
            {
                "accession_number": "0000000001-26-000001",
                "cik": "1",
                "company_name": "Older Fund",
                "form_type": "8-K",
                "filed_date": "2026-03-10",
                "filing_url": "https://example.com/old.txt",
                "index_url": "https://example.com/old-index.html",
                "tracked_name": "Older Fund",
                "tracked_type": "Private",
                "matched_keywords": ["private credit"],
                "description": "older description",
                "openarena_output": "old output",
                "openarena_title": "Older Title",
                "relevance_verdict": "RELEVANT",
                "one_line_takeaway": "Older takeaway",
                "whats_new": ["Older point"],
                "remaining_sections": {},
                "wire_recommendation": "MEDIUM",
                "analysis_source": "openarena",
                "openarena_error": None,
                "source": "sec-daily-index",
            }
        ]
        recent_matches = [
            FilingMatch(
                accession_number="0000000002-26-000002",
                cik="2",
                company_name="Newer Fund",
                form_type="8-K",
                filed_date="2026-03-11",
                filing_url="https://example.com/new.txt",
                index_url="https://example.com/new-index.html",
                tracked_name="Newer Fund",
                tracked_type="Private",
                matched_keywords=["direct lending"],
                description="new description",
                openarena_output="new output",
                openarena_title="Newer Title",
                relevance_verdict="HIGHLY RELEVANT TO PRIVATE CREDIT",
                one_line_takeaway="Newer takeaway",
                whats_new=["Newer point"],
                remaining_sections={},
                wire_recommendation="HIGH",
                analysis_source="openarena",
                openarena_error=None,
                source="sec-daily-index",
            )
        ]

        merged = merge_match_history(existing_payloads, recent_matches, max_results=10)

        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0].accession_number, "0000000002-26-000002")
        self.assertEqual(merged[1].accession_number, "0000000001-26-000001")


if __name__ == "__main__":
    unittest.main()
