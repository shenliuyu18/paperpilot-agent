"""Rule-based reviewer simulation and response scaffold.

This is a self-check written in the shape of a review: it reuses the draft audit,
pre-flight, bounded claims and stored contradictions. It is explicitly **not** a
peer review and never predicts an editor's decision; the recommendation is a
summary of rule findings for the author's own triage.
"""

from __future__ import annotations

from . import contradictions, draft_audit, draft_state, grounding, manuscript, project_state

DISCLAIMER = (
    "这是基于草稿核查、投稿前检查与主张判定的规则自查，不是同行评审，"
    "也不代表任何期刊的编辑决定。"
)
MAX_COMMENTS = 20
MAX_COMMENT_CHARS = 2000


def _block_stats(draft: dict) -> dict:
    paragraphs = []
    supported = partial = unsupported = 0
    for section in draft.get("sections") or []:
        for block in section.get("blocks") or []:
            if not str(block.get("text") or "").strip():
                continue
            if str(block.get("type") or "paragraph") == "paragraph":
                paragraphs.append(block)
            for claim in block.get("claims") or []:
                stance = claim.get("stance")
                if stance == "supported":
                    supported += 1
                elif stance == "partial":
                    partial += 1
                elif stance == "unsupported":
                    unsupported += 1
    bound = sum(1 for block in paragraphs if block.get("evidence_ids"))
    return {
        "paragraphs": len(paragraphs),
        "bound_paragraphs": bound,
        "coverage": round(bound / len(paragraphs), 4) if paragraphs else 0.0,
        "supported": supported,
        "partial": partial,
        "unsupported": unsupported,
    }


def _dedupe(items: list[dict]) -> list[dict]:
    seen = set()
    rows = []
    for item in items:
        key = (item.get("message"), item.get("block_id"), item.get("section_id"))
        if key in seen:
            continue
        seen.add(key)
        rows.append(item)
    return rows


def report(project_id: str) -> dict:
    draft = draft_state.read_draft(project_id)
    memory = project_state.read_memory(project_id)
    preflight = manuscript.preflight(project_id)
    audit = draft_audit.audit_draft(draft, memory)
    stats = _block_stats(draft)
    stored = contradictions.read(project_id)
    open_conflicts = [item for item in stored.get("items") or []
                      if item.get("status") != "dismissed"]

    major: list[dict] = []
    minor: list[dict] = []
    for item in preflight.get("items") or []:
        row = {
            "kind": item.get("kind") or "preflight",
            "message": item.get("message") or "",
            "target": item.get("target") or {},
        }
        (major if item.get("level") == "error" else minor).append(row)
    for issue in audit.get("issues") or []:
        row = {
            "kind": issue.get("kind") or "audit",
            "message": issue.get("message") or "",
            "target": {"block": issue.get("block_id") or "", "section": issue.get("section_id") or ""},
        }
        if issue.get("severity") == "high":
            major.append(row)
        else:
            minor.append(row)
    for item in open_conflicts:
        other = (item.get("paper_b") or {}).get("title") or "另一篇论文"
        major.append({
            "kind": "contradiction",
            "message": f"与《{other}》在「{item.get('metric')}」上方向相反：{item.get('why') or ''}"[:300],
            "target": {"contradiction": item.get("id"), "blocks": item.get("draft_blocks") or []},
        })
    major = _dedupe(major)
    minor = _dedupe([item for item in minor if item not in major])

    strengths: list[str] = []
    if stats["supported"]:
        strengths.append(f"{stats['supported']} 条主张被所绑证据直接支持。")
    if stats["coverage"] >= 0.8:
        strengths.append(f"正文段落证据覆盖 {stats['coverage']:.0%}。")
    if int(preflight.get("references") or 0) > 0:
        strengths.append(f"已登记 {preflight['references']} 条参考文献。")
    if not open_conflicts:
        strengths.append("项目内没有未处理的跨论文矛盾。")
    if not strengths:
        strengths.append("暂无可自动确认的优点；请先补充证据与参考文献。")

    questions: list[str] = []
    for item in (memory.get("open_questions") or [])[:5]:
        if item.get("status", "open") != "open":
            continue
        text = str(item.get("question") or "").strip()
        if text:
            questions.append(text)
    for section in draft.get("sections") or []:
        for block in section.get("blocks") or []:
            for claim in block.get("claims") or []:
                if claim.get("stance") == "unsupported" and len(questions) < 8:
                    text = str(claim.get("text") or "").strip()
                    if text:
                        questions.append("请说明该主张的证据来源，或删除/改写：" + text[:120])

    if major:
        recommendation, label = "major_revision", "需要大修（规则自查）"
    elif minor:
        recommendation, label = "minor_revision", "需要小修（规则自查）"
    else:
        recommendation, label = "ready_for_review", "可进入人工审阅（规则自查）"
    return {
        "project_id": project_id,
        "generated_at": project_state._now(),
        "stats": stats,
        "preflight": {"counts": preflight.get("counts"), "blocking": preflight.get("blocking")},
        "strengths": strengths,
        "major": major[:20],
        "minor": minor[:30],
        "questions": questions[:10],
        "recommendation": recommendation,
        "recommendation_label": label,
        "disclaimer": DISCLAIMER,
    }


