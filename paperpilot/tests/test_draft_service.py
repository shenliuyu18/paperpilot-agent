from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import draft_service, draft_state, project_service, project_state


class DraftServiceTests(unittest.TestCase):
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
        self.project = project_service.create_project("项目", "问题？")
        memory = project_state.read_memory(self.project["id"])
        memory["evidence"] = [{
            "id": "pev-1", "paper_id": "P1", "title": "Paper One",
            "section": "Abstract", "page": 1, "quote": "Evidence quote",
            "verified": True, "kind": "text",
        }]
        memory["papers"] = {"P1": {
            "id": "P1", "title": "Paper One", "authors": ["Alice Author"],
            "year": 2024, "venue": "V", "doi": "", "url": "https://example.org",
        }}
        project_state.write_memory(self.project["id"], memory)

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _payload(self, revision: int = 0, evidence_ids=None, citation_keys=None) -> dict:
        return {
            "revision": revision,
            "title": "研究草稿",
            "citation_style": "numeric",
            "sections": [{
                "id": "sec-001",
                "title": "引言",
                "blocks": [{
                    "id": "blk-001",
                    "type": "paragraph",
                    "text": "存内计算降低数据搬运开销。",
                    "evidence_ids": evidence_ids if evidence_ids is not None else ["pev-1"],
                    "citation_keys": citation_keys or [],
                }],
            }],
        }

    def test_save_increments_revision_and_validates_evidence(self) -> None:
        draft = draft_service.save_draft(self.project["id"], self._payload())
        self.assertEqual(draft["revision"], 1)
        self.assertEqual(draft["sections"][0]["blocks"][0]["status"], "draft")
        with self.assertRaises(draft_service.DraftConflict):
            draft_service.save_draft(self.project["id"], self._payload(revision=0))
        with self.assertRaises(draft_service.DraftValidation):
            draft_service.save_draft(
                self.project["id"], self._payload(revision=1, evidence_ids=["missing"])
            )

    def test_add_citation_is_stable_and_revision_locked(self) -> None:
        draft = draft_service.save_draft(self.project["id"], self._payload())
        result = draft_service.add_citation(self.project["id"], "P1", draft["revision"])
        self.assertEqual(result["key"], "author2024")
        self.assertEqual(result["revision"], 2)
        again = draft_service.add_citation(self.project["id"], "P1", result["revision"])
        self.assertEqual(again["key"], "author2024")
        self.assertEqual(again["revision"], 2)
        with self.assertRaises(draft_service.DraftConflict):
            draft_service.add_citation(self.project["id"], "P1", 0)

    def test_versions_snapshot_and_restore(self) -> None:
        draft = draft_service.save_draft(self.project["id"], self._payload())
        version = draft_service.create_version(self.project["id"], "第一版")
        self.assertEqual(version["label"], "第一版")
        draft = draft_service.get_draft(self.project["id"])
        payload = self._payload(revision=draft["revision"])
        payload["title"] = "第二版"
        draft_service.save_draft(self.project["id"], payload)
        restored = draft_service.restore_version(self.project["id"], version["id"])
        self.assertEqual(restored["title"], "研究草稿")
        self.assertGreater(restored["revision"], version["revision"])

    def test_persisted_counts_match_returned_counts(self) -> None:
        payload = self._payload()
        payload["sections"] = [
            payload["sections"][0],
            {
                "id": "sec-002",
                "title": "方法",
                "blocks": [{
                    "id": "blk-002",
                    "type": "paragraph",
                    "text": "第二个章节的正文。",
                    "evidence_ids": [],
                    "citation_keys": [],
                }],
            },
        ]
        draft = draft_service.save_draft(self.project["id"], payload)
        expected = draft_service.summary(self.project["id"])
        self.assertEqual(expected["sections"], 2)
        self.assertEqual(expected["blocks"], 2)
        stored = project_state.read_projects()["projects"][self.project["id"]]
        self.assertEqual(stored["counts"]["draft_sections"], 2)
        self.assertEqual(stored["counts"]["draft_blocks"], 2)
        self.assertEqual(stored["counts"]["draft_sections"], expected["sections"])
        self.assertEqual(stored["counts"]["draft_blocks"], expected["blocks"])
        self.assertEqual(draft["revision"], 1)

    def test_unhashable_block_type_and_status_are_saved_safely(self) -> None:
        payload = self._payload()
        block = payload["sections"][0]["blocks"][0]
        block["type"] = []
        block["status"] = {}
        draft = draft_service.save_draft(self.project["id"], payload)
        saved = draft["sections"][0]["blocks"][0]
        self.assertEqual(saved["type"], "paragraph")
        self.assertEqual(saved["status"], "draft")
        self.assertEqual(
            draft_service.get_draft(self.project["id"])["sections"][0]["blocks"][0]["status"],
            "draft",
        )

    def test_version_cap_deletes_pruned_snapshot_files(self) -> None:
        draft_service.save_draft(self.project["id"], self._payload())
        versions = [
            draft_service.create_version(self.project["id"], f"第{index}版")
            for index in range(1, draft_state.MAX_VERSIONS + 2)
        ]
        listed = draft_service.list_versions(self.project["id"])
        self.assertEqual(len(listed), draft_state.MAX_VERSIONS)
        self.assertEqual(
            {item["id"] for item in listed},
            {item["id"] for item in versions[-draft_state.MAX_VERSIONS:]},
        )
        directory = draft_state.versions_dir(self.project["id"])
        newest = versions[-1]
        self.assertTrue((directory / f"{newest['id']}.json").exists())
        self.assertTrue(draft_service.get_version(self.project["id"], newest["id"]))
        pruned = versions[: len(versions) - draft_state.MAX_VERSIONS]
        for item in pruned:
            self.assertFalse((directory / f"{item['id']}.json").exists())

    def test_summary_reports_latest_audit_high(self) -> None:
        draft_service.save_draft(self.project["id"], self._payload())
        self.assertEqual(draft_service.summary(self.project["id"])["audit_high"], 0)
        project_service.add_decision(
            self.project["id"], "audit", {"high": 2, "medium": 1, "total": 3}, "草稿核查"
        )
        self.assertEqual(draft_service.summary(self.project["id"])["audit_high"], 2)
        project_service.add_decision(
            self.project["id"], "audit", {"high": 0, "medium": 4, "total": 4}, "草稿核查"
        )
        self.assertEqual(draft_service.summary(self.project["id"])["audit_high"], 0)
        self.assertEqual(
            project_service.get_project(self.project["id"])["draft_summary"]["audit_high"],
            0,
        )
