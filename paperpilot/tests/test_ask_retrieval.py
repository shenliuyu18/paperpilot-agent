from __future__ import annotations

import unittest

from app.services import ask


class AskRetrievalTests(unittest.TestCase):
    def test_answer_reports_retrieval_and_verifies_quotes(self) -> None:
        result = ask.answer("存算一体为什么需要低功耗 ADC？", scope="all", top_k=4)
        self.assertIn("retrieval", result)
        self.assertIn(result["retrieval"]["mode"], {"bm25", "hybrid", "embedding"})
        self.assertEqual(result["citation_verification"]["total"], len(result["citations"]))
        self.assertGreater(result["citation_verification"]["total"], 0)
        self.assertTrue(all("quote_verified" in citation for citation in result["citations"]))
        self.assertIn("verified", result["citation_verification"])
        self.assertEqual(
            result["citation_verification"]["verified"]
            + len(result["citation_verification"]["unverified_indices"]),
            result["citation_verification"]["total"],
        )

    def test_retrieval_meta_is_exposed(self) -> None:
        result = ask.answer("这些工作如何降低 ADC 能耗？", scope="all", top_k=3)
        meta = result["retrieval"]
        self.assertIn("passages", meta)
        self.assertIn("expanded_terms", meta)
        self.assertLessEqual(len(result["citations"]), 3)


if __name__ == "__main__":
    unittest.main()
