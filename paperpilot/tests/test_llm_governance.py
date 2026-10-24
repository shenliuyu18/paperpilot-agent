from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from app import config
from app import llm
from app.services import content_eval, llm_gateway


class ModelRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPERPILOT_DATA_DIR": self.tmp.name})
        self.env.start()
        llm_gateway.reset_audit()
        llm_gateway.reset_cache()

    def tearDown(self) -> None:
        llm_gateway.reset_audit()
        self.env.stop()
        self.tmp.cleanup()

    def test_purpose_route_overrides_default_model(self) -> None:
        with patch.object(config, "LLM_ROUTES", {"stance": "small-model", "assistant": "big-model"}):
            self.assertEqual(llm_gateway._model_for("stance"), "small-model")
            self.assertEqual(llm_gateway._model_for("unknown"), getattr(config, "LLM_MODEL", ""))

    def test_audited_chat_uses_routed_model_and_records_audit(self) -> None:
        with patch.object(config, "LLM_ROUTES", {"stance": "small-model"}), \
             patch.object(llm, "chat", return_value="hello") as chat:
            text, meta = llm_gateway.audited_chat(
                [{"role": "user", "content": "hi"}], purpose="stance")
            summary = llm_gateway.summary()
        self.assertEqual(text, "hello")
        self.assertEqual(meta["model"], "small-model")
        self.assertEqual(chat.call_args.kwargs.get("model"), "small-model")
        self.assertEqual(summary["calls"], 1)
        self.assertEqual(summary["ok"], 1)
        self.assertEqual(summary["routes"], {"stance": "small-model"})

    def test_transport_failure_records_degrade_reason(self) -> None:
        with patch.object(config, "LLM_ROUTES", {}), \
             patch.object(llm, "chat", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                llm_gateway.audited_chat([{"role": "user", "content": "hi"}], purpose="stance")
        summary = llm_gateway.summary()
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["degrade_reasons"].get("transport"), 1)

    def test_unusable_reply_records_schema_degrade(self) -> None:
        with patch.object(config, "LLM_ROUTES", {}), \
             patch.object(llm, "chat", side_effect=llm.LLMBadResponse("empty")):
            with self.assertRaises(llm.LLMBadResponse):
                llm_gateway.audited_chat([{"role": "user", "content": "hi"}], purpose="stance")
        summary = llm_gateway.summary()
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["degrade_reasons"].get("schema"), 1)

    def test_http_4xx_is_recorded_with_status_and_provider_detail(self) -> None:
        error = llm.LLMHTTPError(429, "insufficient_quota: quota exceeded")
        with patch.object(config, "LLM_ROUTES", {}),              patch.object(llm, "chat", side_effect=error):
            with self.assertRaises(llm.LLMHTTPError):
                llm_gateway.audited_chat([{"role": "user", "content": "hi"}], purpose="assistant")
        summary = llm_gateway.summary()
        self.assertEqual(summary["degrade_reasons"].get("rate_limit"), 1)
        entry = llm_gateway.audit_log(1)[0]
        self.assertEqual(entry["error"], "HTTP 429")
        self.assertIn("quota", entry["detail"])

    def test_http_status_maps_to_actionable_reason(self) -> None:
        for status, reason in ((401, "auth"), (403, "auth"), (400, "request"), (429, "rate_limit")):
            llm_gateway.reset_audit()
            with patch.object(config, "LLM_ROUTES", {}),                  patch.object(llm, "chat", side_effect=llm.LLMHTTPError(status, "x")):
                with self.assertRaises(llm.LLMHTTPError):
                    llm_gateway.audited_chat([{"role": "user", "content": "hi"}], purpose="assistant")
            self.assertEqual(llm_gateway.summary()["degrade_reasons"].get(reason), 1, status)

    @patch.object(llm_gateway.time, "sleep")
    def test_an_empty_reply_is_retried_once(self, sleep) -> None:
        with patch.object(config, "LLM_ROUTES", {}), \
             patch.object(llm, "chat", side_effect=[llm.LLMBadResponse("empty"), "ok"]) as chat:
            text, meta = llm_gateway.audited_chat(
                [{"role": "user", "content": "hi"}], purpose="stance")
        self.assertEqual(text, "ok")
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(meta["status"], "ok")
        summary = llm_gateway.summary()
        self.assertEqual(summary["ok"], 1)
        self.assertEqual(summary["failed"], 0)
        sleep.assert_called_once()

    @patch.object(llm_gateway.time, "sleep")
    def test_two_empty_replies_fail_with_schema_degrade(self, sleep) -> None:
        with patch.object(config, "LLM_ROUTES", {}), \
             patch.object(llm, "chat", side_effect=llm.LLMBadResponse("empty")) as chat:
            with self.assertRaises(llm.LLMBadResponse):
                llm_gateway.audited_chat([{"role": "user", "content": "hi"}], purpose="stance")
        self.assertEqual(chat.call_count, 2)
        summary = llm_gateway.summary()
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["degrade_reasons"].get("schema"), 1)

    @patch.object(llm_gateway.time, "sleep")
    def test_reasoning_exhausted_reply_retries_with_a_larger_budget(self, sleep) -> None:
        exhausted = llm.LLMBadResponse("empty", reasoning_exhausted=True)
        with patch.object(config, "LLM_ROUTES", {}), \
             patch.object(llm, "chat", side_effect=[exhausted, "ok"]) as chat:
            text, meta = llm_gateway.audited_chat(
                [{"role": "user", "content": "hi"}], purpose="agent_report", max_tokens=2600)
        self.assertEqual(text, "ok")
        self.assertEqual(meta["status"], "ok")
        self.assertEqual(chat.call_args_list[0].kwargs["max_tokens"], 2600)
        self.assertEqual(chat.call_args_list[1].kwargs["max_tokens"],
                         llm_gateway.REASONING_RETRY_TOKENS)
        self.assertEqual(llm_gateway.summary()["failed"], 0)

    def test_budget_exhaustion_is_visible(self) -> None:
        budget = llm_gateway.CallBudget(max_calls=0)
        payload, meta = llm_gateway.audited_chat_json(
            "sys", "user", purpose="stance", budget=budget)
        self.assertIsNone(payload)
        self.assertEqual(meta["status"], "budget")
        self.assertEqual(meta["degrade_reason"], "budget")
        self.assertEqual(llm_gateway.summary()["degrade_reasons"].get("budget"), 1)

    def test_schema_failure_falls_back_with_reason(self) -> None:
        with patch.object(llm, "chat", return_value="not json at all"):
            payload, meta = llm_gateway.audited_chat_json(
                "sys", "user", purpose="stance", schema=content_eval.ONLINE_SCHEMA)
        self.assertIsNone(payload)
        self.assertEqual(meta["status"], "fallback")
        self.assertEqual(meta["degrade_reason"], "schema")
        self.assertGreaterEqual(llm_gateway.summary()["degrade_reasons"].get("schema", 0), 1)


