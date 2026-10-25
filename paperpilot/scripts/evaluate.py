from __future__ import annotations

from _console import make_console_safe

make_console_safe()

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import DATA_DIR  # noqa: E402
from app.demo_data import GLOSSARY  # noqa: E402
from app.services import analysis, ask, library, pdf, research, search  # noqa: E402


def _paper_text(paper: dict, fields: list[str]) -> str:
    chunks = []
    for field in fields:
        value = paper.get(field)
        if value:
            chunks.append(json.dumps(value, ensure_ascii=False))
    return " ".join(chunks).lower()


def _run_case(case: dict) -> tuple[bool, str]:
    kind = case["type"]
    if kind == "search_contains":
        top_n = case.get("top_n", 5)
        results = search.search_demo(case["query"], 5)[:top_n]
        ids = [paper["id"] for paper in results]
        hit = any(paper_id in ids for paper_id in case["any_of"])
        return hit, f"top{top_n}={ids}"
    if kind == "paper_text_contains":
        paper = search.get_paper(case["paper_id"])
        text = _paper_text(paper, case["fields"]).lower()
        missing = [kw for kw in case["keywords"] if kw.lower() not in text]
        return not missing, "missing=" + ",".join(missing) if missing else "all keywords present"
    if kind == "evidence_min":
        paper = search.get_paper(case["paper_id"])
        count = len(paper.get("evidence", []))
        return count >= case["minimum"], f"evidence={count}"
    if kind == "compare_warning":
        papers = [search.get_paper(pid) for pid in case["paper_ids"]]
        data = analysis.compare(papers)
        text = " ".join(data.get("warnings", []))
        return "不可直接比较" in text, f"warnings={len(data.get('warnings', []))}"
    if kind == "compare_rows":
        papers = [search.get_paper(pid) for pid in case["paper_ids"]]
        rows = analysis.compare(papers).get("rows", [])
        return len(rows) == case["expected"], f"rows={len(rows)}"
    if kind == "glossary":
        actual = GLOSSARY.get(case["term"])
        return actual == case["zh"], f"actual={actual}"
    if kind == "transform_contains":
        paper = search.get_paper(case["paper_id"])
        data = analysis.transform(paper)
        text = json.dumps(data.get("repro", {}), ensure_ascii=False) + " ".join(data.get("slides", []))
        missing = [kw for kw in case["keywords"] if kw not in text]
        return not missing, "missing=" + ",".join(missing) if missing else "all keywords present"
    if kind == "transform_code_contains":
        paper = search.get_paper(case["paper_id"])
        code = analysis.transform(paper).get("code_scaffold", "")
        hit = any(kw in code for kw in case["keywords"])
        return hit, f"code_chars={len(code)}"
    if kind == "gap_contains":
        data = analysis.gaps(case["topic"])
        text = json.dumps(data.get("gaps", []), ensure_ascii=False)
        missing = [kw for kw in case["keywords"] if kw not in text]
        return not missing, "missing=" + ",".join(missing) if missing else "all keywords present"
    if kind == "ask_contains":
        data = ask.answer(case["question"], case.get("scope", "all"), case.get("paper_ids", []), case.get("top_k", 6))
        ids = [item["paper_id"] for item in data["citations"]]
        hit = any(paper_id in ids for paper_id in case["any_of"])
        if case.get("require_location"):
            hit = hit and all(item.get("section") and item.get("page") for item in data["citations"])
        if case.get("require_field"):
            hit = hit and any(item.get("field") == case["require_field"] for item in data["citations"])
        return hit, f"citations={ids[:5]}"
    if kind == "ask_coverage":
        data = ask.answer(case["question"], case.get("scope", "all"))
        unique = len({item["paper_id"] for item in data["citations"]})
        expected = case.get("expected_unique", 3)
        return unique >= expected and data["matched_papers"] >= expected, (
            f"unique={unique}, matched={data['matched_papers']}/{data['papers_considered']}"
        )
    if kind == "ask_no_evidence":
        data = ask.answer(case["question"], case.get("scope", "all"))
        text = data.get("answer", "")
        refused = any(key in text for key in ("没有检索到", "证据不足", "没有找到"))
        return len(data["citations"]) == 0 and refused, f"citations={len(data['citations'])}"
    if kind == "library_roundtrip":
        old = os.environ.get("PAPERPILOT_LIBRARY_FILE")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["PAPERPILOT_LIBRARY_FILE"] = str(Path(tmp) / "library.json")
            try:
                paper = search.get_paper(case["paper_id"])
                library.save(paper)
                library.update(
                    case["paper_id"],
                    status=case["status"],
                    tags=case["tags"],
                    note=case["note"],
                )
                items = library.list_items(case["query"])
                markdown = library.export_markdown()
                removed = library.remove(case["paper_id"])
                passed = (
                    len(items) == 1
                    and items[0]["status"] == case["status"]
                    and items[0]["tags"] == case["tags"]
                    and case["note"] in markdown
                    and removed
                    and library.list_items() == []
                )
                return passed, f"items={len(items)}, removed={removed}"
            finally:
                if old is None:
                    os.environ.pop("PAPERPILOT_LIBRARY_FILE", None)
                else:
                    os.environ["PAPERPILOT_LIBRARY_FILE"] = old
    if kind == "topic_studio":
        data = research.topic_studio(case["topic"])
        stages = data.get("reading_path", [])
        passed = (
            len(data.get("clusters", [])) >= case.get("clusters", 3)
            and len(data.get("questions", [])) >= case.get("questions", 3)
            and len(stages) == 3
            and all(stage.get("papers") for stage in stages)
            and bool(data.get("experiment_plan", {}).get("datasets"))
            and bool(data.get("brief", {}).get("thesis"))
        )
        return passed, (
            f"clusters={len(data.get('clusters', []))}, questions={len(data.get('questions', []))}, "
            f"stages={[(stage['label'], len(stage['papers'])) for stage in stages]}"
        )
    if kind == "draft_contains":
        data = research.draft(case["topic"], case.get("kind", "related_work"))
        text = data.get("markdown", "")
        missing = [kw for kw in case["keywords"] if kw not in text]
        passed = not missing and len(data.get("references", [])) == case.get("expected_refs", 5)
        return passed, "missing=" + ",".join(missing) if missing else f"refs={len(data.get('references', []))}"
    if kind == "check_case":
        data = research.check_claims(case["text"])
        summary = data["summary"]
        missing_text = " ".join(
            quantity
            for item in data["results"]
            for quantity in item.get("missing_quantities", [])
        )
        passed = (
            summary["supported"] >= case.get("supported_min", 0)
            and summary["unsupported"] >= case.get("unsupported_min", 0)
            and summary["unsupported"] <= case.get("unsupported_max", 99)
            and all(value in missing_text for value in case.get("missing_quantities", []))
        )
        return passed, f"summary={summary}, missing={missing_text[:100]}"
    if kind == "ask_support":
        data = ask.answer(case["question"], case.get("scope", "all"))
        direct = data["support_summary"]["direct"]
        expected = case.get("expected_direct", 3)
        return direct >= expected, f"direct={direct}, summary={data['support_summary']}"
    if kind == "ask_quantities":
        data = ask.answer(case["question"], case.get("scope", "all"))
        expected = case.get("minimum", 1)
        return data["quantity_count"] >= expected, f"quantities={data['quantity_count']}"
    if kind == "crossref_parse":
        payload = json.loads((ROOT / "data" / "cache" / "crossref_fixture.json").read_text(encoding="utf-8"))
        papers = search._parse_crossref_items(payload.get("items") or [])
        with_doi = [paper for paper in papers if paper.get("doi") and paper.get("year")]
        return len(papers) >= 5 and len(with_doi) >= 5, f"papers={len(papers)}, with_doi={len(with_doi)}"
    if kind == "pdf_enrichment":
        data = (ROOT / "data" / "demo_pdfs" / case["file"]).read_bytes()
        paper = pdf.parse_pdf_bytes(data, case["file"])
        metrics = paper.get("metrics", {})
        passed = (
            len(paper.get("authors", [])) >= case.get("authors", 3)
            and paper.get("year") == case.get("year")
            and "nm" in str(metrics.get("工艺/验证", ""))
            and len(paper.get("figures", [])) >= case.get("figures", 1)
            and len(paper.get("results", [])) >= case.get("results", 1)
            and all(item.get("page") for item in paper.get("evidence", []))
        )
        return passed, (
            f"authors={len(paper.get('authors', []))}, figures={len(paper.get('figures', []))}, "
            f"results={len(paper.get('results', []))}, metrics={metrics.get('工艺/验证')}"
        )
    if kind == "pdf_limitations":
        data = (ROOT / "data" / "demo_pdfs" / case["file"]).read_bytes()
        paper = pdf.parse_pdf_bytes(data, case["file"])
        text = " ".join(paper.get("limitations", []))
        missing = [kw for kw in case["keywords"] if kw not in text]
        return bool(paper.get("limitations")) and not missing, (
            "missing=" + ",".join(missing) if missing else f"limitations={len(paper['limitations'])}"
        )
    if kind == "library_json_roundtrip":
        old = os.environ.get("PAPERPILOT_LIBRARY_FILE")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.json"
            target = Path(tmp) / "target.json"
            try:
                os.environ["PAPERPILOT_LIBRARY_FILE"] = str(source)
                library.save(search.get_paper(case["paper_id"]))
                library.update(case["paper_id"], note=case["note"], tags=case["tags"], status="reading")
                payload = library.export_json()
                os.environ["PAPERPILOT_LIBRARY_FILE"] = str(target)
                result = library.import_items(payload)
                items = library.list_items()
                bibtex = library.export_bibtex()
                passed = (
                    result["imported"] == 1
                    and items[0]["note"] == case["note"]
                    and items[0]["tags"] == case["tags"]
                    and "@article{" in bibtex
                )
                return passed, f"imported={result['imported']}, bibtex={'@article{' in bibtex}"
            finally:
                if old is None:
                    os.environ.pop("PAPERPILOT_LIBRARY_FILE", None)
                else:
                    os.environ["PAPERPILOT_LIBRARY_FILE"] = old
    if kind == "library_ask":
        old = os.environ.get("PAPERPILOT_LIBRARY_FILE")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["PAPERPILOT_LIBRARY_FILE"] = str(Path(tmp) / "library.json")
            try:
                library.save(search.get_paper(case["paper_id"]))
                data = ask.answer(case["question"], "library")
                ids = [item["paper_id"] for item in data["citations"]]
                return case["paper_id"] in ids, f"library_citations={ids[:4]}"
            finally:
                if old is None:
                    os.environ.pop("PAPERPILOT_LIBRARY_FILE", None)
                else:
                    os.environ["PAPERPILOT_LIBRARY_FILE"] = old
    return False, f"unknown case type: {kind}"


def main() -> None:
    cases = json.loads((ROOT / "data" / "eval_set.json").read_text(encoding="utf-8"))
    results = []
    for case in cases:
        try:
            passed, detail = _run_case(case)
        except Exception as exc:  # noqa: BLE001
            passed, detail = False, f"error: {exc}"
        results.append({**case, "passed": bool(passed), "detail": detail})
        print(f"[{'PASS' if passed else 'FAIL'}] {case['id']} {case['check']} :: {detail}")

    passed = sum(1 for item in results if item["passed"])
    summary = {
        "total": len(results),
        "passed": passed,
        "accuracy": round(passed / max(len(results), 1) * 100, 1),
        "run_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "cases": [{"id": item["id"], "passed": item["passed"], "detail": item["detail"]} for item in results],
        "note": "内置流程回归集：检查检索（含 Crossref）、抽取（含 PDF）、证据、对比、翻译、转化、空白、证据问答、支持度矩阵与研究库备份等链路是否按预期工作。",
    }
    out = DATA_DIR / "eval_results.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{passed}/{len(results)} = {summary['accuracy']}% -> {out}")


if __name__ == "__main__":
    main()
