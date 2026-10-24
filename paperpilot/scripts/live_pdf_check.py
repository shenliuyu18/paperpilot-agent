"""Real-model audit on locally uploaded PDFs.

Requires ``PAPERPILOT_LLM_API_KEY`` and ``PAPERPILOT_LLM_BASE_URL`` (and
optionally ``PAPERPILOT_LLM_MODEL``). Without a key it exits 2 and writes
nothing. It starts an isolated instance, uploads the two bundled demo PDFs,
runs the Agent from the ``upload`` source, then checks project Copilot,
semantic audit, model-call audit and dossier export.

Usage::

    python scripts/live_pdf_check.py [--json out/live_pdf.json]
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests
import truststore

truststore.inject_into_ssl()

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--pdf-dir", type=Path, default=ROOT / "data" / "demo_pdfs")
    args = parser.parse_args()
    if not os.getenv("PAPERPILOT_LLM_API_KEY"):
        print("未配置 PAPERPILOT_LLM_API_KEY，真实模型 PDF 审计不执行（退出码 2，不写报告）。")
        return 2
    if not os.getenv("PAPERPILOT_LLM_BASE_URL"):
        print("未配置 PAPERPILOT_LLM_BASE_URL，真实模型 PDF 审计不执行（退出码 2）。")
        return 2
    pdfs = sorted(args.pdf_dir.glob("*.pdf"))
    if len(pdfs) < 2:
        print("需要至少两篇本地 PDF 才能执行 PDF 审计。")
        return 2
    data_dir = ROOT / "out" / f"live-pdf-{time.strftime('%Y%m%d-%H%M%S')}"
    data_dir.mkdir(parents=True, exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    env.update({
        "PAPERPILOT_DATA_DIR": str(data_dir / "data"),
        "PAPERPILOT_PROJECT_FILE": str(data_dir / "data" / "projects.json"),
        "PAPERPILOT_PROJECT_DIR": str(data_dir / "data" / "projects"),
        "PAPERPILOT_AGENT_FILE": str(data_dir / "data" / "runs.json"),
        "PAPERPILOT_AGENT_DIR": str(data_dir / "data" / "runs"),
        "PAPERPILOT_UPLOAD_DIR": str(data_dir / "data" / "uploads"),
        "PAPERPILOT_LLM_TIMEOUT": os.getenv("PAPERPILOT_LLM_TIMEOUT", "180"),
        "PAPERPILOT_LLM_RETRIES": os.getenv("PAPERPILOT_LLM_RETRIES", "1"),
        "PYTHONUTF8": "1",
    })
    log = (data_dir / "server.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, text=True,
    )
    checks: list[dict] = []

    def check(name: str, ok: bool, detail="") -> bool:
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:500]})
        print(("PASS " if ok else "FAIL ") + name + (f"  | {str(detail)[:180]}" if detail else ""), flush=True)
        return bool(ok)

    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            if process.poll() is not None:
                raise SystemExit(f"server exited early:\n{(data_dir / 'server.log').read_text(encoding='utf-8')[-1200:]}")
            try:
                if requests.get(base + "/api/health", timeout=2).status_code == 200:
                    break
            except requests.RequestException:
                time.sleep(0.3)
        session = requests.Session()
        health = session.get(base + "/api/health", timeout=30).json()
        check("O1 实例在线", health.get("mode") == "online", health)
        probe = session.post(base + "/api/llm/settings/test", json={}, timeout=240)
        if probe.status_code != 200:
            detail = (probe.json() or {}).get("detail") if "json" in probe.headers.get("content-type", "") else probe.text
            print(f"模型端点不可用（{probe.status_code}）：{detail}")
            print("本次 PDF 在线审计未执行，不写报告；配额或鉴权恢复后重跑。")
            checks.append({"name": "O0 模型端点可用", "ok": False, "detail": f"{probe.status_code}: {detail}"[:500]})
            return 3
        check("O0 模型端点可用", True, f"{probe.json().get('latency_ms')} ms")

        project = session.post(base + "/api/projects", json={
            "title": "在线 PDF 审计", "question": "上传 PDF 的能效与校准开销如何权衡？",
            "tags": ["pdf-online"],
        }, timeout=60).json()["project"]
        project_id = project["id"]
        ids = []
        for path in pdfs[:2]:
            response = session.post(base + "/api/upload", files={
                "file": (path.name, path.read_bytes(), "application/pdf"),
            }, timeout=120)
            body = response.json() if response.status_code == 200 else {}
            ids.append(body.get("paper_id"))
            check(f"O2 上传 {path.name}", response.status_code == 200 and bool(body.get("paper_id")),
                  f"code={response.status_code} id={body.get('paper_id')} sections={body.get('sections')}")
        if not all(ids):
            return 1
        read = session.post(base + "/api/read", json={"paper_id": ids[0], "mode": "5min"}, timeout=300)
        read_body = read.json() if read.status_code == 200 else {}
        check("O2b 在线精读由模型增强",
              read.status_code == 200 and read_body.get("extraction_mode") == "llm"
              and bool(read_body.get("tldr")),
              f"code={read.status_code} mode={read_body.get('extraction_mode')} "
              f"tldr={str(read_body.get('tldr'))[:70]}")
        translated = session.post(base + "/api/translate", json={
            "text": "This work achieves a measured energy efficiency of 0.8 pJ/SOP.",
            "target": "zh", "provider": "llm",
        }, timeout=300)
        translate_body = translated.json() if translated.status_code == 200 else {}
        check("O2c 在线翻译由模型生成",
              translated.status_code == 200 and translate_body.get("source") == "llm"
              and bool(translate_body.get("translation")),
              f"code={translated.status_code} source={translate_body.get('source')} "
              f"items={len(translate_body.get('translation') or [])}")
        run = session.post(base + "/api/agent/runs", json={
            "goal": "比较两篇上传论文的能效与校准开销", "source": "upload",
            "mode": "auto", "project_id": project_id, "max_actions": 6,
            "max_seconds": 600, "max_llm_calls": 4, "max_papers": 2,
        }, timeout=120).json()["run"]
        run_id = run["id"]
        terminal = {"completed", "failed", "cancelled", "interrupted", "awaiting_approval"}
        last: dict = {}
        deadline = time.time() + 600
        while time.time() < deadline:
            last = session.get(base + f"/api/agent/runs/{run_id}", timeout=30).json()["run"]
            if last.get("status") in terminal:
                break
            time.sleep(1)
        report_path = Path(str((last.get("report") or {}).get("path") or ""))
        report = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
        check("O3 在线 Agent 完成且使用上传 PDF",
              last.get("status") == "completed" and report
              and (last.get("report") or {}).get("mode") == "llm"
              and bool(last.get("paper_ids"))
              and all(str(item).startswith("upload-") for item in last.get("paper_ids") or []),
              f"status={last.get('status')} papers={last.get('paper_ids')} "
              f"mode={(last.get('report') or {}).get('mode')} chars={len(report)}")
        memory = session.get(base + f"/api/projects/{project_id}/memory?kind=evidence&limit=50",
                             timeout=60).json().get("items") or []
        check("O4 上传 PDF 证据进入项目记忆",
              bool(memory) and any(str(item.get("paper_id", "")).startswith("upload-") for item in memory),
              f"evidence={len(memory)}")
        reply = session.post(base + f"/api/projects/{project_id}/assistant",
                             json={"message": "这两篇上传论文的能效和校准开销结论是什么？"},
                             timeout=300).json().get("reply") or {}
        citations = reply.get("citations") or []
        answer = str(reply.get("text") or "")
        check("O5 Copilot 在线回答带项目引用",
              reply.get("mode") == "llm" and bool(citations)
              and any(marker in answer for marker in ("P1", "P2", "CIM", "上传")),
              f"mode={reply.get('mode')} citations={len(citations)} text={answer[:80]}")
        quote = str((memory[0] if memory else {}).get("quote") or "").strip()
        draft = session.get(base + f"/api/projects/{project_id}/draft", timeout=60).json()["draft"]
        sections = [{
            "id": "sec-001", "title": "引言", "blocks": [
                {"id": "blk-001", "type": "paragraph", "status": "checked",
                 "text": quote[:280] or "存内计算把乘累加搬到存储阵列附近。",
                 "evidence_ids": [memory[0]["id"]] if memory else [], "citation_keys": []},
                {"id": "blk-002", "type": "paragraph", "status": "draft",
                 "text": "该读出方案在 28nm 下把能效提升到 987 TOPS/W，面积只增加 3%。",
                 "evidence_ids": [], "citation_keys": []},
            ],
        }]
        saved = session.put(base + f"/api/projects/{project_id}/draft", json={
            "revision": draft["revision"], "title": "在线 PDF 审计草稿",
            "citation_style": "gb7714", "sections": sections,
        }, timeout=60)
        audit = session.post(base + f"/api/projects/{project_id}/draft/semantic-audit",
                             json={"limit": 5, "use_llm": True}, timeout=600)
        body = audit.json() if audit.status_code == 200 else {}
        items = {str(item.get("block_id")): item for item in body.get("items") or []}
        verdicts = {block_id: item.get("verdict") for block_id, item in items.items()}
        check("O6 语义核查区分原文引句与编造数字",
              saved.status_code == 200 and audit.status_code == 200 and body.get("mode") == "llm"
              and verdicts.get("blk-001") in {"supported", "partial"}
              and verdicts.get("blk-002") in {"unsupported", "insufficient"},
              f"save={saved.status_code} audit={audit.status_code} mode={body.get('mode')} verdicts={verdicts}")
        summary = session.get(base + "/api/llm/audit?limit=50", timeout=60).json().get("summary") or {}
        check("O7 模型调用无降级",
              int(summary.get("ok") or 0) >= 2 and not (summary.get("degrade_reasons") or {}),
              summary)
        archive = session.get(base + f"/api/projects/{project_id}/dossier", timeout=120)
        verified = session.post(base + "/api/dossiers/verify",
                                files={"file": ("online-pdf.zip", archive.content, "application/zip")},
                                timeout=120)
        check("O8 在线项目档案可导出/校验",
              archive.status_code == 200 and verified.status_code == 200
              and (verified.json().get("summary") or {}).get("evidence", 0) > 0,
              f"archive={archive.status_code} bytes={len(archive.content)} verify={verified.status_code}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    passed = sum(1 for item in checks if item["ok"])
    out_path = args.json or (ROOT / "out" / "live_pdf.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "model": os.getenv("PAPERPILOT_LLM_MODEL", ""),
        "checks": checks, "passed": passed, "total": len(checks),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n{passed}/{len(checks)} live PDF checks passed\nartifacts: {out_path}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())