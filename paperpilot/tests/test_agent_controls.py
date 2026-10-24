from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import (
    agent_actions,
    agent_controls,
    agent_policy,
    agent_runs,
    agent_state,
    project_service,
    project_state,
)


def _fake_execute(run: dict, plan: dict, action_id: str = "") -> dict:
    if plan["tool"] == "search":
        agent_actions.save_papers(run, [
            {"id": "P1", "title": "Paper One", "authors": ["A"], "year": 2024, "venue": "V"},
            {"id": "P2", "title": "Paper Two", "authors": ["B"], "year": 2024, "venue": "V"},
        ])
        return {"selected": ["P1", "P2"], "candidates": [{"id": "P1"}, {"id": "P2"}], "new_ids": ["P1", "P2"]}
    if plan["tool"] == "read":
        return {"paper": {"id": plan["input"].get("paper_id", "P1")}, "evidence_added": 0}
    if plan["tool"] == "write":
        return {"section_id": plan["input"].get("section_id") or "sec-001",
                "blocks_added": 1, "evidence_ids": [], "mode": "offline",
                "draft_revision": 1, "evidence_added": 0}
    if plan["tool"] == "report":
        path = agent_state.run_dir(run["id"]) / "report.md"
        path.write_text("# Report\n", encoding="utf-8")
        return {"path": str(path), "chars": 9, "mode": "offline"}
    return {"paper_ids": list(run.get("paper_ids") or []), "evidence_added": 0}


