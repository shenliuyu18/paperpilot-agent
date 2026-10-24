from __future__ import annotations

import math
import re
import threading
from collections import Counter, OrderedDict
from functools import lru_cache

from .. import config
from ..llm import chat
from . import analysis, library, retrieval, search

STOP_WORDS = {
    "the", "of", "and", "in", "to", "is", "we", "this", "with", "for", "on",
    "by", "as", "that", "are", "from", "using", "based", "can", "be", "a", "an",
    "it", "its", "our", "their", "was", "were", "which", "these", "those", "at",
    "or", "not", "has", "have", "had", "but", "than", "then", "also", "into",
}

ASK_PROMPT = (
    "你是严谨的科研证据问答助手。只能使用用户提供的“证据片段”回答，不得引入外部知识，"
    "不得把摘要级信息说成全文结论。每条结论后用 [编号] 标注引用；证据不足时明确写“现有证据不足”。"
    "如果证据只覆盖部分论文或只支持问题的某个方面，必须明确说明，不得用未命中的论文补全。"
    "用中文回答：先给直接结论，再给 2–3 条依据，最后用一句话说明证据边界。"
)

SCOPE_LABELS = {
    "all": "全部语料",
    "library": "我的研究库",
    "selected": "当前勾选论文",
}

_PASSAGE_CACHE: OrderedDict[tuple, tuple[dict, ...]] = OrderedDict()
_PASSAGE_LOCK = threading.Lock()


def _resolve(paper_id: str) -> dict | None:
    paper = search.get_paper(paper_id)
    if paper:
        return paper
    item = library.get(paper_id)
    return library.as_paper(item) if item else None


def _scope_papers(scope: str, paper_ids: list[str]) -> list[dict]:
    if scope == "selected":
        ids = list(dict.fromkeys(paper_ids))
        return [paper for paper in (_resolve(pid) for pid in ids) if paper]
    if scope == "library":
        return [
            paper
            for paper in (_resolve(item["paper_id"]) for item in library.list_items())
            if paper
        ]
    papers = list(search.all_papers())
    seen = {paper.get("id") for paper in papers}
    for item in library.list_items():
        if item["paper_id"] not in seen:
            papers.append(library.as_paper(item))
            seen.add(item["paper_id"])
    return papers


def _split_long(text: str, limit: int) -> list[str]:
    pieces: list[str] = []
    remainder = text
    while len(remainder) > limit:
        cut = remainder.rfind(" ", int(limit * 0.55), limit)
        if cut <= 0:
            cut = limit
        pieces.append(remainder[:cut].strip())
        remainder = remainder[cut:].strip()
    if remainder:
        pieces.append(remainder)
    return pieces


def _readable_chunk(chunk: str) -> bool:
    numbers = re.findall(r"\d+(?:\.\d+)?", chunk)
    words = re.findall(r"[A-Za-z]{3,}", chunk)
    if len(numbers) >= 6 and len(words) < 18:
        return False
    if not re.search(r"[.!?。！？]", chunk) and len(numbers) >= 5 and len(words) < 30:
        return False
    return True


def _chunks(text: str, limit: int = 460) -> list[str]:
    text = " ".join((text or "").split())
    if not text:
        return []
    if len(text) <= limit:
        return [text] if len(text) >= 32 else []
    sentences = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long(sentence, limit))
            continue
        candidate = (current + " " + sentence).strip()
        if current and len(candidate) > limit:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if len(chunk) >= 32 and _readable_chunk(chunk)]


def _paper_signature(paper: dict) -> tuple:
    sections = paper.get("sections") or []
    evidence = paper.get("evidence") or []
    return (
        paper.get("id"),
        paper.get("title"),
        len(paper.get("abstract") or ""),
        tuple(
            (section.get("title"), section.get("page"), len(section.get("text") or ""))
            for section in sections[:24]
        ),
        tuple(
            (item.get("field"), item.get("section"), item.get("page"), len(item.get("quote") or ""))
            for item in evidence[:12]
        ),
    )


