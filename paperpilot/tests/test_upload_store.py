from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import store
from app.services import search


class UploadStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(
            os.environ,
            {"PAPERPILOT_UPLOAD_DIR": str(self.root / "uploads")},
        )
        self.env.start()
        store.UPLOAD_PAPERS.clear()

    def tearDown(self) -> None:
        store.UPLOAD_PAPERS.clear()
        self.env.stop()
        self.tmp.cleanup()

    def test_register_and_restore(self) -> None:
        paper = {
            "id": "upload-abc123",
            "title": "Uploaded Paper",
            "authors": ["Alice"],
            "year": 2025,
            "abstract": "An uploaded abstract.",
            "sections": [{"title": "Abstract", "page": 1, "text": "Text"}],
        }
        store.register_upload(paper)
        self.assertTrue((self.root / "uploads" / "upload-abc123.paper.json").exists())
        store.UPLOAD_PAPERS.clear()
        count = store.restore_uploads()
        self.assertEqual(count, 1)
        self.assertEqual(store.UPLOAD_PAPERS["upload-abc123"]["title"], "Uploaded Paper")
        self.assertEqual(search.get_paper("upload-abc123")["authors"], ["Alice"])

    def test_corrupt_snapshot_is_skipped(self) -> None:
        upload_dir = self.root / "uploads"
        upload_dir.mkdir(parents=True)
        (upload_dir / "bad.paper.json").write_text("{broken", encoding="utf-8")
        count = store.restore_uploads()
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
