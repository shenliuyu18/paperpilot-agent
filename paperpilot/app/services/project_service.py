from __future__ import annotations

import copy
import hashlib
import shutil

from . import agent_state, project_state

ACTIVE_STATUSES = {"queued", "running", "awaiting_approval", "paused"}
TERMINAL_STATUSES = {"completed", "cancelled", "failed", "interrupted"}


def _store() -> dict:
    return project_state.read_projects()


def _save(project: dict) -> dict:
    with project_state.project_lock():
        data = _store()
        project, _ = project_state.normalize_project(project)
        project["updated_at"] = agent_state.now()
        data["projects"][project["id"]] = copy.deepcopy(project)
        project_state.write_projects(data)
        return copy.deepcopy(project)


def _get_raw(project_id: str) -> dict | None:
    with project_state.project_lock():
        project = _store()["projects"].get(project_id)
        if not project:
            return None
        project, changed = project_state.normalize_project(project)
        if changed:
            _save(project)
        return project


def draft_summary(project_id: str) -> dict:
    try:
        from . import draft_service
        return draft_service.summary(project_id)
    except (ImportError, FileNotFoundError):
        return {"sections": 0, "blocks": 0, "updated_at": "", "audit_high": 0}


def _read_draft(project_id: str) -> dict | None:
    try:
        from . import draft_state
        return draft_state.read_draft(project_id)
    except ImportError:
        return None


def _recount(project: dict) -> dict:
    """Refresh ``counts`` from memory and draft.

    Always call this before ``_save``: ``_save`` persists the object it is
    given, so ``_recount(_save(project))`` stores stale counts and only fixes
    the returned copy.
    """
    memory = project_state.read_memory(project["id"])
    project["counts"] = project_state.project_counts(project, memory, _read_draft(project["id"]))
    return project


def sync_counts(project_id: str) -> dict:
    """Recompute and persist a project's counts from memory and draft.

    Called by the draft service after a draft write so the project overview
    shows fresh section/block counts without a separate read cycle.
    """
    with project_state.project_lock():
        project = _get_raw(project_id)
        if not project:
            raise KeyError(project_id)
        # Recount before saving: _save persists the object it is given, so the
        # previous _recount(_save(...)) order left projects.json with stale
        # counts while only the returned copy looked fresh.
        return _save(_recount(project))


def create_project(
    title: str,
    question: str,
    scope: str = "",
    constraints: list[str] | None = None,
    success_criteria: str = "",
    tags: list[str] | None = None,
) -> dict:
    with project_state.project_lock():
        project = project_state.create_project(
            title, question, scope, constraints, success_criteria, tags
        )
        return _recount(project)


def list_projects(status: str = "active", limit: int = 20) -> list[dict]:
    """List projects, newest first.

    ``limit`` is clamped to 0–50; a non-positive limit returns an empty list.
    """
    limit = int(limit)
    if limit <= 0:
        return []
    projects = []
    for project in _store()["projects"].values():
        project, _ = project_state.normalize_project(project)
        if status and project.get("status") != status:
            continue
        project = _recount(project)
        projects.append({
            "id": project["id"],
            "title": project["title"],
            "brief": copy.deepcopy(project.get("brief") or {}),
            "stage": project.get("stage", "exploring"),
            "status": project.get("status", "active"),
            "tags": list(project.get("tags") or []),
            "counts": copy.deepcopy(project.get("counts") or {}),
            "active_run_id": project.get("active_run_id"),
            "updated_at": project.get("updated_at", ""),
        })
    projects.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return projects[:min(limit, 50)]


def reconcile_project(project_id: str, limit: int = 5) -> dict | None:
    """Replay runs whose promotion never reached project memory.

    ``reconciled_runs`` in project memory is the watermark: runs already
    promoted by ``on_run_saved`` (or by an earlier reconciliation) are skipped,
    so repeated detail reads neither re-apply status transitions nor rewrite
    memory. Every run missing from the watermark is promoted in a single pass
    in ascending ``updated_at`` order, so the final status always comes from
    the newest run instead of whichever run survived a limit window.

    ``limit`` is kept for API compatibility. A project references at most
    ``agent_state.MAX_RUNS`` runs, so the pending backlog is always drained in
    one pass; ``limit`` only serves as an upper safety bound for a contaminated
    run list and never trims older pending runs.
    """
    with project_state.project_lock():
        project = _get_raw(project_id)
        if not project:
            return None
        limit = max(0, int(limit))
        run_ids = list(project.get("run_ids") or [])
        data = agent_state.read_store()
        known = data.get("runs", {})
        # create_run binds the project before writing the run store, so a
        # crash between those steps (or a failed compensating release) can
        # leave a run id with no store record. Drop it here; this is the
        # self-healing half of that ordering.
        missing = [run_id for run_id in run_ids if run_id not in known]
        if missing:
            run_ids = [run_id for run_id in run_ids if run_id in known]
            project["run_ids"] = run_ids
        runs = [known[run_id] for run_id in run_ids]
        runs.sort(key=lambda item: item.get("updated_at") or "")
        reconciled = set(
            project_state.reconciled_run_ids(project_state.read_memory(project_id))
        )
        pending = [
            run for run in runs
            if str(run.get("id") or "") not in reconciled
        ]
        # Drain oldest-first. The effective bound never drops below the
        # project run cap, so a real backlog is fully replayed in this pass and
        # the newest status wins; older runs are never deferred to a later read.
        bound = max(limit, agent_state.MAX_RUNS)
        for run in pending[:bound]:
            project_state.promote_run(project_id, run)

        active_run_id = None
        for run_id in run_ids:
            run = data.get("runs", {}).get(run_id)
            if run and run.get("status") in ACTIVE_STATUSES:
                active_run_id = run_id

        previous_active = project.get("active_run_id")
        previous_counts = copy.deepcopy(project.get("counts") or {})
        project = _recount(project)
        project["active_run_id"] = active_run_id
        if (
            missing
            or previous_active != active_run_id
            or previous_counts != project.get("counts")
        ):
            return _save(project)
        return copy.deepcopy(project)


