from __future__ import annotations

import copy
import re

from . import agent_state

CONTROL_ACTIONS = {
    "pause",
    "resume",
    "replan",
    "set_mode",
    "add_constraint",
    "add_papers",
    "inject_question",
}
_MODES = {"auto", "checkpoint", "step"}
_MAX_STEERING = 50
_FINAL_STATUSES = {"applied", "rejected"}


def _next_control_id(run: dict) -> str:
    highest = 0
    for item in run.get("steering") or []:
        match = re.fullmatch(r"ctl-(\d+)", str(item.get("id") or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"ctl-{highest + 1:03d}"


def create_control(run: dict, action: str, **payload) -> dict:
    action = str(action or "").strip()
    if action not in CONTROL_ACTIONS:
        raise ValueError(f"未知控制指令：{action}")
    control = {
        "id": _next_control_id(run),
        "kind": action,
        "payload": copy.deepcopy(payload),
        "status": "pending",
        "requested_at": agent_state.now(),
        "applied_at": "",
        "note": "",
    }
    run.setdefault("steering", []).append(control)
    run["steering"] = run["steering"][-_MAX_STEERING:]
    return control


def _apply_one(run: dict, control: dict) -> None:
    kind = control["kind"]
    payload = control.get("payload") or {}
    if kind == "set_mode":
        mode = str(payload.get("mode") or "")
        if mode not in _MODES:
            raise ValueError("自主程度必须是 auto、checkpoint 或 step。")
        run["mode"] = mode
        agent_state.add_decision(run, "mode", mode, "运行中切换自主程度")
    elif kind == "add_constraint":
        text = " ".join(str(payload.get("constraint") or "").split())
        if not 2 <= len(text) <= 200:
            raise ValueError("约束需要 2–200 个字符。")
        current = str(run.get("constraint") or "").strip()
        merged = f"{current}；{text}"[:600] if current else text
        run["constraint"] = merged
        agent_state.add_decision(run, "constraint", text, "运行中补充约束")
    elif kind == "add_papers":
        from . import agent_actions

        ids = [str(item) for item in (payload.get("paper_ids") or []) if str(item).strip()]
        if not ids:
            raise ValueError("至少选择一篇论文。")
        agent_actions.select_papers(run, ids)
        agent_state.add_decision(run, "papers", ids, "运行中加入论文")
    elif kind == "inject_question":
        question = " ".join(str(payload.get("question") or "").split())
        if not 2 <= len(question) <= 300:
            raise ValueError("问题需要 2–300 个字符。")
        agent_state.upsert_question(
            run,
            question,
            priority=payload.get("priority") or "medium",
            kind="evidence",
            source="user",
        )
        agent_state.add_decision(run, "question", question, "运行中注入问题")
    elif kind == "pause":
        run["pause_requested"] = True
    elif kind == "replan":
        run["replan_requested"] = True
    elif kind == "resume":
        run["pause_requested"] = False


def _mark(control: dict, status: str) -> None:
    control["status"] = status
    control["applied_at"] = agent_state.now()


def settle_pending(run: dict, kinds: set[str], note: str = "") -> int:
    """Mark pending controls of ``kinds`` as applied (superseded/consumed)."""
    settled = 0
    for control in run.get("steering") or []:
        if control.get("status") != "pending" or control.get("kind") not in kinds:
            continue
        _mark(control, "applied")
        if note:
            control["note"] = note
        settled += 1
    return settled


def recompute_requests(run: dict) -> None:
    """Derive pause/replan flags from pending controls.

    Pending controls are the source of truth, so a control written while a
    worker is mid-action stays visible and one already applied is not replayed.
    """
    controls = run.get("steering") or []
    run["pause_requested"] = any(
        control.get("kind") == "pause" and control.get("status") == "pending"
        for control in controls
    )
    run["replan_requested"] = any(
        control.get("kind") == "replan" and control.get("status") == "pending"
        for control in controls
    )


def _merge_control(local: dict, stored: dict) -> dict:
    merged = copy.deepcopy(local)
    # Applied/rejected is terminal and monotonic: a stale stored copy must not
    # downgrade work this worker already finished, but a terminal status from
    # the store must upgrade a pending local copy.
    if (
        local.get("status") not in _FINAL_STATUSES
        and stored.get("status") in _FINAL_STATUSES
    ):
        merged["status"] = stored["status"]
        merged["applied_at"] = stored.get("applied_at") or ""
        merged["note"] = stored.get("note") or ""
    return merged


def merge_steering(run: dict, stored: dict) -> None:
    """Merge the in-memory steering list with the stored control channel.

    Controls created while an action was running are appended to the worker's
    stale list, existing entries keep their local terminal status, and the
    list stays ordered and capped.
    """
    local = list(run.get("steering") or [])
    stored_controls = list(stored.get("steering") or [])
    stored_by_id = {
        control.get("id"): control
        for control in stored_controls
        if control.get("id")
    }
    local_ids = {control.get("id") for control in local if control.get("id")}
    merged = []
    for control in local:
        other = stored_by_id.get(control.get("id"))
        merged.append(_merge_control(control, other) if other else copy.deepcopy(control))
    for control in stored_controls:
        if control.get("id") and control.get("id") in local_ids:
            continue
        merged.append(copy.deepcopy(control))
    run["steering"] = merged[-_MAX_STEERING:]


def apply_pending(run: dict) -> dict:
    """Apply pending steering at a worker boundary.

    ``pause`` wins over every other instruction in the same batch: a pending
    pause is consumed first, the run reports paused, and every other pending
    instruction stays pending for the boundary after the run resumes.
    """
    if run.get("pause_requested"):
        run["pause_requested"] = False
        settle_pending(run, {"pause"})
        return {"paused": True, "replanned": False, "applied": 0, "rejected": 0}

    controls = run.get("steering") or []
    pending_pause = [
        control for control in controls
        if control.get("status") == "pending" and control.get("kind") == "pause"
    ]
    if pending_pause:
        for control in pending_pause:
            _mark(control, "applied")
        run["pause_requested"] = False
        return {"paused": True, "replanned": False, "applied": 0, "rejected": 0}

    applied = 0
    rejected = 0
    for control in controls:
        if control.get("status") != "pending":
            continue
        kind = control.get("kind")
        if kind == "resume":
            run["pause_requested"] = False
            _mark(control, "applied")
            applied += 1
            continue
        if kind == "replan":
            run["replan_requested"] = True
            _mark(control, "applied")
            continue
        try:
            _apply_one(run, control)
            _mark(control, "applied")
            applied += 1
        except Exception as exc:  # validation failures become rejection notes
            control["status"] = "rejected"
            control["note"] = str(exc)[:300]
            rejected += 1

    if run.get("pause_requested"):
        run["pause_requested"] = False
        return {"paused": True, "replanned": False, "applied": applied, "rejected": rejected}

    replanned = bool(run.get("replan_requested"))
    if replanned:
        run["replan_requested"] = False
        run["plan"] = None
        agent_state.add_decision(run, "replan", "boundary", "运行中重新规划下一步")
    return {"paused": False, "replanned": replanned, "applied": applied, "rejected": rejected}
