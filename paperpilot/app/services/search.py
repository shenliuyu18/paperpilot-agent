from __future__ import annotations

import hashlib
import html
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .. import config, store
from ..demo_data import PAPERS, PAPERS_BY_ID
from ..store import UPLOAD_PAPERS
from .discovery import (  # noqa: F401 - parsers are re-exported for callers and tests
    _parse_doaj,
    _parse_europepmc,
    _parse_hal,
    search_doaj,
    search_europepmc,
    search_hal,
)

ARXIV_API = "https://export.arxiv.org/api/query"
CROSSREF_API = "https://api.crossref.org/works"
ATOM = {"a": "http://www.w3.org/2005/Atom"}
SEARCH_CACHE_DIR = config.CACHE_DIR / "search"
SEARCH_CACHE_TTL = int(os.getenv("PAPERPILOT_SEARCH_TTL", "86400"))
NETWORK_TIMEOUT = (5, 20)
AUTO_DEADLINE = float(os.getenv("PAPERPILOT_AUTO_DEADLINE", "8"))
AUTO_COOLDOWN_TTL = int(os.getenv("PAPERPILOT_AUTO_COOLDOWN", "180"))
_AUTO_COOLDOWN: dict[str, float] = {}
_AUTO_LOCK = threading.Lock()
QUERY_STOP_WORDS = {
    "the", "of", "and", "in", "to", "is", "we", "this", "with", "for", "on",
    "by", "as", "that", "are", "from", "using", "based", "can", "be", "a", "an",
    "it", "its", "our", "their", "was", "were", "which", "these", "those", "at",
    "or", "not", "has", "have", "had", "but", "than", "then", "also", "into",
    "paper", "papers", "work", "works", "study", "studies", "research",
}

CROSSREF_SELECT = (
    "DOI,title,author,issued,published,published-online,container-title,"
    "abstract,URL,link,is-referenced-by-count,type,publisher,subject"
)

SYNONYMS = {
    "存算一体": "compute-in-memory CIM",
    "存内计算": "compute-in-memory CIM",
    "低功耗": "low power energy efficient",
    "模数转换": "ADC analog-to-digital",
    "模数转换器": "ADC",
    "感放大器": "sense amplifier SA",
    "灵敏放大器": "sense amplifier SA",
    "非线性": "nonlinear",
    "神经形态": "neuromorphic SNN",
    "电荷": "charge",
    "可重构": "reconfigurable",
    "鲁棒": "robust noise",
    "噪声": "noise",
    "精度": "accuracy",
    "早停": "early stopping early-stopping",
    "读出": "readout",
    "开关电容": "switched capacitor",
    "量化": "quantization",
    "稀疏": "sparse sparsity",
    "脉冲": "spiking spike SNN",
    "校准": "calibration",
    "吞吐": "throughput",
    "延迟": "latency",
    "能效": "energy efficiency",
    "面积": "area",
    "工艺": "process",
    "代价": "cost overhead trade-off",
    "开销": "overhead cost",
    "降低": "reduce lower",
    "转换": "conversion convert",
    "计算": "computing computation",
    "机制": "mechanism",
    "方式": "scheme approach",
    "原理": "principle mechanism",
    "流程": "flow pipeline",
    "局限": "limitation limitations",
    "风险": "risk challenge",
    "不足": "limitation weakness",
    "缺点": "drawback weakness",
    "优点": "advantage benefit",
    "结构": "structure architecture",
    "数据集": "dataset benchmark",
    "实验": "experiment evaluation",
    "结果": "result",
    "指标": "metric",
    "准确率": "accuracy",
    "功耗": "power consumption",
    "电压": "voltage",
    "电流": "current",
    "阵列": "array",
    "单元": "cell",
}

