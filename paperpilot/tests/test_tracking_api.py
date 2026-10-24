from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import project_service


class TrackingApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            "PAPERPILOT_DATA_DIR": str(root / "data"),
            "PAPERPILOT_PROJECT_FILE": str(root / "projects.json"),
            "PAPERPILOT_PROJECT_DIR": str(root / "projects"),
            "PAPERPILOT_AGENT_FILE": str(root / "runs.json"),
            "PAPERPILOT_AGENT_DIR": str(root / "runs"),
            "PAPERPILOT_INDEX_FILE": str(root / "index.json"),
            "PAPERPILOT_SUBSCRIPTIONS_FILE": str(root / "subscriptions.json"),
        })
        self.env.start()
        self.client = TestClient(app)
        self.project = project_service.create_project("订阅项目", "问题？")

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _create(self, **overrides):
        payload = {"label": "量化跟踪", "query": "quantization", "source": "demo",
                   "interval_hours": 168, "limit": 5, "project_id": self.project["id"]}
        payload.update(overrides)
        return self.client.post("/api/subscriptions", json=payload)

    def test_create_list_patch_delete(self) -> None:
        created = self._create()
        self.assertEqual(created.status_code, 200, created.text)
        item = created.json()["subscription"]
        self.assertTrue(item["id"].startswith("sub-"))

        listed = self.client.get("/api/subscriptions").json()
        self.assertEqual(len(listed["items"]), 1)
        self.assertIn("scheduler", listed)
        self.assertIn("index", listed)

        patched = self.client.patch(f"/api/subscriptions/{item['id']}", json={"enabled": False})
        self.assertEqual(patched.status_code, 200)
        self.assertFalse(patched.json()["subscription"]["enabled"])

        deleted = self.client.delete(f"/api/subscriptions/{item['id']}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get("/api/subscriptions").json()["items"], [])

    def test_create_validates_input(self) -> None:
        self.assertEqual(self._create(query="").status_code, 422)
        self.assertEqual(self._create(source="pubmed").status_code, 422)
        self.assertEqual(self._create(interval_hours=999).status_code, 422)

    def test_unknown_subscription_returns_404(self) -> None:
        self.assertEqual(self.client.patch("/api/subscriptions/sub-missing", json={"enabled": False}).status_code, 404)
        self.assertEqual(self.client.delete("/api/subscriptions/sub-missing").status_code, 404)
        self.assertEqual(self.client.post("/api/subscriptions/sub-missing/run").status_code, 404)

    def test_run_demo_subscription_updates_cursor_and_index(self) -> None:
        item = self._create().json()["subscription"]
        run = self.client.post(f"/api/subscriptions/{item['id']}/run")
        self.assertEqual(run.status_code, 200, run.text)
        body = run.json()
        self.assertGreaterEqual(body["result"]["new"], 1)
        self.assertIn("conflict", body["result"])
        self.assertEqual(body["subscription"]["seen_fingerprints"] != [], True)
        self.assertGreaterEqual(body["index"]["total"], 1)

        # A second run does not re-report the same papers.
        again = self.client.post(f"/api/subscriptions/{item['id']}/run").json()
        self.assertEqual(again["result"]["new"], 0)

        status = self.client.get("/api/index/status").json()
        self.assertGreaterEqual(status["index"]["total"], 1)
        self.assertIn("running", status["scheduler"])

    def test_index_sync_from_project(self) -> None:
        response = self.client.post("/api/index/sync", json={"project_id": self.project["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("index", response.json())

    def test_meta_lists_tracking_features(self) -> None:
        features = set(self.client.get("/api/meta").json()["features"])
        for name in ("subscriptions", "incremental_index"):
            self.assertIn(name, features)


if __name__ == "__main__":
    unittest.main()
