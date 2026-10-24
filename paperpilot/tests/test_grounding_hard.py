from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.services import content_eval, grounding

GOLDEN = Path(__file__).resolve().parents[1] / "data" / "eval" / "golden" / "seed-adversarial.json"


class SameDomainHardNegativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    def test_every_cross_paper_probe_is_unsupported(self) -> None:
        probes = [item for item in self.golden["claims"] if item["label"] == "unsupported"]
        self.assertGreaterEqual(len(probes), 30)
        for item in probes:
            result = grounding.classify_claim(item["claim"], item["evidence_quote"])
            self.assertEqual(result["stance"], "unsupported", item["id"])
            self.assertTrue(result["reason"])

    def test_verbatim_anchors_stay_supported(self) -> None:
        anchors = [item for item in self.golden["claims"] if item["label"] == "supported"]
        self.assertGreaterEqual(len(anchors), 4)
        for item in anchors:
            result = grounding.classify_claim(item["claim"], item["evidence_quote"])
            self.assertEqual(result["stance"], "supported", item["id"])

    def test_set_scores_perfectly(self) -> None:
        report = content_eval.evaluate_claims(self.golden)
        self.assertEqual(report["metrics"]["support_precision"], 1.0)
        self.assertEqual(report["metrics"]["support_recall"], 1.0)
        self.assertEqual(report["metrics"]["over_strict_rate"], 0.0)

    def test_extended_but_number_matched_claim_stays_partial(self) -> None:
        evidence = ("The accelerator improves energy efficiency by 2.5 times "
                    "for vision workloads.")
        claim = (evidence + " It also reports 128 TOPS in production deployments.")
        result = grounding.classify_claim(claim, evidence)
        self.assertEqual(result["stance"], "partial")
        self.assertGreaterEqual(result["signals"]["number_overlap"], 0.5)


if __name__ == "__main__":
    unittest.main()
