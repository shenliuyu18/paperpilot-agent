from __future__ import annotations

import copy
import queue
import shutil
import threading

from .. import config
from . import agent_actions, agent_controls, agent_policy, agent_state, agent_stream, project_service, project_state

TOOL_DEFS = agent_state.TOOL_DEFS
STEP_DEFS = [{"id": key, **value} for key, value in agent_state.TOOL_DEFS.items()]
STEP_IDS = list(agent_state.TOOL_DEFS)
DEFAULT_BUDGET = agent_state.DEFAULT_BUDGET
MAX_RUNS = agent_state.MAX_RUNS

# Alias of the single shared store lock. Run-store and project-store
# read-modify-write cycles serialize on the same reentrant lock, so nesting
# (e.g. create_run -> bind_run, or _prune -> release_run) cannot deadlock.
_LOCK = agent_state.STORE_LOCK
_QUEUE: queue.Queue[str] = queue.Queue()
_WORKER: threading.Thread | None = None
_WORKER_LOCK = threading.Lock()
_RECOVERED: set[str] = set()


def _read() -> dict:
    return agent_state.read_store()


def _write(data: dict) -> None:
    agent_state.write_store(data)


def _run_dir(run_id: str):
    return agent_state.run_dir(run_id)


def _save_run(run: dict) -> None:
    """Persist a worker's run without clobbering the control channel.

    The worker holds an in-memory copy for the whole action, so a control
    written by ``control()`` meanwhile only exists in the store. Under the
    shared lock we merge that stored channel back in and derive the request
    flags from pending controls before writing.
    """
    run["updated_at"] = agent_state.now()
    with _LOCK:
        data = _read()
        stored = data.setdefault("runs", {}).get(run["id"]) or {}
        if stored:
            agent_controls.merge_steering(run, stored)
            agent_controls.recompute_requests(run)
            _merge_events(run, stored)
            run["stream_seq"] = max(
                int(stored.get("stream_seq") or 0),
                int(run.get("stream_seq") or 0),
            ) + 1
        else:
            run["stream_seq"] = int(run.get("stream_seq") or 0) + 1
        data["runs"][run["id"]] = copy.deepcopy(run)
        _prune(data)
        _write(data)
    project_service.on_run_saved(run)
    agent_stream.publish(run["id"], agent_stream.compact_run(run))


def _prune(data: dict) -> None:
    runs = data.setdefault("runs", {})
    if len(runs) <= MAX_RUNS:
        return
    project_ids = set()
    for project in project_state.read_projects()["projects"].values():
        if project.get("status") == "active":
            project_ids.update(project.get("run_ids") or [])
    removable = sorted(
        (
            run for run in runs.values()
            if run.get("status") in {"completed", "failed", "cancelled"}
        ),
        key=lambda run: (run.get("id") in project_ids, run.get("updated_at", "")),
    )
    for run in removable:
        if len(runs) <= MAX_RUNS:
            break
        runs.pop(run["id"], None)
        shutil.rmtree(_run_dir(run["id"]), ignore_errors=True)
        if run.get("project_id"):
            project_service.release_run(run["project_id"], run["id"])


def _release_binding_quietly(project_id: str, run_id: str) -> None:
    # Best-effort compensation for a failed create_run. If this write also
    # fails, the project keeps a run id with no store entry and
    # reconcile_project removes it on the next project read.
    try:
        project_service.release_run(project_id, run_id)
    except Exception:
        pass


def _recover_data(data: dict) -> bool:
    changed = False
    for run in data.setdefault("runs", {}).values():
        run, normalized = agent_state.normalize_run(run)
        changed = changed or normalized
        run_id = run.get("id")
        if not run_id or run_id in _RECOVERED:
            continue
        if run.get("status") in {"running", "queued"}:
            run["status"] = "interrupted"
            run["plan"] = None
            for step in run.get("steps", []):
                if step.get("status") == "running":
                    step["status"] = "failed"
                    step["error"] = "进程中断"
            agent_state.add_event(run, "interrupted", "检测到上次进程中断，可从当前状态恢复")
            run["updated_at"] = agent_state.now()
            # A run that died with an active status must not leave the project
            # pointing at it, or the project can never start another run.
            project_service.on_run_saved(run)
            changed = True
        _RECOVERED.add(run_id)
    return changed


