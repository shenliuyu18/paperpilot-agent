from __future__ import annotations

import re

from . import agent_state


def _done(run: dict, tool: str | None = None) -> list[dict]:
    steps = [
        step for step in run.get("steps", [])
        if step.get("status") == "done" and (tool is None or step.get("tool") == tool)
    ]
    return steps


def _latest(run: dict, tool: str) -> dict:
    return agent_state.artifact_by_tool(run, tool)


def _count(run: dict, tool: str) -> int:
    return len(_done(run, tool))


def _selected(run: dict) -> list[str]:
    ids = list(run.get("paper_ids") or [])
    if ids:
        return ids
    search = _latest(run, "search")
    return list(search.get("selected") or [])


def _max_papers(run: dict) -> int:
    return int((run.get("budget") or agent_state.DEFAULT_BUDGET).get("max_papers", 6))


def _plan(tool: str, title: str, rationale: str, input_data: dict | None = None,
          requires_approval: bool = False) -> dict:
    return {
        "tool": tool,
        "title": title,
        "rationale": rationale,
        "input": input_data or {},
        "requires_approval": requires_approval,
    }


def _report_plan(run: dict, rationale: str) -> dict:
    return _plan("report", "收束研究简报", rationale, {}, requires_approval=False)


def _asked_question_ids(run: dict) -> set[str]:
    ids = set()
    for step in _done(run, "ask"):
        artifact = step.get("artifact") or {}
        input_data = step.get("input") or {}
        question_id = artifact.get("question_id") or input_data.get("question_id")
        if question_id:
            ids.add(question_id)
    return ids


def _search_seen(run: dict, query: str) -> bool:
    needle = re.sub(r"\s+", " ", str(query or "")).strip().lower()
    if not needle:
        return False
    for step in _done(run, "search"):
        artifact = step.get("artifact") or {}
        used = artifact.get("query_used") or artifact.get("query")
        if re.sub(r"\s+", " ", str(used or "")).strip().lower() == needle:
            return True
    return False


def _last_tool(run: dict) -> str:
    done = _done(run)
    return done[-1].get("tool", "") if done else ""


def _paper_title(run: dict, paper_id: str) -> str:
    title = paper_id
    search = _latest(run, "search")
    for candidate in search.get("candidates") or []:
        if str(candidate.get("id")) == str(paper_id) and candidate.get("title"):
            title = candidate["title"]
            break
    else:
        for step in _done(run, "read"):
            paper = (step.get("artifact") or {}).get("paper") or {}
            if str(paper.get("id")) == str(paper_id) and paper.get("title"):
                title = paper["title"]
                break
    return title[:60] + "…" if len(title) > 60 else title


def _requires_approval(run: dict, plan: dict) -> bool:
    mode = run.get("mode", "checkpoint")
    if mode != "checkpoint":
        return False
    if run.get("critic", {}).get("next_hint") == "human":
        return True
    if plan.get("tool") == "write":
        return True
    if plan.get("tool") == "search" and plan.get("input", {}).get("expansion"):
        return True
    return _last_tool(run) in {"search", "topic"}


def _draft_gap(run: dict) -> str | None:
    """Return the next section the project draft should be written into.

    ``None`` means "no write needed"; an empty string means the project has no
    structured draft yet and the writing skeleton should be imported first.
    Sections already targeted by a completed ``write`` action are skipped so a
    single section is written at most once per run. A completed blank write for
    the newest skeleton returns ``None`` so the no-op import is not repeated.
    """
    if not run.get("project_id"):
        return None
    try:
        from . import draft_service
        outline = draft_service.outline(run["project_id"])
    except (ImportError, KeyError):
        return None
    written = {
        (step.get("artifact") or {}).get("section_id")
        for step in _done(run, "write")
        if (step.get("artifact") or {}).get("section_id")
    }
    pending = [item for item in outline if item["id"] not in written]
    for section in pending:
        if section["non_empty"] == 0:
            return section["id"]
    for section in pending:
        if section["blocks"] > section["non_empty"]:
            return section["id"]
    if not outline and _latest(run, "draft"):
        # A blank write after the newest draft artifact means the skeleton
        # import produced no sections/blocks. Re-proposing ``write`` would
        # repeat the same no-op until the budget is gone, so move on to
        # review. A later draft artifact may still be imported once.
        done = _done(run)
        last_draft = max(
            (index for index, step in enumerate(done) if step.get("tool") == "draft"),
            default=-1,
        )
        blank_write = any(
            index > last_draft
            and step.get("tool") == "write"
            and not (step.get("artifact") or {}).get("section_id")
            for index, step in enumerate(done)
        )
        return None if blank_write else ""
    return None


