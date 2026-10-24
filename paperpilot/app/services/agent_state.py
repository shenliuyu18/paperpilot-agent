from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .. import config

TOOL_DEFS = {
    "search": {"title": "检索候选论文", "checkpoint": True},
    "read": {"title": "精读论文", "checkpoint": False},
    "ask": {"title": "追问证据", "checkpoint": False},
    "compare": {"title": "横向对比", "checkpoint": False},
    "topic": {"title": "缺口与选题", "checkpoint": True},
    "draft": {"title": "写作骨架", "checkpoint": False},
    "write": {"title": "撰写章节", "checkpoint": True},
    "review": {"title": "论证核查", "checkpoint": False},
    "report": {"title": "研究简报", "checkpoint": False},
}
TOOL_IDS = list(TOOL_DEFS)
DEFAULT_BUDGET = {
    "max_actions": 14,
    "max_seconds": 300,
    "max_papers": 6,
    "max_llm_calls": 4,
}
MODES = {"auto", "checkpoint", "step"}
MAX_RUNS = 40

# One reentrant lock serializes the run store and the project store. Keeping a
# single lock removes the run-store/project-store lock-order split: callers may
# nest project and run operations in any order without deadlocking, and no
# reader can observe projects.json while runs.json (or its temp file) is being
# replaced. agent_runs and project_state alias this lock.
STORE_LOCK = threading.RLock()


@contextmanager
def store_lock():
    with STORE_LOCK:
        yield


def store_path() -> Path:
    override = os.getenv("PAPERPILOT_AGENT_FILE")
    path = Path(override) if override else config.DATA_DIR / "agent_runs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def runs_dir() -> Path:
    override = os.getenv("PAPERPILOT_AGENT_DIR")
    path = Path(override) if override else config.DATA_DIR / "agent_runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_dir(run_id: str) -> Path:
    path = runs_dir() / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_store() -> dict:
    with STORE_LOCK:
        path = store_path()
        if not path.exists():
            return {"version": 2, "runs": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 2, "runs": {}}
        if not isinstance(data, dict) or not isinstance(data.get("runs"), dict):
            return {"version": 2, "runs": {}}
        return data


def write_store(data: dict) -> None:
    with STORE_LOCK:
        path = store_path()
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_run_id(goal: str) -> str:
    raw = f"{goal}|{time.time_ns()}|{uuid.uuid4().hex}".encode("utf-8")
    return "run-" + hashlib.sha1(raw).hexdigest()[:10]


