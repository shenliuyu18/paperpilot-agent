from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import (
    draft_audit,
    draft_service,
    draft_state,
    project_service,
    project_state,
)


class DraftClaimsTests(unittest.TestCase):
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
            "section": "Results", "page": 1,
            "quote": "The prototype consumes 4.2 fJ per conversion.",
            "verified": True, "kind": "text",
        }]
        project_state.write_memory(self.project["id"], memory)
        self.pid = self.project["id"]

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _draft_with_block(self) -> dict:
        draft_service.ensure_from_markdown(
            self.pid, "## 引言\n\nThe prototype consumes 4.2 fJ per conversion.\n"
        )
        draft = draft_state.read_draft(self.pid)
        block = draft["sections"][0]["blocks"][0]
        block["evidence_ids"] = ["pev-1"]
        draft_state.write_draft(self.pid, draft)
        return draft_state.read_draft(self.pid)

    def test_blocks_default_to_empty_claims(self) -> None:
        draft = draft_service.ensure_from_markdown(self.pid, "## 引言\n\n正文。\n")
        read = draft_state.read_draft(self.pid)
        for section in read["sections"]:
            for block in section["blocks"]:
                self.assertIn("claims", block)
                self.assertEqual(block["claims"], [])
        self.assertTrue(draft["sections"])

    def test_save_preserves_claims_when_client_omits_them(self) -> None:
        draft = self._draft_with_block()
        block = draft["sections"][0]["blocks"][0]
        block["claims"] = [{
            "id": "clm-001",
            "text": "The prototype consumes 4.2 fJ per conversion.",
            "evidence_ids": ["pev-1"],
            "stance": "supported",
            "reason": "数值与关键词均命中。",
            "counter_quote": "",
            "checked_at": "2026-10-22 10:00:00",
        }]
        draft_state.write_draft(self.pid, draft)

        payload = copy.deepcopy(draft_state.read_draft(self.pid))
        payload["revision"] = draft_state.read_draft(self.pid)["revision"]
        for section in payload["sections"]:
            for item in section["blocks"]:
                item.pop("claims", None)  # the editor never sends claims
        draft_service.save_draft(self.pid, payload)

        saved = draft_state.read_draft(self.pid)
        claims = saved["sections"][0]["blocks"][0]["claims"]
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["stance"], "supported")

    def test_client_claims_are_normalized_and_capped(self) -> None:
        draft = self._draft_with_block()
        block = draft["sections"][0]["blocks"][0]
        block["claims"] = [
            {"text": f"主张 {index}", "stance": "bogus"}
            for index in range(1, 16)
        ]
        payload = copy.deepcopy(draft)
        draft_service.save_draft(self.pid, payload)

        saved = draft_state.read_draft(self.pid)
        claims = saved["sections"][0]["blocks"][0]["claims"]
        self.assertLessEqual(len(claims), draft_state.MAX_CLAIMS_PER_BLOCK)
        self.assertTrue(all(item["stance"] == "insufficient" for item in claims))
        self.assertTrue(all(item["id"].startswith("clm-") for item in claims))

    def test_audit_surfaces_unsupported_claim(self) -> None:
        draft = {
            "project_id": self.pid,
            "revision": 1,
            "title": "草稿",
            "citation_style": "numeric",
            "sections": [{"id": "sec-001", "title": "引言", "blocks": [{
                "id": "blk-001", "type": "paragraph",
                "text": "The prototype consumes 4.2 fJ per conversion.",
                "evidence_ids": ["pev-1"], "citation_keys": [], "status": "draft",
                "claims": [{
                    "id": "clm-001",
                    "text": "The prototype consumes 1.1 fJ per conversion.",
                    "evidence_ids": ["pev-1"],
                    "stance": "unsupported",
                    "reason": "数值未在证据中出现。",
                }],
            }]}],
            "citations": {},
            "versions": [],
        }
        memory = project_state.read_memory(self.pid)
        result = draft_audit.audit_draft(draft, memory)
        kinds = {(issue["kind"], issue["severity"]) for issue in result["issues"]}
        self.assertIn(("claim_unsupported", "high"), kinds)
        self.assertEqual(result["status_map"]["blk-001"], "needs_evidence")

    def test_audit_partial_claim_is_medium_only(self) -> None:
        draft = {
            "project_id": self.pid,
            "revision": 1,
            "title": "草稿",
            "citation_style": "numeric",
            "sections": [{"id": "sec-001", "title": "引言", "blocks": [{
                "id": "blk-001", "type": "paragraph",
                "text": "The prototype consumes 4.2 fJ per conversion.",
                "evidence_ids": ["pev-1"], "citation_keys": [], "status": "draft",
                "claims": [{
                    "id": "clm-001",
                    "text": "The prototype consumes 4.2 fJ in most workloads.",
                    "evidence_ids": ["pev-1"],
                    "stance": "partial",
                    "reason": "只覆盖了转换能耗。",
                }],
            }]}],
            "citations": {},
            "versions": [],
        }
        memory = project_state.read_memory(self.pid)
        result = draft_audit.audit_draft(draft, memory)
        claims = [issue for issue in result["issues"] if issue["kind"].startswith("claim_")]
        self.assertEqual([(i["kind"], i["severity"]) for i in claims], [("claim_partial", "medium")])
        self.assertEqual(result["status_map"]["blk-001"], "checked")

    def test_old_draft_without_claims_still_reads(self) -> None:
        draft_service.ensure_from_markdown(self.pid, "## 引言\n\n正文。\n")
        path = draft_state.draft_path(self.pid)
        raw = path.read_text(encoding="utf-8").replace('"claims": []', '"legacy_only": true')
        path.write_text(raw, encoding="utf-8")
        read = draft_state.read_draft(self.pid)
        self.assertEqual(read["sections"][0]["blocks"][0]["claims"], [])
