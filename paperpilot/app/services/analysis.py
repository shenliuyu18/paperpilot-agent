from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from collections import OrderedDict

from .. import config
from ..demo_data import GAPS
from . import glossary_store, llm_gateway

PROMPT_DIR = config.ROOT / "prompts"

_READ_CACHE: OrderedDict[str, dict] = OrderedDict()
_READ_LOCK = threading.Lock()
_READ_CACHE_MAX = 128
_LLM_READ_CACHE: OrderedDict[str, dict] = OrderedDict()
_LLM_READ_LOCK = threading.Lock()
_LLM_READ_CACHE_MAX = 64

# The user reads this in the paper card, so it names the reason instead of the exception class.
READ_DEGRADE_LABELS = {
    "budget": "该运行的模型调用预算已用尽",
    "rate": "供应商限流",
    "timeout": "模型响应超时",
    "schema": "模型返回无法解析为约定结构",
    "request": "请求被供应商拒绝",
    "unavailable": "模型服务不可达",
    "config": "未配置模型密钥",
}


def _read_cache_key(paper: dict, mode: str) -> str:
    payload = json.dumps(paper, ensure_ascii=False, sort_keys=True, default=str)
    signature = "|".join(
        [
            mode,
            str(config.LLM_ENABLED),
            config.LLM_BASE_URL if config.LLM_ENABLED else "",
            config.LLM_MODEL if config.LLM_ENABLED else "",
            payload,
        ]
    )
    return hashlib.sha1(signature.encode("utf-8")).hexdigest()


def _llm_read_cache_key(paper: dict) -> str:
    payload = {key: value for key, value in (paper or {}).items() if key != "_llm_read"}
    signature = "|".join(
        [
            "llm-read",
            str(config.LLM_ENABLED),
            config.LLM_BASE_URL if config.LLM_ENABLED else "",
            config.LLM_MODEL if config.LLM_ENABLED else "",
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        ]
    )
    return hashlib.sha1(signature.encode("utf-8")).hexdigest()


def clear_read_cache() -> None:
    """Drop every in-process side cache for model-derived results."""
    with _READ_LOCK:
        _READ_CACHE.clear()
    with _LLM_READ_LOCK:
        _LLM_READ_CACHE.clear()
    with _COMPARE_LOCK:
        _COMPARE_CACHE.clear()


_COMPARE_CACHE: OrderedDict[str, dict] = OrderedDict()
_COMPARE_LOCK = threading.Lock()
_COMPARE_CACHE_MAX = 64


def _compare_cache_key(papers: list[dict]) -> str:
    payload = []
    for paper in papers:
        payload.append({
            "id": paper.get("id"),
            "title": paper.get("title"),
            "year": paper.get("year"),
            "venue": paper.get("venue"),
            "abstract": abstract_of(paper)[:1200],
            "tldr": str(paper.get("tldr") or "")[:300],
            "extraction_level": paper.get("extraction_level"),
            "full_text": paper.get("full_text", False),
            "curated": paper.get("curated", False),
            "results": (paper.get("results") or [])[:4],
            "metrics": paper.get("metrics") or {},
            "limitations": (paper.get("limitations") or [])[:4],
            "sections": [
                [section.get("title"), str(section.get("text") or "")[:600]]
                for section in (paper.get("sections") or [])[:3]
            ],
            "evidence": [
                str(item.get("quote") or "")
                for item in (paper.get("evidence") or [])[:4]
            ],
        })
    signature = "|".join([
        str(config.LLM_ENABLED),
        config.LLM_BASE_URL if config.LLM_ENABLED else "",
        config.LLM_MODEL if config.LLM_ENABLED else "",
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
    ])
    return hashlib.sha1(signature.encode("utf-8")).hexdigest()


def clear_compare_cache() -> None:
    with _COMPARE_LOCK:
        _COMPARE_CACHE.clear()


def _prompt(name: str, fallback: str) -> str:
    path = PROMPT_DIR / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return fallback


ANALYST_PROMPT = _prompt(
    "analyst.md",
    "你是严谨的论文分析员。只根据给定文本抽取信息；每个结论必须附证据；"
    "找不到就写“未提及”；分开作者声称、实验证据和推断。输出 JSON。",
)

READ_SCHEMA = {
    "type": "object",
    # Models occasionally return a nested object or a list for a prose field;
    # _llm_read coerces and clips those values instead of rejecting the whole
    # answer. The object shape itself is enforced here.
    "properties": {},
}


TRANSLATE_SCHEMA = {
    "type": "object",
    "required": ["translation"],
    "properties": {
        "translation": {
            "type": "array",
            "max_items": 80,
            "items": {
                "type": "object",
                "required": ["en", "zh"],
                "properties": {
                    "en": {"type": "string", "max_length": 1200},
                    "zh": {"type": "string", "max_length": 1200},
                },
            },
        },
    },
}


COMPARE_PROMPT = _prompt(
    "critic.md",
    "你是论文评审与综合分析师。只根据给定表格做共识、分歧和风险总结；"
    "不得把不同测试条件的数字直接排序；输出 JSON。",
)

BUILDER_PROMPT = _prompt(
    "builder.md",
    "你是科研复现与教学转化助手。根据论文信息生成可执行但诚实的复现计划、"
    "代码骨架、组会提纲和 quiz；明确不确定项，输出 JSON。",
)