def _clean_passage_text(text: str) -> str:
    value = " ".join(str(text or "").split())
    return re.sub(r"^(?:abstract|摘要)\s*[:：.\-—]?\s*", "", value, flags=re.I)


def _build_passages(papers: list[dict]) -> list[dict]:
    passages: list[dict] = []
    for paper in papers:
        paper_id = paper.get("id")
        title = paper.get("title") or "未命名论文"
        texts = [
            ("Abstract", 1, analysis.abstract_of(paper), None),
            *[
                (section.get("title") or "正文", section.get("page") or 1, _clean_passage_text(section.get("text") or ""), None)
                for section in paper.get("sections", [])[:24]
                if (section.get("title") or "").strip().lower() not in {"references", "bibliography"}
            ],
            *[
                (
                    item.get("section") or "证据",
                    item.get("page") or 1,
                    _clean_passage_text(item.get("quote") or ""),
                    item.get("field"),
                )
                for item in paper.get("evidence", [])[:12]
            ],
        ]
        seen: set[str] = set()
        for section, page, text, field in texts:
            for chunk in _chunks(text):
                key = re.sub(r"\W+", "", chunk.lower())[:180]
                if key in seen:
                    continue
                seen.add(key)
                passages.append(
                    {
                        "paper_id": paper_id,
                        "title": title,
                        "section": section,
                        "page": page,
                        "text": chunk,
                        "field": field,
                        "_tokens": Counter(_tokenize(chunk)),
                    }
                )
    return passages


def _passages(papers: list[dict]) -> list[dict]:
    signature = tuple(_paper_signature(paper) for paper in papers)
    with _PASSAGE_LOCK:
        cached = _PASSAGE_CACHE.get(signature)
        if cached is not None:
            _PASSAGE_CACHE.move_to_end(signature)
            return list(cached)
    passages = _build_passages(papers)
    with _PASSAGE_LOCK:
        _PASSAGE_CACHE[signature] = tuple(passages)
        while len(_PASSAGE_CACHE) > 12:
            _PASSAGE_CACHE.popitem(last=False)
    return passages


@lru_cache(maxsize=8192)
def _tokenize(text: str) -> list[str]:
    text = (text or "").lower()
    tokens: list[str] = []
    for word in re.findall(r"[a-z0-9][a-z0-9\-\.\+%]{1,}", text):
        word = word.strip(".-")
        if len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
            word = word[:-1]
        if len(word) >= 2 and word not in STOP_WORDS:
            tokens.append(word)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            continue
        tokens.extend(run[index:index + 2] for index in range(len(run) - 1))
    return list(dict.fromkeys(tokens))


def _phrase_bonus(question: str, passage: str) -> float:
    lowered = question.lower()
    text = passage.lower()
    bonus = 0.0
    for phrase in re.findall(r"[\u4e00-\u9fff]{3,}", lowered):
        if phrase in text:
            bonus += 1.8
    for phrase in re.findall(r"[a-z0-9][a-z0-9\-]{3,}(?:\s+[a-z0-9][a-z0-9\-]{1,}){0,2}", lowered):
        if phrase in text and len(phrase) >= 5:
            bonus += 0.5
    return min(bonus, 4.0)


INTENT_LABELS = {
    "method": "方法/机制",
    "result": "结果/指标",
    "limitation": "局限/风险",
    "definition": "概念/定义",
    "contribution": "贡献/创新",
    "comparison": "跨论文对比",
    "general": "综合检索",
}

SECTION_HINTS = {
    "method": ("method", "approach", "architecture", "design", "implementation", "proposed", "原理", "方法", "架构", "设计", "实现"),
    "result": ("result", "experiment", "evaluation", "measurement", "performance", "validation", "结果", "实验", "验证", "测量", "性能"),
    "limitation": ("limitation", "discussion", "conclusion", "future", "局限", "讨论", "结论", "展望"),
    "definition": ("introduction", "background", "abstract", "overview", "引言", "背景", "概述"),
    "contribution": ("method", "result", "abstract", "方法", "结果", "贡献"),
    "comparison": ("abstract", "result", "discussion", "结论", "结果"),
}

