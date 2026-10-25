from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from app import config
from app.services import llm_gateway, translation


def _paper(text: str = "We propose a fast quantization method for LLM inference.") -> dict:
    return {
        "id": "upload-test123",
        "title": "Test Paper",
        "authors": ["A. Author"],
        "year": 2026,
        "abstract": text,
        "sections": [
            {"title": "Abstract", "page": 1, "text": text},
            {"title": "Introduction", "page": 1, "text": text + " The code is public."},
        ],
    }


class TranslationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_section_catalog_uses_abstract_and_deduplicates(self) -> None:
        paper = _paper("An abstract about quantization.")
        paper["sections"] = [
            {"title": "Abstract", "page": 1, "text": "An abstract about quantization."},
            {"title": "Introduction", "page": 1, "text": "Intro text."},
        ]
        catalog = translation.section_catalog(paper)
        self.assertEqual([item["title"] for item in catalog], ["Abstract", "Introduction"])
        self.assertEqual([item["index"] for item in catalog], [0, 1])
        self.assertEqual(catalog[0]["text"], "An abstract about quantization.")

    def test_shield_and_restore_preserve_formula_url_and_citation(self) -> None:
        source = "The macro uses $V_{DD}$ at 65 nm [12]; see https://example.org/paper."
        shielded, tokens = translation.shield_text(source)
        self.assertNotIn("$V_{DD}$", shielded)
        self.assertNotIn("https://example.org/paper", shielded)
        self.assertGreaterEqual(len(tokens), 2)
        restored, missing = translation.restore_text(shielded, tokens)
        self.assertEqual(restored, source)
        self.assertEqual(missing, 0)

    def test_split_text_respects_limit_and_preserves_content(self) -> None:
        text = "Sentence one is here. Sentence two is longer. " * 8
        chunks = translation.split_text(text.strip(), 90)
        self.assertTrue(chunks)
        self.assertTrue(all(len(chunk) <= 90 for chunk in chunks))
        self.assertEqual(" ".join(chunks).split(), text.split())

    def test_public_translation_is_truncated_cached_and_source_aligned(self) -> None:
        calls: list[str] = []

        def fake_public(text: str, source_lang: str, target_lang: str) -> str:
            calls.append(text)
            return "译文：" + text

        result = translation.translate_section(
            _paper("A long abstract with enough words to require truncation. " * 8),
            0,
            provider="public",
            cache_dir=self.cache_dir,
            public_fetch=fake_public,
            public_max_chars=120,
        )
        self.assertEqual(result["provider"], "public")
        self.assertTrue(result["truncated"])
        self.assertLessEqual(result["translated_source_chars"], 120)
        self.assertTrue(result["translation"].startswith("译文："))
        self.assertTrue((self.cache_dir / "upload-test123-zh.json").exists())
        first_calls = len(calls)
        self.assertGreater(first_calls, 0)

        again = translation.translate_section(
            _paper("A long abstract with enough words to require truncation. " * 8),
            0,
            provider="public",
            cache_dir=self.cache_dir,
            public_fetch=fake_public,
            public_max_chars=120,
        )
        self.assertTrue(again["cached"])
        self.assertEqual(len(calls), first_calls)

    def test_public_quota_error_is_surfaced(self) -> None:
        def quota_error(text: str, source_lang: str, target_lang: str) -> str:
            raise translation.ProviderUnavailable("公共翻译额度已用完，请配置模型或稍后再试。")

        with self.assertRaises(translation.ProviderUnavailable) as ctx:
            translation.translate_section(
                _paper(),
                0,
                provider="public",
                cache_dir=self.cache_dir,
                public_fetch=quota_error,
            )
        self.assertIn("额度", str(ctx.exception))

    def test_llm_translation_covers_full_section_and_marks_provider(self) -> None:
        def fake_llm(system: str, user: str) -> str:
            return json.dumps(
                {"segments": [{"id": 0, "translation": "我们提出一种快速量化方法。"}]},
                ensure_ascii=False,
            )

        result = translation.translate_section(
            _paper("We propose a fast quantization method for LLM inference."),
            0,
            provider="llm",
            cache_dir=self.cache_dir,
            llm_call=fake_llm,
        )
        self.assertEqual(result["provider"], "llm")
        self.assertFalse(result["truncated"])
        self.assertIn("量化", result["translation"])
        self.assertEqual(result["segments"][0]["translation"], "我们提出一种快速量化方法。")


    @patch.object(config, "LLM_ENABLED", True)
    @patch(
        "app.llm.chat",
        return_value=json.dumps(
            {"segments": [{"id": 0, "translation": "我们提出一种快速量化方法。"}]},
            ensure_ascii=False,
        ),
    )
    def test_default_llm_translation_goes_through_the_gateway(self, chat) -> None:
        llm_gateway.reset_audit()
        llm_gateway.reset_cache()
        result = translation.translate_section(
            _paper("We propose a fast quantization method for LLM inference."),
            0,
            provider="llm",
            cache_dir=self.cache_dir,
        )
        self.assertEqual(result["provider"], "llm")
        self.assertEqual(chat.call_count, 1)
        self.assertIn("translation_full", llm_gateway.summary()["by_purpose"])

    @patch.object(config, "LLM_ENABLED", True)
    @patch("app.llm.chat", side_effect=RuntimeError("连接超时"))
    def test_default_llm_translation_failure_is_surfaced_and_audited(self, chat) -> None:
        llm_gateway.reset_audit()
        llm_gateway.reset_cache()
        with self.assertRaises(translation.TranslationError):
            translation.translate_section(
                _paper("We propose a fast quantization method for LLM inference."),
                0,
                provider="llm",
                cache_dir=self.cache_dir,
            )
        entries = llm_gateway.audit_log()
        self.assertTrue(entries and not entries[-1]["ok"])
        self.assertTrue(entries[-1]["degrade_reason"])

    def test_cache_write_failure_does_not_break_translation(self) -> None:
        def fake_public(text: str, source_lang: str, target_lang: str) -> str:
            return "译文：" + text

        blocked = self.cache_dir / "not-a-directory"
        blocked.write_text("x", encoding="utf-8")
        result = translation.translate_section(
            _paper("A short abstract."),
            0,
            provider="public",
            cache_dir=blocked,
            public_fetch=fake_public,
        )
        self.assertTrue(result["translation"].startswith("译文："))
        self.assertFalse(result["cached"])

    def test_cache_invalidates_when_source_changes(self) -> None:
        calls = {"count": 0}

        def fake_public(text: str, source_lang: str, target_lang: str) -> str:
            calls["count"] += 1
            return "译文：" + text

        translation.translate_section(
            _paper("Original abstract text."),
            0,
            provider="public",
            cache_dir=self.cache_dir,
            public_fetch=fake_public,
        )
        translation.translate_section(
            _paper("Changed abstract text."),
            0,
            provider="public",
            cache_dir=self.cache_dir,
            public_fetch=fake_public,
        )
        self.assertGreaterEqual(calls["count"], 2)

    def test_paper_payload_reports_cached_sections_and_glossary(self) -> None:
        def fake_public(text: str, source_lang: str, target_lang: str) -> str:
            return "译文：" + text

        paper = _paper("The ADC uses a SAR architecture for CIM.")
        translation.translate_section(
            paper,
            0,
            provider="public",
            cache_dir=self.cache_dir,
            public_fetch=fake_public,
        )
        payload = translation.paper_payload(
            paper,
            target="zh",
            cache_dir=self.cache_dir,
            provider="public",
        )
        self.assertEqual(payload["translated_sections"], 1)
        self.assertTrue(payload["sections"][0]["cached"])
        self.assertEqual(payload["sections"][0]["index"], 0)
        self.assertTrue(any(item["term"] == "ADC" for item in payload["glossary_hits"]))


if __name__ == "__main__":
    unittest.main()
