from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app import store
from app.services import (
    agent_state,
    assistant_state,
    dossier,
    draft_state,
    manuscript,
    project_service,
    project_state,
)


class DossierTests(unittest.TestCase):
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
            },
        )
        self.env.start()
        self.client = TestClient(app)
        self.project = project_service.create_project(
            "可复现档案项目", "如何验证证据链？", scope="CIM", tags=["demo"]
        )
        self.pid = self.project["id"]
        self.run = agent_state.create_run_state(
            "验证档案导出", "demo", "auto", project_id=self.pid
        )
        self.rid = self.run["id"]
        self.run["status"] = "completed"
        self.run["steps"] = [{"id": "act-001", "tool": "read", "status": "done"}]
        self.run["evidence"] = [{
            "id": "ev-001", "paper_id": "P1", "title": "Paper One",
            "section": "Results", "page": 2, "quote": "能效提升 10%",
            "claim": "能效提升", "verified": True, "kind": "result",
        }]
        report_dir = agent_state.run_dir(self.rid)
        (report_dir / "papers.json").write_text(
            json.dumps([{"id": "P1", "title": "Paper One"}], ensure_ascii=False),
            encoding="utf-8",
        )
        (report_dir / "report.md").write_text("# 档案报告\n\n证据 [P1]。\n", encoding="utf-8")
        self.run["report"] = {
            "path": str(report_dir / "report.md"), "chars": 12, "mode": "offline",
        }
        store = agent_state.read_store()
        store["runs"][self.rid] = self.run
        agent_state.write_store(store)
        projects = project_state.read_projects()
        projects["projects"][self.pid]["run_ids"] = [self.rid]
        project_state.write_projects(projects)
        memory = project_state.empty_memory(self.pid)
        memory["evidence"] = [{
            "id": "pev-001", "paper_id": "P1", "title": "Paper One",
            "section": "Results", "page": 2, "quote": "能效提升 10%",
            "claim": "能效提升", "verified": True, "kind": "result",
            "origin": {"run_id": self.rid, "tool": "evidence"},
        }]
        memory["reconciled_runs"] = [self.rid]
        memory["papers"] = {"P1": {"id": "P1", "title": "Paper One"}}
        project_state.write_memory(self.pid, memory)
        draft = draft_state.empty_draft(self.pid)
        draft["revision"] = 3
        draft["title"] = "档案草稿"
        draft["sections"] = [{
            "id": "sec-001", "title": "引言", "blocks": [{
                "id": "blk-001", "type": "paragraph", "status": "checked",
                "text": "能效提升 10%", "evidence_ids": ["pev-001"], "citation_keys": [],
            }],
        }]
        draft_state.write_draft(self.pid, draft)
        manuscript.update(self.pid, {"title": "档案稿件", "authors": [{"name": "测试作者"}]})
        assistant_state.append_message(self.pid, {"role": "user", "text": "项目里有什么证据？"})

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _export(self) -> bytes:
        response = self.client.get(f"/api/projects/{self.pid}/dossier")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertIn("attachment", response.headers["content-disposition"])
        return response.content

    def _verify(self, blob: bytes):
        return self.client.post(
            "/api/dossiers/verify",
            files={"file": ("dossier.zip", blob, "application/zip")},
        )

    def test_export_verify_and_import_roundtrip(self) -> None:
        blob = self._export()
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            self.assertEqual(set(archive.namelist()), set(dossier.ZIP_ENTRIES))
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["format"], dossier.FORMAT)
            self.assertEqual(len(manifest["entries"]), 2)
            for entry in manifest["entries"]:
                payload = archive.read(entry["path"])
                self.assertEqual(len(payload), entry["bytes"])
                self.assertEqual(dossier._sha256(payload), entry["sha256"])
            self.assertNotIn(str(self.tmp.name), archive.read("dossier.json").decode("utf-8"))
        verified = self._verify(blob)
        self.assertEqual(verified.status_code, 200, verified.text)
        body = verified.json()
        self.assertEqual(body["summary"]["runs"], 1)
        self.assertEqual(body["summary"]["evidence"], 1)
        self.assertEqual(body["summary"]["draft_blocks"], 1)
        self.assertEqual(body["project"]["title"], "可复现档案项目")

        imported = self.client.post(
            "/api/dossiers/import",
            files={"file": ("dossier.zip", blob, "application/zip")},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        result = imported.json()
        new_pid = result["project"]["id"]
        self.assertNotEqual(new_pid, self.pid)
        projects = project_state.read_projects()["projects"]
        new_project = projects[new_pid]
        self.assertEqual(new_project["tags"][-1], "档案导入")
        self.assertEqual(len(new_project["run_ids"]), 1)
        new_rid = new_project["run_ids"][0]
        self.assertNotEqual(new_rid, self.rid)
        imported_run = agent_state.read_store()["runs"][new_rid]
        self.assertEqual(imported_run["project_id"], new_pid)
        self.assertTrue(imported_run["report"]["path"].startswith(str(agent_state.runs_dir())))
        self.assertEqual(draft_state.read_draft(new_pid)["revision"], 3)
        self.assertEqual(manuscript.read(new_pid)["title"], "档案稿件")
        self.assertEqual(len(assistant_state.read_assistant(new_pid)["messages"]), 1)
        memory = project_state.read_memory(new_pid)
        self.assertEqual(memory["evidence"][0]["origin"]["run_id"], new_rid)
        self.assertEqual(memory["reconciled_runs"], [new_rid])

    def test_tampered_dossier_is_rejected(self) -> None:
        blob = self._export()
        with zipfile.ZipFile(io.BytesIO(blob)) as source:
            files = {name: source.read(name) for name in source.namelist()}
        files["dossier.json"] = files["dossier.json"].replace(b"Paper One", b"Paper Two")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, payload in files.items():
                archive.writestr(name, payload)
        response = self._verify(output.getvalue())
        self.assertEqual(response.status_code, 422)
        self.assertIn("哈希", response.json()["detail"])

    def test_extra_entry_is_rejected(self) -> None:
        blob = self._export()
        with zipfile.ZipFile(io.BytesIO(blob)) as source:
            files = {name: source.read(name) for name in source.namelist()}
        files["extra.txt"] = b"unexpected"
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, payload in files.items():
                archive.writestr(name, payload)
        response = self._verify(output.getvalue())
        self.assertEqual(response.status_code, 422)
        self.assertIn("条目", response.json()["detail"])

    def test_nonterminal_run_is_frozen_on_import(self) -> None:
        store = agent_state.read_store()
        store["runs"][self.rid]["status"] = "running"
        agent_state.write_store(store)
        blob = self._export()
        verified = self._verify(blob).json()
        self.assertTrue(verified["warnings"])
        imported = self.client.post(
            "/api/dossiers/import",
            files={"file": ("dossier.zip", blob, "application/zip")},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        new_pid = imported.json()["project"]["id"]
        new_rid = project_state.read_projects()["projects"][new_pid]["run_ids"][0]
        new_run = agent_state.read_store()["runs"][new_rid]
        self.assertEqual(new_run["status"], "interrupted")
        self.assertFalse(new_run["pause_requested"])

    def test_export_excludes_secrets_and_other_project_data(self) -> None:
        data_dir = Path(os.environ["PAPERPILOT_DATA_DIR"])
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "llm_settings.secrets.json").write_text('{"api_key":"sk-secret"}', encoding="utf-8")
        (data_dir / "uploads").mkdir(exist_ok=True)
        (data_dir / "uploads" / "private.pdf").write_bytes(b"%PDF-secret")
        with zipfile.ZipFile(io.BytesIO(self._export())) as archive:
            names = set(archive.namelist())
        self.assertEqual(names, set(dossier.ZIP_ENTRIES))
        with zipfile.ZipFile(io.BytesIO(self._export())) as archive:
            for name in names:
                self.assertNotIn("sk-secret", archive.read(name).decode("utf-8", "ignore"))

    def test_repeated_import_creates_distinct_ids(self) -> None:
        blob = self._export()
        first = self.client.post("/api/dossiers/import", files={"file": ("a.zip", blob, "application/zip")})
        second = self.client.post("/api/dossiers/import", files={"file": ("a.zip", blob, "application/zip")})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        pid_a = first.json()["project"]["id"]
        pid_b = second.json()["project"]["id"]
        self.assertNotEqual(pid_a, pid_b)
        rid_a = project_state.read_projects()["projects"][pid_a]["run_ids"][0]
        rid_b = project_state.read_projects()["projects"][pid_b]["run_ids"][0]
        self.assertNotEqual(rid_a, rid_b)

    def test_verify_does_not_create_projects(self) -> None:
        before = len(project_state.read_projects()["projects"])
        blob = self._export()
        self.assertEqual(self._verify(blob).status_code, 200)
        self.assertEqual(len(project_state.read_projects()["projects"]), before)

    def test_invalid_project_title_is_rejected_even_with_valid_hashes(self) -> None:
        blob = self._export()
        with zipfile.ZipFile(io.BytesIO(blob)) as source:
            files = {name: source.read(name) for name in source.namelist()}
        dossier_data = json.loads(files["dossier.json"])
        dossier_data["project"]["title"] = "x"
        files["dossier.json"] = json.dumps(
            dossier_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        entries = [
            {"path": "DOSSIER.md", "bytes": len(files["DOSSIER.md"]),
             "sha256": dossier._sha256(files["DOSSIER.md"])},
            {"path": "dossier.json", "bytes": len(files["dossier.json"]),
             "sha256": dossier._sha256(files["dossier.json"])},
        ]
        manifest = {
            "format": dossier.FORMAT, "app_version": "7.0.0", "created_at": "2026-10-23",
            "entries": entries, "root_sha256": dossier._root_hash(entries),
        }
        files["manifest.json"] = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, payload in files.items():
                archive.writestr(name, payload)
        response = self._verify(output.getvalue())
        self.assertEqual(response.status_code, 422)
        self.assertIn("标题", response.json()["detail"])

    def test_missing_file_is_rejected(self) -> None:
        response = self.client.post("/api/dossiers/verify")
        self.assertEqual(response.status_code, 422)

    def test_import_restores_uploaded_paper_metadata(self) -> None:
        upload = {
            "id": "upload-testpdf", "title": "Local PDF", "source": "upload",
            "abstract": "Uploaded abstract.", "sections": [{"title": "Abstract", "page": 1, "text": "Uploaded text."}],
        }
        directory = agent_state.run_dir(self.rid)
        (directory / "papers.json").write_text(json.dumps([upload], ensure_ascii=False), encoding="utf-8")
        memory = project_state.read_memory(self.pid)
        memory["papers"]["upload-testpdf"] = {"id": "upload-testpdf", "title": "Local PDF"}
        project_state.write_memory(self.pid, memory)
        blob = self._export()
        store.UPLOAD_PAPERS.pop("upload-testpdf", None)
        imported = self.client.post(
            "/api/dossiers/import",
            files={"file": ("dossier.zip", blob, "application/zip")},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertIn("upload-testpdf", store.UPLOAD_PAPERS)
        self.assertEqual(store.UPLOAD_PAPERS["upload-testpdf"]["source"], "upload")
        self.assertGreaterEqual(imported.json().get("restored_uploads", 0), 1)

    def test_draft_versions_roundtrip(self) -> None:
        draft = draft_state.read_draft(self.pid)
        version = draft_state.snapshot(draft, "ver-001-1", "初始稿", "manual")
        draft["versions"] = [version]
        draft_state.write_draft(self.pid, draft)
        blob = self._export()
        imported = self.client.post(
            "/api/dossiers/import",
            files={"file": ("dossier.zip", blob, "application/zip")},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        new_pid = imported.json()["project"]["id"]
        imported_draft = draft_state.read_draft(new_pid)
        self.assertEqual([item["id"] for item in imported_draft["versions"]], ["ver-001-1"])
        snapshot_path = draft_state.versions_dir(new_pid) / "ver-001-1.json"
        self.assertTrue(snapshot_path.is_file())
        self.assertIn("sections", json.loads(snapshot_path.read_text(encoding="utf-8")))

    def test_cli_export_verify_import(self) -> None:
        import subprocess
        import sys

        out = Path(self.tmp.name) / "cli.paperpilot.zip"
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        root = Path(__file__).resolve().parent.parent
        exported = subprocess.run(
            [sys.executable, "scripts/dossier.py", "export", "--project-id", self.pid, "--out", str(out)],
            cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertTrue(out.is_file())
        verified = subprocess.run(
            [sys.executable, "scripts/dossier.py", "verify", str(out), "--json"],
            cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        summary = json.loads(verified.stdout)
        self.assertEqual(summary["summary"]["runs"], 1)
        imported = subprocess.run(
            [sys.executable, "scripts/dossier.py", "import", str(out), "--json"],
            cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(imported.returncode, 0, imported.stderr)
        result = json.loads(imported.stdout)
        self.assertNotEqual(result["project"]["id"], self.pid)


if __name__ == "__main__":
    unittest.main()