def choose_next(run: dict) -> dict | None:
    if _latest(run, "report"):
        return None
    if run.get("force_report"):
        return _report_plan(run, "用户要求停止探索，按当前证据边界收束。")
    exhausted = agent_state.budget_exhausted(run)
    if exhausted:
        return _report_plan(run, f"{exhausted}，停止扩展并生成当前证据边界内的简报。")

    selected = _selected(run)
    read_ids = agent_state.known_paper_ids(run)
    constraint = str(run.get("constraint") or "").strip()
    query = " ".join(part for part in (run["goal"], constraint) if part)
    if not _done(run, "search"):
        plan = _plan(
            "search",
            "检索候选论文",
            "还没有候选集，先用研究目标建立可筛选的论文池。",
            {"query": query, "reason": "建立初始候选集", "expansion": False},
        )
        plan["requires_approval"] = _requires_approval(run, plan)
        return plan

    if not selected:
        if _count(run, "search") < 3:
            plan = _plan(
                "search",
                "扩展检索",
                "上一轮检索没有留下可用论文，换一组关键词继续找直接相关工作。",
                {
                    "query": " ".join(
                        part for part in (run["goal"], constraint, "关键工作", "综述") if part
                    ),
                    "reason": "候选为空，扩展检索",
                    "expansion": True,
                },
            )
            plan["requires_approval"] = _requires_approval(run, plan)
            return plan
        return _report_plan(run, "多次检索仍未形成候选集，先按现有线索收束并标注证据缺口。")

    target_reads = min(len(selected), _max_papers(run))
    unread = [paper_id for paper_id in selected if paper_id not in read_ids]
    if len(read_ids) < target_reads and unread:
        paper_id = unread[0]
        plan = _plan(
            "read",
            f"精读：{_paper_title(run, paper_id)}",
            f"已选 {len(selected)} 篇，已读 {len(read_ids)} 篇；先补齐证据再对比。",
            {"paper_id": paper_id},
        )
        plan["requires_approval"] = _requires_approval(run, plan)
        return plan

    compare = _latest(run, "compare")
    compare_ids = set(compare.get("paper_ids") or [])
    if len(read_ids) >= 2 and (not compare or compare_ids != set(read_ids)):
        plan = _plan(
            "compare",
            f"横向对比 {len(read_ids)} 篇",
            "精读集合已变化，需要重新对齐方法、指标、共识与分歧。",
            {"paper_ids": read_ids},
        )
        plan["requires_approval"] = _requires_approval(run, plan)
        return plan

    topic = _latest(run, "topic")
    topic_ids = set(topic.get("paper_ids") or [])
    comparison_ready = len(read_ids) < 2 or bool(compare)
    if read_ids and comparison_ready and (not topic or topic_ids != set(read_ids)):
        plan = _plan(
            "topic",
            "生成候选问题",
            "证据已就绪，下一步把缺口转成可验证的候选方向。",
            {"paper_ids": read_ids},
        )
        plan["requires_approval"] = _requires_approval(run, plan)
        return plan

    asked = _asked_question_ids(run)
    pending_questions = [
        item for item in agent_state.open_questions(run)
        if item.get("id") not in asked
    ]
    if topic and pending_questions and _count(run, "ask") < 3 and not _latest(run, "draft"):
        question = pending_questions[0]
        plan = _plan(
            "ask",
            f"追问：{question['question'][:36]}",
            "候选方向前先补一条直接证据，避免把未验证缺口当成结论。",
            {"question_id": question["id"], "question": question["question"]},
        )
        plan["requires_approval"] = _requires_approval(run, plan)
        return plan

    draft = _latest(run, "draft")
    draft_ids = set(draft.get("paper_ids") or [])
    if topic and (not draft or draft_ids != set(read_ids)):
        plan = _plan(
            "draft",
            "生成写作骨架",
            "候选方向已生成，先把证据组织成相关工作与提纲结构。",
            {"paper_ids": read_ids},
        )
        plan["requires_approval"] = _requires_approval(run, plan)
        return plan

    if run.get("project_id") and draft:
        section_id = _draft_gap(run)
        if section_id is not None:
            plan = _plan(
                "write",
                "撰写章节",
                "写作骨架已生成，先补一个章节的证据化草稿。",
                {"section_id": section_id, "max_blocks": 3},
            )
            plan["requires_approval"] = (
                run.get("mode") == "checkpoint" or _requires_approval(run, plan)
            )
            return plan

    review = _latest(run, "review")
    review_ids = set(review.get("paper_ids") or [])
    if draft and (not review or review_ids != set(read_ids)):
        plan = _plan(
            "review",
            "核查草稿与论证",
            "写作骨架已生成，检查逐句证据、数值和结构缺口。",
            {"paper_ids": read_ids},
        )
        plan["requires_approval"] = _requires_approval(run, plan)
        return plan

    if review:
        claim_summary = review.get("claim_summary") or {}
        argument = review.get("argument") or {}
        unsupported = int(claim_summary.get("unsupported", 0))
        score = int(argument.get("score") or 100)
        draft_high = int(
            ((review.get("draft_audit") or {}).get("counts") or {}).get("high", 0)
        )
        if unsupported or score < 65 or draft_high:
            if draft_high:
                # Draft audit high issues become review-sourced high
                # questions. Unrelated open questions cannot close the audit
                # gap, so only use those; otherwise fall back to search.
                question = next(
                    (
                        item for item in agent_state.open_questions(run)
                        if item.get("id") not in asked
                        and item.get("source") == "review"
                    ),
                    None,
                )
            else:
                question = next(
                    (item for item in agent_state.open_questions(run) if item.get("id") not in asked),
                    None,
                )
                if question is None:
                    low_questions = [
                        item for item in agent_state.open_questions(run)
                        if item.get("id") not in asked
                    ]
                    question = low_questions[0] if low_questions else None
            if question and _count(run, "ask") < 3:
                plan = _plan(
                    "ask",
                    f"补证据：{question['question'][:34]}",
                    (
                        "草稿核查发现高严重度问题，先用证据问答确认能否补上。"
                        if draft_high
                        else "核查发现未支撑主张，先用证据问答确认能否补上。"
                    ),
                    {"question_id": question["id"], "question": question["question"]},
                )
                plan["requires_approval"] = _requires_approval(run, plan)
                return plan
            if _count(run, "search") < 3:
                query = question["question"] if question else f"{run['goal']} 关键证据"
                if _search_seen(run, query):
                    return _report_plan(
                        run,
                        "相同检索已执行且没有带来新的候选论文，停止重复扩展并按当前证据边界收束。",
                    )
                if draft_high and question is None:
                    rationale = (
                        "草稿核查仍有高严重度问题，但没有可用的追问问题，"
                        "先扩展检索补充直接证据。"
                    )
                elif draft_high:
                    rationale = (
                        "草稿核查仍有高严重度问题，追问次数已用尽，"
                        "扩展检索补充更直接的相关工作。"
                    )
                else:
                    rationale = "核查后仍有未支撑主张，尝试补充更直接的相关工作。"
                plan = _plan(
                    "search",
                    "证据不足，扩展检索",
                    rationale,
                    {
                        "query": query,
                        "reason": "草稿核查发现高严重度问题" if draft_high else "核查发现证据不足",
                        "expansion": True,
                    },
                )
                plan["requires_approval"] = _requires_approval(run, plan)
                return plan
        return _report_plan(run, "证据、写作与核查已形成闭环，生成带边界的研究简报。")

    return _report_plan(run, "没有更多可执行动作，按当前证据收束。")


