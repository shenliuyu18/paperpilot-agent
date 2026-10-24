"""Subscription storage: saved searches, cursors and run results."""

from __future__ import annotations

import copy
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from .. import config
from . import project_state

FILE_NAME = "subscriptions.json"
MAX_ITEMS = 50
MAX_SEEN = 500
MAX_LABEL = 60
MAX_QUERY = 160
MAX_SOURCE = 40
HOURS = (1, 6, 24, 168, 720)

_ID_RE = re.compile(r"sub-([0-9a-f]{10})")
_SOURCES = {"demo", "arxiv", "crossref", "europepmc", "doaj", "hal", "auto"}


def _now() -> str:
    return project_state._now()


def _stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def path():
    override = os.getenv("PAPERPILOT_SUBSCRIPTIONS_FILE")
    return Path(override) if override else config.DATA_DIR / FILE_NAME


def empty() -> dict:
    return {"version": 1, "items": []}


def read() -> dict:
    file_path = path()
    if not file_path.exists():
        return empty()
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty()
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return empty()
    data.setdefault("version", 1)
    return data


def write(data: dict) -> None:
    with project_state.project_lock():
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        file_path = path()
        temp = file_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(file_path)


def _next_id(items: list[dict]) -> str:
    highest = 0
    for item in items:
        match = _ID_RE.fullmatch(str(item.get("id") or ""))
        if match:
            highest = max(highest, int(match.group(1), 16))
    return f"sub-{(highest + 1):010x}"


def _clean(payload: dict, current: dict | None = None) -> dict:
    base = current or {}
    label = " ".join(str(payload.get("label", base.get("label", "")) or "").split())
    query = " ".join(str(payload.get("query", base.get("query", "")) or "").split())
    if not query:
        raise ValueError("订阅查询不能为空。")
    if len(query) > MAX_QUERY:
        raise ValueError(f"订阅查询不能超过 {MAX_QUERY} 个字符。")
    if len(label) > MAX_LABEL:
        raise ValueError(f"订阅名称不能超过 {MAX_LABEL} 个字符。")
    source = str(payload.get("source", base.get("source", "arxiv")) or "arxiv")
    if source not in _SOURCES:
        raise ValueError(f"未知检索来源：{source}")
    raw_interval = payload.get("interval_hours", base.get("interval_hours", 168))
    interval = 168 if raw_interval is None else int(raw_interval)
    if interval not in HOURS:
        raise ValueError(f"定时间隔只能是 {HOURS} 小时。")
    raw_limit = payload.get("limit", base.get("limit", 20))
    limit = 20 if raw_limit is None else int(raw_limit)
    if not 1 <= limit <= 50:
        raise ValueError("每次抓取条数需在 1–50 之间。")
    project_id = payload.get("project_id", base.get("project_id", "")) or ""
    return {
        "label": label or query[:MAX_LABEL],
        "query": query,
        "source": source,
        "interval_hours": interval,
        "limit": limit,
        "project_id": str(project_id),
        "enabled": bool(payload.get("enabled", base.get("enabled", True))),
    }


def list_items() -> list[dict]:
    return copy.deepcopy(read().get("items") or [])


def get(subscription_id: str) -> dict | None:
    for item in read().get("items") or []:
        if item.get("id") == subscription_id:
            return copy.deepcopy(item)
    return None


def create(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("订阅格式不正确。")
    data = read()
    items = data.get("items") or []
    if len(items) >= MAX_ITEMS:
        raise ValueError(f"订阅不能超过 {MAX_ITEMS} 条。")
    clean = _clean(payload)
    now = datetime.now()
    item = {
        "id": _next_id(items),
        **clean,
        "last_run_at": "",
        "next_run_at": _stamp(now + timedelta(hours=clean["interval_hours"])),
        "seen_fingerprints": [],
        "last_error": "",
        "last_result": {},
        "created_at": _now(),
        "updated_at": _now(),
    }
    items.append(item)
    data["items"] = items
    write(data)
    return copy.deepcopy(item)


def update(subscription_id: str, payload: dict) -> dict:
    data = read()
    items = data.get("items") or []
    for index, item in enumerate(items):
        if item.get("id") != subscription_id:
            continue
        clean = _clean(payload, item)
        merged = {**item, **clean, "updated_at": _now()}
        if clean["interval_hours"] != item.get("interval_hours"):
            merged["next_run_at"] = _stamp(datetime.now() + timedelta(hours=clean["interval_hours"]))
        items[index] = merged
        data["items"] = items
        write(data)
        return copy.deepcopy(merged)
    raise KeyError(subscription_id)


def remove(subscription_id: str) -> bool:
    data = read()
    items = data.get("items") or []
    remaining = [item for item in items if item.get("id") != subscription_id]
    if len(remaining) == len(items):
        return False
    data["items"] = remaining
    write(data)
    return True


def record_run(subscription_id: str, *, fingerprints: list[str], result: dict,
               error: str = "") -> dict:
    """Persist one run: cursor, counters, error and the next due time."""
    data = read()
    items = data.get("items") or []
    for index, item in enumerate(items):
        if item.get("id") != subscription_id:
            continue
        seen = list(item.get("seen_fingerprints") or [])
        for fp in fingerprints:
            if fp and fp not in seen:
                seen.append(fp)
        item["seen_fingerprints"] = seen[-MAX_SEEN:]
        now = datetime.now()
        item["last_run_at"] = _stamp(now)
        item["next_run_at"] = _stamp(now + timedelta(hours=int(item.get("interval_hours") or 168)))
        item["last_result"] = result
        item["last_error"] = str(error or "")[:300]
        item["updated_at"] = _now()
        items[index] = item
        data["items"] = items
        write(data)
        return copy.deepcopy(item)
    raise KeyError(subscription_id)
