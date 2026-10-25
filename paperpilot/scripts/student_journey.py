"""End-to-end student journey: project -> local PDFs -> Agent -> Copilot -> topic -> draft -> export.

Requires an OpenAI-compatible endpoint in the environment::

    PAPERPILOT_LLM_API_KEY, PAPERPILOT_LLM_BASE_URL, PAPERPILOT_LLM_MODEL

A local gateway additionally requires ``PAPERPILOT_ALLOW_PRIVATE_LLM=1``. Without
credentials the script exits 2 and writes nothing. It uses an isolated
data directory under ``out/student-journey-<timestamp>`` and never touches the
shipped project data. Exit 0 only when the draft saves, preflight is not
blocking, all three exports contain the draft, and the dossier has draft
sections.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests
import truststore

truststore.inject_into_ssl()
ROOT = Path(__file__).resolve().parents[1]
KEY = os.getenv("PAPERPILOT_LLM_API_KEY", "")
BASE_URL = os.getenv("PAPERPILOT_LLM_BASE_URL", "").rstrip("/")
MODEL = os.getenv("PAPERPILOT_LLM_MODEL", "deepseek-v4.1-flash-1017")


def port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_health(base: str, proc: subprocess.Popen) -> None:
    for _ in range(200):
        if proc.poll() is not None:
            raise RuntimeError("server exited")
        try:
            if requests.get(base + "/api/health", timeout=2).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.3)
    raise RuntimeError("server not healthy")


def wait_run(session: requests.Session, base: str, run_id: str) -> dict:
    terminal = {"completed", "failed", "cancelled", "interrupted", "awaiting_approval"}
    last = {}
    for _ in range(900):
        last = session.get(f"{base}/api/agent/runs/{run_id}", timeout=30).json()["run"]
        if last.get("status") in terminal:
            return last
        time.sleep(1)
    return last


def payload(response: requests.Response, label: str) -> dict:
    if response.status_code >= 400:
        raise RuntimeError(f"{label} -> {response.status_code}: {response.text[:1200]}")
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"{label} -> non-JSON body: {response.text[:400]}") from exc


def ensure_ok(response: requests.Response, label: str) -> requests.Response:
    if response.status_code >= 400:
        raise RuntimeError(f"{label} -> {response.status_code}: {response.text[:1200]}")
    return response


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    if not KEY or not BASE_URL:
        print("PAPERPILOT_LLM_API_KEY and PAPERPILOT_LLM_BASE_URL are required")
        return 2
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = ROOT / "out" / f"student-journey-{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    data = work / "data"
    port_number = port()
    base = f"http://127.0.0.1:{port_number}"
    env = dict(os.environ)
    env.update({
        "PAPERPILOT_DATA_DIR": str(data),
        "PAPERPILOT_PROJECT_FILE": str(data / "projects.json"),
        "PAPERPILOT_PROJECT_DIR": str(data / "projects"),
        "PAPERPILOT_AGENT_FILE": str(data / "runs.json"),
        "PAPERPILOT_AGENT_DIR": str(data / "runs"),
        "PAPERPILOT_UPLOAD_DIR": str(data / "uploads"),
        "PAPERPILOT_LLM_API_KEY": KEY,
        "PAPERPILOT_LLM_BASE_URL": BASE_URL,
        "PAPERPILOT_LLM_MODEL": MODEL,
        "PAPERPILOT_LLM_TIMEOUT": "180",
        "PAPERPILOT_LLM_RETRIES": "1",
        "PYTHONUTF8": "1",
    })
    log = (work / "server.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port_number), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, text=True,
    )
    session = requests.Session()
    summary: dict = {"model": MODEL, "artifacts": str(work)}
    try:
        wait_health(base, proc)
        project = payload(session.post(base + "/api/projects", json={
            "title": "低功耗存内计算 ADC 课程论文",
            "question": "存内计算 ADC 的读出能耗与数字校准开销如何权衡？",
            "scope": "CIM / SAR ADC / 65nm",
            "success_criteria": "完成一篇有证据、可导出的课程论文",
            "tags": ["student-journey"],
        }, timeout=60), "create project")["project"]
        pid = project["id"]
        summary["project"] = project
        uploads = []
        for pdf in sorted((ROOT / "data" / "demo_pdfs").glob("*.pdf"))[:2]:
            response = session.post(base + "/api/upload", files={
                "file": (pdf.name, pdf.read_bytes(), "application/pdf"),
            }, timeout=120)
            item = payload(response, f"upload {pdf.name}")
            check(bool(item.get("paper_id")), f"upload {pdf.name} returned no paper_id")
            uploads.append(item)
            (work / pdf.name).write_bytes(pdf.read_bytes())
        check(len(uploads) == 2, f"expected 2 uploaded papers, got {len(uploads)}")
        ids = [item["paper_id"] for item in uploads]
        summary["uploads"] = [{
            "id": item.get("paper_id"), "title": item.get("title"),
            "pages": item.get("pages"), "sections": item.get("sections"),
        } for item in uploads]
        run = payload(session.post(base + "/api/agent/runs", json={
            "goal": "比较两篇本地论文的 ADC 读出能耗与数字校准开销，找出可做的课程论文选题",
            "source": "upload", "mode": "auto", "project_id": pid,
            "max_actions": 8, "max_seconds": 600, "max_papers": 2, "max_llm_calls": 9,
        }, timeout=120), "create agent run")["run"]
        last = wait_run(session, base, run["id"])
        budget_used = last.get("budget_used") or {}
        summary["agent"] = {
            "status": last.get("status"), "paper_ids": last.get("paper_ids"),
            "report": last.get("report"), "steps": len(last.get("steps") or []),
            "error": last.get("error"), "budget_used": budget_used,
        }
        check(last.get("status") == "completed",
              f"agent run ended as {last.get('status')}: {last.get('error')}")
        # The nested 精读 model calls belong to this run, so every real call
        # recorded for it must show up exactly once in its budget; cached replies
        # reuse an answer instead of a call. A run that read papers with 0 calls
        # means the accounting is bypassed somewhere.
        audit_calls = payload(session.get(base + "/api/llm/audit?limit=200", timeout=60),
                              "read llm audit").get("calls") or []
        run_calls = [call for call in audit_calls if call.get("run_id") == last.get("id")]
        charged = sum(1 for call in run_calls if call.get("ok") and not call.get("cached"))
        NESTED_PURPOSES = ("read", "translation", "compare", "ask", "translation_full", "transform")
        nested = [call for call in run_calls if call.get("purpose") in NESTED_PURPOSES]
        check(charged == int(budget_used.get("llm_calls") or 0) and bool(nested),
              f"run budget does not match its model calls: {budget_used} "
              f"charged={charged} nested={len(nested)}")
        report_path = Path(str((last.get("report") or {}).get("path") or ""))
        report = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
        check(len(report) >= 500, f"agent report is too short: {len(report)} chars")
        check((last.get("report") or {}).get("mode") == "llm",
              f"研究简报应由模型撰写，实际 mode={(last.get('report') or {}).get('mode')} "
              f"budget={budget_used}")
        # When the model budget really binds, the run has to say so instead of
        # quietly returning rule-extracted results for every remaining action.
        used = int(budget_used.get("llm_calls") or 0)
        cap = int(((last.get("budget") or {}).get("max_llm_calls")) or 0)
        if cap and used >= cap:
            check(any(event.get("type") == "budget" for event in last.get("events") or []),
                  f"预算用尽但没有写明原因：used={used} cap={cap}")
        (work / "report.md").write_text(report, encoding="utf-8")
        summary["report"] = {
            "chars": len(report),
            "mode": (last.get("report") or {}).get("mode"),
            "headings": re.findall(r"^#{1,3} .+$", report, re.M)[:20],
            "legend": re.findall(r"\[P\d+\] = [^；\n]+", report)[:5],
        }
        memory = payload(session.get(
            base + f"/api/projects/{pid}/memory?kind=evidence&limit=50", timeout=60),
            "read evidence").get("items") or []
        papers = payload(session.get(
            base + f"/api/projects/{pid}/memory?kind=papers&limit=50", timeout=60),
            "read papers").get("items") or []
        summary["memory"] = {"evidence": len(memory), "papers": len(papers)}
        check(len(memory) >= 4, f"agent produced too little evidence: {len(memory)}")
        copilot = payload(session.post(base + f"/api/projects/{pid}/assistant", json={
            "message": "这两篇论文的能效和校准开销怎么比较？如果我要写课程论文，可以选什么题目？",
        }, timeout=300), "copilot").get("reply") or {}
        summary["copilot"] = {
            "mode": copilot.get("mode"), "intent": copilot.get("intent"),
            "text": copilot.get("text"), "citations": copilot.get("citations"),
            "grounding": copilot.get("grounding"), "llm": copilot.get("llm"),
        }
        check(copilot.get("mode") == "llm",
              f"copilot did not use the configured model successfully: {copilot.get('mode')}")
        check(bool(copilot.get("citations")), "copilot returned no citations for a paper question")
        topic = payload(session.post(base + "/api/topic", json={
            "topic": "低功耗存内计算 ADC 的读出与校准权衡",
            "scope": "selected", "paper_ids": ids,
        }, timeout=120), "topic")
        questions = topic.get("questions") or []
        check(len(questions) >= 3, f"topic studio returned {len(questions)} questions")
        ranked = sorted(questions, key=lambda item: (
            float(item.get("novelty", 0)) + float(item.get("feasibility", 0))
            + float(item.get("impact", 0))
        ), reverse=True)
        chosen = ranked[0] if ranked else {}
        summary["topic"] = {
            "stats": topic.get("stats"),
            "top_questions": [{
                key: item.get(key) for key in
                ("id", "title", "question", "why_gap", "novelty", "feasibility", "impact")
            } for item in ranked[:3]],
            "chosen": chosen.get("question"), "chosen_id": chosen.get("id"),
        }
        draft = payload(session.post(base + "/api/draft", json={
            "topic": "低功耗存内计算 ADC 的读出与校准权衡",
            "kind": "related_work", "scope": "selected", "paper_ids": ids,
        }, timeout=120), "generate draft")
        summary["generated_draft"] = {
            "title": draft.get("title"), "body": draft.get("body"),
            "references": draft.get("references"),
        }
        check(bool((draft.get("body") or {}).get("sections")), "generated draft has no sections")
        check(bool(draft.get("references")), "generated draft has no references")
        refs = {
            str(item.get("paper_id")): {
                "key": str(item.get("paper_id")), "title": item.get("title"),
                "authors": item.get("authors") or [], "year": item.get("year"),
                "venue": item.get("venue"), "doi": item.get("doi"),
                "url": item.get("url"), "entry_type": "preprint",
            }
            for item in draft.get("references") or []
        }
        sections = []
        body_sections = (draft.get("body") or {}).get("sections") or []
        for section_index, section in enumerate(body_sections[:4], start=1):
            paragraphs = section.get("paragraphs") or []
            if not paragraphs:
                paragraphs = [
                    f"{str(bullet).strip()}。" for bullet in section.get("bullets") or []
                    if str(bullet).strip()
                ]
            citation_keys = []
            references = draft.get("references") or []
            for citation in section.get("citations") or []:
                match = re.fullmatch(r"P(\d+)", str(citation))
                if match:
                    index = int(match.group(1)) - 1
                    if 0 <= index < len(references):
                        citation_keys.append(str(references[index].get("paper_id")))
            blocks = []
            for block_index, paragraph in enumerate(paragraphs[:2], start=1):
                blocks.append({
                    "id": f"blk-{section_index:02d}{block_index:02d}",
                    "type": "paragraph", "status": "draft",
                    "text": str(paragraph), "evidence_ids": [],
                    "citation_keys": citation_keys[:3], "claims": [],
                })
            if blocks:
                sections.append({
                    "id": f"sec-{section_index:03d}",
                    "title": str(section.get("title") or f"章节 {section_index}"),
                    "blocks": blocks,
                })
        if not sections:
            sections = [{
                "id": "sec-001", "title": "相关工作",
                "blocks": [{
                    "id": "blk-001", "type": "paragraph", "status": "draft",
                    "text": "现有本地论文提供了存内计算 ADC 的方法与结果证据，需要进一步核对测试条件与校准开销。",
                    "evidence_ids": [], "citation_keys": ids, "claims": [],
                }],
            }]
        for section in sections:
            for block in section["blocks"]:
                check('{"text"' not in block["text"],
                      f"structured field leaked into draft text: {block['text'][:120]}")
        current = payload(session.get(base + f"/api/projects/{pid}/draft", timeout=60),
                          "read draft")["draft"]
        saved = session.put(base + f"/api/projects/{pid}/draft", json={
            "revision": current["revision"],
            "title": "低功耗存内计算 ADC 的读出与校准权衡：课程论文初稿",
            "citation_style": "gb7714", "sections": sections, "citations": refs,
        }, timeout=60)
        saved_body = payload(saved, "save draft")
        summary["saved_draft"] = {
            "code": saved.status_code,
            "revision": (saved_body.get("draft") or {}).get("revision"),
            "citations": list(refs), "sections": [item["title"] for item in sections],
        }
        check((saved_body.get("draft") or {}).get("revision") == current["revision"] + 1,
              "draft save did not bump the revision")
        check(bool((saved_body.get("draft") or {}).get("citations")),
              "draft save dropped the inline citation map")
        manuscript = session.put(base + f"/api/projects/{pid}/manuscript", json={
            "title": "低功耗存内计算 ADC 的读出与校准权衡：课程论文初稿",
            "authors": [{"name": "测试学生"}],
            "abstract": "本文比较两篇本地论文的存内计算 ADC 读出与校准开销，并给出可核对的课程论文选题。",
            "keywords": ["存内计算", "ADC", "校准", "能效"],
            "citation_style": "gb7714",
        }, timeout=60)
        payload(manuscript, "save manuscript")
        summary["manuscript"] = {"code": manuscript.status_code}
        preflight = payload(session.post(base + f"/api/projects/{pid}/draft/preflight", json={},
                                         timeout=120), "preflight")
        summary["preflight"] = {
            "counts": preflight.get("counts"),
            "blocking": preflight.get("blocking"),
            "references": preflight.get("references"),
            "body_words": preflight.get("body_words"),
            "issues": [
                {"level": item.get("level"), "kind": item.get("kind"),
                 "message": item.get("message")}
                for item in preflight.get("items") or []
            ],
        }
        check(isinstance(preflight.get("counts"), dict), "preflight returned no counts")
        check(preflight.get("blocking") is False,
              f"preflight is blocking: {preflight.get('items')}")
        check(int(preflight.get("references") or 0) == len(refs),
              "preflight lost registered references")
        markdown = session.get(base + f"/api/projects/{pid}/draft/export?format=markdown", timeout=120)
        docx = session.get(base + f"/api/projects/{pid}/draft/export?format=docx", timeout=120)
        pdf = session.get(base + f"/api/projects/{pid}/draft/export?format=pdf", timeout=300)
        for label, response in (("markdown", markdown), ("docx", docx), ("pdf", pdf)):
            ensure_ok(response, f"export {label}")
            check(len(response.content) > 300, f"{label} export is suspiciously small")
        (work / "draft.md").write_bytes(markdown.content)
        (work / "draft.docx").write_bytes(docx.content)
        (work / "draft.pdf").write_bytes(pdf.content)
        summary["exports"] = {
            "markdown": markdown.status_code, "docx": docx.status_code,
            "pdf": pdf.status_code, "pdf_bytes": len(pdf.content),
            "docx_bytes": len(docx.content),
            "markdown_has_title": "课程论文初稿" in markdown.content.decode("utf-8"),
            "markdown_has_reference": "参考文献" in markdown.content.decode("utf-8"),
        }
        check(summary["exports"]["markdown_has_title"], "markdown export lost the draft title")
        check(summary["exports"]["markdown_has_reference"], "markdown export has no reference section")
        archive = session.get(base + f"/api/projects/{pid}/dossier", timeout=120)
        ensure_ok(archive, "export dossier")
        (work / "dossier.zip").write_bytes(archive.content)
        verify = session.post(base + "/api/dossiers/verify",
                              files={"file": ("dossier.zip", archive.content, "application/zip")},
                              timeout=120)
        verify_body = payload(verify, "verify dossier")
        summary["dossier"] = {
            "export": archive.status_code, "verify": verify.status_code,
            "summary": verify_body.get("summary"),
        }
        check(int((verify_body.get("summary") or {}).get("draft_sections") or 0) > 0,
              "dossier has no draft sections")
        (work / "journey.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2)[:14000])
        print("\nARTIFACTS", work)
        return 0
    except Exception as exc:
        summary["failure"] = f"{type(exc).__name__}: {exc}"
        (work / "journey.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("FAILURE", summary["failure"])
        print("ARTIFACTS", work)
        raise
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
