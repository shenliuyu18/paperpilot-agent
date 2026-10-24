from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PDF_DIR = ROOT.parent / ".bench" / "real_papers"
HEADERS = {"User-Agent": "PaperPilot-real-world-check/1.0"}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_health(base: str, process: subprocess.Popen, timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"server exited early:\n{output[-4000:]}")
        try:
            response = requests.get(base + "/api/health", timeout=2)
            if response.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.3)
    raise RuntimeError("server did not become healthy in time")


def _start_server(pdf_dir: Path) -> tuple[str, subprocess.Popen, Path, str]:
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    data_dir = Path(tempfile.mkdtemp(prefix="paperpilot-real-"))
    env = os.environ.copy()
    env["PAPERPILOT_DATA_DIR"] = str(data_dir)
    env["PAPERPILOT_LLM_API_KEY"] = ""
    env["OPENAI_API_KEY"] = ""
    env["PYTHONIOENCODING"] = "utf-8"
    log_path = data_dir / "server.log"
    log = log_path.open("w", encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _wait_health(base, process)
    return base, process, data_dir, str(log_path)


def _stop_server(process: subprocess.Popen, data_dir: Path, keep: bool) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    if not keep:
        shutil.rmtree(data_dir, ignore_errors=True)


def _post(session: requests.Session, base: str, path: str, payload: dict, timeout: int = 120) -> tuple[dict, float]:
    started = time.perf_counter()
    response = session.post(base + path, json=payload, timeout=timeout)
    elapsed = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    return response.json(), elapsed


def _get(session: requests.Session, base: str, path: str, timeout: int = 60) -> tuple[dict, float]:
    started = time.perf_counter()
    response = session.get(base + path, timeout=timeout)
    elapsed = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    return response.json(), elapsed


def _upload(session: requests.Session, base: str, path: Path) -> dict:
    data = path.read_bytes()
    started = time.perf_counter()
    response = session.post(
        base + "/api/upload",
        files={"file": (path.name, data, "application/pdf")},
        timeout=180,
    )
    elapsed = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    result = response.json()
    result["upload_ms"] = round(elapsed, 1)
    result["bytes"] = len(data)
    return result


def _search_case(session, base, name, source, query, quality_terms, min_quality_hits=1) -> dict:
    payload = {"query": query, "limit": 5, "source": source}
    try:
        data, first_ms = _post(session, base, "/api/search", payload)
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "source": source,
            "query": query,
            "ok": False,
            "error": str(exc),
            "first_ms": None,
            "second_ms": None,
            "count": 0,
            "titles": [],
            "quality_hits": 0,
            "quality_hit": False,
            "cached": False,
        }
    try:
        repeat, second_ms = _post(session, base, "/api/search", payload)
    except Exception:  # noqa: BLE001
        repeat, second_ms = {"papers_full": []}, None
    papers = data.get("papers_full") or data.get("papers") or []
    titles = [paper.get("title") or "" for paper in papers]
    text = " ".join(
        [
            " ".join(titles),
            " ".join((paper.get("abstract") or "")[:500] for paper in papers),
        ]
    ).lower()
    hits = sum(1 for term in quality_terms if term.lower() in text)
    cached = any(bool(paper.get("cached")) for paper in (repeat.get("papers_full") or repeat.get("papers") or []))
    result = {
        "name": name,
        "source": source,
        "query": query,
        "ok": bool(papers),
        "error": "",
        "first_ms": round(first_ms, 1),
        "second_ms": round(second_ms, 1) if second_ms is not None else None,
        "count": len(papers),
        "titles": titles[:5],
        "quality_terms": quality_terms,
        "quality_hits": hits,
        "quality_hit": hits >= min_quality_hits,
        "cached": cached,
    }
    return result


def _ask_quality_ok(data: dict, *, min_citations: int = 4, min_unique: int = 3, min_coverage: int = 2) -> tuple[bool, str]:
    citations = data.get("citations") or []
    unique = len({item.get("paper_id") for item in citations})
    located = all(item.get("section") and item.get("page") for item in citations)
    matched = int((data.get("support_summary") or {}).get("direct", 0)) + int(
        (data.get("support_summary") or {}).get("partial", 0)
    )
    passed = len(citations) >= min_citations and unique >= min_unique and located and matched >= min_coverage
    return passed, f"citations={len(citations)}, unique={unique}, located={located}, matched={matched}"


