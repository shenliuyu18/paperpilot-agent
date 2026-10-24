from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.services import draft_semantic, draft_state, llm_gateway, project_state


class DraftSemanticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(
            os.environ,
            {
                "PAPERPILOT_PROJECT_FILE": str(Path(self.tmp.name) / "projects.json"),
                "PAPERPILOT_PROJECT_DIR": str(Path(self.tmp.name) / "projects"),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.project = project_state.create_project("语义核查项目", "如何验证语义支持？")
        memory = project_state.read_memory(self.project["id"])
        memory["evidence"] = [
            {
                "id": "pev-1",
                "paper_id": "P1",
                "title": "Paper One",
                "section": "Results",
                "page": 3,
                "quote": "The prototype consumes 4.2 fJ per conversion.",
                "claim": "原型每次转换消耗 4.2 fJ",
                "verified": True,
            }
        ]
        project_state.write_memory(self.project["id"], memory)
        draft = draft_state.empty_draft(self.project["id"])
        draft["revision"] = 2
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
                    },
                    {
                        "id": "blk-002",
                        "type": "paragraph",
                        "text": "该方案能耗降低 42%。",
                        "evidence_ids": ["pev-1"],
                    },
                    {
                        "id": "blk-003",
                        "type": "paragraph",
                        "text": "这个方法在全部工艺角下都保持稳定。",
                        "evidence_ids": [],
                    },
                    {
                        "id": "blk-004",
                        "type": "paragraph",
                        "text": "该原型没有达到 4.2 fJ。",
                        "evidence_ids": ["pev-1"],
                    },
                ],
            }
        ]
        draft_state.write_draft(self.project["id"], draft)

    def _items(self, result: dict) -> dict[str, dict]:
        return {item["block_id"]: item for item in result["items"]}

    @patch.object(config, "LLM_ENABLED", False)
    def test_rule_verdicts(self) -> None:
        result = draft_semantic.audit(self.project["id"])
        items = self._items(result)
        self.assertEqual(result["mode"], "rule")
        self.assertEqual(result["checked"], 4)
        self.assertEqual(items["blk-001"]["verdict"], "supported")
        self.assertEqual(items["blk-002"]["verdict"], "unsupported")
        self.assertEqual(items["blk-003"]["verdict"], "insufficient")
        self.assertEqual(items["blk-004"]["verdict"], "partial")
        self.assertEqual(result["counts"]["supported"], 1)

    @patch.object(config, "LLM_ENABLED", False)
    def test_audit_does_not_mutate_draft(self) -> None:
        before = draft_state.read_draft(self.project["id"])
        draft_semantic.audit(self.project["id"])
        after = draft_state.read_draft(self.project["id"])
        self.assertEqual(before["revision"], after["revision"])
        self.assertEqual(
            [block["text"] for section in before["sections"] for block in section["blocks"]],
            [block["text"] for section in after["sections"] for block in section["blocks"]],
        )

    @patch.object(config, "LLM_ENABLED", True)
    @patch.object(
        llm_gateway,
        "audited_chat_json",
        return_value=(
            {
                "items": [
                    {
                        "block_id": "blk-001",
                        "verdict": "partial",
                        "evidence_ids": ["pev-1"],
                        "quote": "The prototype consumes 4.2 fJ per conversion.",
                        "rationale": "模型认为只覆盖部分主张",
                    }
                ]
            },
            {"status": "ok", "cached": False},
        ),
    )
    def test_llm_refinement_overrides_rule(self, _mock) -> None:
        result = draft_semantic.audit(self.project["id"])
        items = self._items(result)
        self.assertEqual(result["mode"], "llm")
        self.assertEqual(items["blk-001"]["verdict"], "partial")
        self.assertEqual(items["blk-001"]["source"], "llm")
        self.assertEqual(items["blk-002"]["source"], "rule")

    @patch.object(config, "LLM_ENABLED", True)
    @patch.object(
        llm_gateway,
        "audited_chat_json",
        return_value=(
            {
                "items": [
                    {
                        "block_id": "blk-001",
                        "verdict": "supported",
                        "evidence_ids": ["pev-999"],
                        "quote": "不存在的引句",
                        "rationale": "bad",
                    }
                ]
            },
            {"status": "ok"},
        ),
    )
    def test_llm_unknown_evidence_and_bad_quote_are_rejected(self, _mock) -> None:
        result = draft_semantic.audit(self.project["id"])
        items = self._items(result)
        self.assertEqual(result["mode"], "llm_degraded")
        self.assertEqual(items["blk-001"]["source"], "rule")

    @patch.object(config, "LLM_ENABLED", True)
    @patch.object(
        llm_gateway,
        "audited_chat_json",
        return_value=(
            {
                "items": [
                    {
                        "block_id": "blk-003",
                        "verdict": "supported",
                        "evidence_ids": [],
                        "quote": "",
                        "rationale": "模型臆测",
                    }
                ]
            },
            {"status": "ok"},
        ),
    )
    def test_model_cannot_claim_support_without_bound_evidence(self, _mock) -> None:
        result = draft_semantic.audit(self.project["id"])
        items = self._items(result)
        self.assertEqual(items["blk-003"]["source"], "rule")
        self.assertEqual(items["blk-003"]["verdict"], "insufficient")
        self.assertEqual(result["mode"], "llm_degraded")

    @patch.object(config, "LLM_ENABLED", False)
    def test_limit_is_bounded(self) -> None:
        result = draft_semantic.audit(self.project["id"], limit=999)
        self.assertLessEqual(result["checked"], 20)
        self.assertEqual(result["checked"], 4)


if __name__ == "__main__":
    unittest.main()
