from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import requests

from .. import config
from . import netguard


class CustomSourceError(RuntimeError):
    pass


PRESETS: dict[str, dict] = {
    "semantic_scholar": {
        "label": "Semantic Scholar",
        "name": "Semantic Scholar",
        "base_url": "https://api.semanticscholar.org/graph/v1/paper/search",
        "method": "GET",
        "query_param": "query",
        "limit_param": "limit",
        "extra_params": {
            "fields": "title,abstract,authors,year,venue,externalIds,url,openAccessPdf"
        },
        "result_path": "data",
        "field_map": {
            "id": "paperId",
            "title": "title",
            "abstract": "abstract",
            "authors": "authors[].name",
            "year": "year",
            "venue": "venue",
            "doi": "externalIds.DOI",
            "url": "url",
            "pdf_url": "openAccessPdf.url",
        },
        "auth_type": "header",
        "auth_name": "x-api-key",
        "account_param": "",
        "license": "Semantic Scholar API",
        "homepage": "https://www.semanticscholar.org/product/api",
        "note": "可匿名调用；配置 x-api-key 可提高额度。",
    },
    "core": {
        "label": "CORE",
        "name": "CORE",
        "base_url": "https://api.core.ac.uk/v3/search/works",
        "method": "GET",
        "query_param": "q",
        "limit_param": "limit",
        "extra_params": {},
        "result_path": "results",
        "field_map": {
            "id": "id",
            "title": "title",
            "abstract": "abstract",
            "authors": "authors",
            "year": "yearPublished",
            "venue": "publisher",
            "doi": "doi",
            "url": "downloadUrl",
            "pdf_url": "downloadUrl",
        },
        "auth_type": "bearer",
        "auth_name": "",
        "account_param": "",
        "license": "CORE API",
        "homepage": "https://core.ac.uk/services/api",
        "note": "需要 CORE API Key；没有 key 时建议使用内置来源。",
    },
    "generic_json": {
        "label": "通用 JSON API",
        "name": "通用 JSON API",
        "base_url": "",
        "method": "GET",
        "query_param": "query",
        "limit_param": "limit",
        "extra_params": {},
        "result_path": "results",
        "field_map": {
            "id": "id",
            "title": "title",
            "abstract": "abstract",
            "authors": "authors",
            "year": "year",
            "venue": "venue",
            "doi": "doi",
            "url": "url",
            "pdf_url": "pdf_url",
        },
        "auth_type": "none",
        "auth_name": "",
        "account_param": "",
        "license": "自定义",
        "homepage": "",
        "note": "按返回 JSON 的字段路径配置；支持点路径与 authors[].name。",
    },
}

AUTH_TYPES = {"none", "header", "bearer", "query", "basic"}
METHODS = {"GET", "POST"}
MAX_SOURCES = 20
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
CACHE_TTL = int(os.getenv("PAPERPILOT_CUSTOM_SOURCE_TTL", "3600"))
DEFAULT_TIMEOUT = (5, 20)


def _config_path() -> Path:
    override = os.getenv("PAPERPILOT_SOURCES_FILE")
    path = Path(override) if override else config.DATA_DIR / "custom_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _secrets_path() -> Path:
    override = os.getenv("PAPERPILOT_SOURCES_SECRETS_FILE")
    path = Path(override) if override else config.DATA_DIR / "custom_sources.secrets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def clear_cache() -> None:
    return None


