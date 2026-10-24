"""Real-model live check: agent run, Copilot, semantic audit, contradiction review.

Requires a real OpenAI-compatible endpoint via the usual environment variables
(``PAPERPILOT_LLM_API_KEY`` / ``_BASE_URL`` / ``_MODEL``). Without a key it exits 2
and writes nothing, exactly like ``eval_real_llm.py``: a skipped real-model run
must never look like a pass.

What it proves, in order:

1. ``/api/health`` flips to online mode from the environment configuration;
2. ``POST /api/llm/settings/test`` reaches the endpoint with a real completion;
3. an ``auto`` agent run reaches a terminal state, produces a report and spends
   real model calls (budget visible);
4. project Copilot answers from project assets with citations (mode ``llm``);
5. the draft semantic audit gets real per-block judgements;
6. contradiction scan + model review either produces reviewed items or reports
   that the corpus has none;
7. ``/api/llm/audit`` shows which purposes actually called the model.

Artifacts go to ``out/live-agent-<timestamp>/`` (never packaged). The key is only
read from the environment, never printed, and the audit log stores no prompts.
"""
from __future__ import annotations

import json
import os
import re
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
WORK = ROOT / "out" / f"live-agent-{STAMP}"
DATA = WORK / "data"
BASE_ENV_KEYS = ("PAPERPILOT_LLM_API_KEY", "OPENAI_API_KEY", "PAPERPILOT_LLM_BASE_URL",
                 "OPENAI_BASE_URL", "PAPERPILOT_LLM_MODEL", "PAPERPILOT_LLM_ROUTES")

TITLE = "存算一体低功耗 ADC 的真实模型核验"
QUESTION = "如何在保持精度的同时降低存内计算 ADC 的数据搬运与校准开销？"
GOAL = "梳理存内计算 ADC 的低功耗架构与数字校准开销"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_server(port: int) -> subprocess.Popen:
    DATA.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PAPERPILOT_DATA_DIR"] = str(DATA)
    env["PYTHONIOENCODING"] = "utf-8"
    log = (WORK / "server.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited early:\n{(WORK / 'server.log').read_text(encoding='utf-8')[-2000:]}")
        try:
            if requests.get(base + "/api/health", timeout=2).status_code == 200:
                return process
        except requests.RequestException:
            time.sleep(0.4)
    raise RuntimeError("server did not become healthy")


def _wait_run(session: requests.Session, base: str, run_id: str, timeout: float = 900.0) -> dict:
    terminal = {"completed", "failed", "cancelled", "awaiting_approval", "interrupted"}
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = session.get(f"{base}/api/agent/runs/{run_id}", timeout=30).json()["run"]
        if last.get("status") in terminal:
            return last
        time.sleep(1.0)
    raise RuntimeError(f"run {run_id} still {last.get('status')} after {timeout:.0f}s")


def _quote_for(evidence: dict) -> str:
    quote = str(evidence.get("quote") or evidence.get("text") or "").strip()
    return " ".join(quote.split())[:220]