def _strip_abstract_heading(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    return re.sub(r"^(?:abstract|摘要)\s*[:：.\-—]?\s*", "", cleaned, flags=re.I)


def abstract_of(paper: dict) -> str:
    if paper.get("abstract"):
        return _strip_abstract_heading(paper["abstract"])
    for section in paper.get("sections") or []:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").lower()
        if title.startswith("abstract"):
            return _strip_abstract_heading(section.get("text") or "")
    if paper.get("sections"):
        first = paper["sections"][0]
        return _strip_abstract_heading(first.get("text") if isinstance(first, dict) else "")
    return ""


def brief(paper: dict) -> dict:
    results = paper.get("results", [])[:2]
    highlights = [
        f"{item.get('name', '指标')}：{item.get('value', '未提及')}"
        for item in results
    ]
    metrics = paper.get("metrics", {})
    preview = list(metrics.items())[:3]
    return {
        "id": paper.get("id"),
        "title": paper.get("title", "Untitled"),
        "authors": paper.get("authors", []),
        "year": paper.get("year"),
        "venue": paper.get("venue"),
        "source": paper.get("source"),
        "source_label": paper.get("source_label"),
        "url": paper.get("url"),
        "pdf_url": paper.get("pdf_url"),
        "doi": paper.get("doi"),
        "cached": paper.get("cached", False),
        "cited_by_count": paper.get("cited_by_count"),
        "tldr": _coerce_text(paper.get("tldr"), 600) or _first_sentences(abstract_of(paper), 2),
        "demo": paper.get("demo", False),
        "full_text": paper.get("full_text", False),
        "extraction_level": paper.get("extraction_level", "摘要级"),
        "highlights": highlights,
        "metrics_preview": [{"key": key, "value": str(value)} for key, value in preview],
        "relevance": paper.get("relevance"),
        "layer": paper.get("layer"),
        "layer_reason": paper.get("layer_reason"),
        "source_type": paper.get("source_type"),
        "concept_coverage": paper.get("concept_coverage"),
        "credibility": paper.get("credibility"),
        "aggregate": paper.get("aggregate", False),
        "aggregate_fallback": paper.get("aggregate_fallback", False),
        "aggregate_sources": paper.get("aggregate_sources", []),
    }


def _text_for_llm(paper: dict, limit: int = 12000) -> str:
    chunks = [_coerce_text(paper.get("title"), 500), abstract_of(paper)]
    for section in (paper.get("sections") or [])[:8]:
        if isinstance(section, dict):
            chunks.append(
                f"\n## {_coerce_text(section.get('title'), 200)}\n"
                f"{_coerce_text(section.get('text'), 4000)}"
            )
    return "\n".join(chunk for chunk in chunks if chunk)[:limit]


_READ_PRIMARY_KEYS = frozenset({
    "text", "summary", "description", "content", "claim", "point", "contribution",
    "limitation", "issue", "weakness", "question", "problem", "method", "answer",
})
_READ_SECONDARY_KEYS = frozenset({"value", "title", "label", "name"})
_READ_METADATA_KEYS = frozenset({
    "confidence", "evidence", "source", "type", "id", "status", "unit",
    "page", "section", "reason", "why", "basis", "category",
})
_INTERNAL_LABEL_RE = re.compile(r"^\s*【[^】]{1,16}】\s*")


def _dict_text(value: dict, limit: int, depth: int) -> str:
    candidates: list[tuple[int, int, int, str]] = []
    for index, (key, item) in enumerate(list(value.items())[:64]):
        if isinstance(item, bool) or not isinstance(item, (str, int, float, dict, list)):
            continue
        normalized_key = str(key or "").strip().lower()
        if normalized_key in _READ_METADATA_KEYS:
            tier = 3
        elif normalized_key in _READ_PRIMARY_KEYS:
            tier = 0
        elif normalized_key in _READ_SECONDARY_KEYS:
            tier = 2
        else:
            tier = 1
        if isinstance(item, (bool, int, float)) and depth >= 3:
            continue
        candidate = _coerce_text(item, min(limit, 2000), depth + 1).strip()
        if candidate:
            candidates.append((tier, -len(candidate), index, candidate))
    return min(candidates)[3] if candidates else ""


def _coerce_text(value, limit: int, _depth: int = 0) -> str:
    if isinstance(value, str):
        text = value
    elif isinstance(value, bool):
        text = "是" if value else "否"
    elif isinstance(value, (int, float)):
        text = str(value)
    elif isinstance(value, (list, tuple)):
        text = "；".join(
            item for item in (_coerce_text(entry, limit, _depth + 1) for entry in list(value)[:32])
            if item
        )
    elif isinstance(value, dict) and _depth < 4:
        text = _dict_text(value, limit, _depth)
    else:
        text = ""
    text = _INTERNAL_LABEL_RE.sub("", " ".join(text.split()))
    return text[:limit]


def _coerce_list(value, limit: int = 6) -> list[str]:
    if isinstance(value, list):
        items = [
            item for item in (_coerce_text(entry, 300).strip() for entry in value[:64]) if item
        ]
    elif isinstance(value, str) and value.strip():
        items = [value.strip()]
    elif isinstance(value, dict):
        items = [item for item in [_coerce_text(value, 300).strip()] if item]
    else:
        items = []
    return [item[:300] for item in items[:limit]]


def coerce_text(value, limit: int) -> str:
    return _coerce_text(value, limit)


def _normalize_read_fields(data: dict | None) -> dict:
    if not isinstance(data, dict):
        return {}
    return {
        "tldr": _coerce_text(data.get("tldr"), 600),
        "problem": _coerce_text(data.get("problem"), 600),
        "method": _coerce_text(data.get("method"), 800),
        "novelty": _coerce_list(data.get("novelty")),
        "limitations": _coerce_list(data.get("limitations")),
        "reviewer_questions": _coerce_list(data.get("reviewer_questions")),
    }


def _llm_read(paper: dict) -> tuple[dict | None, str]:
    """Return ``(fields, degrade_reason)``.

    The reason matters to the caller: a model that was configured and failed this time must not
    have its rule-based fallback cached under a model-enabled key, because the user could then
    raise their budget and still be served offline text.
    """
    if not config.LLM_ENABLED:
        return None, ""
    cache_key = _llm_read_cache_key(paper)
    with _LLM_READ_LOCK:
        cached = _LLM_READ_CACHE.get(cache_key)
        if cached is not None:
            _LLM_READ_CACHE.move_to_end(cache_key)
            return copy.deepcopy(cached), ""
    user = (
        "请分析以下论文文本，输出 JSON：\n"
        "{\n"
        '  "tldr": "三句话以内",\n'
        '  "problem": "要解决的问题",\n'
        '  "method": "核心方法",\n'
        '  "novelty": ["创新点1", "创新点2"],\n'
        '  "limitations": ["局限1", "局限2"],\n'
        '  "reviewer_questions": ["问题1", "问题2", "问题3"],\n'
        '  "section_notes": [{"title": "章节", "page": 1, "note": "中文解读"}]\n'
        "}\n\n论文文本：\n" + _text_for_llm(paper)
    )
    data, meta = llm_gateway.audited_chat_json(
        ANALYST_PROMPT, user, purpose="read", schema=READ_SCHEMA,
        temperature=0.2, max_tokens=16384, use_cache=True,
    )
    normalized = _normalize_read_fields(data)
    if not any(normalized.get(key) for key in ("tldr", "problem", "method")):
        return None, str(meta.get("degrade_reason") or "schema")
    with _LLM_READ_LOCK:
        _LLM_READ_CACHE[cache_key] = copy.deepcopy(normalized)
        _LLM_READ_CACHE.move_to_end(cache_key)
        while len(_LLM_READ_CACHE) > _LLM_READ_CACHE_MAX:
            _LLM_READ_CACHE.popitem(last=False)
    return copy.deepcopy(normalized), ""


def _first_sentences(text: str, count: int = 3) -> str:
    parts = re.split(r"(?<=[.!?。！？])\s+", " ".join((text or "").split()))
    return " ".join(parts[:count]).strip()


def _clip(text: str, limit: int = 420) -> str:
    text = " ".join((text or "").split())
    return text[:limit] + "…" if len(text) > limit else text


def _sentences(text: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"(?<=[.!?。！？])\s+", " ".join((text or "").split()))
        if len(item.strip()) > 24
    ]


def _heuristic_fields(paper: dict) -> dict:
    abstract = abstract_of(paper)
    sections = paper.get("sections", [])
    intro = next(
        (section.get("text", "") for section in sections if "introduction" in section.get("title", "").lower()),
        "",
    )
    body_text = " ".join(section.get("text", "") for section in sections) or abstract
    abstract_sentences = _sentences(abstract)
    body_sentences = _sentences(body_text)
    intro_sentences = _sentences(intro)
    problem_pool = intro_sentences or abstract_sentences or body_sentences
    problem = next(
        (
            sentence
            for sentence in problem_pool[:24]
            if any(
                key in sentence.lower()
                for key in ("challenge", "bottleneck", "however", "limited", "sensitive", "problem")
            )
        ),
        problem_pool[0] if problem_pool else "未提及",
    )
    method_keys = (
        "we propose", "we present", "this work presents", "we introduce",
        "our approach", "this paper presents", "we develop",
    )
    method = (
        _pick_best(
            abstract_sentences + body_sentences,
            _METHOD_MARKERS,
            boost=_METHOD_STRONG,
            penalty=_RESULT_SIGNAL,
        )
        or next(
            (
                sentence
                for sentence in (abstract_sentences + body_sentences)
                if any(key in sentence.lower() for key in method_keys)
            ),
            "",
        )
    )
    if not method or method == problem or method[:60] == problem[:60]:
        method = "摘要未给出明确方法"
    bullets = [
        _clip(match.strip(), 280)
        for match in re.findall(r"[•●]\s*([^\n]+)", body_text)
        if len(match.strip()) > 24
    ]
    novelty = bullets[:3] or ([_clip(method, 280)] if method != "摘要未给出明确方法" else [])
    limitations: list[str] = []
    for sentence in body_sentences:
        low = sentence.lower()
        if _is_method_like(sentence):
            continue
        if any(
            key in low
            for key in ("limitation", "future work", "outside the present model", "not evaluated", "not considered")
        ):
            limitations.append(_clip(sentence, 280))
        if len(limitations) >= 3:
            break
    return {
        "problem": _clip(problem),
        "method": _clip(method),
        "novelty": novelty,
        "limitations": limitations,
        "reviewer_questions": [
            "论文的核心结论由哪类证据支撑：实验、仿真、用户研究还是理论分析？",
            "与 baseline 的测试条件、数据集和指标口径是否一致？",
            "哪些假设在更大规模、不同数据或不同负载下可能失效？",
        ],
    }


