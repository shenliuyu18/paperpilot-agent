from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .. import config
from . import (
    agent_state, analysis, argument, ask, draft_service, library, llm_gateway,
    planner, research, search,
)


def _clip(text, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit] + "…" if len(value) > limit else value


def _cap(text, limit: int) -> str:
    value = str(text or "")
    return value[:limit] + "\n\n…（已截断）" if len(value) > limit else value


def _demote_markdown(text: str) -> str:
    return re.sub(r"(?m)^(#{1,5}) ", lambda match: "##" + match.group(1) + " ", str(text or ""))


def _max_papers(run: dict) -> int:
    return int((run.get("budget") or agent_state.DEFAULT_BUDGET).get("max_papers", 6))


def _snapshot_path(run: dict) -> Path:
    return agent_state.run_dir(run["id"]) / "papers.json"


def papers_for_run(run: dict) -> list[dict]:
    path = _snapshot_path(run)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    papers = []
    for paper_id in run.get("paper_ids") or []:
        paper = search.get_paper(paper_id)
        if not paper:
            item = library.get(paper_id)
            paper = library.as_paper(item) if item else None
        if paper:
            papers.append(paper)
    return papers


def save_papers(run: dict, papers: list[dict]) -> list[dict]:
    deduped = []
    seen = set()
    for paper in papers:
        paper_id = paper.get("id")
        if not paper_id or paper_id in seen:
            continue
        seen.add(paper_id)
        deduped.append(copy.deepcopy(paper))
    deduped = deduped[: _max_papers(run)]
    path = _snapshot_path(run)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(deduped, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    run["paper_ids"] = [paper["id"] for paper in deduped]
    return deduped


def select_papers(run: dict, ids: list[str]) -> list[dict]:
    current = {paper.get("id"): paper for paper in papers_for_run(run)}
    ordered = []
    for paper_id in ids:
        paper = current.get(paper_id)
        if not paper:
            paper = search.get_paper(paper_id)
        if not paper:
            item = library.get(paper_id)
            paper = library.as_paper(item) if item else None
        if paper:
            ordered.append(paper)
    if not ordered:
        raise ValueError("改选的论文都不在可用范围内。")
    return save_papers(run, ordered)


def _candidate(paper: dict) -> dict:
    return {
        "id": paper.get("id"),
        "title": paper.get("title") or "未命名论文",
        "authors": list(paper.get("authors") or [])[:3],
        "year": paper.get("year"),
        "venue": paper.get("venue") or "",
        "source": paper.get("source") or "",
        "layer": paper.get("layer") or "",
        "layer_reason": paper.get("layer_reason") or "",
        "credibility": paper.get("credibility") or {},
        "abstract": _clip(paper.get("abstract") or "", 220),
        "doi": paper.get("doi") or "",
        "url": paper.get("url") or "",
    }


def _clean_quote(text) -> str:
    value = " ".join(str(text or "").split())
    return re.sub(r"^(?:abstract|摘要)\s*[:：.\-—]?\s*", "", value, flags=re.I)


def _quote_verified(paper: dict, quote: str) -> bool:
    needle = re.sub(r"\s+", " ", str(quote or "")).strip().lower()
    if len(needle) < 8:
        return False
    haystack = " ".join(
        str(section.get("text") or "")
        for section in (paper.get("sections") or [])
    )
    haystack = re.sub(r"\s+", " ", haystack).lower()
    return needle[:120] in haystack


def _simplify_query(query: str, limit: int = 4) -> str:
    """Keep a short content-word query so one slow/over-constrained source cannot fail the run."""
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}|[\u4e00-\u9fff]{2,}", str(query or ""))
    kept = [token for token in tokens if token.lower() not in analysis._COMPARE_STOP]
    return " ".join(kept[:limit]) if kept else str(query or "")[:60]


def _search_with_fallback(query: str, limit: int, source: str) -> tuple[list[dict], str, bool, list[str]]:
    errors: list[str] = []

    def attempt(text: str, src: str) -> list[dict]:
        try:
            return search.search(text, limit=limit, source=src)
        except Exception as exc:  # network/source failure: degrade instead of failing the run
            errors.append(f"{src}: {str(exc)[:140]}")
            return []

    papers = attempt(query, source)
    if papers:
        return papers, query, False, errors
    simplified = _simplify_query(query)
    if simplified and simplified != query:
        papers = attempt(simplified, source)
        if papers:
            return papers, simplified, True, errors
    if source == "upload":
        errors.append("upload: 本地还没有上传 PDF，请先上传一篇 PDF。")
        return [], query, True, errors
    if source != "auto":
        papers = attempt(simplified or query, "auto")
        if papers:
            return papers, simplified or query, True, errors
    return [], query, True, errors


