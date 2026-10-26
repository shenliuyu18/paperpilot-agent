"""Four-cell Agent audit: {offline rules, online model} × {online papers, local PDFs}.

Every cell starts an isolated app instance, runs ``mode=auto`` to a terminal
state, and checks the same invariants:

* the run completes and produced real actions/evidence;
* every evidence item carries paper/section/page/quote (so "back to source" works);
* the report exists, is long enough, and its writing mode matches the cell
  (offline cells must not secretly spend model calls; online cells must show them);
* offline cells produce **zero** model calls, online cells show audit entries for
  this run and a budget that equals the charged, non-cached successes.

Artifacts go to ``out/agent-matrix-<timestamp>/`` (never packaged). The key is
read only from the environment; without it the online cells are reported as
skipped and the script exits 2 rather than pretending they passed.
"""
from __future__ import annotations

from _console import make_console_safe

make_console_safe()

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import truststore

truststore.inject_into_ssl()

ROOT = Path(__file__).resolve().parent.parent
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
WORK = ROOT / "out" / f"agent-matrix-{STAMP}"
KEY_ENV = ("PAPERPILOT_LLM_API_KEY", "OPENAI_API_KEY", "PAPERPILOT_LLM_BASE_URL",
           "OPENAI_BASE_URL", "PAPERPILOT_LLM_MODEL", "PAPERPILOT_LLM_ROUTES")
PDFS = [
    ROOT / "data" / "demo_pdfs" / "2606.08947v1.pdf",
    ROOT / "data" / "demo_pdfs" / "2609.24288v1.pdf",
]
ONLINE_GOAL = "梳理检索增强生成（RAG）的关键方法与证据边界"
LOCAL_GOAL = "比较两篇本地论文的 ADC 读出能耗与数字校准开销"
MAX_ACTIONS = 10
MAX_LLM_CALLS = 8

CELLS = [
    {"key": "offline-online", "label": "离线规则 × 在线论文", "online_model": False, "source": "auto", "goal": ONLINE_GOAL},
    {"key": "offline-local", "label": "离线规则 × 本地论文", "online_model": False, "source": "upload", "goal": LOCAL_GOAL},
    {"key": "online-online", "label": "在线模型 × 在线论文", "online_model": True, "source": "auto", "goal": ONLINE_GOAL},
    {"key": "online-local", "label": "在线模型 × 本地论文", "online_model": True, "source": "upload", "goal": LOCAL_GOAL},
]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _env_for(cell: dict, data_dir: Path) -> dict:
    env = dict(os.environ)
    for key in KEY_ENV:
        env.pop(key, None)
    env["PAPERPILOT_DATA_DIR"] = str(data_dir)
    env["PAPERPILOT_UPLOAD_DIR"] = str(data_dir / "uploads")
    env["PYTHONIOENCODING"] = "utf-8"
    if cell["online_model"]:
        for key in KEY_ENV:
            if key in os.environ:
                env[key] = os.environ[key]
        env.setdefault("PAPERPILOT_LLM_TIMEOUT", "180")
    return env


def _start_server(cell: dict, data_dir: Path) -> tuple[subprocess.Popen, str, Path]:
    data_dir.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    log_path = WORK / f"{cell['key']}.log"
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=_env_for(cell, data_dir), stdout=log, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited early:\n{log_path.read_text(encoding='utf-8')[-1500:]}")
        try:
            if requests.get(base + "/api/health", timeout=2).status_code == 200:
                return process, base, log_path
        except requests.RequestException:
            pass
        time.sleep(0.3)
    process.kill()
    raise RuntimeError("server did not become ready within 60s")


def _upload(base: str, path: Path) -> dict:
    with path.open("rb") as handle:
        response = requests.post(
            base + "/api/upload",
            files={"file": (path.name, handle, "application/pdf")},
            timeout=180,
        )
    response.raise_for_status()
    return response.json()


def _run(base: str, cell: dict) -> dict:
    created = requests.post(
        base + "/api/projects",
        json={"title": f"矩阵审计 · {cell['label']}", "question": cell["goal"]},
        timeout=30,
    )
    created.raise_for_status()
    project_id = created.json()["project"]["id"]
    started = requests.post(
        base + "/api/agent/runs",
        json={
            "goal": cell["goal"],
            "source": cell["source"],
            "mode": "auto",
            "project_id": project_id,
            "seed_memory": True,
            "max_actions": MAX_ACTIONS,
            "max_llm_calls": MAX_LLM_CALLS,
        },
        timeout=60,
    )
    started.raise_for_status()
    run_id = started.json()["run"]["id"]
    deadline = time.time() + 600
    run: dict = {}
    while time.time() < deadline:
        payload = requests.get(base + f"/api/agent/runs/{run_id}", timeout=30).json()["run"]
        run = payload
        if payload.get("status") not in {"queued", "running"}:
            break
        time.sleep(1.0)
    run["project_id"] = project_id
    return run


def _audit_calls(base: str, run_id: str) -> list[dict]:
    body = requests.get(base + "/api/llm/audit?limit=200", timeout=30).json()
    return [call for call in body.get("calls", []) if str(call.get("run_id") or "") == run_id]


