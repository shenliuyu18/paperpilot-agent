from __future__ import annotations

import unittest

from app.services import argument


DRAFT = """# 引言

本文研究存算一体中低功耗 ADC 的设计问题，旨在验证能效与精度之间的权衡。
现有工作尚未在跨工艺条件下给出完整验证，这一缺口直接影响可部署性。

## 方法

本文提出一种基于跳比较的读出框架，将比较步数作为可调参数。

## 实验

在 CIFAR-10 上开展实验，结果表明该方案降低了 ADC 能耗，但没有报告所有工艺角。
"""


def _supported(sentence: str) -> dict:
    return {
        "sentence": sentence,
        "level": "supported",
        "label": "直接支持",
        "overlap": 0.7,
        "citation": {"paper_id": "p1", "title": "Evidence", "section": "Results", "page": 3, "quote": "quote"},
        "citations": [{"paper_id": "p1", "title": "Evidence", "section": "Results", "page": 3, "quote": "quote"}],
        "issues": [],
        "suggestion": "可保留。",
    }


def _unsupported(sentence: str) -> dict:
    return {
        "sentence": sentence,
        "level": "unsupported",
        "label": "证据不足",
        "overlap": 0.02,
        "citation": None,
        "citations": [],
        "issues": ["当前范围内没有检索到可引用的匹配证据"],
        "suggestion": "补充引用或缩小论断。",
    }


class StructureReviewTests(unittest.TestCase):
    def test_detects_present_and_missing_argument_sections(self) -> None:
        result = argument.structure_review(DRAFT, topic="存算一体低功耗 ADC")
        checks = {item["id"]: item for item in result["checks"]}
        self.assertEqual(checks["question"]["status"], "present")
        self.assertEqual(checks["gap"]["status"], "present")
        self.assertEqual(checks["method"]["status"], "present")
        self.assertEqual(checks["evidence"]["status"], "present")
        self.assertEqual(checks["conclusion"]["status"], "missing")
        self.assertTrue(0 < result["score"] < 100)
        self.assertTrue(any(issue["id"] == "conclusion" for issue in result["issues"]))
        self.assertEqual(result["counts"]["headings"], 3)

    def test_empty_draft_gets_low_score_and_high_severity_issues(self) -> None:
        result = argument.structure_review("", topic="test")
        self.assertEqual(result["score"], 0)
        self.assertTrue(result["issues"])
        self.assertTrue(all(issue["severity"] == "high" for issue in result["issues"]))
        self.assertEqual(result["verdict"], "结构缺口明显")

    def test_section_map_counts_citations(self) -> None:
        text = "# 方法\n本文提出一种框架 [P1]。\n\n# 实验\n在 CIFAR-10 上实验，结果表明提升 3.2% [P1]。"
        result = argument.structure_review(text)
        self.assertEqual(len(result["sections"]), 2)
        self.assertEqual(result["counts"]["citations"], 2)
        self.assertEqual(result["sections"][0]["title"], "方法")


class ArgumentReviewTests(unittest.TestCase):
    def _checker(self, results):
        def fake(text, scope, paper_ids):
            return {
                "scope": scope,
                "scope_label": "测试范围",
                "summary": {
                    "total": len(results),
                    "supported": sum(1 for item in results if item["level"] == "supported"),
                    "partial": 0,
                    "unsupported": sum(1 for item in results if item["level"] == "unsupported"),
                    "quantity_issues": 0,
                    "overall": "mixed",
                    "overall_label": "需要补充引用",
                },
                "results": results,
                "citations": [],
                "papers_considered": 2,
                "note": "test",
            }
        return fake

    def test_unsupported_claims_are_prioritized_as_actions(self) -> None:
        results = [
            _supported("本文研究存算一体中低功耗 ADC 的设计问题。"),
            _unsupported("该方案已在所有工艺角下得到硅验证。"),
            _unsupported("该方案在所有数据集上取得 99.9% 的能效提升。"),
        ]
        review = argument.argument_review(
            DRAFT,
            topic="存算一体低功耗 ADC",
            scope="selected",
            paper_ids=["p1"],
            claim_checker=self._checker(results),
        )
        self.assertEqual(len(review["unsupported"]), 2)
        self.assertTrue(review["actions"])
        self.assertTrue(any("工艺角" in action for action in review["actions"]))
        self.assertGreaterEqual(len(review["argument_map"]), 1)
        self.assertTrue(0 <= review["score"] <= 100)
        self.assertEqual(review["claims"]["summary"]["total"], 3)

    def test_supported_claims_score_higher_than_unsupported(self) -> None:
        supported = argument.argument_review(
            DRAFT,
            claim_checker=self._checker([_supported("本文研究存算一体中低功耗 ADC 的设计问题。")]),
        )
        unsupported = argument.argument_review(
            DRAFT,
            claim_checker=self._checker([_unsupported("该方案已在所有工艺角下得到硅验证。")]),
        )
        self.assertGreater(supported["score"], unsupported["score"])
        self.assertEqual(supported["verdict"], "论证较完整") if supported["score"] >= 80 else None


if __name__ == "__main__":
    unittest.main()
