"""Papers a student has searched and read must become citable in the project.

Before this, ``memory["papers"]`` had exactly one writer — promoting an Agent run — and the
attach/detach endpoints that could have fixed it were never called from the UI. A student could
search, upload and read five papers and still see "项目记忆中没有论文" in the draft workbench,
because nothing but a run inside that project could make a paper citable.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import config, store
from app.main import app
from app.services import project_state, search


class CitationPoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pp-pool-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = patch.dict(os.environ, {
            "PAPERPILOT_DATA_DIR": str(self.tmp),
            "PAPERPILOT_PROJECT_FILE": str(self.tmp / "projects.json"),
            "PAPERPILOT_PROJECT_DIR": str(self.tmp / "projects"),
        })
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(app)
        project = self.client.post("/api/projects", json={
            "title": "引用池测试", "question": "存算一体 ADC", "scope": "CIM",
            "success_criteria": "可引用", "tags": []}).json()["project"]
        self.pid = project["id"]

    def memory_paper_ids(self) -> set[str]:
        items = self.client.get(f"/api/projects/{self.pid}/memory?kind=papers&limit=50").json()["items"]
        return {str(item.get("id")) for item in items}

    def _upload(self, title: str = "Uploaded CIM paper") -> str:
        paper = {"id": "upload-pool1", "title": title, "abstract": "0.8 pJ/SOP.",
                 "source": "upload", "sections": [], "pages": 2}
        store.register_upload(paper)
        return paper["id"]

    def test_a_demo_corpus_paper_can_be_added_without_a_run(self) -> None:
        paper_id = next(iter(search.PAPERS_BY_ID))
        response = self.client.post(f"/api/projects/{self.pid}/papers", json={"paper_ids": [paper_id]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.memory_paper_ids(), {paper_id})
        self.assertEqual(response.json()["added"], 1)
        self.assertEqual(response.json()["missing"], [])

    def test_an_uploaded_paper_can_be_added_and_carries_its_metadata(self) -> None:
        paper_id = self._upload("可引用的上传论文")
        response = self.client.post(f"/api/projects/{self.pid}/papers", json={"paper_ids": [paper_id]})
        self.assertEqual(response.status_code, 200, response.text)
        item = next(entry for entry in self.client.get(
            f"/api/projects/{self.pid}/memory?kind=papers").json()["items"] if entry["id"] == paper_id)
        self.assertEqual(item["title"], "可引用的上传论文")

    def test_adding_is_idempotent_and_enriches_rather_than_blanking(self) -> None:
        paper_id = self._upload("第一次加入的标题")
        self.client.post(f"/api/projects/{self.pid}/papers", json={"paper_ids": [paper_id]})
        store.register_upload({"id": paper_id, "title": "第一次加入的标题", "venue": "arXiv",
                               "abstract": "0.8 pJ/SOP.", "source": "upload"})
        again = self.client.post(f"/api/projects/{self.pid}/papers", json={"paper_ids": [paper_id]})
        self.assertEqual(again.status_code, 200)
        items = self.client.get(f"/api/projects/{self.pid}/memory?kind=papers").json()["items"]
        self.assertEqual(len(items), 1, "重复加入不得产生第二条")
        self.assertEqual(items[0]["title"], "第一次加入的标题")

    def test_unknown_and_unsafe_ids_are_reported_not_stored(self) -> None:
        response = self.client.post(f"/api/projects/{self.pid}/papers", json={
            "paper_ids": ["nope-404", "..\\llm_settings.secrets", ""]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["added"], 0)
        self.assertEqual(len(response.json()["missing"]), 3, response.json())
        self.assertEqual(self.memory_paper_ids(), set())

    def test_a_paper_can_be_removed_again(self) -> None:
        paper_id = self._upload()
        self.client.post(f"/api/projects/{self.pid}/papers", json={"paper_ids": [paper_id]})
        removed = self.client.delete(f"/api/projects/{self.pid}/papers/{paper_id}")
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(self.memory_paper_ids(), set())

    def test_a_pool_paper_becomes_citable_in_the_draft(self) -> None:
        paper_id = self._upload("可被引用的论文")
        self.client.post(f"/api/projects/{self.pid}/papers", json={"paper_ids": [paper_id]})
        draft = self.client.get(f"/api/projects/{self.pid}/draft").json()["draft"]
        response = self.client.post(f"/api/projects/{self.pid}/draft/citations", json={
            "paper_id": paper_id, "revision": draft.get("revision", 0)})
        self.assertEqual(response.status_code, 200, response.text)
        citation = response.json()["citation"]
        self.assertEqual(citation["title"], "可被引用的论文")
        self.assertTrue(citation["key"])

    def test_a_removed_paper_can_no_longer_be_cited(self) -> None:
        paper_id = self._upload()
        self.client.post(f"/api/projects/{self.pid}/papers", json={"paper_ids": [paper_id]})
        self.client.delete(f"/api/projects/{self.pid}/papers/{paper_id}")
        draft = self.client.get(f"/api/projects/{self.pid}/draft").json()["draft"]
        response = self.client.post(f"/api/projects/{self.pid}/draft/citations", json={
            "paper_id": paper_id, "revision": draft.get("revision", 0)})
        self.assertEqual(response.status_code, 404, response.text)

    def test_the_pool_keeps_papers_that_no_evidence_points_at(self) -> None:
        memory = project_state.read_memory(self.pid)
        memory["papers"] = {
            f"paper-{index:03d}": {"id": f"paper-{index:03d}", "title": f"论文{index}",
                                   "updated_at": f"2026-01-01 00:00:{index % 60:02d}"}
            for index in range(project_state.MAX_PROJECT_PAPERS + 5)
        }
        project_state.write_memory(self.pid, memory)
        project_state.remember_papers(self.pid, [{"id": "paper-newest", "title": "最新加入"}])
        pool = project_state.read_memory(self.pid)["papers"]
        self.assertIn("paper-newest", pool)
        self.assertLessEqual(len(pool), project_state.MAX_PROJECT_PAPERS)

    def test_an_unknown_project_is_404(self) -> None:
        response = self.client.post("/api/projects/prj-does-not-exist/papers", json={"paper_ids": ["x"]})
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
