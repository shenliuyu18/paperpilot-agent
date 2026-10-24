from __future__ import annotations

import math
import re
from datetime import datetime

from . import search

SOURCE_LABELS = {
    "demo": "内置演示语料",
    "arxiv": "arXiv 在线",
    "crossref": "Crossref 在线",
    "europepmc": "Europe PMC",
    "doaj": "DOAJ 开放获取期刊",
    "hal": "HAL 开放获取",
    "upload": "本地 PDF",
    "auto": "多源并发聚合",
}

SOURCE_NOTES = {
    "demo": "内置真实论文元数据，离线可用，适合先验证检索思路。",
    "arxiv": "预印本覆盖快，但未经过同行评议；把结果当作线索而非定论。",
    "crossref": "以 DOI 为核心，期刊/会议题录较完整，部分记录只有摘要。",
    "europepmc": "覆盖生命科学与部分预印本，适合跨学科补充。",
    "doaj": "只收录开放获取期刊，来源透明，但学科覆盖有限。",
    "hal": "法国开放获取仓储，适合补充欧洲会议与学位论文。",
    "upload": "只检索当前实例里已上传的 PDF，不访问任何外部来源。",
    "auto": "并发查询 Crossref、arXiv、Europe PMC，按多源共识重排后去重；8 秒响应上限，慢源冷却后跳过。",
}

ABBREVIATIONS = {
    "adc": "analog-to-digital converter ADC",
    "cim": "compute-in-memory CIM",
    "sram": "static random-access memory SRAM",
    "rram": "resistive random-access memory RRAM",
    "snn": "spiking neural network SNN",
    "sar": "successive approximation register SAR",
    "llm": "large language model LLM",
    "dnn": "deep neural network DNN",
    "cnn": "convolutional neural network CNN",
    "vit": "vision transformer ViT",
    "ptq": "post-training quantization PTQ",
    "qat": "quantization-aware training QAT",
}

SURVEY_WORDS = (
    "survey", "review", "tutorial", "overview", "state of the art", "research progress",
    "综述", "进展", "回顾", "概览", "研究现状",
)

BACKGROUND_WORDS = (
    "introduction", "background", "challenge", "perspective",
    "背景", "挑战", "展望", "趋势",
)

STOP_WORDS = {
    "的", "了", "与", "和", "及", "在", "对", "为", "中", "面向", "基于", "一种",
    "the", "a", "an", "of", "for", "and", "in", "on", "to", "with", "by", "using",
}


def _latin_terms(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[A-Za-z][A-Za-z0-9\-\.\+]*", text or "")
        if len(token) >= 2
    ]


def _term_variants(term: str) -> list[str]:
    term = term.strip()
    if not term:
        return []
    variants = [term, term.replace("-", " "), term.replace("-", "")]
    return list(dict.fromkeys(item.lower() for item in variants if item.strip()))


def concept_blocks(query: str) -> list[dict]:
    source = " ".join(str(query or "").split())
    if not source:
        return []
    blocks: list[dict] = []
    remaining = source
    for _, zh, english in search._matched_synonyms(source):
        position = remaining.find(zh)
        if position >= 0:
            remaining = remaining[:position] + " " + remaining[position + len(zh):]
        terms = [zh, *_latin_terms(english)]
        for token in list(terms):
            expansion = ABBREVIATIONS.get(token.lower())
            if expansion:
                terms.extend(_latin_terms(expansion))
        blocks.append(
            {
                "label": zh,
                "terms": list(dict.fromkeys(terms)),
                "english": english,
                "rationale": f"把“{zh}”扩展到 {english}，兼顾中英文数据库的术语差异。",
            }
        )
    for raw in re.split(r"[\s,，、;；/|（）()\[\]【】]+", remaining):
        token = raw.strip(".。:：")
        if len(token) < 2 or token.lower() in STOP_WORDS:
            continue
        if any(token.lower() == block["label"].lower() for block in blocks):
            continue
        expansion = ABBREVIATIONS.get(token.lower())
        terms = [token]
        rationale = "保留原始检索词，避免改写后偏离你的研究问题。"
        if expansion:
            terms.extend(_latin_terms(expansion))
            rationale = f"保留“{token}”，同时展开全称，减少只检索缩写造成的漏检。"
        blocks.append(
            {
                "label": token,
                "terms": list(dict.fromkeys(terms)),
                "english": expansion or "",
                "rationale": rationale,
            }
        )
    if not blocks:
        blocks = [
            {
                "label": source,
                "terms": [source, *_latin_terms(source)],
                "english": "",
                "rationale": "未匹配到领域词表，保留原始表述作为单一概念块。",
            }
        ]
    deduped: list[dict] = []
    seen: set[str] = set()
    for block in blocks:
        key = block["label"].lower()
        if key in seen:
            continue
        seen.add(key)
        block["terms"] = list(dict.fromkeys(term for term in block["terms"] if term))
        deduped.append(block)
    return deduped[:5]


