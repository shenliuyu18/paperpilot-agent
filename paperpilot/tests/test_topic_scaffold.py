from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services import analysis, research, search


class TopicScaffoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.paper_ids = [paper["id"] for paper in search.PAPERS[:2]]
        cls.result = research.topic_studio(
            "面向存算一体的低功耗 ADC",
            scope="selected",
            paper_ids=cls.paper_ids,
        )

    def test_every_candidate_has_four_dimension_feasibility(self) -> None:
        questions = self.result["questions"]
        self.assertTrue(questions)
        for question in questions:
            detail = question["feasibility_detail"]
            self.assertEqual(detail["verdict"] in {"可开题", "需收窄", "风险偏高"}, True)
            self.assertEqual(len(detail["dimensions"]), 4)
            self.assertTrue(1 <= detail["overall"] <= 5)
            for item in detail["dimensions"]:
                self.assertTrue(1 <= item["score"] <= 5)
                self.assertTrue(item["label"])
                self.assertTrue(item["reason"])

    def test_top_candidate_has_argument_card_and_evidence(self) -> None:
        top = self.result["questions"][0]
        card = top["argument_card"]
        for key in ("claim", "gap", "evidence", "method", "data", "metric", "success_criterion", "failure_mode"):
            self.assertIn(key, card)
        self.assertEqual(card["claim"], top["question"])
        self.assertTrue(card["evidence"])
        self.assertEqual(card["data"], self.result["experiment_plan"]["datasets"][:3])
        self.assertTrue(card["success_criterion"])
        self.assertTrue(top["evidence_papers"])

    def test_advisor_brief_has_options_and_anticipated_questions(self) -> None:
        advisor = self.result["advisor"]
        self.assertGreaterEqual(len(advisor["questions"]), 3)
        self.assertGreaterEqual(len(advisor["anticipated"]), 3)
        for item in advisor["questions"]:
            self.assertGreaterEqual(len(item["options"]), 2)
            self.assertTrue(item["why"])
        for item in advisor["anticipated"]:
            self.assertTrue(item["question"])
            self.assertTrue(item["preparation"])
        self.assertIn("导师", advisor["one_pager"])
        self.assertTrue(advisor["ask_template"])

    def test_topic_studio_keeps_legacy_scores_and_note(self) -> None:
        self.assertIn("feasibility", self.result["questions"][0])
        self.assertIn("novelty", self.result["questions"][0])
        self.assertIn("note", self.result)
        self.assertIn("argument_cards", self.result)

    def test_non_hardware_topic_does_not_reuse_adc_clusters(self) -> None:
        paper = {
            "id": "rag-live-1",
            "title": "Retrieval-Augmented Generation for Domain Question Answering",
            "abstract": (
                "Large language models hallucinate in domain question answering. "
                "We evaluate a retrieval-augmented generation pipeline on a professional corpus "
                "and report answer correctness and faithfulness."
            ),
            "tldr": "",
            "metrics": {},
            "results": [],
            "limitations": [],
            "sections": [],
            "year": 2025,
        }
        with patch.object(research, "_scope_papers", return_value=[paper]):
            result = research.topic_studio(
                "retrieval augmented generation question answering",
                "selected",
                [paper["id"]],
            )
        text = " ".join(
            [
                result["brief"]["thesis"],
                *result["brief"]["contributions"],
                result["brief"]["scope"],
                *[item["label"] for item in result["clusters"]],
                *[name for name in result["experiment_plan"]["datasets"]],
                *[name for name in result["experiment_plan"]["metrics"]],
            ]
        ).lower()
        for term in ("adc", "cim", "存算", "推理系统/边缘部署", "数字/近似计算", "bert"):
            self.assertNotIn(term, text)
        self.assertEqual(result["clusters"][0]["label"], "相关工作")
        self.assertEqual(result["brief"]["keywords"], ["retrieval augmented generation question answering"])
        self.assertTrue(result["questions"])
        self.assertTrue(result["questions"][0]["title"])
        self.assertIn("证据边界", result["brief"]["thesis"])

    def test_unknown_topic_gaps_fall_back_to_generic_questions(self) -> None:
        result = analysis.gaps("retrieval augmented generation question answering")
        self.assertTrue(result["fallback"])
        self.assertTrue(result["gaps"])
        text = " ".join(item["title"] + item["question"] + item["why_gap"] for item in result["gaps"])
        for term in ("ADC", "CIM", "存算", "工艺角"):
            self.assertNotIn(term, text)


class RelatedWorkTextTests(unittest.TestCase):
    def test_related_work_truncates_at_boundary_without_double_punctuation(self) -> None:
        long_text = "第一句很短。" + "这是一段足够长的模型输出，中间包含英文 quote \"The early stopping mechanism ac"
        profile = {
            "cite": "P1",
            "cluster": "神经形态/稀疏",
            "paper": {
                "id": "paper-1",
                "title": "Paper One",
                "abstract": long_text,
                "results": [{"name": "Top-1", "value": "77.3%"}],
            },
            "read": {"problem": long_text, "method": long_text},
            "limitations": [{"limitation": "【作者承认/数据条件】" + long_text, "note": "p.3"}],
        }
        body = research._related_work("低功耗 ADC", [profile])
        text = body["sections"][0]["paragraphs"][0]
        self.assertNotIn("…。", text)
        self.assertNotIn("【作者承认/数据条件】", text)
        self.assertNotIn("p.3", text)
        self.assertNotIn("{'", text)

    def test_related_work_does_not_duplicate_single_paper_opener(self) -> None:
        profile = {
            "cite": "P1",
            "cluster": "ADC 近存转换",
            "paper": {"id": "paper-1", "title": "Paper One", "abstract": "问题", "results": []},
            "read": {"problem": "读出能耗", "method": "SAR skipping"},
            "limitations": ["未测工艺角"],
        }
        body = research._related_work("低功耗 ADC", [profile])
        section = body["sections"][0]
        self.assertEqual(len(section["paragraphs"]), 1)
        self.assertIn("P1", section["paragraphs"][0])


    def test_related_work_strips_connectives_the_template_already_supplies(self) -> None:
            profile = {
                "cite": "P1",
                "cluster": "ADC 近存转换",
                "paper": {
                    "id": "paper-1",
                    "title": "Paper One",
                    "abstract": "问题",
                    "results": [{"name": "精度", "value": "97.2%"}],
                },
                "read": {
                    "problem": "针对模拟 CIM 的 ADC 开销问题",
                    "method": "采用了 twin 9T 位单元与 multi-VDD",
                },
                "limitations": ["未测工艺角"],
            }
            body = research._related_work("低功耗 ADC", [profile])
            text = body["sections"][0]["paragraphs"][0]
            for repeated in ("针对针对", "采用采用", "采用采用了", "报告报告"):
                self.assertNotIn(repeated, text)
            self.assertIn("采用 twin 9T 位单元", text)


if __name__ == "__main__":
    unittest.main()