def _ask_case(session, base, name, question, paper_ids, *, min_citations=4, min_unique=3, expect_none=False) -> dict:
    payload = {
        "question": question,
        "scope": "selected",
        "paper_ids": paper_ids,
        "top_k": 8,
    }
    try:
        data, elapsed = _post(session, base, "/api/ask", payload)
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "question": question, "ok": False, "error": str(exc), "elapsed_ms": None}
    citations = data.get("citations") or []
    unique = len({item.get("paper_id") for item in citations})
    if expect_none:
        passed = len(citations) == 0
        detail = f"citations={len(citations)}, answer={data.get('answer', '')[:90]}"
    else:
        passed, detail = _ask_quality_ok(data, min_citations=min_citations, min_unique=min_unique)
    text = " ".join(
        [
            " ".join(item.get("title") or "" for item in citations),
            " ".join(item.get("quote") or "" for item in citations),
        ]
    ).lower()
    return {
        "name": name,
        "question": question,
        "ok": passed,
        "detail": detail,
        "elapsed_ms": round(elapsed, 1),
        "citations": len(citations),
        "unique_papers": unique,
        "matched_papers": data.get("matched_papers"),
        "papers_considered": data.get("papers_considered"),
        "passages_searched": data.get("passages_searched"),
        "quantity_count": data.get("quantity_count"),
        "support": data.get("support_summary"),
        "confidence": data.get("confidence", {}).get("label"),
        "plan": data.get("plan"),
        "answer": data.get("answer", "")[:500],
        "sample_sections": [
            {
                "title": item.get("title"),
                "section": item.get("section"),
                "page": item.get("page"),
                "quote": (item.get("quote") or "")[:180],
            }
            for item in citations[:4]
        ],
        "text": text,
    }


def _concurrency_case(session, base, questions, paper_ids) -> dict:
    payloads = [
        {"question": question, "scope": "selected", "paper_ids": paper_ids, "top_k": 6}
        for question in questions
    ]

    def run(payload):
        started = time.perf_counter()
        try:
            response = session.post(base + "/api/ask", json=payload, timeout=60)
            ok = response.status_code == 200
            count = len((response.json().get("citations") or [])) if ok else 0
        except Exception:  # noqa: BLE001
            ok, count = False, 0
        return (time.perf_counter() - started) * 1000, ok, count

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, payloads))
    total_ms = (time.perf_counter() - started) * 1000
    latencies = [item[0] for item in results]
    return {
        "requests": len(results),
        "failures": sum(1 for item in results if not item[1]),
        "total_ms": round(total_ms, 1),
        "p50_ms": round(statistics.median(latencies), 1),
        "max_ms": round(max(latencies), 1),
        "all_have_citations": all(item[2] > 0 for item in results),
    }


def _upload_quality(paper: dict) -> dict:
    title = paper.get("title") or ""
    authors = paper.get("authors") or []
    sections = paper.get("sections") or []
    limitations = paper.get("limitations") or []
    results = paper.get("results") or []
    author_artifact = bool(re.search(r"[∗†‡§λ∓]|\b(?:University|Department|Institute|Laboratory)\b", title))
    return {
        "title": title,
        "title_chars": len(title),
        "author_artifact_in_title": author_artifact,
        "authors": authors[:6],
        "author_count": len(authors),
        "year": paper.get("year"),
        "sections": len(sections),
        "section_titles": [item.get("title") for item in sections[:8]],
        "figures": len(paper.get("figures") or []),
        "results": len(results),
        "limitations": len(limitations),
        "pages": (paper.get("metrics") or {}).get("页数"),
        "chars": (paper.get("metrics") or {}).get("字符数"),
        "quality_ok": len(sections) >= 4 and not author_artifact and len(authors) > 0,
    }


def _check_case(session, base, paper_ids) -> dict:
    text = (
        "GPTQ 将 175B 参数模型量化到 3-bit 或 4-bit，并在约四个 GPU 小时内完成。"
        "AWQ 将 0.1% 的显著权重保留为 FP16，以降低量化误差。"
        "所有方法都在真实手机上实现了 1000 倍加速。"
    )
    started = time.perf_counter()
    data, elapsed = _post(
        session,
        base,
        "/api/check",
        {"text": text, "scope": "selected", "paper_ids": paper_ids},
    )
    results = data.get("results") or []
    missing_text = " ".join(
        quantity for item in results for quantity in item.get("missing_quantities", [])
    )
    supported = (data.get("summary") or {}).get("supported", 0) + (data.get("summary") or {}).get("partial", 0)
    passed = len(results) >= 3 and supported >= 1 and ("1000" in missing_text or "1000 倍" in missing_text)
    return {
        "name": "claim_check",
        "ok": passed,
        "elapsed_ms": round(elapsed, 1),
        "total_ms": round((time.perf_counter() - started) * 1000, 1),
        "summary": data.get("summary"),
        "missing": missing_text,
        "sentences": [
            {
                "sentence": item.get("sentence"),
                "label": item.get("label"),
                "overlap": item.get("overlap"),
                "missing": item.get("missing_quantities"),
                "citation": (item.get("citation") or {}).get("title"),
            }
            for item in results
        ],
    }