GENERAL_SYNONYMS = {
    "高效": "efficient effective",
    "低功耗": "low power energy efficient",
    "大语言模型": "large language model LLM",
    "大型语言模型": "large language model LLM",
    "大模型": "large language model LLM",
    "语言模型": "language model LLM",
    "问题": "problem challenge",
    "挑战": "challenge difficulty",
    "瓶颈": "bottleneck limitation challenge",
    "障碍": "barrier obstacle",
    "边缘": "edge",
    "设备": "device",
    "手机等": "mobile edge device",
    "方法": "method approach",
    "方案": "scheme approach solution",
    "思路": "approach idea",
    "贡献": "contribution",
    "创新": "novelty contribution innovation",
    "核心": "key main core",
    "主要": "main primary",
    "解决": "solve address",
    "处理": "handle address",
    "提升": "improve boost increase",
    "提高": "improve increase",
    "降低": "reduce decrease lower",
    "减少": "reduce decrease",
    "优化": "optimize optimization",
    "加速": "accelerate acceleration speedup",
    "压缩": "compress compression",
    "量化": "quantization quantize",
    "推理": "inference",
    "训练": "training train",
    "微调": "fine-tuning finetune",
    "模型": "model",
    "神经网络": "neural network",
    "深度学习": "deep learning",
    "机器学习": "machine learning",
    "注意力": "attention",
    "矩阵": "matrix",
    "权重": "weight",
    "激活": "activation",
    "参数": "parameter",
    "位宽": "bitwidth bit-width",
    "比特": "bit",
    "内存": "memory",
    "显存": "memory GPU",
    "存储": "storage memory",
    "带宽": "bandwidth",
    "计算": "compute computation",
    "算法": "algorithm",
    "架构": "architecture",
    "框架": "framework",
    "硬件": "hardware",
    "软件": "software",
    "芯片": "chip",
    "电路": "circuit",
    "系统": "system",
    "数据": "data",
    "数据集": "dataset",
    "基准": "benchmark",
    "评估": "evaluation evaluate",
    "评测": "evaluation benchmark",
    "实验": "experiment experimental",
    "结果": "result",
    "性能": "performance",
    "精度": "accuracy precision",
    "准确率": "accuracy",
    "速度": "speed latency runtime",
    "延迟": "latency delay",
    "吞吐": "throughput",
    "能效": "energy efficiency",
    "功耗": "power energy",
    "能耗": "energy consumption power",
    "面积": "area",
    "成本": "cost",
    "开销": "overhead cost",
    "鲁棒": "robust robustness",
    "泛化": "generalization",
    "稀疏": "sparse sparsity",
    "剪枝": "pruning prune",
    "蒸馏": "distillation distill",
    "校准": "calibration calibrate",
    "部署": "deployment deploy",
    "端侧": "edge on-device",
    "手机": "mobile phone",
    "服务器": "server",
    "并行": "parallel parallelism",
    "分布式": "distributed",
    "缓存": "cache caching",
    "卷积": "convolution",
    "循环": "recurrent",
    "分类": "classification",
    "检测": "detection",
    "分割": "segmentation",
    "生成": "generation generative",
    "摘要": "abstract summary",
    "结论": "conclusion finding",
    "局限": "limitation limitation",
    "风险": "risk challenge",
    "未来": "future",
    "对比": "comparison compare",
    "区别": "difference",
    "鲁棒性": "robustness",
    "可扩展": "scalable scalability",
    "轻量": "lightweight",
    "大规模": "large-scale",
    "注意力机制": "attention",
    "上下文": "context",
    "长序列": "long sequence context",
    "并行计算": "parallel computing",
    "异构": "heterogeneous",
    "近似": "approximate approximation",
    "误差": "error",
    "校准误差": "calibration error",
}

LIVE_PAPERS: dict[str, dict] = {}
_MEM_CACHE: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()
_MEM_LOCK = threading.Lock()
_HTTP_LOCK = threading.Lock()
_HTTP_SESSION: requests.Session | None = None


def _load_cache() -> None:
    for cache_file in sorted(config.CACHE_DIR.glob("*.json")):
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for paper in data.get("papers", []):
            paper_id = paper.get("id")
            if not paper_id:
                continue
            merged = dict(paper)
            merged.update(PAPERS_BY_ID.get(paper_id, {}))
            LIVE_PAPERS[paper_id] = merged


_load_cache()


def _matched_synonyms(query: str) -> list[tuple[int, str, str]]:
    matches: list[tuple[int, int, int, str, str]] = []
    for priority, mapping in enumerate((SYNONYMS, GENERAL_SYNONYMS)):
        for zh, english in mapping.items():
            position = query.find(zh)
            if position >= 0:
                matches.append((position, -len(zh), priority, zh, english))
    matches.sort()
    seen: set[str] = set()
    spans: list[tuple[int, int]] = []
    result: list[tuple[int, str, str]] = []
    for position, _, _, zh, english in matches:
        if zh in seen:
            continue
        end = position + len(zh)
        if any(position < span_end and end > span_start for span_start, span_end in spans):
            continue
        seen.add(zh)
        spans.append((position, end))
        result.append((position, zh, english))
    return result


