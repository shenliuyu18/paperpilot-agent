from __future__ import annotations

import unittest

from app.services import grounding


class ClaimTierTests(unittest.TestCase):
    EVIDENCE = "The prototype consumes 4.2 fJ per conversion."

    def test_empty_claim_is_unsupported(self) -> None:
        result = grounding.classify_claim("   ", self.EVIDENCE)
        self.assertEqual(result["stance"], "unsupported")
        self.assertIn("主张为空", result["reason"])

    def test_missing_evidence_is_unsupported_and_says_so(self) -> None:
        result = grounding.classify_claim("The prototype consumes 4.2 fJ.", "")
        self.assertEqual(result["stance"], "unsupported")
        self.assertIn("未绑定证据", result["reason"])
        self.assertEqual(result["counter_quote"], "")

    def test_identical_claim_is_supported(self) -> None:
        result = grounding.classify_claim(self.EVIDENCE, self.EVIDENCE)
        self.assertEqual(result["stance"], "supported")
        self.assertEqual(result["signals"]["overlap"], 1.0)
        self.assertEqual(result["signals"]["missing_numbers"], [])

    def test_number_not_in_evidence_is_partial(self) -> None:
        result = grounding.classify_claim(
            "The prototype consumes 8.9 fJ per conversion.", self.EVIDENCE
        )
        self.assertEqual(result["stance"], "partial")
        self.assertEqual(result["signals"]["missing_numbers"], ["8.9"])
        self.assertIn("8.9", result["reason"])

    def test_partial_overlap_is_partial(self) -> None:
        claim = (
            "The prototype consumes 4.2 fJ per conversion and also handles many "
            "unrelated workloads in production clusters daily."
        )
        result = grounding.classify_claim(claim, self.EVIDENCE)
        self.assertEqual(result["stance"], "partial")
        self.assertLess(result["signals"]["overlap"], 0.45)
        self.assertGreaterEqual(result["signals"]["overlap"], 0.20)

    def test_unrelated_claim_is_unsupported(self) -> None:
        result = grounding.classify_claim(
            "Quantum annealing schedules require cryogenic dilution refrigerators.",
            self.EVIDENCE,
        )
        self.assertEqual(result["stance"], "unsupported")
        self.assertTrue(result["reason"])

    def test_negation_conflict_downgrades_and_quotes_evidence(self) -> None:
        claim = "The prototype does not consume 4.2 fJ per conversion."
        result = grounding.classify_claim(claim, self.EVIDENCE)
        self.assertEqual(result["stance"], "partial")
        self.assertTrue(result["signals"]["polarity_conflict"])
        self.assertEqual(result["counter_quote"], self.EVIDENCE)

    def test_direction_conflict_is_unsupported_with_counter_quote(self) -> None:
        claim = "The new method reduces energy by 4.2 fJ."
        evidence = "The new method increases energy by 4.2 fJ."
        result = grounding.classify_claim(claim, evidence)
        self.assertEqual(result["stance"], "unsupported")
        self.assertTrue(result["signals"]["direction_conflict"])
        self.assertEqual(result["counter_quote"], evidence)

    def test_thresholds_can_be_overridden(self) -> None:
        claim = (
            "The prototype consumes 4.2 fJ per conversion and also handles many "
            "unrelated workloads in production clusters daily."
        )
        strict = grounding.classify_claim(claim, self.EVIDENCE)
        loose = grounding.classify_claim(
            claim, self.EVIDENCE, support_overlap=0.30, partial_overlap=0.10,
            length_ratio=99.0,
        )
        self.assertEqual(strict["stance"], "partial")
        self.assertEqual(loose["stance"], "supported")

    def test_classification_is_deterministic(self) -> None:
        first = grounding.classify_claim(self.EVIDENCE, self.EVIDENCE)
        second = grounding.classify_claim(self.EVIDENCE, self.EVIDENCE)
        self.assertEqual(first, second)

    def test_cjk_bigrams_are_shared_with_block_level_tokens(self) -> None:
        tokens = grounding.tokens("存内计算能降低数据搬运开销")
        self.assertIn("存内", tokens)
        self.assertIn("降低", tokens)
        claim = "存内计算降低数据搬运开销。"
        evidence = "存内计算能降低数据搬运开销，减少能耗。"
        result = grounding.classify_claim(claim, evidence)
        self.assertIn(result["stance"], {"supported", "partial"})
        self.assertGreater(result["signals"]["overlap"], 0.4)