def _topic_case(session, base, paper_ids) -> dict:
    data, elapsed = _post(
        session,
        base,
        "/api/topic",
        {"topic": "大模型量化推理", "scope": "selected", "paper_ids": paper_ids},
    )
    stages = data.get("reading_path") or []
    passed = (
        len(data.get("clusters") or []) >= 2
        and len(data.get("questions") or []) >= 3
        and len(stages) == 3
        and all(stage.get("papers") for stage in stages)
    )
    return {
        "name": "topic_studio",
        "ok": passed,
        "elapsed_ms": round(elapsed, 1),
        "clusters": len(data.get("clusters") or []),
        "questions": len(data.get("questions") or []),
        "stages": [(stage.get("label"), len(stage.get("papers") or [])) for stage in stages],
        "datasets": (data.get("experiment_plan") or {}).get("datasets"),
        "brief": (data.get("brief") or {}).get("thesis"),
    }


def _draft_case(session, base, paper_ids) -> dict:
    data, elapsed = _post(
        session,
        base,
        "/api/draft",
        {
            "topic": "大模型量化推理",
            "kind": "related_work",
            "scope": "selected",
            "paper_ids": paper_ids,
        },
    )
    markdown = data.get("markdown") or ""
    references = data.get("references") or []
    passed = len(references) == len(paper_ids) and len(markdown) >= 500 and "[P1]" in markdown
    return {
        "name": "draft",
        "ok": passed,
        "elapsed_ms": round(elapsed, 1),
        "references": len(references),
        "markdown_chars": len(markdown),
        "citation_keys": [item.get("cite") for item in references],
    }


