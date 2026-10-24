from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from typing import Any

import requests

from . import config


class LLMUnavailable(RuntimeError):
    pass


class LLMBadResponse(RuntimeError):
    """The endpoint answered 200 but not with a usable completion."""

    def __init__(self, message: str = "", *, reasoning_exhausted: bool = False) -> None:
        super().__init__(message)
        self.reasoning_exhausted = bool(reasoning_exhausted)


class LLMHTTPError(RuntimeError):
    """A 4xx from the endpoint: auth, quota or a bad request, not a transport blip."""

    def __init__(self, status: int, detail: str = "") -> None:
        super().__init__(f"HTTP {int(status)}" + (f"：{detail}" if detail else ""))
        self.status = int(status)
        self.detail = detail


def _provider_detail(response) -> str:
    """Provider error text, scrubbed of token-shaped strings and bounded."""
    try:
        data = response.json()
    except Exception:  # noqa: BLE001 - error bodies are not always JSON
        return ""
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    if isinstance(error, dict):
        text = " ".join(
            str(part) for part in (error.get("type"), error.get("code"), error.get("message")) if part
        )
    elif isinstance(error, str):
        text = error
    else:
        text = str(data.get("message") or "")
    text = re.sub(r"Bearer\s+\S+", "Bearer …", text)
    text = re.sub(r"sk-[A-Za-z0-9_\-]{4,}", "sk-…", text)
    return " ".join(text.split())[:160]


def extract_content(data: Any) -> str:
    """Read the assistant text from a completion, tolerating chunk-shaped replies.

    Most gateways return ``choices[0].message.content``; some return a single
    ``delta`` chunk when the generation stops on the token cap. Treating the
    delta as a valid answer keeps truncated small probes usable, and an empty
    answer becomes an explicit error instead of a silent blank.
    """
    choices = (data or {}).get("choices") if isinstance(data, dict) else None
    if not choices:
        raise LLMBadResponse("模型返回缺少 choices。")
    choice = choices[0] or {}
    message = choice.get("message") or {}
    content = message.get("content")
    if content is None:
        content = (choice.get("delta") or {}).get("content")
    text = str(content or "").strip()
    if not text:
        usage = (data or {}).get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        reasoning = int(details.get("reasoning_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        exhausted = (
            choice.get("finish_reason") == "length"
            and reasoning > 0
            and (completion <= 0 or reasoning >= completion)
        )
        raise LLMBadResponse(
            "模型返回空内容（可能把额度耗尽在推理片段上）。",
            reasoning_exhausted=exhausted,
        )
    return text


_EMBED_CACHE: OrderedDict[tuple, list[float]] = OrderedDict()
_EMBED_LOCK = threading.RLock()
_EMBED_MAX = 2048


def _chat_endpoint() -> str:
    base = config.LLM_BASE_URL.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _embeddings_endpoint() -> str:
    base = config.LLM_BASE_URL.rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    return base + "/embeddings"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
    }


def chat(
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 1800,
    timeout: float | None = None,
    retries: int | None = None,
    model: str | None = None,
) -> str:
    if not config.LLM_ENABLED:
        raise LLMUnavailable("未配置模型密钥，当前为离线规则模式；可在“模型设置”中填入自己的 API Key。")
    attempts = 1 + max(0, int(config.LLM_RETRIES if retries is None else retries))
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.post(
                _chat_endpoint(),
                headers=_headers(),
                json={
                    "model": model or config.LLM_MODEL,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=timeout or config.LLM_TIMEOUT,
            )
            response.raise_for_status()
            return extract_content(response.json())
        except requests.HTTPError as exc:
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
            if status is not None and 400 <= int(status) < 500:
                raise LLMHTTPError(int(status), _provider_detail(response)) from exc
            last_error = exc
        except requests.RequestException as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(0.8 * (2 ** attempt))
    if last_error is not None:
        raise last_error
    raise LLMUnavailable("模型请求失败。")


def embed(
    texts: list[str],
    model: str | None = None,
    timeout: float | None = None,
) -> list[list[float]] | None:
    if not config.LLM_ENABLED:
        return None
    values = [str(text or "") for text in texts]
    if not values:
        return []
    model_name = model or config.EMBEDDING_MODEL
    keys = [
        (model_name, hashlib.sha1(value.encode("utf-8")).hexdigest())
        for value in values
    ]
    missing: list[tuple[int, str, tuple]] = []
    with _EMBED_LOCK:
        for index, (value, key) in enumerate(zip(values, keys)):
            if key not in _EMBED_CACHE:
                missing.append((index, value, key))
    if missing:
        try:
            response = requests.post(
                _embeddings_endpoint(),
                headers=_headers(),
                json={"model": model_name, "input": [item[1] for item in missing]},
                timeout=timeout or config.LLM_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            vectors = [item["embedding"] for item in data.get("data", [])]
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return None
        if len(vectors) != len(missing):
            return None
        with _EMBED_LOCK:
            for (_, _, key), vector in zip(missing, vectors):
                _EMBED_CACHE[key] = [float(value) for value in vector]
                _EMBED_CACHE.move_to_end(key)
            while len(_EMBED_CACHE) > _EMBED_MAX:
                _EMBED_CACHE.popitem(last=False)
    with _EMBED_LOCK:
        return [list(_EMBED_CACHE[key]) for key in keys]


def status() -> dict:
    return {
        "enabled": bool(config.LLM_ENABLED),
        "mode": "online" if config.LLM_ENABLED else "offline",
        "model": config.LLM_MODEL,
        "base_url": config.LLM_BASE_URL,
        "timeout": config.LLM_TIMEOUT,
        "retries": config.LLM_RETRIES,
        "embeddings": bool(config.EMBEDDING_ENABLED),
        "embedding_model": config.EMBEDDING_MODEL,
    }


def extract_json(text: str) -> Any:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1], strict=False)
        raise


def chat_json(system: str, user: str, temperature: float = 0.1,
              max_tokens: int = 1800) -> Any:
    raw = chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return extract_json(raw)