def _act_search(run: dict, input_data: dict, action_id: str) -> dict:
    query = str(input_data.get("query") or run["goal"]).strip()
    expansion = bool(input_data.get("expansion"))
    plan = planner.build_plan(query, run.get("source", "demo"))
    papers, query_used, fallback, search_errors = _search_with_fallback(
        query,
        max(4, _max_papers(run) * 2),
        run.get("source", "demo"),
    )
    if not papers:
        detail = "；".join(search_errors[-2:]) or "来源没有返回论文"
        raise ValueError(f"检索失败（已尝试简化关键词{'与多源聚合' if run.get('source') != 'auto' else ''}）：{detail}")
    papers = planner.annotate_results(papers, query_used)
    existing = papers_for_run(run)
    existing_ids = {paper.get("id") for paper in existing}
    deduped = []
    seen = set()
    for paper in papers:
        paper_id = paper.get("id")
        if not paper_id or paper_id in seen:
            continue
        seen.add(paper_id)
        deduped.append(paper)
    if expansion and existing:
        selected = existing + [paper for paper in deduped if paper.get("id") not in existing_ids]
    else:
        selected = deduped
    if not selected:
        raise ValueError("检索没有返回可用论文，请更换来源或关键词。")
    selected = save_papers(run, selected)
    new_ids = [paper["id"] for paper in selected if paper["id"] not in existing_ids]
    return {
        "query": query,
        "query_used": query_used,
        "fallback": fallback,
        "errors": search_errors[-3:],
        "reason": input_data.get("reason") or ("扩展检索" if expansion else "建立初始候选集"),
        "expansion": expansion,
        "plan": {
            key: plan.get(key)
            for key in ("query", "source", "concepts", "expanded_terms", "sources", "strategy")
            if key in plan
        },
        "candidates": [_candidate(paper) for paper in deduped],
        "selected": [paper["id"] for paper in selected],
        "new_ids": new_ids,
        "evidence_added": 0,
    }


def _act_read(run: dict, input_data: dict, action_id: str) -> dict:
    papers = papers_for_run(run)
    if not papers:
        raise ValueError("没有可精读的论文。")
    paper_id = input_data.get("paper_id")
    paper = next((item for item in papers if item.get("id") == paper_id), None)
    if paper is None:
        read_ids = set(agent_state.read_paper_ids(run))
        paper = next((item for item in papers if item.get("id") not in read_ids), papers[0])
    data = analysis.read_paper(paper, "5min")
    evidence_items = []
    for item in (data.get("evidence") or [])[:3]:
        quote = _clean_quote(item.get("quote"))
        evidence_items.append(
            {
                "paper_id": paper.get("id"),
                "title": paper.get("title"),
                "section": item.get("section"),
                "page": item.get("page"),
                "claim": item.get("label") or item.get("field") or "证据",
                "quote": quote,
                "verified": _quote_verified(paper, quote),
                "kind": item.get("field") or "text",
            }
        )
    if not evidence_items:
        for kind, label in (("problem", "问题"), ("method", "方法")):
            value = data.get(kind)
            if value and value not in {"未提及", "待结构化抽取"}:
                quote = _clip(value, 280)
                evidence_items.append(
                    {
                        "paper_id": paper.get("id"),
                        "title": paper.get("title"),
                        "section": "Abstract",
                        "page": 1,
                        "claim": label,
                        "quote": quote,
                        "verified": _quote_verified(paper, quote),
                        "kind": kind,
                    }
                )
        for limitation in (data.get("limitations") or [])[:2]:
            quote = _clip(limitation, 280)
            evidence_items.append(
                {
                    "paper_id": paper.get("id"),
                    "title": paper.get("title"),
                    "section": "Limitations",
                    "page": None,
                    "claim": "局限",
                    "quote": quote,
                    "verified": _quote_verified(paper, quote),
                    "kind": "limitation",
                }
            )
    added = agent_state.add_evidence(run, evidence_items, action_id)
    return {
        "paper": {
            "id": paper.get("id"),
            "title": paper.get("title") or "未命名论文",
            "year": paper.get("year"),
        },
        "tldr": data.get("tldr"),
        "problem": data.get("problem"),
        "method": data.get("method"),
        "limitations": list(data.get("limitations") or [])[:3],
        "metrics": data.get("metrics") or {},
        "evidence": evidence_items,
        "extraction_mode": data.get("extraction_mode", ""),
        "evidence_added": added,
    }


