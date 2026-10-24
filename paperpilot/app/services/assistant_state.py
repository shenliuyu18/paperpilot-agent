"""Storage for the project-scoped research Copilot conversation.

The assistant is intentionally a single capped conversation per project. It
shares the project store lock and atomic ``replace`` convention, and never
writes project memory, drafts or run state.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import project_state

MAX_MESSAGES = 60
MAX_TEXT = 4000
MAX_CITATIONS = 12  # assistant_service.TOP_K plus one comparison-ledger passage
MAX_SUGGESTIONS = 4

_ROLES = {"user", "assistant"}
_MODES = {"offline", "llm", "llm_degraded"}


def assistant_path(project_id: str) -> Path:
    return project_state.project_dir(project_id) / "assistant.json"


def empty_assistant(project_id: str) -> dict:
    now = project_state._now()
    return {
        "version": 1,
        "project_id": project_id,
        "messages": [],
        "created_at": now,
        "updated_at": now,
    }


def new_message_id(role: str, text: str, created_at: str) -> str:
    digest = hashlib.sha1(
        f"{role}|{created_at}|{text}".encode("utf-8")
    ).hexdigest()[:10]
    return f"msg-{digest}"


def _clip(text, limit: int) -> str:
    value = str(text or "")
    return value[:limit]


def build_message(role: str, text: str, **fields) -> dict:
    """Normalize a message before it is appended to the conversation."""
    payload = {"role": role, "text": text, **fields}
    return _normalize_message(payload)


def _normalize_message(message: dict) -> dict:
    role = str(message.get("role") or "assistant")
    if role not in _ROLES:
        role = "assistant"
    text = _clip(message.get("text"), MAX_TEXT)
    created_at = str(message.get("created_at") or project_state._now())
    message_id = str(message.get("id") or new_message_id(role, text, created_at))
    mode = str(message.get("mode") or ("offline" if role == "user" else "offline"))
    if mode not in _MODES:
        mode = "offline"
    citations = []
    for item in (message.get("citations") or [])[:MAX_CITATIONS]:
        if not isinstance(item, dict):
            continue
        try:
            citation_index = max(0, int(item.get("index") or 0))
        except (TypeError, ValueError):
            citation_index = 0
        citations.append(
            {
                "index": citation_index,
                "kind": str(item.get("kind") or "evidence"),
                "id": str(item.get("id") or ""),
                "title": _clip(item.get("title"), 200),
                "locator": _clip(item.get("locator"), 200),
                "quote": _clip(item.get("quote"), 500),
                "target": item.get("target") if isinstance(item.get("target"), dict) else {},
            }
        )
    suggestions = []
    for item in (message.get("suggestions") or [])[:MAX_SUGGESTIONS]:
        if not isinstance(item, dict):
            continue
        suggestions.append(
            {
                "type": str(item.get("type") or ""),
                "label": _clip(item.get("label"), 120),
                "reason": _clip(item.get("reason"), 240),
                "payload": item.get("payload") if isinstance(item.get("payload"), dict) else {},
                "requires_confirm": True,
            }
        )
    return {
        "id": message_id,
        "role": role,
        "text": text,
        "created_at": created_at,
        "mode": mode,
        "intent": str(message.get("intent") or "general"),
        "citations": citations,
        "grounding": message.get("grounding") if isinstance(message.get("grounding"), dict) else {},
        "suggestions": suggestions,
        "llm": message.get("llm") if isinstance(message.get("llm"), dict) else {},
    }


def _normalize(data: dict, project_id: str) -> dict:
    base = empty_assistant(project_id)
    if not isinstance(data, dict):
        return base
    base.update(data)
    base["project_id"] = project_id
    base["version"] = 1
    messages = data.get("messages")
    base["messages"] = (
        [_normalize_message(item) for item in messages if isinstance(item, dict)]
        if isinstance(messages, list)
        else []
    )
    if len(base["messages"]) > MAX_MESSAGES:
        base["messages"] = base["messages"][-MAX_MESSAGES:]
    return base


def read_assistant(project_id: str) -> dict:
    with project_state.project_lock():
        path = assistant_path(project_id)
        if not path.exists():
            return empty_assistant(project_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return empty_assistant(project_id)
        return _normalize(data, project_id)


def write_assistant(project_id: str, data: dict) -> dict:
    normalized = _normalize(data, project_id)
    normalized["updated_at"] = project_state._now()
    path = assistant_path(project_id)
    with project_state.project_lock():
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp.replace(path)
    return normalized


def append_message(project_id: str, message: dict) -> dict:
    with project_state.project_lock():
        data = read_assistant(project_id)
        data.setdefault("messages", []).append(_normalize_message(message))
        return write_assistant(project_id, data)


def clear_assistant(project_id: str) -> dict:
    return write_assistant(project_id, empty_assistant(project_id))


def export_markdown(project_id: str) -> str:
    data = read_assistant(project_id)
    project = project_state.read_projects().get("projects", {}).get(project_id) or {}
    title = str(project.get("title") or "研究项目")
    lines = [
        f"# {title} · 研究 Copilot",
        "",
        f"> 导出时间：{project_state._now()}",
        "> 引用只对应项目内证据、草稿块或运行报告；助手不会引入外部知识。",
        "",
    ]
    for message in data.get("messages") or []:
        role = "用户" if message.get("role") == "user" else "助手"
        lines.append(f"## {role}")
        lines.append("")
        lines.append(str(message.get("text") or ""))
        lines.append("")
        citations = message.get("citations") or []
        if citations:
            lines.append("引用：")
            for item in citations:
                lines.append(
                    f"- [{item.get('kind')}] {item.get('title')} · "
                    f"{item.get('locator')}"
                    + (f"：{item.get('quote')}" if item.get("quote") else "")
                )
            lines.append("")
        suggestions = message.get("suggestions") or []
        if suggestions:
            lines.append("建议动作（需用户确认）：")
            for item in suggestions:
                lines.append(f"- {item.get('label')}（{item.get('reason')}）")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
