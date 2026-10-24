from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import citations


def _paper(doi: str = "10.1000/main") -> dict:
    return {
        "id": "crossref-main",
        "doi": doi,
        "title": "A Test Paper",
        "abstract": "A test abstract.",
        "authors": ["A. Author"],
        "year": 2025,
        "venue": "Test Venue",
        "url": "https://doi.org/" + doi,
        "sections": [{"title": "Abstract", "page": 1, "text": "A test abstract."}],
        "source": "crossref",
    }


def _meta(doi: str) -> dict:
    return {
        "id": "crossref-" + doi.replace("/", "-").replace(".", "-"),
        "doi": doi,
        "title": "Paper " + doi,
        "authors": ["B. Author"],
        "year": 2024,
        "venue": "Venue",
        "url": "https://doi.org/" + doi,
        "pdf_url": "",
        "abstract": "Abstract " + doi,
        "sections": [{"title": "Abstract", "page": 1, "text": "Abstract " + doi}],
        "source": "crossref",
        "full_text": False,
        "extraction_level": "Crossref 摘要级",
    }


class CitationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_builds_reference_and_citation_lists_with_metadata(self) -> None:
        def fake_fetch(url: str):
            if url.endswith("/references/10.1000/main"):
                return [{"citing": "10.1000/main", "cited": "10.1000/ref1"}, {"citing": "10.1000/main", "cited": "10.1000/ref1"}]
            if url.endswith("/citations/10.1000/main"):
                return [{"citing": "10.1000/cite1", "cited": "10.1000/main"}]
            raise AssertionError(url)

        def fake_metadata(dois: list[str]) -> list[dict]:
            return [_meta(doi) for doi in dois]

        result = citations.citation_network(
            _paper(),
            limit=5,
            cache_dir=self.cache_dir,
            fetch_json=fake_fetch,
            metadata_fetcher=fake_metadata,
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["reference_count"], 1)
        self.assertEqual(result["citation_count"], 1)
        self.assertEqual(len(result["references"]), 1)
        self.assertEqual(len(result["citations"]), 1)
        self.assertEqual(result["references"][0]["doi"], "10.1000/ref1")
        self.assertEqual(result["citations"][0]["doi"], "10.1000/cite1")
        self.assertTrue((self.cache_dir / "10.1000_main.json").exists())

    def test_missing_doi_returns_available_false(self) -> None:
        paper = _paper("")
        result = citations.citation_network(paper, cache_dir=self.cache_dir)
        self.assertFalse(result["available"])
        self.assertIn("DOI", result["reason"])

    def test_cached_citation_network_avoids_refetch(self) -> None:
        def fake_fetch(url: str):
            if "references" in url:
                return [{"citing": "10.1000/main", "cited": "10.1000/ref1"}]
            return []
        def fake_metadata(dois):
            return [_meta(doi) for doi in dois]
        citations.citation_network(
            _paper(), cache_dir=self.cache_dir, fetch_json=fake_fetch, metadata_fetcher=fake_metadata
        )
        again = citations.citation_network(
            _paper(),
            limit=5,
            cache_dir=self.cache_dir,
            fetch_json=lambda url: (_ for _ in ()).throw(AssertionError("must not fetch")),
            metadata_fetcher=lambda dois: [],
        )
        self.assertTrue(again["cached"])
        self.assertEqual(len(again["references"]), 1)

    def test_metadata_failure_still_returns_doi_items(self) -> None:
        def fake_fetch(url: str):
            if "references" in url:
                return [{"citing": "10.1000/main", "cited": "10.1000/ref1"}]
            return []
        result = citations.citation_network(
            _paper(),
            cache_dir=self.cache_dir,
            fetch_json=fake_fetch,
            metadata_fetcher=lambda dois: [],
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["references"][0]["doi"], "10.1000/ref1")
        self.assertIn("ref1", result["references"][0]["title"])


class CitationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_citation_endpoint_returns_service_payload(self) -> None:
        fake = {
            "available": True,
            "doi": "10.1000/main",
            "references": [],
            "citations": [],
            "reference_count": 0,
            "citation_count": 0,
        }
        with patch.object(citations, "citation_network", return_value=fake):
            response = self.client.get("/api/citations/2606.08947v1")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["available"])

    def test_citation_endpoint_missing_paper(self) -> None:
        response = self.client.get("/api/citations/does-not-exist")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
