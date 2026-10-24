from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.services import (
    assistant_service, assistant_state, draft_state, project_state,
)


class AssistantServiceTests(unittest.TestCase):
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
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.project = project_state.create_project(
            "低功耗 ADC 研究",
            "如何在保持精度的同时降低 ADC 功耗？",
            scope="存内计算 ADC",
            constraints=["优先开放获取论文"],
        )
        memory = project_state.read_memory(self.project["id"])
        memory["evidence"] = [
            {
                "id": "pev-1",
                "paper_id": "P1",
                "title": "A Low-Power SAR ADC",
                "section": "Results",
                "page": 5,
                "quote": "The prototype consumes 4.2 fJ per conversion.",
                "claim": "原型每次转换消耗 4.2 fJ",
                "verified": True,
                "updated_at": "2026-10-21 10:00:00",
            }
        ]
        memory["open_questions"] = [
            {
                "id": "pq-1",
                "question": "在 28nm 以下工艺，校准开销是否抵消功耗收益？",
                "status": "open",
                "priority": "high",
                "kind": "evidence",
            }
        ]
        memory["decisions"] = [
            {
                "id": "pd-1",
                "kind": "topic",
                "choice": "异步 SAR ADC 的校准开销",
                "note": "用户在第 2 个检查点选择",
                "at": "2026-10-21 10:07:00",
            }
        ]
        project_state.write_memory(self.project["id"], memory)
        draft = draft_state.empty_draft(self.project["id"])
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
                ],
            }
        ]
        draft_state.write_draft(self.project["id"], draft)

    def _ask(self, message: str) -> dict:
        with patch.object(config, "LLM_ENABLED", False):
            return assistant_service.answer(self.project["id"], message)["reply"]

    def test_detect_intents(self) -> None:
        cases = {
            "项目进展如何，下一步做什么？": "status",
            "有哪些证据支持 4.2 fJ？": "evidence",
            "还有哪些开放问题和缺口？": "gap",
            "为什么选择这个方向？": "decision",
            "我的草稿哪里有问题？": "draft",
            "对比一下最近两轮运行": "compare",
            "这个方法怎么复现？": "method",
            "你好": "general",
        }
        for message, expected in cases.items():
            self.assertEqual(assistant_service.detect_intent(message), expected, message)

    def test_evidence_answer_uses_citation(self) -> None:
        reply = self._ask("有哪些证据支持 4.2 fJ 的结论？")
        self.assertEqual(reply["intent"], "evidence")
        self.assertIn("4.2 fJ", reply["text"])
        self.assertTrue(reply["citations"])
        self.assertEqual(reply["citations"][0]["kind"], "evidence")
        self.assertEqual(reply["citations"][0]["id"], "pev-1")
        self.assertEqual(reply["mode"], "offline")

    def test_no_evidence_question_refuses(self) -> None:
        reply = self._ask("这个项目里有关于量子纠错的证据吗？")
        self.assertIn("证据不足", reply["text"])
        self.assertEqual(reply["citations"], [])
        self.assertEqual(reply["grounding"]["status"], "insufficient")

    def test_status_uses_actual_counts(self) -> None:
        reply = self._ask("项目进展如何？")
        self.assertEqual(reply["intent"], "status")
        self.assertIn("1 条证据", reply["text"])
        self.assertIn("1 个开放问题", reply["text"])
        self.assertIn("2 个草稿块", reply["text"])
        self.assertEqual(reply["citations"][0]["kind"], "project")

    def test_gap_lists_open_question(self) -> None:
        reply = self._ask("还有哪些开放问题？")
        self.assertIn("28nm", reply["text"])
        self.assertTrue(any(item["kind"] == "question" for item in reply["citations"]))

    def test_decision_lists_choice(self) -> None:
        reply = self._ask("为什么选择这个方向？")
        self.assertIn("异步 SAR ADC", reply["text"])
        self.assertTrue(any(item["kind"] == "decision" for item in reply["citations"]))

    def test_draft_answer_does_not_mutate_draft(self) -> None:
        before = draft_state.read_draft(self.project["id"])
        reply = self._ask("我的草稿哪里有问题？")
        after = draft_state.read_draft(self.project["id"])
        self.assertIn("42%", reply["text"])
        self.assertEqual(before["revision"], after["revision"])
        self.assertEqual(
            [block["status"] for block in before["sections"][0]["blocks"]],
            [block["status"] for block in after["sections"][0]["blocks"]],
        )
        self.assertTrue(any(item["kind"] == "draft_block" for item in reply["citations"]))

    def test_compare_without_runs_falls_back_to_evidence(self) -> None:
        reply = self._ask("比较一下 4.2 fJ 的证据差异")
        self.assertEqual(reply["intent"], "compare")
        self.assertIn("4.2 fJ", reply["text"])
        self.assertTrue(reply["citations"])

    def test_compare_requires_two_completed_runs(self) -> None:
        reply = self._ask("对比一下两轮运行")
        self.assertEqual(reply["intent"], "compare")
        self.assertIn("至少需要两轮", reply["text"])

    def test_suggestions_require_confirmation(self) -> None:
        reply = self._ask("这个项目下一步该做什么？")
        self.assertTrue(reply["suggestions"])
        self.assertTrue(all(item["requires_confirm"] for item in reply["suggestions"]))
        self.assertTrue(any(item["type"] == "new_run" for item in reply["suggestions"]))
        # Suggestions are inert: the project still has zero runs.
        self.assertEqual(len(project_state.read_projects()["projects"][self.project["id"]]["run_ids"]), 0)

    def test_answer_persists_and_get_state(self) -> None:
        self._ask("项目进展如何？")
        data = assistant_state.read_assistant(self.project["id"])
        self.assertEqual(len(data["messages"]), 2)
        state = assistant_service.get_state(self.project["id"])
        self.assertEqual(len(state["messages"]), 2)
        self.assertEqual(state["mode"], "offline")
        self.assertIn("limits", state)

    def test_input_validation(self) -> None:
        with self.assertRaises(ValueError):
            assistant_service.answer(self.project["id"], "   ")
        with self.assertRaises(ValueError):
            assistant_service.answer(self.project["id"], "问" * (assistant_service.MAX_INPUT + 1))

    def test_export_contains_conversation(self) -> None:
        self._ask("项目进展如何？")
        markdown = assistant_service.export_markdown(self.project["id"])
        self.assertIn("研究 Copilot", markdown)
        self.assertIn("项目进展如何", markdown)
        self.assertIn("引用", markdown)


if __name__ == "__main__":
    unittest.main()
