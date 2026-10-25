from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config, llm
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
    @patch("app.llm.chat", return_value="答复")
    def test_configured_timeout_is_honoured_for_long_answers(self, chat) -> None:
        # 120 s is an explicit ceiling; the widening only applies to the untouched
        # default, so a configured timeout is never exceeded.
        with patch.object(config, "LLM_TIMEOUT", 120.0), patch.object(config, "LLM_LONG_TIMEOUT", 180.0):
            llm_gateway.audited_chat([{"role": "user", "content": "x"}], purpose="read",
                                     max_tokens=16384)
        self.assertEqual(chat.call_args.kwargs["timeout"], 120.0)

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", side_effect=RuntimeError("连接超时"))
    def test_failed_call_is_still_charged(self, chat) -> None:
        # A timeout already sent a real request, so it has to consume budget;
        # otherwise a flaky endpoint can be retried without limit.
        run = agent_state.create_run_state("目标", "demo", "auto", {"max_llm_calls": 3})
        budget = llm_gateway.CallBudget(max_calls=2)
        with llm_gateway.agent_run_context(run):
            with self.assertRaises(RuntimeError):
                llm_gateway.audited_chat([{"role": "user", "content": "x"}], purpose="read",
                                         budget=budget)
        self.assertEqual(run["budget_used"]["llm_calls"], 1)
        self.assertEqual(budget.used, 1)
        self.assertFalse(llm_gateway.audit_log()[-1]["ok"])
        with llm_gateway.agent_run_context(run):
            run["budget"]["max_llm_calls"] = 1
            with self.assertRaises(llm_gateway.LLMBudgetExceeded):
                llm_gateway.audited_chat([{"role": "user", "content": "y"}], purpose="read")

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", return_value="答复")
    def test_short_completion_keeps_the_base_timeout(self, chat) -> None:
        with patch.object(config, "LLM_TIMEOUT", 60.0), patch.object(config, "LLM_LONG_TIMEOUT", 180.0):
            llm_gateway.audited_chat(
                [{"role": "user", "content": "段落"}], purpose="draft_write", max_tokens=400
            )
        self.assertEqual(chat.call_args.kwargs["timeout"], 60.0)

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", side_effect=[llm.LLMBadResponse("空回复", reasoning_exhausted=True),
                                        "答复"])
    def test_reasoning_retry_keeps_the_longer_timeout(self, chat) -> None:
        text, meta = llm_gateway.audited_chat(
            [{"role": "user", "content": "精读"}], purpose="read", max_tokens=4000
        )
        self.assertEqual(text, "答复")
        self.assertEqual(meta["status"], "ok")
        tokens = [call.kwargs["max_tokens"] for call in chat.call_args_list]
        timeouts = [call.kwargs["timeout"] for call in chat.call_args_list]
        self.assertGreater(tokens[1], tokens[0])
        self.assertGreater(timeouts[0], config.LLM_TIMEOUT)
        self.assertGreaterEqual(timeouts[1], timeouts[0])

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", return_value="答复")
    def test_answer_sized_completions_get_the_longer_timeout(self, chat) -> None:
        # compare/transform/ask/assistant all ask for 900–1800 tokens and were
        # measured at 8–51 s, so they must not share the 60 s short-call default.
        with patch.object(config, "LLM_TIMEOUT", 60.0), patch.object(config, "LLM_LONG_TIMEOUT", 180.0):
            for tokens in (900, 1600, 1800):
                chat.reset_mock()
                llm_gateway.audited_chat([{"role": "user", "content": "x"}], purpose="compare",
                                         max_tokens=tokens)
                self.assertEqual(chat.call_args.kwargs["timeout"], 180.0, f"max_tokens={tokens}")
            chat.reset_mock()
            llm_gateway.audited_chat([{"role": "user", "content": "x"}], purpose="draft_write",
                                     max_tokens=400)
            self.assertEqual(chat.call_args.kwargs["timeout"], 60.0)

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", return_value="答复")
    def test_explicitly_short_timeout_is_respected(self, chat) -> None:
        # A timeout below the built-in default means the user asked to fail fast.
        with patch.object(config, "LLM_TIMEOUT", 15.0), patch.object(config, "LLM_LONG_TIMEOUT", 180.0):
            llm_gateway.audited_chat([{"role": "user", "content": "x"}], purpose="read",
                                     max_tokens=16384)
        self.assertEqual(chat.call_args.kwargs["timeout"], 15.0)

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", return_value="答复")
    def test_budget_block_on_the_plain_path_is_also_audited(self, chat) -> None:
        llm_gateway.reset_audit()
        run = agent_state.create_run_state("目标", "demo", "auto", {"max_llm_calls": 0})
        with llm_gateway.agent_run_context(run):
            with self.assertRaises(llm_gateway.LLMBudgetExceeded):
                llm_gateway.audited_chat([{"role": "user", "content": "写简报"}],
                                         purpose="agent_report", max_tokens=2600)
        chat.assert_not_called()
        entries = llm_gateway.audit_log()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["purpose"], "agent_report")
        self.assertEqual(entries[0]["degrade_reason"], "budget")
        self.assertEqual(entries[0]["run_id"], run["id"])

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", return_value="答复")
    def test_budget_block_is_audited_once_with_the_run(self, chat) -> None:
        llm_gateway.reset_audit()
        run = agent_state.create_run_state("目标", "demo", "auto", {"max_llm_calls": 0})
        with llm_gateway.agent_run_context(run):
            payload, meta = llm_gateway.audited_chat_json(
                "system", "user", purpose="compare", schema=None
            )
        self.assertIsNone(payload)
        self.assertEqual(meta["status"], "budget")
        chat.assert_not_called()
        entries = llm_gateway.audit_log()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["degrade_reason"], "budget")
        self.assertEqual(entries[0]["purpose"], "compare")
        self.assertEqual(entries[0]["run_id"], run["id"])

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", return_value="答复")
    def test_exhausted_run_can_still_reuse_a_cached_reply(self, chat) -> None:
        run = agent_state.create_run_state("目标", "demo", "auto", {"max_llm_calls": 1})
        messages = [{"role": "user", "content": "同一问题"}]
        with llm_gateway.agent_run_context(run):
            first = llm_gateway.audited_chat(messages, purpose="read", use_cache=True)
            self.assertFalse(first[1]["cached"])
            self.assertEqual(run["budget_used"]["llm_calls"], 1)
            # Reuse is not a model call, so an exhausted budget must not hide an
            # answer the process already has.
            second = llm_gateway.audited_chat(messages, purpose="read", use_cache=True)
        self.assertTrue(second[1]["cached"])
        self.assertEqual(second[0], first[0])
        self.assertEqual(run["budget_used"]["llm_calls"], 1)
        chat.assert_called_once()

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", return_value="答复")
    def test_ambient_run_context_is_charged(self, chat) -> None:
        run = agent_state.create_run_state("目标", "demo", "auto", {"max_llm_calls": 2})
        with llm_gateway.agent_run_context(run):
            llm_gateway.audited_chat([{"role": "user", "content": "x"}], purpose="read")
        self.assertEqual(run["budget_used"]["llm_calls"], 1)
        self.assertEqual(llm_gateway.audit_log()[-1]["run_id"], run["id"])

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", return_value="答复")
    def test_cached_reply_is_audited_but_not_charged(self, chat) -> None:
        run = agent_state.create_run_state("目标", "demo", "auto", {"max_llm_calls": 2})
        messages = [{"role": "user", "content": "同一问题"}]
        with llm_gateway.agent_run_context(run):
            first = llm_gateway.audited_chat(messages, purpose="read", use_cache=True)
            second = llm_gateway.audited_chat(messages, purpose="read", use_cache=True)
        self.assertFalse(first[1]["cached"])
        self.assertTrue(second[1]["cached"])
        chat.assert_called_once()
        self.assertEqual(run["budget_used"]["llm_calls"], 1)
        entries = llm_gateway.audit_log()
        self.assertTrue(entries[-1]["cached"])
        self.assertEqual(entries[-1]["run_id"], run["id"])

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

    def test_a_call_that_never_left_the_process_is_not_charged(self) -> None:
        """Budget is a cost guard: no request, no cost, but the reason stays visible."""
        run = agent_state.create_run_state("目标", "demo", "auto", {"max_llm_calls": 3})
        budget = llm_gateway.CallBudget(max_calls=2)
        with patch.object(config, "LLM_ENABLED", False):
            with self.assertRaises(llm.LLMUnavailable):
                llm_gateway.audited_chat(
                    [{"role": "user", "content": "x"}],
                    purpose="ask", budget=budget, run=run,
                )
        self.assertEqual(budget.used, 0)
        self.assertEqual(run["budget_used"]["llm_calls"], 0)
        rows = [row for row in llm_gateway.audit_log() if row["purpose"] == "ask"]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["degrade_reason"], "config")

    @patch.object(config, "LLM_ENABLED", True)
    def test_a_blocked_repair_reports_budget_once_and_not_as_schema(self) -> None:
        """The repair call is a second call: when the budget stops it, say so once.

        The first answer parses as JSON but misses a required field, so a repair is genuinely
        attempted; the run allows one call, so that repair is blocked. It used to be recorded
        twice, the second time as a ``schema`` failure that never happened.
        """
        run = agent_state.create_run_state("目标", "demo", "auto", {"max_llm_calls": 1})
        budget = llm_gateway.CallBudget(max_calls=2)
        with patch("app.llm.chat", return_value='{"other": "不是要的字段"}'):
            payload, meta = llm_gateway.audited_chat_json(
                "sys", "user", purpose="draft_write",
                schema={"type": "object", "required": ["x"]},
                budget=budget, run=run, temperature=0.1,
            )
        self.assertIsNone(payload)
        self.assertEqual(meta.get("degrade_reason"), "budget", meta)
        rows = [row for row in llm_gateway.audit_log()
                if row["purpose"].startswith("draft_write") and not row.get("ok")]
        self.assertEqual([row.get("degrade_reason") for row in rows], ["budget"], rows)

    @patch.object(config, "LLM_ENABLED", True)
    def test_the_cache_key_carries_the_routed_model(self) -> None:
        """Per-purpose routing must not serve one model's answer as another model's."""
        saved = config.LLM_ROUTES
        try:
            with patch("app.llm.chat", return_value="答案一") as chat:
                config.LLM_ROUTES = {}
                args = {"purpose": "assistant", "temperature": 0.1, "use_cache": True}
                llm_gateway.audited_chat([{"role": "user", "content": "q1"}], **args)
                chat.assert_called_once()
                llm_gateway.audited_chat([{"role": "user", "content": "q1"}], **args)
                chat.assert_called_once()  # same model: reuse

                config.LLM_ROUTES = {"assistant": "model-b"}
                llm_gateway.audited_chat([{"role": "user", "content": "q1"}], **args)
                self.assertEqual(chat.call_count, 2, "换路由模型后不应命中上一个模型的缓存")
        finally:
            config.LLM_ROUTES = saved

    def test_reset_audit_clears_log(self) -> None:
        llm_gateway._record({"at": "now", "purpose": "x"})
        self.assertTrue(llm_gateway.audit_log())
        llm_gateway.reset_audit()
        self.assertEqual(llm_gateway.audit_log(), [])


if __name__ == "__main__":
    unittest.main()
