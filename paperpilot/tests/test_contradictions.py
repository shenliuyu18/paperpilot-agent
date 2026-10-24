from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import contradictions, draft_state, project_service, project_state


def _paper(paper_id: str, title: str, energy: str) -> dict:
    return {
        "id": paper_id,
        "title": title,
        "year": "2026",
        "venue": "arXiv preprint",
        "metrics": {"ADC 能耗": energy, "工艺/验证": "55 nm 仿真"},
        "results": [{
            "name": "ADC 能耗", "value": energy,
            "baseline": "native", "condition": "40 mV 工作点",
        }],
    }


class ContradictionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = patch.dict(
            os.environ,
            {
                "PAPERPILOT_PROJECT_FILE": str(root / "projects.json"),
                "PAPERPILOT_PROJECT_DIR": str(root / "projects"),
                "PAPERPILOT_AGENT_FILE": str(root / "runs.json"),
                "PAPERPILOT_AGENT_DIR": str(root / "runs"),
            },
        )
        self.env.start()
        self.project = project_service.create_project("矛盾项目", "如何降低能耗？")
        self.pid = self.project["id"]

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _seed(self, papers: list[dict]) -> None:
        memory = project_state.read_memory(self.pid)
        memory["papers"] = {paper["id"]: paper for paper in papers}
        project_state.write_memory(self.pid, memory)

    def test_opposite_direction_on_same_metric_is_flagged(self) -> None:
        self._seed([
            _paper("P1", "Paper One", "降低 27.1%"),
            _paper("P2", "Paper Two", "提升 12.5%"),
        ])
        result = contradictions.detect(self.pid)
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["kind"], "metric_conflict")
        self.assertEqual(item["metric"], "ADC 能耗")
        self.assertIn("方向相反", item["why"])
        self.assertIn(item["severity"], {"high", "medium"})
        self.assertTrue(item["quote_a"] and item["quote_b"])

    def test_same_direction_is_not_flagged(self) -> None:
        self._seed([
            _paper("P1", "Paper One", "降低 27.1%"),
            _paper("P2", "Paper Two", "降低 91.7%"),
        ])
        self.assertEqual(contradictions.detect(self.pid)["items"], [])

    def test_missing_direction_is_not_flagged(self) -> None:
        self._seed([
            _paper("P1", "Paper One", "0.8 pJ/SOP"),
            _paper("P2", "Paper Two", "1.2 pJ/SOP"),
        ])
        self.assertEqual(contradictions.detect(self.pid)["items"], [])

    def test_no_shared_metric_is_not_flagged(self) -> None:
        self._seed([
            _paper("P1", "Paper One", "降低 27.1%"),
            {"id": "P2", "title": "Paper Two", "metrics": {"吞吐": "提升 2.0×"}, "results": []},
        ])
        self.assertEqual(contradictions.detect(self.pid)["items"], [])

    def test_draft_blocks_are_linked_through_citation_title(self) -> None:
        self._seed([
            _paper("P1", "Paper One", "降低 27.1%"),
            _paper("P2", "Paper Two", "提升 12.5%"),
        ])
        draft = draft_state.empty_draft(self.pid)
        draft["citations"] = {
            "one2026": {"key": "one2026", "title": "Paper One", "authors": ["A"], "year": 2026,
                        "venue": "V", "doi": "", "url": ""},
        }
        draft["sections"] = [{
            "id": "sec-001", "title": "引言",
            "blocks": [{"id": "blk-001", "type": "paragraph", "text": "能耗更低 [1]。",
                        "evidence_ids": [], "citation_keys": ["one2026"], "status": "draft"}],
        }]
        draft_state.write_draft(self.pid, draft)

        item = contradictions.detect(self.pid)["items"][0]
        self.assertEqual(item["draft_blocks"], ["blk-001"])

    def test_scan_persists_and_read_returns_it(self) -> None:
        self._seed([
            _paper("P1", "Paper One", "降低 27.1%"),
            _paper("P2", "Paper Two", "提升 12.5%"),
        ])
        saved = contradictions.scan(self.pid)
        self.assertEqual(saved["stats"]["items"], 1)
        again = contradictions.read(self.pid)
        self.assertEqual(len(again["items"]), 1)
        self.assertEqual(again["items"][0]["id"], "ctr-001")

    def test_scan_is_stable_across_runs(self) -> None:
        self._seed([
            _paper("P1", "Paper One", "降低 27.1%"),
            _paper("P2", "Paper Two", "提升 12.5%"),
        ])
        first = contradictions.scan(self.pid)["items"]
        second = contradictions.scan(self.pid)["items"]
        self.assertEqual([i["why"] for i in first], [i["why"] for i in second])
        self.assertEqual(len(first), len(second))
