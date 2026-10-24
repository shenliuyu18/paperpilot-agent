from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import translation


class TranslationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self._tmp = tempfile.TemporaryDirectory()
        self._old_translation_dir = os.environ.get("PAPERPILOT_TRANSLATION_DIR")
        os.environ["PAPERPILOT_TRANSLATION_DIR"] = str(Path(self._tmp.name))
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._restore_translation_dir)

    def _restore_translation_dir(self) -> None:
        if self._old_translation_dir is None:
            os.environ.pop("PAPERPILOT_TRANSLATION_DIR", None)
        else:
            os.environ["PAPERPILOT_TRANSLATION_DIR"] = self._old_translation_dir

    def test_translation_workbench_payload_is_available_without_network(self) -> None:
        response = self.client.get("/api/translation/2606.08947v1?target=zh")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["paper"]["id"], "2606.08947v1")
        self.assertEqual(data["target"], "zh")
        self.assertGreaterEqual(len(data["sections"]), 1)
        self.assertEqual(data["sections"][0]["title"], "Abstract")
        self.assertIn("providers", data)
        self.assertFalse(data["sections"][0]["translated"])

    def test_translation_missing_paper_returns_404(self) -> None:
        response = self.client.get("/api/translation/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_translation_section_endpoint_maps_service_result(self) -> None:
        fake = {
            "paper_id": "2606.08947v1",
            "index": 0,
            "title": "Abstract",
            "page": 1,
            "target": "zh",
            "target_label": "简体中文",
            "provider": "public",
            "provider_label": "公共翻译接口",
            "model": None,
            "source_chars": 100,
            "translated_source_chars": 100,
            "truncated": False,
            "translation": "这是译文。",
            "segments": [{"source": "This is source.", "translation": "这是译文。"}],
            "checks": {},
            "note": "公共预览",
            "translated_at": "2026-10-20 12:00:00",
            "cached": False,
        }
        with patch.object(translation, "translate_section", return_value=fake):
            response = self.client.post(
                "/api/translation/section",
                json={
                    "paper_id": "2606.08947v1",
                    "section_index": 0,
                    "target": "zh",
                    "provider": "auto",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["translation"], "这是译文。")

    def test_translation_section_provider_error_is_503(self) -> None:
        with patch.object(
            translation,
            "translate_section",
            side_effect=translation.ProviderUnavailable("额度已用完"),
        ):
            response = self.client.post(
                "/api/translation/section",
                json={"paper_id": "2606.08947v1", "section_index": 0},
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("额度", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
