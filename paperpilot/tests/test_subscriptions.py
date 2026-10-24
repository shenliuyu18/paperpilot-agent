from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import (
    paper_index,
    project_service,
    subscription_service,
    subscription_state,
)


def _paper(paper_id: str, title: str, *, doi: str = "", url: str = "", energy: str = "",
           method: str = "", abstract: str = "", source: str = "arxiv") -> dict:
    paper = {"id": paper_id, "title": title, "year": "2026", "source": source,
             "abstract": abstract, "method": method, "url": url}
    if doi:
        paper["doi"] = doi
    metrics = {}
    if energy:
        metrics["ADC 能耗"] = energy
    paper["metrics"] = metrics
    paper["results"] = []
    return paper


class PaperIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PAPERPILOT_DATA_DIR": self.tmp.name,
            "PAPERPILOT_INDEX_FILE": str(Path(self.tmp.name) / "index.json"),
            "PAPERPILOT_SUBSCRIPTIONS_FILE": str(Path(self.tmp.name) / "subscriptions.json"),
        })
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_fingerprint_prefers_doi_then_arxiv_then_title(self) -> None:
        self.assertTrue(paper_index.fingerprint({"doi": "10.1000/ABC"}).startswith("doi:10.1000/abc"))
        self.assertEqual(paper_index.fingerprint({"url": "https://arxiv.org/abs/2501.01234v2"}),
                         "arxiv:2501.01234")
        self.assertTrue(paper_index.fingerprint({"title": "Some Paper", "year": "2026"}).startswith("title:"))

    def test_same_arxiv_id_from_two_sources_collapses(self) -> None:
        first = paper_index.sync([{"id": "2501.01234v1", "title": "T", "url": "https://arxiv.org/abs/2501.01234v1"}])
        second = paper_index.sync([{"id": "crossref-1", "title": "T", "url": "https://arxiv.org/abs/2501.01234v1"}])
        self.assertEqual(len(first["added"]), 1)
        self.assertEqual(second["added"], [])
        self.assertEqual(paper_index.status()["total"], 1)

    def test_sync_updates_content_hash_when_abstract_changes(self) -> None:
        paper_index.sync([{"id": "P1", "title": "T", "abstract": "v1"}])
        result = paper_index.sync([{"id": "P1", "title": "T", "abstract": "v2"}])
        self.assertEqual(len(result["updated"]), 1)
        self.assertEqual(paper_index.status()["total"], 1)

    def test_sync_links_project_and_subscription(self) -> None:
        paper_index.sync([{"id": "P1", "title": "T"}], project_id="prj-x", subscription_id="sub-y")
        entry = paper_index.find("P1")
        self.assertEqual(entry["projects"], ["prj-x"])
        self.assertEqual(entry["subscriptions"], ["sub-y"])


class SubscriptionStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PAPERPILOT_DATA_DIR": self.tmp.name,
            "PAPERPILOT_INDEX_FILE": str(Path(self.tmp.name) / "index.json"),
            "PAPERPILOT_SUBSCRIPTIONS_FILE": str(Path(self.tmp.name) / "subscriptions.json"),
        })
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_create_update_delete_round_trip(self) -> None:
        item = subscription_state.create({"label": "量化跟踪", "query": "quantization", "source": "demo",
                                          "interval_hours": 24, "limit": 10})
        self.assertTrue(item["id"].startswith("sub-"))
        self.assertTrue(item["next_run_at"])
        updated = subscription_state.update(item["id"], {"enabled": False, "interval_hours": 168})
        self.assertFalse(updated["enabled"])
        self.assertEqual(subscription_state.list_items()[0]["enabled"], False)
        self.assertTrue(subscription_state.remove(item["id"]))
        self.assertEqual(subscription_state.list_items(), [])

    def test_validation_rejects_bad_input(self) -> None:
        for payload in (
            {"query": ""},
            {"query": "x" * 161},
            {"query": "ok", "source": "pubmed"},
            {"query": "ok", "interval_hours": 999},
            {"query": "ok", "limit": 0},
        ):
            with self.assertRaises(ValueError, msg=str(payload)):
                subscription_state.create(payload)

    def test_record_run_accumulates_cursor_and_result(self) -> None:
        item = subscription_state.create({"query": "quantization", "source": "demo"})
        updated = subscription_state.record_run(item["id"], fingerprints=["arxiv:1", "arxiv:2"],
                                               result={"new": 2})
        self.assertEqual(updated["seen_fingerprints"], ["arxiv:1", "arxiv:2"])
        self.assertEqual(updated["last_result"], {"new": 2})
        again = subscription_state.record_run(item["id"], fingerprints=["arxiv:2", "arxiv:3"],
                                             result={"new": 1})
        self.assertEqual(again["seen_fingerprints"], ["arxiv:1", "arxiv:2", "arxiv:3"])