def _act_ask(run: dict, input_data: dict, action_id: str) -> dict:
    question_id = input_data.get("question_id") or ""
    question_record = agent_state.find_question(run, question_id) if question_id else None
    question = str(input_data.get("question") or (question_record or {}).get("question") or "").strip()
    if not question:
        raise ValueError("追问需要一个问题。")
    result = ask.answer(question, scope="selected", paper_ids=run.get("paper_ids") or [], top_k=4)
    citations = result.get("citations") or []
    verification = result.get("citation_verification") or {}
    evidence_items = []
    for citation in citations:
        quote = _clean_quote(citation.get("quote"))
        evidence_items.append(
            {
                "paper_id": citation.get("paper_id"),
                "title": citation.get("title"),
                "section": citation.get("section"),
                "page": citation.get("page"),
                "claim": question,
                "quote": quote,
                "verified": bool(citation.get("quote_verified")),
                "kind": "answer",
            }
        )
    added = agent_state.add_evidence(run, evidence_items, action_id)
    if citations and question_id:
        agent_state.resolve_question(
            run,
            question_id,
            f"获得 {len(citations)} 条引用，核对 {verification.get('verified', 0)} 条",
        )
    return {
        "question_id": question_id,
        "question": question,
        "answer": result.get("answer"),
        "mode": result.get("mode"),
        "citations": citations[:4],
        "retrieval": result.get("retrieval") or {},
        "verification": verification,
        "support_summary": result.get("support_summary") or {},
        "evidence_added": added,
    }


def _resolve_known_paper(run: dict, paper_id: str) -> dict | None:
    """Resolve a known paper that is no longer in the run snapshot.

    Seeded project papers are dropped from the snapshot when a new search
    replaces the selection, but the planner still counts them as known. Project
    memory keeps their metadata, so compare/topic/draft/review can use the same
    id set as the planner instead of failing or replanning forever.
    """
    if run.get("project_id"):
        try:
            from . import project_state
            paper = (
                project_state.read_memory(run["project_id"]).get("papers") or {}
            ).get(paper_id)
        except (ImportError, KeyError):
            paper = None
        if paper:
            return paper
    paper = search.get_paper(paper_id)
    if not paper:
        item = library.get(paper_id)
        paper = library.as_paper(item) if item else None
    return paper


def _known_papers(run: dict) -> list[dict]:
    """Return the papers the planner treats as known (read + seeded).

    The snapshot holds the currently selected papers, which may no longer
    include a seeded paper. Known ids missing from the snapshot are resolved
    from project memory/search/library so the action operates on exactly the
    ``agent_state.known_paper_ids`` set the planner compares against. When the
    run has no known ids at all, the snapshot is used unchanged.
    """
    papers = papers_for_run(run)
    known_ids = list(dict.fromkeys(agent_state.known_paper_ids(run)))
    if not known_ids:
        return papers
    index = {paper.get("id"): paper for paper in papers}
    resolved = []
    for paper_id in known_ids:
        paper = index.get(paper_id) or _resolve_known_paper(run, paper_id)
        if paper:
            resolved.append(paper)
    return resolved


def _act_compare(run: dict, input_data: dict, action_id: str) -> dict:
    papers = _known_papers(run)
    if len(papers) < 2:
        raise ValueError("横向对比至少需要两篇已知论文（已精读或项目带入）。")
    result = analysis.compare(papers)
    return {
        "paper_ids": [paper.get("id") for paper in papers],
        "rows": result.get("rows", []),
        "columns": result.get("columns", []),
        "comparability": result.get("comparability", []),
        "consensus": result.get("consensus", []),
        "divergence": result.get("divergence", []),
        "warnings": result.get("warnings", []),
        "gaps": result.get("gaps", []),
        "evidence_added": 0,
    }