def _normalized_store() -> dict:
    with _LOCK:
        data = _read()
        if _recover_data(data):
            _write(data)
    return data


def _ensure_worker() -> None:
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        _WORKER = threading.Thread(target=_worker_loop, name="paperpilot-agent", daemon=True)
        _WORKER.start()


def _worker_loop() -> None:
    while True:
        run_id = _QUEUE.get()
        try:
            execute_run(run_id)
        except Exception:
            pass
        finally:
            _QUEUE.task_done()


def _enqueue(run_id: str) -> None:
    _ensure_worker()
    _QUEUE.put(run_id)


def create_run(
    goal: str,
    source: str = "demo",
    mode: str = "checkpoint",
    max_actions: int = 14,
    max_seconds: int = 300,
    max_papers: int = 6,
    max_llm_calls: int = 4,
    paper_ids: list[str] | None = None,
    autostart: bool = True,
    checkpoints: list[str] | None = None,
    project_id: str | None = None,
    seed_memory: bool = True,
) -> dict:
    if not 1 <= int(max_actions) <= 30:
        raise ValueError("动作预算需要在 1–30 之间。")
    if not 30 <= int(max_seconds) <= 1800:
        raise ValueError("时间预算需要在 30–1800 秒之间。")
    if not 2 <= int(max_papers) <= 10:
        raise ValueError("论文数量需要在 2–10 之间。")
    if not 0 <= int(max_llm_calls) <= 20:
        raise ValueError("LLM 调用预算需要在 0–20 之间。")
    budget = {
        "max_actions": int(max_actions),
        "max_seconds": int(max_seconds),
        "max_papers": int(max_papers),
        "max_llm_calls": int(max_llm_calls),
    }
    seed = None
    # Hold the shared store lock across the whole project check/seed/create/
    # bind/write sequence so two concurrent starts for the same project cannot
    # both pass the pre-check. The project is bound before the run store is
    # written, so a bind failure never leaves a run record pointing at a
    # project that does not reference it.
    with agent_state.store_lock():
        if project_id:
            project_service.ensure_can_start_run(project_id)
            if seed_memory:
                seed = project_service.seed_for_run(project_id, budget["max_papers"])
        data = _read()
        run = agent_state.create_run_state(
            goal, source, mode, budget, paper_ids,
            project_id=project_id, seed=seed,
        )
        seeded = bool(seed and seed.get("papers"))
        if seeded:
            agent_actions.save_papers(run, seed["papers"])
        while run["id"] in data.get("runs", {}):
            run = agent_state.create_run_state(
                goal, source, mode, budget, paper_ids,
                project_id=project_id, seed=seed,
            )
        if paper_ids:
            run["paper_ids"] = list(dict.fromkeys(paper_ids))[:budget["max_papers"]]
        run["plan"] = agent_policy.choose_next(run)
        if mode == "step":
            run["status"] = "awaiting_approval"
            agent_state.add_event(run, "checkpoint", "逐步模式：等待执行第一个动作")
        data.setdefault("runs", {})[run["id"]] = run
        _RECOVERED.add(run["id"])
        try:
            if project_id:
                project_service.bind_run(project_id, run["id"])
            _prune(data)
            _write(data)
        except BaseException:
            # Roll back the binding if it landed, and drop the seed snapshot so
            # a failed start leaves no run directory behind. If the rollback
            # itself fails, reconcile_project clears the dangling run id.
            if project_id:
                _release_binding_quietly(project_id, run["id"])
            if seeded:
                shutil.rmtree(_run_dir(run["id"]), ignore_errors=True)
            raise
        result = copy.deepcopy(run)
    if autostart and mode != "step":
        _enqueue(run["id"])
    return result


def list_runs(limit: int = 20) -> list[dict]:
    data = _normalized_store()
    runs = sorted(
        data.get("runs", {}).values(),
        key=lambda run: run.get("updated_at", ""),
        reverse=True,
    )
    return [agent_state.summarize_run(run) for run in runs[: max(1, min(int(limit), 50))]]


def get_run(run_id: str, include_artifacts: bool = True) -> dict | None:
    data = _normalized_store()
    run = data.get("runs", {}).get(run_id)
    if not run:
        return None
    result = copy.deepcopy(run)
    if not include_artifacts:
        for step in result.get("steps", []):
            step["artifact"] = {}
    return result