SECTION_BONUS = {
    "method": 1.35,
    "result": 1.25,
    "limitation": 1.5,
    "definition": 0.75,
    "contribution": 0.9,
    "comparison": 0.45,
}


def _detect_intent(text: str) -> dict:
    value = (text or "").lower()
    if re.search(
        r"局限|不足|风险|缺点|限制|未来|挑战|瓶颈|障碍|"
        r"limitation|future work|drawback|weakness|bottleneck|obstacle|barrier",
        value,
    ):
        return {"id": "limitation", "label": INTENT_LABELS["limitation"]}
    if re.search(r"创新|贡献|亮点|新意|novelty|contribution", value):
        return {"id": "contribution", "label": INTENT_LABELS["contribution"]}
    if re.search(r"对比|比较|区别|差异|不同|相比|相较|哪个|哪篇|优劣|compare|versus|vs\b", value):
        return {"id": "comparison", "label": INTENT_LABELS["comparison"]}
    if re.search(r"定量|数值|指标|多少|百分比|具体.*(?:结果|数字)|结果|表现|效果|性能|benchmark|score", value):
        return {"id": "result", "label": INTENT_LABELS["result"]}
    if re.search(
        r"(?:what|which|how much|how many|report(?:ed)?|quantitative|numbers?|多少|数值|定量|指标|结果)"
        r".{0,70}(?:speed|speedup|memory|latency|throughput|accuracy|energy|performance|"
        r"速度|内存|延迟|吞吐|精度|能效|性能|结果)",
        value,
        re.I,
    ):
        return {"id": "result", "label": INTENT_LABELS["result"]}
    if re.search(
        r"如何(?:实现|设计|构建|优化|提升|降低|解决|做|工作)|怎么(?:实现|设计|做)|"
        r"机制|原理|架构|方法|方案|算法|流程|策略|做法|"
        r"how\s+(?:to|does|do|can|is|are)|method|approach|mechanism|architecture|design|technique",
        value,
    ):
        return {"id": "method", "label": INTENT_LABELS["method"]}
    if re.search(
        r"多少|数值|指标|结果|表现|效果|性能|提升|降低|减少|提高|"
        r"速度|能耗|精度|延迟|面积|吞吐|百分比|%|"
        r"result|accuracy|energy|latency|area|throughput|performance|speed|memory|"
        r"improv|reduc|achiev|benchmark|score",
        value,
    ):
        return {"id": "result", "label": INTENT_LABELS["result"]}
    if re.search(r"什么是|何为|定义|含义|概念|what\s+(?:is|are)|definition|overview", value):
        return {"id": "definition", "label": INTENT_LABELS["definition"]}
    return {"id": "general", "label": INTENT_LABELS["general"]}


def _field_bonus(field: str | None, intent: str) -> float:
    if not field:
        return 0.0
    if intent == "limitation" and field == "limitation":
        return 2.4
    if intent == "result" and field == "result":
        return 1.6
    if intent == "contribution" and field in {"method", "result"}:
        return 1.5
    if intent == "method" and field == "method":
        return 0.9
    if intent == "comparison" and field in {"method", "result"}:
        return 0.4
    return 0.0


def _section_bonus(section: str, intent: str) -> float:
    if intent not in SECTION_HINTS:
        return 0.0
    title = (section or "").lower()
    if any(term in title for term in SECTION_HINTS[intent]):
        return SECTION_BONUS[intent]
    if intent in {"definition", "comparison"} and "abstract" in title:
        return SECTION_BONUS[intent]
    return 0.0


def _plan_queries(question: str) -> list[dict]:
    raw = (question or "").strip().rstrip("？?")
    parts = [
        part.strip(" ，,：:？?")
        for part in re.split(r"[；;。？！?!]|以及|另外|此外", raw)
        if part.strip()
    ]
    parts = [part for part in parts if len(part) >= 5]
    if len(parts) <= 1:
        intent = _detect_intent(raw)
        return [
            {
                "label": intent["label"],
                "query": raw,
                "intent": intent["id"],
                "intent_label": intent["label"],
            }
        ]
    aspects = []
    for part in parts[:3]:
        intent = _detect_intent(part)
        aspects.append(
            {
                "label": _clip(part.strip("？? "), 22),
                "query": part,
                "intent": intent["id"],
                "intent_label": intent["label"],
            }
        )
    return aspects