def _expand_query(query: str) -> str:
    extras: list[str] = []
    for _, _, english in _matched_synonyms(query):
        extras.extend(
            term for term in re.findall(r"[A-Za-z][A-Za-z0-9\-\.\+]*", english)
            if len(term) >= 2
        )
    if not extras:
        return query
    return " ".join(dict.fromkeys([query, *extras]))


def _http_session() -> requests.Session:
    global _HTTP_SESSION
    if _HTTP_SESSION is None:
        with _HTTP_LOCK:
            if _HTTP_SESSION is None:
                retry = Retry(
                    total=2,
                    connect=2,
                    read=0,
                    status=1,
                    backoff_factor=0.3,
                    status_forcelist=(429, 500, 502, 503, 504),
                    allowed_methods=frozenset({"GET"}),
                    respect_retry_after_header=True,
                )
                adapter = HTTPAdapter(
                    max_retries=retry,
                    pool_connections=8,
                    pool_maxsize=24,
                )
                session = requests.Session()
                session.mount("https://", adapter)
                session.mount("http://", adapter)
                session.headers.update(
                    {
                        "User-Agent": (
                            f"PaperPilot/{config.APP_VERSION} "
                            f"(mailto:{config.CONTACT_EMAIL})"
                        )
                    }
                )
                _HTTP_SESSION = session
    return _HTTP_SESSION


def _cache_key(source: str, query: str, limit: int) -> str:
    raw = f"{source}|{query.strip().lower()}|{limit}".encode("utf-8")
    return f"{source}-{hashlib.sha1(raw).hexdigest()[:20]}"


