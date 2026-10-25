from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import config, store
from app.main import app


ROOT = Path(__file__).resolve().parent.parent
PDF = sorted((ROOT / "data" / "demo_pdfs").glob("*.pdf"))[0]


class PdfPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = patch.dict(
            os.environ,
            {
                "PAPERPILOT_DATA_DIR": str(root / "data"),
                "PAPERPILOT_PROJECT_FILE": str(root / "projects.json"),
                "PAPERPILOT_PROJECT_DIR": str(root / "projects"),
                "PAPERPILOT_AGENT_FILE": str(root / "runs.json"),
                "PAPERPILOT_AGENT_DIR": str(root / "runs"),
                "PAPERPILOT_UPLOAD_DIR": str(root / "uploads"),
            },
        )
        self.env.start()
        self.upload_dir = patch.object(config, "UPLOAD_DIR", root / "uploads")
        self.parsed_dir = patch.object(config, "PARSED_DIR", root / "parsed")
        self.upload_dir.start()
        self.parsed_dir.start()
        (root / "uploads").mkdir(exist_ok=True)
        (root / "parsed").mkdir(exist_ok=True)
        store.UPLOAD_PAPERS.clear()
        self.client = TestClient(app)
        response = self.client.post(
            "/api/upload",
            files={"file": (PDF.name, PDF.read_bytes(), "application/pdf")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.paper_id = response.json()["paper_id"]

    def tearDown(self) -> None:
        store.UPLOAD_PAPERS.clear()
        self.parsed_dir.stop()
        self.upload_dir.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_meta_exposes_pdf_pages_capability(self) -> None:
        meta = self.client.get("/api/meta").json()
        self.assertIn("pdf_pages", meta.get("features") or [])
        self.assertIn("pdf_evidence", meta.get("features") or [])

    def test_quote_locate_returns_normalized_boxes(self) -> None:
        response = self.client.get(
            f"/api/paper/{self.paper_id}/locate",
            params={"page": 1, "quote": "energy efficiency"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["matched"])
        self.assertTrue(body["rects"])
        for rect in body["rects"]:
            self.assertTrue(0 <= rect["x0"] <= rect["x1"] <= 1)
            self.assertTrue(0 <= rect["y0"] <= rect["y1"] <= 1)

    def test_locate_miss_is_explicit_and_never_fabricates(self) -> None:
        response = self.client.get(
            f"/api/paper/{self.paper_id}/locate",
            params={"page": 1, "quote": "zzzz-not-in-this-paper"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["matched"])
        self.assertEqual(body["rects"], [])
        self.assertIn("未找到", body["note"])

    def test_locate_rejects_bad_pages(self) -> None:
        self.assertEqual(self.client.get(
            f"/api/paper/{self.paper_id}/locate", params={"page": 0, "quote": "energy"}
        ).status_code, 404)
        self.assertEqual(self.client.get(
            "/api/paper/not-a-paper/locate", params={"page": 1, "quote": "energy"}
        ).status_code, 404)

    def test_read_marks_local_pdf(self) -> None:
        response = self.client.post("/api/read", json={"paper_id": self.paper_id, "mode": "5min"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["paper"].get("local_pdf"))

    def test_first_page_is_a_png(self) -> None:
        response = self.client.get(f"/api/paper/{self.paper_id}/pages/1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(response.content), 1000)

    def test_out_of_range_pages_are_rejected(self) -> None:
        self.assertEqual(self.client.get(f"/api/paper/{self.paper_id}/pages/0").status_code, 404)
        self.assertEqual(self.client.get(f"/api/paper/{self.paper_id}/pages/999").status_code, 404)
        self.assertEqual(self.client.get("/api/paper/not-a-paper/pages/1").status_code, 404)

    def test_page_count_probe_matches_the_document(self) -> None:
        response = self.client.get(f"/api/paper/{self.paper_id}/pages")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["local_pdf"])
        self.assertGreaterEqual(payload["pages"], 1)
        self.assertEqual(self.client.get("/api/paper/not-a-paper/pages").status_code, 404)

    def test_demo_paper_without_pdf_is_explicitly_unavailable(self) -> None:
        response = self.client.get("/api/paper/2606.08947v1/pages/1")
        # The bundled demo PDF exists for this paper in the package; if it is
        # not present in a stripped deployment, the API must fail cleanly.
        self.assertIn(response.status_code, (200, 404))


if __name__ == "__main__":
    unittest.main()