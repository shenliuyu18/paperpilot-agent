from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import golden_workbench  # noqa: E402


def _strip(payload: dict) -> dict:
    data = copy.deepcopy(payload)
    data.pop("created_at", None)
    return data


class GoldenWorkbenchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        golden_workbench.build(self.dir / "candidates", source="demo")
        self.candidates = self.dir / "candidates"
        self.cim = self.candidates / "cim-adc.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_build_is_deterministic_and_never_labels_claims(self) -> None:
        golden_workbench.build(self.dir / "second", source="demo")
        first = {path.name: _strip(json.loads(path.read_text(encoding="utf-8")))
                 for path in sorted(self.candidates.glob("*.json"))}
        second = {path.name: _strip(json.loads(path.read_text(encoding="utf-8")))
                  for path in sorted((self.dir / "second").glob("*.json"))}
        self.assertEqual(first, second)
        self.assertEqual(len(first), 4)
        for payload in first.values():
            self.assertEqual(payload["provenance"], "human-required")
            self.assertTrue(all(claim["label"] == "" for claim in payload["claims"]))

    def test_check_reports_shortfall_and_fails_strict_on_unlabeled(self) -> None:
        report = golden_workbench.check(self.cim, source="demo", strict=True)
        self.assertEqual(report["counts"]["labeled"], 0)
        self.assertTrue(report["strict_failed"])
        self.assertGreater(report["shortfall"]["claims"], 0)
        self.assertEqual(report["problems"], [])

    def test_check_rejects_a_fabricated_quote(self) -> None:
        payload = json.loads(self.cim.read_text(encoding="utf-8"))
        payload["claims"][0]["evidence_quote"] = "这句话不在原文里"
        payload["claims"][0]["label"] = "supported"
        payload["claims"][0]["annotator"] = "tester"
        path = self.dir / "fake.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        report = golden_workbench.check(path, source="demo")
        self.assertTrue(any("不是原文子串" in problem for problem in report["problems"]))

    def test_check_rejects_an_invalid_label(self) -> None:
        payload = json.loads(self.cim.read_text(encoding="utf-8"))
        payload["claims"][0].update({"label": "maybe", "annotator": "tester"})
        path = self.dir / "bad-label.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        report = golden_workbench.check(path, source="demo")
        self.assertTrue(any("label 非法" in problem for problem in report["problems"]))

    def _annotation(self, name: str, labels: list[str]) -> Path:
        payload = json.loads(self.cim.read_text(encoding="utf-8"))
        payload["claims"] = payload["claims"][:len(labels)]
        for claim, label in zip(payload["claims"], labels):
            claim["label"] = label
            claim["annotator"] = name
        path = self.dir / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_merge_logs_disagreements_instead_of_picking_a_winner(self) -> None:
        left = self._annotation("alice", ["supported", "partial", "unsupported"])
        right = self._annotation("bob", ["supported", "partial", "supported"])
        out = self.dir / "merged.json"
        result = golden_workbench.merge(left, right, out)
        self.assertEqual(result["claims"], 3)
        self.assertEqual(result["disagreements"], 1)
        self.assertAlmostEqual(result["agreement"], 0.6667, places=3)
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "needs-adjudication")
        self.assertEqual(payload["claims"][2]["label"], "")
        self.assertTrue(payload["claims"][2]["disagreement"])
        self.assertEqual(payload["disagreements"][0]["label_a"], "unsupported")

    def test_merge_refuses_unlabeled_input(self) -> None:
        left = self._annotation("alice", ["supported", "", "unsupported"])
        right = self._annotation("bob", ["supported", "partial", "unsupported"])
        with self.assertRaises(ValueError):
            golden_workbench.merge(left, right, self.dir / "merged.json")

    def test_stats_lists_candidates(self) -> None:
        rows = golden_workbench.stats(self.dir)
        names = {Path(row["file"]).name for row in rows}
        self.assertIn("cim-adc.json", names)
        self.assertTrue(all("error" not in row for row in rows))


if __name__ == "__main__":
    unittest.main()
