"""Concept graph for a paper scope.

The graph is deliberately conservative: nodes come from structured fields
(metrics, results, limitations) plus salient title/method terms, and every node
keeps the papers and quotes that produced it. It is a navigation aid, not a
claim about the literature.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from collections import OrderedDict

from . import analysis, ask

MAX_PAPERS = 20
MAX_PER_TYPE = 8
MAX_CO_OCCUR = 80
MAX_EDGES = 320
CACHE_MAX = 32

_CACHE: OrderedDict[str, dict] = OrderedDict()
_LOCK = threading.Lock()

_STOP = set(analysis._COMPARE_STOP) | {
    "ieee", "acm", "doi", "pdf", "arxiv", "abstract", "introduction", "conclusion",
    "figure", "table", "section", "appendix", "the", "this", "that", "these", "those",
    "we", "our", "their", "its", "they", "it", "is", "are", "be", "been", "being",
    "a", "an", "of", "to", "in", "on", "at", "by", "as", "or", "and", "for", "with",
    "from", "via", "under", "over", "between", "into", "than", "then", "so", "such",
}
_METHOD_SUFFIXES = (
    "tion", "sion", "ing", "ment", "ity", "ance", "ence", "sis", "ics",
    "ware", "based", "aware", "ization", "isation", "ability", "ibility",
)
_TASK_KEYS = (
    "数据集", "网络/任务", "任务", "负载", "场景", "data", "dataset",
    "task", "workload", "benchmark", "corpus",
)
_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,7}\b")
_WORD_RE = re.compile(r"\b[a-z][a-z-]{3,}\b")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _clean_label(text: str, limit: int = 28) -> str:
    label = re.sub(r"\s+", " ", str(text or "")).strip(" .;:,，；：")
    return label[:limit] + "…" if len(label) > limit else label


def _concepts(paper: dict, field: dict) -> list[tuple[str, str]]:
    """Return (label, evidence sentence) pairs for salient concepts."""
    title = str(paper.get("title") or "")
    method = str(field.get("method") or "")
    text = f"{title} {method}"
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(label: str) -> None:
        key = _norm(label)
        if len(key) < 3 or key in _STOP or key in seen:
            return
        if key.isdigit():
            return
        seen.add(key)
        found.append((_clean_label(label), analysis._clip(method or title, 160)))

    for match in _ACRONYM_RE.findall(text):
        add(match)
        if len(found) >= 2:
            break
    for match in _WORD_RE.findall(f"{title} {method}".lower()):
        if any(match.endswith(suffix) for suffix in _METHOD_SUFFIXES):
            add(match)
        if len(found) >= 5:
            break
    for match in re.findall(r"[A-Za-z][A-Za-z-]{3,}", title):
        if len(found) >= 5:
            break
        add(match)
    return found[:5]


def _tasks(paper: dict, field: dict) -> list[tuple[str, str]]:
    metrics = paper.get("metrics") or {}
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, value in metrics.items():
        if str(key).lower() not in _TASK_KEYS and str(key) not in _TASK_KEYS:
            continue
        text = str(value)
        for part in re.split(r"[/,;、，；|]+", text):
            part = re.split(r"[（(]", part)[0].strip()
            label = _clean_label(part, 30)
            if len(label) < 2 or label in {"未提及", "—", "-"}:
                continue
            if "摘要未给" in label or "未给任务" in label:
                continue
            if _norm(label) in seen:
                continue
            seen.add(_norm(label))
            found.append((label, f"{key}：{text}"[:160]))
            if len(found) >= 3:
                return found
    task_text = str(field.get("data_task") or "")
    for match in re.findall(r"\b[A-Z][A-Za-z0-9-]{2,}\b", task_text):
        label = _clean_label(match, 30)
        if _norm(label) in seen or _norm(label) in _STOP:
            continue
        seen.add(_norm(label))
        found.append((label, analysis._clip(task_text, 160)))
        if len(found) >= 4:
            break
    return found


def _metrics(paper: dict) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, value in (paper.get("metrics") or {}).items():
        if key in analysis._GENERIC_METRIC_KEYS or str(value).strip() in {"", "未提及"}:
            continue
        if str(key).lower() in _TASK_KEYS or str(key) in _TASK_KEYS:
            continue
        label = _clean_label(key, 24)
        if _norm(label) in seen:
            continue
        seen.add(_norm(label))
        found.append((label, f"{key}：{value}"[:160]))
        if len(found) >= 4:
            return found
    for item in (paper.get("results") or [])[:4]:
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if len(name) < 2 or _norm(name) in seen:
            continue
        seen.add(_norm(name))
        found.append((_clean_label(name, 24), f"{name}：{value}"[:160]))
    return found[:4]


def _limitations(paper: dict, field: dict) -> list[tuple[str, str]]:
    text = str(field.get("limitation") or "").strip()
    if (
        len(text) <= 20
        or text in {"未提及", "摘要未给出明确方法", "摘要未给出明确数据/任务"}
        or "规则抽取未识别到明确局限" in text
        or analysis._is_method_like(text)
    ):
        return []
    return [(_clean_label(text, 34), analysis._clip(text, 160))]


def _scope_papers(scope: str, paper_ids: list[str]) -> tuple[list[dict], str]:
    labels = {"all": "全部语料", "library": "我的研究库", "selected": "当前勾选论文"}
    papers = ask._scope_papers(scope, paper_ids)
    return papers[:MAX_PAPERS], labels.get(scope, scope)


def _cache_key(papers: list[dict], scope: str) -> str:
    # Mirror every input that can change the graph: title/method/task sentences
    # are derived from tldr + abstract + first sections, so all of them must be
    # part of the key or a late edit could return a stale graph. The topic is
    # only echoed in the response, so it stays out of the key and typing in the
    # query box never triggers a rebuild.
    payload = [
        {
            "id": paper.get("id"),
            "title": paper.get("title"),
            "tldr": str(paper.get("tldr") or "")[:400],
            "abstract": analysis.abstract_of(paper)[:1200],
            "metrics": paper.get("metrics") or {},
            "results": [
                {"name": item.get("name"), "value": item.get("value")}
                for item in (paper.get("results") or [])[:4]
            ],
            "limitations": paper.get("limitations") or [],
            "sections": [
                [section.get("title"), str(section.get("text") or "")[:600]]
                for section in (paper.get("sections") or [])[:3]
            ],
        }
        for paper in papers
    ]
    raw = json.dumps([scope, payload], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def build(scope: str = "all", paper_ids: list[str] | None = None, topic: str = "") -> dict:
    papers, scope_label = _scope_papers(scope, list(paper_ids or []))
    if not papers:
        return {
            "topic": topic,
            "scope": scope,
            "scope_label": scope_label,
            "stats": {"papers": 0, "nodes": 0, "edges": 0},
            "nodes": [],
            "edges": [],
            "papers": [],
            "notes": ["当前范围没有论文；请先检索、上传或收藏论文。"],
        }
    key = _cache_key(papers, scope)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
    if cached is not None:
        result = copy.deepcopy(cached)
        result["topic"] = topic
        return result

    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str], dict] = {}
    paper_nodes: list[dict] = []

    def add_node(
        node_type: str, label: str, paper: dict, quote: str, node_id: str | None = None
    ) -> str:
        node_id = node_id or f"{node_type}:{_norm(label)[:48]}"
        node = nodes.get(node_id)
        if node is None:
            node = {
                "id": node_id,
                "label": label,
                "type": node_type,
                "paper_count": 0,
                "papers": [],
                "evidence": [],
            }
            nodes[node_id] = node
        if paper.get("id") not in node["papers"]:
            node["papers"].append(paper.get("id"))
            node["paper_count"] = len(node["papers"])
            if quote and len(node["evidence"]) < 3:
                node["evidence"].append({"paper_id": paper.get("id"), "quote": quote})
        return node_id

    def add_edge(source: str, target: str, edge_type: str, paper: dict) -> None:
        if source == target:
            return
        sorted_pair = tuple(sorted((source, target)))
        edge = edges.get(sorted_pair)
        if edge is None:
            edge = {
                "source": source,
                "target": target,
                "type": edge_type,
                "weight": 0,
                "papers": [],
            }
            edges[sorted_pair] = edge
        if paper.get("id") not in edge["papers"]:
            edge["papers"].append(paper.get("id"))
            edge["weight"] = len(edge["papers"])

    for paper in papers:
        field = analysis._generic_fields(paper)
        paper_id = add_node(
            "paper",
            analysis._short_title(paper, 44),
            paper,
            analysis._clip(analysis.abstract_of(paper), 160),
            node_id=f"paper:{paper.get('id')}",
        )
        paper_nodes.append({
            "id": paper_id,
            "paper_id": paper.get("id"),
            "title": paper.get("title"),
            "year": paper.get("year"),
            "venue": paper.get("venue"),
        })
        concept_ids = []
        for label, quote in _concepts(paper, field):
            node_id = add_node("concept", label, paper, quote)
            add_edge(paper_id, node_id, "discusses", paper)
            concept_ids.append(node_id)
        for label, quote in _tasks(paper, field):
            node_id = add_node("task", label, paper, quote)
            add_edge(paper_id, node_id, "evaluates_on", paper)
            for concept_id in concept_ids:
                add_edge(concept_id, node_id, "co_occurs", paper)
        for label, quote in _metrics(paper):
            node_id = add_node("metric", label, paper, quote)
            add_edge(paper_id, node_id, "reports", paper)
            for concept_id in concept_ids:
                add_edge(concept_id, node_id, "co_occurs", paper)
        for label, quote in _limitations(paper, field):
            node_id = add_node("limitation", label, paper, quote)
            add_edge(paper_id, node_id, "limited_by", paper)

    order = {"paper": 0, "concept": 1, "task": 2, "metric": 3, "limitation": 4}
    ranked = sorted(
        nodes.values(),
        key=lambda node: (order.get(node["type"], 9), -node["paper_count"], node["label"]),
    )
    kept: dict[str, int] = {}
    selected_nodes: list[dict] = []
    counts: dict[str, int] = {}
    for node in ranked:
        node_type = node["type"]
        if node_type != "paper":
            if counts.get(node_type, 0) >= MAX_PER_TYPE:
                continue
            counts[node_type] = counts.get(node_type, 0) + 1
        selected_nodes.append(node)
        kept[node["id"]] = 0
    if len(selected_nodes) > MAX_PAPERS + MAX_PER_TYPE * 4:
        selected_nodes = selected_nodes[: MAX_PAPERS + MAX_PER_TYPE * 4]
        kept = {node["id"]: 0 for node in selected_nodes}

    selected_edges = [
        edge for edge in edges.values()
        if edge["source"] in kept and edge["target"] in kept
    ]
    co_occurs = sorted(
        (edge for edge in selected_edges if edge["type"] == "co_occurs"),
        key=lambda edge: (-edge["weight"], edge["source"], edge["target"]),
    )
    primary = sorted(
        (edge for edge in selected_edges if edge["type"] != "co_occurs"),
        key=lambda edge: (-edge["weight"], edge["type"], edge["source"], edge["target"]),
    )
    co_keep_budget = min(
        len(co_occurs),
        MAX_CO_OCCUR,
        max(0, MAX_EDGES - min(len(primary), MAX_EDGES)),
    )
    primary_keep = primary[:MAX_EDGES]
    co_keep = co_occurs[:co_keep_budget]
    truncated = len(primary_keep) < len(primary) or len(co_keep) < len(co_occurs)
    selected_edges = primary_keep + co_keep

    stats = {
        "papers": len(paper_nodes),
        "nodes": len(selected_nodes),
        "edges": len(selected_edges),
        "concepts": counts.get("concept", 0),
        "tasks": counts.get("task", 0),
        "metrics": counts.get("metric", 0),
        "limitations": counts.get("limitation", 0),
    }
    notes = [
        "节点来自论文标题/方法句、结构化指标、结果名与局限句；合并同名节点只代表共现，不代表结论一致。",
        "点击节点可查看来源论文与原文摘句；图谱用于导航阅读，不替代原文核对。",
    ]
    if stats["papers"] >= MAX_PAPERS:
        notes.append(f"范围超过 {MAX_PAPERS} 篇，图谱只展示前 {MAX_PAPERS} 篇。")
    if truncated:
        notes.append(
            f"关系过多，只保留权重最高的 {len(selected_edges)} 条边"
            f"（上限 {MAX_EDGES}，共现边最多 {MAX_CO_OCCUR} 条）。"
        )
    result = {
        "topic": topic,
        "scope": scope,
        "scope_label": scope_label,
        "stats": stats,
        "nodes": selected_nodes,
        "edges": selected_edges,
        "papers": paper_nodes,
        "notes": notes,
    }
    with _LOCK:
        _CACHE[key] = copy.deepcopy(result)
        _CACHE.move_to_end(key)
        while len(_CACHE) > CACHE_MAX:
            _CACHE.popitem(last=False)
    return result
