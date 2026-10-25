"""Audited, budget-aware wrapper around the optional OpenAI-compatible client.

The gateway adds four things the raw ``app.llm`` client does not provide:

* a per-call audit trail (purpose, latency, sizes, failure reason) that never
  stores prompts, keys or user text;
* hard call budgets, so ``max_llm_calls`` is enforced before a request leaves
  the process instead of only being charged afterwards;
* a bounded LRU cache for deterministic low-temperature calls;
* one structured-output repair attempt with a lightweight schema check.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import config
from .. import llm
from . import agent_state


_ACTIVE_AGENT_RUN: ContextVar[dict | None] = ContextVar("paperpilot_agent_run", default=None)


class LLMBudgetExceeded(RuntimeError):
    """Raised before a model call when the caller's budget is exhausted."""


@dataclass
class CallBudget:
    max_calls: int
    used: int = 0

    def remaining(self) -> int:
        return max(0, int(self.max_calls) - int(self.used))

    def check(self) -> None:
        if self.remaining() <= 0:
            raise LLMBudgetExceeded("模型调用预算已用尽。")

    def spend(self) -> int:
        self.used += 1
        return self.used


@contextmanager
def agent_run_context(run: dict | None):
    """Attach an Agent run to nested gateway calls that cannot pass it directly."""
    token = _ACTIVE_AGENT_RUN.set(run)
    try:
        yield
    finally:
        _ACTIVE_AGENT_RUN.reset(token)


def _effective_run(run: dict | None) -> dict | None:
    """The explicit run, or the one bound by ``agent_run_context``."""
    return run if run is not None else _ACTIVE_AGENT_RUN.get()


_CACHE: OrderedDict[str, str] = OrderedDict()
_CACHE_LOCK = threading.RLock()
_CACHE_MAX = 128

_AUDIT: list[dict] = []
_AUDIT_LOCK = threading.RLock()
_AUDIT_MAX = 200
_AUDIT_FILE = "llm_calls.json"