def read_paper(paper: dict, mode: str = "5min") -> dict:
    cache_key = _read_cache_key(paper, mode)
    with _READ_LOCK:
        cached = _READ_CACHE.get(cache_key)
        if cached is not None:
            _READ_CACHE.move_to_end(cache_key)
    if cached is not None:
        return copy.deepcopy(cached)
    curated = bool(paper.get("curated"))
    llm_data, read_reason = (None, "") if curated else _llm_read(paper)
    heuristic = _heuristic_fields(paper) if not curated and not llm_data else None
    abstract = abstract_of(paper)
    raw_tldr = _strip_abstract_heading(_coerce_text(paper.get("tldr"), 1200))
    if llm_data and llm_data.get("tldr"):
        tldr = llm_data["tldr"]
    elif raw_tldr and not (abstract and raw_tldr[:40] and raw_tldr[:40] in abstract):
        tldr = raw_tldr
    else:
        tldr = _first_sentences(abstract, 3) or "未生成摘要。"
    sections = paper.get("sections", [])
    if not sections and abstract:
        sections = [{"title": "Abstract", "page": 1, "text": abstract}]
    if mode == "tldr":
        sections = sections[:2]
    elif mode == "5min":
        sections = sections[:6]
    extraction_mode = "curated" if curated else ("llm" if llm_data else "heuristic")
    if extraction_mode == "curated":
        note = "内置演示语料：展示已解析的原文节选与人工校订的结构化字段。"
    elif extraction_mode == "llm":
        note = "已使用配置的模型进行结构化解读；数值仍以原文证据为准。"
    elif read_reason:
        note = (f"已配置模型，但本次结构化解读未完成（{READ_DEGRADE_LABELS.get(read_reason, read_reason)}），"
                "已回退规则抽取；本次结果未缓存，稍后可重试获得模型解读。")
    else:
        note = "未配置模型：当前使用规则抽取（问题/方法/贡献/局限）展示摘要或全文节选；配置模型后可获得更细的字段与翻译。"
    result = {
        "paper": brief(paper),
        "mode": mode,
        "tldr": tldr,
        "abstract": abstract_of(paper),
        "problem": (llm_data or {}).get("problem")
        or (paper.get("problem") if curated else (heuristic or {}).get("problem", "未提及")),
        "method": (llm_data or {}).get("method")
        or (paper.get("method") if curated else (heuristic or {}).get("method", "未提及")),
        "novelty": (llm_data or {}).get("novelty")
        or (paper.get("novelty") if curated else (heuristic or {}).get("novelty", [])),
        "limitations": (llm_data or {}).get("limitations")
        or paper.get("limitations")
        or (heuristic or {}).get("limitations", []),
        "reviewer_questions": (llm_data or {}).get("reviewer_questions")
        or (paper.get("reviewer_questions") if curated else (heuristic or {}).get("reviewer_questions", [])),
        "sections": sections,
        "figures": paper.get("figures", []),
        "metrics": paper.get("metrics", {}),
        "results": paper.get("results", []),
        "evidence": paper.get("evidence", []),
        "translation": paper.get("translation", []),
        "full_text": paper.get("full_text", False),
        "extraction_level": paper.get("extraction_level", "摘要级"),
        "extraction_mode": extraction_mode,
        "llm_enriched": bool(llm_data),
        "note": note,
    }
    for key, text_limit in (("tldr", 1200), ("problem", 600), ("method", 800)):
        result[key] = _coerce_text(result.get(key), text_limit)
    for key in ("novelty", "limitations", "reviewer_questions"):
        result[key] = _coerce_list(result.get(key))
    if not read_reason:
        # Only cache a result that reflects this key's configuration. Caching a degraded one
        # would keep serving rule text under a key that says a model was configured, and the
        # user could raise their budget and still get the offline fields back.
        with _READ_LOCK:
            _READ_CACHE[cache_key] = copy.deepcopy(result)
            _READ_CACHE.move_to_end(cache_key)
            while len(_READ_CACHE) > _READ_CACHE_MAX:
                _READ_CACHE.popitem(last=False)
    return result


def translate(paper: dict | None = None, text: str | None = None) -> dict:
    glossary_hits = [
        {"term": term, "zh": zh}
        for term, zh in glossary_store.merged_glossary().items()
        if text and term.lower() in text.lower()
    ]
    glossary_hits.sort(key=lambda item: (-len(item["term"]), item["term"]))
    glossary_hits = glossary_hits[:30]
    if paper and paper.get("translation"):
        return {
            "translation": paper["translation"],
            "glossary_hits": glossary_hits,
            "source": "builtin-bilingual",
            "note": "来自内置双语对照，公式、引用与数值保持原文。",
        }
    if not config.LLM_ENABLED:
        if paper and paper.get("source") == "upload":
            note = "离线模式不生成机器翻译。这篇本地 PDF 没有人工双语对照；在“模型设置”配置模型后可翻译。"
        else:
            note = "离线模式不生成机器翻译。演示论文已提供人工双语对照；配置 LLM 后可翻译任意文本。"
        return {
            "translation": [],
            "glossary_hits": glossary_hits,
            "source": "offline",
            "note": note,
        }
    content = text or (abstract_of(paper) if paper else "")
    if not content:
        return {"translation": [], "glossary_hits": [], "source": "offline", "note": "没有可翻译的文本。"}
    glossary_text = "；".join(f"{item['term']}→{item['zh']}" for item in glossary_hits) or "无"
    data, _ = llm_gateway.audited_chat_json(
        "你是集成电路领域的学术翻译。保持公式、数字、引用不变；术语使用给定词表；"
        "输出 JSON {\"translation\":[{\"en\":\"原文\",\"zh\":\"译文\"}]}。",
        f"术语表：{glossary_text}\n\n文本：\n{content[:6000]}",
        purpose="translation", schema=TRANSLATE_SCHEMA,
        temperature=0.05, max_tokens=2200, use_cache=True,
    )
    if isinstance(data, dict) and data.get("translation"):
        return {
            "translation": data.get("translation", []),
            "glossary_hits": glossary_hits,
            "source": "llm",
            "note": "机器翻译结果，建议对照原文核对术语与数值。",
        }
    return {
        "translation": [],
        "glossary_hits": glossary_hits,
        "source": "error",
        "note": "模型未能返回可用译文；请检查模型设置或稍后重试。",
    }


_COMPARE_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were",
    "have", "has", "had", "not", "but", "than", "then", "also", "into", "our",
    "their", "its", "which", "these", "those", "using", "use", "used", "based",
    "can", "may", "will", "would", "could", "should", "paper", "study", "work",
    "results", "result", "method", "methods", "approach", "data", "model",
    "models", "system", "systems", "performance", "propose", "proposed",
    "present", "presents", "propose", "proposes", "architecture", "show", "shows",
    "provide", "provides", "however", "information", "demonstrate",
    "demonstrates", "demonstrated", "evaluate", "evaluates", "evaluated",
    "improve", "improves", "improved", "large", "language", "various",
    "therefore", "different", "other", "such", "more", "most", "between",
    "研究", "论文", "本文", "该文", "作者", "工作", "方法", "结果", "实验",
    "数据", "模型", "系统", "问题", "提出", "分析", "使用", "基于", "不同",
    "主要", "其中", "这些", "已经", "没有", "需要", "能够", "表明", "显示",
    "可以", "进行", "一个", "一种", "以及", "通过", "对于", "如果", "由于",
    "相关", "方面", "然而", "因此", "我们", "他们", "具有", "实现", "提供",
    "包括", "同时", "目前", "进一步", "仍然", "可能", "无法", "未提及",
}