def get_project(project_id: str) -> dict | None:
    with project_state.project_lock():
        project = reconcile_project(project_id)
        if not project:
            return None
        runs = []
        data = agent_state.read_store()
        for run_id in project.get("run_ids") or []:
            run = data.get("runs", {}).get(run_id)
            if run:
                runs.append(agent_state.summarize_run(run))
        runs.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        result = copy.deepcopy(project)
        result["recent_runs"] = runs[:5]
        result["draft_summary"] = draft_summary(project_id)
        return result


def update_project(project_id: str, fields: dict) -> dict:
    with project_state.project_lock():
        project = _get_raw(project_id)
        if not project:
            raise KeyError(project_id)
        if "title" in fields:
            title = " ".join(str(fields["title"] or "").split())
            if not 2 <= len(title) <= 80:
                raise ValueError("项目标题需要 2–80 个字符。")
            project["title"] = title
        if "brief" in fields and isinstance(fields["brief"], dict):
            brief = {**project.get("brief", {}), **copy.deepcopy(fields["brief"])}
            project["brief"] = brief
        if "tags" in fields:
            project["tags"] = [str(item) for item in (fields["tags"] or [])][:8]
        if "stage" in fields:
            stage = str(fields["stage"])
            if stage not in project_state.PROJECT_STAGES:
                raise ValueError("未知项目阶段。")
            if stage != project.get("stage"):
                add_decision(project_id, "stage", stage, "用户手动调整阶段")
            project["stage"] = stage
        return _save(project)


def add_decision(project_id: str, kind: str, choice=None, note: str = "") -> dict:
    with project_state.project_lock():
        project = _get_raw(project_id)
        if not project:
            raise KeyError(project_id)
        memory = project_state.read_memory(project_id)
        memory["decisions"].append({
            "id": "pd-" + hashlib.sha1(
                f"{agent_state.now()}|{kind}|{choice}".encode("utf-8")
            ).hexdigest()[:10],
            "kind": kind,
            "choice": copy.deepcopy(choice),
            "note": note,
            "origin": {"run_id": "", "tool": "user"},
            "at": agent_state.now(),
        })
        project_state.trim_memory(memory)
        project_state.write_memory(project_id, memory)
        return memory


def archive_project(project_id: str) -> dict:
    with project_state.project_lock():
        project = _get_raw(project_id)
        if not project:
            raise KeyError(project_id)
        if project.get("active_run_id"):
            raise ValueError("项目仍有活动运行，不能归档。")
        project["status"] = "archived"
        return _save(project)


def restore_project(project_id: str) -> dict:
    with project_state.project_lock():
        project = _get_raw(project_id)
        if not project:
            raise KeyError(project_id)
        project["status"] = "active"
        return _save(project)


def delete_project(project_id: str, keep_runs: bool = True) -> dict:
    runs_to_remove: list[str] = []
    with project_state.project_lock():
        project = _get_raw(project_id)
        if not project:
            raise KeyError(project_id)
        if project.get("status") != "archived":
            raise ValueError("只能删除已归档项目。")
        run_ids = list(project.get("run_ids") or [])
        if keep_runs:
            data = agent_state.read_store()
            for run_id in run_ids:
                run = data.get("runs", {}).get(run_id)
                if run:
                    run["project_id"] = None
                    run["updated_at"] = agent_state.now()
            agent_state.write_store(data)
        else:
            runs_to_remove = run_ids
        data = _store()
        data["projects"].pop(project_id, None)
        project_state.write_projects(data)
        shutil.rmtree(project_state.project_dir(project_id), ignore_errors=True)
    # Run removal takes the agent-run lock, so keep it outside the project lock
    # to avoid lock-order inversion with worker saves that call on_run_saved.
    if runs_to_remove:
        from . import agent_runs
        for run_id in runs_to_remove:
            agent_runs.remove_run(run_id)
    return {"deleted": project_id, "kept_runs": len(run_ids) if keep_runs else 0}