def _act_topic(run: dict, input_data: dict, action_id: str) -> dict:
    papers = _known_papers(run)
    if not papers:
        raise ValueError("选题前至少需要一篇已知论文（已精读或项目带入）。")
    result = research.topic_studio(run["goal"], "selected", [paper["id"] for paper in papers])
    questions = result.get("questions", [])
    if questions and not run.get("selected_topic") and run.get("mode") == "auto":
        run["selected_topic"] = {
            "id": questions[0].get("id"),
            "title": questions[0].get("title"),
        }
    return {
        "paper_ids": [paper.get("id") for paper in papers],
        "stats": result.get("stats", {}),
        "clusters": result.get("clusters", [])[:6],
        "questions": questions[:3],
        "reading_path": result.get("reading_path", []),
        "experiment_plan": result.get("experiment_plan", {}),
        "brief": result.get("brief", {}),
        "note": result.get("note", ""),
        "evidence_added": 0,
    }


def _act_draft(run: dict, input_data: dict, action_id: str) -> dict:
    papers = _known_papers(run)
    if not papers:
        raise ValueError("写作前至少需要一篇已知论文（已精读或项目带入）。")
    selected_topic = input_data.get("selected_topic") or run.get("selected_topic")
    if not selected_topic:
        topic_artifact = agent_state.artifact_by_tool(run, "topic")
        questions = topic_artifact.get("questions") or []
        selected_topic = {"id": questions[0].get("id"), "title": questions[0].get("title")} if questions else None
    focus = run["goal"]
    if selected_topic and selected_topic.get("title"):
        focus = f"{run['goal']}（聚焦：{selected_topic['title']}）"
    result = research.draft(focus, "related_work", "selected", [paper["id"] for paper in papers])
    return {
        "paper_ids": [paper.get("id") for paper in papers],
        "selected_topic": selected_topic,
        "title": result.get("title", ""),
        "markdown": _cap(result.get("markdown", ""), 20000),
        "paper_count": result.get("paper_count", len(papers)),
        "disclaimer": result.get("disclaimer", ""),
        "evidence_added": 0,
    }


def _act_write(run: dict, input_data: dict, action_id: str) -> dict:
    project_id = run.get("project_id")
    if not project_id:
        raise ValueError("独立任务没有项目草稿工作区。")
    draft = draft_service.get_draft(project_id)
    if not draft.get("sections"):
        skeleton = agent_state.artifact_by_tool(run, "draft").get("markdown") or ""
        draft = draft_service.ensure_from_markdown(project_id, skeleton)
    section_id = str(input_data.get("section_id") or "")
    if not section_id:
        outline = draft_service.outline(project_id)
        section_id = next(
            (item["id"] for item in outline if item["id"] and item["non_empty"] == 0),
            "",
        )
        if not section_id:
            section_id = next(
                (
                    item["id"] for item in outline
                    if item["id"] and item["blocks"] > item["non_empty"]
                ),
                "",
            )
        if not section_id:
            # The imported skeleton may already contain paragraphs/bullets in
            # every section, in which case the selection rules above find no
            # writable section. Appending evidence-anchored blocks to the first
            # section this run has not written yet keeps the default project
            # flow from turning ``write`` into a silent no-op. The completed
            # write is recorded by section id, so the fallback happens at most
            # once per section.
            written = {
                (step.get("artifact") or {}).get("section_id")
                for step in run.get("steps", [])
                if step.get("tool") == "write" and step.get("status") == "done"
            }
            section_id = next(
                (
                    item["id"] for item in outline
                    if item["id"] and item["id"] not in written
                ),
                "",
            )
    if not section_id:
        # The skeleton import produced no writable section (for example an
        # empty draft artifact). Mark the attempt so the planner can stop
        # re-proposing the same no-op write.
        return {
            "section_id": "",
            "blocks_added": 0,
            "evidence_ids": [],
            "mode": "offline",
            "draft_revision": draft.get("revision", 0),
            "no_section": True,
            "imported_sections": len(draft.get("sections") or []),
            "evidence_added": 0,
        }
    # Agent writes must be reversible: snapshot the current draft before any
    # block is appended (design 8.2). A failed snapshot must not block writing.
    if draft.get("sections"):
        try:
            draft_service.create_version(
                project_id, "Agent 写入前自动保存", "before_agent_write"
            )
        except (KeyError, OSError):
            pass
    use_llm = bool(input_data.get("use_llm"))
    if use_llm and agent_state.llm_budget_remaining(run) <= 0:
        # The run's LLM budget is enforced before the call, not just charged
        # afterwards; exhausting it falls back to the offline writer.
        use_llm = False
    result = draft_service.write_section(
        project_id,
        section_id,
        max_blocks=int(input_data.get("max_blocks") or 3),
        use_llm=use_llm,
        run=run,
    )
    if result.get("blocks_added", 0) == 0:
        agent_state.upsert_question(
            run,
            f"章节「{section_id}」缺少可用证据，是否需要补充检索或追问？",
            kind="evidence",
            priority="high",
            source="write",
        )
    result["evidence_added"] = 0
    return result