class ClassificationTests(unittest.TestCase):
    def test_opposite_metric_is_conflict(self) -> None:
        label, reason = subscription_service.classify(
            _paper("N1", "New", energy="提升 12%"),
            [_paper("P1", "Old", energy="降低 27%")],
        )
        self.assertEqual(label, "conflict")
        self.assertIn("方向相反", reason)

    def test_same_metric_same_direction_is_support(self) -> None:
        label, _ = subscription_service.classify(
            _paper("N1", "New", energy="降低 31%"),
            [_paper("P1", "Old", energy="降低 27%")],
        )
        self.assertEqual(label, "support")

    def test_method_overlap_is_method(self) -> None:
        text = "shared SAR ADC calibration pipeline with redundant comparators"
        label, _ = subscription_service.classify(
            _paper("N1", "New", method=text),
            [_paper("P1", "Old", method=text)],
        )
        self.assertEqual(label, "method")

    def test_unrelated_is_irrelevant(self) -> None:
        label, _ = subscription_service.classify(
            _paper("N1", "Quantum annealing for protein folding", method="annealing schedule"),
            [_paper("P1", "ADC calibration", method="SAR comparator trimming")],
        )
        self.assertEqual(label, "irrelevant")

    def test_first_paper_in_empty_project_counts_as_support(self) -> None:
        label, _ = subscription_service.classify(_paper("N1", "New"), [])
        self.assertEqual(label, "support")


class SubscriptionRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            "PAPERPILOT_DATA_DIR": str(root / "data"),
            "PAPERPILOT_PROJECT_FILE": str(root / "projects.json"),
            "PAPERPILOT_PROJECT_DIR": str(root / "projects"),
            "PAPERPILOT_AGENT_FILE": str(root / "runs.json"),
            "PAPERPILOT_AGENT_DIR": str(root / "runs"),
            "PAPERPILOT_INDEX_FILE": str(root / "index.json"),
            "PAPERPILOT_SUBSCRIPTIONS_FILE": str(root / "subscriptions.json"),
        })
        self.env.start()
        self.project = project_service.create_project("订阅项目", "问题？")

    def tearDown(self) -> None:
        subscription_service.stop_scheduler()
        self.env.stop()
        self.tmp.cleanup()

    def test_run_once_marks_seen_and_is_idempotent(self) -> None:
        item = subscription_state.create({
            "label": "量化", "query": "quantization", "source": "demo", "limit": 5,
            "project_id": self.project["id"],
        })
        first = subscription_service.run_once(item["id"])
        self.assertGreaterEqual(first["result"]["new"], 1)
        self.assertEqual(first["index"]["added"], first["result"]["new"])
        for key in ("conflict", "support", "method", "irrelevant", "contradictions_added"):
            self.assertIn(key, first["result"])
        self.assertTrue(first["subscription"]["seen_fingerprints"])

        second = subscription_service.run_once(item["id"])
        self.assertEqual(second["result"]["new"], 0)
        self.assertEqual(second["items"], [])

    def test_run_once_unknown_subscription(self) -> None:
        with self.assertRaises(KeyError):
            subscription_service.run_once("sub-missing")

    def test_scheduler_is_off_by_default(self) -> None:
        self.assertFalse(subscription_service.scheduler_enabled())
        self.assertFalse(subscription_service.start_scheduler())
        self.assertFalse(subscription_service.scheduler_status()["running"])

    def test_scheduler_starts_and_stops_when_enabled(self) -> None:
        with patch.dict(os.environ, {"PAPERPILOT_SCHEDULER": "1"}):
            self.assertTrue(subscription_service.start_scheduler())
            self.assertTrue(subscription_service.scheduler_status()["running"])
            subscription_service.stop_scheduler()
            self.assertFalse(subscription_service.scheduler_status()["running"])