def _cache_key(messages: list[dict[str, str]], temperature: float, max_tokens: int) -> str:
    payload = json.dumps(
        {
            "model": config.LLM_MODEL,
            "base_url": config.LLM_BASE_URL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> str | None:
    with _CACHE_LOCK:
        if key not in _CACHE:
            return None
        value = _CACHE[key]
        _CACHE.move_to_end(key)
        return value


def _cache_put(key: str, value: str) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = value
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)


def _audit_path() -> Path:
    configured = os.getenv("PAPERPILOT_DATA_DIR")
    root = Path(configured) if configured else Path(config.DATA_DIR)
    return root / _AUDIT_FILE


def _persist_audit() -> None:
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(
            json.dumps(_AUDIT[-_AUDIT_MAX:], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(path)
    except OSError:
        # Auditing must never break a user request.
        return


def _record(entry: dict) -> None:
    with _AUDIT_LOCK:
        _AUDIT.append(entry)
        del _AUDIT[:-_AUDIT_MAX]
        _persist_audit()


def audit_log(limit: int = 50) -> list[dict]:
    with _AUDIT_LOCK:
        return [dict(item) for item in _AUDIT[-max(0, int(limit)):]]


def reset_audit() -> None:
    with _AUDIT_LOCK:
        _AUDIT.clear()
        _persist_audit()


def reset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _model_for(purpose: str) -> str:
    routes = getattr(config, "LLM_ROUTES", None) or {}
    return str(routes.get(str(purpose)) or getattr(config, "LLM_MODEL", "") or "")


def _degrade_reason(exc: BaseException) -> str:
    name = type(exc).__name__
    if name == "LLMUnavailable":
        return "disabled"
    if name == "LLMBadResponse":
        return "schema"
    if name == "LLMHTTPError":
        status = int(getattr(exc, "status", 0) or 0)
        if status in (401, 403):
            return "auth"
        if status == 429:
            return "rate_limit"
        return "request"
    return "transport"


def _error_label(exc: BaseException) -> str:
    if type(exc).__name__ == "LLMHTTPError":
        return f"HTTP {int(getattr(exc, 'status', 0) or 0)}"
    return type(exc).__name__


def _error_detail(exc: BaseException) -> str:
    if type(exc).__name__ == "LLMHTTPError":
        return str(getattr(exc, "detail", "") or "")
    return ""


def _record_degrade(purpose: str, reason: str, detail: str = "",
                    run: dict | None = None) -> None:
    _record({
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "purpose": purpose,
        "model": _model_for(purpose),
        "latency_ms": 0,
        "prompt_chars": 0,
        "response_chars": 0,
        "ok": False,
        "error": detail or reason,
        "degrade_reason": reason,
        "run_id": str((_effective_run(run) or {}).get("id") or ""),
        "cached": False,
    })


def summary(limit: int = 200) -> dict:
    """Aggregate the audit log for the UI/report: volume, cache hits, cost signals."""
    calls = audit_log(limit)
    total = len(calls)
    ok = sum(1 for call in calls if call.get("ok"))
    cached = sum(1 for call in calls if call.get("cached"))
    latencies = [int(call.get("latency_ms") or 0) for call in calls
                 if call.get("ok") and not call.get("cached")]
    reasons: dict[str, int] = {}
    purposes: dict[str, int] = {}
    for call in calls:
        purposes[str(call.get("purpose") or "")] = purposes.get(str(call.get("purpose") or ""), 0) + 1
        reason = call.get("degrade_reason")
        if reason:
            reasons[str(reason)] = reasons.get(str(reason), 0) + 1
    return {
        "calls": total,
        "ok": ok,
        "failed": total - ok,
        "cached": cached,
        "cache_hit_rate": round(cached / total, 4) if total else 0.0,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        "prompt_chars": sum(int(call.get("prompt_chars") or 0) for call in calls),
        "response_chars": sum(int(call.get("response_chars") or 0) for call in calls),
        "degrade_reasons": reasons,
        "by_purpose": purposes,
        "routes": dict(getattr(config, "LLM_ROUTES", {}) or {}),
        "enabled": bool(getattr(config, "LLM_ENABLED", False)),
    }


def _check_run_budget(run: dict | None) -> None:
    if not run:
        return
    snapshot = agent_state.budget_snapshot(run)
    if int(snapshot["llm_calls"]["used"]) >= int(snapshot["llm_calls"]["max"]):
        raise LLMBudgetExceeded("运行的模型调用预算已用尽。")


EMPTY_RETRY_DELAY = 0.6
REASONING_RETRY_TOKENS = 32768
# Any completion asked for 800+ tokens was measured slower than the 60 s default
# (ask 8–14 s, compare 51 s, assistant 14 s), so those get the long-completion
# timeout. Short structured calls (draft_write 400, contradiction 600) do not.
LONG_COMPLETION_TOKENS = 800


def _timeout_for(max_tokens: int) -> float:
    """Request timeout for one completion, widened for long answers.

    A configured timeout is the user's own ceiling and is honoured verbatim; only
    the untouched 60 s default is widened, because a 2.6k-token 简报 or a full-text
    精读 regularly needs longer and a premature ReadTimeout degrades it silently.
    """
    base = float(config.LLM_TIMEOUT)
    if base != config.DEFAULT_LLM_TIMEOUT:
        return base
    if int(max_tokens) >= LONG_COMPLETION_TOKENS:
        return max(base, float(config.LLM_LONG_TIMEOUT))
    return base


def _call_with_empty_retry(messages: list[dict[str, str]], temperature: float,
                           max_tokens: int, model: str) -> str:
    """One extra attempt when the endpoint answers 200 with unusable content.

    A reasoning gateway can spend the whole completion budget on hidden
    reasoning and return an empty ``content``. In that case retrying with the
    same cap just burns the budget again, so the retry gets a larger cap;
    ordinary empty replies keep their original budget. JSON schema errors keep
    their own repair path and are not retried here.
    """
    try:
        return llm.chat(messages, temperature=temperature, max_tokens=max_tokens,
                        model=model or None, timeout=_timeout_for(max_tokens))
    except llm.LLMBadResponse as exc:
        time.sleep(EMPTY_RETRY_DELAY)
        retry_tokens = max_tokens
        if getattr(exc, "reasoning_exhausted", False):
            retry_tokens = max(max_tokens, REASONING_RETRY_TOKENS)
        return llm.chat(messages, temperature=temperature, max_tokens=retry_tokens,
                        model=model or None, timeout=_timeout_for(retry_tokens))


def audited_chat(
    messages: list[dict[str, str]],
    purpose: str,
    temperature: float = 0.2,
    max_tokens: int = 1800,
    budget: CallBudget | None = None,
    run: dict | None = None,
    use_cache: bool = False,
    defer_cache: bool = False,
) -> tuple[str, dict]:
    """Call the model and return ``(text, meta)``.

    Raises ``LLMBudgetExceeded`` before calling when a budget is exhausted and
    re-raises transport errors after recording an audit entry. A cached reply is
    a reuse rather than a model call: it is audited, never charged, and stays
    available even after a budget is exhausted. The run is the explicit argument
    or, for nested helpers that cannot carry it, the run bound by
    ``agent_run_context``.
    """
    # Nested actions (read/compare/ask/translation) cannot thread the run through
    # every helper signature, so fall back to the action-scoped run.
    effective_run = run if run is not None else _ACTIVE_AGENT_RUN.get()

    prompt_chars = sum(len(str(item.get("content") or "")) for item in messages)
    model = _model_for(purpose)
    cache_key = ""
    if use_cache and temperature <= 0.2:
        cache_key = _cache_key(messages, temperature, max_tokens)
        cached = _cache_get(cache_key)
        if cached is not None:
            _record(
                {
                    "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "purpose": purpose,
                    "model": model,
                    "latency_ms": 0,
                    "prompt_chars": prompt_chars,
                    "response_chars": len(cached),
                    "ok": True,
                    "error": "",
                    "run_id": str((effective_run or {}).get("id") or ""),
                    "cached": True,
                }
            )
            return cached, {
                "purpose": purpose,
                "model": model,
                "latency_ms": 0,
                "chars": len(cached),
                "cached": True,
                "status": "ok",
            }

    # Only a real call has to clear the budgets. The block is audited here so the
    # non-JSON callers leave the same trace as the JSON ones.
    try:
        if budget is not None:
            budget.check()
        _check_run_budget(effective_run)
    except LLMBudgetExceeded as exc:
        _record_degrade(purpose, "budget", str(exc), run=effective_run)
        raise

    started = time.perf_counter()
    try:
        text = _call_with_empty_retry(messages, temperature, max_tokens, model)
    except Exception as exc:  # noqa: BLE001 - audit then re-raise
        # The request already left the process, so it is charged: budget is a
        # cost guard, not a success counter.
        if budget is not None:
            budget.spend()
        if effective_run is not None:
            agent_state.spend_llm(effective_run, 1)
        _record(
            {
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "purpose": purpose,
                "model": model,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "prompt_chars": prompt_chars,
                "response_chars": 0,
                "ok": False,
                "error": _error_label(exc),
                "detail": _error_detail(exc),
                "degrade_reason": _degrade_reason(exc),
                "run_id": str((effective_run or {}).get("id") or ""),
                "cached": False,
            }
        )
        raise

    latency_ms = int((time.perf_counter() - started) * 1000)
    if budget is not None:
        budget.spend()
    if effective_run is not None:
        agent_state.spend_llm(effective_run, 1)
    if cache_key and not defer_cache:
        _cache_put(cache_key, text)
    _record(
        {
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "purpose": purpose,
            "model": model,
            "latency_ms": latency_ms,
            "prompt_chars": prompt_chars,
            "response_chars": len(text),
            "ok": True,
            "error": "",
            "run_id": str((effective_run or {}).get("id") or ""),
            "cached": False,
        }
    )
    return text, {
        "purpose": purpose,
        "model": model,
        "latency_ms": latency_ms,
        "chars": len(text),
        "cached": False,
        "status": "ok",
    }


def _value_error(value: Any, rule: dict, name: str) -> str | None:
    expected = rule.get("type")
    if expected == "string":
        if not isinstance(value, str):
            return f"{name} 必须是字符串"
        if "min_length" in rule and len(value) < int(rule["min_length"]):
            return f"{name} 长度不足"
        if "max_length" in rule and len(value) > int(rule["max_length"]):
            return f"{name} 过长"
    elif expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{name} 必须是整数"
    elif expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{name} 必须是数字"
    elif expected == "boolean":
        if not isinstance(value, bool):
            return f"{name} 必须是布尔值"
    elif expected == "array":
        if not isinstance(value, list):
            return f"{name} 必须是数组"
        if "max_items" in rule and len(value) > int(rule["max_items"]):
            return f"{name} 元素过多"
        item_rule = rule.get("items")
        if item_rule:
            for index, item in enumerate(value):
                error = _value_error(item, item_rule, f"{name}[{index}]")
                if error:
                    return error
    elif expected == "object":
        if not isinstance(value, dict):
            return f"{name} 必须是对象"
    if "enum" in rule and value not in rule["enum"]:
        return f"{name} 取值不在允许范围"
    if "max" in rule and isinstance(value, (int, float)) and value > rule["max"]:
        return f"{name} 超出上限"
    if "min" in rule and isinstance(value, (int, float)) and value < rule["min"]:
        return f"{name} 低于下限"
    return None


def validate_payload(payload: Any, schema: dict | None) -> str | None:
    """Return an error message when ``payload`` does not match the tiny schema."""
    if not schema:
        return None
    if schema.get("type") == "object":
        if not isinstance(payload, dict):
            return "输出必须是 JSON 对象"
        for key in schema.get("required") or []:
            if key not in payload:
                return f"缺少字段 {key}"
        for key, rule in (schema.get("properties") or {}).items():
            if key in payload:
                error = _value_error(payload[key], rule, key)
                if error:
                    return error
        return None
    if schema.get("type") == "array":
        return _value_error(payload, schema, "root")
    return None


def audited_chat_json(
    system: str,
    user: str,
    purpose: str,
    schema: dict | None = None,
    temperature: float = 0.1,
    max_tokens: int = 1800,
    budget: CallBudget | None = None,
    run: dict | None = None,
    use_cache: bool = False,
) -> tuple[Any | None, dict]:
    """Call the model, parse JSON, validate it and repair once if needed."""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    # A JSON response is only safe to reuse after schema validation. Otherwise a
    # malformed answer is cached and every later request pays another repair call.
    response_cache_key = ""
    if use_cache and temperature <= 0.2:
        response_cache_key = _cache_key(messages, temperature, max_tokens)
    try:
        text, meta = audited_chat(
            messages,
            purpose=purpose,
            temperature=temperature,
            max_tokens=max_tokens,
            budget=budget,
            run=run,
            use_cache=use_cache,
            defer_cache=bool(response_cache_key),
        )
    except LLMBudgetExceeded:
        return None, {"purpose": purpose, "model": _model_for(purpose), "latency_ms": 0,
                      "chars": 0, "cached": False, "status": "budget",
                      "degrade_reason": "budget"}
    except Exception as exc:  # noqa: BLE001 - callers fall back offline
        return None, {"purpose": purpose, "model": _model_for(purpose), "latency_ms": 0,
                      "chars": 0, "cached": False, "status": "fallback",
                      "error": type(exc).__name__, "degrade_reason": _degrade_reason(exc)}

    error = ""
    try:
        payload = llm.extract_json(text)
        error = validate_payload(payload, schema) or ""
        if not error:
            if response_cache_key:
                _cache_put(response_cache_key, text)
            return payload, meta
    except Exception as exc:  # noqa: BLE001 - repair path below
        error = type(exc).__name__

    if budget is not None and budget.remaining() <= 0:
        _record_degrade(purpose, "schema", error or "schema")
        return None, {**meta, "status": "fallback", "error": error or "schema",
                      "degrade_reason": "schema"}
    repair_messages = messages + [
        {"role": "assistant", "content": text},
        {
            "role": "user",
            "content": (
                "上一次输出无法通过校验："
                + (error or "schema")
                + "。只输出修正后的 JSON，不要解释，不要添加新事实。"
            ),
        },
    ]
    try:
        repaired, repair_meta = audited_chat(
            repair_messages,
            purpose=purpose + "_repair",
            temperature=temperature,
            max_tokens=max_tokens,
            budget=budget,
            run=run,
            use_cache=False,
        )
        payload = llm.extract_json(repaired)
        error = validate_payload(payload, schema) or ""
        if error:
            _record_degrade(purpose, "schema", error)
            return None, {**repair_meta, "status": "fallback", "error": error,
                          "degrade_reason": "schema"}
        if response_cache_key:
            _cache_put(response_cache_key, repaired)
        return payload, {**repair_meta, "repaired": True}
    except Exception as exc:  # noqa: BLE001 - callers fall back offline
        _record_degrade(purpose, "schema", type(exc).__name__)
        return None, {**meta, "status": "fallback", "error": type(exc).__name__,
                      "degrade_reason": "schema"}