def _mark_cached(papers: list[dict], age_seconds: float) -> list[dict]:
    minutes = max(0, int(age_seconds // 60))
    return [
        {
            **paper,
            "cached": True,
            "cache_age_minutes": minutes,
        }
        for paper in papers
    ]


def _read_cache(source: str, query: str, limit: int, allow_stale: bool = False) -> list[dict] | None:
    key = _cache_key(source, query, limit)
    now = time.time()
    with _MEM_LOCK:
        entry = _MEM_CACHE.get(key)
        if entry:
            _MEM_CACHE.move_to_end(key)
    if entry:
        fetched_at, papers = entry
        age = now - fetched_at
        if allow_stale or age <= SEARCH_CACHE_TTL:
            return _mark_cached([dict(paper) for paper in papers], age)
    path = SEARCH_CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        data.get("source") != source
        or str(data.get("query", "")).strip().lower() != query.strip().lower()
        or int(data.get("limit") or 0) != limit
    ):
        return None
    fetched_at = float(data.get("fetched_at") or 0)
    age = now - fetched_at
    if not allow_stale and age > SEARCH_CACHE_TTL:
        return None
    papers = [paper for paper in data.get("papers") or [] if isinstance(paper, dict)]
    if not papers:
        return None
    with _MEM_LOCK:
        _MEM_CACHE[key] = (fetched_at, papers)
        while len(_MEM_CACHE) > 64:
            _MEM_CACHE.popitem(last=False)
    return _mark_cached([dict(paper) for paper in papers], age)


def _write_cache(source: str, query: str, limit: int, papers: list[dict]) -> None:
    if not papers:
        return
    key = _cache_key(source, query, limit)
    fetched_at = time.time()
    payload = {
        "source": source,
        "query": query,
        "limit": limit,
        "fetched_at": fetched_at,
        "papers": [{k: v for k, v in paper.items() if k != "cached"} for paper in papers],
    }
    with _MEM_LOCK:
        _MEM_CACHE[key] = (fetched_at, payload["papers"])
        _MEM_CACHE.move_to_end(key)
        while len(_MEM_CACHE) > 64:
            _MEM_CACHE.popitem(last=False)
    try:
        SEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        target = SEARCH_CACHE_DIR / f"{key}.json"
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp.replace(target)
    except OSError:
        pass


def _query_groups(query: str) -> list[list[str]]:
    groups: list[list[str]] = []
    covered: set[str] = set()

    def add_group(terms: list[str]) -> None:
        values = []
        for term in terms:
            cleaned = term.strip(".,;:")
            if len(cleaned) < 2:
                continue
            if cleaned.lower() in QUERY_STOP_WORDS:
                continue
            if cleaned.lower() not in covered:
                values.append(cleaned)
        if not values:
            return
        groups.append(values)
        covered.update(value.lower() for value in values)

    original_terms = re.findall(r"[A-Za-z][A-Za-z0-9\-\.\+]*", query)
    for term in original_terms:
        add_group([term])
    for _, _, english in _matched_synonyms(query):
        add_group(re.findall(r"[A-Za-z][A-Za-z0-9\-\.\+]*", english))
    # arXiv treats every group as an AND term; more than four groups makes the
    # query both over-constrained and slow, so keep the leading concepts only.
    return groups[:4]


def _arxiv_query(query: str) -> str:
    groups = _query_groups(query)
    if not groups:
        cleaned = re.sub(r"[\"()]+", " ", query).strip()
        return f"all:{cleaned}" if cleaned else ""
    parts = []
    for group in groups:
        if len(group) == 1:
            parts.append(f"all:{group[0]}")
        else:
            parts.append("(" + " OR ".join(f"all:{term}" for term in group) + ")")
    return " AND ".join(parts)


def _tokens(query: str) -> list[str]:
    expanded = _expand_query(query).lower()
    latin = re.findall(r"[a-z0-9][a-z0-9\-\.]{1,}", expanded)
    chinese = re.findall(r"[\u4e00-\u9fff]{2,}", expanded)
    return list(dict.fromkeys(latin + chinese))


def search_demo(query: str, limit: int = 8) -> list[dict]:
    tokens = _tokens(query)
    scored: list[tuple[float, dict]] = []
    for paper in PAPERS:
        haystack = " ".join(
            [
                paper.get("title", ""),
                paper.get("abstract", ""),
                paper.get("tldr", ""),
                paper.get("problem", ""),
                paper.get("method", ""),
                " ".join(paper.get("novelty", [])),
            ]
        ).lower()
        score = 0.0
        for token in tokens:
            if token in haystack:
                score += 2.0 if token in paper.get("title", "").lower() else 1.0
        if paper.get("full_text"):
            score += 1.25
        if not tokens:
            score = 1.0
        scored.append((score, paper))
    scored.sort(key=lambda item: (-item[0], item[1].get("year", 0)), reverse=False)
    return [dict(paper, relevance=round(score, 2)) for score, paper in scored[:limit]]


def _parse_arxiv(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    papers: list[dict] = []
    for entry in root.findall("a:entry", ATOM):
        def text(tag: str) -> str:
            node = entry.find("a:" + tag, ATOM)
            return " ".join(node.text.split()) if node is not None and node.text else ""

        paper_id = text("id").split("/abs/")[-1]
        pdf_url = ""
        for link in entry.findall("a:link", ATOM):
            if link.attrib.get("title") == "pdf":
                pdf_url = link.attrib.get("href", "")
                break
        published = text("published")[:10]
        year = int(published[:4]) if re.match(r"^\d{4}", published) else None
        papers.append(
            {
                "id": paper_id,
                "title": text("title"),
                "abstract": text("summary"),
                "published": published,
                "updated": text("updated")[:10],
                "year": year,
                "authors": [
                    author.find("a:name", ATOM).text
                    for author in entry.findall("a:author", ATOM)
                    if author.find("a:name", ATOM) is not None
                ],
                "url": text("id"),
                "pdf_url": pdf_url,
                "source": "arxiv",
                "venue": "arXiv preprint",
                "demo": False,
                "full_text": False,
                "extraction_level": "摘要级",
            }
        )
    return papers


def _merge_live(papers: list[dict]) -> list[dict]:
    results: list[dict] = []
    for paper in papers:
        merged = dict(paper)
        paper_id = merged.get("id")
        if paper_id in PAPERS_BY_ID:
            merged.update(PAPERS_BY_ID[paper_id])
        if paper_id:
            LIVE_PAPERS.setdefault(paper_id, merged)
        results.append(merged)
    return results


def search_arxiv(query: str, limit: int = 8) -> list[dict]:
    cache_limit = max(limit, 5)
    cached = _read_cache("arxiv", query, cache_limit)
    if cached is not None:
        return _merge_live(cached[:limit])
    params = {
        "search_query": _arxiv_query(query) or f"all:{query}",
        "start": 0,
        "max_results": cache_limit,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    try:
        response = _http_session().get(
            ARXIV_API,
            params=params,
            timeout=NETWORK_TIMEOUT,
        )
        response.raise_for_status()
        papers = _parse_arxiv(response.content)
    except Exception:
        cached = _read_cache("arxiv", query, cache_limit, allow_stale=True)
        if cached is not None:
            return _merge_live(cached[:limit])
        raise
    results = _merge_live(papers[:limit])
    _write_cache("arxiv", query, cache_limit, results)
    return results


def _clean_crossref(value: str) -> str:
    text = html.unescape(html.unescape(str(value or "")))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace(" ,", ",").strip(" ,;")


def _crossref_year(item: dict) -> int | None:
    for key in ("issued", "published", "published-online", "published-print"):
        parts = ((item.get(key) or {}).get("date-parts") or [[]])[0]
        if parts and parts[0]:
            try:
                return int(parts[0])
            except (TypeError, ValueError):
                continue
    return None


def _crossref_pdf(item: dict) -> str:
    for link in item.get("link") or []:
        url = link.get("URL") or ""
        if link.get("content-type") == "application/pdf" or url.lower().endswith(".pdf"):
            return url
    return ""


def _shorten(text: str, limit: int = 260) -> str:
    text = " ".join((text or "").split())
    return text[:limit].rstrip() + "…" if len(text) > limit else text


def _parse_crossref_items(items: list[dict]) -> list[dict]:
    papers: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"component", "dataset", "peer-review", "grant"}:
            continue
        doi = (item.get("DOI") or "").strip().lower()
        titles = item.get("title") or []
        title = _clean_crossref(titles[0] if titles else "")
        if not title or not doi:
            continue
        title = title.replace(":,", ": ").replace(",:", ": ").strip(" ,;")
        authors: list[str] = []
        for author in item.get("author") or []:
            name = " ".join(
                part for part in [author.get("given"), author.get("family")] if part
            ).strip() or (author.get("name") or "").strip()
            if name:
                authors.append(name)
        abstract = _clean_crossref(item.get("abstract") or "")
        if abstract.lower().startswith("abstract"):
            abstract = re.sub(r"^abstract[\s:—-]*", "", abstract, flags=re.I)
        venues = item.get("container-title") or []
        venue = _clean_crossref(venues[0] if venues else "")
        type_label = {
            "journal-article": "期刊论文",
            "proceedings-article": "会议论文",
            "posted-content": "预印本",
            "book-chapter": "书籍章节",
            "report": "报告",
        }.get(item.get("type") or "", "Crossref 记录")
        venue = venue or _clean_crossref(item.get("publisher") or "") or type_label
        url = item.get("URL") or f"https://doi.org/{doi}"
        sections = (
            [{"title": "Abstract", "page": 1, "text": abstract}]
            if abstract
            else []
        )
        paper = {
            "id": "crossref-" + re.sub(r"[^a-z0-9]+", "-", doi).strip("-"),
            "doi": doi,
            "title": title,
            "abstract": abstract,
            "tldr": _shorten(abstract, 240) if abstract else "Crossref 未提供摘要；打开 DOI 查看原文，或上传 PDF 解锁全文解析。",
            "authors": authors[:12],
            "year": _crossref_year(item),
            "venue": venue,
            "publisher": _clean_crossref(item.get("publisher") or ""),
            "url": url,
            "pdf_url": _crossref_pdf(item),
            "source": "crossref",
            "demo": False,
            "full_text": False,
            "extraction_level": "Crossref 摘要级",
            "cited_by_count": int(item.get("is-referenced-by-count") or 0),
            "metrics": {
                "被引": int(item.get("is-referenced-by-count") or 0),
                "类型": type_label,
                "来源": "Crossref / DOI",
            },
            "results": [],
            "baselines": [],
            "limitations": [],
            "novelty": [],
            "assumptions": [],
            "sections": sections,
            "figures": [],
            "evidence": (
                [
                    {
                        "field": "abstract",
                        "label": "Crossref 摘要",
                        "section": "Abstract",
                        "page": 1,
                        "quote": abstract[:280],
                    }
                ]
                if abstract
                else []
            ),
            "translation": [],
            "reviewer_questions": [],
            "repo_plan": {
                "environment": ["待补充：论文依赖、数据集、代码仓库"],
                "steps": ["先确认 DOI 对应版本与开放获取状态", "查找作者代码或补充材料", "记录实验条件", "对照论文指标"],
                "minimal_experiment": "从论文中最小的数据集/模型配置开始，先跑通一次前向与评估。",
                "risks": ["Crossref 仅提供摘要级元数据", "论文可能未开放全文", "超参数与预处理差异"],
                "deliverables": ["复现日志", "指标对照表", "问题清单"],
            },
            "code_scaffold": "# TODO: 上传开放获取 PDF 或配置 LLM 后，PaperPilot 可生成复现代码骨架。\n",
            "slides": ["研究问题", "方法概览", "关键实验", "主要结果", "局限与启示"],
            "quiz": [],
            "course_case": "待生成：上传 PDF 或配置模型后，可将本文映射到课程知识点。",
        }
        papers.append(paper)
    return papers


def _crossref_query(query: str) -> str:
    expanded = _expand_query(query)
    english = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-\.\+/%]{1,}", expanded)
    terms = [
        term for term in english
        if len(term) >= 2 and term.lower() not in QUERY_STOP_WORDS
    ]
    return " ".join(list(dict.fromkeys(terms))[:14]) or query


def search_crossref(query: str, limit: int = 8) -> list[dict]:
    cache_limit = max(limit, 5)
    cached = _read_cache("crossref", query, cache_limit)
    if cached is not None:
        return _merge_live(cached[:limit])
    params = {
        "query.bibliographic": _crossref_query(query),
        "rows": cache_limit,
        "select": CROSSREF_SELECT,
        "mailto": config.CONTACT_EMAIL,
    }
    try:
        response = _http_session().get(
            CROSSREF_API,
            params=params,
            timeout=NETWORK_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        items = (payload.get("message") or {}).get("items") or []
    except Exception:
        cached = _read_cache("crossref", query, cache_limit, allow_stale=True)
        if cached is not None:
            return _merge_live(cached[:limit])
        raise
    papers: list[dict] = []
    seen: set[str] = set()
    for paper in _parse_crossref_items(items):
        if paper["id"] in seen:
            continue
        seen.add(paper["id"])
        papers.append(paper)
        if len(papers) >= limit:
            break
    results = _merge_live(papers)
    _write_cache("crossref", query, cache_limit, results)
    return results


def _auto_key(paper: dict) -> str:
    doi = str(paper.get("doi") or "").strip().lower()
    if doi:
        return "doi:" + doi
    title = re.sub(r"[^a-z0-9]+", " ", str(paper.get("title") or "").lower()).strip()
    return "title:" + title[:140]


def _paper_quality(paper: dict) -> int:
    return (
        len(str(paper.get("abstract") or ""))
        + (10 if paper.get("doi") else 0)
        + (6 if paper.get("title") else 0)
        + (4 if paper.get("authors") else 0)
        + (3 if paper.get("year") else 0)
    )


def _auto_available(source: str) -> bool:
    with _AUTO_LOCK:
        return _AUTO_COOLDOWN.get(source, 0) <= time.time()


def _auto_mark(source: str, failed: bool) -> None:
    with _AUTO_LOCK:
        if failed:
            _AUTO_COOLDOWN[source] = time.time() + AUTO_COOLDOWN_TTL
        else:
            _AUTO_COOLDOWN.pop(source, None)


def _is_transient_failure(exc: BaseException) -> bool:
    return isinstance(
        exc,
        (
            requests.exceptions.RequestException,
            ConnectionError,
            TimeoutError,
            FuturesTimeoutError,
        ),
    )


def search_auto(query: str, limit: int = 8) -> list[dict]:
    """Concurrent multi-source search with rank-fusion, dedupe and bounded latency."""
    source_order = ("crossref", "arxiv", "europepmc")
    fetchers = {
        "crossref": search_crossref,
        "arxiv": search_arxiv,
        "europepmc": search_europepmc,
    }
    available = [source for source in source_order if _auto_available(source)]
    if not available:
        return [
            {**paper, "aggregate": True, "aggregate_fallback": True, "aggregate_sources": ["demo"]}
            for paper in search_demo(query, limit)
        ]
    per_source = max(4, min(limit, 8))
    gathered: dict[str, list[dict]] = {}
    executor = ThreadPoolExecutor(max_workers=len(available))
    try:
        futures = {
            executor.submit(fetchers[source], query, per_source): source
            for source in available
        }
        timed_out = False
        try:
            for future in as_completed(futures, timeout=AUTO_DEADLINE):
                source = futures[future]
                try:
                    gathered[source] = future.result() or []
                    _auto_mark(source, False)
                except Exception as exc:
                    gathered[source] = []
                    _auto_mark(source, _is_transient_failure(exc))
        except FuturesTimeoutError:
            timed_out = True
        if timed_out:
            for future, source in futures.items():
                if future.done():
                    try:
                        gathered[source] = future.result() or []
                        _auto_mark(source, False)
                    except Exception as exc:
                        gathered[source] = []
                        _auto_mark(source, _is_transient_failure(exc))
                else:
                    gathered[source] = []
                    _auto_mark(source, True)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    entries: dict[str, dict] = {}
    for source in source_order:
        for rank, paper in enumerate(gathered.get(source, [])):
            if not paper.get("id"):
                continue
            key = _auto_key(paper)
            if not key:
                continue
            if key not in entries:
                entries[key] = {
                    "paper": dict(paper),
                    "sources": [],
                    "score": 0.0,
                    "quality": _paper_quality(paper),
                }
            entry = entries[key]
            if source not in entry["sources"]:
                entry["sources"].append(source)
            entry["score"] += 1.0 / (60 + rank + 1)
            quality = _paper_quality(paper)
            if quality > entry["quality"]:
                entry["paper"] = dict(paper)
                entry["quality"] = quality
    ordered = sorted(
        entries.values(),
        key=lambda entry: (
            -entry["score"],
            -(entry["paper"].get("year") or 0),
            str(entry["paper"].get("title") or ""),
        ),
    )
    if not ordered:
        return [
            {**paper, "aggregate": True, "aggregate_fallback": True, "aggregate_sources": ["demo"]}
            for paper in search_demo(query, limit)
        ]
    results = []
    for entry in ordered[:limit]:
        paper = entry["paper"]
        paper["aggregate"] = True
        paper["aggregate_sources"] = list(entry["sources"])
        paper["aggregate_score"] = round(entry["score"], 4)
        results.append(paper)
    return results


def search_upload(query: str, limit: int = 8) -> list[dict]:
    """Search the PDFs the user uploaded to this instance."""
    tokens = _tokens(query)
    scored: list[tuple[float, dict]] = []
    for paper in UPLOAD_PAPERS.values():
        sections = paper.get("sections") or []
        body = " ".join(
            [
                str(paper.get("title") or ""),
                str(paper.get("abstract") or ""),
                str(paper.get("tldr") or ""),
                " ".join(str(section.get("text") or "")[:1200] for section in sections[:6]),
            ]
        ).lower()
        score = 0.0
        for token in tokens:
            if token in body:
                score += 2.0 if token in str(paper.get("title") or "").lower() else 1.0
        if tokens and score <= 0:
            continue
        if not tokens:
            score = 1.0
        scored.append((score, paper))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("title") or "")))
    return [dict(paper, relevance=round(score, 2)) for score, paper in scored[:max(1, limit)]]


