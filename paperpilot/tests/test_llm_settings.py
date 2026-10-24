from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import llm_gateway, llm_settings


KEY = "sk-test-abcdef1234567890"
LOCAL_BASE = "http://127.0.0.1:11434/v1"
_CONFIG_ATTRS = (
    "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_ROUTES", "LLM_TIMEOUT",
    "LLM_RETRIES", "EMBEDDING_MODEL", "EMBEDDING_ENABLED", "LLM_ENABLED", "APP_MODE",
)


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {
            "model": "stub-model",
            "choices": [{"message": {"content": " ok "}}],
        }

    def json(self):
        return self._payload


def _env_overrides(root: Path, **extra) -> dict:
    base = {
        "PAPERPILOT_LLM_SETTINGS_FILE": str(root / "llm_settings.json"),
        "PAPERPILOT_LLM_SETTINGS_SECRETS_FILE": str(root / "llm_settings.secrets.json"),
        "PAPERPILOT_LLM_API_KEY": "",
        "OPENAI_API_KEY": "",
        "PAPERPILOT_LLM_BASE_URL": "",
        "OPENAI_BASE_URL": "",
        "PAPERPILOT_LLM_MODEL": "",
        "PAPERPILOT_LLM_ROUTES": "",
        "PAPERPILOT_EMBEDDINGS": "",
        "PAPERPILOT_LLM_PERSIST_KEY": "1",
        "PAPERPILOT_ALLOW_PRIVATE_LLM": "1",
    }
    base.update(extra)
    return base


class SettingsTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._snapshot = {name: getattr(config, name) for name in _CONFIG_ATTRS}
        self._base_env = patch.dict(os.environ, _env_overrides(self.root), clear=False)
        self._base_env.start()
        llm_settings.clear()

    def tearDown(self) -> None:
        llm_settings.clear()
        self._base_env.stop()
        for name, value in self._snapshot.items():
            setattr(config, name, value)
        self._tmp.cleanup()

    def use_env(self, **extra) -> patch:
        return patch.dict(os.environ, _env_overrides(self.root, **extra), clear=False)


class PresetTests(SettingsTestBase):
    def test_common_providers_are_covered(self) -> None:
        for provider in ("openai", "deepseek", "moonshot", "dashscope", "zhipu", "custom"):
            self.assertIn(provider, llm_settings.PROVIDERS)
        for provider, item in llm_settings.PROVIDERS.items():
            if provider == "custom":
                continue
            self.assertTrue(item["base_url"].startswith("https://"), item["base_url"])
            self.assertIn(item["default_model"], item["models"])
            self.assertTrue(item["key_url"].startswith("https://"))

    def test_purposes_match_gateway_call_sites(self) -> None:
        ids = {item["id"] for item in llm_settings.PURPOSES}
        self.assertEqual(ids, {
            "agent_report", "assistant", "draft_write", "semantic_audit", "contradiction",
        })


