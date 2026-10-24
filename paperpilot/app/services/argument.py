from __future__ import annotations

import re
from collections import Counter

from . import research

CHECKS = (
    ("question", "研究问题", 20, "high", r"研究问题|本文(?:研究|关注|旨在|要回答)|本研究|我们(?:研究|关注)|research question|this (?:paper|study) (?:investigates|examines|addresses)"),
    ("gap", "研究缺口", 15, "medium", r"缺口|空白|尚未|缺乏|不足|未解决|难以|gap|remains?|limited"),
    ("method", "方法与设计", 15, "high", r"方法|提出|框架|算法|设计|模型|method|approach|framework|propose"),
    ("evidence", "结果与证据", 20, "high", r"实验|结果|表明|显示|提升|降低|%|数据集|评估|experiment|result|evaluat|dataset|improv"),
    ("discussion", "讨论与机制", 10, "medium", r"讨论|解释|机制|原因|启示|discussion|implication|mechanism"),
    ("limitation", "局限与风险", 10, "medium", r"局限|不足|边界|风险|未来工作|limitation|future work|threat"),
    ("conclusion", "结论", 10, "high", r"结论|总结|综上|本文(?:证明|说明)|conclusion|in summary|we conclude"),
)

SUGGESTIONS = {
    "question": "在引言开头用一句话写清“本文要回答什么”，并让方法与结论都回应它。",
    "gap": "用“已有工作做到了什么 + 仍缺什么 + 为什么重要”三句建立缺口，不要只写“研究较少”。",
    "method": "补一节方法/设计，说明关键选择、与 baseline 的差异和适用边界。",
    "evidence": "补结果段：数据、指标、对照与失败条件；没有实测数字时不要写强结论。",
    "discussion": "从结果推进到解释：为什么会这样，与已有工作一致或矛盾在哪里。",
    "limitation": "主动写局限、风险与未验证条件，避免把摘要级证据当成全条件结论。",
    "conclusion": "补结论段，只回答引言提出的问题，并给出边界与下一步。",
}


def _headings(text: str) -> list[tuple[int, int, str]]:
    result = []
    for match in re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", text or ""):
        result.append((match.start(), match.end(), match.group(2).strip()))
    return result


def _sentence_count(text: str) -> int:
    parts = [
        part.strip()
        for part in re.split(r"[。！？!?]+|\n+|(?<=\.)\s+", text or "")
        if len(part.strip()) >= 8
    ]
    return len(parts)


def _match_snippet(content: str, match: re.Match) -> str:
    starts = [content.rfind("。", 0, match.start()), content.rfind(". ", 0, match.start())]
    start = max(starts)
    start = 0 if start < 0 else start + 1
    ends = [position for position in (content.find("。", match.end()), content.find(". ", match.end())) if position >= 0]
    end = min(ends) + 1 if ends else len(content)
    return content[start:end].strip()[:220]


def structure_review(text: str, topic: str = "") -> dict:
    source = str(text or "")
    headings = _headings(source)
    sections = []
    if headings:
        for index, (start, end, title) in enumerate(headings):
            next_start = headings[index + 1][0] if index + 1 < len(headings) else len(source)
            content = source[end:next_start].strip()
            sections.append(
                {
                    "title": title,
                    "level": source[start:end].split()[0].count("#"),
                    "content": content,
                    "start": end,
                    "end": next_start,
                    "citations": len(re.findall(r"\[P\d+\]", content)),
                    "sentences": _sentence_count(content),
                }
            )
    else:
        sections.append(
            {
                "title": "正文",
                "level": 0,
                "content": source,
                "start": 0,
                "end": len(source),
                "citations": len(re.findall(r"\[P\d+\]", source)),
                "sentences": _sentence_count(source),
            }
        )
    citations = len(re.findall(r"\[P\d+\]", source)) + len(
        re.findall(r"\([A-Z][A-Za-z\-]+ et al\.,?\s*\d{4}\)", source)
    )
    checks = []
    missing = []
    for check_id, label, weight, severity, pattern in CHECKS:
        content_hit = None
        heading_hit = None
        heading_section = None
        for section in sections:
            if re.search(pattern, section["title"], re.I) and heading_section is None:
                heading_section = section
                heading_hit = section["title"]
            match = re.search(pattern, section["content"], re.I)
            if match and content_hit is None:
                content_hit = _match_snippet(section["content"], match)
        if heading_section is not None:
            match = re.search(pattern, heading_section["content"], re.I)
            content_hit = (
                _match_snippet(heading_section["content"], match)
                if match
                else heading_section["content"].strip()[:220]
            )
        if content_hit:
            status = "present"
            evidence = content_hit
        elif heading_hit:
            status = "partial"
            evidence = f"仅有标题“{heading_hit}”，正文未见对应展开。"
        else:
            status = "missing"
            evidence = ""
            missing.append((check_id, label, weight, severity))
        checks.append(
            {
                "id": check_id,
                "label": label,
                "status": status,
                "weight": weight,
                "evidence": evidence[:220],
                "suggestion": SUGGESTIONS[check_id],
            }
        )
    earned = sum(
        item["weight"] * (1.0 if item["status"] == "present" else 0.5 if item["status"] == "partial" else 0)
        for item in checks
    )
    if source.strip():
        if citations == 0:
            earned -= 10
        score = max(0, min(100, int(round(earned))))
    else:
        score = 0
    issues = []
    if not source.strip():
        for check_id, label, _, _ in missing:
            issues.append(
                {
                    "id": check_id,
                    "severity": "high",
                    "message": f"缺少{label}；当前没有可诊断的正文。",
                    "suggestion": SUGGESTIONS[check_id],
                }
            )
    else:
        for check_id, label, _, severity in missing:
            issues.append(
                {
                    "id": check_id,
                    "severity": severity,
                    "message": f"缺少{label}：{SUGGESTIONS[check_id]}",
                    "suggestion": SUGGESTIONS[check_id],
                }
            )
        if citations == 0:
            issues.insert(
                0,
                {
                    "id": "citations",
                    "severity": "high",
                    "message": "全文没有引用标记 [P1]；文献结论与数据来源无法追溯。",
                    "suggestion": "为每条文献结论补 [P#] 引用，并在文末给出对应题录。",
                },
            )
    if score >= 80:
        verdict = "论证较完整"
    elif score >= 55:
        verdict = "需补关键段落"
    else:
        verdict = "结构缺口明显"
    section_payload = [
        {
            "title": section["title"],
            "level": section["level"],
            "sentences": section["sentences"],
            "citations": section["citations"],
        }
        for section in sections
    ]
    if not source.strip():
        citations = 0
    return {
        "topic": topic,
        "sections": section_payload,
        "checks": checks,
        "issues": issues,
        "score": score,
        "verdict": verdict,
        "counts": {
            "headings": len(headings),
            "paragraphs": len([part for part in re.split(r"\n\s*\n", source) if part.strip()]),
            "sentences": _sentence_count(source),
            "citations": citations,
            "characters": len(source),
        },
    }


