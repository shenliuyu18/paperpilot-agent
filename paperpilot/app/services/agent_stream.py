"""Server-Sent Events for agent runs.

Each run keeps an in-process registry of subscriber queues. Saves and controls
publish a compact snapshot of the run; browsers that hold open an SSE response
receive it. Queues are bounded (max 8) and drop the oldest snapshot first, so a
slow browser can never make the worker block.
"""
from __future__ import annotations

import copy
import json
import queue
import threading
import time
from typing import Callable, Iterator

_SUBSCRIBERS: dict[str, set[queue.Queue]] = {}
_LOCK = threading.RLock()
TERMINAL = {"completed", "cancelled", "failed", "interrupted"}
MAX_QUEUE = 8
EVIDENCE_WINDOW = 3
LIST_WINDOW = 10


def compact_run(run: dict) -> dict:
    """Build the stream snapshot: no artifacts, bounded evidence/lists."""
    steps = []
    for step in run.get("steps") or []:
        steps.append({
            "id": step.get("id"),
            "tool": step.get("tool"),
            "title": step.get("title"),
            "status": step.get("status"),
            "started_at": step.get("started_at"),
            "finished_at": step.get("finished_at"),
            "observation": copy.deepcopy(step.get("observation") or {}),
            "error": step.get("error") or "",
            "checkpoint": bool(step.get("checkpoint")),
        })
    evidence = list(run.get("evidence") or [])
    return {
        "seq": int(run.get("stream_seq") or 0),
        "run": {
            "id": run.get("id"),
            "project_id": run.get("project_id"),
            "goal": run.get("goal"),
            "mode": run.get("mode"),
            "status": run.get("status"),
            "updated_at": run.get("updated_at"),
            "plan": copy.deepcopy(run.get("plan")),
            "steps": steps,
            "evidence_count": len(evidence),
            "latest_evidence": copy.deepcopy(evidence[-EVIDENCE_WINDOW:]),
            "open_questions": copy.deepcopy(list(run.get("open_questions") or [])[-LIST_WINDOW:]),
            "decisions": copy.deepcopy(list(run.get("decisions") or [])[-LIST_WINDOW:]),
            "budget": copy.deepcopy(run.get("budget") or {}),
            "budget_used": copy.deepcopy(run.get("budget_used") or {}),
            "critic": copy.deepcopy(run.get("critic") or {}),
            "report": copy.deepcopy(run.get("report") or {}),
            "pause_requested": bool(run.get("pause_requested")),
            "replan_requested": bool(run.get("replan_requested")),
        },
    }


def subscribe(run_id: str) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=MAX_QUEUE)
    with _LOCK:
        _SUBSCRIBERS.setdefault(run_id, set()).add(q)
    return q


def unsubscribe(run_id: str, q: queue.Queue) -> None:
    with _LOCK:
        subscribers = _SUBSCRIBERS.get(run_id)
        if not subscribers:
            return
        subscribers.discard(q)
        if not subscribers:
            _SUBSCRIBERS.pop(run_id, None)


def publish(run_id: str, payload: dict) -> None:
    with _LOCK:
        subscribers = list(_SUBSCRIBERS.get(run_id) or [])
    for q in subscribers:
        if q.full():
            try:
                q.get_nowait()
            except queue.Empty:
                pass
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass


def _event(payload: dict) -> str:
    seq = payload.get("seq", 0)
    return f"id: {seq}\nevent: snapshot\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def event_stream(
    run_id: str,
    get_run: Callable[[str], dict | None],
    heartbeat: float = 15.0,
) -> Iterator[str]:
    q = subscribe(run_id)
    try:
        run = get_run(run_id)
        if not run:
            return
        yield _event(compact_run(run))
        if run.get("status") in TERMINAL:
            return
        while True:
            try:
                payload = q.get(timeout=heartbeat)
            except queue.Empty:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                yield f"event: heartbeat\ndata: {json.dumps({'at': stamp}, ensure_ascii=False)}\n\n"
                continue
            yield _event(payload)
            if (payload.get("run") or {}).get("status") in TERMINAL:
                return
    finally:
        unsubscribe(run_id, q)
