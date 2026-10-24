from __future__ import annotations

import unittest

from app.services import citation_styles


def _draft(citations: list[dict], keys: list[list[str]] | None = None) -> dict:
    blocks = []
    for index, group in enumerate(keys or [[c["key"]] for c in citations], start=1):
        blocks.append({"id": f"blk-{index:03d}", "type": "paragraph",
                       "text": f"正文 {index}。", "citation_keys": group})
    return {
        "title": "草稿",
        "citation_style": "numeric",
        "sections": [{"id": "sec-001", "title": "引言", "blocks": blocks}],
        "citations": {c["key"]: c for c in citations},
    }


def _citation(key: str, **overrides) -> dict:
    data = {
        "key": key, "title": "A Study of Things", "authors": ["Alice Author", "Bob Builder"],
        "year": "2024", "venue": "Journal of Things", "doi": "10.1000/xyz",
        "url": "", "entry_type": "journal", "volume": "12", "issue": "3", "pages": "45-67",
        "publisher": "", "language": "en",
    }
    data.update(overrides)
    return data


class CitationStyleTests(unittest.TestCase):
    def test_legacy_numeric_reference_is_byte_identical(self) -> None:
        draft = _draft([_citation("a2024")])
        expected = "1. Alice Author, Bob Builder. A Study of Things. Journal of Things, 2024. 10.1000/xyz"
        self.assertEqual(citation_styles.references(draft, "numeric")[0]["text"], expected)

    def test_legacy_author_year_marker(self) -> None:
        draft = _draft([_citation("a2024")])
        self.assertEqual(citation_styles.marker(["a2024"], draft, "author-year"), " (Author, 2024)")

    def test_legacy_numeric_marker(self) -> None:
        draft = _draft([_citation("a2024"), _citation("b2023")])
        self.assertEqual(citation_styles.marker(["b2023", "a2024"], draft, "numeric"), " [2, 1]")

    def test_gb7714_journal_entry(self) -> None:
        draft = _draft([_citation("a2024")])
        text = citation_styles.references(draft, "gb7714")[0]["text"]
        self.assertTrue(text.startswith("[1] Alice Author, Bob Builder."), text)
        self.assertIn("[J]", text)
        self.assertIn("12(3)", text)
        self.assertIn("45-67", text)
        self.assertIn("DOI: 10.1000/xyz", text)

    def test_gb7714_preprint_uses_eb_ol_and_url(self) -> None:
        draft = _draft([_citation("p2025", entry_type="preprint", venue="arXiv preprint",
                                  url="https://arxiv.org/abs/2501.00001", volume="", issue="", pages="")])
        text = citation_styles.references(draft, "gb7714")[0]["text"]
        self.assertIn("[EB/OL]", text)
        self.assertIn("https://arxiv.org/abs/2501.00001", text)

    def test_gb7714_more_than_three_authors_uses_et_al(self) -> None:
        draft = _draft([_citation("m2024", authors=["A One", "B Two", "C Three", "D Four"])])
        text = citation_styles.references(draft, "gb7714")[0]["text"]
        self.assertIn("et al.", text)

    def test_ieee_entry_shape(self) -> None:
        draft = _draft([_citation("a2024")])
        text = citation_styles.references(draft, "ieee")[0]["text"]
        self.assertTrue(text.startswith('[1] Alice Author, Bob Builder,'), text)
        self.assertIn('"A Study of Things,"', text)
        self.assertIn("vol. 12", text)
        self.assertIn("no. 3", text)
        self.assertIn("pp. 45-67", text)

    def test_apa_is_alphabetical_and_uses_author_year_marker(self) -> None:
        draft = _draft([_citation("z2024", authors=["Zoe Zulu"]), _citation("a2023", authors=["Alice Author"])])
        rows = citation_styles.references(draft, "apa")
        self.assertEqual([row["key"] for row in rows], ["a2023", "z2024"])
        self.assertEqual(citation_styles.marker(["z2024"], draft, "apa"), " (Zulu, 2024)")
        self.assertIn("Author, A.", rows[0]["text"])

    def test_validate_reports_missing_and_unused(self) -> None:
        draft = _draft([_citation("a2024", title="", year=""), _citation("unused2024")],
                       keys=[["a2024"]])
        issues = citation_styles.validate(draft, "gb7714")
        fields = {(item["key"], item["field"], item["level"]) for item in issues}
        self.assertIn(("a2024", "title", "error"), fields)
        self.assertIn(("a2024", "year", "error"), fields)
        self.assertIn(("unused2024", "unused", "warn"), fields)

    def test_validate_rejects_unknown_style(self) -> None:
        issues = citation_styles.validate(_draft([]), "chicago")
        self.assertEqual(issues[0]["level"], "error")
        self.assertEqual(issues[0]["field"], "style")

    def test_gb7714_warns_about_missing_volume_and_pages(self) -> None:
        draft = _draft([_citation("a2024", volume="", issue="", pages="")])
        fields = {item["field"] for item in citation_styles.validate(draft, "gb7714")}
        self.assertIn("volume", fields)
        self.assertIn("pages", fields)

    def test_styles_meta_lists_all_five(self) -> None:
        ids = {item["id"] for item in citation_styles.styles_meta()}
        self.assertEqual(ids, {"numeric", "author-year", "gb7714", "apa", "ieee"})
