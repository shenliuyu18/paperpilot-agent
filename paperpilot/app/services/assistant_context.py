"""Read-only context builder for the project Copilot.

Every passage keeps a source type, a source id, a locator and a front-end
target so a citation can jump back to the exact evidence, draft block or run.
Nothing in this module writes project, draft or run data.
"""

from __future__ import annotations

import re

from . import agent_runs, agent_state, draft_state, project_state

MAX_EVIDENCE = 40
MAX_QUESTIONS = 20
MAX_DECISIONS = 20
MAX_PAPERS = 20
MAX_DRAFT_BLOCKS = 60
MAX_REPORT_CHARS = 4000
MAX_PASSAGES = 140


def _clip(text, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit] + "…" if len(value) > limit else value


def _project_passage(project: dict) -> dict:
    brief = project.get("brief") or {}
    constraints = "；".join(str(item) for item in brief.get("constraints") or [] if item)
    counts = project.get("counts") or {}
    parts = [
        f"项目：{project.get('title') or ''}",
        f"研究问题：{brief.get('question') or ''}",
        f"范围：{brief.get('scope') or '未填写'}",
        f"约束：{constraints or '未填写'}",
        f"成功标准：{brief.get('success_criteria') or '未填写'}",
        f"阶段：{project.get('stage') or 'exploring'}",
        f"计数：{counts.get('runs', 0)} 轮运行、{counts.get('evidence', 0)} 条证据、"
        f"{counts.get('open_questions', 0)} 个开放问题、{counts.get('draft_blocks', 0)} 个草稿块",
    ]
    return {
        "source_type": "project",
        "source_id": str(project.get("id") or "project"),
        "title": str(project.get("title") or "项目概况"),
        "locator": "项目概况",
        "quote": "",
        "text": "\n".join(part for part in parts if part),
        "target": {"tab": "overview", "id": ""},
    }


def _evidence_passages(memory: dict) -> list[dict]:
    passages = []
    items = list(memory.get("evidence") or [])
    items.sort(key=lambda item: (bool(item.get("verified")), item.get("updated_at") or ""), reverse=True)
    for item in items[:MAX_EVIDENCE]:
        quote = str(item.get("quote") or "").strip()
        claim = str(item.get("claim") or "").strip()
        if not quote and not claim:
            continue
        title = str(item.get("title") or item.get("paper_id") or "项目证据")
        locator = f"{item.get('section') or '原文'} · p.{item.get('page') if item.get('page') is not None else '—'}"
        passages.append(
            {
                "source_type": "evidence",
                "source_id": str(item.get("id") or ""),
                "title": title,
                "locator": locator,
                "quote": _clip(quote, 500),
                "text": " ".join(part for part in (claim, quote) if part),
                "target": {"tab": "evidence", "id": str(item.get("id") or "")},
            }
        )
    return passages


def _question_passages(memory: dict) -> list[dict]:
    passages = []
    for item in (memory.get("open_questions") or [])[:MAX_QUESTIONS]:
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        resolution = str(item.get("resolution") or "").strip()
        passages.append(
            {
                "source_type": "question",
                "source_id": str(item.get("id") or ""),
                "title": "开放问题",
                "locator": str(item.get("status") or "open"),
                "quote": _clip(question, 400),
                "text": " ".join(part for part in (question, resolution) if part),
                "target": {"tab": "overview", "id": str(item.get("id") or "")},
            }
        )
    return passages


def _decision_passages(memory: dict) -> list[dict]:
    passages = []
    for item in (memory.get("decisions") or [])[:MAX_DECISIONS]:
        choice = item.get("choice")
        if isinstance(choice, list):
            choice = "、".join(str(entry) for entry in choice)
        text = " ".join(
            part for part in (str(item.get("kind") or ""), str(choice or ""), str(item.get("note") or "")) if part
        ).strip()
        if not text:
            continue
        passages.append(
            {
                "source_type": "decision",
                "source_id": str(item.get("id") or ""),
                "title": f"决策 · {item.get('kind') or '记录'}",
                "locator": str(item.get("at") or ""),
                "quote": _clip(text, 400),
                "text": text,
                "target": {"tab": "overview", "id": str(item.get("id") or "")},
            }
        )
    return passages


