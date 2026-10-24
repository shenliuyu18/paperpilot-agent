from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from fastapi.testclient import TestClient

from app.main import app
from app.services import project_service, project_state


class DraftApiTests(unittest.TestCase):
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
        memory = project_state.read_memory(self.project["id"])
        memory["evidence"] = [{
            "id": "pev-1", "paper_id": "P1", "title": "Paper One",
            "section": "Abstract", "page": 1, "quote": "Evidence quote",
            "verified": True, "kind": "text",
        }]
        memory["papers"] = {"P1": {
            "id": "P1", "title": "Paper One", "authors": ["Alice Author"],
            "year": 2024, "venue": "V", "doi": "", "url": "",
        }}
        project_state.write_memory(self.project["id"], memory)

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _base(self) -> str:
        return f"/api/projects/{self.project['id']}/draft"

    def test_draft_save_citation_version_audit_export(self) -> None:
        base = self._base()
        draft = self.client.get(base).json()["draft"]
        self.assertEqual(draft["revision"], 0)
        payload = {
            "revision": 0,
            "title": "草稿",
            "citation_style": "numeric",
            "sections": [{
                "id": "sec-001", "title": "引言",
                "blocks": [{
                    "id": "blk-001", "type": "paragraph",
                    "text": "研究内容。", "evidence_ids": ["pev-1"], "citation_keys": [],
                }],
            }],
        }
        saved = self.client.put(base, json=payload)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["draft"]["revision"], 1)
        citation = self.client.post(f"{base}/citations", json={
            "paper_id": "P1", "revision": 1,
        })
        self.assertEqual(citation.status_code, 200)
        self.assertEqual(citation.json()["key"], "author2024")
        version = self.client.post(f"{base}/versions", json={"label": "第一版"})
        self.assertEqual(version.status_code, 200)
        audit = self.client.post(f"{base}/audit")
        self.assertEqual(audit.status_code, 200)
        self.assertIn("counts", audit.json())
        export = self.client.get(f"{base}/export?format=markdown")
        self.assertEqual(export.status_code, 200)
        self.assertIn("text/markdown", export.headers["content-type"])
        self.assertIn("草稿", export.text)

    def test_save_accepts_inline_citation_map(self) -> None:
        base = self._base()
        saved = self.client.put(base, json={
            "revision": 0, "title": "内联引用", "citation_style": "gb7714",
            "sections": [{"id": "sec-001", "title": "引言", "blocks": [{
                "id": "blk-001", "type": "paragraph", "text": "正文 [P1]。",
                "evidence_ids": [], "citation_keys": ["P1"],
            }]}],
            "citations": {"P1": {
                "key": "P1", "title": "Paper One", "authors": ["Alice Author"],
                "year": 2024, "venue": "V", "doi": "", "url": "", "entry_type": "preprint",
            }},
        })
        self.assertEqual(saved.status_code, 200)
        draft = self.client.get(base).json()["draft"]
        self.assertEqual(draft["citations"]["P1"]["key"], "P1")
        self.assertEqual(draft["sections"][0]["blocks"][0]["citation_keys"], ["P1"])
        markdown = self.client.get(f"{base}/export?format=markdown").text
        self.assertIn("Alice Author", markdown)
        self.assertIn("[P1]", markdown)

    def test_save_without_citations_keeps_existing_map(self) -> None:
        base = self._base()
        draft = self.client.get(base).json()["draft"]
        added = self.client.post(f"{base}/citations", json={
            "paper_id": "P1", "revision": draft["revision"],
        }).json()
        self.assertEqual(added["key"], "author2024")
        saved = self.client.put(base, json={
            "revision": added["revision"], "title": "保留引用", "citation_style": "numeric",
            "sections": [{"id": "sec-001", "title": "引言", "blocks": [{
                "id": "blk-001", "type": "paragraph", "text": "正文。",
                "evidence_ids": [], "citation_keys": ["author2024"],
            }]}],
        })
        self.assertEqual(saved.status_code, 200)
        draft = self.client.get(base).json()["draft"]
        self.assertEqual(draft["citations"]["P1"]["key"], "author2024")

    def test_save_null_citations_clears_existing_map(self) -> None:
        base = self._base()
        draft = self.client.get(base).json()["draft"]
        added = self.client.post(f"{base}/citations", json={
            "paper_id": "P1", "revision": draft["revision"],
        }).json()
        saved = self.client.put(base, json={
            "revision": added["revision"], "title": "清空引用", "citation_style": "numeric",
            "sections": [{"id": "sec-001", "title": "引言", "blocks": [{
                "id": "blk-001", "type": "paragraph", "text": "正文。",
                "evidence_ids": [], "citation_keys": [],
            }]}],
            "citations": None,
        })
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.client.get(base).json()["draft"]["citations"], {})

    def test_save_rejects_non_object_citation_entry(self) -> None:
        base = self._base()
        saved = self.client.put(base, json={
            "revision": 0, "title": "坏引用", "citation_style": "numeric",
            "sections": [{"id": "sec-001", "title": "引言", "blocks": []}],
            "citations": {"P1": "Paper One"},
        })
        self.assertEqual(saved.status_code, 422)
        self.assertIn("引用条目格式不正确", saved.text)

    def test_conflict_maps_to_409_and_unknown_evidence_422(self) -> None:
        base = self._base()
        payload = {
            "revision": 0, "title": "草稿", "citation_style": "numeric",
            "sections": [{"id": "sec-001", "title": "引言", "blocks": []}],
        }
        self.client.put(base, json=payload)
        conflict = self.client.put(base, json=payload)
        self.assertEqual(conflict.status_code, 409)
        invalid = self.client.put(base, json={
            **payload, "revision": 1,
            "sections": [{"id": "sec-001", "title": "引言", "blocks": [{
                "id": "blk-001", "type": "paragraph", "text": "x",
                "evidence_ids": ["missing"],
            }]}],
        })
        self.assertEqual(invalid.status_code, 422)

    def test_versions_list_get_and_restore(self) -> None:
        base = self._base()
        self.client.put(base, json={
            "revision": 0, "title": "第一稿", "citation_style": "numeric",
            "sections": [{"id": "sec-001", "title": "引言", "blocks": [{
                "id": "blk-001", "type": "paragraph", "text": "原始内容",
            }]}],
        })
        created = self.client.post(f"{base}/versions", json={"label": "第一版"})
        self.assertEqual(created.status_code, 200)
        version = created.json()["version"]
        self.assertEqual(version["label"], "第一版")
        self.assertIn("summary", version)

        listing = self.client.get(f"{base}/versions")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["versions"][0]["id"], version["id"])
        snapshot = self.client.get(f"{base}/versions/{version['id']}")
        self.assertEqual(snapshot.status_code, 200)
        body = snapshot.json()["version"]
        self.assertEqual(body["title"], "第一稿")
        self.assertEqual(
            body["sections"][0]["blocks"][0]["text"], "原始内容"
        )

        changed = self.client.put(base, json={
            "revision": 1, "title": "第二稿", "citation_style": "numeric",
            "sections": [{"id": "sec-001", "title": "引言", "blocks": [{
                "id": "blk-001", "type": "paragraph", "text": "修改内容",
            }]}],
        })
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["draft"]["revision"], 2)

        restored = self.client.post(
            f"{base}/versions/{version['id']}/restore"
        )
        self.assertEqual(restored.status_code, 200)
        draft = restored.json()["draft"]
        self.assertEqual(draft["title"], "第一稿")
        self.assertEqual(draft["sections"][0]["blocks"][0]["text"], "原始内容")
        self.assertEqual(draft["revision"], 3)
        labels = [
            item["label"]
            for item in self.client.get(f"{base}/versions").json()["versions"]
        ]
        self.assertIn("恢复前自动保存", labels)

    def test_not_found_conflict_and_validation_mapping(self) -> None:
        missing = "/api/projects/proj-missing/draft"
        payload = {
            "revision": 0, "title": "草稿", "citation_style": "numeric",
            "sections": [],
        }
        self.assertEqual(self.client.get(missing).status_code, 404)
        self.assertEqual(self.client.put(missing, json=payload).status_code, 404)
        self.assertEqual(self.client.post(
            f"{missing}/citations", json={"paper_id": "P1", "revision": 0}
        ).status_code, 404)
        self.assertEqual(self.client.post(
            f"{missing}/versions", json={"label": "x"}
        ).status_code, 404)
        self.assertEqual(self.client.get(f"{missing}/versions").status_code, 404)
        self.assertEqual(
            self.client.get(f"{missing}/versions/ver-001").status_code, 404
        )
        self.assertEqual(self.client.post(
            f"{missing}/versions/ver-001/restore"
        ).status_code, 404)
        self.assertEqual(self.client.post(f"{missing}/audit").status_code, 404)
        self.assertEqual(self.client.get(missing + "/export").status_code, 404)

        base = self._base()
        self.assertEqual(self.client.post(
            f"{base}/citations", json={"paper_id": "nope", "revision": 0}
        ).status_code, 404)
        self.assertEqual(
            self.client.get(f"{base}/versions/ver-999").status_code, 404
        )
        self.assertEqual(self.client.post(
            f"{base}/versions/ver-999/restore"
        ).status_code, 404)
        pdf = self.client.get(f"{base}/export?format=pdf")
        self.assertIn(pdf.status_code, (200, 503))
        if pdf.status_code == 200:
            self.assertTrue(pdf.content.startswith(b"%PDF"))
        else:
            self.assertIn("XeLaTeX", pdf.json()["detail"])
        self.assertEqual(self.client.put(base, json={"title": "无修订号"}).status_code, 422)

    def test_export_formats_and_chinese_download_name(self) -> None:
        base = self._base()
        self.assertEqual(self.client.put(base, json={
            "revision": 0, "title": "草稿", "citation_style": "numeric",
            "sections": [{"id": "sec-001", "title": "引言", "blocks": [{
                "id": "blk-001", "type": "paragraph", "text": "研究内容。",
                "evidence_ids": ["pev-1"],
            }]}],
        }).status_code, 200)

        markdown = self.client.get(f"{base}/export?format=markdown")
        self.assertEqual(markdown.status_code, 200)
        self.assertIn("text/markdown", markdown.headers["content-type"])
        disposition = markdown.headers["content-disposition"]
        self.assertTrue(disposition.isascii())
        self.assertIn('filename="paperpilot-draft.md"', disposition)
        self.assertIn(
            "filename*=UTF-8''" + quote("项目.md", safe=""), disposition
        )
        self.assertIn("草稿", markdown.text)

        with_appendix = self.client.get(
            f"{base}/export?format=markdown&appendix=1"
        )
        self.assertIn("证据附录", with_appendix.text)
        self.assertIn("Evidence quote", with_appendix.text)
        without_appendix = self.client.get(
            f"{base}/export?format=markdown&appendix=0"
        )
        self.assertNotIn("证据附录", without_appendix.text)

        latex = self.client.get(f"{base}/export?format=latex")
        self.assertEqual(latex.status_code, 200)
        self.assertIn("application/zip", latex.headers["content-type"])
        self.assertEqual(latex.content[:2], b"PK")
        self.assertIn('filename="paperpilot-draft.zip"',
                      latex.headers["content-disposition"])

        bibtex = self.client.get(f"{base}/export?format=bibtex")
        self.assertEqual(bibtex.status_code, 200)
        self.assertIn("application/x-bibtex", bibtex.headers["content-type"])

        payload = self.client.get(f"{base}/export?format=json")
        self.assertEqual(payload.status_code, 200)
        self.assertIn("application/json", payload.headers["content-type"])


