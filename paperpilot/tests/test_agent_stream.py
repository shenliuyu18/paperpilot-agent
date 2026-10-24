from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import agent_state, agent_stream


class AgentStreamTests(unittest.TestCase):
    def test_compact_run_drops_artifacts_and_truncates(self) -> None:
        run = agent_state.create_run_state("目标", "demo", "auto", agent_state.DEFAULT_BUDGET)
        run["steps"] = [{
            "id": "act-001", "tool": "read", "title": "精读", "status": "done",
            "artifact": {"huge": "x" * 1000}, "observation": {"summary": "ok"},
        }]
        run["evidence"] = [{"id": f"ev-{i}", "quote": "x"} for i in range(5)]
        run["open_questions"] = [{"id": f"q-{i}", "question": "问题"} for i in range(15)]
        compact = agent_stream.compact_run(run)
        self.assertNotIn("artifact", compact["run"]["steps"][0])
        self.assertEqual(len(compact["run"]["latest_evidence"]), 3)
        self.assertEqual(len(compact["run"]["open_questions"]), 10)
        self.assertEqual(compact["run"]["evidence_count"], 5)

    def test_publish_fans_out_to_subscribers(self) -> None:
        q1 = agent_stream.subscribe("run-x")
        q2 = agent_stream.subscribe("run-x")
        agent_stream.publish("run-x", {"seq": 1, "run": {"id": "run-x"}})
        self.assertEqual(q1.get_nowait()["seq"], 1)
        self.assertEqual(q2.get_nowait()["seq"], 1)
        agent_stream.unsubscribe("run-x", q1)
        agent_stream.unsubscribe("run-x", q2)
        self.assertNotIn("run-x", agent_stream._SUBSCRIBERS)

    def test_event_stream_sends_terminal_snapshot_and_closes(self) -> None:
        run = agent_state.create_run_state("目标", "demo", "auto", agent_state.DEFAULT_BUDGET)
        run["status"] = "completed"
        run["stream_seq"] = 7
        events = list(agent_stream.event_stream(run["id"], lambda _: run, heartbeat=0.01))
        self.assertEqual(len(events), 1)
        self.assertIn("id: 7", events[0])
        payload = json.loads(events[0].split("data: ", 1)[1].strip())
        self.assertEqual(payload["run"]["status"], "completed")

    def test_slow_subscriber_drops_oldest(self) -> None:
        q = agent_stream.subscribe("run-y")
        for index in range(12):
            agent_stream.publish("run-y", {"seq": index, "run": {"id": "run-y"}})
        self.assertEqual(q.qsize(), 8)
        self.assertEqual(q.get_nowait()["seq"], 4)
        agent_stream.unsubscribe("run-y", q)


class AgentStreamApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_stream_returns_terminal_snapshot_with_sse_headers(self) -> None:
        run = agent_state.create_run_state("目标", "demo", "auto", agent_state.DEFAULT_BUDGET)
        run["status"] = "completed"
        run["stream_seq"] = 3
        with patch("app.services.agent_runs.get_run", return_value=run):
            response = self.client.get(f"/api/agent/runs/{run['id']}/stream")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.assertIn(f"id: {run['stream_seq']}", response.text)
        self.assertIn("event: snapshot", response.text)

    def test_stream_missing_run_returns_404(self) -> None:
        with patch("app.services.agent_runs.get_run", return_value=None):
            response = self.client.get("/api/agent/runs/run-missing/stream")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
