from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.services import agent_actions, agent_state


def _run_state() -> dict:
    run = agent_state.create_run_state("低功耗 ADC", "demo", "auto", agent_state.DEFAULT_BUDGET)
    run["steps"] = [
        {"id": "act-001", "tool": "search", "title": "检索", "status": "done",
         "rationale": "建立候选", "artifact": {"selected": ["P1"]}},
        {"id": "act-002", "tool": "read", "title": "精读", "status": "done",
         "rationale": "补证据", "artifact": {"paper": {"id": "P1", "title": "Paper One"}, "tldr": "t"}},
    ]
    run["evidence"] = [
        {"id": "ev-001", "paper_id": "P1", "title": "Paper One", "section": "Abstract",
         "page": 1, "claim": "c", "quote": "q", "verified": True, "kind": "problem"}
    ]
    return run


class AgentLLMReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(
            os.environ,
            {
                "PAPERPILOT_AGENT_FILE": str(Path(self.tmp.name) / "runs.json"),
                "PAPERPILOT_AGENT_DIR": str(Path(self.tmp.name) / "runs"),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def _run(self) -> dict:
        return _run_state()

    @patch.object(config, "LLM_ENABLED", True)
    @patch.object(agent_actions, "_offline_report", return_value="# Offline")
    @patch("app.llm.chat", return_value="# LLM Report\n\n[P1] evidence")
    def test_llm_report_used(self, chat, offline) -> None:
        markdown, mode = agent_actions.build_report(self._run())
        self.assertEqual(mode, "llm")
        self.assertIn("[P1]", markdown)
        self.assertTrue(chat.called)

    @patch.object(config, "LLM_ENABLED", True)
    @patch.object(agent_actions, "_offline_report", return_value="# Offline")
    @patch("app.llm.chat", side_effect=RuntimeError("model down"))
    def test_llm_failure_falls_back(self, chat, offline) -> None:
        markdown, mode = agent_actions.build_report(self._run())
        self.assertEqual(mode, "offline")
        self.assertEqual(markdown, "# Offline")

    @patch.object(config, "LLM_ENABLED", True)
    @patch.object(agent_actions, "_offline_report", return_value="# Offline")
    @patch("app.llm.chat", return_value="没有引用的泛泛而谈")
    def test_llm_output_without_citation_falls_back(self, chat, offline) -> None:
        markdown, mode = agent_actions.build_report(self._run())
        self.assertEqual(mode, "offline")
        self.assertEqual(markdown, "# Offline")


if __name__ == "__main__":
    unittest.main()
