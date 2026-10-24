from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app import config, llm
from app.services import analysis, llm_gateway


PAPER = {
    "id": "upload-test",
    "title": "Uploaded CIM Paper",
    "abstract": "This work presents a CIM macro with 0.8 pJ/SOP.",
    "tldr": "",
    "sections": [{"title": "Abstract", "page": 1, "text": "This work presents a CIM macro."}],
    "source": "upload",
    "full_text": True,
}


class OnlineReadTranslateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPERPILOT_DATA_DIR": self.tmp.name})
        self.env.start()
        llm_gateway.reset_audit()
        llm_gateway.reset_cache()
        analysis.clear_read_cache()

    def tearDown(self) -> None:
        llm_gateway.reset_audit()
        llm_gateway.reset_cache()
        analysis.clear_read_cache()
        self.env.stop()
        self.tmp.cleanup()

    @patch.object(config, "LLM_ENABLED", True)
    @patch.object(llm, "chat")
    def test_read_uses_the_audited_gateway(self, chat) -> None:
        chat.return_value = json.dumps({
            "tldr": "模型摘要",
            "problem": "读出能耗",
            "method": "模拟 CIM",
            "novelty": ["低功耗"],
            "limitations": ["仿真"],
            "reviewer_questions": ["是否有硅测？"],
        }, ensure_ascii=False)
        result = analysis.read_paper(dict(PAPER), "5min")
        self.assertEqual(result["extraction_mode"], "llm")
        self.assertIn("模型", result["note"])
        self.assertEqual(result["tldr"], "模型摘要")
        summary = llm_gateway.summary()
        self.assertIn("read", summary["by_purpose"])
        self.assertEqual(summary["failed"], 0)

    @patch.object(config, "LLM_ENABLED", True)
    @patch.object(llm_gateway, "audited_chat_json")
    def test_read_trims_extra_model_bullets_instead_of_failing(self, audited) -> None:
        audited.return_value = ({
            "tldr": "模型摘要", "problem": "问题", "method": "方法",
            "novelty": [f"创新 {i}" for i in range(8)],
            "limitations": [f"局限 {i}" for i in range(8)],
            "reviewer_questions": [f"问题 {i}" for i in range(8)],
        }, {"status": "ok"})
        result = analysis.read_paper(dict(PAPER), "5min")
        self.assertEqual(result["extraction_mode"], "llm")
        self.assertEqual(len(result["reviewer_questions"]), 6)
        self.assertEqual(len(result["novelty"]), 6)

    @patch.object(config, "LLM_ENABLED", True)
    @patch.object(llm_gateway, "audited_chat_json")
    def test_read_coerces_structured_fields_to_plain_text(self, audited) -> None:
        audited.return_value = ({
            "tldr": {"text": "模型摘要", "confidence": "high"},
            "problem": {"text": "读出能耗", "confidence": "high"},
            "method": {"text": "模拟 CIM 读出"},
            "novelty": [{"text": "低功耗"}, "可重构"],
            "limitations": [{"text": "仅仿真"}, {"text": "缺少硅测"}],
            "reviewer_questions": [{"text": "是否有硅测？"}],
        }, {"status": "ok"})
        result = analysis.read_paper(dict(PAPER), "5min")
        self.assertEqual(result["extraction_mode"], "llm")
        self.assertEqual(result["tldr"], "模型摘要")
        self.assertEqual(result["problem"], "读出能耗")
        self.assertEqual(result["method"], "模拟 CIM 读出")
        self.assertEqual(result["novelty"], ["低功耗", "可重构"])
        self.assertEqual(result["limitations"], ["仅仿真", "缺少硅测"])
        for key in ("tldr", "problem", "method"):
            self.assertNotIn("{", result[key])
        for key in ("novelty", "limitations", "reviewer_questions"):
            self.assertTrue(all("{" not in item for item in result[key]))

    @patch.object(config, "LLM_ENABLED", True)
    @patch.object(llm_gateway, "audited_chat_json")
    def test_read_prefers_domain_content_over_metadata(self, audited) -> None:
        audited.return_value = ({
            "tldr": {"summary": "模型摘要"},
            "problem": {"description": "读出能耗"},
            "method": {"content": "模拟 CIM 读出"},
            "novelty": [{"claim": "低功耗", "confidence": "high"}],
            "limitations": [{"limitation": "只有仿真，缺少硅测验证", "note": "p.3"}],
            "reviewer_questions": [{"question": "是否有硅测？", "why": "仅仿真"}],
        }, {"status": "ok"})
        result = analysis.read_paper(dict(PAPER), "5min")
        self.assertEqual(result["tldr"], "模型摘要")
        self.assertEqual(result["problem"], "读出能耗")
        self.assertEqual(result["method"], "模拟 CIM 读出")
        self.assertEqual(result["novelty"], ["低功耗"])
        self.assertEqual(result["limitations"], ["只有仿真，缺少硅测验证"])
        self.assertEqual(result["reviewer_questions"], ["是否有硅测？"])

    @patch.object(config, "LLM_ENABLED", False)
    def test_non_llm_read_also_coerces_structured_fields(self) -> None:
        paper = {
            **PAPER,
            "curated": True,
            "tldr": {"text": "演示摘要", "confidence": "high"},
            "problem": {"problem": "读出能耗"},
            "method": {"method": "模拟 CIM"},
            "novelty": [{"claim": "低功耗", "confidence": "high"}],
            "limitations": [{"limitation": "只有仿真", "note": "p.3"}],
            "reviewer_questions": [{"question": "是否有硅测？"}],
        }
        result = analysis.read_paper(paper, "5min")
        self.assertEqual(result["extraction_mode"], "curated")
        self.assertEqual(result["tldr"], "演示摘要")
        self.assertEqual(result["problem"], "读出能耗")
        self.assertEqual(result["method"], "模拟 CIM")
        self.assertEqual(result["novelty"], ["低功耗"])
        self.assertEqual(result["limitations"], ["只有仿真"])
        self.assertEqual(result["reviewer_questions"], ["是否有硅测？"])

    @patch.object(config, "LLM_ENABLED", True)
    @patch.object(llm_gateway, "audited_chat_json")
    def test_read_cache_does_not_mutate_shared_paper(self, audited) -> None:
        audited.return_value = ({
            "tldr": "模型摘要", "problem": "问题", "method": "方法",
            "novelty": ["创新"], "limitations": ["局限"], "reviewer_questions": ["问题"],
        }, {"status": "ok"})
        paper = dict(PAPER)
        first = analysis.read_paper(paper, "5min")
        second = analysis.read_paper(paper, "tldr")
        self.assertEqual(first["tldr"], "模型摘要")
        self.assertEqual(second["tldr"], "模型摘要")
        self.assertNotIn("_llm_read", paper)
        self.assertEqual(audited.call_count, 1)

    @patch.object(config, "LLM_ENABLED", True)
    @patch.object(llm, "chat")
    def test_translation_uses_the_audited_gateway(self, chat) -> None:
        chat.return_value = json.dumps({
            "translation": [{"en": "This work achieves 0.8 pJ/SOP.", "zh": "该工作实现 0.8 pJ/SOP。"}],
        }, ensure_ascii=False)
        result = analysis.translate(text="This work achieves 0.8 pJ/SOP.")
        self.assertEqual(result["source"], "llm")
        self.assertEqual(result["translation"][0]["zh"], "该工作实现 0.8 pJ/SOP。")
        summary = llm_gateway.summary()
        self.assertIn("translation", summary["by_purpose"])
        self.assertEqual(summary["failed"], 0)


if __name__ == "__main__":
    unittest.main()