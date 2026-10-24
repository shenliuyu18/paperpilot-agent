"""Fetch a small real-abstract corpus for the four golden-set topics.

Crossref and Europe PMC are used because they are reachable without a key and
return abstracts. OpenAlex/Semantic Scholar are rate-limited from this host and
archive.org-style arXiv access is blocked, so they are not used here.

The output is committed under ``data/eval/golden/corpus/`` so the candidate pool
and the goldens are reproducible without network access. Nothing here labels a
claim; labels are human work (see docs/金标集标注指南.md).
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
from pathlib import Path

import requests
import truststore

truststore.inject_into_ssl()

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "eval" / "golden" / "corpus"
UA = {"User-Agent": "PaperPilot/5.2 (mailto:paperpilot@example.com)"}
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
TITLE_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

TOPICS = {
    "llm-inference-quantization": "large language model inference quantization",
    "rag-pipelines": "retrieval augmented generation",
    "cim-adc": "compute in memory analog to digital converter",
    "recommender-systems": "recommender systems",
}
PAPERS_PER_TOPIC = 6


def _clean(text: str) -> str:
    value = html.unescape(TAG_RE.sub(" ", str(text or "")))
    return SPACE_RE.sub(" ", value).strip()


def _norm_title(title: str) -> str:
    return TITLE_RE.sub("", str(title or "").lower())


def _numeric_sentences(abstract: str) -> int:
    return sum(1 for part in re.split(r"(?<=[.!?。！？])\s+", abstract) if NUMBER_RE.search(part))


def _crossref(query: str, rows: int = 20) -> list[dict]:
    try:
        response = requests.get(
            "https://api.crossref.org/works",
            params={
                "query": query, "rows": rows, "filter": "has-abstract:true",
                "select": "DOI,title,abstract,author,issued,container-title",
            },
            headers=UA, timeout=45,
        )
        response.raise_for_status()
        items = response.json()["message"]["items"]
    except Exception as exc:  # noqa: BLE001 - a source failure is reported, not fatal
        print("crossref failed:", repr(exc)[:160], file=sys.stderr)
        return []
    papers = []
    for item in items:
        title = (item.get("title") or [""])[0]
        abstract = _clean(item.get("abstract") or "")
        if not title or len(abstract) < 300:
            continue
        year = ""
        parts = ((item.get("issued") or {}).get("date-parts") or [[]])[0]
        if parts:
            year = str(parts[0])
        authors = [
            " ".join(filter(None, [author.get("given"), author.get("family")]))
            for author in item.get("author") or []
        ]
        papers.append({
            "id": "cr:" + str(item.get("DOI") or title),
            "title": _clean(title), "abstract": abstract, "year": year,
            "venue": _clean((item.get("container-title") or [""])[0]),
            "authors": [name for name in authors if name],
            "source": "crossref",
        })
    return papers


def _europepmc(query: str, page_size: int = 15) -> list[dict]:
    try:
        response = requests.get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": query, "format": "json", "pageSize": page_size,
                    "resultType": "core"},
            headers=UA, timeout=45,
        )
        response.raise_for_status()
        items = response.json()["resultList"]["result"]
    except Exception as exc:  # noqa: BLE001
        print("europepmc failed:", repr(exc)[:160], file=sys.stderr)
        return []
    papers = []
    for item in items:
        title = _clean(item.get("title") or "")
        abstract = _clean(item.get("abstractText") or "")
        if not title or len(abstract) < 300:
            continue
        papers.append({
            "id": "epmc:" + str(item.get("id") or item.get("pmid") or title),
            "title": title, "abstract": abstract, "year": str(item.get("pubYear") or ""),
            "venue": _clean(item.get("journalTitle") or item.get("bookOrReportDetails")
                            or item.get("source") or ""),
            "authors": _clean(item.get("authorString") or ""),
            "source": "europepmc",
        })
    return papers


def select(papers: list[dict], limit: int = PAPERS_PER_TOPIC) -> list[dict]:
    unique: dict[str, dict] = {}
    for paper in papers:
        key = _norm_title(paper["title"])
        if key and key not in unique:
            unique[key] = paper
    ranked = sorted(
        unique.values(),
        key=lambda paper: (-min(_numeric_sentences(paper["abstract"]), 8),
                           -len(paper["abstract"]), paper["id"]),
    )
    return ranked[:limit]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    all_papers: list[dict] = []
    summary = {}
    for topic, query in TOPICS.items():
        papers = _crossref(query) + _europepmc(query)
        chosen = select(papers)
        payload = {
            "version": 1, "topic": topic, "query": query,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sources": sorted({paper["source"] for paper in chosen}),
            "papers": chosen,
        }
        path = OUT / f"{topic}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        all_papers.extend(chosen)
        summary[topic] = len(chosen)
        print(topic, len(chosen), payload["sources"])
        time.sleep(1)
    (OUT / "all.json").write_text(json.dumps(
        {"version": 1, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
         "papers": all_papers}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if all(count >= PAPERS_PER_TOPIC for count in summary.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
