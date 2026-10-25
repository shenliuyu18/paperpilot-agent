from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import argument, library, search


class SearchPlanApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_search_plan_endpoint_returns_concepts_and_queries(self) -> None:
        response = self.client.post(
            "/api/search/plan",
            json={"query": "存算一体 低功耗 ADC", "source": "auto"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["concepts"])
        self.assertIn("sources", data)
        self.assertTrue(data["strategy"])

    def test_search_results_include_layers_and_credibility(self) -> None:
        response = self.client.post(
            "/api/search",
            json={"query": "存算一体 低功耗 ADC", "limit": 5, "source": "demo"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["papers"])
        self.assertIn("直接相关", data["layers"])
        layers = [paper["layer"] for paper in data["papers"]]
        self.assertEqual(layers, sorted(layers, key={"直接相关": 0, "方法参考": 1, "背景脉络": 2}.get))
        for paper in data["papers"]:
            self.assertIn(paper["layer"], {"直接相关", "方法参考", "背景脉络"})
            self.assertIn("credibility", paper)
            self.assertGreaterEqual(paper["credibility"]["score"], 0)

    def test_library_health_and_argument_endpoints_are_wired(self) -> None:
        with patch.object(
            library,
            "health",
            return_value={"total": 0, "items": [], "actions": []},
        ) as health:
            response = self.client.get("/api/library/health?verify=true")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(health.call_args.kwargs["verify_dois"])

        with patch.object(
            argument,
            "argument_review",
            return_value={"score": 60, "verdict": "需继续修补", "actions": []},
        ) as review:
            response = self.client.post(
                "/api/argument",
                json={"text": "本文研究一个问题，现有工作尚未给出验证。", "topic": "测试"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["score"], 60)
        self.assertTrue(review.called)

    def test_aggregate_search_reports_every_source(self) -> None:
        search._AUTO_COOLDOWN.clear()
        try:
            with patch.object(search, "search_crossref", return_value=[]), \
                 patch.object(search, "search_arxiv", side_effect=RuntimeError("arxiv down")), \
                 patch.object(search, "search_europepmc", return_value=[]):
                response = self.client.post(
                    "/api/search",
                    json={"query": "存算一体 低功耗 ADC", "limit": 4, "source": "auto"},
                )
        finally:
            search._AUTO_COOLDOWN.clear()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        status = data["source_status"]
        self.assertTrue(status["aggregate"])
        self.assertTrue(status["fallback"])
        by_source = {row["source"]: row for row in status["sources"]}
        self.assertEqual(by_source["arxiv"]["status"], "failed")
        self.assertIn("arxiv down", by_source["arxiv"]["detail"])
        self.assertTrue(all(paper["aggregate_fallback"] for paper in data["papers"]))

    def test_plain_sources_report_no_source_status(self) -> None:
        response = self.client.post(
            "/api/search",
            json={"query": "存算一体 低功耗 ADC", "limit": 3, "source": "demo"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source_status"], {})

    def test_security_headers_are_set(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(
            response.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin"
        )
        policy = response.headers.get("Content-Security-Policy", "")
        self.assertIn("frame-ancestors 'self'", policy)
        self.assertIn("modelscope.cn", policy)
        self.assertIn("geolocation=()", response.headers.get("Permissions-Policy", ""))


if __name__ == "__main__":
    unittest.main()
