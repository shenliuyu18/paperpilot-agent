from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import library


def _paper(pid: str, title: str = "A Test Paper", **extra) -> dict:
    data = {
        "id": pid,
        "title": title,
        "authors": ["Alice Alpha", "Bob Beta"],
        "year": 2024,
        "venue": "Test Journal",
        "doi": "10.1000/" + pid,
        "url": "https://doi.org/10.1000/" + pid,
        "abstract": "A sufficiently detailed abstract for citation health checks.",
        "source": "crossref",
    }
    data.update(extra)
    return data


def _crossref(title: str, doi: str) -> dict:
    return {
        "title": [title],
        "DOI": doi,
        "issued": {"date-parts": [[2024]]},
        "container-title": ["Test Journal"],
    }


class LibraryHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.library_file = self.root / "library.json"
        self.cache_dir = self.root / "doi-cache"
        self.env = patch.dict(os.environ, {"PAPERPILOT_LIBRARY_FILE": str(self.library_file)})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self._tmp.cleanup()

    def test_missing_metadata_is_scored_and_explained(self) -> None:
        library.save(_paper("incomplete", authors=[], year=None, venue="", doi="", url="", abstract=""))
        result = library.health(cache_dir=self.cache_dir)
        self.assertEqual(result["total"], 1)
        item = result["items"][0]
        self.assertLess(item["score"], 60)
        issue_ids = {issue["id"] for issue in item["issues"]}
        self.assertIn("missing_authors", issue_ids)
        self.assertIn("missing_year", issue_ids)
        self.assertIn("missing_doi", issue_ids)
        self.assertTrue(result["actions"])
        self.assertEqual(result["doi_checked"], 0)

    def test_duplicate_titles_are_grouped(self) -> None:
        library.save(_paper("one", title="Low-Power ADC for CIM"))
        library.save(_paper("two", title="Low-Power ADC for CIM", doi="10.1000/other"))
        result = library.health(cache_dir=self.cache_dir)
        self.assertEqual(result["duplicate_groups"], 1)
        self.assertTrue(all(item["duplicate"] for item in result["items"]))
        self.assertTrue(result["groups"])

    def test_doi_verification_marks_matching_metadata(self) -> None:
        library.save(_paper("verified", title="Low-Power ADC for CIM"))
        calls = []

        def fetcher(doi: str):
            calls.append(doi)
            return _crossref("Low-Power ADC for CIM", doi)

        result = library.health(verify_dois=True, fetcher=fetcher, cache_dir=self.cache_dir)
        self.assertEqual(result["doi_checked"], 1)
        self.assertEqual(result["doi_verified"], 1)
        item = result["items"][0]
        self.assertEqual(item["doi_status"], "verified")
        self.assertGreaterEqual(item["score"], 75)
        cached = library.health(verify_dois=True, fetcher=fetcher, cache_dir=self.cache_dir)
        self.assertEqual(cached["doi_verified"], 1)
        self.assertEqual(len(calls), 1)

    def test_doi_mismatch_is_flagged(self) -> None:
        library.save(_paper("mismatch", title="Low-Power ADC for CIM"))

        def fetcher(doi: str):
            return _crossref("A Completely Different Study of Databases", doi)

        result = library.health(verify_dois=True, fetcher=fetcher, cache_dir=self.cache_dir)
        item = result["items"][0]
        self.assertEqual(item["doi_status"], "mismatch")
        self.assertEqual(result["doi_mismatch"], 1)
        self.assertTrue(any(issue["id"] == "doi_mismatch" for issue in item["issues"]))

    def test_verification_is_opt_in(self) -> None:
        library.save(_paper("quiet"))
        result = library.health(
            fetcher=lambda doi: (_ for _ in ()).throw(AssertionError("must not fetch")),
            cache_dir=self.cache_dir,
        )
        self.assertEqual(result["doi_checked"], 0)
        self.assertEqual(result["items"][0]["doi_status"], "unchecked")


if __name__ == "__main__":
    unittest.main()