class SaveApplyTests(SettingsTestBase):
    def test_save_applies_runtime_and_masks_the_key(self) -> None:
        with self.use_env():
            with patch.object(llm_settings.netguard, "validate_url", lambda url, **kw: url):
                public = llm_settings.save({"provider": "deepseek", "api_key": KEY})
        self.assertEqual(public["provider"], "deepseek")
        self.assertEqual(public["model"], "deepseek-chat")
        self.assertTrue(public["has_key"])
        self.assertEqual(public["masked_key"], "••••" + KEY[-4:])
        self.assertEqual(public["key_source"], "ui")
        self.assertEqual(config.LLM_MODEL, "deepseek-chat")
        self.assertEqual(config.LLM_BASE_URL, "https://api.deepseek.com/v1")
        self.assertTrue(config.LLM_ENABLED)
        self.assertEqual(config.APP_MODE, "online")

    def test_no_endpoint_payload_contains_the_raw_key(self) -> None:
        with self.use_env():
            with patch.object(llm_settings.netguard, "validate_url", lambda url, **kw: url):
                public = llm_settings.save({"provider": "openai", "api_key": KEY})
        self.assertNotIn(KEY, json.dumps(public, ensure_ascii=False))

    def test_config_file_and_secret_file_are_separate(self) -> None:
        with self.use_env():
            with patch.object(llm_settings.netguard, "validate_url", lambda url, **kw: url):
                llm_settings.save({"provider": "zhipu", "api_key": KEY})
            stored = json.loads((self.root / "llm_settings.json").read_text(encoding="utf-8"))
            secrets = json.loads((self.root / "llm_settings.secrets.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["settings"]["provider"], "zhipu")
        self.assertNotIn("api_key", json.dumps(stored, ensure_ascii=False))
        self.assertEqual(secrets["api_key"], KEY)

    def test_ephemeral_mode_never_writes_the_key(self) -> None:
        with self.use_env(PAPERPILOT_LLM_PERSIST_KEY="0"):
            with patch.object(llm_settings.netguard, "validate_url", lambda url, **kw: url):
                public = llm_settings.save({"provider": "deepseek", "api_key": KEY})
            self.assertFalse((self.root / "llm_settings.secrets.json").exists())
            self.assertTrue(public["has_key"])
            self.assertEqual(public["masked_key"], "")
            self.assertFalse(public["persist_keys"])
            self.assertTrue(config.LLM_ENABLED)

    def test_saving_without_a_key_stays_offline(self) -> None:
        with self.use_env():
            with patch.object(llm_settings.netguard, "validate_url", lambda url, **kw: url):
                public = llm_settings.save({"provider": "deepseek"})
        self.assertFalse(public["has_key"])
        self.assertEqual(public["mode"], "offline")
        self.assertFalse(config.LLM_ENABLED)
        self.assertEqual(config.APP_MODE, "offline")

    def test_re_saved_key_is_replaced_and_clear_key_works(self) -> None:
        with self.use_env():
            with patch.object(llm_settings.netguard, "validate_url", lambda url, **kw: url):
                llm_settings.save({"provider": "deepseek", "api_key": KEY})
                kept = llm_settings.save({"provider": "deepseek", "model": "deepseek-reasoner"})
                self.assertTrue(kept["has_key"])
                self.assertEqual(kept["model"], "deepseek-reasoner")
                cleared = llm_settings.save({"provider": "deepseek", "clear_key": True})
        self.assertFalse(cleared["has_key"])
        self.assertFalse(config.LLM_ENABLED)

    def test_clear_restores_the_environment_baseline(self) -> None:
        with self.use_env(PAPERPILOT_LLM_MODEL="env-model", PAPERPILOT_LLM_BASE_URL="https://env.example/v1"):
            llm_settings.apply()
            self.assertEqual(config.LLM_MODEL, "env-model")
            with patch.object(llm_settings.netguard, "validate_url", lambda url, **kw: url):
                llm_settings.save({"provider": "deepseek", "api_key": KEY})
            self.assertEqual(config.LLM_MODEL, "deepseek-chat")
            public = llm_settings.clear()
        self.assertEqual(config.LLM_MODEL, "env-model")
        self.assertEqual(config.LLM_BASE_URL, "https://env.example/v1")
        self.assertFalse(public["has_key"])
        self.assertEqual(public["driven_by"], "env")

    def test_env_key_still_drives_status_when_nothing_is_saved(self) -> None:
        with self.use_env(PAPERPILOT_LLM_API_KEY="sk-from-env-9999"):
            public = llm_settings.public(llm_settings.apply())
            self.assertTrue(public["has_key"])
            self.assertEqual(public["key_source"], "env")
            self.assertTrue(config.LLM_ENABLED)


class ValidationTests(SettingsTestBase):
    def test_unknown_provider_and_purpose_are_rejected(self) -> None:
        with self.use_env():
            with self.assertRaises(llm_settings.LLMSettingsError):
                llm_settings.save({"provider": "gpt-9000"})
            with self.assertRaises(llm_settings.LLMSettingsError):
                llm_settings.save({"provider": "openai", "routes": {"not_a_purpose": "m"}})

    def test_custom_provider_requires_a_base_url_and_model(self) -> None:
        with self.use_env():
            with self.assertRaises(llm_settings.LLMSettingsError):
                llm_settings.save({"provider": "custom"})
            with self.assertRaises(llm_settings.LLMSettingsError):
                llm_settings.save({"provider": "custom", "base_url": LOCAL_BASE})
            public = llm_settings.save(
                {"provider": "custom", "base_url": LOCAL_BASE, "model": "qwen2.5-7b-instruct"})
        self.assertEqual(public["base_url"], LOCAL_BASE)
        self.assertEqual(public["model"], "qwen2.5-7b-instruct")

    def test_loopback_needs_the_private_opt_in(self) -> None:
        with self.use_env(PAPERPILOT_ALLOW_PRIVATE_LLM="0"):
            with self.assertRaises(llm_settings.LLMSettingsError):
                llm_settings.save({"provider": "custom", "base_url": LOCAL_BASE, "model": "x"})
        with self.use_env():
            self.assertIn(
                "127.0.0.1",
                llm_settings.save({"provider": "custom", "base_url": LOCAL_BASE, "model": "x"})["base_url"],
            )

    def test_metadata_addresses_are_rejected_without_opt_in(self) -> None:
        with self.use_env(PAPERPILOT_ALLOW_PRIVATE_LLM="0"):
            for url in (
                "http://api.example.org/v1",
                "https://169.254.169.254/latest",
                "https://user:pass@api.example.org/v1",
                "https://api.example.org:8443/v1",
            ):
                with self.assertRaises(llm_settings.LLMSettingsError):
                    llm_settings.save({"provider": "custom", "base_url": url, "model": "x"})

    def test_key_shape_and_number_bounds(self) -> None:
        with self.use_env():
            with self.assertRaises(llm_settings.LLMSettingsError):
                llm_settings.save({"provider": "openai", "api_key": "sk with space"})
            with self.assertRaises(llm_settings.LLMSettingsError):
                llm_settings.save({"provider": "openai", "api_key": "sk-" + "x" * 400})
            with self.assertRaises(llm_settings.LLMSettingsError):
                llm_settings.save({"provider": "openai", "timeout": 900})
            with self.assertRaises(llm_settings.LLMSettingsError):
                llm_settings.save({"provider": "openai", "retries": 9})

    def test_trailing_slash_is_normalised(self) -> None:
        with self.use_env():
            public = llm_settings.save({"provider": "custom", "base_url": LOCAL_BASE + "/", "model": "x"})
        self.assertEqual(public["base_url"], LOCAL_BASE)


class RoutingTests(SettingsTestBase):
    def test_per_purpose_routes_reach_the_gateway(self) -> None:
        with self.use_env():
            with patch.object(llm_settings.netguard, "validate_url", lambda url, **kw: url):
                llm_settings.save({
                    "provider": "deepseek",
                    "api_key": KEY,
                    "routes": {"assistant": "deepseek-reasoner"},
                })
        self.assertEqual(llm_gateway._model_for("assistant"), "deepseek-reasoner")
        self.assertEqual(llm_gateway._model_for("semantic_audit"), "deepseek-chat")
        self.assertEqual(llm_gateway.summary()["routes"], {"assistant": "deepseek-reasoner"})

    def test_switching_provider_drops_stale_routes(self) -> None:
        with self.use_env():
            with patch.object(llm_settings.netguard, "validate_url", lambda url, **kw: url):
                llm_settings.save({"provider": "deepseek", "routes": {"assistant": "deepseek-reasoner"}})
                public = llm_settings.save({"provider": "openai"})
        self.assertEqual(public["routes"], {})


class TestConnectionTests(SettingsTestBase):
    def test_ok_reply_reports_latency_and_model(self) -> None:
        with self.use_env():
            calls = []

            def poster(url, headers, payload):
                calls.append((url, headers, payload))
                return _FakeResponse()

            with patch.object(llm_settings.netguard, "validate_url", lambda url, **kw: url):
                result = llm_settings.test_connection(
                    {"provider": "deepseek", "model": "deepseek-chat", "api_key": KEY},
                    poster=poster,
                )
        self.assertTrue(result["ok"])
        self.assertEqual(result["reply"], "ok")
        self.assertEqual(result["model"], "stub-model")
        self.assertGreaterEqual(result["latency_ms"], 0)
        url, headers, payload = calls[0]
        self.assertEqual(url, "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer " + KEY)
        self.assertEqual(payload["model"], "deepseek-chat")
        self.assertLessEqual(payload["max_tokens"], 512)

    def test_http_failures_get_actionable_chinese_messages(self) -> None:
        cases = {
            401: "密钥被拒绝",
            404: "模型或接口地址不存在",
            429: "限流",
            500: "服务端错误",
        }
        with self.use_env():
            for status, needle in cases.items():
                with self.assertRaises(llm_settings.LLMSettingsError) as caught:
                    llm_settings.test_connection(
                        {"provider": "openai", "api_key": KEY},
                        poster=lambda url, headers, payload: _FakeResponse(status, {}),
                    )
                self.assertIn(needle, str(caught.exception))

    def test_non_openai_payload_is_reported(self) -> None:
        with self.use_env():
            with self.assertRaises(llm_settings.LLMSettingsError) as caught:
                llm_settings.test_connection(
                    {"provider": "openai", "api_key": KEY},
                    poster=lambda url, headers, payload: _FakeResponse(200, {"data": "unexpected"}),
                )
        self.assertIn("OpenAI 兼容", str(caught.exception))

    def test_environment_key_is_used_when_the_form_has_none(self) -> None:
        # v6.2.1: a key that comes from the environment must be usable for "测试连接".
        with self.use_env(PAPERPILOT_LLM_API_KEY="sk-from-env-1234",
                          PAPERPILOT_LLM_MODEL="env-model"):
            llm_settings.apply()
            seen = {}

            def poster(url, headers, payload):
                seen.update(headers)
                seen["model"] = payload.get("model")
                return _FakeResponse()

            result = llm_settings.test_connection({}, poster=poster)
        self.assertTrue(result["ok"])
        self.assertEqual(seen["Authorization"], "Bearer sk-from-env-1234")
        self.assertEqual(seen["model"], "env-model")

    def test_provider_error_message_is_surfaced_and_scrubbed(self) -> None:
        with self.use_env():
            with self.assertRaises(llm_settings.LLMSettingsError) as caught:
                llm_settings.test_connection(
                    {"provider": "openai", "api_key": KEY},
                    poster=lambda url, headers, payload: _FakeResponse(400, {"error": {
                        "code": "invalid_parameter",
                        "message": f"Unsupported model (key={KEY})",
                    }}),
                )
        message = str(caught.exception)
        self.assertIn("Unsupported model", message)
        self.assertNotIn(KEY, message)
        self.assertIn("sk-…", message)

    def test_delta_shaped_reply_is_accepted(self) -> None:
        with self.use_env():
            result = llm_settings.test_connection(
                {"provider": "openai", "api_key": KEY},
                poster=lambda url, headers, payload: _FakeResponse(
                    200, {"choices": [{"delta": {"content": "ok"}, "finish_reason": "length"}]}),
            )
        self.assertEqual(result["reply"], "ok")

    def test_empty_chunk_is_reported_as_incompatible(self) -> None:
        with self.use_env():
            with self.assertRaises(llm_settings.LLMSettingsError) as caught:
                llm_settings.test_connection(
                    {"provider": "openai", "api_key": KEY},
                    poster=lambda url, headers, payload: _FakeResponse(
                        200, {"choices": [{"delta": {"content": ""}, "finish_reason": "length"}]}),
                )
        message = str(caught.exception)
        self.assertIn("OpenAI 兼容", message)
        self.assertIn("空内容", message)

    def test_missing_key_or_bad_address_fail_before_the_network(self) -> None:
        def boom(*args):
            raise AssertionError("must not call the network")

        with self.use_env():
            with self.assertRaises(llm_settings.LLMSettingsError):
                llm_settings.test_connection({"provider": "openai"}, poster=boom)
        with self.use_env(PAPERPILOT_ALLOW_PRIVATE_LLM="0"):
            with self.assertRaises(llm_settings.LLMSettingsError):
                llm_settings.test_connection(
                    {"provider": "custom", "base_url": "https://169.254.169.254/latest",
                     "model": "x", "api_key": KEY},
                    poster=boom,
                )


class SettingsApiTests(SettingsTestBase):
    def setUp(self) -> None:
        super().setUp()
        self._data = patch.dict(os.environ, {"PAPERPILOT_DATA_DIR": str(self.root / "data")})
        self._data.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._data.stop()
        super().tearDown()

    def test_get_lists_providers_and_never_the_key(self) -> None:
        with self.use_env():
            body = self.client.get("/api/llm/settings").json()
        settings = body["settings"]
        self.assertEqual(len(settings["providers"]), len(llm_settings.PROVIDERS))
        self.assertFalse(settings["has_key"])
        self.assertNotIn("api_key", settings)
        self.assertIn("calls", body)

    def test_post_then_meta_and_health_reflect_the_new_model(self) -> None:
        with self.use_env():
            with patch.object(llm_settings.netguard, "validate_url", lambda url, **kw: url):
                saved = self.client.post("/api/llm/settings", json={
                    "provider": "deepseek", "api_key": KEY, "model": "deepseek-chat",
                })
                self.assertEqual(saved.status_code, 200)
                self.assertNotIn(KEY, saved.text)
                meta = self.client.get("/api/meta").json()
                health = self.client.get("/api/health").json()
            self.assertEqual(meta["llm"]["model"], "deepseek-chat")
            self.assertTrue(meta["llm"]["enabled"])
            self.assertEqual(meta["llm_settings"]["masked_key"], "••••" + KEY[-4:])
            self.assertIn("custom_llm", meta["features"])
            self.assertEqual(health["mode"], "online")
            self.assertTrue(health["llm_enabled"])

    def test_post_rejects_a_private_url_when_opt_in_is_off(self) -> None:
        with self.use_env(PAPERPILOT_ALLOW_PRIVATE_LLM="0"):
            response = self.client.post("/api/llm/settings", json={
                "provider": "custom", "base_url": LOCAL_BASE, "model": "x",
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn("安全要求", response.json()["detail"])

    def test_test_endpoint_uses_a_draft_key_without_persisting_it(self) -> None:
        with self.use_env():
            calls = []

            def poster(url, headers, payload):
                calls.append((url, headers, payload))
                return _FakeResponse()

            with patch.object(llm_settings, "_default_poster", poster):
                response = self.client.post("/api/llm/settings/test", json={
                    "provider": "deepseek", "api_key": KEY, "model": "deepseek-chat",
                })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["reply"], "ok")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1]["Authorization"], "Bearer " + KEY)
            self.assertFalse((self.root / "llm_settings.secrets.json").exists())

    def test_test_endpoint_surfaces_http_errors_as_400(self) -> None:
        with self.use_env():
            with patch.object(
                llm_settings, "_default_poster",
                lambda url, headers, payload: _FakeResponse(401, {}),
            ):
                response = self.client.post("/api/llm/settings/test", json={
                    "provider": "openai", "api_key": KEY, "model": "gpt-4o-mini",
                })
        self.assertEqual(response.status_code, 400)
        self.assertIn("密钥被拒绝", response.json()["detail"])

    def test_clear_endpoint_returns_to_offline(self) -> None:
        with self.use_env():
            with patch.object(llm_settings.netguard, "validate_url", lambda url, **kw: url):
                self.client.post("/api/llm/settings", json={"provider": "openai", "api_key": KEY})
                self.assertTrue(self.client.get("/api/health").json()["llm_enabled"])
                body = self.client.post("/api/llm/settings/clear", json={}).json()
        self.assertEqual(body["settings"]["mode"], "offline")
        self.assertFalse(config.LLM_ENABLED)
        self.assertFalse((self.root / "llm_settings.json").exists())


if __name__ == "__main__":
    unittest.main()
