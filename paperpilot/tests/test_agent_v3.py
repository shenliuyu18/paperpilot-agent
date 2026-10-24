from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import agent_runs, agent_state, project_service, project_state


class AgentV3Tests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_normalize_v2_run_adds_v3_fields(self) -> None:
        run = {
            "version": 2,
            "id": "run-old",
            "goal": "目标",
            "status": "completed",
            "steps": [],
            "evidence": [],
            "open_questions": [],
            "decisions": [],
            "report": {},
        }
        migrated, changed = agent_state.normalize_run(run)
        self.assertTrue(changed)
        self.assertEqual(migrated["version"], 3)
        self.assertIsNone(migrated["project_id"])
        self.assertEqual(migrated["steering"], [])
        self.assertFalse(migrated["pause_requested"])
        self.assertEqual(migrated["stream_seq"], 0)
        self.assertIn("write", agent_state.TOOL_DEFS)

    def test_project_run_gets_seed_and_active_binding(self) -> None:
        project = project_service.create_project("低功耗 ADC", "如何降低功耗？")
        memory = project_state.read_memory(project["id"])
        memory["evidence"] = [{
            "id": "pev-1", "paper_id": "P1", "title": "Paper One",
            "section": "Abstract", "page": 1, "quote": "Seed evidence",
            "verified": True, "kind": "text",
        }]
        memory["open_questions"] = [{
            "id": "pq-1", "question": "未解决问题", "status": "open",
            "priority": "high", "kind": "evidence",
        }]
        memory["papers"] = {"P1": {"id": "P1", "title": "Paper One", "authors": ["A"], "year": 2024}}
        project_state.write_memory(project["id"], memory)

        run = agent_runs.create_run(
            "继续研究", mode="auto", project_id=project["id"], autostart=False,
        )
        self.assertEqual(run["project_id"], project["id"])
        self.assertEqual(run["seed_summary"]["evidence"], 1)
        self.assertEqual(run["seed_paper_ids"], ["P1"])
        self.assertEqual(run["evidence"][0]["project_evidence_id"], "pev-1")
        self.assertIn(run["id"], project_state.read_projects()["projects"][project["id"]]["run_ids"])
        self.assertEqual(
            project_state.read_projects()["projects"][project["id"]]["active_run_id"],
            run["id"],
        )
        with self.assertRaises(ValueError):
            agent_runs.create_run("第二个", mode="auto", project_id=project["id"], autostart=False)

    def test_remove_run_detaches_from_project(self) -> None:
        project = project_service.create_project("项目", "问题？")
        run = agent_runs.create_run("目标", mode="auto", project_id=project["id"], autostart=False)
        self.assertTrue(agent_runs.remove_run(run["id"]))
        stored = project_state.read_projects()["projects"][project["id"]]
        self.assertNotIn(run["id"], stored["run_ids"])
        self.assertIsNone(stored["active_run_id"])

    def test_known_paper_ids_includes_seed(self) -> None:
        run = agent_state.create_run_state("目标", "demo", "auto", agent_state.DEFAULT_BUDGET)
        run["seed_paper_ids"] = ["P9"]
        self.assertIn("P9", agent_state.known_paper_ids(run))

    def test_concurrent_project_run_start_is_atomic(self) -> None:
        project = project_service.create_project("并发启动", "并发启动问题？")
        barrier = threading.Barrier(2)
        started: list[dict] = []
        errors: list[BaseException] = []
        guard = threading.Lock()

        def worker() -> None:
            try:
                barrier.wait(timeout=5)
                run = agent_runs.create_run(
                    "并发目标", mode="auto", project_id=project["id"], autostart=False,
                )
                with guard:
                    started.append(run)
            except BaseException as exc:  # pragma: no cover - reported below
                with guard:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))

        self.assertEqual(len(started), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)
        self.assertIn("已有活动运行", str(errors[0]))

        winner = started[0]
        stored = project_state.read_projects()["projects"][project["id"]]
        self.assertEqual(stored["run_ids"], [winner["id"]])
        self.assertEqual(stored["active_run_id"], winner["id"])
        store = agent_state.read_store()["runs"]
        project_runs = [
            run_id for run_id, run in store.items()
            if run.get("project_id") == project["id"]
        ]
        self.assertEqual(project_runs, [winner["id"]])
        self.assertEqual(
            [run_id for run_id in project_runs if run_id not in stored["run_ids"]],
            [],
        )

    def test_concurrent_reads_survive_store_replace(self) -> None:
        project = project_service.create_project("并发读取", "并发读取问题？")
        run = agent_runs.create_run(
            "读取目标", mode="auto", project_id=project["id"], autostart=False,
        )
        stop = threading.Event()
        errors: list[BaseException] = []
        guard = threading.Lock()

        def reader() -> None:
            try:
                while not stop.is_set():
                    project_service.get_project(project["id"])
                    project_service.ensure_can_start_run(
                        project["id"], exclude_run_id=run["id"]
                    )
                    time.sleep(0.001)
            except BaseException as exc:  # pragma: no cover - reported below
                with guard:
                    errors.append(exc)

        readers = [threading.Thread(target=reader) for _ in range(3)]
        for thread in readers:
            thread.start()
        try:
            for _ in range(40):
                current = agent_runs.get_run(run["id"])
                agent_runs._save_run(current)
        finally:
            stop.set()
            for thread in readers:
                thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in readers))
        self.assertEqual(errors, [])

    def test_reconcile_project_drops_run_missing_from_store(self) -> None:
        project = project_service.create_project("自愈项目", "自愈问题？")
        stored = project_state.read_projects()
        stored["projects"][project["id"]]["run_ids"] = ["run-missing"]
        stored["projects"][project["id"]]["active_run_id"] = "run-missing"
        project_state.write_projects(stored)

        detail = project_service.get_project(project["id"])
        self.assertEqual(detail["run_ids"], [])
        self.assertIsNone(detail["active_run_id"])
        self.assertEqual(detail["counts"]["runs"], 0)


if __name__ == "__main__":
    unittest.main()
