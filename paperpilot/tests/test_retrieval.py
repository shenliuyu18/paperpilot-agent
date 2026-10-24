from __future__ import annotations

import unittest

from app.services import retrieval


def _passage(pid: str, text: str, section: str = "Abstract") -> dict:
    return {
        "paper_id": pid,
        "title": f"Paper {pid}",
        "section": section,
        "page": 1,
        "text": text,
        "score": 1.0,
    }


class RetrievalTests(unittest.TestCase):
    def test_hybrid_prefers_expanded_term_match(self) -> None:
        relevant = _passage("P1", "本文提出一种低功耗模数转换器读出电路，用于存算一体阵列。")
        irrelevant = _passage("P2", "图像分类网络在自然图像数据集上训练。")
        passages = [relevant, irrelevant]
        ranked, meta = retrieval.rank(
            "低功耗 ADC 读出电路",
            passages,
            [irrelevant, relevant],
            top_k=2,
        )
        self.assertEqual(ranked[0]["paper_id"], "P1")
        self.assertEqual(meta["mode"], "hybrid")
        self.assertEqual(meta["passages"], 2)
        self.assertGreater(meta["base"], 0)
        self.assertTrue(meta["fused"])

    def test_verify_citations(self) -> None:
        ranked = [
            _passage("P1", "本文提出一种低功耗 ADC 读出电路，并报告了测试条件。"),
            _passage("P2", "另一篇论文讨论神经网络训练。"),
        ]
        citations = [
            {"index": 1, "paper_id": "P1", "quote": "低功耗 ADC 读出电路"},
            {"index": 2, "paper_id": "P1", "quote": "不存在的句子"},
            {"index": 3, "paper_id": "P2", "quote": "神经网络训练"},
        ]
        result = retrieval.verify_citations(citations, ranked)
        self.assertEqual(result["verified"], 2)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["unverified_indices"], [2])
        self.assertEqual([item["verified"] for item in result["details"]], [True, False, True])

    def test_empty_base_returns_no_evidence(self) -> None:
        passages = [_passage("P1", "存算一体 ADC"), _passage("P2", "另一主题")]
        ranked, meta = retrieval.rank("ADC", passages, [], top_k=2)
        self.assertEqual(ranked, [])
        self.assertEqual(meta["mode"], "bm25")
        self.assertEqual(meta["fused"], 0)

    def test_rank_handles_short_query(self) -> None:
        passages = [_passage("P1", "存算一体 ADC"), _passage("P2", "另一主题")]
        ranked, meta = retrieval.rank("ADC", passages, passages, top_k=1)
        self.assertEqual(len(ranked), 1)
        self.assertIn(meta["mode"], {"hybrid", "bm25"})
        self.assertGreaterEqual(len(ranked[0]["text"]), 1)


if __name__ == "__main__":
    unittest.main()
