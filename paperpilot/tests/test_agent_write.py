from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import llm
from app.services import (
    agent_actions, agent_policy, agent_runs, agent_state,
    draft_service, project_service, project_state, search,
)


class AgentWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = patch.dict(
            os.environ,
            {
                "PAPERPILOT_PROJECT_FILE": str(root / "projects.json"),
                "PAPERPILOT_PROJECT_DIR": str(root / "projects"),
                "PAPERPILOT_AGENT_FILE": str(root / "runs.json"),
                "PAPERPILOT_AGENT_DIR": str(root / "runs"),
            },
        )
        self.env.start()
        agent_runs._RECOVERED.clear()
        self.project = project_service.create_project("低功耗 ADC", "如何降低功耗？")
        memory = project_state.read_memory(self.project["id"])
        memory["evidence"] = [{
            "id": "pev-1", "paper_id": "P1", "title": "Paper One",
            "section": "Results", "page": 2,
            "quote": "The prototype consumes 4.2 fJ per conversion.",
            "verified": True, "kind": "text",
        }]
        memory["papers"] = {"P1": {
            "id": "P1", "title": "Paper One", "authors": ["Alice Author"],
            "year": 2024, "venue": "V", "doi": "", "url": "",
        }}
        project_state.write_memory(self.project["id"], memory)

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _run(self) -> dict:
        run = agent_state.create_run_state(
            "目标", "demo", "auto", agent_state.DEFAULT_BUDGET,
            project_id=self.project["id"],
        )
        data = agent_state.read_store()
        data["runs"][run["id"]] = run
        agent_state.write_store(data)
        return run

    def _draft(self, blocks: list[dict] | None = None) -> dict:
        return draft_service.save_draft(self.project["id"], {
            "revision": 0,
            "title": "草稿",
            "sections": [{
                "id": "sec-001", "title": "结果", "blocks": blocks or [],
            }],
        })

    def _steps_through_draft(self, run: dict) -> None:
        run["steps"] = [
            {
                "id": "act-001", "tool": "search", "status": "done",
                "artifact": {"selected": ["P1"], "candidates": [{"id": "P1"}], "new_ids": ["P1"]},
            },
            {
                "id": "act-002", "tool": "read", "status": "done",
                "artifact": {"paper": {"id": "P1"}, "evidence_added": 1},
            },
            {
                "id": "act-003", "tool": "topic", "status": "done",
                "artifact": {"paper_ids": ["P1"], "questions": [{"id": "Q1", "title": "方向"}]},
            },
            {
                "id": "act-004", "tool": "draft", "status": "done",
                "artifact": {"markdown": "## 结果\n\n已有骨架", "paper_ids": ["P1"]},
            },
        ]
        run["paper_ids"] = ["P1"]

    def _append_done(self, run: dict, tool: str, artifact: dict,
                     input_data: dict | None = None) -> dict:
        step = {
            "id": f"act-{len(run['steps']) + 1:03d}",
            "tool": tool,
            "status": "done",
            "input": input_data or {},
            "artifact": artifact,
        }
        run["steps"].append(step)
        return step

    def _review_artifact(self, high: int) -> dict:
        return {
            "paper_ids": ["P1"],
            "claim_summary": {
                "total": 1, "supported": 1, "partial": 0, "unsupported": 0,
            },
            "argument": {"score": 90, "actions": []},
            "draft_audit": {
                "issues": [],
                "counts": {"high": high, "medium": 0, "low": 0, "total": high},
            },
        }

    # --- brief tests -----------------------------------------------------

    def test_write_section_creates_evidence_anchored_block(self) -> None:
        self._draft()
        result = draft_service.write_section(self.project["id"], "sec-001", max_blocks=3)
        self.assertEqual(result["blocks_added"], 1)
        saved = draft_service.get_draft(self.project["id"])
        block = saved["sections"][0]["blocks"][0]
        self.assertIn("pev-1", block["evidence_ids"])
        self.assertIn("author2024", block["citation_keys"])
        self.assertIn("待结合研究问题改写", block["text"])

    def test_act_write_uses_project_draft_and_no_evidence_question(self) -> None:
        run = self._run()
        self._draft()
        artifact = agent_actions._act_write(run, {"section_id": "sec-001"}, "act-001")
        self.assertEqual(artifact["blocks_added"], 1)
        self.assertEqual(artifact["section_id"], "sec-001")

    def test_execute_action_dispatches_write(self) -> None:
        run = self._run()
        self._draft()
        artifact = agent_actions.execute_action(
            run,
            {"tool": "write", "input": {"section_id": "sec-001"}},
            "act-001",
        )
        self.assertEqual(artifact["section_id"], "sec-001")
        self.assertEqual(artifact["blocks_added"], 1)

    def test_policy_moves_to_review_after_section_was_written(self) -> None:
        run = self._run()
        self._steps_through_draft(run)
        run["steps"].append({
            "id": "act-005", "tool": "write", "status": "done",
            "artifact": {"section_id": "sec-001", "blocks_added": 1},
        })
        self._draft()
        plan = agent_policy.choose_next(run)
        self.assertEqual(plan["tool"], "review")

    def test_policy_chooses_write_after_draft(self) -> None:
        run = self._run()
        self._steps_through_draft(run)
        self._draft()
        plan = agent_policy.choose_next(run)
        self.assertEqual(plan["tool"], "write")
        self.assertEqual(plan["input"]["section_id"], "sec-001")

    def test_review_includes_draft_audit(self) -> None:
        run = self._run()
        run["paper_ids"] = ["P1"]
        agent_actions.save_papers(run, [{
            "id": "P1", "title": "Paper One", "authors": ["Alice Author"],
            "year": 2024, "venue": "V",
        }])
        run["steps"] = [{
            "id": "act-001", "tool": "read", "status": "done",
            "artifact": {"paper": {"id": "P1", "title": "Paper One"}, "tldr": "研究摘要"},
        }]
        draft_service.save_draft(self.project["id"], {
            "revision": 0,
            "title": "草稿",
            "sections": [{"id": "sec-001", "title": "结果", "blocks": [{
                "id": "blk-001", "type": "paragraph",
                "text": "没有证据的段落。" * 8,
                "evidence_ids": [],
            }]}],
        })
        artifact = agent_actions._act_review(run, {}, "act-002")
        self.assertIn("draft_audit", artifact)
        self.assertEqual(artifact["draft_audit"]["counts"]["high"], 1)

    def test_policy_asks_review_question_when_draft_has_high_issues(self) -> None:
        run = self._run()
        self._steps_through_draft(run)
        agent_actions.save_papers(run, [{
            "id": "P1", "title": "Paper One", "authors": ["Alice Author"],
            "year": 2024, "venue": "V",
        }])
        draft_service.save_draft(self.project["id"], {
            "revision": 0,
            "title": "草稿",
            "sections": [{"id": "sec-001", "title": "结果", "blocks": [{
                "id": "blk-001", "type": "paragraph",
                "text": "没有证据的段落。" * 8,
                "evidence_ids": [],
            }]}],
        })
        # Isolate the draft audit: the sentence checker finds nothing and the
        # argument score stays healthy, so only the audit high issue can drive
        # the next action.
        with patch.object(agent_actions.research, "check_claims", return_value={
            "summary": {"total": 0, "supported": 0, "partial": 0, "unsupported": 0},
            "results": [],
            "citations": [],
        }), patch.object(agent_actions.argument, "argument_review", return_value={
            "score": 90, "verdict": "ok", "summary": {}, "actions": [],
            "unsupported": [], "structure": {},
        }):
            artifact = agent_actions._act_review(run, {}, "act-005")
        self.assertEqual(artifact["claim_summary"]["unsupported"], 0)
        self.assertEqual(artifact["argument"]["score"], 90)
        self.assertEqual(artifact["draft_audit"]["counts"]["high"], 1)
        self._append_done(run, "review", artifact)

        plan = agent_policy.choose_next(run)
        self.assertEqual(plan["tool"], "ask")
        questions = [
            item for item in agent_state.open_questions(run, priority="high")
            if item.get("source") == "review"
        ]
        self.assertTrue(questions)
        self.assertEqual(plan["input"]["question_id"], questions[0]["id"])
        self.assertIn("草稿核查", plan["rationale"])

    def test_policy_expands_search_when_draft_high_issue_has_no_usable_question(self) -> None:
        run = self._run()
        self._draft()
        self._steps_through_draft(run)
        self._append_done(run, "write", {"section_id": "sec-001", "blocks_added": 1})
        asked = agent_state.upsert_question(
            run, "草稿核查：段落没有绑定证据。",
            kind="claim", priority="high", source="review",
        )
        self._append_done(
            run, "ask",
            {
                "question_id": asked["id"], "question": asked["question"],
                "citations": [{"index": 1}], "evidence_added": 1,
            },
            input_data={"question_id": asked["id"], "question": asked["question"]},
        )
        agent_state.resolve_question(run, asked["id"], "1 条引用")
        # An unrelated open question must not be used to close a draft audit gap.
        agent_state.upsert_question(
            run, "检索没有返回可用论文，是否需要更换关键词或来源？",
            kind="evidence", priority="high", source="critic",
        )
        self._append_done(run, "review", self._review_artifact(high=1))

        plan = agent_policy.choose_next(run)
        self.assertEqual(plan["tool"], "search")
        self.assertTrue(plan["input"].get("expansion"))
        self.assertIn("高严重度", plan["rationale"])

    def test_policy_reports_when_draft_has_no_high_issues(self) -> None:
        run = self._run()
        self._draft()
        self._steps_through_draft(run)
        self._append_done(run, "write", {"section_id": "sec-001", "blocks_added": 1})
        self._append_done(run, "review", self._review_artifact(high=0))

        plan = agent_policy.choose_next(run)
        self.assertEqual(plan["tool"], "report")

    def test_empty_skeleton_write_is_not_replanned(self) -> None:
        run = self._run()
        self._steps_through_draft(run)
        run["steps"][-1]["artifact"]["markdown"] = ""

        plan = agent_policy.choose_next(run)
        self.assertEqual(plan["tool"], "write")
        self.assertEqual(plan["input"]["section_id"], "")

        artifact = agent_actions._act_write(run, plan["input"], "act-005")
        self.assertEqual(artifact["section_id"], "")
        self.assertEqual(artifact["blocks_added"], 0)
        self.assertTrue(artifact.get("no_section"))
        self.assertEqual(artifact.get("imported_sections"), 0)
        self._append_done(run, "write", artifact)

        self.assertIsNone(agent_policy._draft_gap(run))
        next_plan = agent_policy.choose_next(run)
        self.assertEqual(next_plan["tool"], "review")
        self.assertNotEqual(next_plan["tool"], "write")

        self._append_done(run, "review", self._review_artifact(high=0))
        self.assertEqual(agent_policy.choose_next(run)["tool"], "report")

    # --- regressions -----------------------------------------------------

    def test_ensure_from_markdown_imports_sections_and_is_idempotent(self) -> None:
        draft = draft_service.ensure_from_markdown(self.project["id"], (
            "# 标题\n\n## 引言\n\n第一段。\n\n### 方法\n"
        ))
        self.assertEqual(
            [section["title"] for section in draft["sections"]],
            ["引言", "方法"],
        )
        block = draft["sections"][0]["blocks"][0]
        self.assertEqual(block["status"], "needs_evidence")
        self.assertEqual(block["evidence_ids"], [])
        self.assertEqual(draft["title"], "研究草稿")
        again = draft_service.ensure_from_markdown(
            self.project["id"], "## 其他\n\n不应覆盖"
        )
        self.assertEqual(
            [section["title"] for section in again["sections"]],
            ["引言", "方法"],
        )
        self.assertNotIn("不应覆盖", str(again["sections"]))

    def test_ensure_from_markdown_without_headings_keeps_paragraphs(self) -> None:
        draft = draft_service.ensure_from_markdown(
            self.project["id"], "第一行\n\n第二行"
        )
        self.assertEqual(draft["sections"][0]["title"], "草稿")
        self.assertEqual(
            [block["text"] for block in draft["sections"][0]["blocks"]],
            ["第一行", "第二行"],
        )

    def test_act_write_without_project_is_rejected(self) -> None:
        run = agent_state.create_run_state(
            "独立目标", "demo", "auto", agent_state.DEFAULT_BUDGET,
        )
        with self.assertRaises(ValueError):
            agent_actions._act_write(run, {"section_id": "sec-001"}, "act-001")

    def test_act_write_without_evidence_adds_high_question(self) -> None:
        memory = project_state.read_memory(self.project["id"])
        memory["evidence"] = []
        project_state.write_memory(self.project["id"], memory)
        run = self._run()
        self._draft()
        artifact = agent_actions._act_write(run, {"section_id": "sec-001"}, "act-001")
        self.assertEqual(artifact["blocks_added"], 0)
        self.assertTrue(artifact.get("no_evidence"))
        saved = draft_service.get_draft(self.project["id"])
        self.assertEqual(saved["sections"][0]["blocks"], [])
        questions = agent_state.open_questions(run)
        self.assertTrue(any(item.get("source") == "write" for item in questions))
        self.assertEqual(questions[0]["priority"], "high")

    def test_policy_skips_write_for_independent_run(self) -> None:
        run = agent_state.create_run_state(
            "独立目标", "demo", "auto", agent_state.DEFAULT_BUDGET,
        )
        self._steps_through_draft(run)
        plan = agent_policy.choose_next(run)
        self.assertNotEqual(plan["tool"], "write")
        self.assertEqual(plan["tool"], "review")

    def test_checkpoint_write_requires_approval(self) -> None:
        run = self._run()
        run["mode"] = "checkpoint"
        self._steps_through_draft(run)
        self._draft()
        plan = agent_policy.choose_next(run)
        self.assertEqual(plan["tool"], "write")
        self.assertTrue(plan["requires_approval"])

    def test_seed_papers_are_known_so_planner_reads_the_rest(self) -> None:
        run = self._run()
        run["paper_ids"] = ["P1", "P2"]
        run["seed_paper_ids"] = ["P1"]
        run["steps"] = [{
            "id": "act-001", "tool": "search", "status": "done",
            "artifact": {
                "selected": ["P1", "P2"],
                "candidates": [{"id": "P1"}, {"id": "P2"}],
            },
        }]
        plan = agent_policy.choose_next(run)
        self.assertEqual(plan["tool"], "read")
        self.assertEqual(plan["input"]["paper_id"], "P2")

    def test_critique_summarizes_write(self) -> None:
        run = self._run()
        step = {
            "id": "act-005", "tool": "write", "status": "done",
            "artifact": {"section_id": "sec-001", "blocks_added": 2, "evidence_added": 0},
        }
        observation = agent_policy.critique(run, step)
        self.assertIn("写入章节 sec-001", observation["summary"])
        self.assertIn("2", observation["summary"])

    def test_project_report_lists_project_evidence_and_draft_progress(self) -> None:
        run = self._run()
        run["seed_summary"] = {"evidence": 2, "questions": 1, "papers": 1}
        self._draft(blocks=[{
            "id": "blk-001", "type": "paragraph", "text": "段落。",
            "evidence_ids": ["pev-1"], "citation_keys": [],
        }])
        with patch.object(agent_actions.config, "LLM_ENABLED", False):
            markdown, mode = agent_actions.build_report(run)
        self.assertEqual(mode, "offline")
        self.assertIn(f"PaperPilot v{agent_actions.config.APP_VERSION}", markdown)
        self.assertIn("## 沿用的项目证据", markdown)
        self.assertIn("## 草稿进度", markdown)
        self.assertIn("1 节 / 1 块", markdown)

    def test_independent_report_has_no_project_sections(self) -> None:
        run = agent_state.create_run_state(
            "独立目标", "demo", "auto", agent_state.DEFAULT_BUDGET,
        )
        with patch.object(agent_actions.config, "LLM_ENABLED", False):
            markdown, mode = agent_actions.build_report(run)
        self.assertEqual(mode, "offline")
        self.assertIn(f"PaperPilot v{agent_actions.config.APP_VERSION}", markdown)
        self.assertNotIn("沿用的项目证据", markdown)
        self.assertNotIn("草稿进度", markdown)

    def test_write_section_uses_llm_when_available(self) -> None:
        self._draft()
        with patch("app.config.LLM_ENABLED", True), patch.object(
            llm, "chat", return_value="只用证据写成的段落（待核对）。"
        ):
            result = draft_service.write_section(
                self.project["id"], "sec-001", use_llm=True
            )
        self.assertEqual(result["mode"], "llm")
        block = draft_service.get_draft(self.project["id"])["sections"][0]["blocks"][0]
        self.assertIn("待核对", block["text"])
        self.assertEqual(block["evidence_ids"], ["pev-1"])

    def test_write_section_bumps_revision_and_reuses_citation_key(self) -> None:
        self._draft()
        first = draft_service.write_section(self.project["id"], "sec-001")
        draft = draft_service.get_draft(self.project["id"])
        second = draft_service.write_section(self.project["id"], "sec-001")
        self.assertEqual(first["blocks_added"], 1)
        self.assertEqual(second["blocks_added"], 0)
        self.assertTrue(second.get("no_evidence"))
        self.assertGreaterEqual(
            draft_service.get_draft(self.project["id"])["revision"],
            draft["revision"],
        )
        block = draft_service.get_draft(self.project["id"])["sections"][0]["blocks"][0]
        self.assertEqual(block["citation_keys"], ["author2024"])

    def _demo_paper(self, paper_id: str) -> dict:
        paper = search.get_paper(paper_id)
        self.assertIsNotNone(paper)
        return paper

    def test_known_papers_include_seed_missing_from_snapshot(self) -> None:
        run = self._run()
        run["seed_paper_ids"] = ["P1"]
        paper = self._demo_paper("2609.24288v1")
        agent_actions.save_papers(run, [paper])
        run["steps"] = [{
            "id": "act-001", "tool": "read", "status": "done",
            "artifact": {"paper": {"id": paper["id"]}},
        }]

        papers = agent_actions._known_papers(run)
        self.assertEqual([item["id"] for item in papers], [paper["id"], "P1"])

    def test_write_falls_back_when_imported_skeleton_has_content(self) -> None:
        run = self._run()
        self._steps_through_draft(run)
        run["steps"][-1]["artifact"]["markdown"] = (
            "## 结果\n\n已有结果段落。\n\n## 讨论\n\n已有讨论段落。"
        )
        plan = agent_policy.choose_next(run)
        self.assertEqual(plan["tool"], "write")
        self.assertEqual(plan["input"]["section_id"], "")

        artifact = agent_actions._act_write(run, plan["input"], "act-005")
        self.assertEqual(artifact["section_id"], "sec-001")
        self.assertGreaterEqual(artifact["blocks_added"], 1)
        draft = draft_service.get_draft(self.project["id"])
        self.assertEqual(
            [section["title"] for section in draft["sections"]],
            ["结果", "讨论"],
        )
        self.assertTrue(
            any(
                "pev-1" in block.get("evidence_ids") or []
                for block in draft["sections"][0]["blocks"]
            )
        )
        self.assertEqual(
            draft["sections"][0]["blocks"][-1]["status"],
            "draft",
        )

        self._append_done(run, "write", artifact)
        self.assertIsNone(agent_policy._draft_gap(run))
        self.assertEqual(agent_policy.choose_next(run)["tool"], "review")

    def test_write_fallback_without_evidence_records_question_and_moves_on(self) -> None:
        memory = project_state.read_memory(self.project["id"])
        memory["evidence"] = []
        project_state.write_memory(self.project["id"], memory)
        run = self._run()
        self._steps_through_draft(run)
        run["steps"][-1]["artifact"]["markdown"] = (
            "## 结果\n\n已有结果段落。\n\n## 讨论\n\n已有讨论段落。"
        )
        plan = agent_policy.choose_next(run)
        self.assertEqual(plan["tool"], "write")

        artifact = agent_actions._act_write(run, plan["input"], "act-005")
        self.assertEqual(artifact["blocks_added"], 0)
        self.assertTrue(artifact.get("no_evidence"))
        self.assertTrue(artifact.get("section_id"))
        questions = [
            item for item in agent_state.open_questions(run)
            if item.get("source") == "write"
        ]
        self.assertTrue(questions)
        self.assertEqual(questions[0]["priority"], "high")

        self._append_done(run, "write", artifact)
        self.assertIsNone(agent_policy._draft_gap(run))
        self.assertEqual(agent_policy.choose_next(run)["tool"], "review")

    def test_seeded_auto_run_compares_seed_and_read_then_reports(self) -> None:
        run = agent_runs.create_run(
            "低功耗 ADC 如何降低功耗", mode="auto",
            project_id=self.project["id"], autostart=False, max_papers=2,
        )
        first = self._demo_paper("2609.24288v1")
        second = self._demo_paper("2609.07059v3")
        agent_actions.save_papers(run, [first, second])
        run["steps"] = [{
            "id": "act-001", "tool": "search", "status": "done",
            "title": "检索候选论文", "rationale": "", "input": {},
            "artifact": {
                "selected": [first["id"], second["id"]],
                "candidates": [{"id": first["id"]}, {"id": second["id"]}],
                "new_ids": [first["id"], second["id"]],
            },
            "observation": {},
        }]
        run["plan"] = None
        agent_runs._save_run(run)

        run = agent_runs.execute_run(run["id"])
        self.assertEqual(run["status"], "completed")
        self.assertTrue(run.get("report", {}).get("path"))
        compare_steps = [
            step for step in run["steps"] if step["tool"] == "compare"
        ]
        self.assertEqual(len(compare_steps), 1)
        self.assertEqual(
            set(compare_steps[0]["artifact"]["paper_ids"]),
            {first["id"], "P1"},
        )
        write_steps = [
            step for step in run["steps"] if step["tool"] == "write"
        ]
        self.assertEqual(len(write_steps), 1)
        write_artifact = write_steps[0]["artifact"]
        write_questions = [
            item for item in run.get("open_questions", [])
            if item.get("source") == "write"
        ]
        self.assertTrue(
            int(write_artifact.get("blocks_added") or 0) > 0
            or write_questions
        )

    def test_project_auto_run_writes_evidence_blocks_and_completes(self) -> None:
        project = project_service.create_project(
            "低功耗 ADC 自动流程", "如何降低功耗？"
        )
        run = agent_runs.create_run(
            "低功耗 ADC 如何降低功耗", mode="auto",
            project_id=project["id"], autostart=False, max_papers=2,
        )
        run = agent_runs.execute_run(run["id"])
        self.assertEqual(run["status"], "completed")
        self.assertTrue(run.get("report", {}).get("path"))

        write_steps = [
            step for step in run["steps"] if step["tool"] == "write"
        ]
        self.assertEqual(len(write_steps), 1)
        artifact = write_steps[0]["artifact"]
        questions = [
            item for item in run.get("open_questions", [])
            if item.get("source") == "write"
        ]
        self.assertTrue(
            int(artifact.get("blocks_added") or 0) > 0 or questions
        )
        draft = draft_service.get_draft(project["id"])
        anchored = [
            block
            for section in draft.get("sections") or []
            for block in section.get("blocks") or []
            if block.get("evidence_ids")
        ]
        self.assertTrue(anchored)
        self.assertIsNone(agent_policy._draft_gap(run))

    def test_act_write_llm_marks_needs_evidence_and_charges_budget(self) -> None:
        run = self._run()
        self._draft()
        with patch("app.config.LLM_ENABLED", True), patch.object(
            llm, "chat", return_value="只用证据写成的段落（待核对）。"
        ):
            artifact = agent_actions._act_write(
                run, {"section_id": "sec-001", "use_llm": True}, "act-001"
            )
        self.assertEqual(artifact["mode"], "llm")
        self.assertEqual(artifact["blocks_added"], 1)
        block = draft_service.get_draft(self.project["id"])["sections"][0]["blocks"][0]
        self.assertEqual(block["status"], "needs_evidence")
        self.assertEqual(run["budget_used"]["llm_calls"], 1)

    def test_act_write_llm_failure_keeps_offline_status_but_still_charges(self) -> None:
        run = self._run()
        self._draft()
        with patch("app.config.LLM_ENABLED", True), patch.object(
            llm, "chat", side_effect=RuntimeError("llm unavailable")
        ):
            artifact = agent_actions._act_write(
                run, {"section_id": "sec-001", "use_llm": True}, "act-001"
            )
        self.assertEqual(artifact["mode"], "offline")
        self.assertEqual(artifact["blocks_added"], 1)
        block = draft_service.get_draft(self.project["id"])["sections"][0]["blocks"][0]
        self.assertEqual(block["status"], "draft")
        self.assertIn("待结合研究问题改写", block["text"])
        # v8.1.3: the request went out, so it costs budget even though it failed.
        self.assertEqual(run["budget_used"]["llm_calls"], 1)

    def test_act_write_llm_mixed_blocks_charge_the_answer_and_the_failure(self) -> None:
        memory = project_state.read_memory(self.project["id"])
        memory["evidence"].append({
            "id": "pev-2", "paper_id": "P1", "title": "Paper One",
            "section": "Results", "page": 3,
            "quote": "The measured leakage stays below 12 nA.",
            "verified": True, "kind": "text",
        })
        project_state.write_memory(self.project["id"], memory)
        run = self._run()
        self._draft()
        with patch("app.config.LLM_ENABLED", True), patch.object(
            llm, "chat", side_effect=["LLM 段落（待核对）。", RuntimeError("boom")]
        ):
            artifact = agent_actions._act_write(
                run, {"section_id": "sec-001", "use_llm": True}, "act-001"
            )
        self.assertEqual(artifact["mode"], "llm")
        self.assertEqual(artifact["blocks_added"], 2)
        self.assertEqual(artifact["llm_blocks"], 1)
        blocks = draft_service.get_draft(self.project["id"])["sections"][0]["blocks"]
        self.assertEqual(
            [block["status"] for block in blocks],
            ["needs_evidence", "draft"],
        )
        self.assertEqual(run["budget_used"]["llm_calls"], 2)


if __name__ == "__main__":
    unittest.main()
