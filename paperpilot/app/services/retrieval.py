from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict

from .. import config
from .. import llm
from . import search

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]*|[\u4e00-\u9fff]+")
_NORMALIZE_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer((text or "").lower()):
        part = match.group(0)
        if part[0].isascii():
            if len(part) > 1:
                tokens.append(part)
        elif len(part) == 1:
            tokens.append(part)
        else:
            tokens.extend(part[index:index + 2] for index in range(len(part) - 1))
    return tokens


def _expand(question: str) -> tuple[list[str], list[str], str]:
    base = _tokenize(question)
    try:
        expanded_text = search._expand_query(question)
    except Exception:
        expanded_text = question
    expanded = _tokenize(expanded_text)
    extra = [token for token in dict.fromkeys(expanded) if token not in set(base)]
    return base, extra, expanded_text


def _key(item: dict) -> tuple:
    text = str(item.get("text") or "")
    return (
        item.get("paper_id"),
        item.get("section") or "",
        item.get("page") or 0,
        hashlib.sha1(text.encode("utf-8")).hexdigest()[:12],
    )


def _lexical_scores(question: str, passages: list[dict]) -> tuple[dict, list[str]]:
    base, extra, _ = _expand(question)
    docs = [_tokenize(str(item.get("text") or "")) for item in passages]
    if not docs:
        return {}, base + extra
    df = Counter()
    for tokens in docs:
        df.update(set(tokens))
    total = len(docs)
    idf = {token: math.log((total + 1) / (count + 1)) + 1.0 for token, count in df.items()}
    weights: dict[str, float] = {}
    for token in base:
        weights[token] = max(weights.get(token, 0.0), 1.6)
    for token in extra:
        weights[token] = max(weights.get(token, 0.0), 0.8)
    query_norm = math.sqrt(sum((weight * idf.get(token, 1.0)) ** 2 for token, weight in weights.items())) or 1.0
    scores: dict[tuple, float] = {}
    for item, tokens in zip(passages, docs):
        counts = Counter(tokens)
        dot = sum(
            weight * idf.get(token, 1.0) * counts.get(token, 0)
            for token, weight in weights.items()
        )
        doc_norm = math.sqrt(
            sum((count * idf.get(token, 1.0)) ** 2 for token, count in counts.items())
        ) or 1.0
        scores[_key(item)] = dot / (query_norm * doc_norm)
    return scores, list(dict.fromkeys(base + extra))


def _embedding_scores(question: str, passages: list[dict]) -> dict[tuple, float] | None:
    if not getattr(config, "EMBEDDING_ENABLED", False):
        return None
    embed = getattr(llm, "embed", None)
    if not callable(embed):
        return None
    subset = passages[:40]
    try:
        vectors = embed([question] + [str(item.get("text") or "") for item in subset])
    except Exception:
        return None
    if not isinstance(vectors, list) or len(vectors) != len(subset) + 1:
        return None
    query = vectors[0]
    qnorm = math.sqrt(sum(float(value) ** 2 for value in query)) or 1.0
    scores: dict[tuple, float] = {}
    for item, vector in zip(subset, vectors[1:]):
        norm = math.sqrt(sum(float(value) ** 2 for value in vector)) or 1.0
        scores[_key(item)] = sum(
            float(left) * float(right) for left, right in zip(query, vector)
        ) / (qnorm * norm)
    return scores


