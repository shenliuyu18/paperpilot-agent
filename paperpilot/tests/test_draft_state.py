from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import draft_state, project_service


class DraftStateTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_empty_and_normalized_draft(self) -> None:
        project = project_service.create_project("项目", "问题？")
        draft = draft_state.read_draft(project["id"])
        self.assertEqual(draft["revision"], 0)
        self.assertEqual(draft["sections"], [])
        normalized, changed = draft_state.normalize_draft({
            "project_id": project["id"],
            "sections": [{"title": "引言", "blocks": [{"text": "内容"}]}],
        })
        self.assertTrue(changed)
        self.assertEqual(normalized["sections"][0]["id"], "sec-001")
        self.assertEqual(normalized["sections"][0]["blocks"][0]["id"], "blk-001")
        self.assertEqual(normalized["sections"][0]["blocks"][0]["status"], "draft")

    def test_round_trip(self) -> None:
        project = project_service.create_project("项目", "问题？")
        draft = draft_state.empty_draft(project["id"])
        draft["title"] = "草稿"
        draft_state.write_draft(project["id"], draft)
        loaded = draft_state.read_draft(project["id"])
        self.assertEqual(loaded["title"], "草稿")
        self.assertEqual(loaded["project_id"], project["id"])

    def test_corrupted_stored_draft_stays_readable(self) -> None:
        project = project_service.create_project("项目", "问题？")
        path = draft_state.draft_path(project["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "project_id": project["id"],
                    "revision": 3,
                    "citation_style": [],
                    "sections": [{
                        "id": "sec-001",
                        "title": "引言",
                        "blocks": [{
                            "id": "blk-001",
                            "type": [],
                            "status": {},
                            "text": "内容",
                        }],
                    }],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        draft = draft_state.read_draft(project["id"])
        block = draft["sections"][0]["blocks"][0]
        self.assertEqual(block["type"], "paragraph")
        self.assertEqual(block["status"], "draft")
        self.assertEqual(draft["citation_style"], "numeric")
        self.assertEqual(draft["revision"], 3)
