from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
from fastapi.testclient import TestClient

from app import config, store
from app.main import app
from app.services import ocr, pdf


def _scanned_pdf(pages: int = 1) -> bytes:
    source = pymupdf.open()
    for index in range(pages):
        page = source.new_page(width=612, height=792)
        page.insert_text((72, 120), f"SCANNED PAPER PAGE {index + 1}", fontsize=30)
        page.insert_text((72, 180), "Energy efficiency is 0.8 pJ/SOP.", fontsize=22)
        page.insert_text((72, 230), "This scanned page contains enough text for local OCR.", fontsize=20)
        page.insert_text((72, 270), "The prototype consumes low power at 65 nm.", fontsize=20)
    scanned = pymupdf.open()
    for index in range(pages):
        image = source[index].get_pixmap(dpi=120).tobytes("png")
        page = scanned.new_page(width=612, height=792)
        page.insert_image(page.rect, stream=image)
    source.close()
    data = scanned.tobytes()
    scanned.close()
    return data


@unittest.skipUnless(ocr.enabled(), "Tesseract is not installed on this machine")
class OcrTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        store.UPLOAD_PAPERS.clear()
        self.parsed_dir.stop()
        self.upload_dir.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_status_reports_the_local_toolchain(self) -> None:
        status = ocr.status()
        self.assertTrue(status["enabled"])
        self.assertIn("languages", status)
        self.assertGreaterEqual(status["max_pages"], 1)

    def test_meta_exposes_ocr_capability(self) -> None:
        client = TestClient(app)
        meta = client.get("/api/meta").json()
        self.assertIn("ocr", meta.get("features") or [])
        self.assertTrue((meta.get("ocr") or {}).get("enabled"))

    def test_image_only_pdf_uses_ocr(self) -> None:
        paper = pdf.parse_pdf_bytes(_scanned_pdf(), "scan.pdf")
        self.assertTrue(paper.get("ocr_used"))
        self.assertGreaterEqual(paper.get("ocr_pages", 0), 1)
        self.assertIn("SCANNED", json.dumps(paper, ensure_ascii=False).upper())

    def test_multipage_ocr_and_truncation_warning(self) -> None:
        with patch.dict(os.environ, {"PAPERPILOT_OCR_MAX_PAGES": "3"}):
            paper = pdf.parse_pdf_bytes(_scanned_pdf(3), "scan.pdf")
        self.assertTrue(paper.get("ocr_used"))
        self.assertEqual(paper.get("ocr_pages"), 3)
        self.assertFalse(paper.get("ocr_truncated"))
        self.assertLess(len(paper.get("title") or ""), 300)
        with patch.dict(os.environ, {"PAPERPILOT_OCR_MAX_PAGES": "1"}):
            truncated = pdf.parse_pdf_bytes(_scanned_pdf(3), "scan.pdf")
        self.assertTrue(truncated.get("ocr_truncated"))
        self.assertIn("仅 OCR", truncated.get("ocr_warning") or "")

    def test_upload_endpoint_reports_ocr(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/api/upload",
            files={"file": ("scan.pdf", _scanned_pdf(), "application/pdf")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body.get("ocr_used"))
        self.assertGreaterEqual(body.get("ocr_pages", 0), 1)
        self.assertIn("OCR", body["paper"].get("extraction_level", ""))

    def test_ocr_can_be_disabled(self) -> None:
        with patch.dict(os.environ, {"PAPERPILOT_OCR": "off"}):
            self.assertFalse(ocr.enabled())
            with self.assertRaises(ValueError) as raised:
                pdf.parse_pdf_bytes(_scanned_pdf(), "scan.pdf")
        self.assertIn("未启用本地 OCR", str(raised.exception))


if __name__ == "__main__":
    unittest.main()