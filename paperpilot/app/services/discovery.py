from __future__ import annotations

import hashlib
import re
from urllib.parse import quote

EUROPEPMC_API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
DOAJ_API = "https://doaj.org/api/search/articles"
HAL_API = "https://api.archives-ouvertes.fr/search"


def _clean(value) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(text.split())


def _shorten(text: str, limit: int = 240) -> str:
    text = _clean(text)
    return text[:limit].rstrip() + "…" if len(text) > limit else text


def _paper(
    *,
    paper_id: str,
    title: str,
    abstract: str,
    authors: list[str],
    year: int | None,
    venue: str,
    url: str,
    pdf_url: str,
    source: str,
    extraction_level: str,
    doi: str = "",
) -> dict:
    abstract = _clean(abstract)
    sections = [{"title": "Abstract", "page": 1, "text": abstract}] if abstract else []
    evidence = (
        [
            {
                "field": "abstract",
                "label": extraction_level,
                "section": "Abstract",
                "page": 1,
                "quote": abstract[:320],
            }
        ]
        if abstract
        else []
    )
    return {
        "id": paper_id,
        "doi": doi,
        "title": _clean(title),
        "abstract": abstract,
        "tldr": _shorten(abstract) if abstract else "该来源未提供摘要；打开原文查看全文。",
        "authors": authors[:12],
        "year": year,
        "venue": venue or extraction_level,
        "publisher": "",
        "url": url,
        "pdf_url": pdf_url,
        "source": source,
        "demo": False,
        "full_text": False,
        "extraction_level": extraction_level,
        "metrics": {"来源": extraction_level},
        "results": [],
        "baselines": [],
        "limitations": [],
        "novelty": [],
        "assumptions": [],
        "sections": sections,
        "figures": [],
        "evidence": evidence,
        "translation": [],
        "reviewer_questions": [],
        "repo_plan": {
            "environment": ["待补充：论文依赖、数据集、代码仓库"],
            "steps": ["确认开放获取版本与 DOI", "查找作者代码或补充材料", "记录实验条件", "对照论文指标"],
            "minimal_experiment": "从论文中最小的数据集或配置开始，先跑通一次前向与评估。",
            "risks": ["来源只提供摘要级元数据", "论文可能未开放全文", "测试条件不一致"],
            "deliverables": ["复现日志", "指标对照表", "问题清单"],
        },
        "code_scaffold": "# TODO: 上传开放获取 PDF 或配置 LLM 后可生成复现代码骨架。\n",
        "slides": ["研究问题", "方法概览", "关键实验", "主要结果", "局限与启示"],
        "quiz": [],
        "course_case": "待生成：上传 PDF 或配置模型后，可将本文映射到课程知识点。",
    }


def _parse_europepmc(payload: dict) -> list[dict]:
    papers: list[dict] = []
    results = ((payload or {}).get("resultList") or {}).get("result") or []
    for item in results:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or ""
        record_id = item.get("id") or item.get("pmid")
        if not title or not record_id:
            continue
        authors = []
        for author in ((item.get("authorList") or {}).get("author") or []):
            if not isinstance(author, dict):
                continue
            name = author.get("fullName") or author.get("collectiveName") or ""
            if name:
                authors.append(name)
        if not authors:
            authors = [
                part.strip()
                for part in re.split(r",|;|\band\b", item.get("authorString") or "")
                if part.strip()
            ]
        pdf_url = ""
        for link in ((item.get("fullTextUrlList") or {}).get("fullTextUrl") or []):
            url = link.get("url") or ""
            if url and (
                link.get("documentStyle") == "pdf"
                or "open" in str(link.get("availability") or "").lower()
            ):
                pdf_url = url
                break
        journal_info = item.get("journalInfo") or {}
        year_text = str(
            item.get("pubYear")
            or journal_info.get("yearOfPublication")
            or (item.get("firstPublicationDate") or "")[:4]
        )
        journal = journal_info.get("journal") or {}
        source_name = item.get("source") or "MED"
        papers.append(
            _paper(
                paper_id=f"europepmc-{source_name}-{record_id}",
                title=title,
                abstract=item.get("abstractText") or "",
                authors=authors,
                year=int(year_text) if year_text.isdigit() and int(year_text) > 0 else None,
                venue=item.get("journalTitle") or journal.get("title") or journal.get("medlineAbbreviation") or "Europe PMC",
                url=f"https://europepmc.org/article/{source_name}/{record_id}",
                pdf_url=pdf_url,
                source="europepmc",
                extraction_level="Europe PMC 摘要级",
                doi=(item.get("doi") or "").strip().lower(),
            )
        )
    return papers