def _concept_english_terms(blocks: list[dict]) -> list[str]:
    terms = []
    for block in blocks:
        for term in block["terms"]:
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9\-\.\+]*", term) and term.lower() not in STOP_WORDS:
                terms.append(term)
    return list(dict.fromkeys(terms))


def _structured_query(blocks: list[dict]) -> str:
    groups = []
    for block in blocks:
        english = [
            term for term in block["terms"]
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9\-\.\+]*", term)
        ]
        if english:
            groups.append("(" + " OR ".join(dict.fromkeys(english)) + ")")
    return " AND ".join(groups) or ""


def build_plan(query: str, source: str = "demo") -> dict:
    blocks = concept_blocks(query)
    english_terms = _concept_english_terms(blocks)
    structured = _structured_query(blocks)
    primary_query = structured or query
    from . import custom_sources

    custom_source = None
    if source.startswith("custom:"):
        custom_source = custom_sources.get_public(source.split(":", 1)[1])
    sources = [
        {
            "id": "auto",
            "label": SOURCE_LABELS["auto"],
            "query": primary_query,
            "note": SOURCE_NOTES["auto"],
        },
        {
            "id": "crossref",
            "label": SOURCE_LABELS["crossref"],
            "query": search._crossref_query(query),
            "note": SOURCE_NOTES["crossref"],
        },
        {
            "id": "arxiv",
            "label": SOURCE_LABELS["arxiv"],
            "query": search._arxiv_query(query),
            "note": SOURCE_NOTES["arxiv"],
        },
        {
            "id": "europepmc",
            "label": SOURCE_LABELS["europepmc"],
            "query": primary_query,
            "note": SOURCE_NOTES["europepmc"],
        },
        {
            "id": "doaj",
            "label": SOURCE_LABELS["doaj"],
            "query": " OR ".join(english_terms) or query,
            "note": SOURCE_NOTES["doaj"],
        },
        {
            "id": "hal",
            "label": SOURCE_LABELS["hal"],
            "query": structured or query,
            "note": SOURCE_NOTES["hal"],
        },
        {
            "id": "upload",
            "label": SOURCE_LABELS["upload"],
            "query": query,
            "note": SOURCE_NOTES["upload"],
        },
        {
            "id": "demo",
            "label": SOURCE_LABELS["demo"],
            "query": query,
            "note": SOURCE_NOTES["demo"],
        },
    ]
    for item in custom_sources.list_sources(include_disabled=False)[:4]:
        source_id = "custom:" + item["id"]
        sources.insert(
            0,
            {
                "id": source_id,
                "label": item["name"],
                "query": query,
                "note": f"自定义 API · {item.get('base_url', '')}",
            },
        )
    warnings = []
    if len(blocks) == 1:
        warnings.append("只识别到一个概念块；换用“研究对象 + 方法 + 约束”的写法通常更精确。")
    if not english_terms:
        warnings.append("没有生成英文检索词；跨库检索可能漏掉英文文献。")
    if source.startswith("custom:"):
        if not custom_source:
            warnings.append("当前自定义来源不存在，请到“来源设置”检查。")
        elif not custom_source.get("enabled", True):
            warnings.append("当前自定义来源已停用，请到“来源设置”启用。")
    elif source not in SOURCE_LABELS:
        warnings.append(f"未知来源 {source}，将使用内置演示语料。")
    source_label = (
        custom_source.get("name")
        if custom_source
        else SOURCE_LABELS.get(source, SOURCE_LABELS["demo"])
    )
    return {
        "query": query,
        "source": source,
        "source_label": source_label,
        "concepts": blocks,
        "expanded_terms": list(
            dict.fromkeys(term for block in blocks for term in block["terms"])
        ),
        "sources": sources,
        "primary_query": primary_query,
        "strategy": [
            "先把研究兴趣拆成“对象—方法—约束”概念块，再做中英文同义词扩展。",
            "检索时优先看直接相关结果；方法参考用于补齐设计选择，背景脉络只用于建立问题。",
            "筛选时同时看来源类型、摘要完整度、DOI 与年份，不把单一指标当作质量证明。",
            "预印本与缺 DOI 记录只作为线索，关键结论要回到同行评议版本或原文核对。",
        ],
        "layering": {
            "直接相关": "标题或摘要覆盖主要概念，可直接进入精读候选。",
            "方法参考": "命中部分概念或提供可迁移的方法/系统设计。",
            "背景脉络": "综述、挑战或相关领域材料，用于建立问题而非直接引用结论。",
        },
        "credibility": {
            "dimensions": [
                {"id": "source", "label": "来源类型", "weight": 25, "description": "期刊/会议/预印本/仓储"},
                {"id": "metadata", "label": "题录完整度", "weight": 25, "description": "作者、年份、venue、DOI、链接"},
                {"id": "abstract", "label": "摘要可用性", "weight": 20, "description": "是否有足够摘要支持筛选"},
                {"id": "review", "label": "同行评议", "weight": 20, "description": "是否经过期刊/会议评审"},
                {"id": "usage", "label": "被引信号", "weight": 10, "description": "仅作辅助，不替代内容判断"},
            ]
        },
        "warnings": warnings,
        "estimated": {
            "concept_count": len(blocks),
            "term_count": len({term for block in blocks for term in block["terms"]}),
            "source_count": len(sources),
        },
    }