def _topic_questions(run: dict) -> list[dict]:
    return (agent_state.artifact_by_tool(run, "topic").get("questions") or [])


def act(
    run_id: str,
    action: str,
    step_id: str | None = None,
    paper_ids: list[str] | None = None,
    topic_id: str | None = None,
    constraint: str | None = None,
    autostart: bool = True,
) -> dict:
    action = (action or "").strip().lower()
    if action not in {"approve", "retry", "skip", "cancel", "resume", "stop"}:
        raise ValueError(f"未知任务动作：{action}")
    with _LOCK:
        data = _read()
        if _recover_data(data):
            # Persist the recovery before any branch can raise, so an
            # interrupted run is not lost together with the rejected action.
            _write(data)
        run = data.get("runs", {}).get(run_id)
        if not run:
            raise KeyError(run_id)
        if action == "cancel":
            if run.get("status") != "cancelled":
                run["status"] = "cancelled"
                run["plan"] = None
                agent_state.add_event(run, "cancelled", "任务已取消")
                _save_run(run)
            return copy.deepcopy(run)

        if action == "stop":
            if run.get("status") in {"completed", "cancelled"}:
                raise ValueError("任务已经结束。")
            run["force_report"] = True
            run["plan"] = {
                "tool": "report",
                "title": "停止探索并生成简报",
                "rationale": "用户要求停止自主探索，按当前证据边界收束。",
                "input": {},
                "requires_approval": False,
            }
            run["status"] = "queued"
            agent_state.add_decision(run, "stop", "force_report", "用户停止探索")
            agent_state.add_event(run, "stop", "用户要求停止探索并生成简报")
            _save_run(run)
            if autostart:
                _enqueue(run_id)
            return copy.deepcopy(run)

        if action == "approve":
            if run.get("status") != "awaiting_approval":
                raise ValueError("当前任务不在等待确认状态。")
            plan = run.get("plan") or agent_policy.choose_next(run)
            if not plan:
                raise ValueError("没有可执行的下一步。")
            if paper_ids:
                cleaned = list(dict.fromkeys(pid for pid in paper_ids if pid))
                if not cleaned:
                    raise ValueError("至少选择一篇论文。")
                agent_actions.select_papers(run, cleaned)
                agent_state.add_decision(run, "papers", cleaned, "检索检查点改选")
            if topic_id:
                question = next(
                    (item for item in _topic_questions(run) if str(item.get("id")) == str(topic_id)),
                    None,
                )
                if not question:
                    raise ValueError("候选方向不存在。")
                run["selected_topic"] = {"id": question.get("id"), "title": question.get("title")}
                agent_state.add_decision(run, "topic", topic_id, "选题检查点")
            if constraint:
                run["constraint"] = str(constraint)[:300]
                agent_state.add_decision(run, "constraint", run["constraint"], "补充约束")
            run["plan"] = agent_policy.choose_next(run)
            if not run["plan"]:
                run["status"] = "completed"
                run["updated_at"] = agent_state.now()
                agent_state.add_event(run, "completed", "任务已完成")
                _save_run(run)
                return copy.deepcopy(run)
            run["status"] = "queued"
            agent_state.add_event(run, "approved", f"已确认「{plan.get('title')}」，继续执行")
            _save_run(run)
            if autostart:
                _enqueue(run_id)
            return copy.deepcopy(run)

        if action == "resume":
            if run.get("status") == "awaiting_approval":
                return act(run_id, "approve", paper_ids=paper_ids, topic_id=topic_id,
                           constraint=constraint, autostart=autostart)
            if run.get("status") not in {"failed", "interrupted", "queued"}:
                raise ValueError("当前任务不需要恢复。")
            if run.get("project_id"):
                project_service.ensure_can_start_run(
                    run["project_id"], exclude_run_id=run_id
                )
            for step in run.get("steps", []):
                if step.get("status") in {"running", "failed"}:
                    step["status"] = "skipped"
                    step["error"] = step.get("error") or "恢复时跳过未完成动作"
            run["plan"] = None
            run["status"] = "queued"
            # Stale requests from a crashed run must not pause the resumed run.
            agent_controls.settle_pending(
                run, {"pause", "replan"}, note="恢复时忽略过期控制"
            )
            run["pause_requested"] = False
            run["replan_requested"] = False
            agent_state.add_event(run, "resumed", "任务已恢复，Planner 将重新规划")
            _save_run(run)
            if autostart:
                _enqueue(run_id)
            return copy.deepcopy(run)

        if action == "retry":
            if run.get("status") not in {"failed", "interrupted"}:
                raise ValueError("只有失败或中断的任务可以重试。")
            if run.get("project_id"):
                project_service.ensure_can_start_run(
                    run["project_id"], exclude_run_id=run_id
                )
            for step in run.get("steps", []):
                if step.get("status") in {"failed", "running"}:
                    step["status"] = "skipped"
            run["plan"] = None
            run["status"] = "queued"
            agent_controls.settle_pending(
                run, {"pause", "replan"}, note="重试时忽略过期控制"
            )
            run["pause_requested"] = False
            run["replan_requested"] = False
            agent_state.add_event(run, "retry", "已重置失败动作，等待重新规划")
            _save_run(run)
            if autostart:
                _enqueue(run_id)
            return copy.deepcopy(run)

        if action == "skip":
            if run.get("status") != "awaiting_approval":
                raise ValueError("只有等待确认的任务可以跳过。")
            agent_state.add_decision(run, "skip", (run.get("plan") or {}).get("tool"), "用户跳过当前计划")
            run["force_report"] = True
            run["plan"] = {
                "tool": "report",
                "title": "跳过当前动作并收束",
                "rationale": "用户跳过当前计划，按已有证据生成简报。",
                "input": {},
                "requires_approval": False,
            }
            run["status"] = "queued"
            agent_state.add_event(run, "skip", "已跳过当前计划，准备收束")
            _save_run(run)
            if autostart:
                _enqueue(run_id)
            return copy.deepcopy(run)

        raise ValueError(f"未知任务动作：{action}")


