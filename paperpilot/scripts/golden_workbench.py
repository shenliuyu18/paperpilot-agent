"""Golden-set workbench: candidate pools, validation, double-annotation merge.

The evaluator needs human labels. This tool never invents them:

* ``build`` extracts claim/quote pairs into ``golden/candidates/<topic>.json``
  with ``label: ""`` and ``provenance: human-required``.
* ``check`` validates structure, labels and (when a corpus is available) that
  every quote is a literal substring of the source text.
* ``merge`` combines two independent annotation files and writes a
  disagreement log instead of silently picking a winner.
* ``stats`` reports how far each topic is from the 4×6-paper / ≥200-claim target.

Target (docs/金标集标注指南.md): 4 topics × 6 papers, ≥200 labelled claims,
two independent annotators, disagreements adjudicated by a third pass.

Usage::

    python scripts/golden_workbench.py build
    python scripts/golden_workbench.py check data/eval/golden/candidates/rag-pipelines.json
    python scripts/golden_workbench.py merge a.json b.json --out merged.json
    python scripts/golden_workbench.py stats
"""
from __future__ import annotations

from _console import make_console_safe, make_json_stdout

make_console_safe()

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PAPERPILOT_DATA_DIR", tempfile.mkdtemp(prefix="pp-golden-workbench-"))

from app.services import grounding, project_state, search  # noqa: E402

GOLDEN_DIR = ROOT / "data" / "eval" / "golden"
CANDIDATE_DIR = GOLDEN_DIR / "candidates"
DEFAULT_LABELS = grounding.STANCES
TOPICS = {
    "llm-inference-quantization": ("LLM 推理与量化", (
        "quantiz", "w4a4", "int4", "low-bit", "low bit", "bit-width",
        "inference", "llm", "post-training",
    )),
    "rag-pipelines": ("RAG 流水线", (
        "retrieval-augmented", "retrieval augmented", "rag ", "chunk",
        "embedding", "rerank", "vector database", "passage",
    )),
    "cim-adc": ("存内计算与 ADC", (
        "cim", "compute-in-memory", "compute in memory", "adc", "analog",
        "macro", "energy", "sar",
    )),
    "recommender-systems": ("推荐系统", (
        "recommend", "collaborative filtering", "click-through", "ctr ",
        "ranking model", "user history",
    )),
}
PAPERS_PER_TOPIC = 6
CLAIMS_PER_PAPER = 10
MIN_CLAIMS_TOTAL = 200
MIN_CLAIMS_PER_TOPIC = MIN_CLAIMS_TOTAL // len(TOPICS)
TITLE_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def _now() -> str:
    return project_state._now()


def _norm_title(title: str) -> str:
    return TITLE_RE.sub("", str(title or "").lower())


def _demo_papers() -> list[dict]:
    return [
        {**paper, "abstract": str(paper.get("abstract") or ""),
         "full_text": str(paper.get("full_text") or "")}
        for paper in search.all_papers()
    ]