def rank(
    question: str,
    passages: list[dict],
    base_ranked: list[dict] | None,
    top_k: int = 6,
) -> tuple[list[dict], dict]:
    base = list(base_ranked or [])
    _, expanded_terms, _ = _expand(question)
    if not base:
        return [], {
            "mode": "bm25",
            "passages": 0,
            "available": len(passages),
            "base": 0,
            "fused": 0,
            "embeddings": False,
            "expanded_terms": expanded_terms[:24],
        }
    lexical, expanded_terms = _lexical_scores(question, base)
    lexical_order = sorted(
        list(base),
        key=lambda item: (-lexical.get(_key(item), 0.0), str(item.get("paper_id") or "")),
    )
    embeddings = _embedding_scores(question, base)
    embedding_order = []
    if embeddings:
        embedding_order = sorted(
            list(base),
            key=lambda item: (-embeddings.get(_key(item), 0.0), str(item.get("paper_id") or "")),
        )
    base_rank = {_key(item): index for index, item in enumerate(base)}
    lexical_rank = {_key(item): index for index, item in enumerate(lexical_order)}
    fused: dict[tuple, float] = defaultdict(float)
    streams = [(0.85, base), (1.15, lexical_order)]
    if embeddings:
        streams.append((0.9, embedding_order))
    for weight, stream in streams:
        for index, item in enumerate(stream[:40]):
            fused[_key(item)] += weight / (60 + index + 1)
    ordered = sorted(
        base,
        key=lambda item: (
            -fused.get(_key(item), 0.0),
            base_rank.get(_key(item), 9999),
            lexical_rank.get(_key(item), 9999),
        ),
    )
    limit = max(1, min(int(top_k), len(ordered))) if ordered else 0
    result = []
    for item in ordered[:limit]:
        candidate = dict(item)
        candidate["hybrid_score"] = round(fused.get(_key(item), 0.0), 6)
        candidate["lexical_score"] = round(lexical.get(_key(item), 0.0), 6)
        candidate.setdefault("score", round(candidate["lexical_score"] * 10, 3))
        result.append(candidate)
    mode = "embedding" if embeddings else "hybrid"
    meta = {
        "mode": mode,
        "passages": len(base),
        "available": len(passages),
        "base": len(base),
        "fused": len(result),
        "embeddings": bool(embeddings),
        "expanded_terms": expanded_terms[:24],
    }
    return result, meta


def rank_passages(
    question: str,
    passages: list[dict],
    top_k: int = 6,
) -> tuple[list[dict], dict]:
    """Rank arbitrary passages with the same lexical model used by evidence Q&A.

    Callers must give every passage a unique key via ``paper_id``/``section``/
    ``page``/``text``. Assistant context uses ``source_id`` plus an index in
    ``page`` so project evidence, draft blocks and report chunks cannot collide.
    No embeddings are requested here: project assets stay on the offline path.
    """
    items = list(passages or [])
    if not items:
        return [], {"mode": "lexical", "passages": 0, "fused": 0, "expanded_terms": []}
    scores, terms = _lexical_scores(question, items)
    ordered = sorted(
        items,
        key=lambda item: (
            -scores.get(_key(item), 0.0),
            str(item.get("source_id") or item.get("paper_id") or ""),
        ),
    )
    limit = max(1, min(int(top_k), len(ordered)))
    result = []
    for item in ordered[:limit]:
        candidate = dict(item)
        candidate["score"] = round(scores.get(_key(item), 0.0), 6)
        result.append(candidate)
    return result, {
        "mode": "lexical",
        "passages": len(items),
        "fused": len(result),
        "expanded_terms": terms[:24],
    }


def _normalize(text: str) -> str:
    return _NORMALIZE_RE.sub("", str(text or "").lower())


def verify_citations(citations: list[dict], ranked: list[dict]) -> dict:
    by_paper: dict[str, list[str]] = defaultdict(list)
    for item in ranked:
        by_paper[str(item.get("paper_id") or "")].append(_normalize(str(item.get("text") or "")))
    details = []
    unverified = []
    verified = 0
    for citation in citations:
        quote = _normalize(str(citation.get("quote") or "").rstrip("…"))
        found = bool(quote) and any(
            quote in text for text in by_paper.get(str(citation.get("paper_id") or ""), [])
        )
        index = citation.get("index")
        details.append({"index": index, "verified": found})
        if found:
            verified += 1
        else:
            unverified.append(index)
    return {
        "verified": verified,
        "total": len(citations),
        "unverified_indices": unverified,
        "details": details,
    }