def should_pause_before(run: dict, plan: dict) -> bool:
    if run.get("mode") == "step":
        return True
    if run.get("mode") == "checkpoint":
        return bool(plan.get("requires_approval")) or run.get("critic", {}).get("next_hint") == "human"
    return False


def _evidence_score(run: dict, review: dict) -> float:
    claim_summary = (review or {}).get("claim_summary") or {}
    total = int(claim_summary.get("total", 0))
    if total:
        supported = int(claim_summary.get("supported", 0))
        partial = int(claim_summary.get("partial", 0))
        return round((supported + partial * 0.5) / total, 3)
    evidence = run.get("evidence") or []
    if evidence:
        return round(sum(1 for item in evidence if item.get("verified")) / len(evidence), 3)
    return 0.0


def critique(run: dict, step: dict) -> dict:
    tool = step.get("tool") or ""
    artifact = step.get("artifact") or {}
    selected = _selected(run)
    read_ids = agent_state.known_paper_ids(run)
    target = max(1, min(len(selected), _max_papers(run)))
    coverage = round(min(1.0, len(read_ids) / target), 3) if selected else 0.0
    review = _latest(run, "review") or (artifact if tool == "review" else {})
    evidence_score = _evidence_score(run, review)
    added = int(artifact.get("evidence_added", 0) or 0)
    total_evidence = max(1, len(run.get("evidence") or []))
    saturation = round(min(1.0, added / total_evidence), 3) if added else 0.0

    summary = ""
    if tool == "search":
        candidates = artifact.get("candidates") or []
        new_ids = artifact.get("new_ids") or artifact.get("selected") or []
        summary = f"检索到 {len(candidates)} 篇，选择 {len(artifact.get('selected') or [])} 篇，新增 {len(new_ids)} 篇。"
        if not candidates:
            agent_state.upsert_question(
                run,
                "检索没有返回可用论文，是否需要更换关键词或来源？",
                kind="evidence",
                priority="high",
                source="critic",
            )
        elif artifact.get("expansion") and not new_ids:
            agent_state.upsert_question(
                run,
                "扩展检索没有新增直接相关工作，是否需要更换检索方向？",
                kind="evidence",
                priority="medium",
                source="critic",
            )
    elif tool == "read":
        paper = artifact.get("paper") or {}
        summary = f"精读 {paper.get('title') or paper.get('id') or '论文'}，新增 {added} 条证据。"
    elif tool == "ask":
        citations = artifact.get("citations") or []
        summary = f"追问得到 {len(citations)} 条引用，证据账本新增 {added} 条。"
        question_id = artifact.get("question_id") or (step.get("input") or {}).get("question_id")
        if question_id and citations:
            agent_state.resolve_question(run, question_id, f"获得 {len(citations)} 条引用证据")
    elif tool == "compare":
        gaps = artifact.get("gaps") or []
        summary = f"对比 {len(artifact.get('paper_ids') or read_ids)} 篇，发现 {len(gaps)} 个缺口线索。"
        for gap in gaps[:2]:
            text = gap.get("question") or gap.get("title") or "对比发现一个待验证缺口"
            agent_state.upsert_question(run, text, kind="evidence", priority="medium", source="compare")
    elif tool == "topic":
        questions = artifact.get("questions") or []
        summary = f"生成 {len(questions)} 个候选方向。"
    elif tool == "draft":
        summary = f"生成写作骨架，约 {len(artifact.get('markdown', ''))} 字符。"
    elif tool == "write":
        summary = (
            f"写入章节 {artifact.get('section_id') or '—'}，"
            f"新增 {artifact.get('blocks_added', 0)} 个块。"
        )
    elif tool == "review":
        claim_summary = artifact.get("claim_summary") or {}
        unsupported = int(claim_summary.get("unsupported", 0))
        summary = f"核查 {claim_summary.get('total', 0)} 句，证据不足 {unsupported} 句。"
        for claim in (artifact.get("claims") or [])[:3]:
            if claim.get("level") != "supported" and claim.get("sentence"):
                agent_state.upsert_question(
                    run,
                    f"补证据或改写：{claim['sentence'][:80]}",
                    kind="claim",
                    priority="high",
                    source="review",
                )
        if unsupported and not artifact.get("claims"):
            agent_state.upsert_question(
                run,
                "核查发现未支撑主张，需要补充直接证据或缩小论断。",
                kind="claim",
                priority="high",
                source="review",
            )
    elif tool == "report":
        summary = f"生成研究简报，约 {artifact.get('chars', 0)} 字符。"

    exhausted = agent_state.budget_exhausted(run)
    pending_questions = agent_state.open_questions(run)
    if tool == "report" or exhausted:
        next_hint = "finalize"
    elif review and (
        int((review.get("claim_summary") or {}).get("unsupported", 0)) > 0
        or int((review.get("argument") or {}).get("score") or 100) < 65
    ):
        next_hint = "ask" if _count(run, "ask") < 3 else "expand_search"
    elif not selected:
        next_hint = "expand_search"
    elif coverage < 0.6 and len(read_ids) < target:
        next_hint = "read_more"
    elif pending_questions:
        next_hint = "ask"
    elif coverage < 0.6:
        next_hint = "expand_search"
    else:
        next_hint = "continue"

    observation = {
        "summary": summary,
        "coverage": coverage,
        "evidence_score": evidence_score,
        "saturation": saturation,
        "next_hint": next_hint,
        "new_evidence": added,
        "at": agent_state.now(),
    }
    step["observation"] = observation
    run["critic"] = {
        "coverage": coverage,
        "evidence_score": evidence_score,
        "saturation": saturation,
        "last_observation": summary,
        "next_hint": next_hint,
    }
    return observation