def _paper_passages(memory: dict) -> list[dict]:
    passages = []
    papers = list((memory.get("papers") or {}).values())[:MAX_PAPERS]
    for paper in papers:
        title = str(paper.get("title") or paper.get("id") or "")
        if not title:
            continue
        authors = "、".join(str(item) for item in (paper.get("authors") or [])[:4])
        text = " ".join(
            part
            for part in (
                title,
                authors,
                str(paper.get("year") or ""),
                str(paper.get("venue") or ""),
                str(paper.get("doi") or ""),
            )
            if part
        )
        passages.append(
            {
                "source_type": "paper",
                "source_id": str(paper.get("id") or ""),
                "title": title,
                "locator": "论文题录",
                "quote": _clip(text, 400),
                "text": text,
                "target": {"tab": "overview", "id": str(paper.get("id") or "")},
            }
        )
    return passages


def _draft_passages(draft: dict) -> list[dict]:
    passages = []
    count = 0
    for section in draft.get("sections") or []:
        for block in section.get("blocks") or []:
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            passages.append(
                {
                    "source_type": "draft_block",
                    "source_id": str(block.get("id") or ""),
                    "title": str(section.get("title") or "草稿章节"),
                    "locator": f"{section.get('id') or ''} · {block.get('id') or ''}",
                    "quote": _clip(text, 400),
                    "text": text,
                    "target": {"tab": "draft", "id": str(block.get("id") or "")},
                }
            )
            count += 1
            if count >= MAX_DRAFT_BLOCKS:
                return passages
    return passages


def _report_chunks(run_id: str) -> list[str]:
    report = agent_runs.report_markdown(run_id) or ""
    report = report[:MAX_REPORT_CHARS]
    if not report:
        return []
    chunks = []
    current = ""
    for line in report.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(current) + len(line) > 900 and current:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}".strip()
    if current:
        chunks.append(current)
    return chunks[:3]


def _run_passages(project: dict) -> list[dict]:
    data = agent_state.read_store()
    runs = []
    for run_id in project.get("run_ids") or []:
        run = (data.get("runs") or {}).get(run_id)
        if run:
            runs.append(run)
    runs.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    passages = []
    for run in runs[:2]:
        for index, chunk in enumerate(_report_chunks(str(run.get("id") or "")), start=1):
            passages.append(
                {
                    "source_type": "run",
                    "source_id": str(run.get("id") or ""),
                    "title": str(run.get("goal") or "运行报告"),
                    "locator": f"运行报告 · 第 {index} 段",
                    "quote": _clip(chunk, 500),
                    "text": chunk,
                    "target": {"tab": "runs", "id": str(run.get("id") or "")},
                }
            )
    return passages


def collect(project_id: str) -> dict:
    project = project_state.read_projects().get("projects", {}).get(project_id)
    if not project:
        raise KeyError(project_id)
    memory = project_state.read_memory(project_id)
    draft = draft_state.read_draft(project_id)
    passages = [_project_passage(project)]
    passages.extend(_evidence_passages(memory))
    passages.extend(_question_passages(memory))
    passages.extend(_decision_passages(memory))
    passages.extend(_paper_passages(memory))
    passages.extend(_draft_passages(draft))
    passages.extend(_run_passages(project))
    passages = passages[:MAX_PASSAGES]
    for index, passage in enumerate(passages, start=1):
        # retrieval._key expects paper_id/section/page/text; use unique values.
        passage["paper_id"] = passage["source_id"]
        passage["section"] = f"{passage['source_type']}:{passage['source_id']}"
        passage["page"] = index
    return {
        "project": project,
        "memory": memory,
        "draft": draft,
        "passages": passages,
    }


def report_excerpt(project_id: str, limit: int = 2000) -> str:
    try:
        context = collect(project_id)
    except KeyError:
        return ""
    runs = [item for item in context["passages"] if item.get("source_type") == "run"]
    return _clip("\n\n".join(item["text"] for item in runs), limit)


def keyword_hits(text: str, question: str) -> int:
    """Small helper used by status/compare templates."""
    words = {word for word in re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}", question.lower())}
    return sum(1 for word in words if word in str(text or "").lower())