def _focus_paper_ids(question: str, passages: list[dict]) -> set[str]:
    query_tokens = set(_tokenize(question))
    if not query_tokens:
        return set()
    titles = {
        item["paper_id"]: item.get("title") or ""
        for item in passages
    }
    focus: set[str] = set()
    for paper_id, title in titles.items():
        prefix = re.split(r"[:：(]", title, maxsplit=1)[0].strip()
        words = re.findall(r"[A-Za-z][A-Za-z0-9.\-]*", prefix)
        if not 1 <= len(words) <= 4:
            continue
        names = {
            word.lower().strip(".")
            for word in words
            if word.isupper() or any(char.isupper() for char in word[1:]) or any(char.isdigit() for char in word)
        }
        if names & query_tokens:
            focus.add(paper_id)
    return focus


def _rank(
    question: str,
    passages: list[dict],
    top_k: int,
    intent: str = "general",
    allow_field_only: bool = False,
) -> list[dict]:
    query_tokens = _tokenize(search._expand_query(question))
    if not query_tokens or not passages:
        return []
    focus_ids = _focus_paper_ids(question, passages)
    token_counts = [
        item.get("_tokens") if "_tokens" in item else Counter(_tokenize(item["text"]))
        for item in passages
    ]
    doc_freq: Counter = Counter()
    for counts in token_counts:
        doc_freq.update(counts.keys())
    total = len(passages)
    avg_len = sum(sum(counts.values()) for counts in token_counts) / max(total, 1)
    k1, b = 1.5, 0.75
    scored: list[dict] = []
    for item, counts in zip(passages, token_counts):
        length = sum(counts.values())
        score = 0.0
        for token in query_tokens:
            freq = counts.get(token, 0)
            if not freq:
                continue
            df = doc_freq.get(token, 0)
            idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
            score += idf * (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * length / max(avg_len, 1)))
        field_bonus = _field_bonus(item.get("field"), intent)
        if score <= 0 and not (allow_field_only and field_bonus > 0):
            continue
        score += field_bonus
        if intent == "limitation" and item.get("field") != "limitation":
            section_is_limit = _section_bonus(item.get("section", ""), "limitation") > 0
            own_limitation = re.search(
                r"limitations?\s*:|(?:our|this (?:paper|work)|we)\b.{0,90}"
                r"(?:limitation|do not|does not|cannot|future work|not supported)",
                item["text"],
                re.I,
            )
            if not section_is_limit and not own_limitation:
                score *= 0.55
        title = item["title"].lower()
        title_hits = sum(1 for token in query_tokens if token in title)
        score += min(title_hits * 0.7, 2.1)
        score += _phrase_bonus(question, item["text"])
        score += _section_bonus(item.get("section", ""), intent)
        if intent == "result":
            quantities_found = len(QUANTITY_RE.findall(item["text"]))
            if quantities_found:
                score += min(1.6, 0.4 * quantities_found)
            else:
                score *= 0.6
        if focus_ids:
            if item["paper_id"] in focus_ids:
                score += 2.0
            else:
                score *= 0.5
        scored.append({**item, "score": round(score, 3)})
    scored.sort(key=lambda item: item["score"], reverse=True)
    if not scored:
        return []

    comparison = bool(
        re.search(
            r"分别|各自|各篇|每篇|五篇|这几篇|这些(?:工作|论文|方法)|全部论文|所有论文|不同|对比|差异|优劣|"
            r"these (?:papers|methods|works|approaches)|each (?:paper|method)|respectively",
            question,
            re.I,
        )
    )
    floor = scored[0]["score"] * (0.08 if comparison else 0.5)
    per_paper_limit = 1 if comparison else 2

    selected: list[dict] = []
    per_paper: Counter = Counter()
    for item in scored:
        if item["score"] < floor:
            break
        if per_paper[item["paper_id"]] >= per_paper_limit:
            continue
        selected.append(item)
        per_paper[item["paper_id"]] += 1
        if len(selected) >= top_k:
            break

    # 先保证跨论文覆盖，再在同一篇论文里补足上下文，低相关片段不硬凑。
    if len(selected) < top_k:
        chosen = {id(item) for item in selected}
        fill_limit = 2 if comparison else 3
        fill_floor = scored[0]["score"] * (0.2 if comparison else 0.5)
        for item in scored:
            if item["score"] < fill_floor:
                break
            if comparison and item["paper_id"] not in per_paper:
                continue
            if id(item) in chosen or per_paper[item["paper_id"]] >= fill_limit:
                continue
            selected.append(item)
            per_paper[item["paper_id"]] += 1
            if len(selected) >= top_k:
                break
    return selected


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text[:limit].rstrip(" ,;，；") + "…" if len(text) > limit else text


