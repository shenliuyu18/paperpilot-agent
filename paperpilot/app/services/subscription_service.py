"""Subscription execution: fetch, de-duplicate, classify, and (optionally) schedule.

Classification is deliberately conservative and rule-based:

``conflict``   the new paper and a project paper report the same metric in opposite directions
``support``    same metric, same direction
``method``     strong overlap on method/title wording without a metric conflict
``irrelevant`` none of the above

``conflict`` items are appended to the project's ``contradictions.json`` as advisory
records so the draft audit can point at them. Nothing is added to the project and no
run starts automatically — the user decides what enters the project.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

from .. import config
from . import (
    contradictions,
    grounding,
    paper_index,
    project_state,
    search,
    subscription_state,
)

_SCHEDULER: dict = {"thread": None, "stop": None, "last_tick": "", "last_error": ""}
_RUNNING: set[str] = set()
_RUNNING_LOCK = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
MAX_PER_TICK = 3
TICK_SECONDS = 60
LOCK_TTL_SECONDS = 30 * 60


def _lock_for(subscription_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(subscription_id, threading.Lock())


def _lock_dir() -> Path:
    override = (os.getenv("PAPERPILOT_LOCK_DIR") or "").strip()
    return Path(override) if override else config.DATA_DIR / "locks"


def _lock_path(subscription_id: str) -> Path:
    safe = "".join(ch for ch in subscription_id if ch.isalnum() or ch in "-_") or "unknown"
    return _lock_dir() / f"{safe}.lock"


def _acquire_file_lock(subscription_id: str) -> str:
    """Create an exclusive lock file. Returns a token, or "" when it is held.

    ``O_CREAT | O_EXCL`` is atomic on Windows and POSIX, so two processes (or two
    workers) cannot both enter. A lock older than ``LOCK_TTL_SECONDS`` is treated
    as a crashed holder and reclaimed.
    """
    path = _lock_path(subscription_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    payload = json.dumps({"pid": os.getpid(), "at": time.time(), "token": token})
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except OSError:
                continue
            if age <= LOCK_TTL_SECONDS:
                return ""
            try:
                path.unlink()
            except OSError:
                return ""
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        return token
    return ""


def _release_file_lock(subscription_id: str, token: str) -> None:
    if not token:
        return
    path = _lock_path(subscription_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if data.get("token") != token:
        return
    try:
        path.unlink()
    except OSError:
        pass


def _file_lock_held(subscription_id: str) -> bool:
    path = _lock_path(subscription_id)
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    if age <= LOCK_TTL_SECONDS:
        return True
    try:
        path.unlink()
    except OSError:
        pass
    return False


def running() -> list[str]:
    with _RUNNING_LOCK:
        active = set(_RUNNING)
    for item in subscription_state.list_items():
        subscription_id = str(item.get("id") or "")
        if subscription_id and _file_lock_held(subscription_id):
            active.add(subscription_id)
    return sorted(active)


def _project_papers(project_id: str) -> list[dict]:
    if not project_id:
        return []
    memory = project_state.read_memory(project_id)
    return [paper for paper in (memory.get("papers") or {}).values() if isinstance(paper, dict)]


def _metric_rows(paper: dict) -> dict:
    return contradictions._metric_rows(paper)


def _method_overlap(left: dict, right: dict) -> float:
    def blob(paper: dict) -> str:
        return " ".join(str(paper.get(key) or "") for key in ("method", "title"))
    return grounding.overlap(blob(left), blob(right))


def classify(paper: dict, project_papers: list[dict]) -> tuple[str, str]:
    """Return ``(label, reason)`` for one new paper against the project's papers."""
    for other in project_papers:
        conflicts = contradictions._compare_pair(paper, other)
        if conflicts:
            item = conflicts[0]
            return "conflict", f"与《{other.get('title') or other.get('id')}》在「{item['metric']}」上方向相反"
    left_rows = _metric_rows(paper)
    best_overlap = 0.0
    for other in project_papers:
        right_rows = _metric_rows(other)
        shared = set(left_rows) & set(right_rows)
        for key in shared:
            a, b = left_rows[key], right_rows[key]
            if a["direction"] and a["direction"] == b["direction"]:
                return "support", f"与《{other.get('title') or other.get('id')}》在同名指标上方向一致"
        best_overlap = max(best_overlap, _method_overlap(paper, other))
    if best_overlap >= 0.25:
        return "method", "方法描述与项目内论文高度重合"
    if not project_papers:
        return "support", "项目内还没有论文，作为首批证据"
    return "irrelevant", "与项目当前证据无直接关系"


