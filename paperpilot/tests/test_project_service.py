from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import (
    agent_runs,
    agent_state,
    draft_service,
    project_service,
    project_state,
)


class ProjectServiceTests(unittest.TestCase):
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

    def _project(self) -> dict:
        return project_service.create_project("低功耗 ADC", "如何降低功耗？")

    def _run(self, status: str = "completed") -> dict:
        run = agent_state.create_run_state("目标", "demo", "auto", agent_state.DEFAULT_BUDGET)
        run["status"] = status
        data = agent_state.read_store()
        data["runs"][run["id"]] = run
        agent_state.write_store(data)
        return run

    def test_create_list_get_and_update(self) -> None:
        project = self._project()
        listed = project_service.list_projects()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], project["id"])
        detail = project_service.get_project(project["id"])
        self.assertEqual(detail["counts"]["runs"], 0)
        updated = project_service.update_project(project["id"], {"stage": "reading", "title": "新标题"})
        self.assertEqual(updated["stage"], "reading")
        self.assertEqual(updated["title"], "新标题")
        self.assertEqual(project_service.get_project(project["id"])["stage"], "reading")

    def test_bind_and_release_run_controls_active_run(self) -> None:
        project = self._project()
        run = self._run("running")
        bound = project_service.bind_run(project["id"], run["id"])
        self.assertEqual(bound["active_run_id"], run["id"])
        self.assertIn(run["id"], bound["run_ids"])
        with self.assertRaises(ValueError):
            project_service.ensure_can_start_run(project["id"])
        project_service.ensure_can_start_run(project["id"], exclude_run_id=run["id"])
        project_service.release_run(project["id"], run["id"])
        self.assertIsNone(project_state.read_projects()["projects"][project["id"]]["active_run_id"])

    def test_attach_detach_completed_v1_run(self) -> None:
        project = self._project()
        run = self._run("completed")
        attached = project_service.attach_run(project["id"], run["id"])
        self.assertIn(run["id"], attached["run_ids"])
        stored = agent_state.read_store()["runs"][run["id"]]
        self.assertEqual(stored["project_id"], project["id"])
        detached = project_service.detach_run(project["id"], run["id"])
        self.assertNotIn(run["id"], detached["run_ids"])
        self.assertIsNone(agent_state.read_store()["runs"][run["id"]]["project_id"])

    def test_archive_and_delete_keep_runs(self) -> None:
        project = self._project()
        run = self._run("completed")
        project_service.attach_run(project["id"], run["id"])
        archived = project_service.archive_project(project["id"])
        self.assertEqual(archived["status"], "archived")
        result = project_service.delete_project(project["id"], keep_runs=True)
        self.assertEqual(result["kept_runs"], 1)
        self.assertIsNone(project_service.get_project(project["id"]))
        self.assertEqual(agent_state.read_store()["runs"][run["id"]]["project_id"], None)

    def test_on_run_saved_promotes_memory_and_clears_active(self) -> None:
        project = self._project()
        run = self._run("running")
        project_service.bind_run(project["id"], run["id"])
        run = agent_state.read_store()["runs"][run["id"]]
        run["project_id"] = project["id"]
        run["evidence"] = [{
            "paper_id": "P1", "title": "Paper", "section": "Abstract",
            "page": 1, "quote": "Evidence quote", "verified": True,
        }]
        run["status"] = "completed"
        data = agent_state.read_store()
        data["runs"][run["id"]] = run
        agent_state.write_store(data)
        project_service.on_run_saved(run)
        detail = project_service.get_project(project["id"])
        self.assertEqual(detail["counts"]["evidence"], 1)
        self.assertIsNone(detail["active_run_id"])

    def test_bind_run_and_on_run_saved_persist_counts_directly(self) -> None:
        project = self._project()
        draft_service.save_draft(project["id"], {
            "revision": 0,
            "title": "草稿",
            "citation_style": "numeric",
            "sections": [
                {
                    "id": "sec-001",
                    "title": "引言",
                    "blocks": [{
                        "id": "blk-001",
                        "type": "paragraph",
                        "text": "第一个段落。",
                        "evidence_ids": [],
                        "citation_keys": [],
                    }],
                },
                {
                    "id": "sec-002",
                    "title": "方法",
                    "blocks": [{
                        "id": "blk-002",
                        "type": "paragraph",
                        "text": "第二个段落。",
                        "evidence_ids": [],
                        "citation_keys": [],
                    }],
                },
            ],
        })
        run = self._run("running")
        bound = project_service.bind_run(project["id"], run["id"])
        stored = project_state.read_projects()["projects"][project["id"]]
        # bind_run must persist the recounted copy it returns; the old
        # _recount(_save(project)) order stored pre-recount counts.
        self.assertEqual(stored["counts"], bound["counts"])
        self.assertEqual(stored["counts"]["runs"], 1)
        self.assertEqual(stored["counts"]["draft_sections"], 2)
        self.assertEqual(stored["counts"]["draft_blocks"], 2)

        run = agent_state.read_store()["runs"][run["id"]]
        run["project_id"] = project["id"]
        run["status"] = "completed"
        run["evidence"] = [{
            "paper_id": "P1", "title": "Paper", "section": "Abstract",
            "page": 1, "quote": "Evidence quote", "verified": True,
        }]
        run["open_questions"] = [{
            "id": "q-001",
            "question": "校准开销是否抵消收益？",
            "status": "open",
            "priority": "high",
            "kind": "evidence",
        }]
        data = agent_state.read_store()
        data["runs"][run["id"]] = run
        agent_state.write_store(data)
        project_service.on_run_saved(run)

        # on_run_saved returns None, so compare the persisted counts with
        # counts recomputed from the saved project, memory and draft. Reading
        # through get_project() would self-heal stale counts and hide the bug.
        stored = project_state.read_projects()["projects"][project["id"]]
        expected = project_state.project_counts(
            stored,
            project_state.read_memory(project["id"]),
            draft_service.get_draft(project["id"]),
        )
        self.assertEqual(stored["counts"], expected)
        self.assertEqual(stored["counts"]["runs"], 1)
        self.assertEqual(stored["counts"]["evidence"], 1)
        self.assertEqual(stored["counts"]["open_questions"], 1)
        self.assertEqual(stored["counts"]["draft_sections"], 2)
        self.assertEqual(stored["counts"]["draft_blocks"], 2)

    def test_run_store_active_run_blocks_new_run_without_pointer(self) -> None:
        project = self._project()
        run = self._run("running")
        run["project_id"] = project["id"]
        data = agent_state.read_store()
        data["runs"][run["id"]] = run
        agent_state.write_store(data)
        self.assertIsNone(
            project_state.read_projects()["projects"][project["id"]]["active_run_id"]
        )
        with self.assertRaises(ValueError):
            project_service.ensure_can_start_run(project["id"])
        project_service.ensure_can_start_run(project["id"], exclude_run_id=run["id"])
        with self.assertRaises(ValueError):
            project_service.bind_run(project["id"], "run-other")

    def test_reconcile_repopulates_memory_and_clears_stale_active_pointer(self) -> None:
        project = self._project()
        older = self._run("completed")
        newer = self._run("completed")
        older["project_id"] = project["id"]
        newer["project_id"] = project["id"]
        older["updated_at"] = "2026-01-01 00:00:00"
        newer["updated_at"] = "2026-01-02 00:00:00"
        older["evidence"] = [{
            "paper_id": "P1", "title": "Older", "section": "Abstract",
            "page": 1, "quote": "Older evidence quote", "verified": True,
        }]
        newer["evidence"] = [{
            "paper_id": "P2", "title": "Newer", "section": "Abstract",
            "page": 1, "quote": "Newer evidence quote", "verified": True,
        }]
        data = agent_state.read_store()
        data["runs"][older["id"]] = older
        data["runs"][newer["id"]] = newer
        agent_state.write_store(data)
        stored = project_state.read_projects()
        stored["projects"][project["id"]]["run_ids"] = [older["id"], newer["id"]]
        stored["projects"][project["id"]]["active_run_id"] = older["id"]
        project_state.write_projects(stored)

        # Simulate a failed memory write plus a stale active pointer. `limit`
        # is only an API-compatibility safety bound now, so it must not leave
        # the older run unreconciled behind the newest one.
        project_state.write_memory(project["id"], project_state.empty_memory(project["id"]))
        reconciled = project_service.reconcile_project(project["id"], limit=1)
        self.assertIsNone(reconciled["active_run_id"])
        self.assertEqual(reconciled["counts"]["evidence"], 2)
        self.assertEqual(len(project_state.read_memory(project["id"])["evidence"]), 2)
        self.assertIsNone(
            project_state.read_projects()["projects"][project["id"]]["active_run_id"]
        )

        project_state.write_memory(project["id"], project_state.empty_memory(project["id"]))
        detail = project_service.get_project(project["id"])
        self.assertIsNone(detail["active_run_id"])
        self.assertEqual(detail["counts"]["evidence"], 2)
        self.assertEqual(len(project_state.read_memory(project["id"])["evidence"]), 2)

    def test_reconcile_project_restores_missing_active_pointer(self) -> None:
        project = self._project()
        run = self._run("awaiting_approval")
        run["project_id"] = project["id"]
        data = agent_state.read_store()
        data["runs"][run["id"]] = run
        agent_state.write_store(data)
        stored = project_state.read_projects()
        stored["projects"][project["id"]]["run_ids"] = [run["id"]]
        stored["projects"][project["id"]]["active_run_id"] = None
        project_state.write_projects(stored)
        detail = project_service.get_project(project["id"])
        self.assertEqual(detail["active_run_id"], run["id"])

    def test_concurrent_create_and_update_keeps_all_projects(self) -> None:
        barrier = threading.Barrier(2)
        created: list[str] = []
        errors: list[BaseException] = []
        guard = threading.Lock()

        def worker(prefix: str) -> None:
            try:
                barrier.wait(timeout=5)
                for index in range(3):
                    project = project_service.create_project(
                        f"{prefix}项目{index}", "并发创建项目？"
                    )
                    project_service.update_project(project["id"], {"tags": [prefix]})
                    with guard:
                        created.append(project["id"])
            except BaseException as exc:  # pragma: no cover - reported below
                with guard:
                    errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(prefix,)) for prefix in ("甲", "乙")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        stored = project_state.read_projects()["projects"]
        self.assertEqual(set(created), set(stored))
        self.assertEqual(len(stored), 6)

    def test_concurrent_project_reads_survive_atomic_replace(self) -> None:
        """Readers must acquire the store lock so they never hold projects.json
        or memory.json open while another thread's temp-file ``os.replace``
        runs (a Windows sharing violation)."""
        seed = self._project()
        iterations = 4
        barrier = threading.Barrier(4)
        created: list[str] = []
        errors: list[BaseException] = []
        guard = threading.Lock()

        def record(error: BaseException) -> None:
            with guard:
                errors.append(error)

        def reader() -> None:
            try:
                barrier.wait(timeout=5)
                for _ in range(iterations * 2):
                    project_service.list_projects()
                    project_service.get_project(seed["id"])
                    project_state.read_memory(seed["id"])
            except BaseException as exc:  # pragma: no cover - reported below
                record(exc)

        def writer(prefix: str, decision: bool) -> None:
            try:
                barrier.wait(timeout=5)
                for index in range(iterations):
                    project = project_service.create_project(
                        f"{prefix}并发项目{index}", "并发读写项目？"
                    )
                    if decision:
                        project_service.add_decision(
                            project["id"], "topic", f"{prefix}-{index}"
                        )
                    else:
                        project_service.update_project(project["id"], {"tags": [prefix]})
                    project_service.add_decision(
                        seed["id"], "note", f"{prefix}-{index}"
                    )
                    with guard:
                        created.append(project["id"])
            except BaseException as exc:  # pragma: no cover - reported below
                record(exc)

        threads = [
            threading.Thread(target=reader),
            threading.Thread(target=reader),
            threading.Thread(target=writer, args=("甲", True)),
            threading.Thread(target=writer, args=("乙", False)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        stored = project_state.read_projects()["projects"]
        self.assertEqual(set(created) | {seed["id"]}, set(stored))
        for project_id in created:
            detail = project_service.get_project(project_id)
            self.assertIsNotNone(detail)
            self.assertEqual(detail["id"], project_id)
        self.assertEqual(
            len(project_state.read_memory(seed["id"])["decisions"]),
            iterations * 2,
        )

    def test_attach_run_rejected_for_archived_project(self) -> None:
        project = self._project()
        run = self._run("completed")
        project_service.archive_project(project["id"])
        with self.assertRaises(ValueError):
            project_service.attach_run(project["id"], run["id"])
        self.assertIsNone(agent_state.read_store()["runs"][run["id"]].get("project_id"))
        self.assertEqual(
            project_state.read_projects()["projects"][project["id"]]["run_ids"], []
        )

    def test_seed_for_run_clamps_max_papers_to_one(self) -> None:
        project = self._project()
        memory = project_state.read_memory(project["id"])
        memory["evidence"] = [
            {"id": "pev-1", "paper_id": "P1", "quote": "first", "verified": True},
            {"id": "pev-2", "paper_id": "P2", "quote": "second", "verified": True},
        ]
        memory["papers"] = {
            "P1": {"id": "P1", "title": "First"},
            "P2": {"id": "P2", "title": "Second"},
        }
        project_state.write_memory(project["id"], memory)
        seed = project_service.seed_for_run(project["id"], 0)
        self.assertEqual(len(seed["papers"]), 1)

    def test_list_projects_limit_zero_returns_empty(self) -> None:
        self._project()
        self.assertEqual(project_service.list_projects(limit=0), [])
        self.assertEqual(len(project_service.list_projects(limit=1)), 1)

    def test_repeated_get_project_keeps_open_question_stable(self) -> None:
        project = self._project()
        run = self._run("completed")
        run["project_id"] = project["id"]
        run["open_questions"] = [{
            "id": "q-001",
            "question": "校准开销是否抵消收益？",
            "status": "open",
            "priority": "high",
            "kind": "evidence",
        }]
        data = agent_state.read_store()
        data["runs"][run["id"]] = run
        agent_state.write_store(data)
        project_service.on_run_saved(run)

        memory = project_state.read_memory(project["id"])
        self.assertEqual(memory["reconciled_runs"], [run["id"]])
        self.assertEqual(memory["open_questions"][0]["recurrences"], 1)
        self.assertEqual(memory["open_questions"][0]["history"], [])

        for _ in range(4):
            detail = project_service.get_project(project["id"])
            self.assertEqual(detail["counts"]["open_questions"], 1)

        memory = project_state.read_memory(project["id"])
        self.assertEqual(memory["reconciled_runs"], [run["id"]])
        self.assertEqual(memory["open_questions"][0]["recurrences"], 1)
        self.assertEqual(memory["open_questions"][0]["history"], [])

    def test_reconcile_replays_run_missing_from_watermark_once(self) -> None:
        project = self._project()
        opener = self._run("completed")
        resolver = self._run("completed")
        question = "校准开销是否抵消收益？"
        opener["project_id"] = project["id"]
        resolver["project_id"] = project["id"]
        opener["updated_at"] = "2026-01-01 00:00:00"
        resolver["updated_at"] = "2026-01-02 00:00:00"
        opener["open_questions"] = [{
            "id": "q-001", "question": question, "status": "open",
            "priority": "high", "kind": "evidence",
        }]
        resolver["open_questions"] = [{
            "id": "q-001", "question": question, "status": "resolved",
            "priority": "high", "kind": "evidence", "resolution": "已补证据",
        }]
        data = agent_state.read_store()
        data["runs"][opener["id"]] = opener
        data["runs"][resolver["id"]] = resolver
        agent_state.write_store(data)
        stored = project_state.read_projects()
        stored["projects"][project["id"]]["run_ids"] = [opener["id"], resolver["id"]]
        project_state.write_projects(stored)

        # Only the opener run was promoted; the newer run's write was lost, so
        # its id is absent from the watermark and must be replayed exactly once.
        project_service.on_run_saved(opener)
        memory = project_state.read_memory(project["id"])
        self.assertEqual(memory["reconciled_runs"], [opener["id"]])
        self.assertEqual(memory["open_questions"][0]["status"], "open")
        self.assertEqual(memory["open_questions"][0]["recurrences"], 1)
        self.assertEqual(memory["open_questions"][0]["history"], [])

        for _ in range(4):
            project_service.get_project(project["id"])
            memory = project_state.read_memory(project["id"])
            self.assertEqual(memory["open_questions"][0]["status"], "resolved")
            self.assertEqual(memory["open_questions"][0]["recurrences"], 1)
            self.assertEqual(len(memory["open_questions"][0]["history"]), 1)
            self.assertEqual(
                memory["reconciled_runs"], [opener["id"], resolver["id"]]
            )

    def test_reconcile_drains_whole_backlog_and_keeps_latest_status(self) -> None:
        project = self._project()
        question = "校准开销是否抵消收益？"
        statuses = ["open", "open", "open", "open", "open", "resolved"]
        runs = []
        data = agent_state.read_store()
        for index, status in enumerate(statuses):
            run = agent_state.create_run_state(
                "目标", "demo", "auto", agent_state.DEFAULT_BUDGET
            )
            run["status"] = "completed"
            run["project_id"] = project["id"]
            run["updated_at"] = f"2026-01-0{index + 1} 00:00:00"
            run["open_questions"] = [{
                "id": "q-001",
                "question": question,
                "status": status,
                "priority": "high",
                "kind": "evidence",
                "resolution": "已补证据" if status == "resolved" else "",
            }]
            data["runs"][run["id"]] = run
            runs.append(run)
        agent_state.write_store(data)
        stored = project_state.read_projects()
        stored["projects"][project["id"]]["run_ids"] = [run["id"] for run in runs]
        project_state.write_projects(stored)
        expected_watermark = [run["id"] for run in runs]

        # Six unreconciled runs exceed the default limit of 5. The oldest
        # opener must be replayed before the newest resolver in the same pass;
        # deferring it would later flip the status back to open.
        detail = project_service.get_project(project["id"])
        memory = project_state.read_memory(project["id"])
        self.assertEqual(memory["open_questions"][0]["status"], "resolved")
        self.assertEqual(detail["counts"]["open_questions"], 0)
        self.assertEqual(memory["reconciled_runs"], expected_watermark)

        for _ in range(4):
            detail = project_service.get_project(project["id"])
            memory = project_state.read_memory(project["id"])
            self.assertEqual(memory["open_questions"][0]["status"], "resolved")
            self.assertEqual(memory["open_questions"][0]["recurrences"], 1)
            self.assertEqual(len(memory["open_questions"][0]["history"]), 1)
            self.assertEqual(detail["counts"]["open_questions"], 0)
            self.assertEqual(memory["reconciled_runs"], expected_watermark)

    def test_reconciled_runs_watermark_never_evicts(self) -> None:
        project = self._project()
        run = self._run("completed")
        run["project_id"] = project["id"]
        run["open_questions"] = [{
            "id": "q-001",
            "question": "校准开销是否抵消收益？",
            "status": "open",
            "priority": "high",
            "kind": "evidence",
        }]
        data = agent_state.read_store()
        data["runs"][run["id"]] = run
        agent_state.write_store(data)
        project_service.on_run_saved(run)

        # A long-lived project can accumulate more reconciled ids than its
        # current run cap (detached runs stay in the watermark). The real run
        # sits first, so any FIFO trim would evict it and force a replay.
        older_ids = [f"run-old-{index:03d}" for index in range(104)]
        memory = project_state.read_memory(project["id"])
        memory["reconciled_runs"] = [run["id"], *older_ids]
        project_state.write_memory(project["id"], memory)
        expected = [run["id"], *older_ids]

        for _ in range(4):
            detail = project_service.get_project(project["id"])
            memory = project_state.read_memory(project["id"])
            self.assertEqual(memory["reconciled_runs"], expected)
            self.assertEqual(memory["open_questions"][0]["recurrences"], 1)
            self.assertEqual(memory["open_questions"][0]["history"], [])
            self.assertEqual(detail["counts"]["open_questions"], 1)

        # Promoting a new run appends without evicting any older id.
        newer = self._run("completed")
        project_state.promote_run(project["id"], newer)
        self.assertEqual(
            project_state.read_memory(project["id"])["reconciled_runs"],
            expected + [newer["id"]],
        )

        # Duplicates collapse in first-seen order.
        memory = project_state.read_memory(project["id"])
        memory["reconciled_runs"] = [
            expected[0], expected[1], expected[0], *expected[2:]
        ]
        project_state.write_memory(project["id"], memory)
        self.assertEqual(
            project_state.read_memory(project["id"])["reconciled_runs"], expected
        )
