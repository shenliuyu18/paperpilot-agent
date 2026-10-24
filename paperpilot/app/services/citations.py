from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from .. import config

OPEN_CITATIONS = "https://opencitations.net/index/api/v1"
CROSSREF_WORK = "https://api.crossref.org/works/"
CACHE_TTL = 7 * 24 * 3600
MIN_METADATA_FETCH = 8


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "paper")[:120]


def _cache_root(cache_dir: Path | None) -> Path:
    root = cache_dir or Path(os.getenv("PAPERPILOT_CITATION_DIR") or (config.DATA_DIR / "citations"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cache_path(cache_dir: Path | None, doi: str) -> Path:
    return _cache_root(cache_dir) / f"{_safe_name(doi)}.json"


def _register(items: list[dict]) -> None:
    from . import search as base

    for item in items:
        paper_id = item.get("id")
        if paper_id:
            base.LIVE_PAPERS.setdefault(paper_id, item)


def _doi_item(doi: str) -> dict:
    paper_id = "doi-" + _safe_name(doi)
    doi_url = f"https://doi.org/{doi}"
    return {
        "id": paper_id,
        "doi": doi,
        "title": f"DOI: {doi}",
        "abstract": "",
        "tldr": "引用记录未返回摘要；请打开 DOI 或 PDF 查看。",
        "authors": [],
        "year": None,
        "venue": "DOI 记录",
        "publisher": "",
        "url": doi_url,
        "pdf_url": "",
        "source": "doi",
        "demo": False,
        "full_text": False,
        "extraction_level": "引用记录",
        "metrics": {},
        "results": [],
        "baselines": [],
        "limitations": [],
        "novelty": [],
        "assumptions": [],
        "sections": [],
        "figures": [],
        "evidence": [],
        "translation": [],
        "reviewer_questions": [],
        "repo_plan": {
            "environment": ["待补充"],
            "steps": ["打开 DOI 查看原文"],
            "minimal_experiment": "先确认原文与开放获取状态。",
            "risks": ["引用记录可能缺少摘要"],
            "deliverables": ["原文链接"],
        },
        "code_scaffold": "",
        "slides": [],
        "quiz": [],
        "course_case": "",
    }


def _crossref_metadata(dois: list[str]) -> list[dict]:
    from . import search as base

    if not dois:
        return []

    def fetch(doi: str):
        try:
            response = base._http_session().get(
                CROSSREF_WORK + quote(doi, safe=""),
                params={"mailto": config.CONTACT_EMAIL},
                timeout=base.NETWORK_TIMEOUT,
            )
            response.raise_for_status()
            item = (response.json() or {}).get("message") or {}
            parsed = base._parse_crossref_items([item])
            if parsed:
                return parsed[0]
        except Exception:
            pass
        return _doi_item(doi)

    with ThreadPoolExecutor(max_workers=4) as executor:
        return list(executor.map(fetch, dois))


def _get_json(url: str):
    from . import search as base

    response = base._http_session().get(url, timeout=(5, 30))
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("OpenCitations 返回格式异常")
    return payload


def _other_doi(item: dict, field: str) -> str:
    value = item.get(field) if isinstance(item, dict) else ""
    return str(value or "").strip().lower()


def _trim_item(paper: dict) -> dict:
    return {
        "paper_id": paper.get("id"),
        "id": paper.get("id"),
        "doi": paper.get("doi") or "",
        "title": paper.get("title") or "",
        "authors": paper.get("authors") or [],
        "year": paper.get("year"),
        "venue": paper.get("venue") or "",
        "url": paper.get("url") or "",
        "pdf_url": paper.get("pdf_url") or "",
        "abstract": paper.get("abstract") or "",
        "source": paper.get("source") or "crossref",
        "extraction_level": paper.get("extraction_level") or "引用记录",
    }


def _read_cache(cache_dir: Path | None, doi: str, limit: int) -> dict | None:
    try:
        path = _cache_path(cache_dir, doi)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("doi") != doi or int(data.get("limit") or 0) < limit:
        return None
    fetched_at = float(data.get("fetched_at") or 0)
    if time.time() - fetched_at > CACHE_TTL:
        return None
    for key in ("references", "citations"):
        _register(data.get(key) or [])
    return {**data, "cached": True}


def _write_cache(cache_dir: Path | None, doi: str, limit: int, data: dict) -> None:
    try:
        payload = {
            **data,
            "doi": doi,
            "limit": limit,
            "fetched_at": time.time(),
            "cached": False,
        }
        path = _cache_path(cache_dir, doi)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
    except OSError:
        return


def citation_network(
    paper: dict,
    limit: int = 8,
    cache_dir: Path | None = None,
    fetch_json=None,
    metadata_fetcher=None,
) -> dict:
    doi = str(paper.get("doi") or "").strip().lower()
    if not doi:
        return {
            "available": False,
            "reason": "当前论文缺少 DOI；引用网络只对 DOI 论文开放。可先通过 Crossref/DOAJ 检索，或上传含 DOI 的 PDF。",
            "references": [],
            "citations": [],
            "reference_count": 0,
            "citation_count": 0,
            "source": "OpenCitations",
        }
    limit = max(1, min(int(limit), 20))
    cached = _read_cache(cache_dir, doi, limit)
    if cached:
        return cached
    fetcher = fetch_json or _get_json
    errors: list[str] = []
    reference_rows: list[dict] = []
    citation_rows: list[dict] = []
    for key, url, target_rows in (
        ("references", f"{OPEN_CITATIONS}/references/{doi}", reference_rows),
        ("citations", f"{OPEN_CITATIONS}/citations/{doi}", citation_rows),
    ):
        try:
            payload = fetcher(url)
            if isinstance(payload, list):
                target_rows.extend(item for item in payload if isinstance(item, dict))
        except Exception as exc:
            errors.append(f"{key}: {exc}")
    reference_dois = list(
        dict.fromkeys(
            _other_doi(item, "cited")
            for item in reference_rows
            if _other_doi(item, "cited") and _other_doi(item, "cited") != doi
        )
    )
    citation_dois = list(
        dict.fromkeys(
            _other_doi(item, "citing")
            for item in citation_rows
            if _other_doi(item, "citing") and _other_doi(item, "citing") != doi
        )
    )
    if not reference_dois and not citation_dois and errors:
        return {
            "available": False,
            "reason": "OpenCitations 暂时不可用：" + "；".join(errors)[:300],
            "doi": doi,
            "references": [],
            "citations": [],
            "reference_count": 0,
            "citation_count": 0,
            "source": "OpenCitations",
        }
    fetch_limit = max(limit, MIN_METADATA_FETCH)
    refs_to_fetch = reference_dois[:fetch_limit]
    cites_to_fetch = citation_dois[:fetch_limit]
    metadata = metadata_fetcher or _crossref_metadata
    metadata_index: dict[str, dict] = {}
    for item in metadata(list(dict.fromkeys(refs_to_fetch + cites_to_fetch))):
        if item.get("doi"):
            metadata_index[item["doi"].lower()] = item
    references = []
    for item_doi in reference_dois[:limit]:
        item = metadata_index.get(item_doi) or _doi_item(item_doi)
        references.append(_trim_item(item))
    citations = []
    for item_doi in citation_dois[:limit]:
        item = metadata_index.get(item_doi) or _doi_item(item_doi)
        citations.append(_trim_item(item))
    _register([metadata_index.get(doi_value) or _doi_item(doi_value) for doi_value in refs_to_fetch + cites_to_fetch])
    result = {
        "available": True,
        "doi": doi,
        "source": "OpenCitations",
        "source_url": f"https://opencitations.net/index/api/v1/citations/{doi}",
        "references": references,
        "citations": citations,
        "reference_count": len(reference_dois),
        "citation_count": len(citation_dois),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": (
            "引用关系来自 OpenCitations，题录由 Crossref 补全；"
            f"最多展示每侧 {limit} 条。"
        ),
        "errors": errors,
        "cached": False,
    }
    _write_cache(cache_dir, doi, limit, result)
    return result