def _rank_planned(
    question: str, passages: list[dict], top_k: int, paper_count: int = 0
) -> tuple[list[dict], list[dict]]:
    plan = _plan_queries(question)
    allow_field_only = paper_count == 1 or bool(
        re.search(
            r"这些工作|这些论文|这些方法|这几篇|五篇|本文|该论文|这篇|上述|全部论文|所有论文|各自|它们|它的|其局限|"
            r"these (?:papers|methods|works)|each (?:paper|method)",
            question,
            re.I,
        )
    )
    coverage_question = bool(
        re.search(
            r"这些|这几篇|五篇|各自|分别|每篇|各篇|全部论文|所有论文|"
            r"these (?:papers|methods|works)|each (?:paper|method)|respectively",
            question,
            re.I,
        )
    )
    if len(plan) == 1:
        ranked = _rank(question, passages, top_k, plan[0]["intent"], allow_field_only)
        for item in ranked:
            item["aspect"] = plan[0]["label"]
            item["aspects"] = [plan[0]["label"]]
        return ranked, plan

    merged: dict[str, dict] = {}
    per_aspect: dict[str, list[dict]] = {}
    aspect_limit = min(6, max(4, top_k)) if coverage_question else min(4, top_k)
    for aspect in plan:
        ranked = _rank(aspect["query"], passages, aspect_limit, aspect["intent"], allow_field_only)
        per_aspect[aspect["label"]] = ranked
        for item in ranked:
            key = re.sub(r"\W+", "", item["text"].lower())[:140]
            if key in merged:
                if aspect["label"] not in merged[key]["aspects"]:
                    merged[key]["aspects"].append(aspect["label"])
                merged[key]["score"] = max(merged[key]["score"], item["score"])
            else:
                merged[key] = {**item, "aspect": aspect["label"], "aspects": [aspect["label"]]}
    if not merged:
        return [], plan

    max_score = max(item["score"] for item in merged.values())
    selected: list[dict] = []
    per_paper: Counter = Counter()
    chosen: set[str] = set()

    # 每个方面先保留一条最强证据，避免一个方面吞掉全部引用。
    for aspect in plan:
        for item in per_aspect.get(aspect["label"], []):
            key = re.sub(r"\W+", "", item["text"].lower())[:140]
            candidate = merged.get(key)
            if not candidate or key in chosen:
                continue
            if candidate["score"] < max_score * 0.3:
                break
            if per_paper[candidate["paper_id"]] >= 2:
                continue
            selected.append(candidate)
            chosen.add(key)
            per_paper[candidate["paper_id"]] += 1
            if len(selected) >= top_k:
                return selected, plan

    for item in sorted(merged.values(), key=lambda entry: entry["score"], reverse=True):
        key = re.sub(r"\W+", "", item["text"].lower())[:140]
        if key in chosen:
            continue
        if item["score"] < max_score * 0.28:
            break
        if per_paper[item["paper_id"]] >= 2:
            continue
        selected.append(item)
        chosen.add(key)
        per_paper[item["paper_id"]] += 1
        if len(selected) >= top_k:
            break

    if coverage_question:
        paper_ids = list(dict.fromkeys(item["paper_id"] for item in passages))
        covered = {item["paper_id"] for item in selected}
        for paper_id in paper_ids:
            if paper_id in covered:
                continue
            group = [item for item in passages if item["paper_id"] == paper_id]
            best = _rank(question, group, 1, plan[0]["intent"], allow_field_only=True)
            if not best or best[0]["score"] < max_score * 0.3:
                continue
            candidate = {
                **best[0],
                "aspect": plan[0]["label"],
                "aspects": [plan[0]["label"]],
            }
            if len(selected) >= top_k:
                counts = Counter(item["paper_id"] for item in selected)
                duplicate_indices = [
                    index
                    for index, item in enumerate(selected)
                    if counts[item["paper_id"]] > 1
                ]
                if not duplicate_indices:
                    break
                drop = min(duplicate_indices, key=lambda index: selected[index]["score"])
                selected.pop(drop)
            selected.append(candidate)
            chosen.add(re.sub(r"\W+", "", candidate["text"].lower())[:140])
            covered.add(paper_id)
    return selected, plan


