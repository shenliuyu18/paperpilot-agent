from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import agent_actions, agent_runs, agent_state


def _paper(pid: str) -> dict:
    return {"id": pid, "title": f"Paper {pid}", "authors": ["A"], "year": 2024, "venue": "V"}


def _fake_execute(run: dict, plan: dict, action_id: str = "") -> dict:
    tool = plan["tool"]
    if tool == "search":
        papers = [_paper("P1"), _paper("P2")]
        agent_actions.save_papers(run, papers)
        return {
            "query": plan["input"].get("query"),
            "candidates": [{"id": "P1"}, {"id": "P2"}],
            "selected": ["P1", "P2"],
            "new_ids": ["P1", "P2"],
            "evidence_added": 0,
        }
    if tool == "read":
        paper_id = plan["input"].get("paper_id") or "P1"
        agent_state.add_evidence(
            run,
            [{"paper_id": paper_id, "section": "Abstract", "page": 1,
              "claim": "problem", "quote": f"Evidence for {paper_id}"}],
            action_id,
        )
        return {"paper": {"id": paper_id}, "evidence_added": 1}
    if tool == "compare":
        return {"paper_ids": list(run.get("paper_ids") or []), "consensus": ["c"], "gaps": [], "evidence_added": 0}
    if tool == "topic":
        return {"paper_ids": list(run.get("paper_ids") or []),
                "questions": [{"id": "Q1", "title": "方向一"}], "evidence_added": 0}
    if tool == "draft":
        return {"paper_ids": list(run.get("paper_ids") or []), "markdown": "# Draft", "evidence_added": 0}
    if tool == "write":
        return {"section_id": plan["input"].get("section_id") or "sec-001",
                "blocks_added": 1, "evidence_ids": [], "mode": "offline",
                "draft_revision": 1, "evidence_added": 0}
    if tool == "review":
        return {"paper_ids": list(run.get("paper_ids") or []),
                "claim_summary": {"total": 2, "supported": 2, "partial": 0, "unsupported": 0},
                "argument": {"score": 85, "actions": []}, "evidence_added": 0}
    if tool == "ask":
        return {"question_id": plan["input"].get("question_id"), "citations": [{"index": 1}],
                "verification": {"verified": 1, "total": 1}, "evidence_added": 1}
    if tool == "report":
        path = agent_state.run_dir(run["id"]) / "report.md"
        path.write_text("# Report\n", encoding="utf-8")
        return {"path": str(path), "chars": 9, "mode": "offline", "evidence_added": 0}
    raise AssertionError(tool)


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(
            os.environ,
            {
                "PAPERPILOT_AGENT_FILE": str(self.root / "runs.json"),
                "PAPERPILOT_AGENT_DIR": str(self.root / "runs"),
            },
        )
        self.env.start()
        self.executor = patch.object(agent_actions, "execute_action", side_effect=_fake_execute)
        self.executor.start()
        agent_runs._RECOVERED.clear()

    def tearDown(self) -> None:
        self.executor.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_auto_runs_until_complete(self) -> None:
        run = agent_runs.create_run("目标", mode="auto", autostart=False)
        run = agent_runs.execute_run(run["id"])
        self.assertEqual(run["status"], "completed")
        self.assertTrue(run.get("report", {}).get("path"))
        tools = [step["tool"] for step in run["steps"]]
        self.assertEqual(tools, ["search", "read", "read", "compare", "topic", "draft", "review", "report"])
        self.assertGreaterEqual(len(run["evidence"]), 2)

    def test_checkpoint_search_pauses_and_paper_selection_applies(self) -> None:
        run = agent_runs.create_run("目标", mode="checkpoint", autostart=False)
        run = agent_runs.execute_run(run["id"])
        self.assertEqual(run["status"], "awaiting_approval")
        self.assertEqual(run["plan"]["tool"], "read")
        run = agent_runs.act(run["id"], "approve", paper_ids=["P2"], autostart=False)
        self.assertEqual(run["paper_ids"], ["P2"])
        self.assertEqual(agent_runs.get_run(run["id"])["decisions"][-1]["kind"], "papers")
        run = agent_runs.execute_run(run["id"])
        self.assertEqual(run["status"], "awaiting_approval")
        self.assertEqual(run["steps"][-1]["tool"], "topic")
        run = agent_runs.act(run["id"], "approve", topic_id="Q1", autostart=False)
        self.assertEqual(run["selected_topic"]["id"], "Q1")
        run = agent_runs.execute_run(run["id"])
        self.assertEqual(run["status"], "completed")

    def test_step_mode_pauses_before_each_action(self) -> None:
        run = agent_runs.create_run("目标", mode="step", autostart=False)
        self.assertEqual(run["status"], "awaiting_approval")
        self.assertEqual(run["plan"]["tool"], "search")
        run = agent_runs.act(run["id"], "approve", autostart=False)
        run = agent_runs.execute_run(run["id"])
        self.assertEqual(run["status"], "awaiting_approval")
        self.assertEqual(len(run["steps"]), 1)
        self.assertEqual(run["steps"][0]["tool"], "search")

    def test_stop_forces_report(self) -> None:
        run = agent_runs.create_run("目标", mode="step", autostart=False)
        run = agent_runs.act(run["id"], "stop", autostart=False)
        run = agent_runs.execute_run(run["id"])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["steps"][-1]["tool"], "report")
        self.assertTrue(run.get("force_report"))

    def test_budget_forces_report(self) -> None:
        run = agent_runs.create_run("目标", mode="auto", max_actions=2, autostart=False)
        run = agent_runs.execute_run(run["id"])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["steps"][-1]["tool"], "report")
        self.assertIn("预算", run["steps"][-1]["rationale"])

    def test_restart_marks_interrupted_and_resume(self) -> None:
        run = agent_runs.create_run("目标", mode="auto", autostart=False)
        run["status"] = "running"
        run["steps"].append({"id": "act-001", "tool": "search", "title": "检索", "status": "running",
                             "artifact": {}, "observation": {}})
        agent_runs._save_run(run)
        agent_runs._RECOVERED.clear()
        loaded = agent_runs.get_run(run["id"])
        self.assertEqual(loaded["status"], "interrupted")
        self.assertEqual(loaded["steps"][0]["status"], "failed")
        resumed = agent_runs.act(run["id"], "resume", autostart=False)
        self.assertEqual(resumed["status"], "queued")

    def test_legacy_v09_run_is_readable(self) -> None:
        legacy = {
            "id": "run-old",
            "goal": "旧任务",
            "source": "demo",
            "status": "completed",
            "paper_ids": ["P1"],
            "steps": [{"id": "search", "title": "检索与筛选", "status": "done",
                       "summary": "ok", "artifact": {"selected": ["P1"]}}],
            "report": {"path": str(self.root / "old.md"), "chars": 3, "mode": "offline"},
        }
        agent_state.write_store({"version": 1, "runs": {"run-old": legacy}})
        loaded = agent_runs.get_run("run-old")
        self.assertTrue(loaded["legacy"])
        self.assertEqual(loaded["steps"][0]["tool"], "search")
        self.assertEqual(loaded["version"], 3)


if __name__ == "__main__":
    unittest.main()
