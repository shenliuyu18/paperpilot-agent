"""Citation grounding checks for generated answers and draft claims.

This module never rewrites user text and never decides whether a claim is true.
It only answers a narrower question: can every citation be resolved to a real
context item, and do numeric claims appear in the item they cite?
"""

from __future__ import annotations

import re

_CITATION_RE = re.compile(r"\[(\d+)\]")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_NORMALIZE_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？])\s*|(?<=[.!?])(?=\s)|[\r\n]+")
_HEADING_RE = re.compile(r"^\s*(#{1,6}\s|[-*]\s)")

_INSUFFICIENT_MARKERS = (
    "证据不足",
    "没有证据",
    "暂无证据",
    "未提及",
    "无法判断",
    "不足以",
    "现有材料不足",
)

_CLAIM_MARKERS = (
    "表明",
    "显示",
    "说明",
    "证明",
    "达到",
    "提升",
    "降低",
    "减少",
    "增加",
    "优于",
    "高于",
    "低于",
    "超过",
    "支持",
    "发现",
    "实验",
    "结果",
    "论文",
    "研究",
    "作者",
    "能耗",
    "精度",
    "吞吐",
)


def normalize(text: str) -> str:
    """Normalize text for substring checks (case/space/punctuation insensitive)."""
    return _NORMALIZE_RE.sub("", str(text or "").lower())


def numbers(text: str) -> set[str]:
    return {match.group(0) for match in _NUMBER_RE.finditer(str(text or ""))}


def citation_indices(text: str) -> list[int]:
    seen: list[int] = []
    for match in _CITATION_RE.finditer(str(text or "")):
        value = int(match.group(1))
        if value not in seen:
            seen.append(value)
    return seen


def sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(str(text or ""))]
    return [part for part in parts if part and len(part) >= 8]


def _looks_like_claim(sentence: str) -> bool:
    if sentence.endswith(("？", "?")):
        return False
    if any(marker in sentence for marker in _INSUFFICIENT_MARKERS):
        return False
    if _HEADING_RE.match(sentence):
        return False
    if len(sentence) < 18:
        return False
    return bool(numbers(sentence)) or any(marker in sentence for marker in _CLAIM_MARKERS)


def validate_answer(
    answer: str,
    passages: list[dict],
    insufficient: bool = False,
) -> dict:
    """Return a grounding report for a generated answer.

    ``passages`` must carry ``index`` and ``text``. The report is deliberately
    conservative: unverified sentences are counted and surfaced, never removed.
    """
    by_index = {
        int(item.get("index")): item
        for item in passages or []
        if item.get("index") is not None
    }
    indices = citation_indices(answer)
    valid_indices = [index for index in indices if index in by_index]
    out_of_range = [index for index in indices if index not in by_index]
    unsupported_numbers: list[dict] = []
    unsupported_sentences: list[str] = []
    claim_sentences = 0

    for sentence in sentences(answer):
        if any(marker in sentence for marker in _INSUFFICIENT_MARKERS):
            continue
        if _HEADING_RE.match(sentence) and not numbers(sentence):
            continue
        sentence_indices = [
            index for index in citation_indices(sentence) if index in by_index
        ]
        # Citation markers themselves contain digits; never treat ``[1]`` as
        # a numeric claim.
        sentence_numbers = numbers(_CITATION_RE.sub(" ", sentence))
        if sentence_numbers:
            if not sentence_indices:
                claim_sentences += 1
                unsupported_sentences.append(sentence)
                continue
            haystack = " ".join(
                str(by_index[index].get("text") or "") for index in sentence_indices
            )
            haystack_numbers = numbers(haystack)
            missing = sorted(
                number for number in sentence_numbers if number not in haystack_numbers
            )
            claim_sentences += 1
            if missing:
                unsupported_numbers.append({"sentence": sentence, "missing": missing})
            continue
        if _looks_like_claim(sentence):
            claim_sentences += 1
            if not sentence_indices:
                unsupported_sentences.append(sentence)

    if insufficient:
        status = "insufficient"
    elif out_of_range:
        status = "invalid"
    elif not valid_indices:
        # A no-evidence answer with no factual claim is an honest
        # "insufficient" answer, not a partially grounded one.
        status = "insufficient" if claim_sentences == 0 else "partial"
    elif unsupported_numbers or unsupported_sentences:
        status = "partial"
    else:
        status = "grounded"

    return {
        "status": status,
        "cited": len(valid_indices),
        "out_of_range": len(out_of_range),
        "out_of_range_indices": out_of_range,
        "unsupported_numbers": unsupported_numbers,
        "unsupported_sentences": unsupported_sentences,
        "claim_sentences": claim_sentences,
        "unsupported_sentence_rate": round(
            len(unsupported_sentences) / max(1, claim_sentences), 4
        ),
    }


