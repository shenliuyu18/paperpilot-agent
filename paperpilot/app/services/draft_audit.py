"""Draft audit rules.

The audit is read-only: it reports issues and suggests a block status, but it
never rewrites block text. ``draft_service.audit_draft`` applies the returned
status map, bumps the draft revision and records a summary decision.
"""

from __future__ import annotations

import re

# Single-character stop words are dropped before measuring overlap; multi
# character entries are kept for parity with the project's other heuristics.
_STOP = set("的 了 和 与 及 在 是 为 对 从 到 中 一种 一个 我们 本文 该 这 那 有 或".split())
_TOKEN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")
_NUMBER = re.compile(r"\d+(?:\.\d+)?%?")

# A block longer than this is treated as a claim-bearing paragraph and must
# cite at least one project evidence item. Short labels/headings stay exempt;
# this matches design section 8.3 ("text length > 40 characters").
MIN_EVIDENCE_CHARS = 40
OVERLAP_THRESHOLD = 0.35


def _text(value) -> str:
    return str(value or "")


def _id_list(value) -> list[str]:
    """Return a clean list of non-empty string ids from a possibly bad value."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def _tokens(text) -> set[str]:
    lowered = _text(text).lower()
    return {item for item in _TOKEN.findall(lowered) if item not in _STOP}


def _numbers(text) -> set[str]:
    return set(_NUMBER.findall(_text(text)))


def _overlap(block_text: str, evidence_text: str) -> float:
    block = _tokens(block_text)
    evidence = _tokens(evidence_text)
    if not block:
        return 1.0
    return len(block & evidence) / len(block)


def _evidence_index(memory: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for item in (memory.get("evidence") if isinstance(memory, dict) else None) or []:
        if isinstance(item, dict) and item.get("id"):
            index[str(item["id"])] = item
    return index


def _citation_keys(draft: dict) -> set[str]:
    citations = draft.get("citations")
    if not isinstance(citations, dict):
        return set()
    return {
        str(item["key"])
        for item in citations.values()
        if isinstance(item, dict) and item.get("key")
    }


def _issue(
    section_id: str,
    block_id: str,
    kind: str,
    severity: str,
    message: str,
    evidence_ids: list[str] | None = None,
) -> dict:
    return {
        "section_id": section_id,
        "block_id": block_id,
        "kind": kind,
        "severity": severity,
        "message": message,
        "evidence_ids": list(evidence_ids or []),
    }


def _sections(draft: dict) -> list:
    sections = draft.get("sections") if isinstance(draft, dict) else None
    return sections if isinstance(sections, list) else []


def audit_draft(draft: dict, memory: dict) -> dict:
    """Audit a structured draft against project memory.

    Returns ``{"issues", "counts", "status_map"}`` where every block with
    content and an id is mapped to ``checked`` or ``needs_evidence``.
    """
    if not isinstance(draft, dict):
        draft = {}
    if not isinstance(memory, dict):
        memory = {}
    evidence = _evidence_index(memory)
    citations = _citation_keys(draft)
    issues: list[dict] = []
    status_map: dict[str, str] = {}
    seen_texts: dict[str, str] = {}

    for section in _sections(draft):
        if not isinstance(section, dict):
            section = {}
        section_id = _text(section.get("id")).strip()
        blocks = section.get("blocks")
        blocks = blocks if isinstance(blocks, list) else []
        if not any(
            _text(block.get("text")).strip()
            for block in blocks
            if isinstance(block, dict)
        ):
            issues.append(_issue(
                section_id, "", "empty_section", "medium",
                "章节没有任何非空块。",
            ))
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_id = _text(block.get("id")).strip()
            text = _text(block.get("text")).strip()
            if not text:
                continue
            block_high = False
            normalized = re.sub(r"\s+", "", text)
            if normalized in seen_texts:
                issues.append(_issue(
                    section_id, block_id, "duplicate_block", "low",
                    f"与块 {seen_texts[normalized]} 文本重复。",
                ))
            else:
                seen_texts[normalized] = block_id

            evidence_ids = _id_list(block.get("evidence_ids"))
            if not evidence_ids and len(text) > MIN_EVIDENCE_CHARS:
                block_high = True
                issues.append(_issue(
                    section_id, block_id, "missing_evidence", "high",
                    "段落没有绑定证据。",
                ))
            unknown_evidence = [
                item for item in evidence_ids if item not in evidence
            ]
            if unknown_evidence:
                block_high = True
                issues.append(_issue(
                    section_id, block_id, "unknown_evidence", "high",
                    f"证据不存在：{unknown_evidence[0]}",
                    unknown_evidence,
                ))
            unknown_citation = [
                item for item in _id_list(block.get("citation_keys"))
                if item not in citations
            ]
            if unknown_citation:
                block_high = True
                issues.append(_issue(
                    section_id, block_id, "unknown_citation", "high",
                    f"引用不存在：{unknown_citation[0]}",
                ))

            bound = [evidence[item] for item in evidence_ids if item in evidence]
            if bound:
                evidence_text = " ".join(
                    _text(item.get("quote")) for item in bound
                )
                if _overlap(text, evidence_text) < OVERLAP_THRESHOLD:
                    issues.append(_issue(
                        section_id, block_id, "unsupported_claim", "medium",
                        "段落与绑定证据的词重合度偏低。",
                        evidence_ids,
                    ))
                missing_numbers = _numbers(text) - _numbers(evidence_text)
                if missing_numbers:
                    block_high = True
                    shown = "、".join(sorted(missing_numbers)[:3])
                    issues.append(_issue(
                        section_id, block_id, "numeric_mismatch", "high",
                        f"段落数值未在绑定证据中出现：{shown}",
                        evidence_ids,
                    ))
            for claim in block.get("claims") or []:
                if not isinstance(claim, dict):
                    continue
                stance = claim.get("stance")
                claim_text = _text(claim.get("text"))[:60]
                if stance == "unsupported":
                    block_high = True
                    issues.append(_issue(
                        section_id, block_id, "claim_unsupported", "high",
                        f"主张未被绑定证据支持：{claim_text}",
                        _id_list(claim.get("evidence_ids")),
                    ))
                elif stance == "partial":
                    issues.append(_issue(
                        section_id, block_id, "claim_partial", "medium",
                        f"主张仅被部分证据覆盖：{claim_text}",
                        _id_list(claim.get("evidence_ids")),
                    ))
            if block_id:
                status_map[block_id] = (
                    "needs_evidence" if block_high else "checked"
                )

    if not _text(draft.get("title")).strip():
        issues.append(_issue(
            "", "", "missing_title", "low", "草稿标题为空。",
        ))

    counts = {
        "high": sum(1 for item in issues if item["severity"] == "high"),
        "medium": sum(1 for item in issues if item["severity"] == "medium"),
        "low": sum(1 for item in issues if item["severity"] == "low"),
        "total": len(issues),
    }
    return {"issues": issues, "counts": counts, "status_map": status_map}