def _parsed_papers() -> list[dict]:
    papers = []
    for path in sorted((ROOT / "data" / "parsed").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not data.get("title"):
            continue
        sections = [section for section in (data.get("sections") or []) if isinstance(section, dict)]
        papers.append({
            "id": str(data.get("id") or path.stem), "title": str(data["title"]),
            "abstract": " ".join(str(s.get("text") or "") for s in sections
                                 if str(s.get("title") or "").lower() == "abstract"),
            "full_text": "\n".join(str(section.get("text") or "") for section in sections),
            "year": data.get("year") or "", "venue": "",
        })
    return papers


def _external_papers(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("papers") or data.get("items") or []
    if not isinstance(data, list):
        raise ValueError(f"论文清单必须是数组：{path}")
    papers = []
    for raw in data:
        if not isinstance(raw, dict) or not raw.get("title"):
            continue
        papers.append({
            "id": str(raw.get("id") or raw.get("doi") or raw.get("title")),
            "title": str(raw["title"]),
            "abstract": str(raw.get("abstract") or ""),
            "full_text": str(raw.get("full_text") or raw.get("text") or ""),
            "year": raw.get("year") or "", "venue": raw.get("venue") or "",
        })
    return papers


def corpus(source: str = "all", papers_path: Path | None = None) -> list[dict]:
    papers: list[dict] = []
    if source in {"all", "demo"}:
        papers.extend(_demo_papers())
    if source in {"all", "parsed"}:
        papers.extend(_parsed_papers())
    if papers_path:
        papers.extend(_external_papers(papers_path))
    unique: dict[str, dict] = {}
    for paper in papers:
        key = _norm_title(paper.get("title"))
        if not key:
            continue
        current = unique.get(key)
        if current is None or len(str(paper.get("abstract") or "")) > len(str(current.get("abstract") or "")):
            unique[key] = paper
    return sorted(unique.values(), key=lambda item: str(item.get("id")))


def _score(paper: dict, keywords: tuple[str, ...]) -> int:
    blob = " ".join(str(paper.get(key) or "") for key in ("title", "abstract", "full_text")).lower()
    return sum(blob.count(word) for word in keywords)


def _source_text(paper: dict) -> str:
    return " ".join(part for part in (paper.get("abstract"), paper.get("full_text")) if part)


def _candidates(paper: dict, count: int) -> list[tuple[str, str]]:
    text = _source_text(paper)
    sentences = [item.strip() for item in grounding.sentences(text) if len(item.strip()) >= 40]
    numeric = [item for item in sentences if grounding.numbers(item)]
    other = [item for item in sentences if item not in numeric]
    picked = (numeric + other)[:count]
    return [(sentence, "Abstract" if sentence in str(paper.get("abstract") or "") else "Full text")
            for sentence in picked]


def load_corpus_dir(directory: Path) -> dict[str, list[dict]]:
    """Load ``<topic>.json`` files written by ``fetch_golden_corpus.py``."""
    topics: dict[str, list[dict]] = {}
    for path in sorted(Path(directory).glob("*.json")):
        if path.name == "all.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        slug = str(data.get("topic") or path.stem)
        topics[slug] = [paper for paper in (data.get("papers") or [])
                        if isinstance(paper, dict) and paper.get("title")]
    return topics


def build(out_dir: Path, source: str = "all", papers_path: Path | None = None,
          corpus_dir: Path | None = None) -> dict:
    papers = corpus(source, papers_path)
    fixed = load_corpus_dir(corpus_dir) if corpus_dir else {}
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    for slug, (label, keywords) in TOPICS.items():
        if fixed.get(slug):
            ranked = fixed[slug][:PAPERS_PER_TOPIC]
        else:
            ranked = sorted(
                (paper for paper in papers if _score(paper, keywords) > 0),
                key=lambda paper: (-_score(paper, keywords), str(paper.get("id"))),
            )[:PAPERS_PER_TOPIC]
        claims: list[dict] = []
        for paper in ranked:
            for sentence, locator in _candidates(paper, CLAIMS_PER_PAPER):
                claims.append({
                    "id": f"g-{slug}-{len(claims) + 1:03d}",
                    "paper_id": paper.get("id"),
                    "claim": sentence,
                    "evidence_quote": sentence,
                    "locator": locator,
                    "label": "",
                    "annotator": "",
                    "note": "",
                })
        payload = {
            "version": 1,
            "topic": slug,
            "title": label,
            "provenance": "human-required",
            "status": "unlabeled",
            "created_at": _now(),
            "target": {"papers": PAPERS_PER_TOPIC, "claims": MIN_CLAIMS_PER_TOPIC},
            "papers_available": len(ranked),
            "shortfall": {
                "papers": max(0, PAPERS_PER_TOPIC - len(ranked)),
                "claims": max(0, MIN_CLAIMS_PER_TOPIC - len(claims)),
            },
            "claims": claims,
        }
        path = out_dir / f"{slug}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary[slug] = {"papers": len(ranked), "claims": len(claims)}
    return summary


def _load(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("claims"), list):
        raise ValueError(f"不是有效的金标集：{path}")
    return data


def check(path: Path, source: str = "all", strict: bool = False,
          papers_path: Path | None = None, corpus_dir: Path | None = None) -> dict:
    data = _load(path)
    corpus_papers = corpus(source, papers_path)
    if corpus_dir:
        for items in load_corpus_dir(corpus_dir).values():
            corpus_papers.extend(items)
    papers = {str(paper.get("id")): paper for paper in corpus_papers}
    seen: set[str] = set()
    counts = {"claims": 0, "labeled": 0, "unlabeled": 0, "papers": 0}
    problems: list[str] = []
    paper_ids: set[str] = set()
    for item in data.get("claims") or []:
        claim_id = str(item.get("id") or "")
        if not claim_id or claim_id in seen:
            problems.append(f"重复或缺失的 id：{claim_id or '(空)'}")
        seen.add(claim_id)
        label = str(item.get("label") or "")
        if label and label not in DEFAULT_LABELS:
            problems.append(f"{claim_id} 的 label 非法：{label}")
        if not str(item.get("claim") or "").strip():
            problems.append(f"{claim_id} 缺 claim")
        counts["claims"] += 1
        if label:
            counts["labeled"] += 1
            if not str(item.get("annotator") or "").strip():
                problems.append(f"{claim_id} 已标注但缺 annotator")
        else:
            counts["unlabeled"] += 1
        paper_id = str(item.get("paper_id") or "")
        paper_ids.add(paper_id)
        evidence_id = str(item.get("evidence_paper_id") or paper_id)
        source_paper = papers.get(evidence_id)
        if source_paper is not None and item.get("evidence_quote"):
            if str(item["evidence_quote"]) not in _source_text(source_paper):
                problems.append(f"{claim_id} 的引句不是原文子串")
    counts["papers"] = len(paper_ids)
    topic = data.get("topic") or ""
    is_constructed = data.get("provenance") == "constructed-seed"
    target = None if is_constructed else {"papers": PAPERS_PER_TOPIC,
                                          "claims": MIN_CLAIMS_PER_TOPIC}
    shortfall = {}
    if target:
        shortfall = {"papers": max(0, target["papers"] - counts["papers"]),
                     "claims": max(0, target["claims"] - counts["labeled"])}
    report = {"file": str(path), "topic": topic, "counts": counts,
              "target": target, "shortfall": shortfall, "problems": problems}
    if strict and not is_constructed:
        if shortfall.get("papers") or shortfall.get("claims") or problems:
            report["strict_failed"] = True
    return report


def merge(left_path: Path, right_path: Path, out_path: Path,
          annotator_a: str = "", annotator_b: str = "") -> dict:
    left, right = _load(left_path), _load(right_path)
    right_by_id = {str(item.get("id")): item for item in right.get("claims") or []}
    a_name = annotator_a or str(left.get("annotator") or "A")
    b_name = annotator_b or str(right.get("annotator") or "B")
    merged: list[dict] = []
    disagreements: list[dict] = []
    agreed = 0
    for item in left.get("claims") or []:
        claim_id = str(item.get("id") or "")
        other = right_by_id.pop(claim_id, None)
        if other is None:
            raise ValueError(f"两份标注的 id 不一致，缺少：{claim_id}")
        label_a = str(item.get("label") or "")
        label_b = str(other.get("label") or "")
        if not label_a or not label_b:
            raise ValueError(f"{claim_id} 仍有未标注项，无法合并")
        row = {**item, "annotator": a_name, "labels": {a_name: label_a, b_name: label_b}}
        if label_a == label_b:
            row["label"] = label_a
            row.pop("disagreement", None)
            agreed += 1
        else:
            row["label"] = ""
            row["disagreement"] = True
            disagreements.append({
                "id": claim_id, "claim": item.get("claim"),
                "label_a": label_a, "label_b": label_b,
                "note_a": item.get("note") or "", "note_b": other.get("note") or "",
            })
        merged.append(row)
    if right_by_id:
        raise ValueError("两份标注的 id 不一致：右侧存在多余条目")
    total = len(merged)
    payload = {
        "version": 1,
        "topic": left.get("topic") or right.get("topic") or "",
        "provenance": "human",
        "status": "labeled" if not disagreements else "needs-adjudication",
        "created_at": _now(),
        "annotators": [a_name, b_name],
        "agreement": round(agreed / total, 4) if total else None,
        "disagreements": disagreements,
        "claims": merged,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"claims": total, "agreed": agreed,
            "agreement": payload["agreement"], "disagreements": len(disagreements),
            "out": str(out_path)}


def stats(directory: Path) -> list[dict]:
    rows = []
    paths = sorted(directory.glob("*.json"))
    candidate_dir = directory / "candidates"
    if candidate_dir.is_dir():
        paths += sorted(candidate_dir.glob("*.json"))
    for path in paths:
        try:
            report = check(path, strict=False, corpus_dir=directory / "corpus")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            rows.append({"file": str(path), "error": repr(exc)[:120]})
            continue
        rows.append({"file": str(path), "topic": report["topic"],
                     **report["counts"], "shortfall": report["shortfall"],
                     "problems": len(report["problems"])})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="PaperPilot golden-set workbench")
    sub = parser.add_subparsers(dest="command", required=True)

    build_cmd = sub.add_parser("build", help="extract candidate claim/quote pairs")
    build_cmd.add_argument("--out", type=Path, default=CANDIDATE_DIR)
    build_cmd.add_argument("--source", choices=("demo", "parsed", "all"), default="all")
    build_cmd.add_argument("--papers", type=Path, default=None,
                           help="external JSON paper list to extend the corpus")
    build_cmd.add_argument("--corpus-dir", type=Path, default=None,
                           help="topic-keyed corpus written by fetch_golden_corpus.py")

    check_cmd = sub.add_parser("check", help="validate one golden file")
    check_cmd.add_argument("file", type=Path)
    check_cmd.add_argument("--source", choices=("demo", "parsed", "all"), default="all")
    check_cmd.add_argument("--papers", type=Path, default=None)
    check_cmd.add_argument("--corpus-dir", type=Path, default=None)
    check_cmd.add_argument("--strict", action="store_true")

    merge_cmd = sub.add_parser("merge", help="merge two independent annotations")
    merge_cmd.add_argument("left", type=Path)
    merge_cmd.add_argument("right", type=Path)
    merge_cmd.add_argument("--out", type=Path, required=True)
    merge_cmd.add_argument("--annotator-a", default="")
    merge_cmd.add_argument("--annotator-b", default="")

    stats_cmd = sub.add_parser("stats", help="summarise labelled and candidate sets")
    stats_cmd.add_argument("--dir", type=Path, default=GOLDEN_DIR)
    stats_cmd.add_argument("--json", action="store_true")

    args = parser.parse_args()
    if args.command == "build":
        summary = build(args.out, args.source, args.papers, args.corpus_dir)
        make_json_stdout()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.command == "check":
        report = check(args.file, args.source, args.strict, args.papers, args.corpus_dir)
        make_json_stdout()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report.get("strict_failed") else 0
    if args.command == "merge":
        result = merge(args.left, args.right, args.out, args.annotator_a, args.annotator_b)
        make_json_stdout()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    rows = stats(args.dir)
    if args.json:
        make_json_stdout()
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            if "error" in row:
                print(f"{Path(row['file']).name}: ERROR {row['error']}")
                continue
            print(f"{Path(row['file']).name:<36} topic={row['topic']:<28} "
                  f"papers={row['papers']:>2} claims={row['claims']:>3} "
                  f"labeled={row['labeled']:>3} shortfall={row['shortfall']} "
                  f"problems={row['problems']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
