from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


class AgentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    @patch("app.services.agent_runs.list_runs", return_value=[{"id": "run-1", "status": "awaiting_approval"}])
    @patch("app.services.agent_runs.create_run")
    def test_create_with_mode_and_budget(self, create_run, list_runs) -> None:
        create_run.return_value = {
            "id": "run-1",
            "mode": "checkpoint",
            "status": "queued",
            "steps": [],
            "plan": {"tool": "search"},
        }
        response = self.client.post(
            "/api/agent/runs",
            json={
                "goal": "低功耗 ADC",
                "source": "demo",
                "mode": "checkpoint",
                "max_actions": 10,
                "max_papers": 4,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run"]["id"], "run-1")
        self.assertEqual(create_run.call_args.kwargs["mode"], "checkpoint")
        self.assertEqual(create_run.call_args.kwargs["max_actions"], 10)
        self.assertEqual(create_run.call_args.kwargs["max_papers"], 4)
        response = self.client.get("/api/agent/runs")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["runs"][0]["status"], "awaiting_approval")

    @patch("app.services.agent_runs.get_run", return_value={"id": "run-1", "status": "completed"})
    def test_get_run(self, get_run) -> None:
        response = self.client.get("/api/agent/runs/run-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run"]["status"], "completed")
        get_run.assert_called_once_with("run-1")

    @patch("app.services.agent_runs.act")
    def test_action_accepts_papers_topic_and_constraint(self, act) -> None:
        act.return_value = {"id": "run-1", "status": "queued"}
        response = self.client.post(
            "/api/agent/runs/run-1/action",
            json={
                "action": "approve",
                "paper_ids": ["P1", "P2"],
                "topic_id": "Q1",
                "constraint": "只考虑 28nm",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run"]["status"], "queued")
        self.assertEqual(act.call_args.kwargs["paper_ids"], ["P1", "P2"])
        self.assertEqual(act.call_args.kwargs["topic_id"], "Q1")
        self.assertEqual(act.call_args.kwargs["constraint"], "只考虑 28nm")

        act.side_effect = KeyError("run-1")
        response = self.client.post("/api/agent/runs/run-1/action", json={"action": "cancel"})
        self.assertEqual(response.status_code, 404)

        act.side_effect = ValueError("当前任务不在等待确认状态。")
        response = self.client.post("/api/agent/runs/run-1/action", json={"action": "approve"})
        self.assertEqual(response.status_code, 400)

    @patch("app.services.agent_runs.report_markdown", return_value="# Report\n")
    def test_report_download(self, report) -> None:
        response = self.client.get("/api/agent/runs/run-1/report")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/markdown", response.headers["content-type"])
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual(response.text, "# Report\n")

    @patch("app.services.agent_runs.report_markdown", return_value=None)
    def test_report_missing(self, report) -> None:
        response = self.client.get("/api/agent/runs/run-1/report")
        self.assertEqual(response.status_code, 404)

    @patch("app.services.agent_runs.remove_run", return_value=True)
    def test_delete(self, remove) -> None:
        response = self.client.delete("/api/agent/runs/run-1")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_validation(self) -> None:
        self.assertEqual(self.client.post("/api/agent/runs", json={"goal": "x"}).status_code, 422)
        self.assertEqual(
            self.client.post("/api/agent/runs", json={"goal": "目标", "mode": "nope"}).status_code,
            422,
        )

    def test_meta_exposes_adaptive_agent(self) -> None:
        response = self.client.get("/api/meta")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("agent_adaptive", data["features"])
        self.assertIn("agent", data)
        self.assertTrue(data["agent"]["steps"])


if __name__ == "__main__":
    unittest.main()