QUANTITY_RE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*-?\s*"
    r"(TOPS/W|TOPS|GOPS|SOP|MS/s|GS/s|%|×|x|nm|µm|μm|pJ|fJ|nJ|µW|μW|mW|MHz|GHz|mV|dB|bit|b|倍|纳米|微米|皮焦|纳焦|毫瓦|微瓦|毫伏|吉赫|兆赫|比特|位)"
    r"(?![A-Za-z])",
    re.I,
)


def _quantities(text: str, limit: int = 6) -> list[dict]:
    found = []
    seen = set()
    for match in QUANTITY_RE.finditer(text or ""):
        value, unit = match.group(1), match.group(2)
        key = f"{value}{unit.lower()}"
        if key in seen:
            continue
        seen.add(key)
        found.append({"value": value, "unit": unit, "text": match.group(0).strip()})
        if len(found) >= limit:
            break
    return found


def _support_matrix(papers: list[dict], citations: list[dict]) -> tuple[list[dict], dict]:
    if not citations:
        return (
            [
                {
                    "paper_id": paper.get("id"),
                    "title": paper.get("title") or "未命名论文",
                    "level": "none",
                    "label": "未命中",
                    "score": 0,
                    "citation_index": None,
                }
                for paper in papers
            ],
            {"direct": 0, "partial": 0, "none": len(papers)},
        )
    top_score = max(item["score"] for item in citations) or 1
    support = []
    for paper in papers:
        matches = [item for item in citations if item["paper_id"] == paper.get("id")]
        if not matches:
            support.append(
                {
                    "paper_id": paper.get("id"),
                    "title": paper.get("title") or "未命名论文",
                    "level": "none",
                    "label": "未命中",
                    "score": 0,
                    "citation_index": None,
                }
            )
            continue
        best = max(matches, key=lambda item: item["score"])
        ratio = best["score"] / top_score
        level = "direct" if ratio >= 0.55 else "partial"
        support.append(
            {
                "paper_id": paper.get("id"),
                "title": paper.get("title") or "未命名论文",
                "level": level,
                "label": "直接证据" if level == "direct" else "部分相关",
                "score": best["score"],
                "citation_index": best["index"],
            }
        )
    summary = {
        "direct": sum(1 for item in support if item["level"] == "direct"),
        "partial": sum(1 for item in support if item["level"] == "partial"),
        "none": sum(1 for item in support if item["level"] == "none"),
    }
    return support, summary


