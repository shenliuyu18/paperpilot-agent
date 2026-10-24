from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .. import config
from . import agent_state

PROJECT_STAGES = ("exploring", "reading", "writing", "review", "done")
MAX_PROJECTS = 20
MAX_PROJECT_EVIDENCE = 300
MAX_PROJECT_QUESTIONS = 80
MAX_PROJECT_DECISIONS = 120
MAX_PROJECT_PAPERS = 100

# Shared by API and worker threads: every project read-modify-write cycle must
# run inside project_lock() so concurrent saves cannot drop another writer's
# changes. This is the same reentrant lock as the run store (agent_state.
# STORE_LOCK), so project and run operations can nest in either order without
# a lock-order inversion. RLock keeps nested service calls (e.g.
# update_project -> add_decision) reentrant.
_LOCK = agent_state.STORE_LOCK


@contextmanager
def project_lock():
    with _LOCK:
        yield


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def projects_path() -> Path:
    override = os.getenv("PAPERPILOT_PROJECT_FILE")
    path = Path(override) if override else config.DATA_DIR / "projects.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def projects_dir() -> Path:
    override = os.getenv("PAPERPILOT_PROJECT_DIR")
    path = Path(override) if override else config.DATA_DIR / "projects"
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_dir(project_id: str) -> Path:
    path = projects_dir() / project_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_projects() -> dict:
    # Hold the shared store lock while the file is open: on Windows a reader
    # that still has projects.json open makes a concurrent writer's
    # temp.replace(path) fail with PermissionError. RLock keeps nested calls
    # from create_project/update_project/... reentrant.
    with _LOCK:
        path = projects_path()
        if not path.exists():
            return {"version": 1, "projects": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "projects": {}}
        if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
            return {"version": 1, "projects": {}}
        return data


def write_projects(data: dict) -> None:
    path = projects_path()
    with _LOCK:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)


def _new_project_id(title: str) -> str:
    raw = f"{title}|{time.time_ns()}|{uuid.uuid4().hex}".encode("utf-8")
    return "prj-" + hashlib.sha1(raw).hexdigest()[:10]


