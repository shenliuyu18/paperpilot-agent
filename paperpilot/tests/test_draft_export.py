from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.services import (
    draft_export,
    draft_service,
    draft_state,
    project_service,
    project_state,
)


def _tex_binary() -> str | None:
    for name in ("xelatex", "pdflatex"):
        path = shutil.which(name)
        if path:
            return path
    return None


TEX_BINARY = _tex_binary()


DRAFT = {
    "project_id": "prj-1",
    "revision": 3,
    "title": "低功耗 ADC 研究草稿",
    "citation_style": "numeric",
    "sections": [{
        "id": "sec-001",
        "title": "引言",
        "blocks": [
            {
                "id": "blk-001",
                "type": "paragraph",
                "text": "存内计算降低数据搬运开销 & 功耗。",
                "evidence_ids": ["pev-1"],
                "citation_keys": ["author2024"],
            },
            {
                "id": "blk-002",
                "type": "bullet",
                "text": "原型达到 4.2 fJ。",
                "evidence_ids": ["pev-1"],
                "citation_keys": ["author2024"],
            },
        ],
    }],
    "citations": {
        "P1": {
            "key": "author2024",
            "title": "A Low-Power ADC",
            "authors": ["Alice Author", "Bob Builder"],
            "year": 2024,
            "venue": "JSSC",
            "doi": "10.1/x",
            "url": "https://example.org/p1",
        }
    },
    "versions": [],
}

PROJECT = {"id": "prj-1", "title": "低功耗 ADC 研究", "brief": {"question": "如何降低功耗？"}}
MEMORY = {"evidence": [{"id": "pev-1", "quote": "The prototype consumes 4.2 fJ.", "title": "A Low-Power ADC"}], "papers": {}}