def _act_review(run: dict, input_data: dict, action_id: str) -> dict:
    papers = _known_papers(run)
    draft = agent_state.artifact_by_tool(run, "draft")
    text = draft.get("markdown") or "\n".join(
        f"{item.get('paper', {}).get('title')}: {item.get('tldr')}"
        for item in [
            step.get("artifact") or {}
            for step in run.get("steps", [])
            if step.get("tool") == "read"
        ]
    )
    ids = [paper["id"] for paper in papers]
    claims = research.check_claims(text, "selected", ids)
    review = argument.argument_review(
        text,
        topic=run["goal"],
        scope="selected",
        paper_ids=ids,
        claim_checker=lambda _text, _scope, _ids: claims,
    )
    draft_audit_result = {}
    if run.get("project_id"):
        audit = draft_service.audit_draft(run["project_id"])
        draft_audit_result = audit
        high_issues = [
            issue for issue in audit.get("issues") or []
            if issue.get("severity") == "high"
        ]
        for issue in high_issues[:5]:
            agent_state.upsert_question(
                run,
                f"草稿核查：{issue.get('message')}",
                kind="claim",
                priority="high",
                source="review",
            )
    return {
        "paper_ids": ids,
        "claim_summary": claims.get("summary", {}),
        "claims": list(claims.get("results", []))[:8],
        "citations": list(claims.get("citations", []))[:8],
        "argument": {
            "score": review.get("score"),
            "verdict": review.get("verdict"),
            "summary": review.get("summary", {}),
            "actions": list(review.get("actions", []))[:8],
            "unsupported": list(review.get("unsupported", []))[:5],
            "structure": review.get("structure", {}),
        },
        "draft_audit": draft_audit_result,
        "evidence_added": 0,
    }


def _table_row(values: list[str]) -> str:
    return "| " + " | ".join(str(value).replace("|", "\\|") for value in values) + " |"


def _legend_line(papers: list[dict]) -> str:
    mapping = "；".join(
        f"[P{index}] = {(paper.get('title') or '未命名论文')}"
        for index, paper in enumerate(papers, start=1)
    )
    return f"> 引用标记：{mapping}" if mapping else ""


def _reference_line(index: int, paper: dict) -> str:
    authors = ", ".join((paper.get("authors") or [])[:3])
    if len(paper.get("authors") or []) > 3:
        authors += " 等"
    suffix = f" DOI: {paper['doi']}" if paper.get("doi") else (f" {paper['url']}" if paper.get("url") else "")
    return (
        f"- [P{index}] {authors}. {paper.get('title') or '未命名论文'}. "
        f"{paper.get('venue') or '—'}, {paper.get('year') or '—'}.{suffix}"
    )


def _references_block(papers: list[dict]) -> list[str]:
    return [_reference_line(index, paper) for index, paper in enumerate(papers, start=1)]


def _with_legend(markdown: str, papers: list[dict]) -> str:
    """Rebuild the [Pn] legend and reference list from the run's paper order.

    The legend and the reference list are the only places where a reader learns
    what ``[P2]`` points at, so a model-written version is discarded rather than
    trusted: a swapped or missing mapping would make every citation in the body
    point at the wrong paper. A model-written report may also omit the
    reference section entirely, which leaves the numbered citations dangling.
    """
    legend = _legend_line(papers)
    if not legend:
        return markdown
    lines = [
        line for line in markdown.splitlines()
        if not re.match(r"^\s*>\s*\*{0,2}引用标记\*{0,2}\s*[：:]", line)
    ]
    insert_at = 0
    for index, line in enumerate(lines):
        text = line.strip()
        if text.startswith(">"):
            insert_at = index + 1
        elif text and not text.startswith("#"):
            break
    lines.insert(insert_at, legend)
    references = _references_block(papers)
    reference_heading = re.compile(r"^\s*##\s*\*{0,2}参考文献\*{0,2}\s*$")
    heading = next(
        (index for index, line in enumerate(lines) if reference_heading.match(line)),
        None,
    )
    if heading is None:
        lines += ["", "## 参考文献", "", *references]
    else:
        end = next(
            (index for index in range(heading + 1, len(lines))
             if re.match(r"^\s*##\s", lines[index])),
            len(lines),
        )
        tail = lines[end:]
        lines = lines[:heading] + ["## 参考文献", "", *references]
        if tail:
            lines += [""] + tail
    return "\n".join(lines).strip() + "\n"


