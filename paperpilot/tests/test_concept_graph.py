from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import concept_graph, search


class ConceptGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        concept_graph.clear_cache()

    def test_selected_papers_produce_typed_graph(self) -> None:
        papers = [
            search.get_paper("2609.24288v1"),
            search.get_paper("2606.08947v1"),
        ]
        result = concept_graph.build("selected", [paper["id"] for paper in papers], "存算一体")
        self.assertEqual(result["stats"]["papers"], 2)
        self.assertGreater(result["stats"]["nodes"], 2)
        self.assertGreater(result["stats"]["edges"], 0)
        types = {node["type"] for node in result["nodes"]}
        self.assertIn("paper", types)
        self.assertIn("concept", types)
        self.assertIn("metric", types)
        node_ids = {node["id"] for node in result["nodes"]}
        for edge in result["edges"]:
            self.assertIn(edge["source"], node_ids)
            self.assertIn(edge["target"], node_ids)
        for node in result["nodes"]:
            self.assertTrue(node["label"])
            self.assertTrue(node["id"].startswith(f"{node['type']}:"))

    def test_empty_scope_is_graceful(self) -> None:
        result = concept_graph.build("selected", [], "")
        self.assertEqual(result["stats"]["nodes"], 0)
        self.assertTrue(result["notes"])
        self.assertEqual(result["papers"], [])

    def test_non_adc_papers_do_not_invent_adc_concepts(self) -> None:
        papers = [
            {
                "id": "graph-browser-a",
                "title": "Browser Agents for Web Tasks",
                "abstract": "We study browser agents that read pages and click. Evaluation on web tasks measures latency.",
                "metrics": {"被引": 2},
                "results": [{"name": "latency", "value": "-12%"}],
                "limitations": ["Security risks remain."],
                "sections": [],
            },
            {
                "id": "graph-browser-b",
                "title": "Secure Browser Navigation",
                "abstract": "We evaluate browser agents on navigation benchmarks and report latency.",
                "metrics": {"被引": 4},
                "results": [{"name": "latency", "value": "-8%"}],
                "limitations": [],
                "sections": [],
            },
        ]
        result = concept_graph.build("selected", [paper["id"] for paper in papers], "browser")
        labels = " ".join(node["label"] for node in result["nodes"]).lower()
        for term in ("adc", "cim", "存算", "模数转换"):
            self.assertNotIn(term, labels)

    def test_cache_reuses_build_and_returns_copy(self) -> None:
        papers = [
            search.get_paper("2609.24288v1"),
            search.get_paper("2606.08947v1"),
        ]
        ids = [paper["id"] for paper in papers]
        with patch.object(concept_graph, "_concepts", wraps=concept_graph._concepts) as spy:
            first = concept_graph.build("selected", ids, "")
            second = concept_graph.build("selected", ids, "另一个主题")
            self.assertEqual(spy.call_count, len(papers))
        self.assertEqual(second["topic"], "另一个主题")
        first["notes"].append("mutated")
        self.assertNotIn("mutated", second["notes"])

    def test_cache_key_tracks_tldr_and_sections(self) -> None:
        paper = {
            "id": "cache-key-paper",
            "title": "Adaptive Cache Paper",
            "abstract": "We propose a cache-aware scheduler for browser agents.",
            "tldr": "",
            "metrics": {},
            "results": [],
            "limitations": [],
            "sections": [],
        }
        papers = [paper]
        with patch.object(
            concept_graph, "_scope_papers", return_value=(papers, "测试范围")
        ), patch.object(concept_graph, "_concepts", wraps=concept_graph._concepts) as spy:
            concept_graph.build("selected", [paper["id"]], "cache")
            concept_graph.build("selected", [paper["id"]], "cache")
            self.assertEqual(spy.call_count, 1)
            paper["tldr"] = "A totally different method summary now leads this paper."
            concept_graph.build("selected", [paper["id"]], "cache")
            self.assertEqual(spy.call_count, 2)
            paper["sections"] = [
                {"title": "Method", "text": "We evaluate a new retrieval pipeline on WebArena."}
            ]
            concept_graph.build("selected", [paper["id"]], "cache")
            self.assertEqual(spy.call_count, 3)

    def test_selected_ids_are_deduplicated(self) -> None:
        result = concept_graph.build(
            "selected", ["2609.24288v1", "2609.24288v1", "2609.24288v1"], ""
        )
        self.assertEqual(result["stats"]["papers"], 1)

    def test_edge_cap_is_enforced_and_reported(self) -> None:
        ids = ["2609.24288v1", "2606.08947v1"]
        normal = concept_graph.build("selected", ids, "")
        self.assertLessEqual(len(normal["edges"]), concept_graph.MAX_EDGES)
        concept_graph.clear_cache()
        with patch.object(concept_graph, "MAX_EDGES", 12):
            capped = concept_graph.build("selected", ids, "")
        self.assertLessEqual(len(capped["edges"]), 12)
        self.assertTrue(any("上限 12" in note for note in capped["notes"]))
        node_ids = {node["id"] for node in capped["nodes"]}
        for edge in capped["edges"]:
            self.assertIn(edge["source"], node_ids)
            self.assertIn(edge["target"], node_ids)

    def test_graph_api_rejects_malformed_input(self) -> None:
        client = TestClient(app)
        cases = [
            {"scope": "everything"},
            {"scope": "selected", "paper_ids": [""]},
            {"scope": "selected", "paper_ids": ["x" * 121]},
            {"scope": "all", "topic": "t" * 201},
        ]
        for payload in cases:
            response = client.post("/api/graph", json=payload)
            self.assertEqual(response.status_code, 422, payload)

    def test_graph_api_unknown_ids_return_empty_graph(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/api/graph",
            json={"scope": "selected", "paper_ids": ["missing-a", "missing-b"]},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["stats"]["nodes"], 0)
        self.assertEqual(data["edges"], [])
        self.assertTrue(data["notes"])

    def test_graph_api_contract(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/api/graph",
            json={"scope": "selected", "paper_ids": ["2609.24288v1", "2606.08947v1"]},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("stats", data)
        self.assertIn("nodes", data)
        self.assertIn("edges", data)
        self.assertGreaterEqual(data["stats"]["papers"], 2)

    def test_graph_api_rejects_oversized_selection(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/api/graph",
            json={"scope": "selected", "paper_ids": [f"p{i}" for i in range(21)]},
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