def control(run_id: str, action: str, **payload) -> dict:
    action = str(action or "").strip()
    if action not in agent_controls.CONTROL_ACTIONS:
        raise ValueError(f"未知控制指令：{action}")
    with _LOCK:
        data = _read()
        if _recover_data(data):
            # Persist the recovery before any validation error raises below.
            _write(data)
        run = data.get("runs", {}).get(run_id)
        if not run:
            raise KeyError(run_id)
        status = run.get("status")
        if status in {"completed", "cancelled", "failed", "interrupted"}:
            raise ValueError("任务已经结束。")
        if action == "pause":
            agent_controls.create_control(run, "pause")
            if status == "queued":
                run["status"] = "paused"
                # The queued pause is consumed immediately; steering keeps the
                # entry so the UI can still show what happened.
                agent_controls.settle_pending(run, {"pause"})
                agent_state.add_event(run, "paused", "排队任务已暂停")
            elif status == "running":
                agent_state.add_event(run, "pause_requested", "将在当前动作结束后暂停")
            else:
                raise ValueError("只有排队或运行中的任务可以暂停。")
        elif action == "resume":
            if status != "paused":
                raise ValueError("只有已暂停的任务可以恢复。")
            if run.get("project_id"):
                project_service.ensure_can_start_run(
                    run["project_id"], exclude_run_id=run_id
                )
            run["status"] = "queued"
            agent_controls.settle_pending(
                run, {"pause"}, note="恢复时解除暂停请求"
            )
            agent_state.add_event(run, "resumed", "任务已恢复")
        elif action == "replan":
            agent_controls.create_control(run, "replan")
            if status == "running":
                agent_state.add_event(run, "replan_requested", "将在当前动作结束后重新规划")
            elif status in {"paused", "awaiting_approval"}:
                run["plan"] = agent_policy.choose_next(run)
                agent_controls.settle_pending(run, {"replan"})
                agent_state.add_event(run, "replanned", "已重新规划下一步")
            else:
                raise ValueError("当前状态不能重新规划。")
        else:
            created = agent_controls.create_control(run, action, **payload)
            if status != "running":
                applied = agent_controls.apply_pending(run)
                if created.get("status") == "rejected":
                    # Immediate validation failures are request errors, not
                    # queued steering; the run is left untouched.
                    raise ValueError(created.get("note") or f"控制指令无效：{action}")
                if applied["paused"] and status == "queued":
                    run["status"] = "paused"
                    agent_state.add_event(run, "paused", "排队任务已暂停")
                if applied["applied"] or applied["replanned"]:
                    run["plan"] = None
            agent_state.add_event(run, "control", f"已接收控制：{action}")
        agent_controls.recompute_requests(run)
        run["updated_at"] = agent_state.now()
        run["stream_seq"] = int(run.get("stream_seq") or 0) + 1
        _write(data)
        project_service.on_run_saved(run)
        agent_stream.publish(run["id"], agent_stream.compact_run(run))
        result = copy.deepcopy(run)
    if action == "resume":
        _enqueue(run_id)
    return result


