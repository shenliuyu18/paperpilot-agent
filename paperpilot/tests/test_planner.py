from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services import planner, search


def _paper(pid: str, title: str, abstract: str, source: str = "crossref", **extra) -> dict:
    data = {
        "id": pid,
        "title": title,
        "abstract": abstract,
        "authors": ["Alice Alpha", "Bob Beta", "Carol Gamma"],
        "year": 2024,
        "venue": "IEEE Transactions on Circuits and Systems",
        "doi": "10.1000/" + pid,
        "url": "https://doi.org/10.1000/" + pid,
        "source": source,
        "metrics": {"类型": "期刊论文"},
    }
    data.update(extra)
    return data


class PlannerTests(unittest.TestCase):
    def test_concept_blocks_expand_chinese_terms_and_abbreviations(self) -> None:
        blocks = planner.concept_blocks("存算一体 低功耗 ADC")
        terms = " ".join(term for block in blocks for term in block["terms"]).lower()
        self.assertGreaterEqual(len(blocks), 2)
        self.assertIn("compute-in-memory", terms)
        self.assertIn("low power", terms)
        self.assertIn("analog-to-digital", terms)
        self.assertTrue(all(block["rationale"] for block in blocks))

    def test_build_plan_returns_source_queries_and_explanation(self) -> None:
        plan = planner.build_plan("存算一体 低功耗 ADC", "auto")
        source_ids = {item["id"] for item in plan["sources"]}
        self.assertIn("crossref", source_ids)
        self.assertIn("arxiv", source_ids)
        self.assertIn("europepmc", source_ids)
        self.assertGreaterEqual(len(plan["strategy"]), 3)
        self.assertGreaterEqual(plan["estimated"]["term_count"], 3)
        arxiv = next(item for item in plan["sources"] if item["id"] == "arxiv")
        self.assertTrue("AND" in arxiv["query"] or "all:" in arxiv["query"] or len(arxiv["query"]) > 10)

    def test_annotations_mark_direct_journal_paper_as_high_credibility(self) -> None:
        query = "存算一体 低功耗 ADC"
        journal = _paper(
            "journal",
            "A Low-Power ADC for Compute-in-Memory Accelerators",
            "This paper presents an energy-efficient SAR ADC for CIM macro readout. Experiments show lower energy.",
        )
        annotated = planner.annotate_results([journal], query)[0]
        self.assertEqual(annotated["layer"], "直接相关")
        self.assertEqual(annotated["source_type"], "期刊论文")
        self.assertGreaterEqual(annotated["credibility"]["score"], 75)
        self.assertEqual(annotated["credibility"]["level"], "高")
        self.assertIn("DOI", " ".join(annotated["credibility"]["signals"]))
        self.assertNotIn("layer", journal)

    def test_annotations_warn_about_preprints_and_background_surveys(self) -> None:
        query = "存算一体 低功耗 ADC"
        preprint = _paper(
            "preprint",
            "Low-Power ADC Design for Compute-in-Memory",
            "A preprint about ADC readout for in-memory computing.",
            source="arxiv",
            doi="",
            venue="arXiv",
            metrics={},
            url="https://arxiv.org/abs/1234.5678",
        )
        survey = _paper(
            "survey",
            "A Survey of Compute-in-Memory Architectures",
            "This review surveys compute-in-memory architectures and discusses open challenges.",
            doi="10.1000/survey",
            venue="ACM Computing Surveys",
            metrics={"类型": "期刊论文"},
        )
        results = planner.annotate_results([preprint, survey], query)
        by_id = {item["id"]: item for item in results}
        self.assertEqual(by_id["preprint"]["layer"], "直接相关")
        self.assertEqual(by_id["preprint"]["source_type"], "预印本")
        self.assertLess(by_id["preprint"]["credibility"]["score"], 75)
        self.assertTrue(any("预印本" in warning for warning in by_id["preprint"]["credibility"]["warnings"]))
        self.assertEqual(by_id["survey"]["layer"], "背景脉络")
        summary = planner.layer_summary(results)
        self.assertEqual(summary["直接相关"], 1)
        self.assertEqual(summary["背景脉络"], 1)

    def test_unrelated_result_is_background_not_method(self) -> None:
        unrelated = _paper(
            "unrelated",
            "Database Indexing for Web Applications",
            "We evaluate query latency in relational databases.",
            doi="10.1000/db",
        )
        annotated = planner.annotate_results([unrelated], "存算一体 低功耗 ADC")[0]
        self.assertEqual(annotated["layer"], "背景脉络")
        self.assertEqual(annotated["concept_coverage"]["ratio"], 0)


class AutoSearchTests(unittest.TestCase):
    def test_concurrent_sources_are_deduped_and_tagged(self) -> None:
        shared = _paper("shared", "Low-Power ADC for CIM", "ADC readout for compute-in-memory.", "crossref", doi="10.1000/shared")
        arxiv_item = dict(shared, id="arxiv-shared", source="arxiv", venue="arXiv", doi="10.1000/shared")
        crossref_only = _paper("crossref-only", "SAR ADC Calibration", "A calibrated SAR ADC design.", "crossref", doi="10.1000/c1")
        arxiv_only = _paper("arxiv-only", "CIM Readout Circuits", "Readout circuits for CIM.", "arxiv", doi="", venue="arXiv")

        with patch.object(search, "search_crossref", return_value=[shared, crossref_only]), \
             patch.object(search, "search_arxiv", return_value=[arxiv_item, arxiv_only]), \
             patch.object(search, "search_europepmc", return_value=[]):
            results = search.search_auto("存算一体 ADC", 6)

        self.assertEqual(len(results), 3)
        merged = next(item for item in results if item["id"] == "shared")
        self.assertCountEqual(merged["aggregate_sources"], ["crossref", "arxiv"])
        self.assertTrue(all(item.get("aggregate") for item in results))

    def test_partial_source_failure_keeps_available_results(self) -> None:
        item = _paper("only", "Low-Power ADC", "ADC design.", "crossref")
        with patch.object(search, "search_crossref", return_value=[item]), \
             patch.object(search, "search_arxiv", side_effect=RuntimeError("down")), \
             patch.object(search, "search_europepmc", side_effect=RuntimeError("down")):
            results = search.search_auto("ADC", 4)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "only")

    def test_all_sources_failing_falls_back_to_demo(self) -> None:
        with patch.object(search, "search_crossref", side_effect=RuntimeError("down")), \
             patch.object(search, "search_arxiv", side_effect=RuntimeError("down")), \
             patch.object(search, "search_europepmc", side_effect=RuntimeError("down")):
            results = search.search_auto("存算一体", 3)
        self.assertTrue(results)
        self.assertTrue(all(item.get("aggregate_fallback") for item in results))

    def test_transient_source_failure_gets_a_short_cooldown(self) -> None:
        search._AUTO_COOLDOWN.clear()
        try:
            with patch.object(search, "search_crossref", return_value=[_paper("one", "Low-Power ADC", "ADC design.")]), \
                 patch.object(search, "search_arxiv", side_effect=search.requests.exceptions.ConnectionError("down")), \
                 patch.object(search, "search_europepmc", return_value=[]):
                results = search.search_auto("cooldown-test", 3)
            self.assertTrue(results)
            self.assertGreater(search._AUTO_COOLDOWN.get("arxiv", 0), 0)
        finally:
            search._AUTO_COOLDOWN.clear()


if __name__ == "__main__":
    unittest.main()
