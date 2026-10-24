from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import golden_workbench  # noqa: E402

CORPUS = ROOT / "data" / "eval" / "golden" / "corpus"
GOLDEN = ROOT / "data" / "eval" / "golden"
TOPICS = {"llm-inference-quantization", "rag-pipelines", "cim-adc", "recommender-systems"}


class MultiTopicGoldenTests(unittest.TestCase):
    def test_corpus_has_the_four_topics_and_six_papers_each(self) -> None:
        topics = golden_workbench.load_corpus_dir(CORPUS)
        self.assertEqual(set(topics), TOPICS)
        for slug, papers in topics.items():
            self.assertEqual(len(papers), 6, slug)
            for paper in papers:
                self.assertGreater(len(paper.get("abstract") or ""), 300, paper.get("id"))
                self.assertTrue(paper.get("id"), slug)

    def test_candidate_pool_exceeds_200_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            summary = golden_workbench.build(
                Path(folder), source="demo", corpus_dir=CORPUS,
            )
            total = sum(row["claims"] for row in summary.values())
            self.assertGreaterEqual(total, 200)
            self.assertEqual(set(summary), TOPICS)
            for slug, row in summary.items():
                self.assertEqual(row["papers"], 6, slug)
                payload = json.loads((Path(folder) / f"{slug}.json").read_text(encoding="utf-8"))
                self.assertTrue(all(claim["label"] == "" for claim in payload["claims"]))

    def test_multitopic_sets_are_labelled_and_quotes_are_substrings(self) -> None:
        topics = golden_workbench.load_corpus_dir(CORPUS)
        papers = {paper["id"]: paper for items in topics.values() for paper in items}
        total = 0
        for slug in TOPICS:
            path = GOLDEN / f"multitopic-{slug}.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["provenance"], "constructed-multitopic")
            self.assertGreaterEqual(len(payload["claims"]), 70, slug)
            total += len(payload["claims"])
            for item in payload["claims"]:
                self.assertIn(item["label"], {"supported", "partial", "unsupported"})
                evidence = papers.get(item.get("evidence_paper_id"))
                self.assertIsNotNone(evidence, item["id"])
                self.assertIn(item["evidence_quote"], evidence["abstract"], item["id"])
        self.assertGreaterEqual(total, 280)

    def test_check_accepts_multitopic_with_corpus(self) -> None:
        report = golden_workbench.check(
            GOLDEN / "multitopic-rag-pipelines.json", corpus_dir=CORPUS,
        )
        self.assertEqual(report["problems"], [])
        self.assertGreater(report["counts"]["labeled"], 0)


if __name__ == "__main__":
    unittest.main()
