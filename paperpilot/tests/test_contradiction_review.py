from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import contradictions, draft_service, project_service, project_state


def _paper(pid: str, value: str) -> dict:
    return {
        "id": pid, "title": f"Paper {pid}", "year": "2026", "venue": "arXiv preprint",
        "metrics": {"ADC 能耗": value}, "results": [],
    }


class ContradictionReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            "PAPERPILOT_PROJECT_FILE": str(root / "projects.json"),
            "PAPERPILOT_PROJECT_DIR": str(root / "projects"),
            "PAPERPILOT_AGENT_FILE": str(root / "runs.json"),
            "PAPERPILOT_AGENT_DIR": str(root / "runs"),
        })
        self.env.start()
        self.client = TestClient(app)
        self.project = project_service.create_project("复核项目", "问题？")
        self.pid = self.project["id"]
        memory = project_state.read_memory(self.pid)
        memory["papers"] = {
            "P1": _paper("P1", "降低 27.1%"),
            "P2": _paper("P2", "提升 12.5%"),
        }
        project_state.write_memory(self.pid, memory)
        self.scanned = contradictions.scan(self.pid)
        self.assertEqual(len(self.scanned["items"]), 1)

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _review(self, payload: dict, **kwargs):
        with patch.object(contradictions.config, "LLM_ENABLED", True), \
             patch("app.services.llm_gateway.audited_chat_json",
                   return_value=(payload, {"model": "mock-model", "status": "ok"})):
            return contradictions.review(self.pid, use_llm=True, **kwargs)

    def test_offline_is_degraded_and_does_not_write(self) -> None:
        with patch.object(contradictions.config, "LLM_ENABLED", False):
            result = contradictions.review(self.pid, use_llm=True)
        self.assertTrue(result["degraded"])
        self.assertEqual(result["degrade_reason"], "no_key")
        self.assertEqual(contradictions.read(self.pid)["items"][0]["status"], "open")

    def test_disabled_never_calls_the_model(self) -> None:
        with patch("app.services.llm_gateway.audited_chat_json") as gateway:
            result = contradictions.review(self.pid, use_llm=False)
        self.assertEqual(result["degrade_reason"], "disabled")
        gateway.assert_not_called()

    def test_confirmed_updates_the_item(self) -> None:
        result = self._review({"verdict": "confirmed", "reason": "同一指标，方向相反。"})
        self.assertEqual(result["reviewed"], 1)
        self.assertEqual(result["confirmed"], 1)
        item = contradictions.read(self.pid)["items"][0]
        self.assertEqual(item["status"], "confirmed")
        self.assertEqual(item["review"]["reason"], "同一指标，方向相反。")
        self.assertEqual(item["paper_a"]["id"], "P1")

    def test_dismissed_is_excluded_from_the_draft_audit(self) -> None:
        self._review({"verdict": "dismissed", "reason": "测试条件不同，不构成冲突。"})
        audit = draft_service.audit_draft(self.pid)
        self.assertFalse([issue for issue in audit["issues"]
                          if issue.get("kind") == "contradiction"])

    def test_fabricated_quote_is_rejected(self) -> None:
        before = Path(self.tmp.name, "projects", self.pid, "contradictions.json").read_text(encoding="utf-8")
        result = self._review({
            "verdict": "confirmed", "reason": "看起来冲突。",
            "quote_a": "证据里根本没有这句话", "quote_b": "提升 12.5%",
        })
        self.assertEqual(result["reviewed"], 0)
        self.assertEqual(result["rejected_quote"], 1)
        after = Path(self.tmp.name, "projects", self.pid, "contradictions.json").read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_only_open_items_are_reviewed_once(self) -> None:
        self._review({"verdict": "uncertain", "reason": "需要看全文。"})
        second = self._review({"verdict": "confirmed", "reason": "再次判断。"})
        self.assertEqual(second["reviewed"], 0)
        self.assertEqual(second["items"], [])

    def test_route_contract(self) -> None:
        response = self.client.post(f"/api/projects/{self.pid}/contradictions/review",
                                    json={"use_llm": False})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["degraded"])
        missing = self.client.post("/api/projects/prj-missing/contradictions/review",
                                   json={"use_llm": False})
        self.assertEqual(missing.status_code, 404)
        invalid = self.client.post(f"/api/projects/{self.pid}/contradictions/review",
                                   json={"use_llm": False, "limit": 99})
        self.assertEqual(invalid.status_code, 422)


if __name__ == "__main__":
    unittest.main()
