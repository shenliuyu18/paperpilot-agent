from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import agent_state


class AgentStateTests(unittest.TestCase):
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

    def test_create_state_has_plan_slots_and_budget(self) -> None:
        run = agent_state.create_run_state(
            "低功耗 ADC",
            "demo",
            "checkpoint",
            agent_state.DEFAULT_BUDGET,
        )
        self.assertEqual(run["version"], 3)
        self.assertEqual(run["mode"], "checkpoint")
        self.assertEqual(run["steps"], [])
        self.assertEqual(run["budget"]["max_actions"], 14)
        self.assertEqual(run["critic"]["next_hint"], "continue")
        self.assertEqual(run["evidence"], [])
        self.assertEqual(run["open_questions"], [])
        self.assertTrue(run["id"].startswith("run-"))

    def test_evidence_dedupes_and_questions_resolve(self) -> None:
        run = agent_state.create_run_state("目标", "demo", "auto", agent_state.DEFAULT_BUDGET)
        item = {
            "paper_id": "P1",
            "section": "Abstract",
            "page": 1,
            "claim": "c",
            "quote": "q",
        }
        agent_state.add_evidence(run, [item, item], "act-001")
        self.assertEqual(len(run["evidence"]), 1)
        question = agent_state.upsert_question(
            run,
            "这个缺口存在吗？",
            kind="evidence",
            priority="high",
        )
        agent_state.resolve_question(run, question["id"], "找到 2 条证据")
        self.assertEqual(run["open_questions"][0]["status"], "resolved")
        self.assertEqual(run["open_questions"][0]["priority"], "high")

    def test_evidence_dedupes_across_sections(self) -> None:
        run = agent_state.create_run_state("目标", "demo", "auto", agent_state.DEFAULT_BUDGET)
        base = {
            "paper_id": "P1",
            "claim": "c",
            "quote": "Retrieval augmented generation reduces hallucinations in domain QA.",
        }
        agent_state.add_evidence(
            run,
            [
                {**base, "section": "Abstract", "page": 1},
                {**base, "section": "Answer", "page": 2, "quote": "Retrieval  augmented generation reduces hallucinations in domain QA."},
            ],
            "act-001",
        )
        self.assertEqual(len(run["evidence"]), 1)
        self.assertEqual(agent_state.open_questions(run), [])

    def test_budget_and_migration(self) -> None:
        run = agent_state.create_run_state(
            "目标",
            "demo",
            "auto",
            {**agent_state.DEFAULT_BUDGET, "max_actions": 1},
        )
        agent_state.spend_action(run)
        self.assertIn("动作", agent_state.budget_exhausted(run))
        legacy = {
            "id": "run-old",
            "goal": "目标",
            "status": "awaiting_approval",
            "paper_ids": ["P1"],
            "steps": [
                {
                    "id": "search",
                    "title": "检索与筛选",
                    "status": "done",
                    "summary": "ok",
                    "artifact": {"selected": ["P1"]},
                },
                {
                    "id": "read",
                    "title": "精读证据",
                    "status": "pending",
                    "summary": "",
                    "artifact": {},
                },
            ],
            "report": {},
        }
        migrated, changed = agent_state.normalize_run(legacy)
        self.assertTrue(changed)
        self.assertEqual(migrated["version"], 3)
        self.assertEqual(migrated["steps"][0]["tool"], "search")
        self.assertEqual(migrated["steps"][0]["status"], "done")
        self.assertTrue(migrated["legacy"])
        self.assertEqual(migrated["plan"]["tool"], "read")
        self.assertEqual(migrated["critic"]["next_hint"], "continue")

    def test_store_round_trip_and_summary(self) -> None:
        run = agent_state.create_run_state("目标", "demo", "auto", agent_state.DEFAULT_BUDGET)
        data = agent_state.read_store()
        data["runs"][run["id"]] = run
        agent_state.write_store(data)
        loaded = agent_state.read_store()["runs"][run["id"]]
        self.assertEqual(loaded["goal"], "目标")
        summary = agent_state.summarize_run(loaded)
        self.assertEqual(summary["action_count"], 0)
        self.assertEqual(summary["mode"], "auto")


if __name__ == "__main__":
    unittest.main()