def remove_run(run_id: str) -> bool:
    with _LOCK:
        data = _read()
        run = data.get("runs", {}).get(run_id)
        if not run:
            return False
        project_id = run.get("project_id")
        data["runs"].pop(run_id, None)
        _write(data)
    shutil.rmtree(_run_dir(run_id), ignore_errors=True)
    if project_id:
        project_service.release_run(project_id, run_id)
    return True


def _merge_events(run: dict, stored: dict) -> None:
    """Keep events written by control() while this worker was mid-action."""
    merged = list(stored.get("events") or [])
    seen = {
        (event.get("at"), event.get("type"), event.get("message"))
        for event in merged
    }
    for event in run.get("events") or []:
        key = (event.get("at"), event.get("type"), event.get("message"))
        if key not in seen:
            merged.append(event)
            seen.add(key)
    run["events"] = merged[-120:]


def _apply_controls(run: dict) -> dict:
    """Apply controls at a worker boundary.

    Only the control channel (steering, request flags and events) is refreshed
    from the store: ``run`` also carries the action artifact produced since the
    last save, which a bare store reload would lose. Applied non-pause results
    are persisted immediately so a concurrent control write is never
    overwritten by the run's later save.
    """
    with _LOCK:
        stored = _read().get("runs", {}).get(run.get("id")) or {}
        if stored:
            run["steering"] = copy.deepcopy(stored.get("steering") or [])
            run["pause_requested"] = bool(stored.get("pause_requested"))
            run["replan_requested"] = bool(stored.get("replan_requested"))
            _merge_events(run, stored)
        result = agent_controls.apply_pending(run)
        if not result["paused"] and (
            result["applied"] or result["rejected"] or result["replanned"]
        ):
            _save_run(run)
    return result


