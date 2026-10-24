from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services import content_eval


def _golden(claims: list[dict], pairs: list[dict] | None = None) -> dict:
    return {
        "version": 1, "topic": "unit", "provenance": "test",
        "papers": [], "claims": claims, "contradiction_pairs": pairs or [], "disagreements": [],
    }


class ContentEvalTests(unittest.TestCase):
    def test_metrics_from_fixed_predictions(self) -> None:
        golden = _golden([
            {"id": "1", "claim": "a", "evidence_quote": "x", "label": "supported"},
            {"id": "2", "claim": "b", "evidence_quote": "x", "label": "supported"},
            {"id": "3", "claim": "c", "evidence_quote": "x", "label": "unsupported"},
            {"id": "4", "claim": "d", "evidence_quote": "x", "label": "partial"},
        ])
        predictions = {"a": "supported", "b": "partial", "c": "partial", "d": "unsupported"}
        report = content_eval.evaluate_claims(golden, predict=lambda c, q: predictions[c])
        metrics = report["metrics"]
        # judged-supported: a only, and a is labelled supported -> precision 1.0
        self.assertEqual(metrics["support_precision"], 1.0)
        # labelled-supported: a, b; only a judged supported -> recall 0.5
        self.assertEqual(metrics["support_recall"], 0.5)
        self.assertEqual(metrics["over_strict_rate"], 0.0)
        self.assertEqual(report["counts"]["claims"], 4)

    def test_over_strict_rate_counts_false_negatives(self) -> None:
        golden = _golden([
            {"id": "1", "claim": "a", "evidence_quote": "x", "label": "supported"},
            {"id": "2", "claim": "b", "evidence_quote": "x", "label": "supported"},
        ])
        report = content_eval.evaluate_claims(
            golden, predict=lambda c, q: "unsupported" if c == "a" else "supported"
        )
        self.assertEqual(report["metrics"]["over_strict_rate"], 0.5)
        self.assertEqual(report["metrics"]["support_recall"], 0.5)

    def test_numeric_probe_consistency(self) -> None:
        golden = _golden([
            {"id": "1", "claim": "a", "evidence_quote": "x", "label": "partial",
             "probe": "missing_number"},
            {"id": "2", "claim": "b", "evidence_quote": "x", "label": "partial",
             "probe": "missing_number"},
        ])
        report = content_eval.evaluate_claims(
            golden, predict=lambda c, q: "partial" if c == "a" else "supported"
        )
        self.assertEqual(report["metrics"]["numeric_consistency"], 0.5)

    def test_invalid_claims_are_skipped(self) -> None:
        golden = _golden([
            {"id": "1", "claim": "", "evidence_quote": "x", "label": "supported"},
            {"id": "2", "claim": "b", "evidence_quote": "", "label": "supported"},
            {"id": "3", "claim": "c", "evidence_quote": "x", "label": "unknown"},
        ])
        report = content_eval.evaluate_claims(golden, predict=lambda c, q: "supported")
        self.assertEqual(report["counts"]["claims"], 0)

    def test_thresholds_mark_failures_and_skips(self) -> None:
        rows = content_eval.check_thresholds({
            "support_precision": 0.5, "support_recall": 0.9,
            "over_strict_rate": 0.0, "numeric_consistency": 1.0,
            "coverage": None, "contradiction_recall": 0.9, "contradiction_fpr": 0.0,
        })
        by_name = {row["name"]: row for row in rows}
        self.assertFalse(by_name["support_precision"]["ok"])
        self.assertTrue(by_name["support_recall"]["ok"])
        self.assertIsNone(by_name["coverage"]["ok"])
        # lower-is-better metrics flip the comparison
        strict = content_eval.check_thresholds({"over_strict_rate": 0.2})
        self.assertFalse({r["name"]: r for r in strict}["over_strict_rate"]["ok"])

    def test_contradiction_scoring(self) -> None:
        def paper(pid: str, energy: str) -> dict:
            return {"id": pid, "title": pid, "metrics": {"ADC 能耗": energy}, "results": []}

        golden = _golden([], [
            {"id": "p1", "expected": True, "paper_a": paper("A", "降低 10%"), "paper_b": paper("B", "提升 5%")},
            {"id": "p2", "expected": False, "paper_a": paper("C", "降低 10%"), "paper_b": paper("D", "降低 7%")},
        ])
        report = content_eval.evaluate_contradictions(golden)
        self.assertEqual(report["counts"]["tp"], 1)
        self.assertEqual(report["counts"]["tn"], 1)
        self.assertEqual(report["metrics"]["contradiction_recall"], 1.0)
        self.assertEqual(report["metrics"]["contradiction_fpr"], 0.0)

    def test_coverage_counts_paragraph_blocks_only(self) -> None:
        draft = {"sections": [{"blocks": [
            {"type": "paragraph", "text": "有证据", "evidence_ids": ["e1"]},
            {"type": "paragraph", "text": "无证据", "evidence_ids": []},
            {"type": "heading", "text": "标题", "evidence_ids": []},
            {"type": "paragraph", "text": "   ", "evidence_ids": []},
        ]}]}
        self.assertEqual(content_eval.coverage(draft), 0.5)

    def test_loader_rejects_malformed_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps({"claims": "not-a-list"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                content_eval.load_golden(path)