class DraftExportFormatTests(unittest.TestCase):
    def test_markdown_contains_sections_citations_and_appendix(self) -> None:
        text = draft_export.export_markdown(DRAFT, PROJECT, MEMORY, appendix=True)
        self.assertIn("# 低功耗 ADC 研究草稿", text)
        self.assertIn("## 引言", text)
        self.assertIn("[1]", text)
        self.assertIn("## 参考文献", text)
        self.assertIn("## 证据附录", text)
        self.assertIn("The prototype consumes 4.2 fJ.", text)

    def test_markdown_omits_appendix_by_default(self) -> None:
        text = draft_export.export_markdown(DRAFT, PROJECT, MEMORY)
        self.assertIn("## 参考文献", text)
        self.assertNotIn("## 证据附录", text)

    def test_markdown_author_year_style(self) -> None:
        draft = {**DRAFT, "citation_style": "author-year"}
        text = draft_export.export_markdown(draft, PROJECT, MEMORY)
        self.assertIn("(Author, 2024)", text)
        self.assertNotIn("[1]", text)

    def test_bibtex_only_exports_cited_entries(self) -> None:
        text = draft_export.export_bibtex(DRAFT)
        self.assertIn("@article{author2024", text)
        self.assertIn("Alice Author and Bob Builder", text)
        self.assertIn("A Low-Power ADC", text)
        draft = {
            **DRAFT,
            "citations": {
                **DRAFT["citations"],
                "P2": {
                    "key": "unused2020",
                    "title": "Never Cited",
                    "authors": ["Carol Citer"],
                    "year": 2020,
                    "venue": "V",
                    "doi": "",
                    "url": "",
                },
            },
        }
        self.assertNotIn("unused2020", draft_export.export_bibtex(draft))

    def test_latex_zip_contains_tex_and_bib_and_escapes(self) -> None:
        payload = draft_export.export_latex_zip(DRAFT)
        archive = zipfile.ZipFile(io.BytesIO(payload))
        names = archive.namelist()
        self.assertIn("main.tex", names)
        self.assertIn("refs.bib", names)
        tex = archive.read("main.tex").decode("utf-8")
        self.assertIn("\\section{引言}", tex)
        self.assertIn("\\cite{author2024}", tex)
        self.assertIn("\\&", tex)
        self.assertIn("\\bibliography{refs}", tex)
        bib = archive.read("refs.bib").decode("utf-8")
        self.assertIn("@article{author2024", bib)
        self.assertNotIn("\\&", bib)

    def test_latex_preamble_loads_cjk_support(self) -> None:
        archive = zipfile.ZipFile(io.BytesIO(draft_export.export_latex_zip(DRAFT)))
        tex = archive.read("main.tex").decode("utf-8")
        self.assertIn("\\documentclass{article}", tex)
        self.assertIn("\\usepackage[UTF8]{ctex}", tex)
        self.assertIn("\\title{低功耗 ADC 研究草稿}", tex)

    @unittest.skipUnless(TEX_BINARY, "no xelatex/pdflatex binary installed")
    def test_latex_chinese_draft_compiles(self) -> None:
        payload = draft_export.export_latex_zip(DRAFT)
        archive = zipfile.ZipFile(io.BytesIO(payload))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("main.tex", "refs.bib"):
                (root / name).write_bytes(archive.read(name))
            result = subprocess.run(
                [
                    TEX_BINARY,
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "main.tex",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=180,
            )
            output = result.stdout + result.stderr
            log = root / "main.log"
            if log.exists():
                output += log.read_text(encoding="utf-8", errors="replace")
            self.assertEqual(result.returncode, 0, output[-4000:])
            self.assertNotIn("Missing character", output, output[-4000:])
            self.assertTrue((root / "main.pdf").exists())

    def test_export_sanitizes_unsafe_citation_keys(self) -> None:
        evil = "evil}\\input{boom"
        draft = copy.deepcopy(DRAFT)
        draft["citations"]["P1"]["key"] = evil
        for block in draft["sections"][0]["blocks"]:
            block["citation_keys"] = [evil]
        archive = zipfile.ZipFile(io.BytesIO(draft_export.export_latex_zip(draft)))
        tex = archive.read("main.tex").decode("utf-8")
        bib = archive.read("refs.bib").decode("utf-8")
        self.assertIn("\\cite{evilinputboom}", tex)
        self.assertIn("@article{evilinputboom,", bib)
        self.assertNotIn(evil, tex)
        self.assertNotIn(evil, bib)

    def test_export_falls_back_to_stable_key_for_empty_sanitized_key(self) -> None:
        evil = "{}"
        digest = hashlib.sha1(evil.encode("utf-8")).hexdigest()
        draft = copy.deepcopy(DRAFT)
        draft["citations"]["P1"]["key"] = evil
        for block in draft["sections"][0]["blocks"]:
            block["citation_keys"] = [evil]
        archive = zipfile.ZipFile(io.BytesIO(draft_export.export_latex_zip(draft)))
        tex = archive.read("main.tex").decode("utf-8")
        bib = archive.read("refs.bib").decode("utf-8")
        self.assertIn(f"\\cite{{key-{digest}}}", tex)
        self.assertIn(f"@article{{key-{digest},", bib)

    def test_json_and_filename(self) -> None:
        payload = json.loads(draft_export.export_json(DRAFT, PROJECT, MEMORY))
        self.assertEqual(payload["draft"]["sections"][0]["title"], "引言")
        self.assertEqual(payload["project"]["id"], "prj-1")
        self.assertEqual(payload["evidence"][0]["id"], "pev-1")
        self.assertEqual(
            draft_export.download_name({**PROJECT, "title": "a/b:c"}, "markdown"),
            "a_b_c.md",
        )

    def test_download_names_use_safe_extensions(self) -> None:
        self.assertEqual(
            draft_export.download_name(PROJECT, "latex"), "低功耗_ADC_研究.zip"
        )
        self.assertTrue(draft_export.download_name(PROJECT, "bibtex").endswith(".bib"))
        self.assertTrue(draft_export.download_name(PROJECT, "json").endswith(".json"))
        self.assertEqual(draft_export.download_name({}, "markdown"), "paperpilot-draft.md")

    def test_download_name_avoids_unsafe_windows_names(self) -> None:
        self.assertEqual(
            draft_export.download_name({"title": "."}, "markdown"),
            "paperpilot-draft.md",
        )
        self.assertEqual(
            draft_export.download_name({"title": "Report. "}, "markdown"),
            "Report.md",
        )
        for reserved in ("CON", "PRN", "AUX", "NUL", "COM1", "LPT9", "con.txt"):
            self.assertEqual(
                draft_export.download_name({"title": reserved}, "markdown"),
                "paperpilot-draft.md",
            )
        self.assertEqual(
            draft_export.download_name({"title": ".hidden"}, "json"), "hidden.json"
        )
        self.assertFalse(
            draft_export.download_name({"title": ".."}, "latex").startswith(".")
        )


class DraftExportServiceTests(unittest.TestCase):
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
        self.project = project_service.create_project("低功耗 ADC 研究", "如何降低功耗？")
        memory = project_state.read_memory(self.project["id"])
        memory["evidence"] = [{
            "id": "pev-1", "paper_id": "P1", "title": "A Low-Power ADC",
            "section": "Results", "page": 2, "quote": "The prototype consumes 4.2 fJ.",
            "verified": True, "kind": "text",
        }]
        memory["papers"] = {"P1": {
            "id": "P1", "title": "A Low-Power ADC", "authors": ["Alice Author"],
            "year": 2024, "venue": "JSSC", "doi": "10.1/x", "url": "https://example.org/p1",
        }}
        project_state.write_memory(self.project["id"], memory)
        payload = {
            "revision": 0,
            "title": "低功耗 ADC 研究草稿",
            "citation_style": "numeric",
            "sections": [{
                "id": "sec-001",
                "title": "引言",
                "blocks": [{
                    "id": "blk-001",
                    "type": "paragraph",
                    "text": "存内计算降低数据搬运开销 & 功耗。",
                    "evidence_ids": ["pev-1"],
                    "citation_keys": [],
                }],
            }],
        }
        draft = draft_service.save_draft(self.project["id"], payload)
        added = draft_service.add_citation(
            self.project["id"], "P1", draft["revision"]
        )
        payload["revision"] = added["revision"]
        payload["sections"][0]["blocks"][0]["citation_keys"] = [added["key"]]
        draft_service.save_draft(self.project["id"], payload)

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_export_draft_returns_content_filename_and_media_type(self) -> None:
        markdown = draft_service.export_draft(
            self.project["id"], "markdown", appendix=True
        )
        self.assertIn("text/markdown", markdown["media_type"])
        self.assertTrue(markdown["filename"].endswith(".md"))
        self.assertIn("## 引言".encode("utf-8"), markdown["content"])
        self.assertIn("[1]".encode("utf-8"), markdown["content"])
        self.assertIn("The prototype consumes 4.2 fJ.".encode("utf-8"), markdown["content"])

        latex = draft_service.export_draft(self.project["id"], "latex")
        self.assertEqual(latex["media_type"], "application/zip")
        self.assertTrue(latex["filename"].endswith(".zip"))
        archive = zipfile.ZipFile(io.BytesIO(latex["content"]))
        self.assertIn("main.tex", archive.namelist())
        self.assertIn("refs.bib", archive.namelist())

        bibtex = draft_service.export_draft(self.project["id"], "bibtex")
        self.assertIn("application/x-bibtex", bibtex["media_type"])
        self.assertIn(b"@article{author2024", bibtex["content"])

        payload = json.loads(
            draft_service.export_draft(self.project["id"], "json")["content"]
        )
        self.assertEqual(payload["project"]["id"], self.project["id"])
        self.assertEqual(payload["evidence"][0]["id"], "pev-1")

    def test_export_draft_rejects_unknown_format_and_missing_project(self) -> None:
        with self.assertRaises(draft_service.DraftValidation):
            draft_service.export_draft(self.project["id"], "rst")
        with self.assertRaises(KeyError):
            draft_service.export_draft("prj-missing", "markdown")

    def test_save_draft_rejects_unsafe_citation_key(self) -> None:
        draft = draft_service.get_draft(self.project["id"])
        citations = copy.deepcopy(draft["citations"])
        citations["P2"] = {
            "key": "evil}\\input{boom",
            "title": "Evil",
            "authors": ["Mallory"],
            "year": 2024,
            "venue": "V",
            "doi": "",
            "url": "",
        }
        payload = {
            "revision": draft["revision"],
            "sections": draft["sections"],
            "citations": citations,
        }
        with self.assertRaises(draft_service.DraftValidation):
            draft_service.save_draft(self.project["id"], payload)
        self.assertEqual(
            draft_service.get_draft(self.project["id"])["revision"],
            draft["revision"],
        )

    def test_export_draft_does_not_mutate_draft_file_or_revision(self) -> None:
        path = draft_state.draft_path(self.project["id"])
        before_bytes = path.read_bytes()
        before_revision = draft_service.get_draft(self.project["id"])["revision"]
        for fmt in ("markdown", "latex", "bibtex", "json"):
            draft_service.export_draft(self.project["id"], fmt, appendix=True)
        self.assertEqual(path.read_bytes(), before_bytes)
        self.assertEqual(
            draft_service.get_draft(self.project["id"])["revision"],
            before_revision,
        )


if __name__ == "__main__":
    unittest.main()
