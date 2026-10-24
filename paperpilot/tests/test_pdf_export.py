from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import draft_service, pdf_export, project_service


def _draft() -> dict:
    return {
        "title": "草稿标题",
        "citation_style": "gb7714",
        "sections": [{
            "id": "sec-001", "title": "引言",
            "blocks": [
                {"id": "blk-001", "type": "paragraph",
                 "text": "存内计算降低数据搬运开销，命中率提升 27.1%。",
                 "citation_keys": ["a2024"]},
                {"id": "blk-002", "type": "bullet", "text": "要点一", "citation_keys": []},
                {"id": "blk-003", "type": "paragraph", "text": "100% 的样本可复现。", "citation_keys": []},
            ],
        }],
        "citations": {"a2024": {
            "key": "a2024", "title": "Paper One", "authors": ["Alice Author"],
            "year": "2024", "venue": "Journal of Things", "doi": "10.1000/xyz",
            "entry_type": "journal", "volume": "12", "issue": "3",
            "pages": "45-67", "publisher": "", "language": "en",
        }},
        "revision": 3,
    }


def _meta() -> dict:
    return {
        "title": "低功耗存内计算研究", "subtitle": "课程论文",
        "authors": [{"name": "张三", "affiliation": "微电子学院"}],
        "abstract": "本文研究存内计算的能耗问题。",
        "keywords": ["存算一体", "ADC"], "funding": "国家自然科学基金",
        "citation_style": "gb7714", "language": "zh",
    }


class PDFSourceTests(unittest.TestCase):
    def test_front_matter_sections_and_bibliography(self) -> None:
        source = pdf_export.latex_document(_draft(), _meta())
        self.assertIn(r"\documentclass[12pt,a4paper]{ctexart}", source)
        self.assertIn("低功耗存内计算研究", source)
        self.assertIn(r"\section{引言}", source)
        self.assertIn(r"\cite{a2024}", source)
        self.assertIn(r"\bibitem{a2024}", source)
        self.assertIn("关键词", source)
        self.assertIn(r"100\% 的样本可复现", source)

    def test_author_year_style_uses_export_marker(self) -> None:
        source = pdf_export.latex_document(_draft(), _meta(), style="apa")
        self.assertIn("(Author, 2024)", source)
        self.assertNotIn(r"\cite{a2024}", source)

    def test_ctex_fontset_can_be_configured_for_linux(self) -> None:
        with patch.dict(os.environ, {"PAPERPILOT_CTEX_FONTSET": "ubuntu"}):
            source = pdf_export.latex_document(_draft(), _meta())
        self.assertIn(r"\documentclass[12pt,a4paper,fontset=ubuntu]{ctexart}", source)

    def test_acknowledgments_are_rendered_but_removed_when_blinded(self) -> None:
        meta = _meta()
        meta["acknowledgments"] = "感谢实验室同学在仿真上的帮助。"
        source = pdf_export.latex_document(_draft(), meta)
        self.assertIn(r"\section*{致谢}", source)
        self.assertIn("感谢实验室", source)
        blinded = pdf_export.latex_document(_draft(), meta, blinded=True)
        self.assertNotIn("感谢实验室", blinded)

    def test_missing_toolchain_raises_typed_error(self) -> None:
        with patch.object(pdf_export, "tool_path", return_value=""):
            self.assertFalse(pdf_export.available())
            with self.assertRaises(pdf_export.PDFUnavailable):
                pdf_export.build(_draft(), _meta())

    @unittest.skipUnless(pdf_export.available(), "本机没有 XeLaTeX")
    def test_real_xelatex_produces_a_pdf(self) -> None:
        payload = pdf_export.build(_draft(), _meta())
        self.assertTrue(payload.startswith(b"%PDF"))
        self.assertGreater(len(payload), 10_000)


class PDFApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            "PAPERPILOT_PROJECT_FILE": str(root / "projects.json"),
            "PAPERPILOT_PROJECT_DIR": str(root / "projects"),
            "PAPERPILOT_AGENT_FILE": str(root / "runs.json"),
            "PAPERPILOT_AGENT_DIR": str(root / "runs"),
        })
        self.env.start()
        self.client = TestClient(app)
        self.project = project_service.create_project("项目", "问题？")
        self.pid = self.project["id"]

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_export_draft_returns_pdf_payload(self) -> None:
        with patch.object(pdf_export, "build", return_value=b"%PDF-1.4 test"):
            result = draft_service.export_draft(self.pid, "pdf")
        self.assertEqual(result["media_type"], "application/pdf")
        self.assertTrue(result["filename"].endswith(".pdf"))
        self.assertEqual(result["content"], b"%PDF-1.4 test")

    def test_route_maps_missing_toolchain_to_503(self) -> None:
        with patch.object(pdf_export, "tool_path", return_value=""):
            response = self.client.get(f"/api/projects/{self.pid}/draft/export?format=pdf")
        self.assertEqual(response.status_code, 503)
        self.assertIn("XeLaTeX", response.json()["detail"])

    def test_route_maps_build_error_to_422(self) -> None:
        with patch.object(pdf_export, "build",
                          side_effect=pdf_export.PDFBuildError("编译失败：测试")):
            response = self.client.get(f"/api/projects/{self.pid}/draft/export?format=pdf")
        self.assertEqual(response.status_code, 422)
        self.assertIn("编译失败", response.json()["detail"])

    @unittest.skipUnless(pdf_export.available(), "本机没有 XeLaTeX")
    def test_route_downloads_a_real_pdf(self) -> None:
        response = self.client.get(f"/api/projects/{self.pid}/draft/export?format=pdf")
        self.assertEqual(response.status_code, 200, response.text[:300])
        self.assertTrue(response.content.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
