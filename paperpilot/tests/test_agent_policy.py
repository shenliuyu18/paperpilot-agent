from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import agent_policy, agent_state


def _run(**kwargs) -> dict:
    run = agent_state.create_run_state("低功耗 ADC", "demo", "auto", agent_state.DEFAULT_BUDGET)
    run.update(kwargs)
    return run


def _done(run: dict, tool: str, artifact: dict, **extra) -> dict:
    step = {
        "id": agent_state.new_step_id(run),
        "tool": tool,
        "title": agent_state.TOOL_DEFS[tool]["title"],
        "status": "done",
        "rationale": "",
        "input": extra.pop("input", {}),
        "artifact": artifact,
        "observation": {},
        "started_at": "2026-10-20 22:00:00",
        "finished_at": "2026-10-20 22:00:01",
        "attempts": 1,
        "checkpoint": tool in {"search", "topic"},
        "decision": None,
        "error": "",
    }
    step.update(extra)
    run["steps"].append(step)
    return step


def _seed_through_review(run: dict, unsupported: int = 0, score: int = 80) -> None:
    _done(run, "search", {"selected": ["P1", "P2"], "candidates": [{"id": "P1"}, {"id": "P2"}]})
    _done(run, "read", {"paper": {"id": "P1"}, "evidence_added": 2})
    _done(run, "read", {"paper": {"id": "P2"}, "evidence_added": 2})
    _done(run, "compare", {"paper_ids": ["P1", "P2"], "gaps": [], "consensus": ["c"]})
    _done(run, "topic", {"paper_ids": ["P1", "P2"], "questions": [{"id": "Q1", "title": "方向一"}]})
    _done(run, "draft", {"paper_ids": ["P1", "P2"], "markdown": "# Draft"})
    _done(
        run,
        "review",
        {
            "paper_ids": ["P1", "P2"],
            "claim_summary": {"total": 4, "supported": 4 - unsupported, "partial": 0, "unsupported": unsupported},
            "argument": {"score": score, "actions": []},
        },
    )


class AgentPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "PAPERPILOT_AGENT_FILE": str(Path(self.tmp.name) / "runs.json"),
                "PAPERPILOT_AGENT_DIR": str(Path(self.tmp.name) / "runs"),
            },
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_initial_plan_is_search(self) -> None:
        run = _run()
        plan = agent_policy.choose_next(run)
        self.assertEqual(plan["tool"], "search")
        self.assertIn("候选", plan["rationale"])
        self.assertFalse(plan["requires_approval"])

    def test_after_search_reads_unread_papers(self) -> None:
        run = _run(paper_ids=["P1", "P2"])
        _done(run, "search", {"selected": ["P1", "P2"]})
        plan = agent_policy.choose_next(run)
        self.assertEqual(plan["tool"], "read")
        self.assertEqual(plan["input"]["paper_id"], "P1")

    def test_after_two_reads_compares(self) -> None:
        run = _run(paper_ids=["P1", "P2"])
        _done(run, "search", {"selected": ["P1", "P2"]})
        _done(run, "read", {"paper": {"id": "P1"}})
        _done(run, "read", {"paper": {"id": "P2"}})
        plan = agent_policy.choose_next(run)
        self.assertEqual(plan["tool"], "compare")
        self.assertEqual(set(plan["input"]["paper_ids"]), {"P1", "P2"})

    def test_topic_then_draft_then_review(self) -> None:
        run = _run(paper_ids=["P1", "P2"])
        _done(run, "search", {"selected": ["P1", "P2"]})
        _done(run, "read", {"paper": {"id": "P1"}})
        _done(run, "read", {"paper": {"id": "P2"}})
        _done(run, "compare", {"paper_ids": ["P1", "P2"]})
        self.assertEqual(agent_policy.choose_next(run)["tool"], "topic")
        _done(run, "topic", {"paper_ids": ["P1", "P2"], "questions": [{"id": "Q1"}]})
        self.assertEqual(agent_policy.choose_next(run)["tool"], "draft")
        _done(run, "draft", {"paper_ids": ["P1", "P2"], "markdown": "# x"})
        self.assertEqual(agent_policy.choose_next(run)["tool"], "review")

    def test_completed_review_reports(self) -> None:
        run = _run(paper_ids=["P1", "P2"])
        _seed_through_review(run, unsupported=0, score=85)
        self.assertEqual(agent_policy.choose_next(run)["tool"], "report")
        _done(run, "report", {"path": "r.md", "chars": 10, "mode": "offline"})
        self.assertIsNone(agent_policy.choose_next(run))

    def test_unsupported_claims_trigger_ask_then_search(self) -> None:
        run = _run(paper_ids=["P1", "P2"])
        _seed_through_review(run, unsupported=2, score=40)
        q = agent_state.upsert_question(run, "这句结论有哪些直接证据？", kind="claim", priority="high")
        plan = agent_policy.choose_next(run)
        self.assertEqual(plan["tool"], "ask")
        self.assertEqual(plan["input"]["question_id"], q["id"])
        _done(run, "ask", {"question_id": q["id"], "question": q["question"], "citations": [{"index": 1}], "evidence_added": 1},
              input={"question_id": q["id"], "question": q["question"]})
        plan = agent_policy.choose_next(run)
        self.assertIn(plan["tool"], {"search", "report"})
        if plan["tool"] == "search":
            self.assertTrue(plan["input"].get("expansion"))

    def test_budget_exhaustion_forces_report(self) -> None:
        run = _run(paper_ids=["P1"])
        run["budget_used"]["actions"] = run["budget"]["max_actions"]
        plan = agent_policy.choose_next(run)
        self.assertEqual(plan["tool"], "report")
        self.assertIn("预算", plan["rationale"])

    def test_mode_pause_rules(self) -> None:
        step_run = _run(mode="step")
        checkpoint_run = _run(mode="checkpoint")
        auto_run = _run(mode="auto")
        plan = {"tool": "search", "requires_approval": False}
        self.assertTrue(agent_policy.should_pause_before(step_run, plan))
        self.assertFalse(agent_policy.should_pause_before(auto_run, plan))
        _done(checkpoint_run, "search", {"selected": ["P1"]})
        next_plan = agent_policy.choose_next(checkpoint_run)
        self.assertTrue(next_plan["requires_approval"])
        self.assertTrue(agent_policy.should_pause_before(checkpoint_run, next_plan))

    def test_critic_adds_and_resolves_questions(self) -> None:
        run = _run()
        search = _done(run, "search", {"selected": [], "candidates": []})
        observation = agent_policy.critique(run, search)
        self.assertTrue(agent_state.open_questions(run))
        self.assertEqual(observation["next_hint"], "expand_search")
        q = agent_state.upsert_question(run, "代价是什么？", kind="evidence", priority="high")
        ask = _done(run, "ask", {"question_id": q["id"], "citations": [{"index": 1}], "evidence_added": 1},
                   input={"question_id": q["id"]})
        agent_state.resolve_question(run, q["id"], "1 条引用")
        agent_policy.critique(run, ask)
        self.assertEqual(q["status"], "resolved")


if __name__ == "__main__":
    unittest.main()