def _offline_report(run: dict) -> str:
    search_art = agent_state.artifact_by_tool(run, "search")
    read_steps = [
        step for step in run.get("steps", [])
        if step.get("tool") == "read" and step.get("status") == "done"
    ]
    compare_art = agent_state.artifact_by_tool(run, "compare")
    topic_art = agent_state.artifact_by_tool(run, "topic")
    draft_art = agent_state.artifact_by_tool(run, "draft")
    review_art = agent_state.artifact_by_tool(run, "review")
    papers = papers_for_run(run)
    evidence = run.get("evidence") or []
    questions = run.get("open_questions") or []
    decisions = run.get("decisions") or []
    budget = agent_state.budget_snapshot(run)
    verified = sum(1 for item in evidence if item.get("verified"))
    lines = [
        f"# {run['goal']} · 自适应研究简报",
        "",
        f"> PaperPilot v{config.APP_VERSION} · {agent_state.now()}",
        f"> 自主程度：{run.get('mode', 'checkpoint')} · 动作：{budget['actions']['used']}/{budget['actions']['max']} · "
        f"论文：{len(papers)}/{budget['papers']['max']} · 证据：{len(evidence)}（已核对 {verified}）",
        "> 证据边界：本简报由 Agent 动作、检索证据与规则核查拼装；未解决问题与未核验引用均在文末列出。",
        "",
        "## 摘要",
        "",
    ]
    brief = topic_art.get("brief") or {}
    if brief.get("thesis"):
        lines.append(_clip(brief["thesis"], 520))
    else:
        lines.append(
            f"围绕“{run['goal']}”，Agent 在 {len(run.get('steps', []))} 个动作内检索并精读 {len(papers)} 篇论文，"
            f"形成 {len(evidence)} 条证据、{len(questions)} 个开放问题。"
        )
    lines.extend(["", "## 1. 检索与选文", ""])
    candidates = search_art.get("candidates") or []
    if candidates:
        lines.append(_table_row(["论文", "年份", "来源", "分层"]))
        lines.append(_table_row(["---", "---", "---", "---"]))
        for item in candidates[:8]:
            lines.append(
                _table_row(
                    [
                        item.get("title") or "未命名论文",
                        item.get("year") or "—",
                        item.get("source") or "—",
                        item.get("layer") or "—",
                    ]
                )
            )
    else:
        lines.append("没有形成候选集。")
    lines.extend(["", "## 2. 证据账本", ""])
    if evidence:
        lines.append(_table_row(["论文", "位置", "证据", "核对"]))
        lines.append(_table_row(["---", "---", "---", "---"]))
        for item in evidence[:40]:
            lines.append(
                _table_row(
                    [
                        item.get("title") or item.get("paper_id") or "—",
                        f"{item.get('section') or '原文'} · p.{item.get('page') or '—'}",
                        _clip(item.get("quote"), 160),
                        "已核对" if item.get("verified") else "待核对",
                    ]
                )
            )
    else:
        lines.append("本任务尚未形成可引用证据。")
    lines.extend(["", "## 3. 精读摘要", ""])
    if read_steps:
        for step in read_steps:
            artifact = step.get("artifact") or {}
            paper = artifact.get("paper") or {}
            lines.append(f"### {paper.get('title') or paper.get('id') or '未命名论文'}")
            lines.append("")
            if artifact.get("tldr"):
                lines.append(f"- **一句话**：{_clip(artifact['tldr'], 300)}")
            if artifact.get("problem"):
                lines.append(f"- **问题**：{_clip(artifact['problem'], 260)}")
            if artifact.get("method"):
                lines.append(f"- **方法**：{_clip(artifact['method'], 300)}")
            for limitation in (artifact.get("limitations") or [])[:2]:
                lines.append(f"- **局限**：{_clip(limitation, 220)}")
            lines.append("")
    else:
        lines.append("尚未精读论文。")
    lines.extend(["## 4. 横向对比", ""])
    if compare_art:
        for item in (compare_art.get("consensus") or [])[:3]:
            lines.append(f"- **共识**：{_clip(item, 260)}")
        for item in (compare_art.get("divergence") or [])[:3]:
            lines.append(f"- **分歧**：{_clip(item, 260)}")
        for item in (compare_art.get("warnings") or [])[:3]:
            lines.append(f"- **注意**：{_clip(item, 260)}")
        rows = compare_art.get("rows") or []
        if rows:
            lines.extend(["", _table_row(["论文", "年份", "关键结果", "局限"]), _table_row(["---", "---", "---", "---"])])
            for row in rows[:8]:
                lines.append(
                    _table_row(
                        [
                            row.get("title") or "—",
                            row.get("year") or "—",
                            _clip(row.get("headline") or "未提及", 100),
                            _clip(row.get("limitation") or "未提及", 100),
                        ]
                    )
                )
    else:
        lines.append("尚未生成对比。")
    lines.extend(["", "## 5. 候选方向", ""])
    if topic_art.get("questions"):
        for index, question in enumerate(topic_art["questions"][:3], start=1):
            lines.append(f"### Q{index}. {question.get('title') or question.get('question') or '候选问题'}")
            lines.append("")
            card = question.get("argument_card") or {}
            for label, key in (
                ("缺口", "gap"),
                ("最小方法", "method"),
                ("成功判据", "success_criterion"),
                ("失败模式", "failure_mode"),
            ):
                if card.get(key):
                    lines.append(f"- **{label}**：{_clip(card[key], 300)}")
            lines.append("")
    else:
        lines.append("尚未生成候选方向。")
    lines.extend(["## 6. 写作骨架", ""])
    if draft_art.get("markdown"):
        lines.append(_cap(_demote_markdown(draft_art["markdown"]), 6000))
        lines.append("")
        if draft_art.get("disclaimer"):
            lines.append(f"> {draft_art['disclaimer']}")
    else:
        lines.append("尚未生成写作骨架。")
    lines.extend(["", "## 7. 核查结果", ""])
    if review_art:
        claim_summary = review_art.get("claim_summary") or {}
        argument_summary = (review_art.get("argument") or {}).get("summary") or {}
        lines.append(
            f"- 逐句核查：共 {claim_summary.get('total', 0)} 句，支持 {claim_summary.get('supported', 0)}，"
            f"部分 {claim_summary.get('partial', 0)}，不足 {claim_summary.get('unsupported', 0)}。"
        )
        if argument_summary:
            lines.append(f"- 论证体检：{argument_summary.get('score', '—')} 分 · {argument_summary.get('verdict', '—')}。")
        for action in (review_art.get("argument") or {}).get("actions", [])[:5]:
            lines.append(f"- 修改动作：{_clip(action, 220)}")
    else:
        lines.append("尚未生成核查。")
    lines.extend(["", "## 8. 未解决问题", ""])
    open_items = [item for item in questions if item.get("status") == "open"]
    if open_items:
        for item in open_items[:10]:
            lines.append(f"- [{item.get('priority', 'medium')}] {item.get('question')}（{item.get('kind', 'evidence')}）")
    else:
        lines.append("没有未解决问题；仍需人工核对原文测试条件。")
    if questions:
        resolved = [item for item in questions if item.get("status") == "resolved"]
        if resolved:
            lines.append("")
            lines.append("已解决：" + "；".join(_clip(item.get("question"), 80) for item in resolved[:5]))
    lines.extend(["", "## 9. 决策与预算", ""])
    lines.append(
        f"- 动作预算：{budget['actions']['used']}/{budget['actions']['max']}；"
        f"时间：{budget['seconds']['used']}/{budget['seconds']['max']} 秒；"
        f"LLM：{budget['llm_calls']['used']}/{budget['llm_calls']['max']}。"
    )
    for decision in decisions[-8:]:
        lines.append(
            f"- {decision.get('at')} · {decision.get('kind')}：{_clip(decision.get('choice'), 160)}"
            + (f"（{decision.get('note')}）" if decision.get('note') else "")
        )
    lines.extend(["", "## 10. 动作轨迹", ""])
    for step in run.get("steps", []):
        observation = step.get("observation") or {}
        status = "done" if step.get("tool") == "report" else step.get("status")
        lines.append(
            f"- `{step.get('id')}` **{step.get('title') or step.get('tool')}** · {status}"
            f"：{_clip(step.get('rationale'), 160)}"
            + (f" → {_clip(observation.get('summary'), 220)}" if observation.get('summary') else "")
        )
    lines.extend(["", "## 参考文献", ""])
    if papers:
        for index, paper in enumerate(papers, start=1):
            lines.append(_reference_line(index, paper))
    else:
        lines.append("- 无可用题录。")
    if run.get("project_id"):
        seed_summary = run.get("seed_summary") or {}
        lines += [
            "",
            "## 沿用的项目证据",
            "",
            f"- 本轮带入 {seed_summary.get('evidence', 0)} 条已验证证据、"
            f"{seed_summary.get('questions', 0)} 个未解决问题、"
            f"{seed_summary.get('papers', 0)} 篇论文快照。",
        ]
        try:
            draft_summary = draft_service.summary(run["project_id"])
            lines += [
                "",
                "## 草稿进度",
                "",
                f"- 当前草稿：{draft_summary.get('sections', 0)} 节 / "
                f"{draft_summary.get('blocks', 0)} 块，"
                f"revision {draft_summary.get('revision', 0)}。",
            ]
        except (ImportError, KeyError):
            pass
    lines.append("")
    return _with_legend("\n".join(lines), papers)