def _blocks(draft: dict) -> list[dict]:
    rows = []
    for section in draft.get("sections") or []:
        for block in section.get("blocks") or []:
            text = " ".join(str(block.get("text") or "").split())
            if not text:
                continue
            rows.append({
                "block_id": block.get("id") or "",
                "section_id": section.get("id") or "",
                "section": section.get("title") or "",
                "text": text,
                "evidence_ids": block.get("evidence_ids") or [],
                "citation_keys": block.get("citation_keys") or [],
            })
    return rows


def map_comments(project_id: str, comments) -> dict:
    """Rank draft blocks for each reviewer comment. Read-only; no drafting."""
    if not isinstance(comments, list) or not comments:
        raise ValueError("comments 必须是非空数组。")
    if len(comments) > MAX_COMMENTS:
        raise ValueError(f"单次最多映射 {MAX_COMMENTS} 条审稿意见。")
    clean = []
    for item in comments:
        text = " ".join(str(item or "").split())
        if not text:
            continue
        if len(text) > MAX_COMMENT_CHARS:
            raise ValueError(f"单条审稿意见不能超过 {MAX_COMMENT_CHARS} 个字符。")
        clean.append(text)
    if not clean:
        raise ValueError("没有可映射的审稿意见。")
    rows = _blocks(draft_state.read_draft(project_id))
    items = []
    for comment in clean:
        scored = []
        for block in rows:
            score = grounding.overlap(comment, block["text"])
            if score > 0:
                scored.append({**block, "score": round(score, 4)})
        scored.sort(key=lambda item: (-item["score"], item["block_id"]))
        matches = scored[:3]
        if matches:
            first = matches[0]
            suggestion = (
                f"感谢指出。我们已定位到「{first['section'] or '正文'}」中的段落 "
                f"{first['block_id'] or ''}，将据此补充/修改并在回复中说明。"
            )
        else:
            suggestion = "这条意见暂未定位到具体段落；需要在正文补充对应内容或说明不适用。"
        items.append({
            "comment": comment,
            "matches": matches,
            "suggestion": suggestion,
        })
    return {
        "project_id": project_id,
        "comments": len(items),
        "items": items,
        "disclaimer": "匹配是词法重合排序，只用于定位段落，不代表对审稿意见的理解或同意。",
    }


def response_template(project_id: str, comments=None) -> str:
    data = report(project_id)
    mapped = map_comments(project_id, comments) if comments else None
    lines = [
        "# Response to Reviewers（模板）",
        "",
        f"> {DISCLAIMER}",
        "",
        "## 修改摘要",
        "",
        "（用 3–5 条说明本轮修改；每条对应下面的意见编号。）",
        "",
        "## 自查问题",
        "",
    ]
    issues = data["major"] + data["minor"]
    if not issues:
        lines.append("（规则自查没有发现问题。）")
    else:
        lines += ["| # | 问题 | 处理 | 位置 |", "|---|---|---|---|"]
        for index, item in enumerate(issues, start=1):
            target = item.get("target") or {}
            location = target.get("block") or target.get("panel") or target.get("contradiction") or ""
            lines.append(f"| {index} | {item.get('message') or ''} | （待填写） | {location} |")
    lines += [
        "",
        "## 审稿人意见",
        "",
    ]
    if mapped:
        lines += ["| # | Reviewer comment | 建议定位 | Response（逐条回应） | Changes made |",
                  "|---|---|---|---|---|"]
        for index, item in enumerate(mapped["items"], start=1):
            first = (item["matches"] or [{}])[0]
            location = first.get("block_id") or "正文（未匹配）"
            section = first.get("section") or ""
            lines.append(
                f"| {index} | {item['comment']} | {section} {location} | （待填写） | （待填写） |"
            )
    else:
        lines += [
            "| # | Reviewer comment（粘贴原文） | Response（逐条回应） | Changes made | Location |",
            "|---|---|---|---|---|",
            "| 1 |  |  |  |  |",
            "| 2 |  |  |  |  |",
        ]
    lines += [
        "",
        "## 需要作者确认的问题",
        "",
    ]
    for question in data["questions"]:
        lines.append(f"- {question}")
    if not data["questions"]:
        lines.append("- （暂无）")
    lines.append("")
    return "\n".join(lines)