if __name__ == "__main__":
    unittest.main()


class CitationStyleSyncTests(unittest.TestCase):
    """v6.3: the draft drawer and the manuscript drawer must not disagree."""

    setUp = DraftApiTests.setUp
    tearDown = DraftApiTests.tearDown

    def test_draft_style_flows_to_manuscript_and_export(self) -> None:
        pid = self.project["id"]
        draft = self.client.get(f"/api/projects/{pid}/draft").json()["draft"]
        added = self.client.post(f"/api/projects/{pid}/draft/citations",
                                 json={"paper_id": "P1", "revision": draft["revision"]}).json()
        draft = self.client.get(f"/api/projects/{pid}/draft").json()["draft"]
        draft["sections"] = [{"id": "sec-001", "title": "引言", "blocks": [
            {"id": "blk-001", "type": "paragraph", "text": "正文引用。", "evidence_ids": [],
             "citation_keys": [added["key"]]}]}]
        saved = self.client.put(f"/api/projects/{pid}/draft", json={
            "revision": draft["revision"], "title": "T", "citation_style": "apa",
            "sections": draft["sections"], "citations": draft["citations"],
        })
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.client.get(f"/api/projects/{pid}/manuscript").json()["citation_style"], "apa")
        markdown = self.client.get(f"/api/projects/{pid}/draft/export?format=markdown").content.decode("utf-8")
        self.assertIn("(2024).", markdown)
        self.assertNotIn("[1] Alice Author", markdown)

    def test_manuscript_style_flows_back_without_bumping_revision(self) -> None:
        pid = self.project["id"]
        draft = self.client.get(f"/api/projects/{pid}/draft").json()["draft"]
        saved = self.client.put(f"/api/projects/{pid}/draft", json={
            "revision": draft["revision"], "title": "T", "citation_style": "gb7714",
            "sections": [{"id": "sec-001", "title": "引言", "blocks": [
                {"id": "blk-001", "type": "paragraph", "text": "正文。", "evidence_ids": [], "citation_keys": []}]}],
        })
        self.assertEqual(saved.status_code, 200)
        after_save = self.client.get(f"/api/projects/{pid}/draft").json()["draft"]
        response = self.client.put(f"/api/projects/{pid}/manuscript", json={"citation_style": "ieee"})
        self.assertEqual(response.status_code, 200)
        after_style = self.client.get(f"/api/projects/{pid}/draft").json()["draft"]
        self.assertEqual(after_style["citation_style"], "ieee")
        self.assertEqual(after_style["revision"], after_save["revision"])
