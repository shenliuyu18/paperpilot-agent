from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import agent_runs, agent_state, project_service, project_state


class ProjectApiTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _create_project(self, title: str = "项目", question: str = "问题？") -> dict:
        response = self.client.post("/api/projects", json={"title": title, "question": question})
        self.assertEqual(response.status_code, 200)
        return response.json()["project"]

    def _seed_run(self, status: str = "completed") -> dict:
        run = agent_state.create_run_state("目标", "demo", "auto", agent_state.DEFAULT_BUDGET)
        run["status"] = status
        data = agent_state.read_store()
        data["runs"][run["id"]] = run
        agent_state.write_store(data)
        return run

    def test_project_crud_and_memory(self) -> None:
        response = self.client.post("/api/projects", json={
            "title": "低功耗 ADC",
            "question": "如何降低功耗？",
            "constraints": ["优先开放获取"],
            "tags": ["CIM"],
        })
        self.assertEqual(response.status_code, 200)
        project = response.json()["project"]
        project_id = project["id"]
        self.assertEqual(project["stage"], "exploring")

        listing = self.client.get("/api/projects").json()
        self.assertEqual(listing["projects"][0]["id"], project_id)
        detail = self.client.get(f"/api/projects/{project_id}").json()["project"]
        self.assertEqual(detail["brief"]["question"], "如何降低功耗？")

        patch_response = self.client.patch(f"/api/projects/{project_id}", json={"stage": "reading"})
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["project"]["stage"], "reading")

        memory = self.client.get(f"/api/projects/{project_id}/memory?kind=evidence").json()
        self.assertEqual(memory["items"], [])
        self.assertIn("total", memory)
        for kind in ("questions", "papers"):
            payload = self.client.get(f"/api/projects/{project_id}/memory?kind={kind}").json()
            self.assertEqual(payload["total"], 0)
        # 手动调整阶段会写入一条决策。
        decisions = self.client.get(f"/api/projects/{project_id}/memory?kind=decisions").json()
        self.assertGreaterEqual(decisions["total"], 1)

    def test_agent_run_accepts_project_and_seeds(self) -> None:
        project = self.client.post("/api/projects", json={
            "title": "低功耗 ADC",
            "question": "如何降低功耗？",
        }).json()["project"]
        response = self.client.post("/api/agent/runs", json={
            "goal": "继续研究",
            "mode": "step",
            "project_id": project["id"],
            "seed_memory": True,
        })
        self.assertEqual(response.status_code, 200)
        run = response.json()["run"]
        self.assertEqual(run["project_id"], project["id"])
        self.assertEqual(run["status"], "awaiting_approval")

    def test_archive_delete_and_errors(self) -> None:
        project = self.client.post("/api/projects", json={
            "title": "项目", "question": "问题？",
        }).json()["project"]
        archived = self.client.post(f"/api/projects/{project['id']}/archive")
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(archived.json()["project"]["status"], "archived")
        deleted = self.client.delete(f"/api/projects/{project['id']}?keep_runs=true")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get(f"/api/projects/{project['id']}").status_code, 404)
        self.assertEqual(self.client.get("/api/projects/unknown").status_code, 404)

    def test_meta_exposes_projects(self) -> None:
        self._create_project()
        data = self.client.get("/api/meta").json()
        self.assertIn("projects", data["features"])
        self.assertEqual(data["projects"]["count"], 1)
        self.assertEqual(data["projects"]["max"], project_state.MAX_PROJECTS)

    def test_validation_404_and_conflicts(self) -> None:
        self.assertEqual(
            self.client.post("/api/projects", json={"title": "x", "question": "问题？"}).status_code,
            422,
        )
        self.assertEqual(
            self.client.post("/api/projects", json={"title": "项目", "question": "?"}).status_code,
            422,
        )
        project = self._create_project()

        self.assertEqual(self.client.get("/api/projects/unknown/memory").status_code, 404)
        self.assertEqual(self.client.post("/api/projects/unknown/archive").status_code, 404)
        self.assertEqual(self.client.post("/api/projects/unknown/restore").status_code, 404)
        self.assertEqual(self.client.delete("/api/projects/unknown").status_code, 404)
        self.assertEqual(
            self.client.patch("/api/projects/unknown", json={"stage": "reading"}).status_code,
            404,
        )
        self.assertEqual(
            self.client.patch(f"/api/projects/{project['id']}", json={"stage": "nope"}).status_code,
            422,
        )
        self.assertEqual(
            self.client.get(f"/api/projects/{project['id']}/memory?kind=nope").status_code,
            422,
        )

        # 未归档项目不能直接删除。
        self.assertEqual(self.client.delete(f"/api/projects/{project['id']}").status_code, 409)

        self.client.post(f"/api/projects/{project['id']}/archive")
        self.assertEqual(self.client.get("/api/projects").json()["projects"], [])
        archived = self.client.get("/api/projects?status=archived").json()["projects"]
        self.assertEqual(archived[0]["id"], project["id"])
        restored = self.client.post(f"/api/projects/{project['id']}/restore")
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["project"]["status"], "active")
        self.assertEqual(self.client.get("/api/projects").json()["projects"][0]["id"], project["id"])

    def test_project_runs_attach_and_detach(self) -> None:
        project = self._create_project()
        self.assertEqual(self.client.get(f"/api/projects/{project['id']}/runs").json()["runs"], [])
        done = self._seed_run("completed")
        attached = self.client.post(f"/api/projects/{project['id']}/runs/{done['id']}/attach")
        self.assertEqual(attached.status_code, 200)
        self.assertIn(done["id"], attached.json()["project"]["run_ids"])
        runs = self.client.get(f"/api/projects/{project['id']}/runs").json()["runs"]
        self.assertEqual([item["id"] for item in runs], [done["id"]])

        running = self._seed_run("running")
        conflict = self.client.post(f"/api/projects/{project['id']}/runs/{running['id']}/attach")
        self.assertEqual(conflict.status_code, 409)
        missing = self.client.post(f"/api/projects/{project['id']}/runs/run-missing/attach")
        self.assertEqual(missing.status_code, 404)

        detached = self.client.post(f"/api/projects/{project['id']}/runs/{done['id']}/detach")
        self.assertEqual(detached.status_code, 200)
        self.assertEqual(detached.json()["project"]["run_ids"], [])
        self.assertEqual(self.client.get(f"/api/projects/{project['id']}/runs").json()["runs"], [])

        self.client.post(f"/api/projects/{project['id']}/archive")
        blocked = self.client.post(f"/api/projects/{project['id']}/runs/{done['id']}/attach")
        self.assertEqual(blocked.status_code, 409)

    def test_agent_run_project_error_mapping(self) -> None:
        missing = self.client.post("/api/agent/runs", json={
            "goal": "继续研究", "mode": "step", "project_id": "prj-missing",
        })
        self.assertEqual(missing.status_code, 404)

        project = self._create_project()
        active = self._seed_run("running")
        project_service.bind_run(project["id"], active["id"])
        conflict = self.client.post("/api/agent/runs", json={
            "goal": "继续研究", "mode": "step", "project_id": project["id"],
        })
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(self.client.post(f"/api/projects/{project['id']}/archive").status_code, 409)

        archived = self._create_project(title="已归档项目")
        self.client.post(f"/api/projects/{archived['id']}/archive")
        locked = self.client.post("/api/agent/runs", json={
            "goal": "继续研究", "mode": "step", "project_id": archived["id"],
        })
        self.assertEqual(locked.status_code, 409)


if __name__ == "__main__":
    unittest.main()