def create_project(
    title: str,
    question: str,
    scope: str = "",
    constraints: list[str] | None = None,
    success_criteria: str = "",
    tags: list[str] | None = None,
) -> dict:
    title = " ".join(str(title or "").split())
    question = " ".join(str(question or "").split())
    if not 2 <= len(title) <= 80:
        raise ValueError("项目标题需要 2–80 个字符。")
    if not 2 <= len(question) <= 500:
        raise ValueError("研究问题需要 2–500 个字符。")
    cleaned_constraints = [" ".join(str(item).split()) for item in (constraints or []) if str(item).strip()]
    cleaned_tags = [" ".join(str(item).split()) for item in (tags or []) if str(item).strip()]
    if len(cleaned_constraints) > 8:
        raise ValueError("约束最多 8 条。")
    if any(not 2 <= len(item) <= 200 for item in cleaned_constraints):
        raise ValueError("每条约束需要 2–200 个字符。")
    if len(cleaned_tags) > 8:
        raise ValueError("标签最多 8 个。")
    if any(not 1 <= len(item) <= 24 for item in cleaned_tags):
        raise ValueError("每个标签需要 1–24 个字符。")
    project = {
        "version": 1,
        "id": _new_project_id(title),
        "title": title,
        "brief": {
            "question": question,
            "scope": str(scope or "")[:1000],
            "constraints": cleaned_constraints,
            "success_criteria": str(success_criteria or "")[:500],
        },
        "stage": "exploring",
        "status": "active",
        "tags": cleaned_tags,
        "run_ids": [],
        "active_run_id": None,
        "counts": {"runs": 0, "evidence": 0, "open_questions": 0, "draft_sections": 0, "draft_blocks": 0},
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _LOCK:
        data = read_projects()
        if len(data["projects"]) >= MAX_PROJECTS:
            raise ValueError("项目数量已达上限。")
        while project["id"] in data["projects"]:
            project["id"] = _new_project_id(title)
        data["projects"][project["id"]] = project
        write_projects(data)
        write_memory(project["id"], empty_memory(project["id"]))
    return copy.deepcopy(project)


def normalize_project(project: dict) -> tuple[dict, bool]:
    changed = False
    defaults = {
        "version": 1,
        "stage": "exploring",
        "status": "active",
        "tags": [],
        "run_ids": [],
        "active_run_id": None,
        "counts": {"runs": 0, "evidence": 0, "open_questions": 0, "draft_sections": 0, "draft_blocks": 0},
    }
    for key, value in defaults.items():
        if key not in project:
            project[key] = copy.deepcopy(value)
            changed = True
    if project.get("stage") not in PROJECT_STAGES:
        project["stage"] = "exploring"
        changed = True
    return project, changed


def memory_path(project_id: str) -> Path:
    return project_dir(project_id) / "memory.json"


def empty_memory(project_id: str) -> dict:
    return {
        "version": 1,
        "project_id": project_id,
        "evidence": [],
        "open_questions": [],
        "decisions": [],
        "papers": {},
        "reconciled_runs": [],
    }


def reconciled_run_ids(memory: dict) -> list[str]:
    """Return every reconciled run id, deduplicated in first-seen order.

    There is deliberately no FIFO cap. A project references at most
    ``agent_state.MAX_RUNS`` runs at once, but over its lifetime it may
    reconcile many more (detached/released runs), and forgetting an old id
    would make the next read promote that run again.
    """
    runs = memory.get("reconciled_runs")
    if not isinstance(runs, list):
        return []
    cleaned = [str(item) for item in runs if item]
    return list(dict.fromkeys(cleaned))


def read_memory(project_id: str) -> dict:
    # Same rule as read_projects: never keep memory.json open outside the
    # shared store lock, or a concurrent write's atomic replace can fail.
    with _LOCK:
        path = memory_path(project_id)
        if not path.exists():
            return empty_memory(project_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return empty_memory(project_id)
        if not isinstance(data, dict):
            return empty_memory(project_id)
        base = empty_memory(project_id)
        base.update(data)
        base["reconciled_runs"] = reconciled_run_ids(base)
        return base


def write_memory(project_id: str, memory: dict) -> None:
    path = memory_path(project_id)
    with _LOCK:
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def evidence_key(item: dict) -> str:
    return "|".join([
        str(item.get("paper_id") or ""),
        str(item.get("section") or ""),
        str(item.get("page") if item.get("page") is not None else ""),
        _normalize_text(item.get("quote")),
    ])


def project_evidence_id(item: dict) -> str:
    return "pev-" + hashlib.sha1(evidence_key(item).encode("utf-8")).hexdigest()[:10]


def _question_id(question: str) -> str:
    normalized = re.sub(r"\s+", "", str(question or "")).strip("？?")
    return "pq-" + hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:10]


def _decision_id(run_id: str, decision: dict) -> str:
    raw = json.dumps(
        [run_id, decision.get("kind"), decision.get("choice"), decision.get("note")],
        ensure_ascii=False,
        sort_keys=True,
    )
    return "pd-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def _run_papers(run: dict) -> list[dict]:
    path = agent_state.run_dir(run["id"]) / "papers.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def promote_run(project_id: str, run: dict) -> dict:
    with _LOCK:
        return _promote_run(project_id, run)


def _promote_run(project_id: str, run: dict) -> dict:
    memory = read_memory(project_id)
    run_id = str(run.get("id") or "")
    origin = {"run_id": run_id}

    existing_evidence = {item["id"]: item for item in memory["evidence"] if item.get("id")}
    for item in run.get("evidence") or []:
        quote = str(item.get("quote") or "").strip()
        if not quote:
            continue
        item = {**item, "quote": quote}
        evidence_id = project_evidence_id(item)
        current = existing_evidence.get(evidence_id)
        if current is None:
            current = {
                "id": evidence_id,
                "paper_id": item.get("paper_id") or "",
                "title": item.get("title") or "",
                "section": item.get("section") or "",
                "page": item.get("page"),
                "quote": quote[:360],
                "claim": item.get("claim") or "",
                "verified": bool(item.get("verified")),
                "kind": item.get("kind") or "text",
                "origin": {**origin, "action_id": item.get("source_action") or "", "tool": "evidence"},
                "created_at": _now(),
                "updated_at": _now(),
            }
            memory["evidence"].append(current)
            existing_evidence[evidence_id] = current
        else:
            for key in ("title", "claim"):
                if item.get(key) and not current.get(key):
                    current[key] = item[key]
            current["verified"] = bool(current.get("verified") or item.get("verified"))
            current["updated_at"] = _now()

    questions = {item["id"]: item for item in memory["open_questions"] if item.get("id")}
    for item in run.get("open_questions") or []:
        text = str(item.get("question") or "").strip()
        if not text:
            continue
        question_id = _question_id(text)
        current = questions.get(question_id)
        if current is None:
            current = {
                "id": question_id,
                "question": " ".join(text.split()),
                "status": item.get("status") or "open",
                "priority": item.get("priority") or "medium",
                "kind": item.get("kind") or "evidence",
                "origin": {**origin, "action_id": item.get("source_action") or "", "tool": "critic"},
                "recurrences": 1,
                "resolution": item.get("resolution") or "",
                "history": [],
                "created_at": _now(),
                "updated_at": _now(),
            }
            memory["open_questions"].append(current)
            questions[question_id] = current
        else:
            new_status = item.get("status") or "open"
            if new_status != current.get("status"):
                current["history"] = (current.get("history") or [])[-9:] + [{
                    "at": _now(), "status": current.get("status"), "run_id": run_id,
                }]
                current["status"] = new_status
                if new_status == "open":
                    current["recurrences"] = int(current.get("recurrences") or 1) + 1
            current["priority"] = item.get("priority") or current.get("priority") or "medium"
            if item.get("resolution"):
                current["resolution"] = item["resolution"]
            current["updated_at"] = _now()

    existing_decisions = {item["id"] for item in memory["decisions"] if item.get("id")}
    for decision in run.get("decisions") or []:
        decision_id = _decision_id(run_id, decision)
        if decision_id in existing_decisions:
            continue
        memory["decisions"].append({
            "id": decision_id,
            "kind": decision.get("kind") or "decision",
            "choice": copy.deepcopy(decision.get("choice")),
            "note": decision.get("note") or "",
            "origin": {**origin, "tool": decision.get("kind") or ""},
            "at": decision.get("at") or _now(),
        })
        existing_decisions.add(decision_id)

    for paper in _run_papers(run):
        paper_id = str(paper.get("id") or "")
        if not paper_id:
            continue
        current = memory["papers"].get(paper_id) or {}
        merged = {**current}
        for key in ("title", "authors", "year", "venue", "doi", "url", "source"):
            if paper.get(key) not in (None, "", []):
                merged[key] = copy.deepcopy(paper[key])
        merged["id"] = paper_id
        memory["papers"][paper_id] = merged

    # Stage the watermark in the same atomic write as the promoted data: if the
    # write fails, the run stays absent from reconciled_runs and is retried by
    # the next reconcile_project call. Re-promotion moves the id to the tail
    # without ever evicting older ids.
    if run_id:
        reconciled = [item for item in reconciled_run_ids(memory) if item != run_id]
        memory["reconciled_runs"] = reconciled + [run_id]

    memory = trim_memory(memory)
    write_memory(project_id, memory)
    return memory


def trim_memory(memory: dict) -> dict:
    evidence = memory.get("evidence") or []
    evidence.sort(key=lambda item: (bool(item.get("verified")), item.get("updated_at", "")), reverse=True)
    memory["evidence"] = evidence[:MAX_PROJECT_EVIDENCE]
    questions = memory.get("open_questions") or []
    questions.sort(key=lambda item: (item.get("status") == "open", item.get("priority") == "high", item.get("updated_at", "")), reverse=True)
    memory["open_questions"] = questions[:MAX_PROJECT_QUESTIONS]
    decisions = sorted(memory.get("decisions") or [], key=lambda item: item.get("at", ""), reverse=True)
    memory["decisions"] = decisions[:MAX_PROJECT_DECISIONS]
    memory["reconciled_runs"] = reconciled_run_ids(memory)
    papers = memory.get("papers") or {}
    if len(papers) > MAX_PROJECT_PAPERS:
        keep = [item.get("paper_id") for item in memory["evidence"][:MAX_PROJECT_PAPERS]]
        memory["papers"] = {key: value for key, value in papers.items() if key in keep}
    return memory


def seed_memory(project_id: str, max_papers: int = 6) -> dict:
    memory = read_memory(project_id)
    evidence = [item for item in memory["evidence"] if item.get("verified")][:40]
    questions = [item for item in memory["open_questions"] if item.get("status") == "open"][:20]
    paper_ids = []
    for item in evidence:
        paper_id = item.get("paper_id")
        if paper_id and paper_id not in paper_ids:
            paper_ids.append(paper_id)
    papers = [memory["papers"][pid] for pid in paper_ids if pid in memory["papers"]][:max_papers]
    topic = next(
        (item for item in reversed(memory["decisions"]) if item.get("kind") == "topic"),
        None,
    )
    topic_choice = topic.get("choice") if topic else None
    if isinstance(topic_choice, dict):
        selected_topic = copy.deepcopy(topic_choice)
    elif topic_choice:
        selected_topic = {"id": str(topic_choice), "title": str(topic_choice)}
    else:
        selected_topic = None
    return {
        "evidence": [
            {
                **copy.deepcopy(item),
                "project_evidence_id": item["id"],
                "origin": "project",
            }
            for item in evidence
        ],
        "open_questions": [
            {
                **copy.deepcopy(item),
                "project_question_id": item["id"],
                "origin": "project",
            }
            for item in questions
        ],
        "papers": copy.deepcopy(papers),
        "selected_topic": selected_topic,
        "summary": {"evidence": len(evidence), "questions": len(questions), "papers": len(papers)},
    }


def project_counts(project: dict, memory: dict, draft: dict | None = None) -> dict:
    sections = (draft or {}).get("sections") or []
    blocks = sum(len(section.get("blocks") or []) for section in sections)
    return {
        "runs": len(project.get("run_ids") or []),
        "evidence": len(memory.get("evidence") or []),
        "open_questions": sum(1 for item in memory.get("open_questions") or [] if item.get("status") == "open"),
        "draft_sections": len(sections),
        "draft_blocks": blocks,
    }