class AgentControlTests(unittest.TestCase):
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
        self.client = TestClient(app)
        self.executor = patch.object(agent_actions, "execute_action", side_effect=_fake_execute)
        self.executor.start()

    def tearDown(self) -> None:
        self.executor.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_pause_queued_run_does_not_execute_action(self) -> None:
        run = agent_runs.create_run("目标", mode="auto", autostart=False)
        paused = agent_runs.control(run["id"], "pause")
        self.assertEqual(paused["status"], "paused")
        pauses = [item for item in paused["steering"] if item.get("kind") == "pause"]
        self.assertEqual(len(pauses), 1)
        self.assertEqual(pauses[0]["status"], "applied")
        result = agent_runs.execute_run(run["id"])
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["steps"], [])

    def test_queued_constraint_applies_before_first_action(self) -> None:
        seen = []
        def capture(run, plan, action_id=""):
            seen.append((run.get("constraint", ""), plan.get("input", {}).get("query", "")))
            return _fake_execute(run, plan, action_id)
        self.executor.stop()
        self.executor = patch.object(agent_actions, "execute_action", side_effect=capture)
        self.executor.start()
        run = agent_runs.create_run("目标", mode="auto", autostart=False)
        agent_runs.control(run["id"], "add_constraint", constraint="关注 28nm")
        agent_runs.execute_run(run["id"])
        self.assertEqual(seen[0][0], "关注 28nm")
        self.assertIn("关注 28nm", seen[0][1])

    def test_inject_question_and_set_mode(self) -> None:
        run = agent_runs.create_run("目标", mode="auto", autostart=False)
        updated = agent_runs.control(run["id"], "inject_question", question="缺口存在吗？", priority="high")
        self.assertEqual(updated["open_questions"][-1]["question"], "缺口存在吗？")
        updated = agent_runs.control(run["id"], "set_mode", mode="checkpoint")
        self.assertEqual(updated["mode"], "checkpoint")

    def test_replan_on_paused_run_keeps_pause(self) -> None:
        run = agent_runs.create_run("目标", mode="auto", autostart=False)
        agent_runs.control(run["id"], "pause")
        updated = agent_runs.control(run["id"], "replan")
        self.assertEqual(updated["status"], "paused")
        self.assertIsNotNone(updated["plan"])
        replans = [item for item in updated["steering"] if item.get("kind") == "replan"]
        self.assertEqual(len(replans), 1)
        self.assertEqual(replans[0]["status"], "applied")

    def test_apply_pending_marks_rejected(self) -> None:
        run = agent_state.create_run_state("目标", "demo", "auto", agent_state.DEFAULT_BUDGET)
        agent_controls.create_control(run, "add_papers", paper_ids=["UNKNOWN"])
        result = agent_controls.apply_pending(run)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(run["steering"][0]["status"], "rejected")

    def test_control_api_route(self) -> None:
        run = agent_runs.create_run("目标", mode="auto", autostart=False)
        response = self.client.post(
            f"/api/agent/runs/{run['id']}/control",
            json={"action": "pause"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run"]["status"], "paused")


class AgentControlBoundaryTests(unittest.TestCase):
    """Boundary, priority and error-mapping coverage for Task 5."""

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
        self.client = TestClient(app)
        self.executor = patch.object(agent_actions, "execute_action", side_effect=_fake_execute)
        self.executor.start()

    def tearDown(self) -> None:
        self.executor.stop()
        self.env.stop()
        self.tmp.cleanup()

    def _use_executor(self, side_effect) -> None:
        self.executor.stop()
        self.executor = patch.object(agent_actions, "execute_action", side_effect=side_effect)
        self.executor.start()

    def test_running_pause_applies_at_action_boundary(self) -> None:
        calls = []

        def pause_after_first(run, plan, action_id=""):
            calls.append(plan["tool"])
            artifact = _fake_execute(run, plan, action_id)
            if len(calls) == 1:
                agent_runs.control(run["id"], "pause")
            return artifact

        self._use_executor(pause_after_first)
        run = agent_runs.create_run("目标", mode="auto", autostart=False)
        result = agent_runs.execute_run(run["id"])
        self.assertEqual(result["status"], "paused")
        self.assertEqual(calls, ["search"])
        self.assertEqual(len(result["steps"]), 1)
        self.assertFalse(result["pause_requested"])
        event_types = [event["type"] for event in result["events"]]
        self.assertIn("pause_requested", event_types)
        self.assertIn("paused", event_types)
        pauses = [item for item in result["steering"] if item.get("kind") == "pause"]
        self.assertEqual(len(pauses), 1)
        self.assertEqual(pauses[0]["status"], "applied")
        again = agent_runs.execute_run(run["id"])
        self.assertEqual(again["status"], "paused")
        self.assertEqual(len(again["steps"]), 1)

    def test_boundary_pause_resume_replans_next_action(self) -> None:
        calls = []

        def pause_after_each_of_two(run, plan, action_id=""):
            calls.append(plan["tool"])
            artifact = _fake_execute(run, plan, action_id)
            if len(calls) <= 2:
                agent_runs.control(run["id"], "pause")
            return artifact

        self._use_executor(pause_after_each_of_two)
        run = agent_runs.create_run("目标", mode="auto", autostart=False)
        paused = agent_runs.execute_run(run["id"])
        self.assertEqual(paused["status"], "paused")
        self.assertEqual([step["tool"] for step in paused["steps"]], ["search"])
        self.assertIsNone(paused["plan"])
        with patch.object(agent_runs, "_enqueue"):
            resumed = agent_runs.control(run["id"], "resume")
        self.assertEqual(resumed["status"], "queued")
        result = agent_runs.execute_run(run["id"])
        self.assertEqual(result["status"], "paused")
        self.assertEqual(calls, ["search", "read"])
        self.assertEqual([step["tool"] for step in result["steps"]], ["search", "read"])
        self.assertEqual(
            len([step for step in result["steps"] if step["tool"] == "search"]), 1
        )

    def test_pause_before_first_action_clears_plan(self) -> None:
        def pause_after_first(run, plan, action_id=""):
            artifact = _fake_execute(run, plan, action_id)
            agent_runs.control(run["id"], "pause")
            return artifact

        self._use_executor(pause_after_first)
        run = agent_runs.create_run("目标", mode="auto", autostart=False)
        data = agent_state.read_store()
        agent_controls.create_control(data["runs"][run["id"]], "pause")
        agent_state.write_store(data)
        paused = agent_runs.execute_run(run["id"])
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["steps"], [])
        self.assertIsNone(paused["plan"])
        with patch.object(agent_runs, "_enqueue"):
            resumed = agent_runs.control(run["id"], "resume")
        self.assertEqual(resumed["status"], "queued")
        result = agent_runs.execute_run(run["id"])
        self.assertEqual(result["status"], "paused")
        self.assertEqual([step["tool"] for step in result["steps"]], ["search"])

    def test_pause_priority_leaves_other_controls_pending(self) -> None:
        run = agent_state.create_run_state("目标", "demo", "auto", agent_state.DEFAULT_BUDGET)
        agent_controls.create_control(run, "add_constraint", constraint="关注 28nm")
        agent_controls.create_control(run, "pause")
        result = agent_controls.apply_pending(run)
        self.assertTrue(result["paused"])
        self.assertEqual(result["applied"], 0)
        self.assertNotIn("constraint", run)
        self.assertEqual(run["steering"][0]["status"], "pending")
        self.assertEqual(run["steering"][1]["status"], "applied")

    def test_control_api_error_mapping(self) -> None:
        run = agent_runs.create_run("目标", mode="auto", autostart=False)
        response = self.client.post(
            f"/api/agent/runs/{run['id']}/control", json={"action": "pause"}
        )
        self.assertEqual(response.status_code, 200)
        # Pausing a paused run is an invalid state.
        response = self.client.post(
            f"/api/agent/runs/{run['id']}/control", json={"action": "pause"}
        )
        self.assertEqual(response.status_code, 409)
        with patch.object(agent_runs, "_enqueue"):
            response = self.client.post(
                f"/api/agent/runs/{run['id']}/control", json={"action": "resume"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run"]["status"], "queued")
        # Replanning a queued run is an invalid state.
        response = self.client.post(
            f"/api/agent/runs/{run['id']}/control", json={"action": "replan"}
        )
        self.assertEqual(response.status_code, 409)
        # Missing or invalid parameters map to 422.
        for payload in (
            {"action": "set_mode"},
            {"action": "add_constraint"},
            {"action": "add_papers", "paper_ids": []},
            {"action": "add_papers", "paper_ids": ["UNKNOWN"]},
            {"action": "inject_question"},
            {"action": "unknown"},
        ):
            response = self.client.post(
                f"/api/agent/runs/{run['id']}/control", json=payload
            )
            self.assertEqual(response.status_code, 422, payload)
        agent_runs.act(run["id"], "cancel", autostart=False)
        for payload in (
            {"action": "pause"},
            {"action": "set_mode", "mode": "auto"},
        ):
            response = self.client.post(
                f"/api/agent/runs/{run['id']}/control", json=payload
            )
            self.assertEqual(response.status_code, 409, payload)
        response = self.client.post(
            "/api/agent/runs/run-missing/control", json={"action": "pause"}
        )
        self.assertEqual(response.status_code, 404)

    def test_project_active_follows_pause_and_recovery_clears_it(self) -> None:
        project = project_service.create_project("项目", "问题？")
        run = agent_runs.create_run(
            "目标", mode="auto", project_id=project["id"], autostart=False
        )
        agent_runs.control(run["id"], "pause")
        stored = project_state.read_projects()["projects"][project["id"]]
        self.assertEqual(stored["active_run_id"], run["id"])
        with patch.object(agent_runs, "_enqueue"):
            resumed = agent_runs.control(run["id"], "resume")
        self.assertEqual(resumed["status"], "queued")
        stored = project_state.read_projects()["projects"][project["id"]]
        self.assertEqual(stored["active_run_id"], run["id"])
        # Simulate a process restart while the resumed run is executing.
        data = agent_state.read_store()
        data["runs"][run["id"]]["status"] = "running"
        agent_state.write_store(data)
        agent_runs._RECOVERED.clear()
        loaded = agent_runs.get_run(run["id"])
        self.assertEqual(loaded["status"], "interrupted")
        stored = project_state.read_projects()["projects"][project["id"]]
        self.assertIsNone(stored["active_run_id"])

    def test_resume_and_retry_reject_second_project_active_run(self) -> None:
        project = project_service.create_project("互斥项目", "互斥问题？")
        active = agent_runs.create_run(
            "活动目标", mode="auto", project_id=project["id"], autostart=False
        )
        failed = agent_state.create_run_state(
            "失败目标", "demo", "auto", agent_state.DEFAULT_BUDGET,
            project_id=project["id"],
        )
        failed["status"] = "failed"
        data = agent_state.read_store()
        data["runs"][failed["id"]] = failed
        agent_state.write_store(data)
        project_data = project_state.read_projects()
        project_data["projects"][project["id"]]["run_ids"].append(failed["id"])
        project_state.write_projects(project_data)
        agent_runs._RECOVERED.add(failed["id"])
        with self.assertRaises(ValueError) as ctx:
            agent_runs.act(failed["id"], "resume", autostart=False)
        self.assertIn("活动运行", str(ctx.exception))
        with self.assertRaises(ValueError):
            agent_runs.act(failed["id"], "retry", autostart=False)
        self.assertEqual(agent_runs.get_run(active["id"])["status"], "queued")

    def test_control_after_action_before_worker_save_is_not_lost(self) -> None:
        original_choose = agent_policy.choose_next
        injected = []

        def choose_with_pause(run):
            plan = original_choose(run)
            # Runs after the action boundary but before the worker's next save,
            # i.e. exactly the window where a stale in-memory copy used to
            # overwrite the control channel.
            if not injected and run.get("steps"):
                injected.append(True)
                agent_runs.control(run["id"], "pause")
            return plan

        with patch.object(agent_policy, "choose_next", side_effect=choose_with_pause):
            run = agent_runs.create_run("目标", mode="auto", autostart=False)
            result = agent_runs.execute_run(run["id"])
        self.assertTrue(injected)
        self.assertEqual(result["status"], "paused")
        self.assertEqual([step["tool"] for step in result["steps"]], ["search", "read"])
        pauses = [item for item in result["steering"] if item.get("kind") == "pause"]
        self.assertEqual(len(pauses), 1)
        self.assertEqual(pauses[0]["status"], "applied")
        self.assertFalse(result["pause_requested"])

    def test_stream_seq_strictly_increases_across_control_writes(self) -> None:
        run = agent_runs.create_run("目标", mode="auto", autostart=False)
        data = agent_state.read_store()
        data["runs"][run["id"]]["status"] = "running"
        worker_copy = copy.deepcopy(data["runs"][run["id"]])
        agent_state.write_store(data)
        first = agent_runs.control(run["id"], "add_constraint", constraint="约束甲")
        second = agent_runs.control(run["id"], "add_constraint", constraint="约束乙")
        self.assertLess(first["stream_seq"], second["stream_seq"])
        # The worker saves its stale copy after both control writes.
        agent_runs._save_run(worker_copy)
        stored = agent_runs.get_run(run["id"])
        self.assertGreater(stored["stream_seq"], second["stream_seq"])
        self.assertEqual(
            [(item["kind"], item["status"]) for item in stored["steering"]],
            [("add_constraint", "pending"), ("add_constraint", "pending")],
        )
        self.assertFalse(stored["pause_requested"])
        self.assertFalse(stored["replan_requested"])

    def test_control_rejects_failed_and_interrupted_runs(self) -> None:
        run = agent_runs.create_run("目标", mode="auto", autostart=False)
        for status in ("failed", "interrupted"):
            data = agent_state.read_store()
            data["runs"][run["id"]]["status"] = status
            agent_state.write_store(data)
            with self.assertRaises(ValueError) as ctx:
                agent_runs.control(run["id"], "set_mode", mode="auto")
            self.assertIn("任务已经结束", str(ctx.exception))
            response = self.client.post(
                f"/api/agent/runs/{run['id']}/control",
                json={"action": "set_mode", "mode": "auto"},
            )
            self.assertEqual(response.status_code, 409)
            response = self.client.post(
                f"/api/agent/runs/{run['id']}/control",
                json={"action": "add_constraint", "constraint": "有效约束"},
            )
            self.assertEqual(response.status_code, 409)

    def test_control_resume_rejects_second_active_project_run(self) -> None:
        project = project_service.create_project("控制互斥", "互斥问题？")
        active = agent_runs.create_run(
            "活动目标", mode="auto", project_id=project["id"], autostart=False
        )
        paused = agent_state.create_run_state(
            "暂停目标", "demo", "auto", agent_state.DEFAULT_BUDGET,
            project_id=project["id"],
        )
        paused["status"] = "paused"
        paused["plan"] = agent_policy.choose_next(paused)
        data = agent_state.read_store()
        data["runs"][paused["id"]] = paused
        agent_state.write_store(data)
        project_data = project_state.read_projects()
        project_data["projects"][project["id"]]["run_ids"].append(paused["id"])
        project_state.write_projects(project_data)
        with patch.object(agent_runs, "_enqueue"):
            with self.assertRaises(ValueError) as ctx:
                agent_runs.control(paused["id"], "resume")
            self.assertIn("活动运行", str(ctx.exception))
            response = self.client.post(
                f"/api/agent/runs/{paused['id']}/control", json={"action": "resume"}
            )
            self.assertEqual(response.status_code, 409)
        self.assertEqual(agent_runs.get_run(paused["id"])["status"], "paused")
        self.assertEqual(agent_runs.get_run(active["id"])["status"], "queued")

    def test_expansion_search_query_includes_constraint(self) -> None:
        run = agent_state.create_run_state("目标", "demo", "auto", agent_state.DEFAULT_BUDGET)
        run["constraint"] = "关注 28nm"
        run["steps"] = [{
            "id": "act-001",
            "tool": "search",
            "status": "done",
            "artifact": {"selected": []},
        }]
        plan = agent_policy.choose_next(run)
        self.assertEqual(plan["tool"], "search")
        self.assertIn("关注 28nm", plan["input"]["query"])


if __name__ == "__main__":
    unittest.main()
