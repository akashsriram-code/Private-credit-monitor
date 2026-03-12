import unittest

from private_credit_monitor.monitor import (
    TrackedEntity,
    choose_entity,
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


if __name__ == "__main__":
    unittest.main()
