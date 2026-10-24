from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import glossary_store


AITD_SAMPLE = """# Letter All
| 编号 | 英文名称 | 中文翻译 | 其他缩写 | 来源&扩展 | 备注 |
|---|---|---|---|---|---|
AITD-00000|0-1 Loss Function|0-1损失函数||[1]||
AITD-00015|Activation Function|激活函数||[1]|机器学习|
AITD-00099|Vector Store|向量库||[1]|在线扩展|
"""


class GlossaryParserTests(unittest.TestCase):
    def test_parses_aitd_markdown_table(self) -> None:
        terms = glossary_store.parse_glossary(AITD_SAMPLE, source_name="aitd")
        self.assertEqual(len(terms), 3)
        by_term = {item["term"]: item for item in terms}
        self.assertEqual(by_term["Activation Function"]["zh"], "激活函数")
        self.assertEqual(by_term["Vector Store"]["zh"], "向量库")
        self.assertTrue(all(item["category"] == "在线扩展" for item in terms))

    def test_parses_json_and_csv_shapes(self) -> None:
        json_terms = glossary_store.parse_glossary(
            json.dumps({"items": [{"term": "retrieval", "zh": "检索", "category": "系统与性能"}]}),
            source_name="json",
        )
        self.assertEqual(json_terms[0]["term"], "retrieval")
        csv_terms = glossary_store.parse_glossary(
            "english,chinese,category\nlatency,延迟,系统与性能\ncache,缓存,系统与性能\n",
            source_name="csv",
        )
        self.assertEqual(len(csv_terms), 2)
        self.assertEqual(csv_terms[0]["zh"], "延迟")


class GlossaryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.env = patch.dict(
            os.environ,
            {
                "PAPERPILOT_GLOSSARY_DIR": str(self.root / "glossaries"),
                "PAPERPILOT_ALLOW_PRIVATE_FETCH": "1",
            },
        )
        self.env.start()
        glossary_store.clear_cache()

    def tearDown(self) -> None:
        glossary_store.clear_cache()
        self.env.stop()
        self._tmp.cleanup()

    def test_import_pack_adds_new_terms_without_overriding_builtin(self) -> None:
        pack = glossary_store.import_text(
            "my_terms.csv",
            "english,chinese,category\ntransformer,变换器,大模型与推理\nvector store,向量库,在线扩展\n".encode("utf-8"),
            name="My Pack",
            license="internal",
        )
        self.assertEqual(pack["count"], 2)
        merged = glossary_store.merged_items()
        by_term = {item["term"].lower(): item for item in merged}
        self.assertEqual(by_term["transformer"]["zh"], "Transformer 模型")
        self.assertEqual(by_term["vector store"]["zh"], "向量库")
        packs = glossary_store.list_packs()
        self.assertEqual(len(packs["packs"]), 1)
        self.assertEqual(packs["builtin"]["count"], len(glossary_store.builtin_items()))

    def test_toggle_and_delete_pack(self) -> None:
        pack = glossary_store.import_text(
            "x.json",
            json.dumps([{"term": "vector store", "zh": "向量库"}]).encode("utf-8"),
            name="Toggle Pack",
        )
        glossary_store.set_enabled(pack["id"], False)
        self.assertFalse(any(item["term"] == "vector store" for item in glossary_store.merged_items()))
        glossary_store.set_enabled(pack["id"], True)
        self.assertTrue(any(item["term"] == "vector store" for item in glossary_store.merged_items()))
        self.assertTrue(glossary_store.delete_pack(pack["id"]))
        self.assertFalse(glossary_store.list_packs()["packs"])

    def test_online_preset_fetch_and_refresh(self) -> None:
        calls = []
        def fetcher(url: str, headers: dict, timeout: int) -> bytes:
            calls.append(url)
            return AITD_SAMPLE.encode("utf-8")

        pack = glossary_store.fetch_online("jiqizhixin_aitd", fetcher=fetcher)
        self.assertEqual(pack["count"], 3)
        self.assertIn("CC BY-NC-SA", pack["license"])
        self.assertIn("机器之心", pack["attribution"])
        again = glossary_store.fetch_online("jiqizhixin_aitd", fetcher=fetcher)
        self.assertEqual(again["id"], pack["id"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(glossary_store.list_packs()["packs"]), 1)

    def test_api_pack_management_and_merged_search(self) -> None:
        client = TestClient(app)
        packs = client.get("/api/glossary/packs")
        self.assertEqual(packs.status_code, 200)
        self.assertIn("presets", packs.json())
        response = client.post(
            "/api/glossary/packs/import",
            data={"name": "API Pack", "license": "internal", "category": "在线扩展"},
            files={"file": ("api.csv", b"english,chinese\nvector store,\xe5\x90\x91\xe9\x87\x8f\xe5\xba\x93\n", "text/csv")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        filtered = client.get("/api/glossary?q=vector store")
        self.assertEqual(filtered.status_code, 200)
        self.assertTrue(any(item["term"] == "vector store" for item in filtered.json()["items"]))
        pack_id = response.json()["pack"]["id"]
        toggled = client.post(f"/api/glossary/packs/{pack_id}/toggle", json={"enabled": False})
        self.assertEqual(toggled.status_code, 200)
        self.assertFalse(any(item["term"] == "vector store" for item in client.get("/api/glossary?q=vector store").json()["items"]))
        deleted = client.post(f"/api/glossary/packs/{pack_id}/delete")
        self.assertEqual(deleted.status_code, 200)

    def test_online_api_uses_fetcher(self) -> None:
        client = TestClient(app)
        with patch.object(glossary_store, "fetch_online", return_value={
            "id": "preset-jiqizhixin_aitd",
            "name": "机器之心 AI 术语库",
            "count": 3,
            "license": "CC BY-NC-SA 4.0",
            "attribution": "机器之心",
            "enabled": True,
        }) as mocked:
            response = client.post("/api/glossary/packs/online", json={"preset_id": "jiqizhixin_aitd"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(mocked.called)
        self.assertEqual(response.json()["pack"]["count"], 3)

    def test_translation_hits_include_imported_pack(self) -> None:
        from app.services import translation

        glossary_store.import_text(
            "terms.csv",
            b"english,chinese\nvector store,\xe5\x90\x91\xe9\x87\x8f\xe5\xba\x93\n",
            name="Translation Pack",
        )
        hits = translation._glossary_hits("A vector store retrieves passages.")
        self.assertTrue(any(hit["term"] == "vector store" and hit["zh"] == "向量库" for hit in hits))


if __name__ == "__main__":
    unittest.main()