def quote_supported(quote: str, text: str) -> bool:
    """True when ``quote`` is a normalized substring of ``text``."""
    normalized_quote = normalize(str(quote or "").rstrip("…"))
    return bool(normalized_quote) and normalized_quote in normalize(text)


# --- claim-level support classification (v5.0) -------------------------------
# The functions below answer a narrower question than "is this true": given the
# evidence a claim is bound to, is the claim fully, partially or not supported,
# and which sentence of the evidence argues against it. Block-level callers
# (draft_semantic) share the tokenizer/overlap primitives but pass their own
# thresholds, because block granularity differs from claim granularity.

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]*|\d+(?:\.\d+)?|[\u4e00-\u9fff]+")
_NEGATIONS = ("不", "没有", "未", "无", "非", "不能", "无法", "并未", "尚未")
_NEGATION_EN = re.compile(
    r"\b(?:not|no|never|without|cannot|can't|doesn't|don't|didn't|isn't|aren't|fail(?:s|ed)?|unable)\b",
    re.IGNORECASE,
)
_UP_WORDS = (
    "提升", "提高", "增加", "增长", "更高", "优于", "上升", "改善", "增强",
    "increase", "increases", "increased", "improve", "improves", "improved",
    "higher", "better", "gain", "gains",
)
_DOWN_WORDS = (
    "降低", "减少", "下降", "更低", "劣于", "恶化", "削弱",
    "decrease", "decreases", "decreased", "reduce", "reduces", "reduced",
    "lower", "worse", "drop", "drops", "cut", "cuts",
)
STANCES = ("supported", "partial", "unsupported")


def tokens(text: str) -> set[str]:
    """Token set: ASCII words/numbers plus CJK character bigrams."""
    out: set[str] = set()
    for match in _TOKEN_RE.finditer(str(text or "").lower()):
        part = match.group(0)
        if part[0].isascii():
            if len(part) > 1:
                out.add(part)
        elif len(part) == 1:
            out.add(part)
        else:
            out.update(part[index:index + 2] for index in range(len(part) - 1))
    return out


def overlap(text: str, evidence_text: str) -> float:
    """Share of the text's tokens that also appear in the evidence."""
    own = tokens(text)
    if not own:
        return 0.0
    return len(own & tokens(evidence_text)) / len(own)


def negated(text: str) -> bool:
    value = str(text or "")
    if any(word in value for word in _NEGATIONS):
        return True
    return bool(_NEGATION_EN.search(value))


def direction(text: str) -> str:
    """Coarse polarity of a numeric claim: 'up', 'down' or ''."""
    value = str(text or "").lower()
    up = any(word in value for word in _UP_WORDS)
    down = any(word in value for word in _DOWN_WORDS)
    if up == down:
        return ""
    return "up" if up else "down"


_MARKER_RE = re.compile(r"\d+(?:\.\d+)?$")
_MARKER_STOP = {
    "the", "and", "with", "for", "from", "this", "that", "these", "those",
    "using", "based", "paper", "work", "propose", "proposed", "present",
    "presented", "result", "results", "achieve", "achieves", "achieved",
    "than", "more", "less", "into", "under", "over", "such", "which", "while",
    "have", "has", "been", "are", "was", "were", "can", "our", "its", "their",
    "not", "but", "all", "also", "via", "method", "approach", "show", "shows",
}


def markers(text: str) -> set[str]:
    """System/method names and other distinctive Latin tokens in a sentence."""
    return {
        token for token in tokens(text)
        if token[0].isascii() and len(token) >= 3
        and token not in _MARKER_STOP and not _MARKER_RE.fullmatch(token)
    }


def _clip(text: str, limit: int = 240) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[:limit] + "…"


def _counter_sentence(evidence_text: str, claim: str) -> str:
    """Pick the evidence sentence least aligned with the claim, for the user to read."""
    parts = [part for part in sentences(evidence_text) if part]
    if not parts:
        return _clip(evidence_text)
    own = tokens(claim)
    ranked = sorted(parts, key=lambda part: len(own & tokens(part)))
    return _clip(ranked[0] if ranked else evidence_text)


