from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import agent_compare, agent_state, project_service


class AgentCompareTests(unittest.TestCase):
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

    def _run(self, project_id: str, run_id: str, tools: list[str], quotes: list[str]) -> None:
        run = agent_state.create_run_state("目标", "demo", "auto", agent_state.DEFAULT_BUDGET)
        run["id"] = run_id
        run["project_id"] = project_id
        run["status"] = "completed"
        run["steps"] = [
            {"id": f"act-{i:03d}", "tool": tool, "status": "done", "artifact": {}}
            for i, tool in enumerate(tools, start=1)
        ]
        run["evidence"] = [
            {"paper_id": "P1", "section": "Abstract", "page": 1, "quote": quote, "verified": True}
            for quote in quotes
        ]
        data = agent_state.read_store()
        data["runs"][run_id] = run
        agent_state.write_store(data)

    def test_compare_reports_shared_and_unique_evidence(self) -> None:
        project = project_service.create_project("项目", "问题？")
        self._run(project["id"], "run-a", ["search", "read", "read"], ["shared", "only-a"])
        self._run(project["id"], "run-b", ["search", "read", "ask"], ["shared", "only-b"])
        result = agent_compare.compare_runs(project["id"], ["run-a", "run-b"])
        self.assertEqual(len(result["runs"]), 2)
        self.assertEqual(result["evidence"]["shared"], [
            agent_compare.evidence_key({"paper_id": "P1", "section": "Abstract", "page": 1, "quote": "shared"})
        ])
        self.assertEqual(result["tool_sequences"]["run-a"], ["search", "read", "read"])
        self.assertEqual(result["runs"][0]["tool_counts"]["read"], 2)

    def test_compare_rejects_foreign_or_too_many_runs(self) -> None:
        project = project_service.create_project("项目", "问题？")
        other = project_service.create_project("其他", "问题？")
        self._run(project["id"], "run-a", ["search"], [])
        self._run(other["id"], "run-b", ["search"], [])
        with self.assertRaises(ValueError):
            agent_compare.compare_runs(project["id"], ["run-a", "run-b"])
        with self.assertRaises(ValueError):
            agent_compare.compare_runs(project["id"], ["run-a"])


class AgentCompareApiTests(unittest.TestCase):
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
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _run(self, project_id: str, run_id: str, tools: list[str], quotes: list[str]) -> None:
        run = agent_state.create_run_state("目标", "demo", "auto", agent_state.DEFAULT_BUDGET)
        run["id"] = run_id
        run["project_id"] = project_id
        run["status"] = "completed"
        run["steps"] = [
            {"id": f"act-{i:03d}", "tool": tool, "status": "done", "artifact": {}}
            for i, tool in enumerate(tools, start=1)
        ]
        run["evidence"] = [
            {"paper_id": "P1", "section": "Abstract", "page": 1, "quote": quote, "verified": True}
            for quote in quotes
        ]
        data = agent_state.read_store()
        data["runs"][run_id] = run
        agent_state.write_store(data)

    def test_compare_route(self) -> None:
        project = project_service.create_project("项目", "问题？")
        self._run(project["id"], "run-a", ["search", "read"], ["shared", "only-a"])
        self._run(project["id"], "run-b", ["search", "ask"], ["shared", "only-b"])
        response = self.client.get(
            f"/api/projects/{project['id']}/compare", params={"run_ids": "run-a,run-b"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["runs"]), 2)
        self.assertEqual(body["tool_sequences"]["run-b"], ["search", "ask"])
        self.assertEqual(body["evidence"]["shared"], [
            agent_compare.evidence_key(
                {"paper_id": "P1", "section": "Abstract", "page": 1, "quote": "shared"}
            )
        ])

        too_few = self.client.get(
            f"/api/projects/{project['id']}/compare", params={"run_ids": "run-a"}
        )
        self.assertEqual(too_few.status_code, 422)
        missing = self.client.get(
            "/api/projects/unknown/compare", params={"run_ids": "run-a,run-b"}
        )
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