def _llm_report(run: dict) -> str | None:
    if not config.LLM_ENABLED:
        return None
    state = {
        "goal": run.get("goal"),
        "mode": run.get("mode"),
        "budget": agent_state.budget_snapshot(run),
        "steps": [
            {
                "tool": step.get("tool"),
                "status": step.get("status"),
                "rationale": step.get("rationale"),
                "observation": step.get("observation"),
            }
            for step in run.get("steps", [])
        ],
        "evidence": (run.get("evidence") or [])[:40],
        "open_questions": run.get("open_questions") or [],
        "decisions": run.get("decisions") or [],
    }
    payload = _cap(json.dumps(state, ensure_ascii=False, default=str), 14000)
    draft = _cap(_offline_report(run), 9000)
    system = (
        "你是严谨的科研简报编辑。只能使用用户提供的状态与离线草稿，不得编造论文、数值、引用或实验结论。"
        "保留证据边界与未解决问题；每条结论至少带一个引用标记（如 [P1] 或 [1]）。"
        "输出中文 Markdown，保留离线草稿中的证据账本、未解决问题、预算与动作轨迹。"
    )
    user = f"任务状态 JSON：\n{payload}\n\n离线简报草稿：\n{draft}"
    for attempt in range(2):
        try:
            text, _ = llm_gateway.audited_chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                purpose="agent_report",
                temperature=0.2,
                max_tokens=2600 if attempt == 0 else 3200,
                run=run,
            )
            text = text.strip()
        except Exception:  # noqa: BLE001 - budget or transport: fall back to the offline report
            return None
        if len(text) >= 10 and re.search(r"\[[^\]]+\]", text):
            return text
        if len(text) >= 10:
            # Long but uncited: retrying would only burn tokens, so keep the offline report.
            return None
    return None


