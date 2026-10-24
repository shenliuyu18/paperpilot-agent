from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import draft_state, project_service, project_state


class ManuscriptApiTests(unittest.TestCase):
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
        self.client = TestClient(app)
        self.project = project_service.create_project("项目", "问题？")
        self.pid = self.project["id"]
        memory = project_state.read_memory(self.pid)
        memory["evidence"] = [{
            "id": "pev-1", "paper_id": "P1", "title": "Paper One",
            "section": "Abstract", "page": 1, "quote": "Evidence quote",
            "verified": True, "kind": "text",
        }]
        memory["papers"] = {"P1": {
            "id": "P1", "title": "Paper One", "authors": ["Alice Author"],
            "year": 2024, "venue": "Journal of Things", "doi": "", "url": "",
        }}
        project_state.write_memory(self.pid, memory)

        draft = draft_state.read_draft(self.pid)
        draft["title"] = "草稿"
        draft["citations"] = {"a2024": {
            "key": "a2024", "title": "Paper One", "authors": ["Alice Author"], "year": "2024",
            "venue": "Journal of Things", "doi": "", "url": "", "entry_type": "journal",
            "volume": "12", "issue": "3", "pages": "45-67", "publisher": "", "language": "en",
        }}
        draft["sections"] = [{
            "id": "sec-001", "title": "引言",
            "blocks": [{"id": "blk-001", "type": "paragraph", "text": "存内计算降低数据搬运开销",
                        "evidence_ids": ["pev-1"], "citation_keys": ["a2024"], "status": "draft"}],
        }]
        draft_state.write_draft(self.pid, draft)

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_citation_styles_catalogue(self) -> None:
        response = self.client.get("/api/citation-styles")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        ids = {item["id"] for item in body["styles"]}
        self.assertEqual(ids, {"numeric", "author-year", "gb7714", "apa", "ieee"})
        self.assertEqual(body["default"], "gb7714")

    def test_manuscript_get_put_round_trip(self) -> None:
        payload = {
            "title": "低功耗存内计算研究",
            "authors": [{"name": "张三", "affiliation": "微电子学院"}],
            "abstract": "摘要正文", "keywords": ["存算一体"],
            "citation_style": "gb7714", "language": "zh",
        }
        saved = self.client.put(f"/api/projects/{self.pid}/manuscript", json=payload)
        self.assertEqual(saved.status_code, 200, saved.text)
        fetched = self.client.get(f"/api/projects/{self.pid}/manuscript")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["title"], "低功耗存内计算研究")
        self.assertEqual(fetched.json()["authors"][0]["name"], "张三")

    def test_invalid_style_and_field_limits(self) -> None:
        bad = self.client.put(f"/api/projects/{self.pid}/manuscript",
                              json={"citation_style": "chicago"})
        self.assertEqual(bad.status_code, 422)
        long_title = self.client.put(f"/api/projects/{self.pid}/manuscript",
                                     json={"title": "长" * 201})
        self.assertEqual(long_title.status_code, 422)

    def test_references_preview(self) -> None:
        response = self.client.post(f"/api/projects/{self.pid}/draft/references/preview",
                                    json={"style": "gb7714"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["style"], "gb7714")
        self.assertEqual(body["markers"][0]["marker"], " [1]")
        self.assertIn("[J]", body["references"][0]["text"])
        self.assertEqual(body["references"][0]["index"], 1)

    def test_references_preview_rejects_unknown_style(self) -> None:
        response = self.client.post(f"/api/projects/{self.pid}/draft/references/preview",
                                    json={"style": "chicago"})
        self.assertEqual(response.status_code, 422)

    def test_preflight_shape(self) -> None:
        self.client.put(f"/api/projects/{self.pid}/manuscript",
                        json={"title": "标题", "authors": [{"name": "张三"}]})
        response = self.client.post(f"/api/projects/{self.pid}/draft/preflight",
                                    json={"style": "gb7714"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        for key in ("items", "counts", "style", "blocking", "body_words"):
            self.assertIn(key, body)
        self.assertFalse(body["blocking"])
        self.assertEqual(body["style"], "gb7714")

    def test_export_docx(self) -> None:
        self.client.put(f"/api/projects/{self.pid}/manuscript",
                        json={"title": "标题", "authors": [{"name": "张三"}]})
        response = self.client.get(f"/api/projects/{self.pid}/draft/export",
                                   params={"format": "docx"})
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertIn("wordprocessingml.document", response.headers["content-type"])
        self.assertTrue(response.content.startswith(b"PK"))
        self.assertIn("attachment", response.headers.get("content-disposition", ""))

    def test_export_markdown_honours_style_override(self) -> None:
        response = self.client.get(f"/api/projects/{self.pid}/draft/export",
                                   params={"format": "markdown", "style": "ieee"})
        self.assertEqual(response.status_code, 200)
        text = response.content.decode("utf-8")
        self.assertIn('"Paper One,"', text)

    def test_export_unknown_format_is_422(self) -> None:
        response = self.client.get(f"/api/projects/{self.pid}/draft/export",
                                   params={"format": "doc"})
        self.assertEqual(response.status_code, 422)

    def test_meta_lists_v5_features(self) -> None:
        features = set(self.client.get("/api/meta").json()["features"])
        for name in ("manuscript", "docx", "citation_styles", "preflight", "claims", "contradictions"):
            self.assertIn(name, features)
