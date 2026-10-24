from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import draft_audit, draft_service, project_service, project_state


def _draft(blocks: list[dict]) -> dict:
    return {
        "project_id": "prj-x",
        "revision": 1,
        "title": "草稿",
        "citation_style": "numeric",
        "sections": [{"id": "sec-001", "title": "引言", "blocks": blocks}],
        "citations": {},
        "versions": [],
    }


class DraftAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.memory = {
            "evidence": [{
                "id": "pev-1", "paper_id": "P1", "section": "Results", "page": 1,
                "quote": "The prototype consumes 4.2 fJ per conversion.",
                "verified": True,
            }],
            "papers": {"P1": {"id": "P1", "title": "Paper", "authors": ["A"], "year": 2024}},
        }

    def test_missing_and_unknown_evidence(self) -> None:
        result = draft_audit.audit_draft(_draft([
            {"id": "blk-1", "type": "paragraph", "text": "一段没有任何证据的长文字。" * 4},
            {"id": "blk-2", "type": "paragraph", "text": "引用不存在证据。", "evidence_ids": ["missing"]},
        ]), self.memory)
        kinds = {item["kind"] for item in result["issues"]}
        self.assertIn("missing_evidence", kinds)
        self.assertIn("unknown_evidence", kinds)
        self.assertEqual(result["counts"]["high"], 2)

    def test_missing_evidence_length_boundary(self) -> None:
        prefix = "这是一段没有绑定证据的段落，"
        text_40 = prefix + "文" * (40 - len(prefix))
        text_41 = prefix + "文" * (41 - len(prefix))
        self.assertEqual(len(text_40), 40)
        self.assertEqual(len(text_41), 41)

        at_limit = draft_audit.audit_draft(_draft([
            {"id": "blk-40", "type": "paragraph", "text": text_40},
        ]), self.memory)
        self.assertNotIn(
            "missing_evidence", {item["kind"] for item in at_limit["issues"]}
        )
        self.assertEqual(at_limit["status_map"]["blk-40"], "checked")

        over_limit = draft_audit.audit_draft(_draft([
            {"id": "blk-41", "type": "paragraph", "text": text_41},
        ]), self.memory)
        issues = {item["kind"]: item for item in over_limit["issues"]}
        self.assertIn("missing_evidence", issues)
        self.assertEqual(issues["missing_evidence"]["block_id"], "blk-41")
        self.assertEqual(issues["missing_evidence"]["severity"], "high")
        self.assertEqual(over_limit["counts"]["high"], 1)
        self.assertEqual(over_limit["status_map"]["blk-41"], "needs_evidence")

    def test_numeric_mismatch_and_checked(self) -> None:
        result = draft_audit.audit_draft(_draft([
            {"id": "blk-1", "type": "paragraph", "text": "原型功耗为 9.9 fJ。", "evidence_ids": ["pev-1"]},
            {"id": "blk-2", "type": "paragraph", "text": "原型功耗为 4.2 fJ。", "evidence_ids": ["pev-1"]},
        ]), self.memory)
        issues = {item["kind"]: item for item in result["issues"]}
        self.assertEqual(issues["numeric_mismatch"]["block_id"], "blk-1")
        self.assertEqual(result["status_map"]["blk-1"], "needs_evidence")
        self.assertEqual(result["status_map"]["blk-2"], "checked")

    def test_unknown_citation_and_empty_section(self) -> None:
        draft = _draft([{"id": "blk-1", "type": "paragraph", "text": "文字", "citation_keys": ["nope"]}])
        draft["sections"].append({"id": "sec-002", "title": "空节", "blocks": []})
        result = draft_audit.audit_draft(draft, self.memory)
        kinds = {item["kind"] for item in result["issues"]}
        self.assertIn("unknown_citation", kinds)
        self.assertIn("empty_section", kinds)

    def test_service_applies_statuses_and_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = patch.dict(os.environ, {
                "PAPERPILOT_PROJECT_FILE": str(root / "projects.json"),
                "PAPERPILOT_PROJECT_DIR": str(root / "projects"),
                "PAPERPILOT_AGENT_FILE": str(root / "runs.json"),
                "PAPERPILOT_AGENT_DIR": str(root / "runs"),
            })
            env.start()
            try:
                project = project_service.create_project("项目", "问题？")
                memory = project_state.read_memory(project["id"])
                memory["evidence"] = self.memory["evidence"]
                memory["papers"] = self.memory["papers"]
                project_state.write_memory(project["id"], memory)
                draft_service.save_draft(project["id"], {
                    "revision": 0,
                    "title": "草稿",
                    "sections": [{"id": "sec-001", "title": "引言", "blocks": [{
                        "id": "blk-001", "type": "paragraph", "text": "无证据内容。" * 8,
                    }]}],
                })
                result = draft_service.audit_draft(project["id"])
                self.assertEqual(result["counts"]["high"], 1)
                saved = draft_service.get_draft(project["id"])
                self.assertEqual(saved["sections"][0]["blocks"][0]["status"], "needs_evidence")
                decisions = project_state.read_memory(project["id"])["decisions"]
                self.assertTrue(any(item["kind"] == "audit" for item in decisions))
            finally:
                env.stop()