def _load(path: Path, key: str) -> dict:
    if not path.exists():
        return {key: {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {key: {}}
    if not isinstance(data, dict) or not isinstance(data.get(key), dict):
        return {key: {}}
    return data


def _load_configs() -> dict:
    return _load(_config_path(), "sources")["sources"]


def _load_secrets() -> dict:
    return _load(_secrets_path(), "sources")["sources"]


def _save(path: Path, key: str, values: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps({key: values}, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", value or "").strip("-").lower()
    return text[:36] or "source"


def _new_id(name: str, preset: str) -> str:
    digest = hashlib.sha1(f"{name}|{preset}|{time.time()}".encode("utf-8")).hexdigest()[:6]
    return f"custom-{_slug(name)}-{digest}"


def _mask(token: str) -> str:
    if not token:
        return ""
    return "••••" + token[-4:]


def _public(source_id: str, config_value: dict, secrets: dict) -> dict:
    token = str(secrets.get("token") or "")
    return {
        **config_value,
        "id": source_id,
        "has_token": bool(token),
        "masked_token": _mask(token),
        "has_password": bool(secrets.get("password")),
    }


def _preset_defaults(preset: str) -> dict:
    base = PRESETS.get(preset)
    if not base:
        raise CustomSourceError(f"未知来源预设：{preset}")
    return {
        key: json.loads(json.dumps(value)) if isinstance(value, (dict, list)) else value
        for key, value in base.items()
        if key not in {"label", "license", "homepage", "note"}
    }


def preset_defaults(preset: str) -> dict:
    return _preset_defaults(preset)


def validate_config(config_value: dict) -> dict:
    preset = str(config_value.get("preset") or "generic_json")
    if preset not in PRESETS:
        raise CustomSourceError(f"未知来源预设：{preset}")
    defaults = _preset_defaults(preset)
    overrides = {}
    for key, value in config_value.items():
        if value is None:
            continue
        if key in {"field_map", "extra_params"} and value == {}:
            continue
        overrides[key] = value
    merged = {**defaults, **overrides}
    name = str(merged.get("name") or "").strip()
    if not name:
        raise CustomSourceError("来源名称不能为空。")
    base_url = str(merged.get("base_url") or "").strip()
    if not base_url:
        raise CustomSourceError("API 地址不能为空。")
    try:
        netguard.validate_url(base_url)
    except netguard.UnsafeURLError as exc:
        raise CustomSourceError(f"API 地址不符合安全要求：{exc}") from exc
    method = str(merged.get("method") or "GET").upper()
    if method not in METHODS:
        raise CustomSourceError("只支持 GET 或 POST。")
    auth_type = str(merged.get("auth_type") or "none").lower()
    if auth_type not in AUTH_TYPES:
        raise CustomSourceError("不支持的认证方式。")
    field_map = merged.get("field_map")
    if not isinstance(field_map, dict) or not field_map.get("title"):
        raise CustomSourceError("字段映射至少需要 title。")
    if not all(isinstance(value, str) for value in field_map.values()):
        raise CustomSourceError("字段映射必须是字符串路径。")
    extra_params = merged.get("extra_params") or {}
    if not isinstance(extra_params, dict):
        raise CustomSourceError("extra_params 必须是 JSON 对象。")
    headers = merged.get("headers") or {}
    if not isinstance(headers, dict):
        raise CustomSourceError("headers 必须是 JSON 对象。")
    result_path = str(merged.get("result_path") or "").strip()
    return {
        "name": name[:80],
        "preset": preset,
        "base_url": base_url,
        "method": method,
        "query_param": str(merged.get("query_param") or "query")[:60],
        "limit_param": str(merged.get("limit_param") or "limit")[:60],
        "extra_params": extra_params,
        "headers": {str(key)[:80]: str(value)[:300] for key, value in headers.items()},
        "result_path": result_path[:200],
        "field_map": {str(key)[:40]: str(value)[:200] for key, value in field_map.items()},
        "auth_type": auth_type,
        "auth_name": str(merged.get("auth_name") or "")[:80],
        "account_param": str(merged.get("account_param") or "")[:80],
        "account": str(merged.get("account") or "")[:200],
        "enabled": bool(merged.get("enabled", True)),
        "license": PRESETS[preset].get("license", "自定义"),
        "homepage": PRESETS[preset].get("homepage", ""),
        "note": PRESETS[preset].get("note", ""),
    }


def upsert(payload: dict) -> dict:
    configs = _load_configs()
    secrets_all = _load_secrets()
    source_id = str(payload.get("id") or "").strip()
    existing = configs.get(source_id, {}) if source_id else {}
    existing_secrets = secrets_all.get(source_id, {}) if source_id else {}
    merged_input = {**existing, **{key: value for key, value in payload.items() if value is not None}}
    normalized = validate_config(merged_input)
    if not source_id:
        source_id = _new_id(normalized["name"], normalized["preset"])
        if len(configs) >= MAX_SOURCES:
            raise CustomSourceError(f"最多配置 {MAX_SOURCES} 个自定义来源。")
        normalized["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    else:
        if source_id not in configs:
            raise CustomSourceError("来源不存在。")
        normalized["created_at"] = existing.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M")
    normalized["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    secrets = dict(existing_secrets)
    for key in ("token", "password"):
        value = payload.get(key)
        if value:
            secrets[key] = str(value).strip()
    if payload.get("clear_secret"):
        secrets.pop("token", None)
        secrets.pop("password", None)
    configs[source_id] = normalized
    secrets_all[source_id] = secrets
    _save(_config_path(), "sources", configs)
    _save(_secrets_path(), "sources", secrets_all)
    return _public(source_id, normalized, secrets)


def list_sources(include_disabled: bool = True) -> list[dict]:
    configs = _load_configs()
    secrets_all = _load_secrets()
    result = []
    for source_id, value in configs.items():
        if not include_disabled and not value.get("enabled", True):
            continue
        result.append(_public(source_id, value, secrets_all.get(source_id, {})))
    result.sort(key=lambda item: item.get("name", "").lower())
    return result


def get_public(source_id: str) -> dict | None:
    configs = _load_configs()
    value = configs.get(source_id)
    if not value:
        return None
    return _public(source_id, value, _load_secrets().get(source_id, {}))


def get_private(source_id: str) -> dict | None:
    configs = _load_configs()
    value = configs.get(source_id)
    if not value:
        return None
    return {"config": value, "secrets": _load_secrets().get(source_id, {})}


def set_enabled(source_id: str, enabled: bool) -> dict | None:
    configs = _load_configs()
    if source_id not in configs:
        return None
    configs[source_id]["enabled"] = bool(enabled)
    configs[source_id]["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    _save(_config_path(), "sources", configs)
    return get_public(source_id)


def delete(source_id: str) -> bool:
    configs = _load_configs()
    if source_id not in configs:
        return False
    del configs[source_id]
    secrets_all = _load_secrets()
    secrets_all.pop(source_id, None)
    _save(_config_path(), "sources", configs)
    _save(_secrets_path(), "sources", secrets_all)
    return True


def _extract(value, path: str):
    if not path:
        return value
    if "[]" in path:
        head, tail = path.split("[]", 1)
        head = head.rstrip(".")
        tail = tail.lstrip(".")
        items = _extract(value, head) if head else value
        if not isinstance(items, list):
            return []
        result = []
        for item in items:
            extracted = _extract(item, tail)
            if isinstance(extracted, list):
                result.extend(extracted)
            elif extracted is not None:
                result.append(extracted)
        return result
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(_text(item) for item in value if item is not None).strip()
    if isinstance(value, dict):
        for key in ("name", "display_name", "title", "value", "text"):
            if value.get(key):
                return _text(value[key])
        return ""
    return " ".join(str(value).split())


def _authors(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    result = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                name = _text(item.get("name") or item.get("display_name") or item.get("full_name"))
            else:
                name = _text(item)
            if name and name not in result:
                result.append(name)
    return result[:12]


def _year(value) -> int | None:
    if isinstance(value, int):
        return value if 1900 <= value <= 2100 else None
    match = re.search(r"(19|20)\d{2}", str(value or ""))
    return int(match.group(0)) if match else None


def _doi(value) -> str:
    text = _text(value).lower().strip()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    return text


def _paper_from_item(item: dict, source_id: str, source_name: str) -> dict | None:
    if not isinstance(item, dict):
        return None
    field_map = _load_configs().get(source_id, {}).get("field_map", {})
    title = _text(_extract(item, field_map.get("title", "title")))
    if not title:
        return None
    abstract = _text(_extract(item, field_map.get("abstract", "abstract")))
    authors = _authors(_extract(item, field_map.get("authors", "authors")))
    doi = _doi(_extract(item, field_map.get("doi", "doi")))
    external_id = _text(_extract(item, field_map.get("id", "id"))) if field_map.get("id") else ""
    digest = hashlib.sha1((doi or title or external_id).encode("utf-8")).hexdigest()[:14]
    suffix = re.sub(r"[^a-zA-Z0-9_.-]+", "-", external_id)[:40].strip("-")
    paper_id = f"{source_id}-{suffix or digest}"
    abstract_sections = [{"title": "Abstract", "page": 1, "text": abstract}] if abstract else []
    return {
        "id": paper_id,
        "doi": doi,
        "title": title,
        "abstract": abstract,
        "tldr": abstract[:240] if abstract else "自定义来源未提供摘要；打开原文或上传 PDF 解锁精读。",
        "authors": authors,
        "year": _year(_extract(item, field_map.get("year", "year"))),
        "venue": _text(_extract(item, field_map.get("venue", "venue"))),
        "url": _text(_extract(item, field_map.get("url", "url"))),
        "pdf_url": _text(_extract(item, field_map.get("pdf_url", "pdf_url"))),
        "source": f"custom:{source_id}",
        "source_label": source_name,
        "demo": False,
        "full_text": False,
        "extraction_level": "自定义来源 · 摘要级",
        "metrics": {"来源": source_name, "类型": "自定义 API"},
        "results": [],
        "baselines": [],
        "limitations": [],
        "novelty": [],
        "assumptions": [],
        "sections": abstract_sections,
        "figures": [],
        "evidence": (
            [{"field": "abstract", "label": source_name, "section": "Abstract", "page": 1, "quote": abstract[:280]}]
            if abstract
            else []
        ),
        "translation": [],
        "reviewer_questions": [],
        "repo_plan": {
            "environment": ["待补充：数据、代码与运行环境"],
            "steps": ["核对来源字段与原文", "补充开放获取全文", "记录实验条件", "对照论文指标"],
            "minimal_experiment": "先确认来源记录的 DOI/原文可用，再抽取最小实验所需信息。",
            "risks": ["自定义来源只提供摘要", "字段映射可能与实际 API 不完全一致"],
            "deliverables": ["题录核对表", "开放全文", "实验记录"],
        },
        "code_scaffold": "# TODO: 配置开放获取全文或模型后生成复现代码骨架。\n",
        "slides": ["研究问题", "方法概览", "关键结果", "局限与启示"],
        "quiz": [],
        "course_case": "待生成：确认来源与全文后映射到课程知识点。",
    }


def _build_request(source_id: str, value: dict, secrets: dict, query: str, limit: int) -> dict:
    params = dict(value.get("extra_params") or {})
    query_param = value.get("query_param") or "query"
    limit_param = value.get("limit_param") or "limit"
    params[query_param] = query
    if limit_param:
        params[limit_param] = limit
    account = str(value.get("account") or "").strip()
    account_param = str(value.get("account_param") or "").strip()
    if account and account_param:
        params[account_param] = account
    headers = {
        "Accept": "application/json",
        "User-Agent": f"PaperPilot/{config.APP_VERSION} (custom source)",
        **(value.get("headers") or {}),
    }
    token = str(secrets.get("token") or "").strip()
    auth_type = value.get("auth_type") or "none"
    auth_name = str(value.get("auth_name") or "").strip()
    if token:
        if auth_type == "bearer":
            headers["Authorization"] = f"Bearer {token}"
        elif auth_type == "header":
            headers[auth_name or "Authorization"] = token
        elif auth_type == "query":
            params[auth_name or "api_key"] = token
    auth = None
    if auth_type == "basic":
        auth = (str(value.get("account") or ""), str(secrets.get("password") or ""))
    request = {
        "method": value.get("method") or "GET",
        "url": value["base_url"],
        "params": {} if request_method_post(value) else params,
        "json": params if request_method_post(value) else None,
        "headers": headers,
        "auth": auth,
        "timeout": DEFAULT_TIMEOUT,
    }
    return request


def request_method_post(value: dict) -> bool:
    return str(value.get("method") or "GET").upper() == "POST"


def _default_fetcher(request: dict) -> dict:
    try:
        netguard.validate_url(request["url"])
    except netguard.UnsafeURLError as exc:
        raise CustomSourceError(str(exc)) from exc
    try:
        if request["method"].upper() == "POST":
            response = requests.post(
                request["url"],
                json=request.get("json") or {},
                headers=request["headers"],
                auth=request.get("auth"),
                timeout=request.get("timeout") or DEFAULT_TIMEOUT,
            )
        else:
            response = requests.get(
                request["url"],
                params=request.get("params") or {},
                headers=request["headers"],
                auth=request.get("auth"),
                timeout=request.get("timeout") or DEFAULT_TIMEOUT,
            )
        if response.status_code >= 400:
            raise CustomSourceError(f"来源请求失败（HTTP {response.status_code}）。")
        if len(response.content or b"") > MAX_RESPONSE_BYTES:
            raise CustomSourceError("来源返回内容过大，已停止解析。")
        return response.json()
    except CustomSourceError:
        raise
    except requests.exceptions.RequestException as exc:
        raise CustomSourceError("来源请求失败，请检查网络、地址或认证配置。") from exc
    except ValueError as exc:
        raise CustomSourceError("来源返回的不是有效 JSON。") from exc


def _cache_path(source_id: str, value: dict, query: str, limit: int, cache_dir: Path | None) -> Path:
    root = Path(cache_dir) if cache_dir else config.CACHE_DIR / "search"
    root.mkdir(parents=True, exist_ok=True)
    signature = hashlib.sha1(
        json.dumps(
            {
                "id": source_id,
                "base_url": value.get("base_url"),
                "params": value.get("extra_params"),
                "field_map": value.get("field_map"),
                "query": query,
                "limit": limit,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:20]
    return root / f"custom-{_slug(source_id)}-{signature}.json"


def search(
    source_id: str,
    query: str,
    limit: int = 8,
    *,
    fetcher=None,
    cache_dir: Path | None = None,
) -> list[dict]:
    private = get_private(source_id)
    if not private:
        raise CustomSourceError("自定义来源不存在。")
    value = private["config"]
    if not value.get("enabled", True):
        raise CustomSourceError("自定义来源已停用。")
    path = _cache_path(source_id, value, query, limit, cache_dir)
    now = time.time()
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if now - float(cached.get("fetched_at") or 0) <= CACHE_TTL:
                papers = cached.get("papers") or []
                return [{**paper, "cached": True, "cache_age_minutes": int((now - float(cached["fetched_at"])) // 60)} for paper in papers]
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    request = _build_request(source_id, value, private["secrets"], query, limit)
    payload = (fetcher or _default_fetcher)(request)
    if isinstance(payload, dict):
        root = _extract(payload, value.get("result_path") or "")
        if root is None:
            root = payload.get("results") or payload.get("data") or payload.get("items") or []
    else:
        root = payload
    if not isinstance(root, list):
        raise CustomSourceError("来源响应没有找到结果列表，请检查 result_path。")
    papers = []
    for item in root[: max(1, limit)]:
        paper = _paper_from_item(item, source_id, value.get("name") or "自定义来源")
        if paper:
            papers.append(paper)
    try:
        path.write_text(
            json.dumps({"fetched_at": now, "papers": papers}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
    return papers


def test_connection(source_id: str, *, fetcher=None, cache_dir: Path | None = None) -> dict:
    started = time.perf_counter()
    papers = search(source_id, "machine learning", 3, fetcher=fetcher, cache_dir=cache_dir)
    return {
        "ok": True,
        "count": len(papers),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "titles": [paper.get("title") for paper in papers[:3]],
    }
