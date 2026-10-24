from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import assistant_state, project_state


class AssistantStateTests(unittest.TestCase):
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
        self.project = project_state.create_project("助手状态项目", "如何测试助手状态？")

    def test_empty_conversation(self) -> None:
        data = assistant_state.read_assistant(self.project["id"])
        self.assertEqual(data["messages"], [])
        self.assertEqual(data["version"], 1)

    def test_append_and_normalize_message(self) -> None:
        message = assistant_state.build_message(
            "assistant",
            "回答",
            citations=[{"index": 2, "kind": "evidence", "id": "pev-1", "title": "T", "quote": "Q"}],
            suggestions=[{"type": "new_run", "label": "跑一轮"}],
            grounding={"status": "grounded"},
            mode="llm",
            intent="evidence",
        )
        self.assertTrue(message["id"].startswith("msg-"))
        self.assertTrue(message["suggestions"][0]["requires_confirm"])
        saved = assistant_state.append_message(self.project["id"], message)
        self.assertEqual(len(saved["messages"]), 1)
        reopened = assistant_state.read_assistant(self.project["id"])
        self.assertEqual(reopened["messages"][0]["citations"][0]["id"], "pev-1")
        self.assertEqual(reopened["messages"][0]["citations"][0]["index"], 2)

    def test_message_cap_keeps_newest(self) -> None:
        for index in range(assistant_state.MAX_MESSAGES + 5):
            assistant_state.append_message(
                self.project["id"],
                assistant_state.build_message("user", f"问题 {index}"),
            )
        data = assistant_state.read_assistant(self.project["id"])
        self.assertEqual(len(data["messages"]), assistant_state.MAX_MESSAGES)
        self.assertEqual(data["messages"][-1]["text"], f"问题 {assistant_state.MAX_MESSAGES + 4}")
        self.assertNotIn("问题 0", [item["text"] for item in data["messages"]])

    def test_clear_and_export(self) -> None:
        assistant_state.append_message(
            self.project["id"],
            assistant_state.build_message(
                "assistant",
                "带引用的回答",
                citations=[{"kind": "evidence", "id": "pev-1", "title": "证据", "locator": "p.1", "quote": "原文"}],
                suggestions=[{"type": "open_draft", "label": "打开草稿", "reason": "有草稿"}],
            ),
        )
        markdown = assistant_state.export_markdown(self.project["id"])
        self.assertIn("助手状态项目", markdown)
        self.assertIn("带引用的回答", markdown)
        self.assertIn("原文", markdown)
        self.assertIn("需用户确认", markdown)
        assistant_state.clear_assistant(self.project["id"])
        self.assertEqual(assistant_state.read_assistant(self.project["id"])["messages"], [])


if __name__ == "__main__":
    unittest.main()
