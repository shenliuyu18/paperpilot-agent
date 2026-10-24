from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import config, store
from app.main import app


ROOT = Path(__file__).resolve().parent.parent
PDFS = sorted((ROOT / "data" / "demo_pdfs").glob("*.pdf"))


class UploadFeatureTests(unittest.TestCase):
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
        (root / "uploads").mkdir(parents=True, exist_ok=True)
        (root / "parsed").mkdir(parents=True, exist_ok=True)
        store.UPLOAD_PAPERS.clear()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        store.UPLOAD_PAPERS.clear()
        self.parsed_dir.stop()
        self.upload_dir.stop()
        self.env.stop()
        self.tmp.cleanup()

    def _upload(self, path: Path) -> dict:
        response = self.client.post(
            "/api/upload",
            files={"file": (path.name, path.read_bytes(), "application/pdf")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_corrupt_pdf_returns_400_not_500(self) -> None:
        response = self.client.post(
            "/api/upload",
            files={"file": ("broken.pdf", b"%PDF-1.7\nnot a real pdf", "application/pdf")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("损坏", response.json()["detail"])

    def test_blank_scanned_pdf_is_rejected_with_ocr_hint(self) -> None:
        import pymupdf

        doc = pymupdf.open()
        doc.new_page()
        doc.new_page()
        payload = doc.tobytes()
        doc.close()
        response = self.client.post(
            "/api/upload",
            files={"file": ("scanned.pdf", payload, "application/pdf")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("扫描件", response.json()["detail"])

    def test_uploaded_pdfs_are_a_search_source(self) -> None:
        first = self._upload(PDFS[0])
        second = self._upload(PDFS[1])
        self.assertEqual(first["paper"].get("source_label"), "本地 PDF")
        ids = {first["paper_id"], second["paper_id"]}
        response = self.client.post(
            "/api/search",
            json={"query": "CIM ADC", "limit": 8, "source": "upload"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        results = response.json().get("papers") or []
        result_ids = {item.get("id") for item in results}
        self.assertTrue(ids & result_ids)
        self.assertFalse({item for item in result_ids if item and not item.startswith("upload-")})

    def test_agent_upload_source_uses_uploaded_papers(self) -> None:
        first = self._upload(PDFS[0])
        second = self._upload(PDFS[1])
        uploaded_ids = {first["paper_id"], second["paper_id"]}
        response = self.client.post("/api/agent/runs", json={
            "goal": "总结上传的 CIM 论文",
            "source": "upload",
            "mode": "auto",
            "max_actions": 2,
            "max_seconds": 60,
            "max_llm_calls": 0,
            "max_papers": 2,
        })
        self.assertEqual(response.status_code, 200, response.text)
        run_id = response.json()["run"]["id"]
        terminal = {"completed", "failed", "cancelled", "interrupted", "awaiting_approval"}
        last = {}
        for _ in range(80):
            last = self.client.get(f"/api/agent/runs/{run_id}").json()["run"]
            if last.get("status") in terminal:
                break
            time.sleep(0.25)
        self.assertEqual(last.get("status"), "completed", last.get("error"))
        self.assertTrue(last.get("paper_ids"))
        self.assertTrue(all(str(item).startswith("upload-") for item in last["paper_ids"]))
        self.assertTrue(set(last["paper_ids"]) <= uploaded_ids)

    def test_uploaded_pdf_result_metrics_are_not_mixed(self) -> None:
        first = self._upload(PDFS[0])
        second = self._upload(PDFS[1])
        detail_first = self.client.get(f"/api/paper/{first['paper_id']}").json()
        energy_values = [
            str(item.get("value") or "")
            for item in detail_first.get("results") or []
            if item.get("name") == "能效"
        ]
        self.assertTrue(energy_values)
        self.assertFalse(any("%" in value for value in energy_values), energy_values)
        detail_second = self.client.get(f"/api/paper/{second['paper_id']}").json()
        contexts = " ".join(str(item.get("context") or "") for item in detail_second.get("results") or [])
        self.assertNotIn("ISAAC reports", contexts)

    def test_uploaded_pdf_limitations_skip_captions_and_tradeoff(self) -> None:
        second = self._upload(PDFS[1])
        detail = self.client.get(f"/api/paper/{second['paper_id']}").json()
        limitations = detail.get("limitations") or []
        joined = " ".join(limitations)
        self.assertNotIn("TABLE", joined.upper())
        self.assertFalse(any("Trade-off We evaluate" in item for item in limitations))

    def test_offline_translation_note_matches_uploaded_pdf(self) -> None:
        first = self._upload(PDFS[0])
        response = self.client.post("/api/translate", json={
            "paper_id": first["paper_id"], "target": "zh", "provider": "auto",
        })
        self.assertEqual(response.status_code, 200)
        note = response.json().get("note") or ""
        self.assertIn("本地 PDF", note)
        self.assertNotIn("演示论文已提供", note)

    def test_agent_upload_source_without_uploads_fails_clearly(self) -> None:
        response = self.client.post("/api/agent/runs", json={
            "goal": "总结本地 PDF",
            "source": "upload",
            "mode": "auto",
            "max_actions": 2,
            "max_seconds": 30,
            "max_llm_calls": 0,
            "max_papers": 2,
        })
        self.assertEqual(response.status_code, 200, response.text)
        run_id = response.json()["run"]["id"]
        terminal = {"completed", "failed", "cancelled", "interrupted", "awaiting_approval"}
        last = {}
        for _ in range(80):
            last = self.client.get(f"/api/agent/runs/{run_id}").json()["run"]
            if last.get("status") in terminal:
                break
            time.sleep(0.25)
        self.assertEqual(last.get("status"), "failed")
        self.assertIn("上传", json.dumps(last, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()