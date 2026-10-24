from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import requests

from app import config
from app import llm


def _response(status: int, payload: dict | None = None) -> Mock:
    response = Mock()
    response.status_code = status
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
    else:
        response.raise_for_status.return_value = None
    response.json.return_value = payload or {}
    return response


class LLMClientTests(unittest.TestCase):
    @patch("app.llm.time.sleep")
    @patch("app.llm.requests.post")
    @patch.object(config, "LLM_ENABLED", True)
    def test_retries_server_error(self, post, sleep) -> None:
        bad = _response(500)
        good = _response(200, {"choices": [{"message": {"content": "ok"}}]})
        post.side_effect = [bad, good]
        result = llm.chat([{"role": "user", "content": "x"}], retries=1)
        self.assertEqual(result, "ok")
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once()

    @patch("app.llm.requests.post")
    @patch.object(config, "LLM_ENABLED", True)
    def test_does_not_retry_client_error(self, post) -> None:
        post.return_value = _response(401)
        with self.assertRaises(llm.LLMHTTPError) as caught:
            llm.chat([{"role": "user", "content": "x"}], retries=3)
        self.assertEqual(caught.exception.status, 401)
        self.assertEqual(post.call_count, 1)

    @patch("app.llm.requests.post")
    @patch.object(config, "LLM_ENABLED", True)
    def test_embed_returns_vectors(self, post) -> None:
        post.return_value = _response(
            200,
            {"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]},
        )
        result = llm.embed(["a", "b"])
        self.assertEqual(result, [[0.1, 0.2], [0.3, 0.4]])
        self.assertEqual(post.call_count, 1)

    @patch("app.llm.requests.post")
    @patch.object(config, "LLM_ENABLED", True)
    def test_embed_failure_returns_none(self, post) -> None:
        post.return_value = _response(500)
        self.assertIsNone(llm.embed(["a"]))

    @patch.object(config, "LLM_ENABLED", True)
    def test_status_does_not_expose_key(self) -> None:
        status = llm.status()
        self.assertTrue(status["enabled"])
        self.assertNotIn("api_key", status)
        self.assertIn("embeddings", status)
        self.assertIn("model", status)

    @patch.object(config, "LLM_ENABLED", False)
    def test_disabled_chat_raises(self) -> None:
        with self.assertRaises(llm.LLMUnavailable):
            llm.chat([{"role": "user", "content": "x"}])


    @patch("app.llm.requests.post")
    @patch.object(config, "LLM_ENABLED", True)
    def test_rate_limit_raises_a_typed_error_with_provider_detail(self, post) -> None:
        post.return_value = _response(429, {"error": {
            "type": "insufficient_quota",
            "message": "You exceeded your current quota, key sk-live-abcd1234",
        }})
        with self.assertRaises(llm.LLMHTTPError) as caught:
            llm.chat([{"role": "user", "content": "x"}], retries=3)
        self.assertEqual(caught.exception.status, 429)
        self.assertIn("insufficient_quota", caught.exception.detail)
        self.assertIn("quota", caught.exception.detail)
        self.assertNotIn("sk-live-abcd1234", caught.exception.detail)
        self.assertEqual(post.call_count, 1)


class ExtractContentTests(unittest.TestCase):
    def test_reads_message_content(self) -> None:
        self.assertEqual(
            llm.extract_content({"choices": [{"message": {"content": " hello "}}]}), "hello"
        )

    def test_accepts_a_truncated_delta_chunk(self) -> None:
        # ModelScope's gateway answers a token-capped call with a single delta chunk.
        self.assertEqual(
            llm.extract_content({"choices": [{"delta": {"content": "ok"}, "finish_reason": "length"}]}),
            "ok",
        )

    def test_empty_or_missing_content_is_an_explicit_error(self) -> None:
        for payload in (
            {},
            {"choices": []},
            {"choices": [{"message": {"content": "   "}}]},
            {"choices": [{"delta": {"content": None}}]},
        ):
            with self.assertRaises(llm.LLMBadResponse):
                llm.extract_content(payload)

    def test_reasoning_exhausted_empty_reply_is_marked(self) -> None:
        payload = {
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
            "usage": {
                "completion_tokens": 2600,
                "completion_tokens_details": {"reasoning_tokens": 2600},
            },
        }
        with self.assertRaises(llm.LLMBadResponse) as raised:
            llm.extract_content(payload)
        self.assertTrue(raised.exception.reasoning_exhausted)

    def test_extract_json_tolerates_raw_newlines_in_model_strings(self) -> None:
        raw = '{"problem": "line one' + chr(10) + 'line two", "method": "ok"}'
        payload = llm.extract_json(raw)
        self.assertEqual(payload["problem"], "line one" + chr(10) + "line two")

    @patch("app.llm.requests.post")
    @patch.object(config, "LLM_ENABLED", True)
    def test_chat_returns_text_from_a_delta_shaped_reply(self, post) -> None:
        post.return_value = _response(200, {"choices": [{"delta": {"content": "ok"}}]})
        self.assertEqual(llm.chat([{"role": "user", "content": "x"}]), "ok")

    @patch("app.llm.requests.post")
    @patch.object(config, "LLM_ENABLED", True)
    def test_chat_raises_on_an_empty_reply_without_retrying(self, post) -> None:
        post.return_value = _response(200, {"choices": [{"delta": {"content": ""}}]})
        with self.assertRaises(llm.LLMBadResponse):
            llm.chat([{"role": "user", "content": "x"}], retries=3)
        self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