_QUESTION_MARKERS = (
    "?", "？", "we study", "we investigate", "we ask", "this paper",
    "本文", "研究问题", "旨在", "目的是", "探讨",
)
_METHOD_MARKERS = (
    "we propose", "we present", "we introduce", "we develop", "we design",
    "propose", "present", "introduce", "develop",
    "method", "framework", "architecture", "algorithm", "approach", "design",
    "提出", "方法", "架构", "框架", "算法", "设计", "机制", "策略",
)
_TASK_MARKERS = (
    "dataset", "benchmark", "evaluation", "experiment", "task", "workload",
    "corpus", "数据集", "任务", "基准", "评估", "实验", "语料", "场景",
)
_RESULT_MARKERS = (
    "we achieve", "results show", "improves", "reduces", "outperforms",
    "achieves", "accuracy", "latency", "throughput", "达到", "提升", "降低",
    "优于", "降低", "减少", "增加", "%", "×", "倍", "毫秒",
)
_RESULT_SIGNAL = (
    "reduces", "improves", "achieves", "outperforms", "improvement",
    "提升", "降低", "达到", "减少", "增加", "优于", "%", "倍",
)
_METHOD_STRONG = (
    "we propose", "we present", "we introduce", "we develop", "we design",
    "this paper proposes", "this paper presents", "this paper introduces",
    "proposes", "presents", "introduces", "提出", "设计", "构建",
)
_LIMITATION_MARKERS = (
    "limitation", "struggle", "risk", "overhead", "challenge", "fails",
    "fail to", "drawback", "bottleneck", "局限", "风险", "挑战", "不足",
    "瓶颈", "代价", "开销", "尚未", "难以",
)
_LIMITATION_STRONG = (
    "limitation", "future work", "not evaluated", "not considered",
    "outside the present", "drawback", "bottleneck", "struggle", "overhead",
    "局限", "尚未", "难以", "不足", "代价", "开销",
)
_METHOD_LIKE = (
    "we propose", "we present", "we introduce", "we develop", "we design",
    "our approach", "this paper proposes", "this paper presents",
    "to address these limitations", "to address this limitation",
    "提出", "本文提出", "我们提出",
)


def _is_method_like(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in _METHOD_LIKE)


def _is_extraction_note(text: str) -> bool:
    return "规则抽取未识别到明确局限" in str(text or "")


def _pick_limitation_sentence(sentences: list[str]) -> str:
    for sentence in sentences:
        if _is_method_like(sentence):
            continue
        lowered = sentence.lower()
        if any(marker in lowered for marker in _LIMITATION_STRONG):
            return sentence
    return ""


_GENERIC_METRIC_KEYS = {
    "被引", "类型", "来源", "页数", "字符数", "标题", "作者", "年份", "数据集",
    "venue", "DOI", "doi", "citation", "cited", "dataset", "datasets",
}
_TASK_GENERIC = {
    "dataset", "datasets", "benchmark", "benchmarks", "evaluation", "experiment",
    "experiments", "task", "tasks", "corpus", "workload", "performance",
    "摘要", "摘要未给出明确数据", "明确数据", "数据集", "任务", "基准",
    "评估", "实验", "语料", "场景", "未提及",
}


def _short_title(paper: dict, limit: int = 52) -> str:
    title = " ".join(str(paper.get("title") or "未命名论文").split())
    return title[:limit] + "…" if len(title) > limit else title


def _pick_sentence(sentences: list[str], markers: tuple[str, ...]) -> str:
    for sentence in sentences:
        lowered = sentence.lower()
        if any(marker in lowered for marker in markers):
            return sentence
    return ""


def _pick_best(
    sentences: list[str],
    markers: tuple[str, ...],
    boost: tuple[str, ...] = (),
    penalty: tuple[str, ...] = (),
) -> str:
    """Pick the best matching sentence instead of the first weak hit.

    Strong markers (``boost``) weigh double, while result-signal words
    (``penalty``) subtract, so a results sentence is not mistaken for the
    method description.
    """
    best = ""
    best_score = 0
    for sentence in sentences:
        lowered = sentence.lower()
        score = sum(2 if marker in boost else 1 for marker in markers if marker in lowered)
        if not score:
            continue
        score -= sum(1 for marker in penalty if marker in lowered)
        if score > best_score:
            best, best_score = sentence, score
    return best


def _limitation_clause(sentence: str) -> str:
    """Keep only the contrast clause when a result sentence also states a limitation."""
    sentence = str(sentence or "").strip()
    if not sentence:
        return ""
    lowered = sentence.lower()
    for marker in (
        " but ", " however, ", " however ", ", however ", "; however ",
        "然而，", "但是", "但",
    ):
        index = lowered.find(marker)
        if index <= 0:
            continue
        tail = sentence[index + len(marker):].strip()
        if tail and any(item in tail.lower() for item in _LIMITATION_MARKERS):
            return tail
    return sentence


def _tldr_part(paper: dict, abstract: str) -> str:
    """Return the TLDR only when it adds content beyond the abstract.

    Online sources often store ``tldr`` as a truncated copy of the abstract.
    Concatenating that fragment with the full abstract creates a broken
    pseudo-sentence (the cut-off tail merges with the next real sentence), so
    it is skipped here; a genuinely different summary is kept with a hard
    sentence boundary.
    """
    tldr = _strip_abstract_heading(str(paper.get("tldr") or ""))
    if not tldr:
        return ""
    if abstract and tldr[:40] and tldr[:40] in abstract:
        return ""
    return tldr + "。"


def _compare_text(paper: dict) -> str:
    abstract = abstract_of(paper)
    parts = [str(paper.get("title") or ""), _tldr_part(paper, abstract), abstract]
    for section in (paper.get("sections") or [])[:6]:
        parts.append(str(section.get("text") or ""))
    for item in (paper.get("results") or [])[:6]:
        parts.append(f"{item.get('name') or ''} {item.get('value') or ''}")
    for item in (paper.get("limitations") or [])[:4]:
        parts.append(str(item))
    for item in (paper.get("evidence") or [])[:6]:
        parts.append(str(item.get("quote") or ""))
    return " ".join(part for part in parts if part).strip()[:8000]


_COMPARE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]*|[\u4e00-\u9fff]+")


def _add_english_token(tokens: set[str], part: str) -> None:
    part = part.strip("._-")
    if len(part) < 3 or part in _COMPARE_STOP:
        return
    tokens.add(part)
    # Normalize simple plurals so "agents" and "agent" match.
    if part.endswith("ies") and len(part) > 5:
        tokens.add(part[:-3] + "y")
    elif (
        part.endswith("s")
        and len(part) > 4
        and not part.endswith(("ss", "is", "us"))
    ):
        tokens.add(part[:-1])


def _compare_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in _COMPARE_TOKEN_RE.finditer(str(text or "").lower()):
        part = match.group(0)
        if part[0].isascii():
            # Split compounds such as "long-document" or "key-value".
            for piece in re.split(r"[._-]+", part):
                _add_english_token(tokens, piece)
        elif len(part) == 1:
            continue
        else:
            for index in range(len(part) - 1):
                term = part[index:index + 2]
                if term not in _COMPARE_STOP:
                    tokens.add(term)
    return tokens