def search(query: str, limit: int = 8, source: str = "demo") -> list[dict]:
    if source.startswith("custom:"):
        from . import custom_sources

        source_id = source.split(":", 1)[1]
        return _merge_live(custom_sources.search(source_id, query, limit))
    if source == "arxiv":
        return search_arxiv(query, limit)
    if source == "crossref":
        return search_crossref(query, limit)
    if source == "europepmc":
        return search_europepmc(query, limit)
    if source == "doaj":
        return search_doaj(query, limit)
    if source == "hal":
        return search_hal(query, limit)
    if source == "auto":
        return search_auto(query, limit)
    if source == "upload":
        return search_upload(query, limit)
    return search_demo(query, limit)


def get_paper(paper_id: str) -> dict | None:
    if paper_id in PAPERS_BY_ID:
        return PAPERS_BY_ID[paper_id]
    if paper_id in LIVE_PAPERS:
        return LIVE_PAPERS[paper_id]
    return UPLOAD_PAPERS.get(paper_id)


def all_papers() -> list[dict]:
    """内置语料与上传论文；在线检索结果需收藏进研究库后才会进入全库问答。"""
    ordered = list(PAPERS)
    seen = {paper.get("id") for paper in ordered}
    for paper in UPLOAD_PAPERS.values():
        paper_id = paper.get("id")
        if paper_id and paper_id not in seen:
            seen.add(paper_id)
            ordered.append(paper)
    return ordered


def register_upload(paper: dict) -> None:
    store.register_upload(paper)


def cache_dir() -> Path:
    return config.CACHE_DIR