def _parse_doaj(payload: dict) -> list[dict]:
    papers: list[dict] = []
    for result in (payload or {}).get("results") or []:
        bib = (result or {}).get("bibjson") or {}
        title = bib.get("title") or ""
        if not title:
            continue
        identifiers = bib.get("identifier") or []
        doi = next(
            (
                str(item.get("id") or "").strip().lower()
                for item in identifiers
                if isinstance(item, dict) and item.get("type") == "doi"
            ),
            "",
        )
        links = bib.get("link") or []
        url = next(
            (item.get("url") for item in links if isinstance(item, dict) and item.get("url")),
            "",
        )
        pdf_url = next(
            (
                item.get("url")
                for item in links
                if isinstance(item, dict)
                and item.get("url")
                and str(item.get("type") or "").lower() in {"fulltext", "pdf"}
                and str(item.get("url")).lower().endswith(".pdf")
            ),
            "",
        )
        authors = [
            author.get("name")
            for author in (bib.get("author") or [])
            if isinstance(author, dict) and author.get("name")
        ]
        journal = (bib.get("journal") or {}).get("title") or "DOAJ"
        year_text = str(bib.get("year") or "")
        if not doi:
            doi = ""
        paper_id = "doaj-" + (
            re.sub(r"[^a-z0-9]+", "-", doi).strip("-")
            or hashlib.sha1(title.encode("utf-8")).hexdigest()[:12]
        )
        papers.append(
            _paper(
                paper_id=paper_id,
                title=title,
                abstract=bib.get("abstract") or "",
                authors=authors,
                year=int(year_text) if year_text.isdigit() else None,
                venue=journal,
                url=url or (f"https://doi.org/{doi}" if doi else "https://doaj.org"),
                pdf_url=pdf_url,
                source="doaj",
                extraction_level="DOAJ 摘要级",
                doi=doi,
            )
        )
    return papers


def _parse_hal(payload: dict) -> list[dict]:
    papers: list[dict] = []
    docs = ((payload or {}).get("response") or {}).get("docs") or []
    for item in docs:
        if not isinstance(item, dict):
            continue
        title_values = item.get("title_s") or []
        title = title_values[0] if isinstance(title_values, list) and title_values else ""
        doc_id = item.get("docid")
        if not title or not doc_id:
            continue
        abstract_values = item.get("abstract_s") or []
        abstract = abstract_values[0] if isinstance(abstract_values, list) and abstract_values else ""
        doi_values = item.get("doiId_s")
        if isinstance(doi_values, list):
            doi = str(doi_values[0]).lower() if doi_values else ""
        else:
            doi = str(doi_values or "").lower()
        year = item.get("producedDateY_i")
        venue_values = item.get("journalTitle_s")
        venue = (
            venue_values
            if isinstance(venue_values, str)
            else (venue_values[0] if isinstance(venue_values, list) and venue_values else "HAL")
        )
        papers.append(
            _paper(
                paper_id=f"hal-{doc_id}",
                title=title,
                abstract=abstract,
                authors=list(item.get("authFullName_s") or []),
                year=int(year) if str(year or "").isdigit() else None,
                venue=venue,
                url=item.get("uri_s") or f"https://hal.science/{doc_id}",
                pdf_url=item.get("fileMain_s") or "",
                source="hal",
                extraction_level="HAL 摘要级",
                doi=doi,
            )
        )
    return papers


def _search_external(source: str, url: str, params: dict, query: str, limit: int, parser) -> list[dict]:
    from . import search as base

    cache_limit = max(limit, 5)
    cached = base._read_cache(source, query, cache_limit)
    if cached is not None:
        return base._merge_live(cached[:limit])
    try:
        response = base._http_session().get(url, params=params, timeout=base.NETWORK_TIMEOUT)
        response.raise_for_status()
        papers = parser(response.json())
    except Exception:
        cached = base._read_cache(source, query, cache_limit, allow_stale=True)
        if cached is not None:
            return base._merge_live(cached[:limit])
        raise
    results = base._merge_live(papers[:limit])
    base._write_cache(source, query, cache_limit, results)
    return results


def search_europepmc(query: str, limit: int = 8) -> list[dict]:
    from . import search as base

    translated = base._crossref_query(query)
    query_text = f"TITLE_ABS:({translated})" if translated else translated
    results = _search_external(
        "europepmc",
        EUROPEPMC_API,
        {
            "query": query_text,
            "format": "json",
            "pageSize": max(limit, 5),
            "resultType": "core",
        },
        query,
        limit,
        _parse_europepmc,
    )
    if not results and query_text != translated:
        results = _search_external(
            "europepmc",
            EUROPEPMC_API,
            {
                "query": translated,
                "format": "json",
                "pageSize": max(limit, 5),
                "resultType": "core",
            },
            query,
            limit,
            _parse_europepmc,
        )
    return results


def search_doaj(query: str, limit: int = 8) -> list[dict]:
    from . import search as base

    return _search_external(
        "doaj",
        DOAJ_API + "/" + quote(base._crossref_query(query), safe=""),
        {"pageSize": max(limit, 5)},
        query,
        limit,
        _parse_doaj,
    )


def search_hal(query: str, limit: int = 8) -> list[dict]:
    from . import search as base

    return _search_external(
        "hal",
        HAL_API,
        {
            "q": base._crossref_query(query),
            "fl": "docid,title_s,abstract_s,authFullName_s,producedDateY_i,doiId_s,uri_s,journalTitle_s,fileMain_s",
            "rows": max(limit, 5),
            "wt": "json",
        },
        query,
        limit,
        _parse_hal,
    )