def _generic_fields(paper: dict) -> dict:
    abstract = abstract_of(paper) or ""
    tldr = str(paper.get("tldr") or "")
    body = " ".join(
        [_tldr_part(paper, abstract), abstract]
        + [str(section.get("text") or "") for section in (paper.get("sections") or [])[:3]]
    )
    sentences = _sentences(body)
    question = next(
        (sentence for sentence in sentences if sentence.rstrip().endswith(("?", "？"))),
        "",
    ) or _pick_sentence(sentences, _QUESTION_MARKERS) or tldr or (sentences[0] if sentences else "未提及")
    method = (
        _pick_best(sentences, _METHOD_MARKERS, boost=_METHOD_STRONG, penalty=_RESULT_SIGNAL)
        or _pick_sentence(sentences, _METHOD_MARKERS)
        or "摘要未给出明确方法"
    )
    task = _pick_sentence(sentences, _TASK_MARKERS) or "摘要未给出明确数据/任务"
    results = paper.get("results") or []
    key_result = "；".join(
        f"{item.get('name', '指标')} {item.get('value', '未提及')}"
        for item in results[:2]
    ) or _pick_sentence(sentences, _RESULT_MARKERS) or "摘要未给出明确量化结果"
    limitations = [
        str(item).strip()
        for item in (paper.get("limitations") or [])
        if str(item).strip() and not _is_method_like(item) and not _is_extraction_note(item)
    ]
    limitation = limitations[0] if limitations else (
        _limitation_clause(_pick_limitation_sentence(sentences)) or "未提及"
    )
    evidence = next(
        (str(item.get("quote") or "") for item in (paper.get("evidence") or []) if item.get("quote")),
        "",
    ) or abstract[:180] or "未提及"
    return {
        "research_question": _clip(question, 260),
        "method": _clip(method, 260),
        "data_task": _clip(task, 260),
        "key_result": _clip(key_result, 260),
        "limitation": _clip(limitation, 220),
        "evidence": _clip(evidence, 220),
    }


