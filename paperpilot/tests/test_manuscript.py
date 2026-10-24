from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import draft_service, draft_state, manuscript, project_service, project_state


class ManuscriptTests(unittest.TestCase):
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
        self.project = project_service.create_project("稿件项目", "问题？")
        self.pid = self.project["id"]
        memory = project_state.read_memory(self.pid)
        memory["evidence"] = [{
            "id": "pev-1", "paper_id": "P1", "title": "Paper One", "section": "Results",
            "page": 1, "quote": "The prototype consumes 4.2 fJ per conversion.",
            "verified": True, "kind": "text",
        }]
        memory["papers"] = {"P1": {
            "id": "P1", "title": "Paper One", "authors": ["Alice Author"], "year": "2024",
            "venue": "Journal of Things", "doi": "10.1000/xyz", "url": "",
        }}
        project_state.write_memory(self.pid, memory)

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _draft(self) -> dict:
        draft_service.ensure_from_markdown(
            self.pid, "## 引言\n\nThe prototype consumes 4.2 fJ per conversion.\n"
        )
        draft = draft_state.read_draft(self.pid)
        block = draft["sections"][0]["blocks"][0]
        block["evidence_ids"] = ["pev-1"]
        draft_state.write_draft(self.pid, draft)
        return draft_state.read_draft(self.pid)

    def test_defaults_are_empty_and_follow_draft_style(self) -> None:
        meta = manuscript.read(self.pid)
        self.assertEqual(meta["title"], "")
        self.assertEqual(meta["citation_style"], "numeric")  # falls back to the empty draft's legacy style

    def test_update_persists_and_round_trips(self) -> None:
        saved = manuscript.update(self.pid, {
            "title": "低功耗 ADC 研究", "subtitle": "课程论文",
            "authors": [{"name": "张三", "affiliation": "微电子学院", "email": "z@example.edu"}],
            "abstract": "本文研究……", "keywords": ["存算一体", "ADC"],
            "funding": "国家自然科学基金", "citation_style": "gb7714", "language": "zh",
        })
        self.assertEqual(saved["title"], "低功耗 ADC 研究")
        again = manuscript.read(self.pid)
        self.assertEqual(again["authors"][0]["name"], "张三")
        self.assertEqual(again["citation_style"], "gb7714")
        self.assertEqual(again["keywords"], ["存算一体", "ADC"])

    def test_updates_merge_without_clearing_other_fields(self) -> None:
        manuscript.update(self.pid, {"title": "标题", "keywords": ["a"]})
        manuscript.update(self.pid, {"abstract": "摘要"})
        meta = manuscript.read(self.pid)
        self.assertEqual(meta["title"], "标题")
        self.assertEqual(meta["keywords"], ["a"])
        self.assertEqual(meta["abstract"], "摘要")

    def test_limits_are_enforced(self) -> None:
        with self.assertRaises(ValueError):
            manuscript.update(self.pid, {"title": "长" * 201})
        with self.assertRaises(ValueError):
            manuscript.update(self.pid, {"citation_style": "chicago"})
        with self.assertRaises(ValueError):
            manuscript.update(self.pid, {"language": "fr"})
        with self.assertRaises(ValueError):
            manuscript.update(self.pid, {"authors": [{"name": f"A{i}"} for i in range(21)]})
        with self.assertRaises(ValueError):
            manuscript.update(self.pid, {"keywords": [f"k{i}" for i in range(13)]})
        with self.assertRaises(ValueError):
            manuscript.update(self.pid, {"authors": [{"name": ""}]})

    def test_keywords_deduplicate(self) -> None:
        meta = manuscript.update(self.pid, {"keywords": ["ADC", "ADC", "存算"]})
        self.assertEqual(meta["keywords"], ["ADC", "存算"])

    def test_word_count_mixes_cjk_and_latin(self) -> None:
        self.assertEqual(manuscript.word_count("存内计算 降低 energy 38%"), 8)

    def test_preflight_flags_missing_metadata_and_unbound_blocks(self) -> None:
        self._draft()
        report = manuscript.preflight(self.pid, "gb7714")
        kinds = {(item["kind"], item["level"]) for item in report["items"]}
        self.assertIn(("metadata", "error"), kinds)       # no title / authors
        self.assertTrue(report["blocking"])
        self.assertEqual(report["body_blocks"], 1)

    def test_preflight_flags_unsupported_claim_and_contradiction(self) -> None:
        draft = self._draft()
        block = draft["sections"][0]["blocks"][0]
        block["claims"] = [{
            "id": "clm-001", "text": "The prototype consumes 1.1 fJ per conversion.",
            "evidence_ids": ["pev-1"], "stance": "unsupported", "reason": "数值缺失",
        }]
        draft_state.write_draft(self.pid, draft)
        manuscript.update(self.pid, {"title": "标题", "authors": [{"name": "张三"}]})

        report = manuscript.preflight(self.pid)
        kinds = {item["kind"] for item in report["items"]}
        self.assertIn("claim_unsupported", kinds)

    def test_preflight_flags_caption_and_cross_reference_gaps(self) -> None:
        draft_service.ensure_from_markdown(
            self.pid,
            "## 结果\n\n图 1 展示了能耗。\n\n图 3 是第二张图。\n\n见表 2。\n",
        )
        manuscript.update(self.pid, {"title": "标题", "authors": [{"name": "张三"}]})
        report = manuscript.preflight(self.pid)
        messages = " ".join(item["message"] for item in report["items"])
        self.assertIn("题注编号不连续", messages)
        self.assertIn("引用了不存在的表", messages)

    def test_preflight_counts_formulas_and_reports_docx_limit(self) -> None:
        draft_service.ensure_from_markdown(
            self.pid, "## 方法\n\n目标函数为 $L = x^2 + y^2$，其中 $x$ 为输入。\n"
        )
        manuscript.update(self.pid, {"title": "标题", "authors": [{"name": "张三"}]})
        report = manuscript.preflight(self.pid)
        self.assertEqual(report["formulas"], 2)
        self.assertTrue(any(item["kind"] == "formulas" for item in report["items"]))

    def test_references_preview_markers_and_issues(self) -> None:
        draft = self._draft()
        draft["citations"] = {"a2024": {
            "key": "a2024", "title": "Paper One", "authors": ["Alice Author"], "year": "2024",
            "venue": "Journal of Things", "doi": "", "url": "", "entry_type": "journal",
            "volume": "", "issue": "", "pages": "", "publisher": "", "language": "en",
        }}
        draft["sections"][0]["blocks"][0]["citation_keys"] = ["a2024"]
        draft_state.write_draft(self.pid, draft)

        preview = manuscript.references_preview(self.pid, "gb7714")
        self.assertEqual(preview["style"], "gb7714")
        self.assertEqual(preview["markers"][0]["marker"], " [1]")
        self.assertTrue(preview["references"][0]["text"].startswith("[1]"))
        fields = {item["field"] for item in preview["issues"]}
        self.assertIn("volume", fields)
        with self.assertRaises(ValueError):
            manuscript.references_preview(self.pid, "chicago")

    def test_preflight_does_not_mutate_draft_or_manuscript(self) -> None:
        self._draft()
        manuscript.update(self.pid, {"title": "标题", "authors": [{"name": "张三"}]})
        before_draft = copy.deepcopy(draft_state.read_draft(self.pid))
        before_meta = copy.deepcopy(manuscript.read(self.pid))
        manuscript.preflight(self.pid)
        self.assertEqual(draft_state.read_draft(self.pid), before_draft)
        self.assertEqual(manuscript.read(self.pid), before_meta)