def _source_type(paper: dict) -> tuple[str, bool]:
    source = str(paper.get("source") or "").lower()
    metrics = paper.get("metrics") or {}
    type_label = str(metrics.get("类型") or "")
    venue = str(paper.get("venue") or "")
    low_venue = venue.lower()
    if source == "arxiv":
        return "预印本", False
    if source == "doaj":
        return "开放获取期刊", True
    if source == "hal":
        return "开放获取仓储", False
    if source == "europepmc":
        if any(word in low_venue for word in ("medrxiv", "biorxiv", "preprint")):
            return "预印本", False
        return "生命科学索引", True
    if source == "crossref":
        if "预印本" in type_label:
            return "预印本", False
        if "会议" in type_label:
            return "会议论文", True
        if "期刊" in type_label:
            return "期刊论文", True
        if "书籍" in type_label or "章节" in type_label:
            return "书籍章节", False
        return "Crossref 记录", bool(paper.get("doi"))
    if source == "upload":
        return "本地 PDF", False
    if source.startswith("custom:"):
        return "自定义来源", False
    if paper.get("demo"):
        return "内置演示语料", bool(paper.get("doi"))
    if paper.get("doi"):
        return "期刊论文/记录", True
    return "其他来源", False


def _matches_concept(concept: dict, text: str) -> bool:
    haystack = text.lower()
    for term in concept.get("terms", []):
        for variant in _term_variants(term):
            if variant and variant in haystack:
                return True
    return False