def _domain_metric_keys(papers: list[dict]) -> list[str]:
    counts: dict[str, int] = {}
    for paper in papers:
        for key, value in (paper.get("metrics") or {}).items():
            if key in _GENERIC_METRIC_KEYS or str(value).strip() in {"", "未提及"}:
                continue
            counts[key] = counts.get(key, 0) + 1
    threshold = max(2, (len(papers) + 1) // 2)
    keys = [key for key, count in counts.items() if count >= threshold]
    keys.sort(key=lambda key: (-counts[key], key))
    return keys[:3]


def _meaningful_metric(value: object) -> bool:
    return str(value).strip() not in {"", "未提及", "None", "[]", "{}"}


def _shared_terms(left: set[str], right: set[str], limit: int = 3) -> list[str]:
    common = [
        term for term in left & right
        if len(term) >= 3 and term not in _TASK_GENERIC and term not in _COMPARE_STOP
    ]
    common.sort(key=lambda term: (-len(term), term))
    return common[:limit]


def _comparability(papers: list[dict], fields: list[dict], token_sets: list[set[str]]) -> list[dict]:
    """Judge each paper pair from shared metrics, task terms and topic terms."""
    items: list[dict] = []
    for left_index in range(len(papers)):
        for right_index in range(left_index + 1, len(papers)):
            left = papers[left_index]
            right = papers[right_index]
            left_metrics = left.get("metrics") or {}
            right_metrics = right.get("metrics") or {}
            shared_metrics = [
                key for key, value in left_metrics.items()
                if key in right_metrics
                and key not in _GENERIC_METRIC_KEYS
                and _meaningful_metric(value)
                and _meaningful_metric(right_metrics.get(key))
            ]
            shared_task = _shared_terms(
                _compare_tokens(fields[left_index]["data_task"]),
                _compare_tokens(fields[right_index]["data_task"]),
                limit=3,
            )
            shared_topics = _shared_terms(token_sets[left_index], token_sets[right_index], limit=4)
            comparable = bool(shared_metrics or shared_task or len(shared_topics) >= 4)
            if shared_metrics:
                confidence = "high"
            elif shared_task or len(shared_topics) >= 6:
                confidence = "medium"
            else:
                confidence = "low"

            differences: list[str] = []
            left_year, right_year = left.get("year"), right.get("year")
            if isinstance(left_year, int) and isinstance(right_year, int) and abs(left_year - right_year) > 2:
                differences.append(f"年份相差 {abs(left_year - right_year)} 年")
            left_level = str(left.get("extraction_level") or "")
            right_level = str(right.get("extraction_level") or "")
            if left_level and right_level and left_level != right_level:
                differences.append(f"证据层级不同（{left_level} / {right_level}）")
            if not shared_metrics:
                differences.append("没有共同的结构化指标")

            shared_labels = [f"指标：{key}" for key in shared_metrics[:2]]
            shared_labels += [f"主题：{term}" for term in shared_topics[:3]]
            if comparable:
                reason = (
                    f"在 {'、'.join(shared_labels[:3])} 上可以对照；"
                    "数值仍需核对测试条件与指标口径。"
                )
            else:
                reason = (
                    "未发现共同指标、任务或足够的主题词，"
                    f"仅共享 {len(shared_topics)} 个泛化词；建议按不同研究方向分别阅读。"
                )
            items.append({
                "paper_ids": [left.get("id"), right.get("id")],
                "titles": [_short_title(left, 40), _short_title(right, 40)],
                "comparable": comparable,
                "confidence": confidence,
                "shared": shared_labels,
                "differences": differences,
                "reason": reason,
            })
    return items


_DIVERGENCE_STOP = {
    "rate", "rates", "ten", "one", "two", "three", "four", "five", "six",
    "first", "second", "third", "distinct", "previously", "earlier", "certain",
    "several", "many", "much", "less", "least", "higher", "lower", "large",
    "approximately", "configuration", "configurations", "methodological",
    "methodologically", "significantly", "comparison", "comparisons",
}


def _offline_compare_sections(
    papers: list[dict],
    rows: list[dict],
    fields: list[dict],
    token_sets: list[set[str]],
    texts: list[str],
) -> dict:
    df: dict[str, int] = {}
    for tokens in token_sets:
        for token in tokens:
            df[token] = df.get(token, 0) + 1
    comparability = _comparability(papers, fields, token_sets)
    shared = sorted(
        (
            token for token, count in df.items()
            if count >= 2 and len(token) > 1 and token not in _TASK_GENERIC
        ),
        key=lambda token: (-df[token], -len(token), token),
    )

    consensus: list[str] = []
    for key in _domain_metric_keys(papers):
        reported = [
            (paper, str((paper.get("metrics") or {}).get(key)))
            for paper in papers
            if _meaningful_metric((paper.get("metrics") or {}).get(key))
        ]
        if len(reported) < 2:
            continue
        values = "；".join(
            f"《{_short_title(paper, 30)}》={value}" for paper, value in reported[:3]
        )
        consensus.append(
            f"{len(reported)} 篇报告了「{key}」：{values}。指标口径与测试条件未统一，不能直接按大小排序。"
        )
        if len(consensus) >= 2:
            break

    if not consensus and shared:
        top = shared[:3]
        consensus.append(
            f"所选论文共同涉及「{'」「'.join(top)}」等主题（至少 {df[top[0]]}/{len(papers)} 篇）。"
        )
    if not consensus:
        consensus.append(
            "所选论文之间没有可自动识别的共同指标或任务；它们可能属于不同研究方向，建议人工阅读摘要后再判断可比性。"
        )

    divergence: list[str] = []
    seen_limitations: set[str] = set()
    for paper, row in zip(papers, rows):
        limitation = str(((row.get("values") or {}).get("limitation")) or "").strip()
        if (
            len(limitation) <= 20
            or limitation in seen_limitations
            or limitation in {"未提及", "摘要未给出明确方法", "摘要未给出明确数据/任务"}
        ):
            continue
        seen_limitations.add(limitation)
        divergence.append(f"《{_short_title(paper, 36)}》的局限：{_clip(limitation, 120)}")
        if len(divergence) >= 3:
            break
    if not divergence:
        for paper, text, tokens in zip(papers, texts, token_sets):
            title_tokens = _compare_tokens(paper.get("title") or "")
            title_candidates = [
                token for token in title_tokens
                if df.get(token, 0) == 1
                and len(token) >= 5
                and token not in _DIVERGENCE_STOP
            ]
            abstract_candidates = [
                token for token in tokens
                if df.get(token, 0) == 1
                and len(token) >= 5
                and not token.isdigit()
                and not any(char.isdigit() for char in token)
                and token not in _DIVERGENCE_STOP
            ]
            candidates = list(dict.fromkeys(title_candidates + abstract_candidates))
            candidates.sort(key=lambda token: (-len(token), -text.count(token), token))
            if candidates:
                labels = "、".join(f"「{token}」" for token in candidates[:3])
                divergence.append(f"《{_short_title(paper)}》更关注 {labels}。")
    if not divergence:
        divergence.append("所选论文的研究重点差异不明显，或摘要信息不足；建议阅读全文后再判断。")

    warnings = [
        "不同论文的数据集、评价指标与测试条件可能不同，表中数字不可直接比较或按大小排序。",
        "“未提及”表示当前抽取材料（摘要或全文节选）没有给出该字段，不代表论文没有做。",
    ]
    years = sorted({str(paper.get("year")) for paper in papers if paper.get("year")})
    venues = {str(paper.get("venue") or "") for paper in papers if paper.get("venue")}
    if len(years) > 1:
        warnings.append("所选论文年份跨度较大（" + " / ".join(years) + "），实验条件与 baseline 可能不同。")
    if len(venues) > 1:
        warnings.append("所选论文来自不同期刊/会议/预印本来源，审稿与验证强度可能不同。")
    if any("摘要" in str(paper.get("extraction_level") or "") for paper in papers):
        warnings.append("部分论文只有摘要级信息，方法、结果与局限的覆盖不完整。")
    if comparability and not any(item["comparable"] for item in comparability):
        warnings.append("所选论文两两之间都未发现共同指标，建议把它们当作不同方向分别阅读，而不是强行对比。")

    gaps: list[dict] = []
    seen_questions: set[str] = set()
    for paper, row in zip(papers, rows):
        extracted = str(((row.get("values") or {}).get("limitation")) or "").strip()
        candidates = [str(item).strip() for item in (paper.get("limitations") or [])]
        if extracted:
            candidates.append(extracted)
        limitations = [
            item for item in candidates
            if len(item) > 20
            and "规则抽取未识别" not in item
            and item not in {"未提及", "摘要未给出明确方法", "摘要未给出明确数据/任务"}
        ]
        if limitations:
            question = f"能否针对「{_clip(limitations[0], 110)}」设计一个最小验证实验？"
            if question not in seen_questions:
                seen_questions.add(question)
                gaps.append({
                    "title": f"《{_short_title(paper, 36)}》的局限",
                    "question": question,
                    "impact": "medium",
                })
    for paper in papers:
        future = next(
            (
                sentence for sentence in _sentences(_compare_text(paper))
                if any(marker in sentence.lower() for marker in (
                    "future work", "open question", "remains", "尚未", "未来",
                    "进一步", "有待", "后续",
                ))
            ),
            "",
        )
        if future:
            question = _clip(future, 170)
            if question not in seen_questions:
                seen_questions.add(question)
                gaps.append({
                    "title": "作者指出的后续方向",
                    "question": question,
                    "impact": "medium",
                })
    if not gaps:
        gaps.append({
            "title": "材料局限",
            "question": "所选论文的当前材料未给出明确局限；建议阅读全文的 Discussion / Conclusion 后再定。",
            "impact": "low",
        })
    return {
        "warnings": warnings,
        "consensus": consensus[:6],
        "divergence": divergence[:6],
        "gaps": gaps[:4],
        "comparability": comparability,
    }


def _llm_compare(papers: list[dict], rows: list[dict], columns: list[dict]) -> dict | None:
    if not config.LLM_ENABLED:
        return None
    context = [
        {
            "id": paper.get("id"),
            "title": paper.get("title"),
            "abstract": abstract_of(paper)[:1600],
            "limitations": (paper.get("limitations") or [])[:3],
            "results": (paper.get("results") or [])[:3],
        }
        for paper in papers
    ]
    user = (
        "只根据下面的论文内容与对比表，输出 JSON："
        '{"comparability":[{"paper_ids":["A","B"],"comparable":true,'
        '"reason":"..."}],"consensus":["..."],"divergence":["..."],'
        '"cautions":["..."],"gaps":[{"question":"...","why_gap":"..."}]}。\n'
        "comparability 必须覆盖每一对论文；只有共享指标/任务/条件时才判 comparable=true。\n"
        "consensus 至少两篇支持；divergence 写清哪两篇、在哪一条件下不同；"
        "gaps 必须跟随文证据。不要使用任何内置演示语料；主题不同时明确写“不可直接比较”。\n"
        "论文内容：\n" + json.dumps(context, ensure_ascii=False, default=str)[:14000]
        + "\n\n对比表：\n" + json.dumps(rows, ensure_ascii=False, default=str)[:9000]
        + "\n\n列定义：\n" + json.dumps(columns, ensure_ascii=False, default=str)
    )
    data, _ = llm_gateway.audited_chat_json(COMPARE_PROMPT, user, purpose="compare")
    if not isinstance(data, dict):
        return None

    def _list(key: str) -> list[str]:
        value = data.get(key)
        if not isinstance(value, list):
            return []
        result = []
        for item in value:
            if isinstance(item, str) and item.strip():
                result.append(item.strip())
            elif isinstance(item, dict):
                text = item.get("claim") or item.get("question") or item.get("reason") or ""
                if text:
                    result.append(str(text).strip())
        return result[:6]

    consensus = _list("consensus")
    divergence = _list("divergence")
    cautions = _list("cautions")
    valid_ids = {str(paper.get("id")) for paper in papers}
    comparability: list[dict] = []
    raw_pairs = data.get("comparability")
    if isinstance(raw_pairs, list):
        for item in raw_pairs[:10]:
            if not isinstance(item, dict):
                continue
            ids = item.get("paper_ids")
            if not isinstance(ids, list) or len(ids) != 2:
                continue
            ids = [str(value) for value in ids]
            if any(value not in valid_ids for value in ids) or ids[0] == ids[1]:
                continue
            if not isinstance(item.get("comparable"), bool):
                continue
            reason = str(item.get("reason") or "").strip()
            if not reason:
                continue
            comparability.append({
                "paper_ids": ids,
                "comparable": item["comparable"],
                "confidence": "llm",
                "shared": [],
                "differences": [],
                "reason": reason[:240],
            })
    gaps: list[dict] = []
    raw_gaps = data.get("gaps")
    if isinstance(raw_gaps, list):
        for item in raw_gaps[:4]:
            if isinstance(item, dict):
                question = str(item.get("question") or "").strip()
                why = str(item.get("why_gap") or "").strip()
            else:
                question, why = str(item or "").strip(), ""
            if len(question) < 12:
                continue
            gaps.append({
                "title": "模型建议",
                "question": question[:220],
                "why_gap": why[:200],
                "impact": "medium",
            })
    corpus = " ".join(_compare_text(paper) for paper in papers).lower()
    combined = " ".join(
        consensus + divergence + cautions
        + [item["question"] for item in gaps]
        + [item["reason"] for item in comparability]
    ).lower()
    # Guard against a model reusing the old built-in ADC narrative on unrelated
    # papers: if a domain term is absent from the selected corpus, reject it.
    for domain in ("adc", "cim", "存算", "模数转换"):
        if domain in combined and domain not in corpus:
            return None
    if not consensus and not divergence:
        return None
    return {
        "consensus": consensus or None,
        "divergence": divergence or None,
        "warnings": cautions or None,
        "comparability": comparability or None,
        "gaps": gaps or None,
    }


def compare(papers: list[dict]) -> dict:
    papers = [paper for paper in papers if isinstance(paper, dict)]
    cache_key = _compare_cache_key(papers)
    with _COMPARE_LOCK:
        cached = _COMPARE_CACHE.get(cache_key)
        if cached is not None:
            _COMPARE_CACHE.move_to_end(cache_key)
    if cached is not None:
        return copy.deepcopy(cached)

    rows = []
    fields = [_generic_fields(paper) for paper in papers]
    domain_keys = _domain_metric_keys(papers)
    columns = [
        {"key": "title", "label": "论文", "kind": "paper"},
        {"key": "research_question", "label": "研究问题", "kind": "text"},
        {"key": "method", "label": "方法 / 路径", "kind": "text"},
        {"key": "data_task", "label": "数据 / 任务", "kind": "text"},
        {"key": "key_result", "label": "主要结果", "kind": "text"},
        {"key": "limitation", "label": "局限", "kind": "text"},
    ]
    columns.extend(
        {"key": f"metric:{key}", "label": key, "kind": "text"}
        for key in domain_keys
    )
    columns.append({"key": "evidence", "label": "原文证据", "kind": "quote"})

    for paper, field in zip(papers, fields):
        metrics = paper.get("metrics") or {}
        values = {key: field[key] for key in (
            "research_question", "method", "data_task", "key_result",
            "limitation", "evidence",
        )}
        for key in domain_keys:
            values[f"metric:{key}"] = str(metrics.get(key) or "未提及")
        rows.append({
            "id": paper.get("id"),
            "title": paper.get("title"),
            "year": paper.get("year"),
            "venue": paper.get("venue"),
            "extraction_level": paper.get("extraction_level", "摘要级"),
            "source_url": paper.get("url", ""),
            "values": values,
            # Legacy fields kept for older clients; the web UI uses columns/values.
            "process": values.get("metric:工艺/验证") or values.get("metric:工艺") or "未提及",
            "adc": values.get("metric:ADC 方案") or "未提及",
            "task": values.get("metric:网络/任务") or values.get("metric:负载") or field["data_task"],
            "headline": field["key_result"],
            "limitation": field["limitation"],
            "evidence": field["evidence"],
        })

    texts = [_compare_text(paper) for paper in papers]
    token_sets = [_compare_tokens(text) for text in texts]
    offline = _offline_compare_sections(papers, rows, fields, token_sets, texts)
    warnings = offline["warnings"]
    consensus = offline["consensus"]
    divergence = offline["divergence"]
    gaps = offline["gaps"]
    comparability = offline["comparability"]
    llm_result = _llm_compare(papers, rows, columns)
    if llm_result:
        consensus = llm_result["consensus"] or consensus
        divergence = llm_result["divergence"] or divergence
        warnings = llm_result["warnings"] or warnings
        if llm_result.get("comparability"):
            merged = {
                tuple(item["paper_ids"]): item for item in comparability
            }
            for item in llm_result["comparability"]:
                merged[tuple(item["paper_ids"])] = item
            comparability = list(merged.values())
        if llm_result.get("gaps"):
            seen = {gap["question"] for gap in gaps}
            gaps = gaps + [
                gap for gap in llm_result["gaps"] if gap["question"] not in seen
            ][: max(0, 4 - len(gaps))]
    result = {
        "rows": rows,
        "columns": columns,
        "warnings": warnings,
        "consensus": consensus,
        "divergence": divergence,
        "gaps": gaps,
        "comparability": comparability,
        "evidence_note": "每行保留一条原文证据；点击论文标题可查看完整证据链。",
    }
    if llm_result:
        # Only a model-backed answer may be reused. Caching the rule-only assembly
        # would keep serving it after the user raises the budget or the endpoint
        # recovers, and a cached reply is never charged against the run.
        with _COMPARE_LOCK:
            _COMPARE_CACHE[cache_key] = copy.deepcopy(result)
            _COMPARE_CACHE.move_to_end(cache_key)
            while len(_COMPARE_CACHE) > _COMPARE_CACHE_MAX:
                _COMPARE_CACHE.popitem(last=False)
    return result


def innovate(paper: dict) -> dict:
    evidence = paper.get("evidence", [])
    claims = paper.get("novelty", [])
    limitations = paper.get("limitations", [])
    if not claims:
        claims = ["当前材料未给出明确贡献列表；建议上传全文或配置模型后抽取。"]
    metric_names = [
        key for key, value in (paper.get("metrics") or {}).items()
        if key not in _GENERIC_METRIC_KEYS and str(value).strip() not in {"", "未提及"}
    ][:3]
    metric_text = "、".join(metric_names) or "任务效果"
    inferences = [
        f"这篇工作的核心取舍可以从“{metric_text}、实现成本、系统复杂度”三角来读。",
        "摘要级证据只能支持方向性判断，不能替代对实验设置和数据集的核查。",
        f"如果要把结论迁移到自己的项目，先确认 {metric_text}、测试条件与 baseline 是否一致。",
    ]
    return {
        "paper": brief(paper),
        "claims": claims,
        "evidence": evidence,
        "inferences": inferences,
        "assumptions": paper.get("assumptions", []),
        "limitations": limitations,
        "reviewer_questions": paper.get("reviewer_questions", []),
        "scores": {
            "新颖性": 4 if paper.get("full_text") else 3,
            "证据强度": 4 if paper.get("full_text") else 2,
            "复现可行性": 3 if paper.get("full_text") else 2,
        },
        "note": "“作者声称 / 实验证据 / 模型推断”三栏分离；推断不是论文结论。",
    }


def _heuristic_transform(paper: dict) -> dict:
    read = read_paper(paper, "5min")
    title = _short_title(paper, 90)
    problem = str(read.get("problem") or "").strip()
    method = str(read.get("method") or "").strip()
    limitations = [str(item) for item in (read.get("limitations") or []) if str(item).strip()]
    if not method or method == "摘要未给出明确方法":
        method = "论文方法（需核对全文）"
    code = (
        f'"""Reproduction scaffold for: {title}\n\n'
        "TODO: replace the placeholders with the paper's actual data loader, method and metrics.\n"
        '"""\n'
        "from dataclasses import dataclass\n\n\n"
        "@dataclass\n"
        "class ExperimentConfig:\n"
        '    dataset: str = "TODO_dataset"\n'
        '    baseline: str = "TODO_baseline"\n'
        '    method: str = "TODO_method"\n'
        "    seed: int = 42\n\n\n"
        "def load_data(config: ExperimentConfig):\n"
        '    """TODO: load the paper dataset and document its license/split."""\n'
        "    raise NotImplementedError\n\n\n"
        "def build_method(config: ExperimentConfig):\n"
        '    """TODO: implement the core method described in the paper."""\n'
        "    raise NotImplementedError\n\n\n"
        "def evaluate(predictions, references, config: ExperimentConfig):\n"
        '    """TODO: use the same metric definition and test conditions as the paper."""\n'
        "    raise NotImplementedError\n\n\n"
        "def main() -> None:\n"
        "    config = ExperimentConfig()\n"
        "    data = load_data(config)\n"
        "    model = build_method(config)\n"
        "    print({'config': config, 'data': data, 'model': model})\n\n\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )
    slides = [
        f"标题与问题：{title}",
        f"核心问题：{_clip(problem or '待核对原文', 140)}",
        f"方法：{_clip(method, 160)}",
        "证据：核对数据集、指标口径与 baseline，区分作者声称与实测结果",
        f"边界与下一步：{_clip(limitations[0] if limitations else '摘要未给出明确局限，需读全文', 140)}",
    ]
    quiz = [
        {
            "q": f"《{title}》试图解决什么问题？",
            "a": _clip(problem or "当前材料未给出明确问题，需核对原文。", 180),
        },
        {
            "q": "这篇论文的方法与 baseline 的关键差异是什么？",
            "a": _clip(method, 180),
        },
        {
            "q": "复现时最先要核对哪一项条件？",
            "a": "数据集划分、指标定义与测试条件；摘要级材料不足以确认，需要读全文或补充材料。",
        },
    ]
    course = (
        f"课程案例：以《{_clip(title, 60)}》为例，按“问题 → 方法 → 证据 → 边界”四步拆解，"
        "要求学生先复述问题，再对照摘要与实验设置检查结论是否成立。"
    )
    return {
        "repro": {
            "environment": ["Python 3.10+", "按论文依赖安装（待核对）", "准备论文数据与评测脚本"],
            "steps": [
                "通过 DOI/URL 获取开放版本并确认版本号",
                "查找作者代码或补充材料；没有代码时按方法节重建最小实现",
                "复现一个 baseline 指标，记录数据划分、随机种子与硬件",
                "加入单变量对照，报告失败条件与代价",
            ],
            "minimal_experiment": f"用最小数据复现 {_clip(method, 60)}，先对齐一个指标。",
            "risks": ["仅摘要级元数据，需核对全文", "作者可能未开源代码", "测试条件与指标口径可能不同"],
            "deliverables": ["最小复现脚本", "指标对照表", "失败条件记录"],
        },
        "code_scaffold": code,
        "slides": slides,
        "quiz": quiz,
        "course_case": course,
    }


def transform(paper: dict, outputs: list[str] | None = None) -> dict:
    outputs = outputs or ["repro", "code", "slides", "quiz", "course"]
    repo_plan = paper.get("repo_plan", {})
    code = paper.get("code_scaffold", "")
    slides = paper.get("slides", [])
    quiz = paper.get("quiz", [])
    course = paper.get("course_case", "")
    source = "builtin"

    if not paper.get("curated") and config.LLM_ENABLED:
        data, _ = llm_gateway.audited_chat_json(
            BUILDER_PROMPT,
            "根据以下论文信息，生成 JSON：\n"
            '{"repro":{"environment":[],"steps":[],"minimal_experiment":"","risks":[],"deliverables":[]},'
            '"code_scaffold":"Python 骨架，标注 TODO 和不可确定项",'
            '"slides":["..."],"quiz":[{"q":"...","a":"..."}],'
            '"course_case":"如何映射到课程案例"}\n\n'
            + _text_for_llm(paper, 9000),
            purpose="transform",
        )
        if isinstance(data, dict):
            repo_plan = data.get("repro") or repo_plan
            code = data.get("code_scaffold") or code
            slides = data.get("slides") or slides
            quiz = data.get("quiz") or quiz
            course = data.get("course_case") or course
            source = "llm"

    if not paper.get("curated"):
        fallback = _heuristic_transform(paper)
        repo_plan = repo_plan or fallback["repro"]
        code = code or fallback["code_scaffold"]
        slides = slides or fallback["slides"]
        quiz = quiz or fallback["quiz"]
        course = course or fallback["course_case"]
        if not config.LLM_ENABLED:
            source = "heuristic"

    return {
        "paper": brief(paper),
        "repro": repo_plan,
        "code_scaffold": code,
        "slides": slides,
        "quiz": quiz,
        "course_case": course,
        "selected": outputs,
        "source": source,
        "disclaimer": "复现计划与代码骨架是学习脚手架，不代表论文结果可自动复现；请以原文、开源仓库和实验验证为准。",
    }


def _generic_gaps(topic: str) -> list[dict]:
    base = topic.strip() or "当前研究方向"
    specs = [
        (
            "跨数据集与测试条件的一致性",
            f"“{base}”的结论在另一数据集、领域或测试条件下是否仍成立？",
            "当前主题没有命中内置空白库，也未绑定论文证据；先从所选论文中找出只在单一条件下验证的结论。",
            "选取两篇方法相近的论文，更换一个数据条件复现并比较结果，记录失败条件与代价。",
        ),
        (
            "评价口径与指标可比性",
            f"不同论文对“{base}”的指标口径是否一致，如何建立可对照的评测？",
            "摘要级材料常省略数据集划分、测试条件与指标定义，直接比较数值可能得出错误结论。",
            "建立统一指标卡：数据来源、划分方式、测试条件、指标定义与 baseline，先对齐再比较。",
        ),
        (
            "最小可复现实验与失败边界",
            f"如何用最小实验验证“{base}”中最关键的一条结论？",
            "现有材料给出了方向，但没有明确的失败条件与负结果报告，复现风险未知。",
            "复现一篇最接近的工作，做一次单变量对照，并预先写下失败判据与止损点。",
        ),
    ]
    return [
        {
            "id": f"gap-generic-{index}",
            "title": title,
            "question": question,
            "why_gap": why,
            "evidence": ["尚未绑定论文证据：先检索并勾选论文，或用选题工作台生成带证据的方向"],
            "novelty": 3,
            "feasibility": 4,
            "impact": 3,
            "experiment": experiment,
        }
        for index, (title, question, why, experiment) in enumerate(specs, start=1)
    ]


def gaps(topic: str) -> dict:
    tokens = [token for token in re.split(r"\s+", topic.lower()) if len(token) >= 2]
    ranked = []
    for gap in GAPS:
        haystack = (gap["title"] + gap["question"] + gap["why_gap"]).lower()
        score = sum(1 for token in tokens if token in haystack)
        ranked.append((score, gap))
    ranked.sort(key=lambda item: (-item[0], -(item[1]["novelty"] + item[1]["impact"])))
    selected = [gap for score, gap in ranked if score > 0]
    fallback = not selected
    if fallback:
        selected = _generic_gaps(topic)
    return {
        "topic": topic,
        "gaps": selected,
        "fallback": fallback,
        "critic": [
            "每个研究问题都必须能指出“哪篇论文的哪条证据”暴露了缺口。",
            "优先选可在一个学期内完成最小验证的问题，而不是泛泛的“未来工作”。",
            "如果问题同时需要新方法和新数据条件，先拆成两个可独立验证的子问题。",
        ],
        "note": (
            "内置空白库未覆盖该主题，以上为通用研究设计缺口；请结合所选论文证据再确认。"
            if fallback
            else "评分是启发式排序（新颖性/可行性/影响），用于讨论，不是论文结论。"
        ),
    }


def meta() -> dict:
    return {
        "app": "PaperPilot",
        "version": config.APP_VERSION,
        "mode": config.APP_MODE,
        "llm_enabled": config.LLM_ENABLED,
        "llm_model": config.LLM_MODEL if config.LLM_ENABLED else None,
        "demo_topic": "面向存算一体的低功耗 ADC 设计",
        "glossary_count": len(glossary_store.merged_items()),
        "notice": (
            "离线规则模式：内置真实 arXiv 元数据 + 精选全文节选；结构化字段已标注抽取层级。"
            if not config.LLM_ENABLED
            else "在线增强模式：已配置 LLM，可对上传 PDF 做结构化抽取。"
        ),
    }
