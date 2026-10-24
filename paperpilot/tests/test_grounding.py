from __future__ import annotations

import unittest

from app.services import grounding


def _passages() -> list[dict]:
    return [
        {"index": 1, "text": "The prototype consumes 4.2 fJ per conversion."},
        {"index": 2, "text": "吞吐提升 1.8 倍。"},
    ]


class GroundingTests(unittest.TestCase):
    def test_cited_numbers_are_grounded(self) -> None:
        report = grounding.validate_answer(
            "原型功耗为 4.2 fJ [1]，吞吐提升 1.8 倍 [2]。",
            _passages(),
        )
        self.assertEqual(report["status"], "grounded")
        self.assertEqual(report["cited"], 2)
        self.assertEqual(report["out_of_range"], 0)
        self.assertEqual(report["unsupported_numbers"], [])
        self.assertEqual(report["unsupported_sentences"], [])

    def test_out_of_range_citation_is_invalid(self) -> None:
        report = grounding.validate_answer("根据证据 [9]，结论成立。", _passages())
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["out_of_range"], 1)
        self.assertEqual(report["out_of_range_indices"], [9])

    def test_uncited_numeric_claim_is_partial(self) -> None:
        report = grounding.validate_answer("该方案的能耗降低了 42%。", _passages())
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["unsupported_numbers"], [])
        self.assertEqual(len(report["unsupported_sentences"]), 1)

    def test_number_missing_from_cited_passage_is_partial(self) -> None:
        report = grounding.validate_answer("该方案的能耗降低了 42% [1]。", _passages())
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["unsupported_numbers"][0]["missing"], ["42"])

    def test_insufficient_flag_wins(self) -> None:
        report = grounding.validate_answer(
            "现有证据不足，无法判断。", _passages(), insufficient=True
        )
        self.assertEqual(report["status"], "insufficient")
        self.assertEqual(report["cited"], 0)

    def test_quote_supported_normalizes_whitespace_and_punctuation(self) -> None:
        self.assertTrue(
            grounding.quote_supported("4.2 fJ per conversion", "The prototype consumes 4.2 fJ per conversion.")
        )
        self.assertFalse(grounding.quote_supported("4.3 fJ", "The prototype consumes 4.2 fJ."))

    def test_headings_and_questions_are_not_claims(self) -> None:
        report = grounding.validate_answer(
            "## 证据边界\n这个方向值得继续研究吗？\n现有证据不足，无法判断。",
            [],
        )
        self.assertEqual(report["status"], "insufficient")
        self.assertEqual(report["unsupported_sentences"], [])


if __name__ == "__main__":
    unittest.main()