def new_step_id(run: dict) -> str:
    highest = 0
    for step in run.get("steps", []):
        match = re.fullmatch(r"act-(\d+)", str(step.get("id") or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"act-{highest + 1:03d}"


def add_event(run: dict, kind: str, message: str) -> None:
    run.setdefault("events", []).append({"at": now(), "type": kind, "message": message})
    run["events"] = run["events"][-120:]


def add_decision(run: dict, kind: str, choice, note: str = "") -> None:
    run.setdefault("decisions", []).append(
        {"at": now(), "kind": kind, "choice": choice, "note": note}
    )
    run["decisions"] = run["decisions"][-80:]


def _normalize_question(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip("？?")


def _default_critic() -> dict:
    return {
        "coverage": 0.0,
        "evidence_score": 0.0,
        "saturation": 0.0,
        "last_observation": "",
        "next_hint": "continue",
    }


def create_run_state(
    goal: str,
    source: str = "demo",
    mode: str = "checkpoint",
    budget: dict | None = None,
    paper_ids: list[str] | None = None,
    project_id: str | None = None,
    seed: dict | None = None,
) -> dict:
    goal = (goal or "").strip()
    if len(goal) < 2:
        raise ValueError("研究目标至少需要 2 个字符。")
    if mode not in MODES:
        raise ValueError("自主程度必须是 auto、checkpoint 或 step。")
    merged_budget = dict(DEFAULT_BUDGET)
    if budget:
        for key, value in budget.items():
            if key in merged_budget:
                merged_budget[key] = int(value)
    timestamp = now()
    run_id = new_run_id(goal)
    run = {
        "version": 3,
        "id": run_id,
        "goal": goal,
        "source": source or "demo",
        "mode": mode,
        "status": "queued",
        "paper_ids": list(dict.fromkeys(paper_ids or []))[: merged_budget["max_papers"]],
        "selected_topic": None,
        "budget": merged_budget,
        "budget_used": {"actions": 0, "seconds": 0, "llm_calls": 0},
        "plan": None,
        "steps": [],
        "evidence": [],
        "open_questions": [],
        "decisions": [],
        "critic": _default_critic(),
        "report": {},
        "events": [],
        "legacy": False,
        "project_id": project_id,
        "seed_evidence_ids": [],
        "seed_paper_ids": [],
        "seed_summary": {"evidence": 0, "questions": 0, "papers": 0},
        "steering": [],
        "pause_requested": False,
        "replan_requested": False,
        "stream_seq": 0,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    add_event(run, "created", f"已创建任务：{goal}")
    if seed:
        run["seed_paper_ids"] = [
            paper["id"] for paper in seed.get("papers") or [] if paper.get("id")
        ]
        if not run["paper_ids"]:
            run["paper_ids"] = list(run["seed_paper_ids"])[: merged_budget["max_papers"]]
        run["seed_summary"] = dict(seed.get("summary") or run["seed_summary"])
        run["selected_topic"] = seed.get("selected_topic") or run.get("selected_topic")
        for item in seed.get("evidence") or []:
            run["seed_evidence_ids"].append(
                item.get("project_evidence_id") or item.get("id")
            )
        add_evidence(run, seed.get("evidence") or [], "seed")
        for item in seed.get("open_questions") or []:
            question = upsert_question(
                run,
                item.get("question") or "",
                priority=item.get("priority"),
                kind=item.get("kind"),
                source="project",
                evidence_ids=item.get("evidence_ids") or [],
            )
            question["origin"] = "project"
            question["project_question_id"] = item.get("project_question_id") or item.get("id")
    return run


def _legacy_step_to_action(step: dict, index: int) -> dict:
    tool = str(step.get("id") or step.get("tool") or "")
    status = str(step.get("status") or "pending")
    if status == "running":
        status = "pending"
    return {
        "id": f"act-{index:03d}",
        "tool": tool,
        "title": step.get("title") or TOOL_DEFS.get(tool, {}).get("title", tool),
        "status": status,
        "rationale": "v0.9 固定步骤迁移",
        "input": {},
        "artifact": copy.deepcopy(step.get("artifact") or {}),
        "observation": {"summary": step.get("summary") or ""},
        "started_at": step.get("started_at") or "",
        "finished_at": step.get("finished_at") or "",
        "attempts": int(step.get("attempts") or 0),
        "checkpoint": tool in {"search", "topic"},
        "decision": None,
        "error": step.get("error") or "",
    }


def normalize_run(run: dict) -> tuple[dict, bool]:
    changed = False
    original_version = int(run.get("version") or 0)
    if original_version < 2 or not isinstance(run.get("steps"), list) or "plan" not in run:
        legacy_steps = run.get("steps") or []
        run["steps"] = [
            _legacy_step_to_action(step, index)
            for index, step in enumerate(legacy_steps, start=1)
        ]
        run["version"] = 2
        run["mode"] = run.get("mode") if run.get("mode") in MODES else "checkpoint"
        run.setdefault("selected_topic", None)
        run.setdefault("evidence", [])
        run.setdefault("open_questions", [])
        run.setdefault("decisions", [])
        run.setdefault("critic", _default_critic())
        run.setdefault("report", {})
        run.setdefault("budget", dict(DEFAULT_BUDGET))
        run.setdefault("budget_used", {"actions": len(run["steps"]), "seconds": 0, "llm_calls": 0})
        run.setdefault("legacy", True)
        run.setdefault("migrated_from", "0.9")
        if not run.get("plan"):
            pending = next((step for step in run["steps"] if step["status"] in {"pending", "failed"}), None)
            if pending:
                run["plan"] = {
                    "tool": pending["tool"],
                    "title": pending["title"],
                    "rationale": "从 v0.9 任务恢复：继续未完成动作。",
                    "input": {},
                    "requires_approval": True,
                }
        run["updated_at"] = run.get("updated_at") or now()
        add_event(run, "migrated", "已从 v0.9 任务结构迁移")
        changed = True
    defaults = {
        "selected_topic": None,
        "budget": dict(DEFAULT_BUDGET),
        "budget_used": {"actions": 0, "seconds": 0, "llm_calls": 0},
        "plan": None,
        "evidence": [],
        "open_questions": [],
        "decisions": [],
        "critic": _default_critic(),
        "report": {},
        "events": [],
        "legacy": False,
        "project_id": None,
        "seed_evidence_ids": [],
        "seed_paper_ids": [],
        "seed_summary": {"evidence": 0, "questions": 0, "papers": 0},
        "steering": [],
        "pause_requested": False,
        "replan_requested": False,
        "stream_seq": 0,
    }
    for key, value in defaults.items():
        if key not in run:
            run[key] = copy.deepcopy(value)
            changed = True
    if int(run.get("version") or 0) < 3:
        for key, value in {
            "project_id": None,
            "seed_evidence_ids": [],
            "seed_paper_ids": [],
            "seed_summary": {"evidence": 0, "questions": 0, "papers": 0},
            "steering": [],
            "pause_requested": False,
            "replan_requested": False,
            "stream_seq": 0,
        }.items():
            run.setdefault(key, copy.deepcopy(value))
        run["version"] = 3
        changed = True
    if not isinstance(run.get("steps"), list):
        run["steps"] = []
        changed = True
    for step in run["steps"]:
        if "tool" not in step:
            step["tool"] = step.get("id") or ""
            changed = True
        step.setdefault("title", TOOL_DEFS.get(step["tool"], {}).get("title", step["tool"]))
        step.setdefault("status", "done")
        step.setdefault("rationale", "")
        step.setdefault("input", {})
        step.setdefault("artifact", {})
        step.setdefault("observation", {})
        step.setdefault("attempts", 1)
        step.setdefault("checkpoint", step["tool"] in {"search", "topic"})
        step.setdefault("decision", None)
        step.setdefault("error", "")
    return run, changed


def summarize_run(run: dict) -> dict:
    return {
        "id": run["id"],
        "goal": run.get("goal", ""),
        "source": run.get("source", "demo"),
        "mode": run.get("mode", "checkpoint"),
        "status": run.get("status", "queued"),
        "paper_count": len(run.get("paper_ids") or []),
        "evidence_count": len(run.get("evidence") or []),
        "open_question_count": len(open_questions(run)),
        "action_count": len(run.get("steps") or []),
        "report_available": bool(run.get("report", {}).get("path")),
        "plan_tool": (run.get("plan") or {}).get("tool", ""),
        "legacy": bool(run.get("legacy")),
        "created_at": run.get("created_at", ""),
        "updated_at": run.get("updated_at", ""),
    }


def add_evidence(run: dict, items: list[dict], action_id: str = "") -> int:
    def signature(item: dict) -> tuple[str, str]:
        quote = re.sub(r"\s+", " ", str(item.get("quote") or "")).strip().lower()
        return (str(item.get("paper_id") or ""), quote[:200])

    existing = {signature(item) for item in run.get("evidence", [])}
    added = 0
    for item in items or []:
        quote = str(item.get("quote") or "").strip()
        if not quote:
            continue
        key = signature(item)
        if key in existing:
            continue
        existing.add(key)
        run.setdefault("evidence", []).append(
            {
                "id": f"ev-{len(run.get('evidence', [])) + 1:03d}",
                "paper_id": item.get("paper_id") or "",
                "title": item.get("title") or "",
                "section": item.get("section") or "",
                "page": item.get("page"),
                "claim": item.get("claim") or item.get("label") or "",
                "quote": quote[:360],
                "verified": bool(item.get("verified")),
                "kind": item.get("kind") or "text",
                "source_action": action_id,
                "origin": item.get("origin") or "run",
                "project_evidence_id": item.get("project_evidence_id") or "",
            }
        )
        added += 1
    if len(run.get("evidence", [])) > 120:
        run["evidence"] = run["evidence"][-120:]
    return added


def find_question(run: dict, question_id: str) -> dict | None:
    return next(
        (item for item in run.get("open_questions", []) if item.get("id") == question_id),
        None,
    )


def upsert_question(run: dict, question: str, **fields) -> dict:
    normalized = _normalize_question(question)
    if not normalized:
        raise ValueError("开放问题不能为空。")
    for item in run.get("open_questions", []):
        if _normalize_question(item.get("question", "")) == normalized:
            for key in ("priority", "kind", "source"):
                if fields.get(key):
                    item[key] = fields[key]
            return item
    item = {
        "id": f"q-{len(run.get('open_questions', [])) + 1:03d}",
        "question": " ".join(str(question).split()),
        "status": "open",
        "priority": fields.get("priority") or "medium",
        "kind": fields.get("kind") or "evidence",
        "source": fields.get("source") or "critic",
        "evidence_ids": list(fields.get("evidence_ids") or []),
        "resolution": "",
        "created_at": now(),
    }
    run.setdefault("open_questions", []).append(item)
    return item


def resolve_question(run: dict, question_id: str, resolution: str = "") -> dict | None:
    item = find_question(run, question_id)
    if not item:
        return None
    item["status"] = "resolved"
    item["resolution"] = str(resolution or "")
    item["resolved_at"] = now()
    return item


def open_questions(run: dict, priority: str | None = None) -> list[dict]:
    items = [item for item in run.get("open_questions", []) if item.get("status") == "open"]
    if priority:
        items = [item for item in items if item.get("priority") == priority]
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(items, key=lambda item: order.get(item.get("priority"), 9))


def artifact_by_tool(run: dict, tool: str, latest: bool = True) -> dict:
    steps = [
        step for step in run.get("steps", [])
        if step.get("tool") == tool and step.get("status") == "done"
    ]
    if not steps:
        return {}
    step = steps[-1] if latest else steps[0]
    return copy.deepcopy(step.get("artifact") or {})


def completed_tools(run: dict) -> list[str]:
    return [
        step.get("tool")
        for step in run.get("steps", [])
        if step.get("status") == "done" and step.get("tool")
    ]


def read_paper_ids(run: dict) -> list[str]:
    ids = []
    for step in run.get("steps", []):
        if step.get("tool") != "read" or step.get("status") != "done":
            continue
        artifact = step.get("artifact") or {}
        paper_id = (artifact.get("paper") or {}).get("id") or artifact.get("paper_id")
        if paper_id and paper_id not in ids:
            ids.append(paper_id)
    return ids


def known_paper_ids(run: dict) -> list[str]:
    ids = list(read_paper_ids(run))
    for paper_id in run.get("seed_paper_ids") or []:
        if paper_id and paper_id not in ids:
            ids.append(paper_id)
    return ids


def refresh_seconds(run: dict) -> int:
    try:
        created = datetime.strptime(str(run.get("created_at")), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return 0
    seconds = max(0, int((datetime.now() - created).total_seconds()))
    run.setdefault("budget_used", {})["seconds"] = seconds
    return seconds


def spend_action(run: dict) -> int:
    used = run.setdefault("budget_used", {"actions": 0, "seconds": 0, "llm_calls": 0})
    used["actions"] = int(used.get("actions", 0)) + 1
    refresh_seconds(run)
    return used["actions"]


def spend_llm(run: dict, count: int = 1) -> int:
    used = run.setdefault("budget_used", {"actions": 0, "seconds": 0, "llm_calls": 0})
    used["llm_calls"] = int(used.get("llm_calls", 0)) + count
    return used["llm_calls"]


def budget_exhausted(run: dict) -> str | None:
    budget = run.get("budget") or DEFAULT_BUDGET
    used = run.setdefault("budget_used", {"actions": 0, "seconds": 0, "llm_calls": 0})
    if int(used.get("actions", 0)) >= int(budget.get("max_actions", 14)):
        return "动作预算已用尽"
    if refresh_seconds(run) >= int(budget.get("max_seconds", 300)):
        return "时间预算已用尽"
    return None


def llm_budget_remaining(run: dict) -> int:
    snapshot = budget_snapshot(run)
    return max(
        0,
        int(snapshot["llm_calls"]["max"]) - int(snapshot["llm_calls"]["used"]),
    )


def budget_snapshot(run: dict) -> dict:
    budget = run.get("budget") or DEFAULT_BUDGET
    used = run.get("budget_used") or {}
    return {
        "actions": {"used": int(used.get("actions", 0)), "max": int(budget.get("max_actions", 14))},
        "seconds": {"used": refresh_seconds(run), "max": int(budget.get("max_seconds", 300))},
        "papers": {"used": len(run.get("paper_ids") or []), "max": int(budget.get("max_papers", 6))},
        "llm_calls": {"used": int(used.get("llm_calls", 0)), "max": int(budget.get("max_llm_calls", 4))},
    }