def bind_run(project_id: str, run_id: str) -> dict:
    with project_state.project_lock():
        project = _get_raw(project_id)
        if not project:
            raise KeyError(project_id)
        if project.get("status") != "active":
            raise ValueError("项目已归档，不能启动新运行。")
        ensure_can_start_run(project_id, exclude_run_id=run_id)
        if run_id not in project["run_ids"]:
            project["run_ids"].append(run_id)
        project["active_run_id"] = run_id
        return _save(_recount(project))


def release_run(project_id: str, run_id: str) -> None:
    with project_state.project_lock():
        project = _get_raw(project_id)
        if not project:
            return
        if project.get("active_run_id") == run_id:
            project["active_run_id"] = None
        project["run_ids"] = [item for item in project.get("run_ids") or [] if item != run_id]
        _save(_recount(project))


def ensure_can_start_run(project_id: str, exclude_run_id: str | None = None) -> None:
    with project_state.project_lock():
        project = _get_raw(project_id)
        if not project:
            raise KeyError(project_id)
        active = project.get("active_run_id")
        if active and active != exclude_run_id:
            raise ValueError(f"项目已有活动运行：{active}")
        for run_id, run in agent_state.read_store().get("runs", {}).items():
            if run_id == exclude_run_id:
                continue
            if run.get("project_id") != project_id:
                continue
            if run.get("status") in ACTIVE_STATUSES:
                raise ValueError(f"项目已有活动运行：{run_id}")


def seed_for_run(project_id: str, max_papers: int) -> dict:
    with project_state.project_lock():
        project = _get_raw(project_id)
        if not project:
            raise KeyError(project_id)
        if project.get("status") != "active":
            raise ValueError("项目已归档。")
        return project_state.seed_memory(project_id, max(1, int(max_papers)))


def attach_run(project_id: str, run_id: str) -> dict:
    with project_state.project_lock():
        project = _get_raw(project_id)
        if not project:
            raise KeyError(project_id)
        if project.get("status") != "active":
            raise ValueError("项目已归档，不能关联运行。")
        data = agent_state.read_store()
        run = data.get("runs", {}).get(run_id)
        if not run:
            raise KeyError(run_id)
        if run.get("status") not in {"completed", "failed", "cancelled"}:
            raise ValueError("只能归档已完成、失败或取消的运行。")
        if run.get("project_id") and run["project_id"] != project_id:
            raise ValueError("运行已属于其他项目。")
        if run.get("status") == "failed":
            ensure_can_start_run(project_id, exclude_run_id=run_id)
        run["project_id"] = project_id
        run["updated_at"] = agent_state.now()
        agent_state.write_store(data)
        if run_id not in project["run_ids"]:
            project["run_ids"].append(run_id)
        project_state.promote_run(project_id, run)
        if run.get("status") in ACTIVE_STATUSES:
            project["active_run_id"] = run_id
        return _save(_recount(project))


def detach_run(project_id: str, run_id: str) -> dict:
    with project_state.project_lock():
        project = _get_raw(project_id)
        if not project:
            raise KeyError(project_id)
        data = agent_state.read_store()
        run = data.get("runs", {}).get(run_id)
        if run and run.get("project_id") == project_id:
            run["project_id"] = None
            run["updated_at"] = agent_state.now()
            agent_state.write_store(data)
        project["run_ids"] = [item for item in project.get("run_ids") or [] if item != run_id]
        if project.get("active_run_id") == run_id:
            project["active_run_id"] = None
        return _save(_recount(project))


def on_run_saved(run: dict) -> None:
    project_id = run.get("project_id")
    if not project_id:
        return
    with project_state.project_lock():
        project = _get_raw(project_id)
        if not project:
            return
        run_id = run["id"]
        if run_id not in project["run_ids"]:
            project["run_ids"].append(run_id)
        status = run.get("status")
        if status in ACTIVE_STATUSES:
            project["active_run_id"] = run_id
        elif project.get("active_run_id") == run_id:
            project["active_run_id"] = None
        project_state.promote_run(project_id, run)
        memory = project_state.read_memory(project_id)
        if project.get("stage") == "exploring" and (
            len(memory.get("evidence") or []) >= 3
            or len(agent_state.read_paper_ids(run)) >= 1
        ):
            project["stage"] = "reading"
        _save(_recount(project))


def advance_stage(project_id: str, target: str) -> dict:
    order = {"exploring": 0, "reading": 1, "writing": 2, "review": 3, "done": 4}
    if target not in order:
        raise ValueError("未知项目阶段。")
    with project_state.project_lock():
        project = _get_raw(project_id)
        if not project:
            raise KeyError(project_id)
        if order[target] <= order.get(project.get("stage"), 0):
            return _recount(project)
        project["stage"] = target
        return _save(_recount(project))
