from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import history


class SearchHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "history.json"
        self.env = patch.dict(os.environ, {"PAPERPILOT_HISTORY_FILE": str(self.path)})
        self.env.start()
        history.clear()

    def tearDown(self) -> None:
        self.env.stop()
        self._tmp.cleanup()

    def test_record_list_export_and_clear(self) -> None:
        entry = history.record(
            "存算一体 ADC",
            "demo",
            "内置演示语料",
            5,
            {"直接相关": 3, "方法参考": 2, "背景脉络": 0},
            ["Paper A", "Paper B"],
        )
        self.assertTrue(entry["id"])
        entries = history.list_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["query"], "存算一体 ADC")
        markdown = history.export_markdown()
        self.assertIn("存算一体 ADC", markdown)
        self.assertIn("直接相关 3", markdown)
        csv_text = history.export_csv()
        self.assertIn("query,source", csv_text)
        history.clear()
        self.assertEqual(history.list_entries(), [])

    def test_search_endpoint_records_history(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/api/search",
            json={"query": "存算一体 低功耗 ADC", "limit": 3, "source": "demo"},
        )
        self.assertEqual(response.status_code, 200)
        listed = client.get("/api/history")
        self.assertEqual(listed.status_code, 200)
        entries = listed.json()["entries"]
        self.assertTrue(entries)
        self.assertEqual(entries[0]["query"], "存算一体 低功耗 ADC")
        exported = client.get("/api/history/export?format=markdown")
        self.assertEqual(exported.status_code, 200)
        self.assertIn("检索留痕", exported.text)
        cleared = client.post("/api/history/clear")
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(client.get("/api/history").json()["entries"], [])

    def test_history_is_capped(self) -> None:
        for index in range(205):
            history.record(f"q{index}", "demo", "内置演示语料", 1, {}, [])
        entries = history.list_entries(limit=500)
        self.assertEqual(len(entries), 200)
        self.assertEqual(entries[0]["query"], "q204")


if __name__ == "__main__":
    unittest.main()
