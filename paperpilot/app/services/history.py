from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from datetime import datetime
from pathlib import Path

from .. import config

_MAX_ENTRIES = 200


def _path() -> Path:
    override = os.getenv("PAPERPILOT_HISTORY_FILE")
    path = Path(override) if override else config.DATA_DIR / "search_history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load() -> list[dict]:
    path = _path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else data
    return entries if isinstance(entries, list) else []


def _save(entries: list[dict]) -> None:
    path = _path()
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps({"version": 1, "entries": entries[:_MAX_ENTRIES]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def record(
    query: str,
    source: str,
    source_label: str,
    count: int,
    layers: dict | None = None,
    titles: list[str] | None = None,
) -> dict:
    entries = _load()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "id": hashlib.sha1(f"{now}|{query}|{source}|{len(entries)}".encode("utf-8")).hexdigest()[:14],
        "at": now,
        "query": str(query)[:300],
        "source": str(source)[:120],
        "source_label": str(source_label)[:120],
        "count": int(count),
        "layers": {
            str(key): int(value)
            for key, value in (layers or {}).items()
            if key in {"直接相关", "方法参考", "背景脉络"}
        },
        "titles": [str(title)[:160] for title in (titles or [])[:5]],
    }
    entries.insert(0, entry)
    _save(entries[:_MAX_ENTRIES])
    return entry


def list_entries(limit: int = 50) -> list[dict]:
    return _load()[: max(1, min(int(limit), _MAX_ENTRIES))]


def clear() -> None:
    _save([])


def export_markdown() -> str:
    entries = _load()
    lines = [
        "# 检索留痕",
        "",
        f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · {len(entries)} 条",
        "",
        "> 记录每次检索的查询、来源、结果数与分层；可用于复现检索过程，不代表结果质量。",
        "",
    ]
    for entry in entries:
        layers = " / ".join(f"{key} {value}" for key, value in (entry.get("layers") or {}).items())
        lines.extend(
            [
                f"## {entry.get('at')} · {entry.get('query')}",
                "",
                f"- 来源：{entry.get('source_label') or entry.get('source')} (`{entry.get('source')}`)",
                f"- 结果：{entry.get('count')} 篇" + (f"；{layers}" if layers else ""),
            ]
        )
        if entry.get("titles"):
            lines.append(f"- 首批结果：{'；'.join(entry['titles'])}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def export_csv() -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["query", "source", "source_label", "count", "direct", "method", "background", "at"])
    for entry in _load():
        layers = entry.get("layers") or {}
        writer.writerow(
            [
                entry.get("query", ""),
                entry.get("source", ""),
                entry.get("source_label", ""),
                entry.get("count", 0),
                layers.get("直接相关", 0),
                layers.get("方法参考", 0),
                layers.get("背景脉络", 0),
                entry.get("at", ""),
            ]
        )
    return buffer.getvalue()
