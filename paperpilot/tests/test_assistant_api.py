from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import draft_state, project_state


class AssistantAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(
            os.environ,
            {
                "PAPERPILOT_PROJECT_FILE": str(Path(self.tmp.name) / "projects.json"),
                "PAPERPILOT_PROJECT_DIR": str(Path(self.tmp.name) / "projects"),
                "PAPERPILOT_AGENT_FILE": str(Path(self.tmp.name) / "runs.json"),
                "PAPERPILOT_AGENT_DIR": str(Path(self.tmp.name) / "runs"),
                "PAPERPILOT_DATA_DIR": str(Path(self.tmp.name) / "data"),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = TestClient(app)
        with patch.object(config, "LLM_ENABLED", False):
            project = self.client.post(
                "/api/projects",
                json={"title": "API 助手项目", "question": "如何验证助手接口？"},
            ).json()["project"]
        self.project_id = project["id"]
        memory = project_state.read_memory(self.project_id)
        memory["evidence"] = [
            {
                "id": "pev-1",
                "paper_id": "P1",
                "title": "Paper One",
                "section": "Results",
                "page": 2,
                "quote": "The prototype consumes 4.2 fJ per conversion.",
                "claim": "原型每次转换消耗 4.2 fJ",
                "verified": True,
            }
        ]
        project_state.write_memory(self.project_id, memory)
        draft = draft_state.empty_draft(self.project_id)
        draft["sections"] = [
            {
                "id": "sec-001",
                "title": "结果",
                "blocks": [
                    {
                        "id": "blk-001",
                        "type": "paragraph",
                        "text": "该原型每次转换消耗 4.2 fJ。",
                        "evidence_ids": ["pev-1"],
                    }
                ],
            }
        ]
        draft_state.write_draft(self.project_id, draft)

    def _base(self) -> str:
        return f"/api/projects/{self.project_id}"

    def test_get_empty_state(self) -> None:
        response = self.client.get(self._base() + "/assistant")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["messages"], [])
        self.assertEqual(data["mode"], "offline")
        self.assertIn("limits", data)

    def test_post_message_and_export(self) -> None:
        with patch.object(config, "LLM_ENABLED", False):
            response = self.client.post(
                self._base() + "/assistant",
                json={"message": "有哪些证据支持 4.2 fJ？"},
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["messages"]), 2)
        self.assertEqual(data["reply"]["role"], "assistant")
        self.assertTrue(data["reply"]["citations"])
        export = self.client.get(self._base() + "/assistant/export")
        self.assertEqual(export.status_code, 200)
        self.assertIn("API 助手项目", export.text)
        self.assertIn("attachment", export.headers["content-disposition"])

    def test_clear_conversation(self) -> None:
        with patch.object(config, "LLM_ENABLED", False):
            self.client.post(self._base() + "/assistant", json={"message": "项目进展如何？"})
        response = self.client.delete(self._base() + "/assistant")
        self.assertEqual(response.status_code, 200)
        data = self.client.get(self._base() + "/assistant").json()
        self.assertEqual(data["messages"], [])

    def test_unknown_project_returns_404(self) -> None:
        response = self.client.get("/api/projects/prj-missing/assistant")
        self.assertEqual(response.status_code, 404)
        response = self.client.post(
            "/api/projects/prj-missing/assistant", json={"message": "你好"}
        )
        self.assertEqual(response.status_code, 404)

    def test_invalid_message_returns_422(self) -> None:
        response = self.client.post(self._base() + "/assistant", json={"message": ""})
        self.assertEqual(response.status_code, 422)
        response = self.client.post(
            self._base() + "/assistant", json={"message": "问" * 1001}
        )
        self.assertEqual(response.status_code, 422)

    def test_semantic_audit_endpoint_does_not_mutate_draft(self) -> None:
        before = draft_state.read_draft(self.project_id)
        with patch.object(config, "LLM_ENABLED", False):
            response = self.client.post(
                self._base() + "/draft/semantic-audit",
                json={"limit": 20, "use_llm": True},
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["mode"], "rule")
        self.assertEqual(data["checked"], 1)
        self.assertTrue(data["items"])
        after = draft_state.read_draft(self.project_id)
        self.assertEqual(before["revision"], after["revision"])
        self.assertEqual(
            before["sections"][0]["blocks"][0]["text"],
            after["sections"][0]["blocks"][0]["text"],
        )


if __name__ == "__main__":
    unittest.main()
