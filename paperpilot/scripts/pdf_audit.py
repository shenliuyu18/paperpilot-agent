"""Offline audit of the local-PDF feature path.

Uploads the two bundled demo PDFs into an isolated data directory and runs the
features that consume uploaded papers: detail, read, translate, ask, compare,
graph, topic, draft, check, argument, library, upload-source search and Agent,
plus corrupt/scanned upload rejection and the research-dossier export.

Usage::

    python scripts/pdf_audit.py [--json out/pdf_audit.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(tempfile.mkdtemp(prefix="pp-pdf-audit-"))
os.environ.update({
    "PAPERPILOT_DATA_DIR": str(DATA),
    "PAPERPILOT_PROJECT_FILE": str(DATA / "projects.json"),
    "PAPERPILOT_PROJECT_DIR": str(DATA / "projects"),
    "PAPERPILOT_AGENT_FILE": str(DATA / "runs.json"),
    "PAPERPILOT_AGENT_DIR": str(DATA / "runs"),
    "PAPERPILOT_UPLOAD_DIR": str(DATA / "uploads"),
})
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)
RESULTS: list[dict] = []


def check(name: str, ok: bool, detail="") -> bool:
    RESULTS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})
    print(("PASS " if ok else "FAIL ") + name + (f"  | {str(detail)[:180]}" if detail else ""), flush=True)
    return bool(ok)


def post(path: str, payload: dict) -> tuple[int, dict]:
    response = client.post(path, json=payload)
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - error pages are not JSON
        body = {}
    return response.status_code, body


def upload(path: Path) -> tuple[int, dict]:
    response = client.post(
        "/api/upload",
        files={"file": (path.name, path.read_bytes(), "application/pdf")},
    )
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        body = {}
    return response.status_code, body


def wait_run(run_id: str, timeout: float = 90.0) -> dict:
    terminal = {"completed", "failed", "cancelled", "interrupted", "awaiting_approval"}
    last: dict = {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        last = client.get(f"/api/agent/runs/{run_id}").json()["run"]
        if last.get("status") in terminal:
            return last
        time.sleep(0.4)
    return last


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()
    pdfs = sorted((ROOT / "data" / "demo_pdfs").glob("*.pdf"))
    if len(pdfs) < 2:
        raise SystemExit("need two demo PDFs")
    uploaded = []
    for path in pdfs[:2]:
        status, body = upload(path)
        uploaded.append(body)
        check(
            f"P1 上传 {path.name}",
            status == 200 and bool(body.get("paper_id")) and body.get("sections", 0) > 0,
            f"code={status} id={body.get('paper_id')} pages={body.get('pages')} "
            f"chars={body.get('chars')} sections={body.get('sections')}",
        )
    ids = [item.get("paper_id") for item in uploaded if item.get("paper_id")]
    check("P2 两篇本地 PDF 均有 ID", len(ids) == 2, ids)
    if len(ids) < 2:
        return 1

    detail = client.get(f"/api/paper/{ids[0]}").json()
    check("P3 详情含全文/章节", bool(detail.get("sections")) and detail.get("full_text") is True,
          f"sections={len(detail.get('sections') or [])}")

    status, body = post("/api/read", {"paper_id": ids[0], "mode": "5min"})
    check("P4 离线精读", status == 200 and bool(body.get("tldr") or body.get("sections")), f"code={status}")
    status, body = post("/api/translate", {"paper_id": ids[0], "target": "zh", "provider": "auto"})
    note = str(body.get("note") or "")
    check("P5 离线翻译提示与本地 PDF 一致",
          status == 200 and "本地 PDF" in note and "演示论文已提供" not in note, note[:140])
    status, body = post("/api/ask", {"question": "能效结果是什么？", "scope": "selected", "paper_ids": ids})
    citations = body.get("citations") or []
    check("P6 证据问答引用上传 PDF",
          status == 200 and bool(body.get("answer")) and bool(citations)
          and all(str(item.get("paper_id") or "").startswith("upload-") for item in citations),
          f"code={status} citations={len(citations)}")
    status, body = post("/api/compare", {"paper_ids": ids})
    rows = body.get("rows") or []
    check("P7 横向对比行对应上传 PDF",
          status == 200 and len(rows) == 2 and all(str(row.get("id") or "").startswith("upload-") for row in rows),
          f"code={status} rows={[row.get('id') for row in rows]}")
    status, body = post("/api/graph", {"scope": "selected", "paper_ids": ids, "topic": "CIM"})
    nodes = body.get("nodes") or []
    paper_nodes = [node for node in nodes if node.get("type") == "paper"]
    check("P8 概念图谱论文节点来自上传 PDF",
          status == 200 and bool(paper_nodes)
          and all(str(node.get("id") or "").startswith("paper:upload-") for node in paper_nodes),
          f"code={status} papers={len(paper_nodes)}")
    status, body = post("/api/topic", {"topic": "存内计算 ADC", "scope": "selected", "paper_ids": ids})
    clusters = body.get("clusters") or []
    check("P9 选题工作台覆盖上传 PDF",
          status == 200 and bool(clusters)
          and all(str(paper.get("paper_id") or "").startswith("upload-")
                  for cluster in clusters for paper in cluster.get("papers") or []),
          f"code={status} clusters={len(clusters)}")
    status, body = post("/api/draft", {"topic": "存内计算 ADC", "kind": "related_work",
                                       "scope": "selected", "paper_ids": ids})
    refs = body.get("references") or []
    check("P10 写作草稿参考文献来自上传 PDF",
          status == 200 and bool(refs)
          and all(str(ref.get("paper_id") or "").startswith("upload-") for ref in refs),
          f"code={status} refs={len(refs)}")
    status, body = post("/api/check", {"text": "NeuDW-CIM 在 65nm 下实测能效 0.8 pJ/SOP。",
                                       "scope": "selected", "paper_ids": ids})
    results = body.get("results") or []
    check("P11 逐句核查引用上传 PDF",
          status == 200 and bool(results)
          and all(str((item.get("citation") or {}).get("paper_id") or "").startswith("upload-")
                  for item in results if item.get("citation")),
          f"code={status} results={len(results)}")
    status, body = post("/api/argument", {"text": "存内计算可以降低 ADC 读出能耗。",
                                          "topic": "存内计算 ADC", "scope": "selected", "paper_ids": ids})
    check("P12 论证体检可运行", status == 200 and bool(body.get("structure") or body.get("claims")), f"code={status}")

    for paper_id in ids:
        response = client.post("/api/library", json={"paper_id": paper_id})
        check(f"P13 收藏 {paper_id}", response.status_code == 200, response.status_code)
    library = client.get("/api/library").json()
    check("P14 研究库包含两篇上传 PDF", len((library or {}).get("items") or []) == 2,
          len((library or {}).get("items") or []))

    status, body = post("/api/search", {"query": "CIM", "limit": 8, "source": "upload"})
    papers = body.get("papers") or []
    check("P15 搜索来源 upload 只返回上传 PDF",
          status == 200 and bool(papers)
          and all(str(item.get("id") or "").startswith("upload-") for item in papers),
          f"code={status} ids={[item.get('id') for item in papers]}")

    detail_second = client.get(f"/api/paper/{ids[1]}").json()
    energy_values = [
        str(item.get("value") or "") for item in detail.get("results") or []
        if item.get("name") == "能效"
    ]
    # The first demo PDF has an energy result; the second's extracted metrics
    # must still not carry a prior-work attribution sentence.
    check("P16 规则抽取不把引用他人工作的句子当本文结果",
          "ISAAC reports" not in " ".join(str(item.get("context") or "")
                                          for item in detail_second.get("results") or []),
          "prior-work filter")
    check("P17 能效结果值不混入准确率百分号",
          not any("%" in value for value in energy_values), energy_values)

    project = client.post("/api/projects", json={
        "title": "本地 PDF 档案审计", "question": "上传 PDF 能否进入研究档案？",
    }).json()["project"]
    status, run = post("/api/agent/runs", {
        "goal": "总结两篇本地 PDF 的能效与校准开销", "source": "upload",
        "mode": "auto", "project_id": project["id"], "max_actions": 3,
        "max_seconds": 90, "max_llm_calls": 0, "max_papers": 2,
    })
    run_id = (run.get("run") or {}).get("id") if isinstance(run, dict) else None
    last = wait_run(run_id) if run_id else {}
    check("P18 Agent upload 来源使用上传 PDF 并完成",
          bool(run_id) and last.get("status") == "completed" and bool(last.get("paper_ids"))
          and all(str(item).startswith("upload-") for item in last.get("paper_ids") or []),
          f"status={last.get('status')} papers={last.get('paper_ids')}")
    archive = client.get(f"/api/projects/{project['id']}/dossier")
    verified = client.post("/api/dossiers/verify",
                           files={"file": ("pdf-audit.zip", archive.content, "application/zip")})
    check("P19 含上传 PDF 的项目档案可导出/校验",
          archive.status_code == 200 and verified.status_code == 200
          and (verified.json().get("summary") or {}).get("evidence", 0) > 0,
          f"archive={archive.status_code} verify={verified.status_code}")

    status, body = upload_from_bytes(b"not a pdf at all", "plain.pdf")
    check("P20 非 PDF 被拒绝", status == 400, f"code={status}")
    status, body = upload_from_bytes(b"%PDF-1.7\nnot really", "broken.pdf")
    check("P21 损坏 PDF 返回 400", status == 400, f"code={status} detail={body.get('detail')}")
    import pymupdf
    doc = pymupdf.open()
    doc.new_page()
    blank = doc.tobytes()
    doc.close()
    status, body = upload_from_bytes(blank, "scanned.pdf")
    check("P22 无文字扫描件返回 400 并提示 OCR", status == 400 and "扫描件" in str(body.get("detail")),
          f"code={status} detail={body.get('detail')}")

    passed = sum(1 for item in RESULTS if item["ok"])
    out = args.json or (ROOT / "out" / "pdf_audit.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"checks": RESULTS, "passed": passed, "total": len(RESULTS)},
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n{passed}/{len(RESULTS)} PDF checks passed\nartifacts: {out}")
    return 0 if passed == len(RESULTS) else 1


def upload_from_bytes(data: bytes, filename: str) -> tuple[int, dict]:
    response = client.post("/api/upload", files={"file": (filename, data, "application/pdf")})
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        body = {}
    return response.status_code, body


if __name__ == "__main__":
    raise SystemExit(main())