class OnlinePredictorTests(unittest.TestCase):
    CLAIM = "The prototype consumes 4.2 fJ per conversion."
    QUOTE = "The prototype consumes 4.2 fJ per conversion."

    def test_agreement_keeps_rule_stance(self) -> None:
        predict, stats = content_eval.online_predictor(
            lambda system, user: {"stance": "supported", "reason": "numbers match"})
        self.assertEqual(predict(self.CLAIM, self.QUOTE), "supported")
        self.assertEqual(stats["agreed"], 1)
        self.assertEqual(stats["disagreed"], 0)

    def test_disagreement_keeps_rule_and_is_counted(self) -> None:
        predict, stats = content_eval.online_predictor(
            lambda system, user: {"stance": "unsupported", "reason": "model says no"})
        self.assertEqual(predict(self.CLAIM, self.QUOTE), "supported")
        self.assertEqual(stats["disagreed"], 1)

    def test_fabricated_counter_quote_is_rejected(self) -> None:
        predict, stats = content_eval.online_predictor(
            lambda system, user: {"stance": "supported", "reason": "ok",
                                  "counter_quote": "这段话不在证据里"})
        self.assertEqual(predict(self.CLAIM, self.QUOTE), "supported")
        self.assertEqual(stats["rejected_quote"], 1)
        self.assertEqual(stats["agreed"], 0)

    def test_real_counter_quote_is_accepted(self) -> None:
        quote = "The prototype consumes 4.2 fJ per conversion. Older designs consumed more."
        predict, stats = content_eval.online_predictor(
            lambda system, user: {"stance": "supported", "reason": "ok",
                                  "counter_quote": "The prototype consumes 4.2 fJ per conversion."})
        self.assertEqual(predict(self.CLAIM, quote), "supported")
        self.assertEqual(stats["rejected_quote"], 0)

    def test_transport_error_degrades_to_rule(self) -> None:
        def boom(system, user):
            raise RuntimeError("offline")
        predict, stats = content_eval.online_predictor(boom)
        self.assertEqual(predict(self.CLAIM, self.QUOTE), "supported")
        self.assertEqual(stats["degraded"], 1)

    def test_bad_stance_degrades_to_rule(self) -> None:
        predict, stats = content_eval.online_predictor(
            lambda system, user: {"stance": "maybe", "reason": "?"})
        self.assertEqual(predict(self.CLAIM, self.QUOTE), "supported")
        self.assertEqual(stats["degraded"], 1)


if __name__ == "__main__":
    unittest.main()
