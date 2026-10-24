"""Real-key consistency audit: does the MODEL's output stay tied to the evidence?

`audit_consistency.py` covers the deterministic offline chain. This script covers the
paths where a model writes text, using the same key the product uses:

1. agent report (LLM mode) — canonical `[Pn]` legend, numbered references, and **every
   substantive number in the body must exist in the run state** (evidence / paper
   metadata / goal); a fabricated number is a hard failure.
2. Copilot — `mode=llm`, citations present, every cited quote is verbatim in the paper,
   and numbers in the answer must come from the cited quotes or the question.
3. semantic audit — a block that copies a verified quote must not be judged
   unsupported; a block with an invented number must not be judged supported.

Without ``PAPERPILOT_LLM_API_KEY`` it exits 2 and writes nothing.

Usage::

    python scripts/live_audit_consistency.py [--json out/live-audit.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import requests
import truststore

truststore.inject_into_ssl()

ROOT = Path(__file__).resolve().parents[1]
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
WORK = ROOT / "out" / f"live-audit-{STAMP}"
DATA = WORK / "data"

TITLE = "真实模型一致性审计"
QUESTION = "存内计算 ADC 的读出能耗与校准开销如何权衡？"
GOAL = "梳理存内计算 ADC 的低功耗架构与数字校准开销"

_NUM = re.compile(r"(?<![\w.\-])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(\s*[%×xX])?")


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
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited early:\n{(WORK / 'server.log').read_text(encoding='utf-8')[-1500:]}")
        try:
            if requests.get(base + "/api/health", timeout=2).status_code == 200:
                return process
        except requests.RequestException:
            time.sleep(0.4)
    raise RuntimeError("server did not become healthy")


def _wait_run(session: requests.Session, base: str, run_id: str, timeout: float = 900.0) -> dict:
    terminal = {"completed", "failed", "cancelled", "awaiting_approval", "interrupted"}
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = session.get(f"{base}/api/agent/runs/{run_id}", timeout=30).json()["run"]
        if last.get("status") in terminal:
            return last
        time.sleep(1.0)
    return last


def _norm(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    return " ".join(value.split())


def _numbers(text: str) -> list[str]:
    """Only claim-like numbers: percentages, decimals, values >= 100, or years."""
    found: list[str] = []
    for match in _NUM.finditer(_norm(text)):
        raw, unit = match.group(1), (match.group(2) or "").strip()
        value = raw.replace(",", "")
        if "." not in value:
            try:
                number = int(value)
            except ValueError:
                continue
            if not (number >= 100 or 1900 <= number <= 2100):
                continue
        token = value + ("%" if unit == "%" else "")
        if token not in found:
            found.append(token)
    return found


def _contains_number(haystack: str, token: str) -> bool:
    if token.endswith("%"):
        return token in haystack
    return bool(re.search(rf"(?<![\d.]){re.escape(token)}(?![\d.])", haystack))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()
    if not os.getenv("PAPERPILOT_LLM_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        print("未配置 PAPERPILOT_LLM_API_KEY，真实模型一致性审计不执行（退出码 2，不写报告）。")
        return 2
    WORK.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []
    artifacts: dict = {"started_at": STAMP, "model": os.getenv("PAPERPILOT_LLM_MODEL", ""),
                       "base_url": os.getenv("PAPERPILOT_LLM_BASE_URL", ""), "checks": checks}

    def check(name: str, ok: bool, detail: str = "", skipped: bool = False) -> bool:
        checks.append({"name": name, "ok": bool(ok), "skipped": bool(skipped),
                       "detail": str(detail)[:500]})
        label = "SKIP " if skipped else ("PASS " if ok else "FAIL ")
        print(label + name + ("  | " + str(detail)[:160] if detail else ""), flush=True)
        return ok

    port = _free_port()
    process = _start_server(port)
    base = f"http://127.0.0.1:{port}"
    session = requests.Session()
    try:
        health = session.get(f"{base}/api/health", timeout=30).json()
        check("L1 实例以真实模型在线", health.get("mode") == "online", health)

        # Refuse to run when the endpoint itself is unusable: a quota/credential failure
        # must never be reported as a consistency bug.
        probe = session.post(f"{base}/api/llm/settings/test", json={}, timeout=180)
        if probe.status_code != 200:
            detail = (probe.json() or {}).get("detail") if "json" in probe.headers.get("content-type", "") else probe.text
            print(f"模型端点不可用（{probe.status_code}）：{detail}")
            print("本次审计未执行，不写报告；配额或鉴权恢复后重跑。")
            artifacts["provider_error"] = {"code": probe.status_code, "detail": str(detail)[:300]}
            return 3
        artifacts["probe"] = probe.json()

        project = session.post(f"{base}/api/projects", json={
            "title": TITLE, "question": QUESTION, "scope": "CIM ADC", "tags": ["live-audit"],
        }, timeout=60).json()["project"]
        pid = project["id"]
        run = session.post(f"{base}/api/agent/runs", json={
            "goal": GOAL, "source": "demo", "mode": "auto", "project_id": pid,
            "max_papers": 3, "max_actions": 8, "max_llm_calls": 6, "max_seconds": 600,
        }, timeout=120).json()["run"]
        run = _wait_run(session, base, run["id"])
        report_meta = run.get("report") or {}
        report_path = Path(str(report_meta.get("path") or ""))
        report = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
        check("L2 Agent 运行完成且简报由模型撰写",
              run.get("status") == "completed" and report_meta.get("mode") == "llm" and bool(report),
              f"status={run.get('status')} mode={report_meta.get('mode')} chars={len(report)}")

        papers = session.get(f"{base}/api/projects/{pid}/memory?kind=papers&limit=100",
                             timeout=60).json().get("items") or []
        evidence = session.get(f"{base}/api/projects/{pid}/memory?kind=evidence&limit=100",
                               timeout=60).json().get("items") or []
        titles = {str(paper.get("title") or "") for paper in papers}
        legend = dict(re.findall(r"\[(P\d+)\] = ([^；\n]+)", report))
        used = set(re.findall(r"\[(P\d+)\]", report))
        legend_ok = (bool(legend) and set(legend) == {f"P{i}" for i in range(1, len(papers) + 1)}
                     and all(name.strip()[:20] in {title[:20] for title in titles}
                             for name in legend.values()))
        refs = report.split("## 参考文献")[-1] if "## 参考文献" in report else ""
        ref_marks = set(re.findall(r"^- \[(P\d+)\]", refs, re.M))
        check("L3 简报引用图例与本次论文一一对应",
              legend_ok and ref_marks == set(legend) and used <= set(legend),
              f"legend={legend} refs={sorted(ref_marks)} used={sorted(used)}")

        # Ground truth for numbers: run state (evidence + paper metadata + goal), not the report.
        # The memory API keeps only paper briefs; the run's own snapshot has the
        # abstract/tldr/results the report was allowed to read.
        snapshot = report_path.parent / "papers.json"
        papers_for_numbers = papers
        if snapshot.is_file():
            try:
                loaded = json.loads(snapshot.read_text(encoding="utf-8"))
                if isinstance(loaded, list) and loaded:
                    papers_for_numbers = loaded
            except (OSError, json.JSONDecodeError):
                pass
        haystack = _norm(" ".join([
            GOAL,
            *[str(item.get(key) or "") for item in evidence
              for key in ("quote", "claim", "title", "section")],
            *[json.dumps({k: paper.get(k) for k in ("title", "tldr", "metrics", "results", "abstract")},
                         ensure_ascii=False, default=str) for paper in papers_for_numbers],
        ]))
        prose = report.split("## 参考文献")[0]
        body = "\n".join(
            line for line in prose.splitlines()
            if line.strip() and not line.lstrip().startswith("#") and not line.startswith(">")
            and not line.startswith("|") and not line.startswith("## 动作轨迹")
        )
        numbers = _numbers(body)
        missing = [token for token in numbers if not _contains_number(haystack, token)]
        model_report = report_meta.get("mode") == "llm"
        check("L4 简报里的数字都能在证据/论文元数据里找到（不编数）",
              (not missing) if model_report else True,
              (f"checked={len(numbers)} missing={missing[:8]}" if model_report
               else "简报不是模型撰写（模型调用失败），本条不适用"),
              skipped=not model_report)
        artifacts["report"] = {"path": str(report_path), "chars": len(report),
                               "legend": legend, "numbers": numbers, "missing": missing}

        # Copilot must tie its answer (and its numbers) to the passages it cites.
        keyword = ""
        if papers:
            words = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", str(papers[0].get("title") or ""))
            keyword = words[0] if words else ""
        question = f"{keyword} 这篇工作的能效和实验结论是什么？" if keyword else "项目里有哪些方法证据？"
        reply = (session.post(f"{base}/api/projects/{pid}/assistant",
                              json={"message": question}, timeout=300).json() or {}).get("reply") or {}
        citations = reply.get("citations") or []
        cited_text = _norm(" ".join(
            str(item.get(key) or "")
            for item in citations
            for key in ("quote", "title", "locator")
        ))
        cited_marks = {int(value) for value in re.findall(r"\[(\d{1,2})\]", str(reply.get("text") or ""))}
        answer_numbers = _numbers(str(reply.get("text") or ""))
        unsupported_numbers = [token for token in answer_numbers
                               if not _contains_number(cited_text, token)
                               and not _contains_number(_norm(question), token)]
        check("L5 Copilot 用模型回答且引用列表覆盖回答里的编号",
              reply.get("mode") == "llm" and len(citations) >= max(1, len(cited_marks)),
              f"mode={reply.get('mode')} citations={len(citations)} marks={sorted(cited_marks)} "
              f"text={str(reply.get('text'))[:70]}")
        check("L6 Copilot 回答里的数字都来自所引原文",
              not unsupported_numbers,
              f"checked={len(answer_numbers)} missing={unsupported_numbers[:8]} cited={len(citations)}")
        artifacts["copilot"] = {"question": question, "mode": reply.get("mode"),
                                "citations": len(citations), "numbers": answer_numbers,
                                "unsupported": unsupported_numbers,
                                "text": reply.get("text")}

        # Semantic audit: verbatim evidence must not be called unsupported, invented numbers must not pass.
        anchored = [item for item in evidence if item.get("verified")][:1]
        quote = _norm(anchored[0].get("quote")) if anchored else ""
        draft = session.get(f"{base}/api/projects/{pid}/draft", timeout=60).json()["draft"]
        sections = [{"id": "sec-001", "title": "引言", "blocks": [
            {"id": "blk-001", "type": "paragraph", "status": "checked",
             "text": quote[:280] or "存内计算把乘累加搬到存储阵列附近。",
             "evidence_ids": [anchored[0]["id"]] if anchored else [], "citation_keys": []},
            {"id": "blk-002", "type": "paragraph", "status": "draft",
             "text": "该读出方案在 28nm 下把能效提升到 987 TOPS/W，面积只增加 3%。",
             "evidence_ids": [], "citation_keys": []},
        ]}]
        saved = session.put(f"{base}/api/projects/{pid}/draft", json={
            "revision": draft["revision"], "title": "真实模型审计草稿",
            "citation_style": "gb7714", "sections": sections,
        }, timeout=60)
        audit = session.post(f"{base}/api/projects/{pid}/draft/semantic-audit",
                             json={"limit": 5, "use_llm": True}, timeout=600)
        body_audit = audit.json() if audit.status_code == 200 else {}
        items = {str(item.get("block_id")): item for item in body_audit.get("items") or []}
        verdict_quote = str((items.get("blk-001") or {}).get("verdict") or "")
        verdict_fake = str((items.get("blk-002") or {}).get("verdict") or "")
        check("L7 语义核查由模型复核",
              saved.status_code == 200 and audit.status_code == 200 and body_audit.get("mode") == "llm",
              f"save={saved.status_code} audit={audit.status_code} mode={body_audit.get('mode')}")
        check("L8 原文引句不被判为不支持",
              verdict_quote in {"supported", "partial"}, f"verdict={verdict_quote}")
        check("L9 编造数字的段落不被判为支持",
              verdict_fake in {"unsupported", "insufficient"}, f"verdict={verdict_fake}")
        artifacts["semantic_audit"] = {"verdicts": {key: item.get("verdict") for key, item in items.items()},
                                       "sources": {key: item.get("source") for key, item in items.items()},
                                       "mode": body_audit.get("mode"), "counts": body_audit.get("counts")}

        llm = session.get(f"{base}/api/llm/audit?limit=30", timeout=60).json()
        summary = llm.get("summary") or {}
        check("L10 模型调用被审计且无降级",
              int(summary.get("ok") or 0) >= 3 and not (summary.get("degrade_reasons") or {}),
              f"calls={summary.get('calls')} ok={summary.get('ok')} failed={summary.get('failed')} "
              f"reasons={summary.get('degrade_reasons')} purposes={summary.get('by_purpose')}")
        artifacts["llm_audit"] = summary
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    scored = [item for item in checks if not item.get("skipped")]
    passed = sum(1 for item in scored if item["ok"])
    summary = artifacts.get("llm_audit") or {}
    reasons = summary.get("degrade_reasons") or {}
    # The provider refusing calls (quota / auth / throttling) means the model-path
    # questions were never answered — that is "inconclusive", not a consistency bug.
    blocked = (not int(summary.get("ok") or 0)
               and any(reason in reasons for reason in ("auth", "rate_limit", "request")))
    artifacts["passed"] = passed
    artifacts["total"] = len(scored)
    artifacts["skipped"] = len(checks) - len(scored)
    artifacts["inconclusive"] = bool(blocked)
    out_path = args.json or (WORK / "evidence.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifacts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n{passed}/{len(scored)} live consistency checks passed"
          + (f"（跳过 {len(checks) - len(scored)} 条）" if len(scored) != len(checks) else ""))
    print("artifacts:", out_path)
    if blocked:
        print("结果不确定：模型端点拒绝了本次审计（" + ", ".join(sorted(reasons))
              + "），这是供应商限流/鉴权问题，不是一致性缺陷；稍后重跑。")
        return 4
    return 0 if passed == len(scored) else 1


if __name__ == "__main__":
    raise SystemExit(main())