def _render_markdown(result: dict) -> str:
    lines = [
        "# PaperPilot 真机能力检查",
        "",
        f"- 时间：{result['run_at']}",
        f"- 版本：{result['environment'].get('version')}，模式：{result['environment'].get('mode')}",
        f"- 实测论文：{len(result['uploads'])} 篇开放获取 arXiv PDF",
        f"- 自动检查通过：{result['summary']['passed']}/{result['summary']['total']}",
        "",
        "## 在线检索",
        "",
        "| 用例 | 来源 | 查询 | 首轮耗时 | 复跑耗时 | 数量 | 相关命中 | 结论 |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for item in result["search"]:
        lines.append(
            f"| {item['name']} | {item['source']} | {item['query']} | "
            f"{item.get('first_ms') or '—'} ms | {item.get('second_ms') or '—'} ms | "
            f"{item.get('count', 0)} | {item.get('quality_hits', 0)} | "
            f"{'通过' if item.get('ok') and item.get('quality_hit') else '未通过'} |"
        )
    lines.extend(
        [
            "",
            "## PDF 解析",
            "",
            "| 文件 | 上传+解析 | 页数 | 字符 | 章节 | 图表 | 数值 | 局限 | 作者 | 标题问题 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in result["uploads"]:
        quality = item["quality"]
        lines.append(
            f"| {item['file']} | {item['upload_ms']} ms | {quality.get('pages')} | {quality.get('chars')} | "
            f"{quality.get('sections')} | {quality.get('figures')} | {quality.get('results')} | "
            f"{quality.get('limitations')} | {quality.get('author_count')} | "
            f"{'标题混入作者/单位' if quality.get('author_artifact_in_title') else '正常'} |"
        )
    lines.extend(
        [
            "",
            "## 任务能力",
            "",
            "| 用例 | 耗时 | 结论 | 说明 |",
            "|---|---:|---|---|",
        ]
    )
    for item in result["tasks"]:
        lines.append(
            f"| {item['name']} | {item.get('elapsed_ms', '—')} ms | "
            f"{'通过' if item.get('ok') else '未通过'} | {item.get('detail') or item.get('summary') or ''} |"
        )
    concurrency = result.get("concurrency") or {}
    lines.extend(
        [
            "",
            "## 并发",
            "",
            f"- {concurrency.get('requests', 0)} 个并发问答请求，失败 {concurrency.get('failures', 0)} 个；"
            f"总耗时 {concurrency.get('total_ms', '—')} ms，P50 {concurrency.get('p50_ms', '—')} ms，"
            f"最大 {concurrency.get('max_ms', '—')} ms。",
            "",
            "> 本报告由 `scripts/real_world_check.py` 自动生成。检索相关命中按查询概念词统计，"
            "只用于发现明显退化，不等同于人工相关性评分。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run PaperPilot against real open-access papers.")
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR)
    parser.add_argument("--out", type=Path, default=ROOT.parent / ".bench" / "real_world_result.json")
    parser.add_argument("--markdown", type=Path, default=None)
    parser.add_argument("--base-url", default="")
    parser.add_argument("--keep-data", action="store_true")
    args = parser.parse_args()

    pdfs = sorted(args.pdf_dir.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"No PDFs found in {args.pdf_dir}")
    process = None
    data_dir = None
    if args.base_url:
        base = args.base_url.rstrip("/")
    else:
        base, process, data_dir, log_path = _start_server(args.pdf_dir)
        print(f"server: {base} (log: {log_path})", flush=True)

    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        health, health_ms = _get(session, base, "/api/health")
        print(f"health {health} in {health_ms:.1f} ms", flush=True)

        uploads = []
        for path in pdfs:
            print(f"upload {path.name} ...", flush=True)
            try:
                uploaded = _upload(session, base, path)
                detail, _ = _get(session, base, f"/api/paper/{uploaded['paper_id']}")
                quality = _upload_quality(detail)
                uploads.append(
                    {
                        "file": path.name,
                        "paper_id": uploaded["paper_id"],
                        "upload_ms": uploaded["upload_ms"],
                        "bytes": uploaded["bytes"],
                        "quality": quality,
                    }
                )
                print(
                    f"  {uploaded['title'][:70]} | {uploaded['pages']}p | "
                    f"{quality['sections']} sections | {uploaded['upload_ms']} ms",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                uploads.append({"file": path.name, "error": str(exc), "quality": {}})
                print(f"  ERROR {exc}", flush=True)

        paper_ids = [item["paper_id"] for item in uploads if item.get("paper_id")]
        if not paper_ids:
            raise SystemExit("no paper uploaded successfully")

        search = []
        search_specs = [
            (
                "中文关键词-arXiv",
                "arxiv",
                "高效大模型量化推理",
                ["quantiz", "language model", "llm", "inference", "compress"],
                2,
            ),
            (
                "英文自然语言-arXiv",
                "arxiv",
                "efficient LLM inference quantization",
                ["quantiz", "language model", "llm", "inference", "compress"],
                2,
            ),
            (
                "中文关键词-Crossref",
                "crossref",
                "高效大模型量化推理",
                ["quantiz", "language model", "llm", "inference", "compress"],
                2,
            ),
            (
                "英文自然语言-Crossref",
                "crossref",
                "efficient LLM inference quantization",
                ["quantiz", "language model", "llm", "inference", "compress"],
                2,
            ),
        ]
        for name, source, query, terms, minimum in search_specs:
            print(f"search {name} ...", flush=True)
            item = _search_case(session, base, name, source, query, terms, minimum)
            search.append(item)
            print(
                f"  {item.get('count')} results in {item.get('first_ms')} ms "
                f"(repeat {item.get('second_ms')} ms, qualities {item.get('quality_hits')})",
                flush=True,
            )

        questions = [
            (
                "中文跨语言-核心问题",
                "这些论文分别解决什么问题？核心方法是什么？",
                {"min_citations": 5, "min_unique": 4},
                False,
            ),
            (
                "中文-局限",
                "这些工作的局限性是什么？",
                {"min_citations": 3, "min_unique": 2},
                False,
            ),
            (
                "中文-定量结果",
                "这些方法在速度、内存或精度上的定量结果分别是什么？",
                {"min_citations": 3, "min_unique": 2},
                False,
            ),
            (
                "英文-局限",
                "What are the limitations of these methods?",
                {"min_citations": 3, "min_unique": 2},
                False,
            ),
            (
                "越界问题拒答",
                "这些论文对烹饪技术有什么建议？",
                {},
                True,
            ),
        ]
        asks = []
        for name, question, kwargs, expect_none in questions:
            print(f"ask {name} ...", flush=True)
            item = _ask_case(
                session,
                base,
                name,
                question,
                paper_ids,
                expect_none=expect_none,
                **kwargs,
            )
            asks.append(item)
            print(f"  ok={item.get('ok')} {item.get('detail', item.get('error'))}", flush=True)

        print("topic ...", flush=True)
        topic = _topic_case(session, base, paper_ids)
        print(f"  ok={topic['ok']} clusters={topic['clusters']} questions={topic['questions']}", flush=True)
        print("draft ...", flush=True)
        draft = _draft_case(session, base, paper_ids)
        print(f"  ok={draft['ok']} refs={draft['references']} chars={draft['markdown_chars']}", flush=True)
        print("claim check ...", flush=True)
        check = _check_case(session, base, paper_ids)
        print(f"  ok={check['ok']} missing={check['missing'][:80]}", flush=True)

        concurrency = _concurrency_case(
            session,
            base,
            [
                "这些论文的核心方法是什么？",
                "这些工作的局限是什么？",
                "这些方法有哪些定量结果？",
                "这些论文解决什么问题？",
                "这些工作如何权衡精度与速度？",
                "这些论文的主要贡献是什么？",
            ],
            paper_ids,
        )
        print(f"concurrency: {concurrency}", flush=True)

        upload_checks = sum(
            1
            for item in uploads
            if item.get("quality", {}).get("quality_ok")
        )
        tasks = []
        for item in asks:
            tasks.append(
                {
                    "name": item["name"],
                    "ok": bool(item.get("ok")),
                    "elapsed_ms": item.get("elapsed_ms"),
                    "detail": item.get("detail"),
                }
            )
        tasks.append(
            {
                "name": "PDF 结构抽取",
                "ok": upload_checks >= max(1, len(uploads) - 1),
                "elapsed_ms": round(statistics.mean(item["upload_ms"] for item in uploads if "upload_ms" in item), 1)
                if uploads
                else None,
                "detail": f"{upload_checks}/{len(uploads)} 篇满足章节/作者/标题质量门槛",
            }
        )
        tasks.append(
            {
                "name": topic["name"],
                "ok": topic["ok"],
                "elapsed_ms": topic["elapsed_ms"],
                "detail": f"clusters={topic['clusters']}, questions={topic['questions']}, stages={topic['stages']}",
            }
        )
        tasks.append(
            {
                "name": draft["name"],
                "ok": draft["ok"],
                "elapsed_ms": draft["elapsed_ms"],
                "detail": f"references={draft['references']}, markdown_chars={draft['markdown_chars']}",
            }
        )
        tasks.append(
            {
                "name": check["name"],
                "ok": check["ok"],
                "elapsed_ms": check["elapsed_ms"],
                "detail": f"summary={check['summary']}, missing={check['missing'][:80]}",
            }
        )
        search_checks = sum(
            1 for item in search if item.get("ok") and item.get("quality_hit")
        )
        tasks.append(
            {
                "name": "在线检索相关性",
                "ok": search_checks >= 3,
                "elapsed_ms": round(statistics.mean(item["first_ms"] for item in search if item.get("first_ms")), 1)
                if search
                else None,
                "detail": f"{search_checks}/{len(search)} 条检索达到概念词门槛",
            }
        )

        result = {
            "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "environment": {
                **health,
                "data_dir": str(data_dir) if data_dir else "",
                "pdf_count": len(uploads),
            },
            "uploads": uploads,
            "search": search,
            "asks": asks,
            "topic": topic,
            "draft": draft,
            "check": check,
            "concurrency": concurrency,
            "tasks": tasks,
            "summary": {
                "passed": sum(1 for item in tasks if item["ok"]),
                "total": len(tasks),
                "search_passed": search_checks,
                "search_total": len(search),
                "upload_passed": upload_checks,
                "upload_total": len(uploads),
                "ask_passed": sum(1 for item in asks if item.get("ok")),
                "ask_total": len(asks),
            },
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown = _render_markdown(result)
        markdown_path = args.markdown or args.out.with_suffix(".md")
        markdown_path.write_text(markdown, encoding="utf-8")
        print(f"\nwritten: {args.out}\nwritten: {markdown_path}", flush=True)
        print(markdown, flush=True)
    finally:
        session.close()
        if process is not None and data_dir is not None:
            _stop_server(process, data_dir, args.keep_data)


if __name__ == "__main__":
    main()