def audit_cell(cell: dict) -> dict:
    data_dir = WORK / cell["key"]
    process, base, log_path = _start_server(cell, data_dir)
    entry: dict = {"key": cell["key"], "label": cell["label"], "checks": []}

    def check(name: str, ok: bool, detail) -> None:
        entry["checks"].append({"name": name, "ok": bool(ok), "detail": detail})

    try:
        health = requests.get(base + "/api/health", timeout=10).json()
        want_mode = "online" if cell["online_model"] else "offline"
        check("实例模式", health.get("mode") == want_mode, health)

        if cell["source"] == "upload":
            uploaded = []
            for path in PDFS:
                if not path.exists():
                    break
                uploaded.append(_upload(base, path))
            ids = [item.get("paper_id") for item in uploaded if item.get("paper_id")]
            check("本地论文上传", len(ids) == len(PDFS), {"ids": ids})
        else:
            probe = requests.post(
                base + "/api/search",
                json={"query": cell["goal"], "limit": 5, "source": "auto"},
                timeout=120,
            ).json()
            status = probe.get("source_status") or {}
            check("在线检索可用", probe.get("count", 0) >= 2 and not status.get("fallback"),
                  {"count": probe.get("count"), "fallback": status.get("fallback"),
                   "sources": [s.get("status") for s in status.get("sources", [])]})

        run = _run(base, cell)
        steps = run.get("steps") or []
        evidence = run.get("evidence") or []
        check("运行完成", run.get("status") == "completed",
              {"status": run.get("status"), "error": run.get("error")})
        check("动作数", len(steps) >= 4, {"steps": len(steps),
                                       "tools": [s.get("tool") for s in steps]})
        check("证据非空", len(evidence) >= 3, {"evidence": len(evidence)})
        needs_page = cell["source"] == "upload"
        complete = [e for e in evidence
                    if e.get("paper_id") and e.get("section") and e.get("quote")
                    and (e.get("page") or not needs_page)]
        check("证据可回原文", len(complete) == len(evidence),
              {"complete": len(complete), "total": len(evidence), "page_required": needs_page})

        recorded = Path(str((run.get("report") or {}).get("path") or ""))
        report_path = recorded if recorded.is_file() else data_dir / "agent_runs" / str(run.get("id")) / "report.md"
        report = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
        check("简报存在", len(report) >= 500, {"chars": len(report), "path": str(report_path)})
        if cell["online_model"]:
            check("简报表记为模型撰写", run.get("report", {}).get("mode") == "llm",
                  {"mode": run.get("report", {}).get("mode")})
        else:
            check("简报由规则生成", run.get("report", {}).get("mode") in {"offline", "rules", None},
                  {"mode": run.get("report", {}).get("mode")})
        legend = run.get("report", {}).get("legend") or {}
        cited = sorted({token for token in legend})
        check("简报图例覆盖论文", len(cited) >= 2 or not legend, {"legend": cited[:6]})

        calls = _audit_calls(base, str(run.get("id")))
        charged = [c for c in calls if c.get("ok") and not c.get("cached")]
        budget = (run.get("budget_used") or {}).get("llm_calls")
        if cell["online_model"]:
            check("模型调用已留审计", bool(charged), {"calls": len(calls), "charged": len(charged)})
            failed_calls = [c for c in calls if not c.get("ok")]
            # Failures are allowed, but each must carry a reason: a silent one
            # would look like a success in the audit log.
            check("失败调用都有原因", all(c.get("degrade_reason") for c in failed_calls),
                  {"failed": [c.get("degrade_reason") for c in failed_calls]})
            check("预算与审计一致", int(budget or 0) == len(charged),
                  {"budget": budget, "charged": len(charged)})
        else:
            check("离线不调用模型", not calls, {"calls": len(calls)})

        entry.update({
            "status": run.get("status"),
            "actions": len(steps),
            "tools": [s.get("tool") for s in steps],
            "evidence": len(evidence),
            "report_chars": len(report),
            "report_mode": run.get("report", {}).get("mode"),
            "llm_calls": budget,
            "audit_calls": len(calls),
            "project_id": run.get("project_id"),
        })
    except Exception as exc:  # noqa: BLE001 - a crashed cell is a finding, not a crash
        check("执行未抛错", False, f"{type(exc).__name__}: {exc}")
        entry["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
        entry["log"] = str(log_path)
    entry["passed"] = sum(1 for c in entry["checks"] if c["ok"])
    entry["total"] = len(entry["checks"])
    return entry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default="")
    parser.add_argument("--only", default="", help="逗号分隔的格子键")
    args = parser.parse_args()

    WORK.mkdir(parents=True, exist_ok=True)
    has_key = bool(os.environ.get("PAPERPILOT_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    wanted = {part.strip() for part in args.only.split(",") if part.strip()}
    results = []
    for cell in CELLS:
        if wanted and cell["key"] not in wanted:
            continue
        if cell["online_model"] and not has_key:
            results.append({"key": cell["key"], "label": cell["label"], "skipped": True,
                            "checks": [], "passed": 0, "total": 0})
            print(f"SKIP {cell['label']}（未提供 PAPERPILOT_LLM_API_KEY）")
            continue
        print(f"\n== {cell['label']} ==")
        entry = audit_cell(cell)
        results.append(entry)
        for item in entry["checks"]:
            print(f"  {'PASS' if item['ok'] else 'FAIL'} {item['name']} | {item['detail']}")
        print(f"  -> {entry['passed']}/{entry['total']}")

    passed = sum(1 for r in results if not r.get("skipped") and r["passed"] == r["total"])
    total = sum(1 for r in results if not r.get("skipped"))
    print(f"\n{passed}/{total} cells passed")
    report = {"generated_at": STAMP, "cells": results}
    target = Path(args.json) if args.json else WORK / "matrix.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [r for r in results if not r.get("skipped") and r["passed"] != r["total"]]
    skipped = [r for r in results if r.get("skipped")]
    if skipped:
        return 2
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())