def classify_claim(
    claim: str,
    evidence_text: str,
    *,
    support_overlap: float | None = None,
    partial_overlap: float | None = None,
    length_ratio: float | None = None,
) -> dict:
    """Classify how well ``evidence_text`` supports ``claim``.

    Returns ``{stance, reason, counter_quote, signals}``. This is a support
    relation check, not a truth verdict: a claim can be ``unsupported`` by the
    bound evidence while still being true in the world, and the reason string
    says which check failed so the user can judge.
    """
    from .. import config

    support = config.CLAIM_SUPPORT_OVERLAP if support_overlap is None else support_overlap
    partial = config.CLAIM_PARTIAL_OVERLAP if partial_overlap is None else partial_overlap
    max_ratio = config.CLAIM_LENGTH_RATIO if length_ratio is None else length_ratio
    text = " ".join(str(claim or "").split())
    evidence = " ".join(str(evidence_text or "").split())
    signals = {
        "overlap": 0.0,
        "missing_numbers": [],
        "numbers_absent": False,
        "number_overlap": None,
        "marker_overlap": None,
        "polarity_conflict": False,
        "direction_conflict": False,
    }
    if not text:
        return {"stance": "unsupported", "reason": "主张为空。", "counter_quote": "", "signals": signals}
    if not evidence:
        return {
            "stance": "unsupported",
            "reason": "未绑定证据，无法判断支持关系。",
            "counter_quote": "",
            "signals": signals,
        }

    shared = overlap(text, evidence)
    signals["overlap"] = round(shared, 4)
    claim_numbers = numbers(text)
    evidence_numbers = numbers(evidence)
    missing = sorted(claim_numbers - evidence_numbers)
    signals["missing_numbers"] = missing
    signals["numbers_absent"] = bool(claim_numbers) and not (claim_numbers & evidence_numbers)
    signals["number_overlap"] = (
        round(len(claim_numbers & evidence_numbers) / len(claim_numbers), 4)
        if claim_numbers else None
    )
    claim_markers = markers(text)
    shared_markers = claim_markers & tokens(evidence)
    signals["marker_overlap"] = (
        round(len(shared_markers) / len(claim_markers), 4) if claim_markers else None
    )
    claim_size = len(tokens(text))
    evidence_size = len(tokens(evidence)) or 1
    ratio = claim_size / evidence_size
    signals["length_ratio"] = round(ratio, 4)

    # Order matters. Low overlap first: evidence about something else must not be
    # rescued into "partial" just because the claim happens to contain numbers the
    # evidence lacks. A claim that asserts materially more than the evidence is
    # only partially covered even when every shared token matches.
    if shared < partial:
        stance = "unsupported"
        reason = "关键词重合度低，绑定证据没有覆盖该主张。"
    elif shared < support and signals["number_overlap"] is not None \
            and signals["number_overlap"] < 0.5:
        # Same-domain sentence from another paper: the vocabulary overlaps enough
        # to clear the partial floor, but most claimed measurements do not exist
        # in the evidence, so the evidence is about a different system.
        stance = "unsupported"
        reason = "主张中的数值大多未在绑定证据中出现，证据描述的是另一组数据。"
    elif shared < support and claim_markers and signals["marker_overlap"] < 0.25:
        stance = "unsupported"
        reason = "主张中的方法或系统名称大多未出现在绑定证据中。"
    elif ratio > max_ratio:
        stance = "partial"
        reason = "主张明显长于所引证据，证据只能覆盖其中一部分。"
    elif missing:
        stance = "partial"
        reason = "正文数值未在绑定证据中出现：" + "、".join(missing)
    elif shared >= support:
        stance = "supported"
        reason = "关键词重合度高，绑定证据可以直接支持该主张。"
    else:
        stance = "partial"
        reason = "关键词部分重合，证据只覆盖该主张的一部分。"

    claim_negative = negated(text)
    evidence_negative = negated(evidence)
    counter = ""
    if claim_negative != evidence_negative:
        signals["polarity_conflict"] = True
        counter = _counter_sentence(evidence, text)
        stance = {"supported": "partial", "partial": "unsupported"}.get(stance, "unsupported")
        reason = "主张与证据的否定极性相反：" + reason

    claim_direction = direction(text)
    evidence_direction = direction(evidence)
    if claim_direction and evidence_direction and claim_direction != evidence_direction:
        signals["direction_conflict"] = True
        counter = _counter_sentence(evidence, text)
        stance = "unsupported"
        reason = "主张与证据的数值方向相反（" + claim_direction + " vs " + evidence_direction + "）。"

    return {"stance": stance, "reason": reason, "counter_quote": counter, "signals": signals}
