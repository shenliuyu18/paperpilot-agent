from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.demo_data import GLOSSARY
from app.main import app
from app.services import translation


class GlossaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from app.glossary import GLOSSARY_CATEGORIES

        cls.categories = GLOSSARY_CATEGORIES

    def test_glossary_is_substantially_richer_and_categorized(self) -> None:
        self.assertGreaterEqual(len(GLOSSARY), 180)
        self.assertGreaterEqual(len(self.categories), 6)
        for category, terms in self.categories.items():
            self.assertTrue(terms, category)
        for term in ("transformer", "attention", "quantization", "OpenCitations", "singular value"):
            self.assertIn(term, GLOSSARY)

    def test_translation_glossary_hits_use_rich_terms(self) -> None:
        hits = translation._glossary_hits(
            "The transformer uses self-attention and quantization for efficient inference."
        )
        terms = {item["term"] for item in hits}
        self.assertTrue({"transformer", "quantization"} & terms)

    def test_glossary_api_filters_by_query_and_category(self) -> None:
        client = TestClient(app)
        response = client.get("/api/glossary")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertGreaterEqual(data["total"], 180)
        self.assertGreaterEqual(len(data["categories"]), 6)

        filtered = client.get("/api/glossary?q=quantiz")
        self.assertEqual(filtered.status_code, 200)
        items = filtered.json()["items"]
        self.assertTrue(items)
        self.assertTrue(any("quantiz" in item["term"].lower() for item in items))

        category = filtered.json()["categories"][0]["id"]
        by_category = client.get(f"/api/glossary?category={category}")
        self.assertEqual(by_category.status_code, 200)
        self.assertTrue(by_category.json()["items"])
        self.assertTrue(
            all(item["category"] == category for item in by_category.json()["items"])
        )


if __name__ == "__main__":
    unittest.main()