def _confidence(citations: list[dict], support_summary: dict) -> dict:
    if not citations:
        return {"level": "low", "label": "低", "reason": "没有检索到可引用证据。"}
    top = max(item["score"] for item in citations)
    direct = support_summary.get("direct", 0)
    if top >= 2.5 and direct >= 1:
        return {
            "level": "high",
            "label": "高",
            "reason": f"首位证据得分 {top:.2f}，且 {direct} 篇论文有直接证据。",
        }
    if top >= 1.2:
        return {
            "level": "medium",
            "label": "中",
            "reason": f"检索到相关证据，但跨论文覆盖或得分有限（首位 {top:.2f}）。",
        }
    return {
        "level": "low",
        "label": "低",
        "reason": f"证据得分偏低（首位 {top:.2f}），建议换关键词或上传全文。",
    }


def _extractive_answer(
    question: str,
    citations: list[dict],
    plan: list[dict],
    support_summary: dict,
) -> str:
    if not citations:
        return "当前语料里没有检索到可引用的证据。可以换一组关键词，或在“我的库”中收藏更多论文后扩大检索范围。"
    lines = []
    if len(plan) > 1:
        lines.append("按问题拆解整理原文证据：")
        for aspect in plan:
            aspect_cites = [item for item in citations if aspect["label"] in item.get("aspects", [])]
            if not aspect_cites:
                continue
            lines.append(f"【{aspect['label']}】")
            for item in aspect_cites[:2]:
                lines.append(f"[{item['index']}] {_clip(item['quote'], 155)}")
    elif re.search(r"分别|各自|五篇|这几篇|这些工作", question):
        lines.append("按论文整理原文证据：")
        seen = set()
        for item in citations:
            if item["paper_id"] in seen:
                continue
            seen.add(item["paper_id"])
            lines.append(f"[{item['index']}] {item['title'][:46]}：{_clip(item['quote'], 130)}")
    else:
        lines.append(f"与问题最相关的原文证据（{len(citations)} 条）：")
        for item in citations[:4]:
            lines.append(f"[{item['index']}] {_clip(item['quote'], 170)}")
    lines.append(
        f"证据覆盖：直接 {support_summary.get('direct', 0)} 篇、"
        f"部分相关 {support_summary.get('partial', 0)} 篇、"
        f"未命中 {support_summary.get('none', 0)} 篇。"
    )
    lines.append("以上为原文摘录，未做生成式改写；数值请结合测试条件核对。")
    return "\n\n".join(lines)


def _llm_answer(question: str, citations: list[dict], plan: list[dict] | None = None) -> str | None:
    if not config.LLM_ENABLED or not citations:
        return None
    evidence = []
    for index, item in enumerate(citations, start=1):
        aspects = " / ".join(item.get("aspects", []))
        evidence.append(
            f"[{index}] {item['title']} · {item['section']} · p.{item['page']}"
            + (f" · 方面：{aspects}" if aspects else "")
            + f"\n{item['quote']}"
        )
    plan_text = ""
    if plan and len(plan) > 1:
        plan_text = "\n问题拆解：" + "；".join(aspect["label"] for aspect in plan)
    try:
        text = chat(
            [
                {"role": "system", "content": ASK_PROMPT},
                {
                    "role": "user",
                    "content": f"问题：{question}{plan_text}\n\n证据片段：\n\n" + "\n\n".join(evidence),
                },
            ],
            temperature=0.1,
            max_tokens=900,
        ).strip()
    except Exception:
        return None
    if not text:
        return None
    if not re.search(r"\[\d+\]", text):
        text += "\n\n引用：" + " ".join(f"[{index}]" for index in range(1, min(len(citations), 4) + 1))
    return text