def _append_conflicts(project_id: str, items: list[dict]) -> int:
    if not project_id or not items:
        return 0
    stored = contradictions.read(project_id)
    existing = {
        (item.get("paper_a", {}).get("id"), item.get("paper_b", {}).get("id"), item.get("metric"))
        for item in stored.get("items") or []
    }
    merged = list(stored.get("items") or [])
    added = 0
    for item in items:
        key = (item.get("paper_a", {}).get("id"), item.get("paper_b", {}).get("id"), item.get("metric"))
        if key in existing:
            continue
        item = {**item, "id": f"ctr-{len(merged) + 1:03d}", "status": "open", "source": "subscription"}
        merged.append(item)
        existing.add(key)
        added += 1
    if not added:
        return 0
    payload = {
        "version": 1,
        "project_id": project_id,
        "generated_at": project_state._now(),
        "papers": stored.get("papers") or [],
        "stats": {"papers": len(stored.get("papers") or []), "items": len(merged),
                  "high": sum(1 for item in merged if item.get("severity") == "high")},
        "items": merged,
    }
    path = project_state.project_dir(project_id) / contradictions.FILE_NAME
    with project_state.project_lock():
        temp = path.with_suffix(".json.tmp")
        temp.write_text(__import__("json").dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
    return added


def run_once(subscription_id: str, *, block: bool = False) -> dict:
    """Fetch once; concurrent callers for the same id get ``{"skipped": "busy"}``.

    Two guards cooperate: a per-id ``threading.Lock`` for same-process callers and
    an exclusive lock file for other processes/workers. The file lock is reclaimed
    after ``LOCK_TTL_SECONDS`` so a crashed holder cannot block forever.
    """
    lock = _lock_for(subscription_id)
    if not lock.acquire(blocking=block):
        return {"skipped": "busy", "subscription_id": subscription_id}
    token = ""
    try:
        token = _acquire_file_lock(subscription_id)
        if not token:
            return {"skipped": "busy", "subscription_id": subscription_id}
        with _RUNNING_LOCK:
            _RUNNING.add(subscription_id)
        return _run_once_locked(subscription_id)
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard(subscription_id)
        _release_file_lock(subscription_id, token)
        lock.release()


def _run_once_locked(subscription_id: str) -> dict:
    """Fetch once, update the index/cursor and classify the new papers."""
    subscription = subscription_state.get(subscription_id)
    if not subscription:
        raise KeyError(subscription_id)
    project_id = str(subscription.get("project_id") or "")
    try:
        papers = search.search(
            str(subscription.get("query") or ""),
            limit=int(subscription.get("limit") or 20),
            source=str(subscription.get("source") or "arxiv"),
        )
    except Exception as exc:  # network/transport failure is recorded, not raised
        subscription_state.record_run(subscription_id, fingerprints=[], result={}, error=repr(exc)[:300])
        raise

    sync_result = paper_index.sync(
        papers, project_id=project_id, subscription_id=subscription_id
    )
    seen = set(subscription.get("seen_fingerprints") or [])
    project_papers = _project_papers(project_id)
    by_id = {str(paper.get("id")): paper for paper in papers}
    counter = {"conflict": 0, "support": 0, "method": 0, "irrelevant": 0}
    items, conflicts = [], []
    for added in sync_result["added"]:
        fingerprint = added.get("fingerprint") or ""
        if fingerprint in seen:
            continue
        paper = by_id.get(added["paper_id"]) or next(
            (p for p in papers if paper_index.fingerprint(p) == fingerprint), {}
        )
        if not paper:
            continue
        label, reason = classify(paper, project_papers)
        counter[label] += 1
        entry = {
            "paper_id": added["paper_id"], "title": added.get("title") or paper.get("title"),
            "year": paper.get("year"), "label": label, "reason": reason,
            "fingerprint": fingerprint,
        }
        items.append(entry)
        if label == "conflict":
            for other in project_papers:
                pair = contradictions._compare_pair(paper, other)
                if pair:
                    conflicts.append(pair[0])
                    break
    appended = _append_conflicts(project_id, conflicts)
    result = {
        "new": len(items),
        "conflict": counter["conflict"],
        "support": counter["support"],
        "method": counter["method"],
        "irrelevant": counter["irrelevant"],
        "contradictions_added": appended,
        "at": project_state._now(),
    }
    updated = subscription_state.record_run(
        subscription_id,
        fingerprints=[paper_index.fingerprint(paper) for paper in papers],
        result=result,
    )
    return {"subscription": updated, "items": items, "result": result,
            "index": {"added": len(sync_result["added"]), "updated": len(sync_result["updated"]),
                      "total": sync_result["total"]}}


def due(now: str | None = None) -> list[dict]:
    stamp = now or project_state._now()
    active = set(running())
    return [
        item for item in subscription_state.list_items()
        if item.get("enabled") and str(item.get("next_run_at") or "") <= stamp
        and item.get("id") not in active
    ]


def _loop(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            pending = due()[:MAX_PER_TICK]
            for item in pending:
                if stop.is_set():
                    break
                try:
                    run_once(item["id"])
                except Exception as exc:  # one bad subscription must not stall the rest
                    _SCHEDULER["last_error"] = repr(exc)[:300]
            _SCHEDULER["last_tick"] = project_state._now()
        except Exception as exc:  # keep the loop alive; the error is visible via status()
            _SCHEDULER["last_error"] = repr(exc)[:300]
        stop.wait(TICK_SECONDS)


def scheduler_enabled() -> bool:
    return os.getenv("PAPERPILOT_SCHEDULER", "0").strip().lower() in {"1", "true", "yes", "on"}


def start_scheduler() -> bool:
    """Start the single background thread once. Disabled unless PAPERPILOT_SCHEDULER=1."""
    if not scheduler_enabled():
        return False
    if _SCHEDULER["thread"] and _SCHEDULER["thread"].is_alive():
        return False
    stop = threading.Event()
    thread = threading.Thread(target=_loop, args=(stop,), name="pp-subscriptions", daemon=True)
    _SCHEDULER.update({"thread": thread, "stop": stop})
    thread.start()
    return True


def stop_scheduler() -> None:
    stop = _SCHEDULER.get("stop")
    if stop:
        stop.set()
    _SCHEDULER["thread"] = None
    _SCHEDULER["stop"] = None


def scheduler_status() -> dict:
    thread = _SCHEDULER.get("thread")
    return {
        "enabled": scheduler_enabled(),
        "running": bool(thread and thread.is_alive()),
        "last_tick": _SCHEDULER.get("last_tick") or "",
        "last_error": _SCHEDULER.get("last_error") or "",
        "running_ids": running(),
        "tick_seconds": TICK_SECONDS,
    }
