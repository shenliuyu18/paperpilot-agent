from __future__ import annotations

import unittest
from unittest.mock import patch

from app import config
from app.services import analysis, search


def _browser_paper(paper_id: str, title: str, abstract: str, year: int = 2024) -> dict:
    return {
        "id": paper_id,
        "title": title,
        "abstract": abstract,
        "tldr": abstract[:180],
        "authors": ["A. Author"],
        "year": year,
        "venue": "arXiv",
        "url": f"https://example.org/{paper_id}",
        "source": "crossref",
        "demo": False,
        "extraction_level": "Crossref 摘要级",
        "metrics": {"被引": 3, "类型": "预印本", "来源": "Crossref"},
        "results": [],
        "limitations": [],
        "sections": [{"title": "Abstract", "page": 1, "text": abstract}],
        "evidence": [{"quote": abstract[:200]}],
    }


class CompareTests(unittest.TestCase):
    def setUp(self) -> None:
        analysis.clear_compare_cache()

    def test_non_adc_papers_use_their_own_content(self) -> None:
        papers = [
            _browser_paper(
                "crossref-browser-1",
                "One Flight Short: When the Browser Meets the Screen",
                "We study browser automation agents that read screen pixels and issue clicks. "
                "Millions of users registered with Scratch. The agent improves task success by 18% "
                "but struggles with security and prompt injection.",
            ),
            _browser_paper(
                "crossref-browser-2",
                "Building Browser Agents: Architecture, Security",
                "This paper proposes a browser agent architecture with a policy layer and a DOM "
                "sandbox. Evaluation on 120 web tasks shows higher success but exposes latency "
                "overhead and cross-site security risks.",
            ),
            _browser_paper(
                "crossref-browser-3",
                "New Browser: Java Based Web Browser for Mobile Phones",
                "We present a mobile browser architecture with a rendering pipeline and network "
                "cache. Experiments measure latency and memory on low-end devices.",
                2022,
            ),
        ]
        result = analysis.compare(papers)
        text = " ".join(result["consensus"] + result["divergence"] + result["warnings"])
        for forbidden in ("ADC", "CIM", "存算", "模数转换"):
            self.assertNotIn(forbidden, text)
        labels = [column["label"] for column in result["columns"]]
        self.assertIn("研究问题", labels)
        self.assertIn("方法 / 路径", labels)
        self.assertNotIn("ADC 方案", labels)
        self.assertTrue(all(row["values"].get("research_question") for row in result["rows"]))
        self.assertIn("browser", result["rows"][0]["values"]["research_question"].lower())
        self.assertTrue(result["gaps"])
        self.assertNotIn("ADC", " ".join(gap["question"] for gap in result["gaps"]))
        self.assertTrue(any("摘要" in warning for warning in result["warnings"]))

    def test_builtin_papers_use_actual_metrics(self) -> None:
        papers = [search.get_paper("2609.24288v1"), search.get_paper("2606.08947v1")]
        result = analysis.compare(papers)
        self.assertEqual(len(result["rows"]), 2)
        self.assertTrue(result["consensus"])
        self.assertFalse(any(item.startswith("P1 走") for item in result["divergence"]))
        self.assertNotIn("五篇工作", " ".join(result["consensus"]))
        self.assertTrue(all(row["values"].get("key_result") for row in result["rows"]))

    def test_mixed_topics_do_not_invent_shared_consensus(self) -> None:
        browser = _browser_paper(
            "crossref-browser-x",
            "Browser Agents for Web Tasks",
            "We evaluate browser agents on web navigation tasks and measure latency.",
        )
        adc = search.get_paper("2609.24288v1")
        result = analysis.compare([browser, adc])
        text = " ".join(result["consensus"])
        self.assertNotIn("五篇工作", text)
        self.assertTrue(
            any("没有可自动识别" in item or "不同研究方向" in item for item in result["consensus"])
        )

    def test_llm_adc_hallucination_is_rejected(self) -> None:
        papers = [
            _browser_paper(
                "crossref-browser-y",
                "Browser Security Agents",
                "We study browser security agents and DOM sandboxing.",
            ),
            _browser_paper(
                "crossref-browser-z",
                "Web Agent Evaluation",
                "We evaluate web agents on navigation benchmarks and latency.",
            ),
        ]
        with patch.object(config, "LLM_ENABLED", True), patch.object(
            analysis.llm_gateway,
            "audited_chat_json",
            return_value=(
                {
                    "consensus": ["五篇工作都把 ADC 的能耗/面积视为核心瓶颈。"],
                    "divergence": ["P1 走 SAR 跳比较。"],
                    "cautions": ["不同工艺不可直接比较。"],
                },
                {"status": "ok"},
            ),
        ):
            result = analysis.compare(papers)
        self.assertNotIn("ADC", " ".join(result["consensus"] + result["divergence"]))

    def test_method_is_not_a_results_sentence(self) -> None:
        paper = _browser_paper(
            "crossref-method",
            "Efficient Sparse Routing",
            "Transformer attention scales quadratically with sequence length. "
            "This paper introduces sparse routing that prunes key-value pairs. "
            "On language modeling benchmarks the method reduces FLOPs by 38%.",
        )
        result = analysis.compare([paper])
        method = result["rows"][0]["values"]["method"]
        self.assertIn("sparse routing", method)
        self.assertNotIn("reduces FLOPs", method)

    def test_gaps_use_extracted_limitation(self) -> None:
        paper = _browser_paper(
            "crossref-gap",
            "Browser Agents for Web Tasks",
            "We evaluate browser agents on web navigation tasks and measure latency. "
            "The approach struggles with multi-hop questions and adds retrieval latency.",
        )
        result = analysis.compare([paper])
        limitation = result["rows"][0]["values"]["limitation"]
        self.assertTrue(limitation.lower().startswith("the approach struggles"), limitation)
        questions = " ".join(gap["question"] for gap in result["gaps"])
        self.assertIn("struggles with multi-hop", questions)

    def test_token_normalization_matches_compounds_and_plurals(self) -> None:
        papers = [
            _browser_paper(
                "crossref-token-a",
                "Browser Agents for Retrieval",
                "We study browser agents that read long-document pages.",
            ),
            _browser_paper(
                "crossref-token-b",
                "Browser Agent Pipelines",
                "We evaluate browser agent pipelines on long documents.",
            ),
        ]
        result = analysis.compare(papers)
        consensus = " ".join(result["consensus"])
        self.assertIn("browser", consensus.lower())
        self.assertNotIn("没有可自动识别", consensus)

    def test_comparability_covers_pairs_and_flags_mixed_topics(self) -> None:
        papers = [
            _browser_paper(
                "cmp-a",
                "Retrieval Augmented QA",
                "We study retrieval pipelines for QA. Evaluation on QA datasets shows EM +4.2.",
            ),
            _browser_paper(
                "cmp-b",
                "Sparse Attention QA",
                "We evaluate sparse attention on QA datasets and report EM +3.1, however routing overhead remains.",
                2025,
            ),
            _browser_paper(
                "cmp-c",
                "Mobile Browser Rendering",
                "We present a mobile browser rendering pipeline and measure memory.",
                2022,
            ),
        ]
        for paper in papers[:2]:
            paper["metrics"] = {**paper["metrics"], "主指标": "EM"}
        result = analysis.compare(papers)
        pairs = {tuple(item["paper_ids"]): item for item in result["comparability"]}
        self.assertEqual(len(pairs), 3)
        self.assertTrue(pairs[("cmp-a", "cmp-b")]["comparable"])
        self.assertEqual(pairs[("cmp-a", "cmp-b")]["confidence"], "high")
        self.assertFalse(pairs[("cmp-a", "cmp-c")]["comparable"])
        self.assertIn("不同研究方向", pairs[("cmp-a", "cmp-c")]["reason"])
        self.assertTrue(result["warnings"])

    def test_structured_consensus_and_limitation_divergence(self) -> None:
        left = _browser_paper(
            "cmp-consensus-a",
            "Retrieval QA A",
            "We study retrieval for QA. Evaluation on QA datasets shows EM +4.2, but it struggles with multi-hop questions.",
        )
        right = _browser_paper(
            "cmp-consensus-b",
            "Retrieval QA B",
            "We study retrieval for QA. Evaluation on QA datasets shows EM +3.1, however routing overhead remains.",
        )
        left["metrics"] = {**left["metrics"], "主指标": "EM +4.2"}
        right["metrics"] = {**right["metrics"], "主指标": "EM +3.1"}
        result = analysis.compare([left, right])
        consensus = " ".join(result["consensus"])
        self.assertIn("主指标", consensus)
        self.assertIn("+4.2", consensus)
        self.assertIn("+3.1", consensus)
        divergence = " ".join(result["divergence"])
        self.assertIn("multi-hop", divergence)
        self.assertIn("routing overhead", divergence)

    def test_llm_comparability_and_gaps_are_used(self) -> None:
        papers = [
            _browser_paper("cmp-llm-a", "Browser Security", "We study browser security agents."),
            _browser_paper("cmp-llm-b", "Web Agent Evaluation", "We evaluate web agents on navigation benchmarks."),
        ]
        payload = {
            "comparability": [
                {
                    "paper_ids": ["cmp-llm-a", "cmp-llm-b"],
                    "comparable": True,
                    "reason": "两篇都在浏览器/Web 任务上评估。",
                },
                {"paper_ids": ["cmp-llm-a", "missing-id"], "comparable": True, "reason": "越界"},
            ],
            "consensus": ["两篇都涉及 Web 任务。"],
            "divergence": ["A 关注安全，B 关注导航评测。"],
            "cautions": ["测试条件不同。"],
            "gaps": [{"question": "能否在跨站点条件下复测成功率和延迟？", "why_gap": "当前材料未覆盖"}],
        }
        with patch.object(config, "LLM_ENABLED", True), patch.object(
            analysis.llm_gateway, "audited_chat_json", return_value=(payload, {"status": "ok"})
        ):
            result = analysis.compare(papers)
        pairs = {tuple(item["paper_ids"]): item for item in result["comparability"]}
        self.assertEqual(pairs[("cmp-llm-a", "cmp-llm-b")]["confidence"], "llm")
        self.assertNotIn(("cmp-llm-a", "missing-id"), pairs)
        self.assertTrue(any(gap["title"] == "模型建议" for gap in result["gaps"]))

    def test_compare_cache_reuses_result_and_returns_copy(self) -> None:
        papers = [
            _browser_paper("cmp-cache-a", "Browser Agents", "We study browser agents and latency."),
            _browser_paper("cmp-cache-b", "Web Agents", "We evaluate web agents and latency."),
        ]
        with patch.object(
            analysis, "_offline_compare_sections", wraps=analysis._offline_compare_sections
        ) as spy:
            first = analysis.compare(papers)
            second = analysis.compare(papers)
            self.assertEqual(spy.call_count, 1)
        first["warnings"].append("mutated")
        self.assertNotIn("mutated", second["warnings"])

    def test_columns_include_common_domain_metrics(self) -> None:
        paper_a = dict(search.get_paper("2609.24288v1"))
        paper_b = dict(search.get_paper("2606.08947v1"))
        result = analysis.compare([paper_a, paper_b])
        labels = [column["label"] for column in result["columns"]]
        self.assertTrue(any(label not in {
            "论文", "研究问题", "方法 / 路径", "数据 / 任务",
            "主要结果", "局限", "原文证据",
        } for label in labels))


    def test_non_adc_innovate_uses_paper_fields(self) -> None:
        paper = _browser_paper(
            "crossref-innovate",
            "Browser Agents for Web Tasks",
            "We study browser agents that read pages and click. Evaluation on web tasks measures task success.",
        )
        paper["metrics"] = {"任务成功率": "82%"}
        result = analysis.innovate(paper)
        text = " ".join(result["inferences"])
        self.assertIn("任务成功率", text)
        for term in ("ADC", "CIM", "存算", "能耗", "面积"):
            self.assertNotIn(term, text)

    def test_non_curated_transform_has_offline_scaffold(self) -> None:
        paper = _browser_paper(
            "crossref-transform",
            "Browser Agents for Web Tasks",
            "We study browser agents that read pages and click. Evaluation on web tasks measures task success.",
        )
        result = analysis.transform(paper)
        self.assertEqual(result["source"], "heuristic")
        self.assertIn("ExperimentConfig", result["code_scaffold"])
        self.assertGreaterEqual(len(result["slides"]), 3)
        self.assertEqual(len(result["quiz"]), 3)
        self.assertIn("课程案例", result["course_case"])
        self.assertTrue(result["repro"]["steps"])


if __name__ == "__main__":
    unittest.main()