def main() -> int:
    if not os.getenv("PAPERPILOT_LLM_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        print("未配置 PAPERPILOT_LLM_API_KEY，真实模型实测不执行（退出码 2，不写报告）。")
        return 2
    WORK.mkdir(parents=True, exist_ok=True)
    base = ""
    checks: list[dict] = []
    artifacts: dict = {"started_at": STAMP, "model": os.getenv("PAPERPILOT_LLM_MODEL", ""),
                       "base_url": os.getenv("PAPERPILOT_LLM_BASE_URL", ""), "checks": checks}

    def check(name: str, ok: bool, detail: str = "") -> bool:
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})
        print(("PASS " if ok else "FAIL ") + name + (" | " + str(detail)[:140] if detail else ""), flush=True)
        return ok

    port = _free_port()
    process = _start_server(port)
    base = f"http://127.0.0.1:{port}"
    session = requests.Session()
    try:
        health = session.get(f"{base}/api/health", timeout=30).json()
        check("health 反映在线模式", health.get("mode") == "online" and health.get("llm_enabled"),
              f"mode={health.get('mode')} version={health.get('version')}")

        settings = session.get(f"{base}/api/llm/settings", timeout=60).json()
        st = settings.get("settings") or {}
        check("设置来自环境变量且不回显密钥",
              st.get("has_key") and st.get("key_source") == "env" and "api_key" not in st,
              f"model={st.get('model')} masked={st.get('masked_key')}")

        probe = session.post(f"{base}/api/llm/settings/test", json={}, timeout=180)
        body = probe.json() if probe.headers.get("content-type", "").startswith("application/json") else {}
        check("真实最小补全探活", probe.status_code == 200 and bool(body.get("reply")),
              f"code={probe.status_code} model={body.get('model')} reply={str(body.get('reply'))[:60]} "
              f"latency={body.get('latency_ms')}ms detail={str(body.get('detail'))[:100]}")
        artifacts["probe"] = body

        project = session.post(f"{base}/api/projects", json={
            "title": TITLE, "question": QUESTION,
            "scope": "存内计算架构、SAR ADC、数字校准", "tags": ["真实模型", "ADC"],
        }, timeout=60).json()["project"]
        pid = project["id"]
        artifacts["project_id"] = pid

        started = time.time()
        run = session.post(f"{base}/api/agent/runs", json={
            "goal": GOAL, "source": "demo", "mode": "auto", "project_id": pid,
            "max_papers": 3, "max_actions": 8, "max_llm_calls": 6, "max_seconds": 600,
        }, timeout=120).json()["run"]
        run = _wait_run(session, base, run["id"])
        elapsed = round(time.time() - started, 1)
        steps = run.get("steps") or []
        titles = [str(step.get("title") or step.get("tool") or "") for step in steps]
        report_meta = run.get("report") if isinstance(run.get("report"), dict) else {}
        report_path = Path(str(report_meta.get("path") or ""))
        report = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
        budget = run.get("budget_used") or {}
        check("Agent auto 运行完成", run.get("status") == "completed",
              f"status={run.get('status')} steps={len(steps)} elapsed={elapsed}s "
              f"llm_calls={budget.get('llm_calls')} titles={titles[:6]}")
        has_markers = bool(re.search(r"\[[^\]]+\]", report))
        check("Agent 由模型撰写研究简报",
              len(report) >= 300 and report_meta.get("mode") == "llm" and has_markers
              and report_meta.get("chars") == len(report),
              f"report_chars={len(report)} mode={report_meta.get('mode')} "
              f"markers={has_markers} path={report_path.name}")
        artifacts["run"] = {"id": run["id"], "status": run.get("status"), "elapsed_s": elapsed,
                            "titles": titles, "budget": budget, "report_chars": len(report),
                            "report_mode": report_meta.get("mode"), "report": report}
        (WORK / "agent-report.md").write_text(report or "(empty)", encoding="utf-8")

        evidence = session.get(f"{base}/api/projects/{pid}/memory?kind=evidence&limit=100",
                               timeout=60).json().get("items") or []
        papers = session.get(f"{base}/api/projects/{pid}/memory?kind=papers&limit=100",
                             timeout=60).json().get("items") or []
        anchored = [item for item in evidence if item.get("verified")][:2]
        artifacts["memory"] = {"evidence": len(evidence), "papers": len(papers)}

        keyword = ""
        if papers:
            words = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}|[\u4e00-\u9fff]{2,}",
                               str(papers[0].get("title") or ""))
            keyword = words[0] if words else ""
        question = f"{keyword} 这篇工作的方法和实验证据是什么？" if keyword else "项目里有哪些方法证据？"
        ask = session.post(f"{base}/api/projects/{pid}/assistant",
                           json={"message": question}, timeout=300)
        reply = (ask.json() or {}).get("reply") or {}
        citations = reply.get("citations") or []
        check("Copilot 使用模型回答", ask.status_code == 200 and reply.get("mode") == "llm",
              f"mode={reply.get('mode')} citations={len(citations)} question={question[:40]}")
        check("Copilot 回答带项目引用", len(citations) >= 1,
              f"citations={len(citations)} text={str(reply.get('text'))[:80]}")
        artifacts["copilot"] = {"question": question, "mode": reply.get("mode"),
                                "citations": len(citations), "text": reply.get("text"),
                                "llm": reply.get("llm")}

        draft = session.get(f"{base}/api/projects/{pid}/draft", timeout=60).json()["draft"]
        quote = _quote_for(anchored[0]) if anchored else ""
        sections = [
            {"id": "sec-001", "title": "引言", "blocks": [
                {"id": "blk-001", "type": "paragraph", "status": "checked",
                 "text": f"已有工作报告了存内计算读出电路的设计取舍。{quote}"[:600],
                 "evidence_ids": [anchored[0]["id"]] if anchored else [], "citation_keys": []},
                {"id": "blk-002", "type": "paragraph", "status": "draft",
                 "text": "该方案在 28nm 工艺下把能效提升到 987 TOPS/W，面积仅增加 3%。",
                 "evidence_ids": [], "citation_keys": []},
            ]},
        ]
        saved = session.put(f"{base}/api/projects/{pid}/draft", json={
            "revision": draft["revision"], "title": "真实模型核验草稿",
            "citation_style": "gb7714", "sections": sections,
        }, timeout=60)
        check("草稿保存（含证据锚点与未支撑数值）", saved.status_code == 200, f"code={saved.status_code}")

        audit = session.post(f"{base}/api/projects/{pid}/draft/semantic-audit",
                             json={"limit": 5, "use_llm": True}, timeout=600)
        audit_body = audit.json() if audit.status_code == 200 else {}
        items = audit_body.get("items") or []
        verdicts = [str(item.get("verdict") or "") for item in items]
        sources = [str(item.get("source") or "") for item in items]
        check("语义核查返回逐块判定", audit.status_code == 200 and bool(items),
              f"code={audit.status_code} blocks={len(items)} verdicts={verdicts[:5]}")
        check("语义核查由模型复核（mode=llm）",
              audit_body.get("mode") == "llm" and "llm" in sources,
              f"mode={audit_body.get('mode')} sources={sources[:5]} counts={audit_body.get('counts')}")
        artifacts["semantic_audit"] = {"code": audit.status_code, "count": len(items),
                                       "verdicts": verdicts, "sources": sources,
                                       "mode": audit_body.get("mode"),
                                       "counts": audit_body.get("counts"),
                                       "llm": audit_body.get("llm"), "body": audit_body}

        scan = session.post(f"{base}/api/projects/{pid}/contradictions/scan",
                            json={"paper_ids": [p["id"] for p in papers[:5]]}, timeout=120)
        contradictions = session.get(f"{base}/api/projects/{pid}/contradictions", timeout=60).json()
        entries = contradictions.get("items") or contradictions.get("contradictions") or []
        review = {}
        if entries:
            review = session.post(f"{base}/api/projects/{pid}/contradictions/review",
                                  json={"use_llm": True, "limit": 5}, timeout=600).json()
            reviewed = [item.get("status") for item in (review.get("items") or [])]
            check("矛盾模型复核写入结论",
                  int(review.get("reviewed") or 0) >= 1 and not review.get("degraded"),
                  f"scan={scan.status_code} items={len(entries)} reviewed={review.get('reviewed')} "
                  f"statuses={reviewed[:4]} degraded={review.get('degraded')} "
                  f"reason={review.get('degrade_reason')}")
        else:
            check("矛盾扫描（语料内无矛盾属预期）", scan.status_code == 200,
                  f"scan={scan.status_code} items=0")
        artifacts["contradictions"] = {"scan_code": scan.status_code, "count": len(entries),
                                       "review": review}

        llm = session.get(f"{base}/api/llm/audit?limit=50", timeout=60).json()
        summary = llm.get("summary") or {}
        calls = llm.get("calls") or []
        purposes = summary.get("by_purpose") or {}
        models = sorted({str(call.get("model") or "") for call in calls})
        check("模型调用被审计且成功", int(summary.get("ok") or 0) >= 3,
              f"calls={summary.get('calls')} ok={summary.get('ok')} failed={summary.get('failed')} "
              f"purposes={purposes} models={models}")
        check("Agent/Copilot/核查三类用途都真的调用了模型",
              {"agent_report", "assistant", "semantic_audit"} <= set(purposes),
              f"purposes={sorted(purposes)}")
        artifacts["llm_audit"] = {"summary": summary, "models": models,
                                  "calls": [{k: call.get(k) for k in
                                             ("at", "purpose", "model", "latency_ms", "ok",
                                              "error", "degrade_reason", "response_chars")}
                                            for call in calls]}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    passed = sum(1 for item in checks if item["ok"])
    artifacts["passed"] = passed
    artifacts["total"] = len(checks)
    (WORK / "evidence.json").write_text(json.dumps(artifacts, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
    lines = [f"# 真实模型实测 {STAMP}", "",
             f"- 模型：{artifacts['model']}",
             f"- 端点：{artifacts['base_url']}", "",
             "| 检查 | 结果 | 证据 |", "|---|---|---|"]
    for item in checks:
        lines.append(f"| {item['name']} | {'PASS' if item['ok'] else 'FAIL'} | {item['detail'].replace('|', '/')} |")
    (WORK / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{passed}/{len(checks)} live checks passed")
    print("artifacts:", WORK)
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