def execute_run(run_id: str) -> dict:
    # Hold the shared lock across the state check and transition so a pause
    # submitted for a queued run cannot be overwritten by "running".
    with _LOCK:
        run = get_run(run_id)
        if not run:
            raise KeyError(run_id)
        if run.get("status") in {"completed", "cancelled", "awaiting_approval", "paused"}:
            return run
        _RECOVERED.add(run_id)
        control_result = agent_controls.apply_pending(run)
        if control_result["paused"]:
            run["status"] = "paused"
            # Nothing has run in this pass, so the stored plan is only a
            # leftover; drop it so resume recomputes the next action.
            run["plan"] = None
            agent_state.add_event(run, "paused", "收到暂停请求，任务已暂停")
            _save_run(run)
            return copy.deepcopy(run)
        if control_result["applied"] or control_result["replanned"]:
            run["plan"] = None
        run["status"] = "running"
        _save_run(run)
    while True:
        run = get_run(run_id)
        if not run:
            raise KeyError(run_id)
        if run.get("status") in {"completed", "cancelled", "awaiting_approval", "paused"}:
            return run
        plan = run.get("plan") or agent_policy.choose_next(run)
        if run.get("force_report"):
            plan = {
                "tool": "report",
                "title": "停止探索并生成简报",
                "rationale": "用户要求停止自主探索，按当前证据边界收束。",
                "input": {},
                "requires_approval": False,
            }
        if plan is None:
            run["status"] = "completed"
            run["plan"] = None
            run["updated_at"] = agent_state.now()
            agent_state.add_event(run, "completed", "任务已完成")
            _save_run(run)
            return copy.deepcopy(run)
        action_id = agent_state.new_step_id(run)
        step = {
            "id": action_id,
            "tool": plan.get("tool"),
            "title": plan.get("title") or plan.get("tool"),
            "status": "running",
            "rationale": plan.get("rationale") or "",
            "input": copy.deepcopy(plan.get("input") or {}),
            "artifact": {},
            "observation": {},
            "started_at": agent_state.now(),
            "finished_at": "",
            "attempts": 1,
            "checkpoint": bool(plan.get("requires_approval")),
            "decision": None,
            "error": "",
        }
        run.setdefault("steps", []).append(step)
        run["plan"] = plan
        run["status"] = "running"
        if plan.get("tool") != "report":
            # The report is the wrap-up artifact, not an exploratory action; charging
            # it would display 15/14 when the budget is spent on exploration.
            agent_state.spend_action(run)
        _save_run(run)
        try:
            artifact = agent_actions.execute_action(run, plan, action_id)
            if not isinstance(artifact, dict):
                artifact = {}
        except Exception as exc:
            step["status"] = "failed"
            step["finished_at"] = agent_state.now()
            step["error"] = str(exc)[:500]
            run["status"] = "failed"
            run["plan"] = None
            agent_state.add_event(run, "failed", f"「{step['title']}」失败：{step['error']}")
            _save_run(run)
            return copy.deepcopy(run)
        step["status"] = "done"
        step["finished_at"] = agent_state.now()
        step["artifact"] = artifact
        if (
            config.LLM_ENABLED
            and agent_state.llm_budget_remaining(run) <= 0
            and not any(event.get("type") == "budget" for event in run.get("events") or [])
        ):
            # The action budget still allows more steps, so say why the remaining
            # ones come back as offline rule results.
            agent_state.add_event(
                run, "budget", "模型调用预算已用尽，后续动作只使用离线规则结果。"
            )
        observation = agent_policy.critique(run, step)
        step["observation"] = observation
        run["updated_at"] = agent_state.now()
        if step.get("tool") == "report":
            if artifact.get("path"):
                run["report"] = {
                    "path": artifact.get("path"),
                    "chars": artifact.get("chars", 0),
                    "mode": artifact.get("mode", "offline"),
                    "generated_at": agent_state.now(),
                    "budget": agent_state.budget_snapshot(run),
                }
            run["status"] = "completed"
            run["plan"] = None
            agent_state.add_event(run, "completed", "研究简报已生成，任务完成")
            _save_run(run)
            return copy.deepcopy(run)
        agent_state.add_event(run, "step", f"完成「{step['title']}」")
        control_result = _apply_controls(run)
        if control_result["paused"]:
            run["status"] = "paused"
            # run["plan"] still points at the action that just completed;
            # clear it so resume replans instead of replaying that action.
            run["plan"] = None
            agent_state.add_event(run, "paused", "当前动作结束，任务已暂停")
            _save_run(run)
            return copy.deepcopy(run)
        if control_result["replanned"] or control_result["applied"]:
            run["plan"] = None
        next_plan = agent_policy.choose_next(run)
        run["plan"] = next_plan
        if run.get("mode") == "step":
            run["status"] = "awaiting_approval"
            agent_state.add_event(run, "checkpoint", "逐步模式：等待执行下一个动作")
            _save_run(run)
            return copy.deepcopy(run)
        if run.get("mode") == "checkpoint" and (
            observation.get("next_hint") == "human"
            or (next_plan and next_plan.get("requires_approval"))
        ):
            run["status"] = "awaiting_approval"
            agent_state.add_event(run, "checkpoint", f"等待确认下一步：{next_plan.get('title') if next_plan else '收束'}")
            _save_run(run)
            return copy.deepcopy(run)
        if run.get("mode") == "auto" and observation.get("next_hint") == "human":
            run["status"] = "awaiting_approval"
            agent_state.add_event(run, "checkpoint", "Critic 请求人工确认")
            _save_run(run)
            return copy.deepcopy(run)
        if next_plan is None:
            run["status"] = "completed"
            run["plan"] = None
            agent_state.add_event(run, "completed", "任务已完成")
            _save_run(run)
            return copy.deepcopy(run)
        _save_run(run)


def report_markdown(run_id: str) -> str | None:
    run = get_run(run_id)
    if not run:
        return None
    return agent_actions.report_markdown(run)


def _save_papers(run: dict, papers: list[dict]) -> list[dict]:
    return agent_actions.save_papers(run, papers)


def _papers_for_run(run: dict) -> list[dict]:
    return agent_actions.papers_for_run(run)


def build_report(run: dict) -> tuple[str, str]:
    return agent_actions.build_report(run)
