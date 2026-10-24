from __future__ import annotations

import hashlib
import re
from collections import Counter

from . import agent_state, project_service


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def evidence_key(item: dict) -> str:
    raw = "|".join([
        str(item.get("paper_id") or ""),
        str(item.get("section") or ""),
        str(item.get("page") if item.get("page") is not None else ""),
        _normalize(item.get("quote")),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _run_summary(run: dict) -> dict:
    steps = run.get("steps") or []
    evidence = run.get("evidence") or []
    questions = run.get("open_questions") or []
    decisions = run.get("decisions") or []
    return {
        "id": run.get("id"),
        "goal": run.get("goal"),
        "mode": run.get("mode"),
        "status": run.get("status"),
        "action_count": len(steps),
        "tool_counts": dict(Counter(step.get("tool") for step in steps if step.get("tool"))),
        "evidence_count": len(evidence),
        "verified_count": sum(1 for item in evidence if item.get("verified")),
        "open_question_count": sum(1 for item in questions if item.get("status") == "open"),
        "resolved_question_count": sum(1 for item in questions if item.get("status") == "resolved"),
        "decision_count": len(decisions),
        "budget": run.get("budget") or {},
        "report_chars": int((run.get("report") or {}).get("chars") or 0),
    }


def compare_runs(project_id: str, run_ids: list[str]) -> dict:
    ids = [str(item) for item in run_ids if str(item).strip()]
    if not 2 <= len(ids) <= 3:
        raise ValueError("请选择 2–3 个运行进行对比。")
    if len(set(ids)) != len(ids):
        raise ValueError("运行 ID 不能重复。")
    project = project_service.get_project(project_id)
    if not project:
        raise KeyError(project_id)
    data = agent_state.read_store().get("runs", {})
    runs = []
    for run_id in ids:
        run = data.get(run_id)
        if not run:
            raise KeyError(run_id)
        if run.get("project_id") != project_id:
            raise ValueError("运行不属于当前项目。")
        runs.append(run)

    keys = {run["id"]: {evidence_key(item) for item in run.get("evidence") or []} for run in runs}
    shared = set.intersection(*keys.values()) if keys else set()
    unique = {run_id: sorted(values - shared) for run_id, values in keys.items()}
    return {
        "runs": [_run_summary(run) for run in runs],
        "tool_sequences": {run["id"]: [step.get("tool") for step in run.get("steps") or []] for run in runs},
        "evidence": {
            "keys": {run_id: sorted(values) for run_id, values in keys.items()},
            "shared": sorted(shared),
            "unique": unique,
        },
        "questions": {
            run["id"]: [item.get("question") for item in run.get("open_questions") or []]
            for run in runs
        },
        "decisions": {
            run["id"]: [item.get("kind") for item in run.get("decisions") or []]
            for run in runs
        },
    }