def build_report(run: dict) -> tuple[str, str]:
    papers = papers_for_run(run)
    if config.LLM_ENABLED:
        generated = _llm_report(run)
        if generated:
            return _with_legend(generated, papers), "llm"
    return _offline_report(run), "offline"


def report_markdown(run: dict) -> str | None:
    path = (run.get("report") or {}).get("path")
    candidates = [Path(path)] if path else []
    candidates.append(agent_state.run_dir(run["id"]) / "report.md")
    for candidate in candidates:
        if candidate.exists():
            try:
                return candidate.read_text(encoding="utf-8")
            except OSError:
                return None
    return None


def _act_report(run: dict, input_data: dict, action_id: str) -> dict:
    markdown, mode = build_report(run)
    path = agent_state.run_dir(run["id"]) / "report.md"
    path.write_text(markdown, encoding="utf-8")
    run["report"] = {
        "path": str(path),
        "chars": len(markdown),
        "mode": mode,
        "generated_at": agent_state.now(),
        "budget": agent_state.budget_snapshot(run),
    }
    return {"path": str(path), "chars": len(markdown), "mode": mode, "evidence_added": 0}


_ACTIONS = {
    "search": _act_search,
    "read": _act_read,
    "ask": _act_ask,
    "compare": _act_compare,
    "topic": _act_topic,
    "draft": _act_draft,
    "write": _act_write,
    "review": _act_review,
    "report": _act_report,
}


def execute_action(run: dict, plan: dict, action_id: str = "") -> dict:
    tool = str(plan.get("tool") or "")
    if tool not in _ACTIONS:
        raise ValueError(f"未知动作：{tool}")
    return _ACTIONS[tool](run, plan.get("input") or {}, action_id)
