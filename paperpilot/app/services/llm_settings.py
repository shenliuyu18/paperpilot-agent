"""User-supplied model configuration (v6.2).

Everything the offline-first product used to inherit from environment variables
can now be filled in from the UI: a provider preset, the model name and the user's
own API key. Two rules make that safe enough to ship on a shared demo instance:

* the key is never returned by any endpoint and is only echoed as a mask when this
  instance also persists it (``PAPERPILOT_LLM_PERSIST_KEY=0`` keeps it in memory);
* the base URL goes through the same SSRF guard as custom sources, so a hosted
  instance cannot be pointed at loopback or link-local metadata addresses.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .. import config
from .. import llm
from . import netguard


class LLMSettingsError(ValueError):
    pass


PROVIDERS: dict[str, dict] = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
        "default_model": "gpt-4o-mini",
        "embedding_models": ["text-embedding-3-small", "text-embedding-3-large"],
        "key_url": "https://platform.openai.com/api-keys",
        "note": "OpenAI 官方接口，也可换成任何 OpenAI 兼容网关。",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
        "embedding_models": [],
        "key_url": "https://platform.deepseek.com/api_keys",
        "note": "中文写作与推理性价比高；reasoner 模型输出较慢，建议只用于核查类任务。",
    },
    "moonshot": {
        "label": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "default_model": "moonshot-v1-32k",
        "embedding_models": [],
        "key_url": "https://platform.moonshot.cn/console/api-keys",
        "note": "长上下文友好，适合整篇草稿的语义核查。",
    },
    "dashscope": {
        "label": "阿里通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-turbo", "qwen-max"],
        "default_model": "qwen-plus",
        "embedding_models": ["text-embedding-v3"],
        "key_url": "https://bailian.console.aliyun.com/",
        "note": "百炼平台的 OpenAI 兼容模式。",
    },
    "zhipu": {
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-plus", "glm-4-air", "glm-4-flash"],
        "default_model": "glm-4-air",
        "embedding_models": [],
        "key_url": "https://open.bigmodel.cn/usercenter/apikeys",
        "note": "开放平台 API 地址已带 /v4，无需再补路径。",
    },
    "custom": {
        "label": "自定义 OpenAI 兼容",
        "base_url": "",
        "models": [],
        "default_model": "",
        "embedding_models": [],
        "key_url": "",
        "note": "自建网关、vLLM、OneAPI 等；本地地址需设置 PAPERPILOT_ALLOW_PRIVATE_LLM=1。",
    },
}

PURPOSES = [
    {"id": "agent_report", "label": "研究报告生成"},
    {"id": "assistant", "label": "项目 Copilot 对话"},
    {"id": "draft_write", "label": "草稿段落写作"},
    {"id": "semantic_audit", "label": "草稿语义核查"},
    {"id": "contradiction", "label": "跨论文矛盾复核"},
]

_PURPOSE_IDS = {item["id"] for item in PURPOSES}
MAX_KEY_CHARS = 300
MAX_MODEL_CHARS = 120
TEST_MAX_TOKENS = 512
TEST_TIMEOUT = 20.0
_KEY_SHAPE = re.compile(r"^[^\s\x00-\x1f\x7f]+$")


def persist_keys() -> bool:
    return os.getenv("PAPERPILOT_LLM_PERSIST_KEY", "1").strip().lower() not in {
        "0", "false", "no", "off"
    }


def allow_private_base() -> bool:
    return os.getenv("PAPERPILOT_ALLOW_PRIVATE_LLM", "").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _settings_path() -> Path:
    override = os.getenv("PAPERPILOT_LLM_SETTINGS_FILE")
    path = Path(override) if override else config.DATA_DIR / "llm_settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _secrets_path() -> Path:
    override = os.getenv("PAPERPILOT_LLM_SETTINGS_SECRETS_FILE")
    path = Path(override) if override else config.DATA_DIR / "llm_settings.secrets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


_memory_key = ""


def _stored() -> dict:
    data = _read_json(_settings_path())
    return data.get("settings") if isinstance(data.get("settings"), dict) else {}


def _stored_key() -> str:
    if not persist_keys():
        return _memory_key
    return str(_read_json(_secrets_path()).get("api_key") or "")


def _save_stored(value: dict) -> None:
    _write_json(_settings_path(), {"settings": value})


def _save_secret(key: str) -> None:
    global _memory_key
    _memory_key = key
    if persist_keys():
        _write_json(_secrets_path(), {"api_key": key, "updated_at": _now()})


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _mask(token: str) -> str:
    return "••••" + token[-4:] if token and len(token) > 4 else ("••••" if token else "")


def _from_env() -> dict:
    # config.LLM_* is mutated by apply(), so the fallback is re-parsed from the
    # environment each time instead of read back off the module.
    key = (os.getenv("PAPERPILOT_LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    base = (
        os.getenv("PAPERPILOT_LLM_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    ).strip()
    model = (os.getenv("PAPERPILOT_LLM_MODEL") or "gpt-4o-mini").strip()
    try:
        timeout = float(os.getenv("PAPERPILOT_LLM_TIMEOUT", "60"))
    except ValueError:
        timeout = 60.0
    try:
        retries = int(os.getenv("PAPERPILOT_LLM_RETRIES", "1"))
    except ValueError:
        retries = 1
    routes = {}
    for part in str(os.getenv("PAPERPILOT_LLM_ROUTES", "") or "").split(","):
        if ":" not in part:
            continue
        purpose, routed = part.split(":", 1)
        if purpose.strip() and routed.strip():
            routes[purpose.strip()] = routed.strip()
    embeddings = bool(key) and os.getenv(
        "PAPERPILOT_EMBEDDINGS", "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
    return {
        "provider": "",
        "base_url": base,
        "model": model,
        "api_key": key,
        "key_source": "env" if key else "none",
        "routes": routes,
        "timeout": timeout,
        "retries": max(0, min(retries, 3)),
        "embeddings": embeddings,
        "embedding_model": (os.getenv("PAPERPILOT_EMBEDDING_MODEL") or "text-embedding-3-small").strip(),
        "updated_at": "",
    }


def validate_base_url(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    try:
        parts = urlsplit(text)
    except ValueError as exc:
        raise LLMSettingsError("接口地址无法解析。") from exc
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise LLMSettingsError("接口地址需要以 http:// 或 https:// 开头。")
    if parts.username or parts.password:
        raise LLMSettingsError("接口地址不能包含账号或密码，请写在 API Key 里。")
    if not allow_private_base():
        try:
            netguard.validate_url(text)
        except netguard.UnsafeURLError as exc:
            raise LLMSettingsError(
                f"接口地址不符合安全要求：{exc}（本地网关请设置 PAPERPILOT_ALLOW_PRIVATE_LLM=1）"
            ) from exc
    return text


def validate_api_key(value: str) -> str:
    text = str(value or "").strip()
    if len(text) > MAX_KEY_CHARS:
        raise LLMSettingsError(f"API Key 过长（最多 {MAX_KEY_CHARS} 字符）。")
    if text and not _KEY_SHAPE.match(text):
        raise LLMSettingsError("API Key 不能包含空格或换行。")
    return text


def _validate_routes(value: dict) -> dict:
    if not isinstance(value, dict):
        raise LLMSettingsError("分用途模型必须是 JSON 对象。")
    routes: dict[str, str] = {}
    for purpose, model in value.items():
        key = str(purpose or "").strip()
        if not key:
            continue
        if key not in _PURPOSE_IDS:
            raise LLMSettingsError(f"不支持的用途：{key}")
        text = str(model or "").strip()[:MAX_MODEL_CHARS]
        if text:
            routes[key] = text
    return routes


def _validate_number(value, low: float, high: float, name: str, default):
    if value is None or value == "":
        return float(default) if isinstance(default, float) else int(default)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LLMSettingsError(f"{name}必须是数字。") from exc
    if not low <= number <= high:
        raise LLMSettingsError(f"{name}需要在 {low} 到 {high} 之间。")
    return number if isinstance(default, float) else int(number)


def _pick_text(payload: dict, existing: dict, provider: str, field: str) -> str:
    text = str(payload.get(field) or "").strip()[:MAX_MODEL_CHARS if field == "model" else 300]
    if text:
        return text
    if provider == existing.get("provider"):
        return str(existing.get(field) or "").strip()
    return ""


def _fallback_base(preset: dict, provider: str) -> str:
    base = str(preset.get("base_url") or "").strip()
    if base:
        return base
    raise LLMSettingsError("请填写接口地址（Base URL）。")


def normalize(payload: dict, existing: dict) -> dict:
    provider = str(payload.get("provider") or existing.get("provider") or "openai").strip()
    if provider not in PROVIDERS:
        raise LLMSettingsError(f"未知的模型预设：{provider}")
    preset = PROVIDERS[provider]
    base_url = validate_base_url(_pick_text(payload, existing, provider, "base_url") or _fallback_base(preset, provider))
    model = _pick_text(payload, existing, provider, "model") or preset["default_model"]
    if not model:
        raise LLMSettingsError("请填写模型名（自定义网关没有预设模型清单）。")
    routes_payload = payload.get("routes")
    if routes_payload is None:
        routes = dict(existing.get("routes") or {})
        if provider != existing.get("provider"):
            routes = {}
    else:
        routes = _validate_routes(routes_payload)
    embeddings = payload.get("embeddings")
    embedding_model = str(
        payload.get("embedding_model") or existing.get("embedding_model") or ""
    ).strip()[:MAX_MODEL_CHARS]
    return {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "routes": routes,
        "timeout": _validate_number(payload.get("timeout"), 1.0, 300.0, "超时", config.LLM_TIMEOUT),
        "retries": _validate_number(payload.get("retries"), 0, 3, "重试次数", config.LLM_RETRIES),
        "embeddings": bool(embeddings) if embeddings is not None else bool(existing.get("embeddings")),
        "embedding_model": embedding_model or "text-embedding-3-small",
        "updated_at": _now(),
    }


def effective() -> dict:
    stored = _stored()
    baseline = _from_env()
    if not stored:
        return baseline
    key = _stored_key()
    return {
        "provider": stored.get("provider") or "",
        "base_url": str(stored.get("base_url") or baseline["base_url"]),
        "model": str(stored.get("model") or baseline["model"]),
        "api_key": key,
        "key_source": "ui" if key else "none",
        "routes": dict(stored.get("routes") or {}),
        "timeout": float(stored.get("timeout") or baseline["timeout"]),
        "retries": int(stored.get("retries") if stored.get("retries") is not None else baseline["retries"]),
        "embeddings": bool(stored.get("embeddings")) and bool(key),
        "embedding_model": str(stored.get("embedding_model") or baseline["embedding_model"]),
        "updated_at": str(stored.get("updated_at") or ""),
    }


def apply() -> dict:
    """Push the effective settings onto ``config`` so every caller sees them live."""
    values = effective()
    config.LLM_API_KEY = values["api_key"]
    config.LLM_BASE_URL = values["base_url"]
    config.LLM_MODEL = values["model"]
    config.LLM_ROUTES = dict(values["routes"])
    config.LLM_TIMEOUT = values["timeout"]
    config.LLM_RETRIES = values["retries"]
    config.EMBEDDING_MODEL = values["embedding_model"]
    config.EMBEDDING_ENABLED = values["embeddings"]
    config.LLM_ENABLED = bool(values["api_key"])
    config.APP_MODE = "online" if config.LLM_ENABLED else "offline"
    return values


def public(values: dict | None = None) -> dict:
    values = values if values is not None else effective()
    provider = values.get("provider") or ""
    preset = PROVIDERS.get(provider) or {}
    key = str(values.get("api_key") or "")
    # When this instance does not persist keys it is a shared/ephemeral deployment,
    # so even a masked tail is treated as somebody else's secret.
    mask = _mask(key) if (key and persist_keys()) else ""
    return {
        "provider": provider,
        "provider_label": preset.get("label") or ("环境变量" if not provider else provider),
        "base_url": values.get("base_url", ""),
        "model": values.get("model", ""),
        "models": list(preset.get("models") or []),
        "embedding_models": list(preset.get("embedding_models") or []),
        "key_url": preset.get("key_url", ""),
        "note": preset.get("note", ""),
        "has_key": bool(key),
        "masked_key": mask,
        "key_source": values.get("key_source", "none"),
        "persist_keys": persist_keys(),
        "allow_private": allow_private_base(),
        "enabled": bool(key),
        "mode": "online" if key else "offline",
        "routes": dict(values.get("routes") or {}),
        "purposes": [dict(item) for item in PURPOSES],
        "timeout": values.get("timeout", 60.0),
        "retries": values.get("retries", 1),
        "embeddings": bool(values.get("embeddings")),
        "embedding_model": values.get("embedding_model", ""),
        "updated_at": values.get("updated_at", ""),
        "driven_by": "ui" if _stored() else "env",
        "providers": [
            {
                "id": key_id,
                "label": item["label"],
                "base_url": item["base_url"],
                "models": list(item["models"]),
                "embedding_models": list(item["embedding_models"]),
                "default_model": item["default_model"],
                "key_url": item["key_url"],
                "note": item["note"],
            }
            for key_id, item in PROVIDERS.items()
        ],
    }


def save(payload: dict) -> dict:
    existing = _stored()
    normalized = normalize(payload, existing)
    key = _stored_key()
    if payload.get("clear_key"):
        key = ""
    supplied = validate_api_key(str(payload.get("api_key") or ""))
    if supplied:
        key = supplied
    normalized["embeddings"] = normalized["embeddings"] and bool(key)
    _save_stored(normalized)
    _save_secret(key)
    return public(apply())


def clear() -> dict:
    global _memory_key
    path = _settings_path()
    if path.exists():
        path.unlink()
    secrets = _secrets_path()
    if secrets.exists():
        secrets.unlink()
    _memory_key = ""
    return public(apply())


def _chat_endpoint(base_url: str) -> str:
    base = str(base_url or "").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _default_poster(url: str, headers: dict, payload: dict) -> requests.Response:
    return requests.post(url, headers=headers, json=payload, timeout=TEST_TIMEOUT)


def _provider_message(response) -> str:
    """Best-effort provider error text, scrubbed of anything token-shaped."""
    try:
        data = response.json()
    except Exception:  # noqa: BLE001 - error bodies are not always JSON
        return ""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return ""
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    if isinstance(error, dict):
        text = " ".join(str(part) for part in (error.get("code"), error.get("message")) if part)
    elif isinstance(error, str):
        text = error
    else:
        text = str(data.get("message") or "")
    text = re.sub(r"Bearer\s+\S+", "Bearer …", text)
    text = re.sub(r"sk-[A-Za-z0-9_\-]{4,}", "sk-…", text)
    return " ".join(text.split())[:160]


def _failure(status: int, response=None) -> str:
    if status in (401, 403):
        base = "密钥被拒绝（HTTP %d）：请确认 API Key 正确且已开通该模型。" % status
    elif status == 404:
        base = "模型或接口地址不存在（HTTP 404）：请核对模型名与 Base URL。"
    elif status == 429:
        base = "触发了限流（HTTP 429）：额度或并发不足，稍后再试。"
    elif status >= 500:
        base = f"服务端错误（HTTP {status}）：稍后重试或更换模型。"
    else:
        base = f"请求失败（HTTP {status}）。"
    detail = _provider_message(response) if response is not None else ""
    return f"{base} 详情：{detail}" if detail else base


def test_connection(payload: dict | None = None, *, poster=None) -> dict:
    """Send one tiny chat request; unsaved draft values may override the store."""
    values = effective()
    payload = payload or {}
    provider = str(payload.get("provider") or values.get("provider") or "").strip()
    if provider and provider not in PROVIDERS:
        raise LLMSettingsError(f"未知的模型预设：{provider}")
    preset = PROVIDERS.get(provider) or {}
    # Testing a provider the user has not saved uses that provider's own defaults;
    # testing the saved provider keeps the address/model they edited.
    saved_provider = provider == str(values.get("provider") or "")
    draft_base = str(payload.get("base_url") or "").strip()
    base_url = validate_base_url(
        draft_base
        or ((values.get("base_url") or "") if saved_provider else "")
        or preset.get("base_url")
        or values.get("base_url")
        or ""
    )
    draft_model = str(payload.get("model") or "").strip()[:MAX_MODEL_CHARS]
    model = (
        draft_model
        or ((values.get("model") or "") if saved_provider else "")
        or preset.get("default_model")
        or values.get("model")
        or ""
    )
    if not model:
        raise LLMSettingsError("请填写模型名。")
    key = validate_api_key(str(payload.get("api_key") or "")) or str(values.get("api_key") or "")
    if not key:
        raise LLMSettingsError("先填写 API Key，再测试连接。")
    url = _chat_endpoint(base_url)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        "temperature": 0,
        "max_tokens": TEST_MAX_TOKENS,
    }
    started = time.perf_counter()
    try:
        response = (poster or _default_poster)(url, headers, body)
    except requests.exceptions.Timeout as exc:
        raise LLMSettingsError(f"请求超时（{int(TEST_TIMEOUT)} 秒）：请检查网络或提高超时。") from exc
    except requests.exceptions.RequestException as exc:
        raise LLMSettingsError("无法连接该接口：请检查 Base URL、网络与证书。") from exc
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    status = int(getattr(response, "status_code", 200) or 200)
    if status >= 400:
        raise LLMSettingsError(_failure(status, response))
    try:
        data = response.json()
        if isinstance(data, str):
            data = json.loads(data)
        reply = llm.extract_content(data)
    except llm.LLMBadResponse as exc:
        raise LLMSettingsError(f"返回不是 OpenAI 兼容的完整回复：{exc}") from exc
    except (AttributeError, ValueError, TypeError) as exc:
        raise LLMSettingsError("返回不是 OpenAI 兼容格式：请确认地址指向 /v1 聊天补全接口。") from exc
    return {
        "ok": True,
        "provider": provider,
        "model": str(data.get("model") or model),
        "base_url": base_url,
        "latency_ms": latency_ms,
        "reply": " ".join(str(reply or "").split())[:120],
    }