def _credibility(paper: dict, source_type: str, peer_reviewed: bool) -> dict:
    score = 48
    signals: list[str] = []
    warnings: list[str] = []
    title = str(paper.get("title") or "").strip()
    abstract = str(paper.get("abstract") or "")
    authors = paper.get("authors") or []
    year = paper.get("year")
    venue = str(paper.get("venue") or "").strip()
    doi = str(paper.get("doi") or "").strip()
    url = str(paper.get("url") or "").strip()
    if title:
        score += 6
    if len(abstract) >= 80:
        score += 10
        signals.append("有可用摘要")
    elif abstract:
        score += 3
        warnings.append("摘要过短，筛选依据有限")
    else:
        score -= 8
        warnings.append("缺少摘要，只能依据题录判断")
    if len(abstract) >= 400:
        score += 4
    if authors:
        score += 7
        signals.append("作者信息完整")
        if len(authors) >= 3:
            score += 2
    else:
        score -= 4
        warnings.append("缺少作者信息，引用格式不完整")
    current_year = datetime.now().year
    if isinstance(year, int) and 1990 <= year <= current_year + 1:
        score += 6
    else:
        score -= 3
        warnings.append("缺少或异常年份，无法判断时效性")
    if venue:
        score += 5
        signals.append(f"来源：{venue}")
    else:
        score -= 4
        warnings.append("缺少 venue，来源可信度未知")
    if doi:
        score += 10
        signals.append("有 DOI，可回溯原文")
    else:
        score -= 4
        warnings.append("缺少 DOI，引用前需人工补全")
    if url:
        score += 2
    if peer_reviewed:
        score += 8
        signals.append("同行评议或开放获取索引来源")
    if source_type == "预印本":
        score -= 10
        warnings.append("预印本未经同行评议，结论需回到正式版本核对")
    if source_type in {"其他来源", "Crossref 记录"} and not peer_reviewed:
        warnings.append("来源类型不明确")
    cited = paper.get("cited_by_count")
    try:
        if cited is not None and int(cited) >= 10:
            score += 3
            signals.append(f"被引 {int(cited)} 次（仅作辅助）")
    except (TypeError, ValueError):
        pass
    score = max(0, min(100, score))
    level = "高" if score >= 75 else "中" if score >= 55 else "低"
    return {
        "score": score,
        "level": level,
        "signals": signals,
        "warnings": warnings,
    }


def annotate_results(papers: list[dict], query: str) -> list[dict]:
    blocks = concept_blocks(query)
    total = max(len(blocks), 1)
    annotated: list[dict] = []
    for paper in papers:
        title = str(paper.get("title") or "")
        body = " ".join(
            str(paper.get(key) or "")
            for key in ("title", "abstract", "tldr", "venue")
        )
        matched = [block["label"] for block in blocks if _matches_concept(block, body)]
        title_matched = [block["label"] for block in blocks if _matches_concept(block, title)]
        ratio = round(len(matched) / total, 2)
        source_type, peer_reviewed = _source_type(paper)
        low_title = title.lower()
        survey = any(word in low_title for word in SURVEY_WORDS)
        background_hit = any(word in low_title for word in BACKGROUND_WORDS)
        if survey:
            layer = "背景脉络"
            reason = "标题显示为综述/进展材料，适合建立问题与术语框架，不直接作为方法证据。"
        elif title_matched and (
            len(title_matched) >= math.ceil(total * 0.5)
            or (ratio >= 0.6 and len(title_matched) >= 1)
        ):
            layer = "直接相关"
            reason = f"标题命中 {len(title_matched)}/{total} 个概念块，主题直接对应检索问题。"
        elif ratio >= 0.25 or title_matched:
            layer = "方法参考"
            reason = f"正文命中 {len(matched)}/{total} 个概念块，可作为方法或设计选择的参考。"
        elif background_hit:
            layer = "背景脉络"
            reason = "命中背景/挑战类表述，适合用于引言与研究动机。"
        else:
            layer = "背景脉络"
            reason = "未命中核心概念，保留为背景扫描，不建议占用精读时间。"
        annotated.append(
            {
                **paper,
                "layer": layer,
                "layer_reason": reason,
                "source_type": source_type,
                "concept_coverage": {
                    "matched": matched,
                    "title_matched": title_matched,
                    "total": total,
                    "ratio": ratio,
                },
                "credibility": _credibility(paper, source_type, peer_reviewed),
            }
        )
    return annotated


def layer_summary(papers: list[dict]) -> dict:
    summary = {"total": len(papers), "直接相关": 0, "方法参考": 0, "背景脉络": 0}
    for paper in papers:
        layer = paper.get("layer")
        if layer in summary:
            summary[layer] += 1
    return summary
