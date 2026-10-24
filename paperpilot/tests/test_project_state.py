from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import agent_state, project_state


class ProjectStateTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _project(self) -> dict:
        return project_state.create_project(
            "低功耗 ADC",
            "如何在保持精度的同时降低功耗？",
            scope="CIM/ADC",
            constraints=["优先开放获取"],
            tags=["CIM"],
        )

    def test_create_project_has_defaults_and_counts(self) -> None:
        project = self._project()
        self.assertTrue(project["id"].startswith("prj-"))
        self.assertEqual(project["stage"], "exploring")
        self.assertEqual(project["status"], "active")
        self.assertEqual(project["run_ids"], [])
        self.assertIsNone(project["active_run_id"])
        self.assertEqual(project["counts"]["evidence"], 0)
        stored = project_state.read_projects()["projects"][project["id"]]
        self.assertEqual(stored["title"], "低功耗 ADC")

    def test_promote_run_dedupes_and_is_idempotent(self) -> None:
        project = self._project()
        run = agent_state.create_run_state("目标", "demo", "auto", agent_state.DEFAULT_BUDGET)
        item = {
            "paper_id": "P1",
            "title": "Paper One",
            "section": "Results",
            "page": 3,
            "claim": "功耗更低",
            "quote": "The prototype consumes 4.2 fJ/conversion-step.",
            "verified": True,
            "kind": "text",
        }
        agent_state.add_evidence(run, [item, item], "act-001")
        run["open_questions"] = [{
            "id": "q-001",
            "question": "校准开销是否抵消收益？",
            "status": "open",
            "priority": "high",
            "kind": "evidence",
            "source": "critic",
        }]
        run["decisions"] = [{"kind": "topic", "choice": "校准开销", "note": "用户选择"}]
        paper_dir = agent_state.run_dir(run["id"])
        (paper_dir / "papers.json").write_text(
            json.dumps([{
                "id": "P1",
                "title": "Paper One",
                "authors": ["A. Author"],
                "year": 2024,
                "venue": "V",
                "doi": "10.1/x",
                "url": "https://example.org/p1",
                "source": "demo",
            }], ensure_ascii=False),
            encoding="utf-8",
        )

        memory = project_state.promote_run(project["id"], run)
        self.assertEqual(len(memory["evidence"]), 1)
        self.assertTrue(memory["evidence"][0]["id"].startswith("pev-"))
        self.assertEqual(memory["evidence"][0]["origin"]["run_id"], run["id"])
        self.assertEqual(memory["open_questions"][0]["status"], "open")
        self.assertEqual(memory["papers"]["P1"]["year"], 2024)

        again = project_state.promote_run(project["id"], run)
        self.assertEqual(len(again["evidence"]), 1)
        self.assertEqual(len(again["decisions"]), 1)

    def test_resolved_then_reopened_question_updates_latest_status(self) -> None:
        project = self._project()
        first = agent_state.create_run_state("目标", "demo", "auto", agent_state.DEFAULT_BUDGET)
        first["open_questions"] = [{
            "id": "q-001", "question": "缺口存在吗？", "status": "resolved",
            "priority": "high", "kind": "evidence", "source": "critic",
            "resolution": "已补证据",
        }]
        project_state.promote_run(project["id"], first)
        self.assertEqual(project_state.read_memory(project["id"])["open_questions"][0]["status"], "resolved")

        second = agent_state.create_run_state("目标", "demo", "auto", agent_state.DEFAULT_BUDGET)
        second["open_questions"] = [{
            "id": "q-001", "question": "缺口存在吗？", "status": "open",
            "priority": "high", "kind": "evidence", "source": "critic",
        }]
        project_state.promote_run(project["id"], second)
        question = project_state.read_memory(project["id"])["open_questions"][0]
        self.assertEqual(question["status"], "open")
        self.assertEqual(question["recurrences"], 2)

    def test_seed_uses_verified_evidence_and_counts(self) -> None:
        project = self._project()
        memory = project_state.read_memory(project["id"])
        memory["evidence"] = [
            {"id": "pev-1", "paper_id": "P1", "quote": "verified", "verified": True, "title": "P1"},
            {"id": "pev-2", "paper_id": "P2", "quote": "unverified", "verified": False, "title": "P2"},
        ]
        memory["open_questions"] = [{
            "id": "pq-1", "question": "未解决问题", "status": "open",
            "priority": "high", "kind": "evidence",
        }]
        memory["papers"] = {"P1": {"id": "P1", "title": "P1", "authors": ["A"], "year": 2024}}
        project_state.write_memory(project["id"], memory)

        seed = project_state.seed_memory(project["id"], max_papers=3)
        self.assertEqual([item["paper_id"] for item in seed["evidence"]], ["P1"])
        self.assertEqual(seed["summary"]["evidence"], 1)
        self.assertEqual(seed["summary"]["questions"], 1)
        self.assertEqual(seed["papers"][0]["id"], "P1")


if __name__ == "__main__":
    unittest.main()
