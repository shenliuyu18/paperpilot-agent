from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import custom_sources, netguard


def _s2_payload() -> dict:
    return {
        "data": [
            {
                "paperId": "p1",
                "title": "Low-Power SAR ADC for Compute-in-Memory",
                "abstract": "A low-power ADC readout for CIM macros.",
                "authors": [{"name": "Alice Alpha"}, {"name": "Bob Beta"}],
                "year": 2024,
                "venue": "IEEE Test",
                "externalIds": {"DOI": "10.1000/test"},
                "url": "https://example.org/paper",
                "openAccessPdf": {"url": "https://example.org/paper.pdf"},
            }
        ]
    }


class NetGuardTests(unittest.TestCase):
    def test_public_https_is_allowed(self) -> None:
        url = netguard.validate_url("https://api.example.org/search", resolver=lambda host: ["93.184.216.34"])
        self.assertEqual(url, "https://api.example.org/search")

    def test_private_local_and_insecure_urls_are_rejected(self) -> None:
        for url in (
            "http://api.example.org/search",
            "https://localhost/search",
            "https://127.0.0.1/search",
            "https://user:pass@api.example.org/search",
            "https://api.example.org:8443/search",
        ):
            with self.assertRaises(netguard.UnsafeURLError):
                netguard.validate_url(url, resolver=lambda host: ["93.184.216.34"])
        with self.assertRaises(netguard.UnsafeURLError):
            netguard.validate_url("https://private.example.org", resolver=lambda host: ["10.0.0.5"])

    def test_private_fetch_can_be_enabled_for_local_tests(self) -> None:
        url = netguard.validate_url("https://127.0.0.1:443/search", allow_private=True)
        self.assertEqual(url, "https://127.0.0.1:443/search")


class CustomSourceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.config_file = root / "sources.json"
        self.secrets_file = root / "sources.secrets.json"
        self.env = patch.dict(
            os.environ,
            {
                "PAPERPILOT_SOURCES_FILE": str(self.config_file),
                "PAPERPILOT_SOURCES_SECRETS_FILE": str(self.secrets_file),
                "PAPERPILOT_ALLOW_PRIVATE_FETCH": "1",
            },
        )
        self.env.start()
        custom_sources.clear_cache()

    def tearDown(self) -> None:
        custom_sources.clear_cache()
        self.env.stop()
        self._tmp.cleanup()

    def test_create_preset_masks_and_isolates_secret(self) -> None:
        public = custom_sources.upsert(
            {
                "name": "My Semantic Scholar",
                "preset": "semantic_scholar",
                "token": "super-secret-token",
            }
        )
        self.assertTrue(public["id"].startswith("custom-"))
        self.assertTrue(public["has_token"])
        self.assertNotIn("super-secret-token", json.dumps(public, ensure_ascii=False))
        self.assertTrue(public["masked_token"].endswith("oken"))
        self.assertIn("super-secret-token", self.secrets_file.read_text(encoding="utf-8"))
        listed = custom_sources.list_sources()
        self.assertNotIn("super-secret-token", json.dumps(listed, ensure_ascii=False))

    def test_search_builds_request_and_normalizes_papers(self) -> None:
        public = custom_sources.upsert(
            {
                "name": "S2 Test",
                "preset": "semantic_scholar",
                "base_url": "https://api.example.org/search",
                "token": "abc123",
            }
        )
        seen = {}

        def fetcher(request: dict) -> dict:
            seen.update(request)
            return _s2_payload()

        papers = custom_sources.search(
            public["id"],
            "low power ADC",
            3,
            fetcher=fetcher,
            cache_dir=Path(self._tmp.name) / "cache",
        )
        self.assertEqual(seen["params"]["query"], "low power ADC")
        self.assertEqual(seen["params"]["limit"], 3)
        self.assertEqual(seen["headers"]["x-api-key"], "abc123")
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper["source"], f"custom:{public['id']}")
        self.assertEqual(paper["title"], "Low-Power SAR ADC for Compute-in-Memory")
        self.assertEqual(paper["authors"], ["Alice Alpha", "Bob Beta"])
        self.assertEqual(paper["doi"], "10.1000/test")
        self.assertEqual(paper["pdf_url"], "https://example.org/paper.pdf")
        self.assertEqual(paper["extraction_level"], "自定义来源 · 摘要级")

    def test_search_cache_avoids_second_fetch(self) -> None:
        public = custom_sources.upsert(
            {
                "name": "Cached",
                "preset": "semantic_scholar",
                "base_url": "https://api.example.org/search",
            }
        )
        cache = Path(self._tmp.name) / "cache"
        first = custom_sources.search(
            public["id"], "adc", 2, fetcher=lambda request: _s2_payload(), cache_dir=cache
        )
        second = custom_sources.search(
            public["id"],
            "adc",
            2,
            fetcher=lambda request: (_ for _ in ()).throw(AssertionError("must not fetch")),
            cache_dir=cache,
        )
        self.assertEqual(len(first), len(second))
        self.assertTrue(second[0].get("cached"))

    def test_disabled_source_cannot_search(self) -> None:
        public = custom_sources.upsert(
            {"name": "Off", "preset": "semantic_scholar", "base_url": "https://api.example.org/search"}
        )
        custom_sources.set_enabled(public["id"], False)
        with self.assertRaises(custom_sources.CustomSourceError):
            custom_sources.search(public["id"], "adc", 2)

    def test_update_without_token_keeps_existing_secret(self) -> None:
        public = custom_sources.upsert(
            {"name": "Keep", "preset": "core", "token": "secret-one"}
        )
        updated = custom_sources.upsert(
            {"id": public["id"], "name": "Keep Renamed", "preset": "core", "token": ""}
        )
        self.assertTrue(updated["has_token"])
        private = custom_sources.get_private(public["id"])
        self.assertEqual(private["secrets"]["token"], "secret-one")

    def test_api_crud_and_search_dispatch(self) -> None:
        client = TestClient(app)
        created = client.post(
            "/api/sources/custom",
            json={
                "name": "API Source",
                "preset": "semantic_scholar",
                "base_url": "https://api.example.org/search",
                "token": "api-secret",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        public = created.json()["source"]
        self.assertNotIn("api-secret", created.text)
        listed = client.get("/api/sources/custom")
        self.assertEqual(listed.status_code, 200)
        self.assertTrue(listed.json()["sources"])
        self.assertNotIn("api-secret", listed.text)

        fake = [{
            "id": "custom-api-p1",
            "title": "Custom Paper",
            "abstract": "abstract",
            "authors": ["A"],
            "year": 2024,
            "venue": "V",
            "doi": "10.1/custom",
            "url": "",
            "pdf_url": "",
            "source": f"custom:{public['id']}",
            "source_label": "API Source",
            "sections": [{"title": "Abstract", "page": 1, "text": "abstract"}],
            "full_text": False,
            "extraction_level": "自定义来源 · 摘要级",
        }]
        with patch.object(custom_sources, "search", return_value=fake):
            response = client.post(
                "/api/search",
                json={"query": "adc", "limit": 3, "source": f"custom:{public['id']}"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["papers"][0]["title"], "Custom Paper")

        bad = client.post("/api/search", json={"query": "adc", "limit": 3, "source": "custom:nope"})
        self.assertEqual(bad.status_code, 400)

        plan = client.post(
            "/api/search/plan",
            json={"query": "adc", "source": f"custom:{public['id']}"},
        )
        self.assertEqual(plan.status_code, 200, plan.text)
        self.assertEqual(plan.json()["source_label"], "API Source")


if __name__ == "__main__":
    unittest.main()