def _argument_map(text: str, claims: list[dict], sections: list[dict]) -> list[dict]:
    if not sections:
        return []
    ranges = []
    offset = 0
    for section in sections:
        title = section.get("title") or "正文"
        if section.get("level", 0):
            pattern = re.compile(r"(?m)^#{1,6}\s+" + re.escape(title) + r"\s*$")
            match = pattern.search(text, offset)
            content_start = match.end() if match else offset
        else:
            content_start = 0
        ranges.append({"title": title, "start": content_start, "claims": [], "supported": 0, "partial": 0, "unsupported": 0, "citations": 0, "evidence_links": 0})
    for index, range_item in enumerate(ranges):
        end = ranges[index + 1]["start"] if index + 1 < len(ranges) else len(text)
        range_item["citations"] = len(re.findall(r"\[P\d+\]", text[range_item["start"]:end]))
    used = [0] * len(ranges)
    for item in claims:
        sentence = str(item.get("sentence") or "")
        position = text.find(sentence, used[0]) if sentence else -1
        index = 0
        if position >= 0:
            for idx, range_item in enumerate(ranges):
                if position >= range_item["start"]:
                    index = idx
            used[0] = max(used[0], position + 1)
        bucket = ranges[index]
        bucket["claims"].append(sentence)
        level = item.get("level")
        if level in {"supported", "partial", "unsupported"}:
            bucket[level] += 1
        bucket["evidence_links"] += len(item.get("citations") or [])
    return [
        {
            "title": item["title"],
            "claims": len(item["claims"]),
            "supported": item["supported"],
            "partial": item["partial"],
            "unsupported": item["unsupported"],
            "citations": item["citations"],
            "evidence_links": item["evidence_links"],
        }
        for item in ranges
    ]


def argument_review(
    text: str,
    topic: str = "",
    scope: str = "all",
    paper_ids: list[str] | None = None,
    claim_checker=None,
) -> dict:
    structure = structure_review(text, topic)
    checker = claim_checker or research.check_claims
    claims = checker(text, scope, paper_ids or [])
    results = claims.get("results") or []
    counts = Counter(item.get("level") for item in results)
    total = len(results)
    supported = counts.get("supported", 0)
    partial = counts.get("partial", 0)
    unsupported_count = counts.get("unsupported", 0)
    evidence_score = (supported + partial * 0.5) / max(total, 1) * 100 if total else 0
    score = int(round(structure["score"] * 0.6 + evidence_score * 0.4))
    if score >= 80:
        verdict = "论证较完整"
    elif score >= 55:
        verdict = "需继续修补"
    else:
        verdict = "论证风险较高"
    unsupported = [
        {
            "sentence": item.get("sentence"),
            "label": item.get("label") or "证据不足",
            "level": item.get("level"),
            "issues": item.get("issues") or [],
            "suggestion": item.get("suggestion") or "",
        }
        for item in results
        if item.get("level") != "supported"
    ]
    actions: list[str] = []
    for issue in structure["issues"]:
        action = f"{issue['message']}"
        if action not in actions:
            actions.append(action)
    for item in unsupported:
        sentence = str(item.get("sentence") or "").strip()
        if sentence:
            action = f"补证据或改写：{sentence[:70]}"
            if action not in actions:
                actions.append(action)
    actions = actions[:8]
    return {
        "topic": topic,
        "scope": scope,
        "structure": structure,
        "claims": claims,
        "unsupported": unsupported,
        "argument_map": _argument_map(text, results, structure["sections"]),
        "summary": {
            "claims": total,
            "supported": supported,
            "partial": partial,
            "unsupported": unsupported_count,
            "evidence_score": round(evidence_score),
            "structure_score": structure["score"],
            "score": score,
            "verdict": verdict,
        },
        "score": score,
        "verdict": verdict,
        "actions": actions,
        "note": "论证体检检查结构与引用的可追溯性，不判断研究价值或事实真伪；结论仍需回到原文与实验条件核对。",
    }
