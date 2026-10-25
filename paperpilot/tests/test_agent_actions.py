from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.services import agent_actions, agent_state, analysis, llm_gateway


def _paper(pid: str, title: str = "Paper") -> dict:
    return {
        "id": pid,
        "title": title,
        "authors": ["Alice Alpha"],
        "year": 2024,
        "venue": "Test Venue",
        "source": "demo",
        "abstract": "A detailed abstract about low-power ADC design.",
        "tldr": "TLDR",
        "sections": [{"title": "Abstract", "page": 1, "text": "Evidence text."}],
        "metrics": {"工艺": "28nm"},
        "results": [{"name": "能效", "value": "10fJ"}],
        "limitations": ["Only simulation."],
        "evidence": [
            {"field": "result", "label": "能效", "section": "Results", "page": 2, "quote": "10fJ"}
        ],
    }


class AgentActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(
            os.environ,
            {
                "PAPERPILOT_DATA_DIR": str(self.root / "data"),
                "PAPERPILOT_AGENT_FILE": str(self.root / "runs.json"),
                "PAPERPILOT_AGENT_DIR": str(self.root / "runs"),
            },
        )
        self.env.start()
        self.run = agent_state.create_run_state(
            "低功耗 ADC",
            "demo",
            "auto",
            {**agent_state.DEFAULT_BUDGET, "max_papers": 3},
        )

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    @patch.object(agent_actions.planner, "annotate_results", side_effect=lambda papers, query: [dict(p, layer="直接相关") for p in papers])
    @patch.object(agent_actions.planner, "build_plan", return_value={"concepts": ["ADC"], "sources": [], "strategy": "s"})
    @patch.object(agent_actions.search, "search", return_value=[_paper("P1"), _paper("P2"), _paper("P3")])
    def test_search_selects_and_snapshots(self, search, plan, annotate) -> None:
        artifact = agent_actions.execute_action(
            self.run,
            {"tool": "search", "input": {"query": "ADC", "expansion": False}},
            action_id="act-001",
        )
        self.assertEqual(artifact["selected"], ["P1", "P2", "P3"])
        self.assertEqual(self.run["paper_ids"], ["P1", "P2", "P3"])
        self.assertEqual(len(artifact["candidates"]), 3)
        self.assertTrue((agent_state.run_dir(self.run["id"]) / "papers.json").exists())

    @patch.object(
        agent_actions.analysis,
        "read_paper",
        return_value={
            "tldr": "t",
            "problem": "p",
            "method": "m",
            "limitations": ["l1", "l2"],
            "metrics": {"工艺": "28nm"},
            "evidence": [{"field": "result", "label": "能效", "section": "Results", "page": 2, "quote": "10fJ"}],
            "extraction_mode": "heuristic",
        },
    )
    def test_read_adds_evidence(self, read) -> None:
        agent_actions.save_papers(self.run, [_paper("P1"), _paper("P2")])
        artifact = agent_actions.execute_action(
            self.run,
            {"tool": "read", "input": {"paper_id": "P1"}},
            action_id="act-002",
        )
        self.assertEqual(artifact["paper"]["id"], "P1")
        self.assertGreater(artifact["evidence_added"], 0)
        self.assertTrue(self.run["evidence"])
        self.assertTrue(all(item["source_action"] == "act-002" for item in self.run["evidence"]))

    @patch.object(
        agent_actions.ask,
        "answer",
        return_value={
            "answer": "有证据 [1]",
            "citations": [
                {"index": 1, "paper_id": "P1", "title": "Paper", "section": "Abstract", "page": 1,
                 "quote": "low-power ADC", "quote_verified": True}
            ],
            "citation_verification": {"verified": 1, "total": 1, "unverified_indices": []},
            "retrieval": {"mode": "hybrid"},
            "support_summary": {"direct": 1, "partial": 0, "none": 0},
        },
    )
    def test_ask_resolves_question_and_adds_citations(self, answer) -> None:
        agent_actions.save_papers(self.run, [_paper("P1")])
        question = agent_state.upsert_question(self.run, "代价是什么？", kind="evidence", priority="high")
        artifact = agent_actions.execute_action(
            self.run,
            {"tool": "ask", "input": {"question_id": question["id"], "question": question["question"]}},
            action_id="act-003",
        )
        self.assertEqual(len(artifact["citations"]), 1)
        self.assertEqual(agent_state.find_question(self.run, question["id"])["status"], "resolved")
        self.assertEqual(len(self.run["evidence"]), 1)

    @patch.object(agent_actions.planner, "annotate_results", side_effect=lambda papers, query: papers)
    @patch.object(agent_actions.planner, "build_plan", return_value={})
    @patch.object(agent_actions.search, "search", side_effect=[RuntimeError("timeout"), [_paper("P9")]])
    def test_search_falls_back_to_simplified_query(self, search, plan, annotate) -> None:
        run = agent_state.create_run_state(
            "retrieval augmented generation question answering",
            "arxiv",
            "auto",
            {**agent_state.DEFAULT_BUDGET, "max_papers": 3},
        )
        artifact = agent_actions.execute_action(
            run,
            {"tool": "search", "input": {"query": "retrieval augmented generation for faithful question answering"}},
            action_id="act-001",
        )
        self.assertTrue(artifact["fallback"])
        self.assertEqual(artifact["query_used"], "retrieval augmented generation faithful")
        self.assertEqual(len(artifact["candidates"]), 1)
        self.assertEqual(search.call_count, 2)

    @patch.object(agent_actions.planner, "annotate_results", side_effect=lambda papers, query: papers)
    @patch.object(agent_actions.planner, "build_plan", return_value={})
    @patch.object(agent_actions.search, "search", side_effect=RuntimeError("down"))
    def test_search_failure_raises_clear_error(self, search, plan, annotate) -> None:
        run = agent_state.create_run_state(
            "retrieval augmented generation",
            "arxiv",
            "auto",
            {**agent_state.DEFAULT_BUDGET, "max_papers": 3},
        )
        with self.assertRaises(ValueError) as context:
            agent_actions.execute_action(
                run,
                {"tool": "search", "input": {"query": "retrieval augmented generation"}},
                action_id="act-001",
            )
        self.assertIn("检索失败", str(context.exception))
        self.assertEqual(search.call_count, 2)

    @patch.object(
        agent_actions.research,
        "topic_studio",
        return_value={
            "stats": {"papers": 2},
            "clusters": [{"label": "ADC", "count": 2}],
            "questions": [{"id": "Q1", "title": "方向一"}, {"id": "Q2", "title": "方向二"}],
            "reading_path": [],
            "experiment_plan": {},
            "brief": {"working_title": "T"},
        },
    )
    @patch.object(
        agent_actions.analysis,
        "compare",
        return_value={"rows": [], "consensus": ["c"], "divergence": ["d"], "warnings": ["w"], "gaps": []},
    )
    def test_compare_and_topic(self, compare, studio) -> None:
        agent_actions.save_papers(self.run, [_paper("P1"), _paper("P2")])
        for pid in ("P1", "P2"):
            self.run["steps"].append(
                {"id": agent_state.new_step_id(self.run), "tool": "read", "title": "read",
                 "status": "done", "artifact": {"paper": {"id": pid}}}
            )
        compare_artifact = agent_actions.execute_action(self.run, {"tool": "compare", "input": {}})
        self.assertEqual(compare_artifact["paper_ids"], ["P1", "P2"])
        topic_artifact = agent_actions.execute_action(self.run, {"tool": "topic", "input": {"paper_ids": ["P1", "P2"]}})
        self.assertEqual(len(topic_artifact["questions"]), 2)

    @patch.object(
        agent_actions.research,
        "draft",
        return_value={"title": "Draft", "markdown": "# Draft\n\n## 方向\n正文", "paper_count": 2, "disclaimer": "check"},
    )
    @patch.object(
        agent_actions.argument,
        "argument_review",
        return_value={
            "score": 72,
            "verdict": "需继续修补",
            "actions": ["补证据"],
            "summary": {"claims": 2},
            "unsupported": [{"sentence": "s"}],
        },
    )
    @patch.object(
        agent_actions.research,
        "check_claims",
        return_value={
            "summary": {"total": 2, "supported": 1, "partial": 1, "unsupported": 0},
            "results": [{"level": "supported"}, {"level": "partial"}],
            "citations": [],
        },
    )
    def test_draft_and_review(self, claims, review, draft) -> None:
        agent_actions.save_papers(self.run, [_paper("P1"), _paper("P2")])
        self.run["selected_topic"] = {"id": "Q1", "title": "方向一"}
        draft_artifact = agent_actions.execute_action(self.run, {"tool": "draft", "input": {}})
        self.assertIn("Draft", draft_artifact["markdown"])
        review_artifact = agent_actions.execute_action(self.run, {"tool": "review", "input": {}})
        self.assertEqual(review_artifact["paper_ids"], ["P1", "P2"])
        self.assertEqual(review_artifact["claim_summary"]["total"], 2)
        self.assertEqual(review_artifact["argument"]["score"], 72)

    @patch.object(config, "LLM_ENABLED", False)
    def test_report_contains_ledger_questions_actions(self) -> None:
        agent_actions.save_papers(self.run, [_paper("P1"), _paper("P2")])
        self.run["steps"] = [
            {"id": "act-001", "tool": "search", "title": "检索候选论文", "status": "done",
             "rationale": "建立候选集", "artifact": {"selected": ["P1", "P2"], "candidates": []}},
            {"id": "act-002", "tool": "read", "title": "精读：Paper", "status": "done",
             "rationale": "补证据", "artifact": {"paper": {"id": "P1", "title": "Paper"}, "tldr": "t"}},
        ]
        agent_state.add_evidence(
            self.run,
            [{"paper_id": "P1", "title": "Paper", "section": "Abstract", "page": 1,
              "claim": "ADC 是瓶颈", "quote": "ADC is a bottleneck.", "verified": True, "kind": "problem"}],
            "act-002",
        )
        agent_state.upsert_question(self.run, "代价是否可比较？", kind="evidence", priority="high")
        markdown, mode = agent_actions.build_report(self.run)
        self.assertEqual(mode, "offline")
        for text in ("证据账本", "未解决问题", "动作轨迹", "参考文献", "预算"):
            self.assertIn(text, markdown)
        report = agent_actions.execute_action(self.run, {"tool": "report", "input": {}}, action_id="act-003")
        self.assertTrue(Path(report["path"]).exists())
        self.assertEqual(agent_actions.report_markdown(self.run), markdown)

    @patch.object(config, "LLM_ENABLED", True)
    @patch.object(agent_actions.llm_gateway, "audited_chat")
    def test_report_retries_once_when_the_model_answer_is_truncated(self, chat) -> None:
        # A reasoning model can spend its whole budget and return one character.
        chat.side_effect = [("x", {}), ("## 简报\n\n结论见 [P1]。", {})]
        markdown, mode = agent_actions.build_report(self.run)
        self.assertEqual(mode, "llm")
        self.assertIn("[P1]", markdown)
        self.assertEqual(chat.call_count, 2)

    @patch.object(config, "LLM_ENABLED", False)
    def test_report_legend_maps_every_marker_to_a_reference(self) -> None:
        agent_actions.save_papers(self.run, [_paper("P1", "Paper One"), _paper("P2", "Paper Two")])
        markdown, mode = agent_actions.build_report(self.run)
        self.assertEqual(mode, "offline")
        legend = dict(re.findall(r"\[(P\d+)\] = ([^；\n]+)", markdown))
        self.assertEqual(legend, {"P1": "Paper One", "P2": "Paper Two"})
        for marker, title in (("P1", "Paper One"), ("P2", "Paper Two")):
            self.assertIn(f"[{marker}] Alice Alpha. {title}.", markdown)

    @patch.object(config, "LLM_ENABLED", True)
    @patch.object(agent_actions.llm_gateway, "audited_chat")
    def test_model_legend_is_replaced_with_the_canonical_mapping(self, chat) -> None:
        agent_actions.save_papers(self.run, [_paper("P1", "Paper One"), _paper("P2", "Paper Two")])
        chat.return_value = (
            "# 简报\n\n> 引用标记：[P1] = 另一篇论文；[P2] = 又一篇\n\n结论 [P1]。\n",
            {},
        )
        markdown, mode = agent_actions.build_report(self.run)
        self.assertEqual(mode, "llm")
        self.assertNotIn("另一篇论文", markdown)
        self.assertIn("[P1] = Paper One", markdown)
        self.assertIn("[P2] = Paper Two", markdown)
        self.assertIn("## 参考文献", markdown)
        self.assertIn("- [P1] Alice Alpha. Paper One.", markdown)
        self.assertIn("- [P2] Alice Alpha. Paper Two.", markdown)

    @patch.object(config, "LLM_ENABLED", True)
    @patch.object(agent_actions.llm_gateway, "audited_chat")
    def test_model_reference_list_is_rebuilt_from_the_run(self, chat) -> None:
        agent_actions.save_papers(self.run, [_paper("P1", "Paper One"), _paper("P2", "Paper Two")])
        chat.return_value = (
            "# 简报\n\n结论 [P1]。\n\n## 参考文献\n\n- [P1] 错误论文\n- [P9] 幽灵条目\n",
            {},
        )
        markdown, mode = agent_actions.build_report(self.run)
        self.assertEqual(mode, "llm")
        self.assertNotIn("错误论文", markdown)
        self.assertNotIn("[P9]", markdown)
        self.assertIn("- [P1] Alice Alpha. Paper One.", markdown)
        self.assertIn("- [P2] Alice Alpha. Paper Two.", markdown)

    @patch.object(config, "LLM_ENABLED", True)
    @patch.object(agent_actions.llm_gateway, "audited_chat")
    def test_bold_model_legend_and_references_are_rebuilt(self, chat) -> None:
        agent_actions.save_papers(self.run, [_paper("P1", "Paper One"), _paper("P2", "Paper Two")])
        chat.return_value = (
            "# 简报\n\n> **引用标记**：[P1] = 另一篇论文；[P2] = 又一篇\n\n结论 [P1]。\n\n"
            "## **参考文献**\n\n- [P1] 错误论文\n",
            {},
        )
        markdown, mode = agent_actions.build_report(self.run)
        self.assertEqual(mode, "llm")
        self.assertEqual(markdown.count("引用标记"), 1)
        self.assertNotIn("另一篇论文", markdown)
        self.assertNotIn("错误论文", markdown)
        self.assertIn("- [P1] Alice Alpha. Paper One.", markdown)
        self.assertIn("- [P2] Alice Alpha. Paper Two.", markdown)

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat")
    def test_nested_llm_read_spends_agent_budget(self, chat) -> None:
        llm_gateway.reset_cache()
        analysis.clear_read_cache()
        chat.return_value = (
            '{"tldr":"模型摘要","problem":"模型问题","method":"模型方法",'
            '"novelty":[],"limitations":[],"reviewer_questions":[]}'
        )
        self.run["budget"]["max_llm_calls"] = 1
        agent_actions.save_papers(self.run, [_paper("P1", "Paper One"), _paper("P2", "Paper Two")])
        first = agent_actions.execute_action(
            self.run, {"tool": "read", "input": {"paper_id": "P1"}}, action_id="act-read-1"
        )
        self.assertEqual(first["extraction_mode"], "llm")
        self.assertEqual(self.run["budget_used"]["llm_calls"], 1)
        self.assertEqual(chat.call_count, 1)

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat")
    def test_exhausted_agent_budget_degrades_nested_llm_read(self, chat) -> None:
        llm_gateway.reset_cache()
        analysis.clear_read_cache()
        chat.return_value = (
            '{"tldr":"模型摘要","problem":"模型问题","method":"模型方法",'
            '"novelty":[],"limitations":[],"reviewer_questions":[]}'
        )
        self.run["budget"]["max_llm_calls"] = 1
        agent_actions.save_papers(self.run, [_paper("P1", "Paper One"), _paper("P2", "Paper Two")])
        agent_actions.execute_action(
            self.run, {"tool": "read", "input": {"paper_id": "P1"}}, action_id="act-read-1"
        )
        second = agent_actions.execute_action(
            self.run, {"tool": "read", "input": {"paper_id": "P2"}}, action_id="act-read-2"
        )
        self.assertEqual(second["extraction_mode"], "heuristic")
        self.assertEqual(self.run["budget_used"]["llm_calls"], 1)
        self.assertEqual(chat.call_count, 1)

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat")
    def test_nested_llm_ask_spends_agent_budget(self, chat) -> None:
        llm_gateway.reset_cache()
        llm_gateway.reset_audit()
        chat.return_value = "低功耗来自并行比较与事件驱动采样 [1]。"
        self.run["budget"]["max_llm_calls"] = 2
        self.run["paper_ids"] = [paper["id"] for paper in agent_actions.search.all_papers()[:2]]
        question = agent_state.upsert_question(self.run, "存算一体为什么需要低功耗 ADC？", kind="evidence", priority="high")
        artifact = agent_actions.execute_action(
            self.run,
            {"tool": "ask", "input": {"question_id": question["id"], "question": question["question"]}},
            action_id="act-ask-llm",
        )
        self.assertTrue(artifact["citations"], "ask 动作应基于运行内的真实论文拿到引用")
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(int(self.run["budget_used"]["llm_calls"]), 1)
        entries = llm_gateway.audit_log()
        ask_entries = [entry for entry in entries if entry["purpose"] == "ask"]
        self.assertEqual(len(ask_entries), 1)
        self.assertEqual(ask_entries[0]["run_id"], self.run["id"])

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat")
    def test_exhausted_agent_budget_degrades_ask_instead_of_failing(self, chat) -> None:
        llm_gateway.reset_cache()
        chat.return_value = "低功耗来自并行比较 [1]。"
        self.run["budget"]["max_llm_calls"] = 1
        self.run["paper_ids"] = [paper["id"] for paper in agent_actions.search.all_papers()[:2]]
        first = agent_state.upsert_question(self.run, "存算一体为什么需要低功耗 ADC？", kind="evidence", priority="high")
        agent_actions.execute_action(
            self.run, {"tool": "ask", "input": {"question_id": first["id"], "question": first["question"]}},
            action_id="act-ask-1",
        )
        second = agent_state.upsert_question(self.run, "这些工作的主要局限是什么？", kind="gap", priority="high")
        artifact = agent_actions.execute_action(
            self.run,
            {"tool": "ask", "input": {"question_id": second["id"], "question": second["question"]}},
            action_id="act-ask-2",
        )
        self.assertEqual(self.run["budget_used"]["llm_calls"], 1)
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(artifact["mode"], "extractive")

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat")
    def test_nested_llm_compare_spends_agent_budget(self, chat) -> None:
        llm_gateway.reset_cache()
        analysis.clear_read_cache()
        chat.return_value = (
            '{"comparability":[],"consensus":["均为 28nm 工艺"],"divergence":[],'
            '"cautions":[],"gaps":[]}'
        )
        self.run["budget"]["max_llm_calls"] = 2
        agent_actions.save_papers(self.run, [_paper("P1", "Paper One"), _paper("P2", "Paper Two")])
        artifact = agent_actions.execute_action(
            self.run, {"tool": "compare", "input": {}}, action_id="act-cmp-1"
        )
        self.assertEqual(int(self.run["budget_used"]["llm_calls"]), 1)
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(len(artifact["rows"]), 2)

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat")
    def test_exhausted_agent_budget_still_answers_compare(self, chat) -> None:
        llm_gateway.reset_cache()
        analysis.clear_read_cache()
        chat.return_value = (
            '{"tldr":"模型摘要","problem":"模型问题","method":"模型方法",'
            '"novelty":[],"limitations":[],"reviewer_questions":[]}'
        )
        self.run["budget"]["max_llm_calls"] = 1
        agent_actions.save_papers(self.run, [_paper("P1", "Paper One"), _paper("P2", "Paper Two")])
        agent_actions.execute_action(
            self.run, {"tool": "read", "input": {"paper_id": "P1"}}, action_id="act-read-1"
        )
        artifact = agent_actions.execute_action(
            self.run, {"tool": "compare", "input": {}}, action_id="act-cmp-1"
        )
        self.assertEqual(self.run["budget_used"]["llm_calls"], 1)
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(len(artifact["rows"]), 2)

    @patch.object(config, "LLM_ENABLED", True)
    @patch.object(agent_actions.llm_gateway, "audited_chat")
    def test_report_does_not_retry_a_long_answer_without_citations(self, chat) -> None:
        chat.return_value = ("这是一段足够长但完全没有引用标记的回答。" * 3, {})
        markdown, mode = agent_actions.build_report(self.run)
        self.assertEqual(mode, "offline")
        self.assertEqual(chat.call_count, 1)


if __name__ == "__main__":
    unittest.main()