def answer(question: str, scope: str = "all", paper_ids: list[str] | None = None,
           top_k: int = 6) -> dict:
    papers = _scope_papers(scope, paper_ids or [])
    passages = _passages(papers)
    candidate_k = max(12, min(top_k * 3, 24))
    ranked, plan = _rank_planned(question, passages, candidate_k, len(papers))
    try:
        ranked, retrieval_meta = retrieval.rank(question, passages, ranked, top_k)
    except Exception:
        retrieval_meta = {
            "mode": "bm25",
            "passages": len(passages),
            "base": len(ranked),
            "fused": len(ranked[:top_k]),
            "embeddings": False,
            "expanded_terms": [],
        }
        ranked = ranked[:top_k]
    citations = []
    for index, item in enumerate(ranked, start=1):
        quantities = _quantities(item["text"])
        citations.append(
            {
                "index": index,
                "paper_id": item["paper_id"],
                "title": item["title"],
                "section": item["section"],
                "page": item["page"],
                "quote": _clip(item["text"], 320),
                "score": item["score"],
                "hybrid_score": item.get("hybrid_score"),
                "aspect": item.get("aspect", ""),
                "aspects": item.get("aspects", []),
                "field": item.get("field") or "",
                "evidence_type": "field" if item.get("field") else "text",
                "quantities": quantities,
            }
        )
    try:
        citation_verification = retrieval.verify_citations(citations, ranked)
        verified_by_index = {
            item["index"]: item["verified"] for item in citation_verification["details"]
        }
        for citation in citations:
            citation["quote_verified"] = bool(verified_by_index.get(citation["index"]))
    except Exception:
        citation_verification = {
            "verified": 0,
            "total": len(citations),
            "unverified_indices": [item["index"] for item in citations],
            "details": [],
        }
        for citation in citations:
            citation["quote_verified"] = False
    support, support_summary = _support_matrix(papers, citations)
    confidence = _confidence(citations, support_summary)
    primary_intent = (
        _detect_intent(question)
        if len(plan) == 1
        else {"id": "multi", "label": "多问题拆解"}
    )
    cited_ids = {item["paper_id"] for item in citations}
    coverage = [
        {
            "paper_id": paper.get("id"),
            "title": paper.get("title") or "未命名论文",
            "matched": paper.get("id") in cited_ids,
            "level": next(
                (entry["level"] for entry in support if entry["paper_id"] == paper.get("id")),
                "none",
            ),
        }
        for paper in papers
    ]
    aspect_payload = [
        {
            "label": aspect["label"],
            "intent": aspect["intent"],
            "intent_label": aspect["intent_label"],
            "citation_count": sum(
                1 for item in citations if aspect["label"] in item.get("aspects", [])
            ),
        }
        for aspect in plan
    ]
    generated = _llm_answer(question, citations, plan)
    mode = "llm" if generated else "extractive"
    text = generated or _extractive_answer(question, citations, plan, support_summary)
    quantity_count = sum(len(item.get("quantities", [])) for item in citations)
    return {
        "question": question,
        "answer": text,
        "mode": mode,
        "scope": scope,
        "scope_label": SCOPE_LABELS.get(scope, "全部语料"),
        "intent": primary_intent,
        "plan": aspect_payload,
        "citations": citations,
        "coverage": coverage,
        "support": support,
        "support_summary": support_summary,
        "confidence": confidence,
        "matched_papers": support_summary["direct"] + support_summary["partial"],
        "papers_considered": len(papers),
        "passages_searched": len(passages),
        "quantity_count": quantity_count,
        "retrieval": retrieval_meta,
        "citation_verification": citation_verification,
        "note": (
            "答案由配置的模型基于检索证据生成，引用编号对应下方证据卡片；"
            "检索置信度只表示匹配强度，不代表结论正确。"
            if mode == "llm"
            else "离线模式：答案为原文抽取与整理，未做生成式改写；检索置信度只表示匹配强度，数值请回到测试条件核对。"
        ),
        "trace": [
            {
                "step": "Planner",
                "detail": f"{len(plan)} 个方面 · {primary_intent['label']}",
            },
            {"step": "Retriever", "detail": f"扫描 {len(papers)} 篇论文、{len(passages)} 个片段"},
            {
                "step": "Ranker",
                "detail": f"{retrieval_meta.get('mode', 'bm25')} 融合 + 章节意图，保留 {len(citations)} 条",
            },
            {
                "step": "Critic",
                "detail": f"直接 {support_summary['direct']} / 部分 {support_summary['partial']} / 未命中 {support_summary['none']}，数值 {quantity_count} 个",
            },
            {"step": "Synthesizer", "detail": "模型综合" if mode == "llm" else "抽取式整理"},
        ],
    }
