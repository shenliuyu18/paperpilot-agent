from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.services import agent_state, llm_gateway


class LLMGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(
            os.environ, {"PAPERPILOT_DATA_DIR": self.tmp.name}
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        llm_gateway.reset_audit()
        llm_gateway.reset_cache()

    def test_budget_is_checked_before_calling(self) -> None:
        budget = llm_gateway.CallBudget(max_calls=0)
        with patch("app.llm.chat") as chat:
            with self.assertRaises(llm_gateway.LLMBudgetExceeded):
                llm_gateway.audited_chat(
                    [{"role": "user", "content": "x"}],
                    purpose="test",
                    budget=budget,
                )
        chat.assert_not_called()

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", return_value="ok")
    def test_success_spends_budget_and_run(self, chat) -> None:
        run = agent_state.create_run_state("目标", "demo", "auto", {"max_llm_calls": 3})
        budget = llm_gateway.CallBudget(max_calls=2)
        text, meta = llm_gateway.audited_chat(
            [{"role": "user", "content": "x"}],
            purpose="assistant",
            budget=budget,
            run=run,
        )
        self.assertEqual(text, "ok")
        self.assertEqual(meta["status"], "ok")
        self.assertEqual(budget.used, 1)
        self.assertEqual(run["budget_used"]["llm_calls"], 1)
        entries = llm_gateway.audit_log()
        self.assertEqual(entries[-1]["purpose"], "assistant")
        self.assertTrue(entries[-1]["ok"])

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", return_value="ok")
    def test_run_budget_is_enforced(self, chat) -> None:
        run = agent_state.create_run_state("目标", "demo", "auto", {"max_llm_calls": 0})
        with self.assertRaises(llm_gateway.LLMBudgetExceeded):
            llm_gateway.audited_chat(
                [{"role": "user", "content": "x"}],
                purpose="agent_report",
                run=run,
            )
        chat.assert_not_called()

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", return_value="ok")
    def test_audit_never_stores_prompt_text(self, chat) -> None:
        secret = "SECRET-PROMPT-TEXT"
        llm_gateway.audited_chat(
            [{"role": "user", "content": secret}],
            purpose="test",
        )
        path = Path(self.tmp.name) / "llm_calls.json"
        self.assertTrue(path.exists())
        self.assertNotIn(secret, path.read_text(encoding="utf-8"))
        self.assertEqual(llm_gateway.audit_log()[-1]["prompt_chars"], len(secret))

    def test_schema_validation(self) -> None:
        schema = {
            "type": "object",
            "required": ["answer", "used_indices"],
            "properties": {
                "answer": {"type": "string", "max_length": 20},
                "used_indices": {
                    "type": "array",
                    "max_items": 3,
                    "items": {"type": "integer"},
                },
            },
        }
        self.assertIsNone(
            llm_gateway.validate_payload({"answer": "ok", "used_indices": [1]}, schema)
        )
        self.assertIn(
            "缺少字段",
            llm_gateway.validate_payload({"answer": "ok"}, schema),
        )
        self.assertIn(
            "过长",
            llm_gateway.validate_payload(
                {"answer": "x" * 30, "used_indices": [1]}, schema
            ),
        )
        self.assertIn(
            "必须是整数",
            llm_gateway.validate_payload(
                {"answer": "ok", "used_indices": ["1"]}, schema
            ),
        )

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", side_effect=["not json", '{"answer": "ok", "used_indices": [1]}'])
    def test_json_repair_retries_once(self, chat) -> None:
        schema = {
            "type": "object",
            "required": ["answer", "used_indices"],
            "properties": {
                "answer": {"type": "string"},
                "used_indices": {"type": "array", "items": {"type": "integer"}},
            },
        }
        payload, meta = llm_gateway.audited_chat_json(
            "system",
            "user",
            purpose="assistant",
            schema=schema,
            budget=llm_gateway.CallBudget(max_calls=2),
        )
        self.assertEqual(payload["answer"], "ok")
        self.assertTrue(meta.get("repaired"))
        self.assertEqual(chat.call_count, 2)

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", return_value="cached")
    def test_cache_avoids_duplicate_calls(self, chat) -> None:
        messages = [{"role": "user", "content": "same"}]
        first, _ = llm_gateway.audited_chat(
            messages, purpose="test", temperature=0.1, use_cache=True
        )
        second, meta = llm_gateway.audited_chat(
            messages, purpose="test", temperature=0.1, use_cache=True
        )
        self.assertEqual(first, "cached")
        self.assertEqual(second, "cached")
        self.assertTrue(meta["cached"])
        self.assertEqual(chat.call_count, 1)

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", side_effect=["not json", '{"answer": "ok", "used_indices": [1]}'])
    def test_json_response_is_cached_only_after_schema_success(self, chat) -> None:
        schema = {
            "type": "object",
            "required": ["answer", "used_indices"],
            "properties": {
                "answer": {"type": "string"},
                "used_indices": {"type": "array", "items": {"type": "integer"}},
            },
        }
        _, first = llm_gateway.audited_chat_json(
            "system", "user", purpose="assistant", schema=schema, use_cache=True,
            budget=llm_gateway.CallBudget(max_calls=2),
        )
        payload, second = llm_gateway.audited_chat_json(
            "system", "user", purpose="assistant", schema=schema, use_cache=True,
        )
        self.assertTrue(first.get("repaired"))
        self.assertTrue(second.get("cached"))
        self.assertEqual(payload["answer"], "ok")
        self.assertEqual(chat.call_count, 2)

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", side_effect=["not json", "still not json", "again", "nope"])
    def test_schema_failure_is_not_persistent_in_cache(self, chat) -> None:
        payload, _ = llm_gateway.audited_chat_json(
            "system", "user", purpose="assistant",
            schema={"type": "object", "required": ["answer"]}, use_cache=True,
            budget=llm_gateway.CallBudget(max_calls=2),
        )
        self.assertIsNone(payload)
        payload2, _ = llm_gateway.audited_chat_json(
            "system", "user", purpose="assistant",
            schema={"type": "object", "required": ["answer"]}, use_cache=True,
            budget=llm_gateway.CallBudget(max_calls=2),
        )
        self.assertIsNone(payload2)
        self.assertEqual(chat.call_count, 4)

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", side_effect=RuntimeError("down"))
    def test_transport_failure_returns_none_for_json(self, chat) -> None:
        payload, meta = llm_gateway.audited_chat_json(
            "system", "user", purpose="assistant"
        )
        self.assertIsNone(payload)
        self.assertEqual(meta["status"], "fallback")
        self.assertEqual(llm_gateway.audit_log()[-1]["ok"], False)

    def test_reset_audit_clears_log(self) -> None:
        llm_gateway._record({"at": "now", "purpose": "x"})
        self.assertTrue(llm_gateway.audit_log())
        llm_gateway.reset_audit()
        self.assertEqual(llm_gateway.audit_log(), [])


if __name__ == "__main__":
    unittest.main()
