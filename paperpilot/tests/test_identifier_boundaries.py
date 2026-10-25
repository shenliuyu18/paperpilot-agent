"""Identifiers that become file names must not be able to leave their directory.

Windows treats a backslash as a path separator, so a URL segment like ``..\\llm_settings.secrets``
walks out of the parsed-cache directory and the endpoint happily returns that JSON — on a local
install that is the stored API key, readable by any web page because CORS is ``*``.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from fastapi.testclient import TestClient

from app import config, store
from app.main import app
from app.services import draft_state, identifiers


class ParsedPaperBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pp-ident-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._dirs = (config.PARSED_DIR, config.UPLOAD_DIR, config.DATA_DIR)
        config.PARSED_DIR = self.tmp / "parsed"
        config.UPLOAD_DIR = self.tmp / "uploads"
        for directory in (config.PARSED_DIR, config.UPLOAD_DIR):
            directory.mkdir(parents=True, exist_ok=True)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        config.PARSED_DIR, config.UPLOAD_DIR, config.DATA_DIR = self._dirs

    def _sentinel_outside_parsed(self, name: str, payload: dict) -> Path:
        path = self.tmp / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_traversal_is_rejected_and_does_not_leak_the_sibling_file(self) -> None:
        secret = self._sentinel_outside_parsed("llm_settings.secrets", {"api_key": "sk-secret-should-never-leak"})
        for bad in ("..\\llm_settings.secrets", "..%5Cllm_settings.secrets", "../llm_settings.secrets"):
            with self.subTest(bad=bad):
                response = self.client.get(
                    f"/api/paper/{urllib.parse.quote(bad, safe='')}/parsed")
                self.assertIn(response.status_code, (400, 404), bad)
                self.assertNotIn("sk-secret-should-never-leak", response.text)
        self.assertTrue(secret.is_file(), "the test file must still be there; the request must not touch it")

    def test_a_legitimate_parsed_id_still_reads(self) -> None:
        (config.PARSED_DIR / "upload-abc12345.json").write_text('{"tldr": "ok"}', encoding="utf-8")
        response = self.client.get("/api/paper/upload-abc12345/parsed")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tldr"], "ok")

    def test_arxiv_style_and_long_ids_are_accepted(self) -> None:
        for paper_id in ("2609.07059v3", "x" * 120):
            with self.subTest(paper_id=paper_id[:12]):
                self.assertTrue(identifiers.is_safe_id(paper_id))

    def test_separator_and_traversal_shapes_are_rejected(self) -> None:
        for bad in ("", ".", "..", "a/b", "a\\b", "x" * 121, "_leading", "名字", "a b", "a\0b"):
            with self.subTest(bad=bad):
                self.assertFalse(identifiers.is_safe_id(bad))


class StoreBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pp-ident-store-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._upload = config.UPLOAD_DIR
        config.UPLOAD_DIR = self.tmp / "uploads"
        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        config.UPLOAD_DIR = self._upload

    def test_register_upload_refuses_an_id_that_escapes_the_upload_dir(self) -> None:
        with self.assertRaises(ValueError):
            store.register_upload({"id": "..\\outside", "title": "x"})
        self.assertEqual(list(self.tmp.glob("outside*")), [])

    def test_draft_snapshot_refuses_an_id_that_escapes_the_versions_dir(self) -> None:
        project = self.tmp / "projects" / "prj-1"
        (project / "versions").mkdir(parents=True)
        with self.assertRaises(ValueError):
            draft_state.snapshot({"project_id": "prj-1", "blocks": []}, "..\\leak", "l", "r")
        self.assertEqual(list(project.glob("leak*")), [])


if __name__ == "__main__":
    unittest.main()
