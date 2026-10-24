from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
from datetime import datetime
from pathlib import Path

import requests

from .. import config
from ..demo_data import GLOSSARY
from ..glossary import GLOSSARY_CATEGORY_OF
from . import netguard

KNOWN_CATEGORIES = {
    "大模型与推理",
    "机器学习基础",
    "芯片与存算",
    "数学与统计",
    "系统与性能",
    "科研与写作",
    "开放科学",
    "在线扩展",
}
EXT_CATEGORY = "在线扩展"
MAX_PACK_BYTES = 5 * 1024 * 1024
MAX_TERMS = 20000

PRESETS: dict[str, dict] = {
    "jiqizhixin_aitd": {
        "id": "jiqizhixin_aitd",
        "name": "机器之心 AI 术语库",
        "url": "https://api.github.com/repos/jiqizhixin/Artificial-Intelligence-Terminology/contents/data/All.md?ref=master",
        "format": "aitd_markdown",
        "license": "CC BY-NC-SA 4.0",
        "attribution": "机器之心 / SyncedAI00",
        "homepage": "https://github.com/jiqizhixin/Artificial-Intelligence-Terminology",
        "note": "约 2442 条英中 AI 术语；非商业署名-相同方式共享许可，导入后仅保存在当前实例。",
    }
}


def _pack_dir() -> Path:
    override = os.getenv("PAPERPILOT_GLOSSARY_DIR")
    path = Path(override) if override else config.DATA_DIR / "glossaries"
    path.mkdir(parents=True, exist_ok=True)
    return path


def clear_cache() -> None:
    return None


def _slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", value or "").strip("-").lower()
    return text[:40] or "pack"


def builtin_items() -> list[dict]:
    return [
        {
            "term": term,
            "zh": zh,
            "category": GLOSSARY_CATEGORY_OF.get(term, "芯片与存算"),
            "source": "内置术语",
            "pack_id": "builtin",
            "aliases": [],
        }
        for term, zh in GLOSSARY.items()
    ]


def _clean_text(value) -> str:
    text = str(value or "").strip()
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return " ".join(text.split())[:300]


def _normalize_term(raw: dict, source_name: str = "") -> dict | None:
    term = _clean_text(
        raw.get("term")
        or raw.get("english")
        or raw.get("en")
        or raw.get("name")
        or raw.get("word")
    )
    zh = _clean_text(
        raw.get("zh")
        or raw.get("chinese")
        or raw.get("cn")
        or raw.get("translation")
        or raw.get("中文")
        or raw.get("中文翻译")
    )
    if not term or not zh or len(term) > 180 or len(zh) > 300:
        return None
    category = _clean_text(raw.get("category") or raw.get("类别"))
    if category not in KNOWN_CATEGORIES:
        category = EXT_CATEGORY
    aliases = raw.get("aliases") or raw.get("alias") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    aliases = [_clean_text(item) for item in aliases if _clean_text(item)][:8]
    return {
        "term": term,
        "zh": zh,
        "category": category,
        "source": source_name,
        "aliases": aliases,
    }


def _dedupe(items: list[dict]) -> list[dict]:
    result = []
    seen = set()
    for item in items:
        key = item["term"].strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= MAX_TERMS:
            break
    return result


def _parse_json(text: str, source_name: str) -> list[dict]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("JSON 术语文件无法解析。") from exc
    if isinstance(payload, dict):
        if isinstance(payload.get("items"), list):
            rows = payload["items"]
        elif isinstance(payload.get("terms"), list):
            rows = payload["terms"]
        elif isinstance(payload.get("data"), list):
            rows = payload["data"]
        else:
            rows = [
                {"term": term, "zh": zh}
                for term, zh in payload.items()
                if isinstance(zh, str)
            ]
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError("JSON 术语文件需要对象或数组。")
    result = []
    for row in rows:
        if isinstance(row, dict):
            item = _normalize_term(row, source_name)
            if item:
                result.append(item)
    return _dedupe(result)


def _header_index(header: list[str]) -> dict:
    normalized = [cell.strip().lower() for cell in header]
    mapping = {}
    for index, cell in enumerate(normalized):
        if index is None:
            continue
        if any(key in cell for key in ("english", "英文", "term", "word", "name")):
            mapping.setdefault("term", index)
        elif any(key in cell for key in ("chinese", "中文", "zh", "translation", "汉语")):
            mapping.setdefault("zh", index)
        elif any(key in cell for key in ("category", "分类", "类别")):
            mapping.setdefault("category", index)
        elif "alias" in cell or "缩写" in cell:
            mapping.setdefault("aliases", index)
    return mapping


def _parse_aitd(text: str, source_name: str) -> list[dict]:
    result = []
    for line in text.splitlines():
        if "AITD-" not in line or "|" not in line:
            continue
        parts = line.strip().strip("|").split("|")
        if len(parts) < 3:
            continue
        term = _clean_text(parts[1])
        zh = _clean_text(parts[2])
        aliases = _clean_text(parts[3]) if len(parts) > 3 else ""
        item = _normalize_term(
            {
                "term": term,
                "zh": zh,
                "category": EXT_CATEGORY,
                "aliases": [aliases] if aliases else [],
            },
            source_name,
        )
        if item:
            result.append(item)
    return _dedupe(result)


def _parse_table(text: str, source_name: str) -> list[dict]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    delimiter = "\t" if "\t" in lines[0] else ","
    reader = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))
    if not reader:
        return []
    header = reader[0]
    mapping = _header_index(header)
    start = 1 if mapping else 0
    if not mapping:
        mapping = {"term": 0, "zh": 1, "category": 2} if len(header) >= 2 else {"term": 0, "zh": 1}
    result = []
    for row in reader[start:]:
        if not row:
            continue
        raw = {}
        for field, index in mapping.items():
            if index < len(row):
                raw[field] = row[index]
        item = _normalize_term(raw, source_name)
        if item:
            result.append(item)
    return _dedupe(result)


def _parse_markdown_table(text: str, source_name: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or all(set(cell) <= set("-: ") for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        return []
    mapping = _header_index(rows[0])
    start = 1 if mapping else 0
    if not mapping:
        mapping = {"term": 0, "zh": 1, "category": 2} if len(rows[0]) >= 2 else {"term": 0, "zh": 1}
    result = []
    for row in rows[start:]:
        raw = {}
        for field, index in mapping.items():
            if index < len(row):
                raw[field] = row[index]
        item = _normalize_term(raw, source_name)
        if item:
            result.append(item)
    return _dedupe(result)


def parse_glossary(text: str, source_name: str = "") -> list[dict]:
    source = str(text or "").lstrip("\ufeff").strip()
    if not source:
        return []
    if source.startswith("{") or source.startswith("["):
        return _parse_json(source, source_name)
    first_lines = "\n".join(source.splitlines()[:8])
    if "AITD-" in source or "英文名称" in first_lines:
        return _parse_aitd(source, source_name)
    if "|" in first_lines:
        return _parse_markdown_table(source, source_name)
    if "\t" in first_lines or "," in first_lines:
        return _parse_table(source, source_name)
    result = []
    for line in source.splitlines():
        parts = re.split(r"\s{2,}|\s+[-—]\s+", line.strip(), maxsplit=1)
        if len(parts) != 2:
            continue
        item = _normalize_term({"term": parts[0], "zh": parts[1]}, source_name)
        if item:
            result.append(item)
    return _dedupe(result)


def _pack_path(pack_id: str) -> Path:
    return _pack_dir() / (_slug(pack_id) + ".json")


def _load_pack(pack_id: str) -> dict | None:
    path = _pack_path(pack_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("terms"), list):
        return None
    return data


def _save_pack(pack: dict) -> None:
    path = _pack_path(pack["id"])
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _summary(pack: dict) -> dict:
    return {
        "id": pack.get("id"),
        "name": pack.get("name"),
        "count": len(pack.get("terms") or []),
        "enabled": bool(pack.get("enabled", True)),
        "source_url": pack.get("source_url") or "",
        "license": pack.get("license") or "",
        "attribution": pack.get("attribution") or "",
        "homepage": pack.get("homepage") or "",
        "format": pack.get("format") or "auto",
        "imported_at": pack.get("imported_at") or "",
        "built_in": False,
    }


def list_packs() -> dict:
    packs = []
    for path in sorted(_pack_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("terms"), list):
            packs.append(_summary(data))
    packs.sort(key=lambda item: item.get("imported_at") or "", reverse=True)
    installed = {item.get("id") for item in packs}
    presets = [
        {**preset, "installed": f"preset-{preset['id']}" in installed}
        for preset in PRESETS.values()
    ]
    builtin = {
        "id": "builtin",
        "name": "PaperPilot 内置术语",
        "count": len(builtin_items()),
        "enabled": True,
        "source_url": "",
        "license": "项目内置",
        "attribution": "PaperPilot",
        "homepage": "",
        "format": "builtin",
        "imported_at": "",
        "built_in": True,
    }
    return {"builtin": builtin, "packs": packs, "presets": presets}


def import_text(
    filename: str,
    content: bytes,
    *,
    name: str,
    license: str = "",
    category: str = "",
    source_url: str = "",
    attribution: str = "本地导入",
) -> dict:
    if len(content or b"") > MAX_PACK_BYTES:
        raise ValueError("术语包不能超过 5 MB。")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("gb18030")
        except UnicodeDecodeError as exc:
            raise ValueError("术语文件需要 UTF-8 或 GB18030 编码。") from exc
    terms = parse_glossary(text, source_name=name)
    if not terms:
        raise ValueError("没有解析到术语；请检查列名或 JSON 字段。")
    if category and category in KNOWN_CATEGORIES:
        for item in terms:
            if item["category"] == EXT_CATEGORY:
                item["category"] = category
    pack_name = (name or Path(filename).stem).strip()[:80]
    pack_id = f"pack-{_slug(pack_name)}-{hashlib.sha1(pack_name.lower().encode('utf-8')).hexdigest()[:6]}"
    existing = _load_pack(pack_id) or {}
    pack = {
        "version": 1,
        "id": pack_id,
        "name": pack_name,
        "terms": terms,
        "enabled": existing.get("enabled", True),
        "source_url": source_url,
        "license": license or "自定义/未标注",
        "attribution": attribution,
        "homepage": "",
        "format": Path(filename).suffix.lower().lstrip(".") or "auto",
        "imported_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _save_pack(pack)
    return _summary(pack)


def _decode_github_json(data: bytes) -> bytes:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return data
    if isinstance(payload, dict) and payload.get("encoding") == "base64" and payload.get("content"):
        try:
            return base64.b64decode(payload["content"])
        except (ValueError, TypeError):
            return data
    return data


def _default_fetcher(url: str, headers: dict, timeout: int) -> bytes:
    try:
        netguard.validate_url(url)
    except netguard.UnsafeURLError as exc:
        raise ValueError(str(exc)) from exc
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.exceptions.RequestException as exc:
        raise ValueError("在线术语库下载失败，请检查网络或地址。") from exc
    if response.status_code >= 400:
        raise ValueError(f"在线术语库返回 HTTP {response.status_code}。")
    if len(response.content or b"") > MAX_PACK_BYTES:
        raise ValueError("在线术语库超过 5 MB，已停止下载。")
    return _decode_github_json(response.content or b"")


def fetch_online(
    preset_id: str | None = None,
    *,
    url: str | None = None,
    name: str | None = None,
    license: str | None = None,
    attribution: str | None = None,
    fetcher=None,
) -> dict:
    preset = PRESETS.get(preset_id or "")
    target_url = url or (preset or {}).get("url")
    if not target_url:
        raise ValueError("需要提供在线术语库预设或 URL。")
    try:
        netguard.validate_url(target_url)
    except netguard.UnsafeURLError as exc:
        raise ValueError(f"在线术语库地址不符合安全要求：{exc}") from exc
    headers = {
        "Accept": "application/vnd.github.raw+json, application/vnd.github+json;q=0.9, text/plain;q=0.8, application/json;q=0.7",
        "User-Agent": f"PaperPilot/{config.APP_VERSION} (glossary)",
    }
    fetch = fetcher or _default_fetcher
    content = fetch(target_url, headers, 20)
    if len(content or b"") > MAX_PACK_BYTES:
        raise ValueError("在线术语库超过 5 MB，已停止下载。")
    pack_name = (name or (preset or {}).get("name") or "在线术语库").strip()[:80]
    pack_id = (
        f"preset-{preset['id']}"
        if preset
        else f"online-{_slug(pack_name)}-{hashlib.sha1(target_url.encode('utf-8')).hexdigest()[:6]}"
    )
    terms = parse_glossary(content.decode("utf-8-sig", errors="replace"), source_name=pack_name)
    if not terms:
        raise ValueError("在线文件没有解析到术语；当前支持 JSON、CSV/TSV 与 Markdown 表格。")
    existing = _load_pack(pack_id) or {}
    pack = {
        "version": 1,
        "id": pack_id,
        "name": pack_name,
        "terms": terms,
        "enabled": existing.get("enabled", True),
        "source_url": target_url,
        "license": license or (preset or {}).get("license") or "自定义/未标注",
        "attribution": attribution or (preset or {}).get("attribution") or pack_name,
        "homepage": (preset or {}).get("homepage") or "",
        "format": (preset or {}).get("format") or "auto",
        "imported_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _save_pack(pack)
    return _summary(pack)


def set_enabled(pack_id: str, enabled: bool) -> dict | None:
    pack = _load_pack(pack_id)
    if not pack:
        return None
    pack["enabled"] = bool(enabled)
    _save_pack(pack)
    return _summary(pack)


def delete_pack(pack_id: str) -> bool:
    path = _pack_path(pack_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def merged_items(include_disabled: bool = False) -> list[dict]:
    result = list(builtin_items())
    seen = {item["term"].strip().lower() for item in result}
    packs = []
    for path in sorted(_pack_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("terms"), list):
            continue
        if not include_disabled and not data.get("enabled", True):
            continue
        packs.append(data)
    packs.sort(key=lambda item: item.get("imported_at") or "")
    for pack in packs:
        for raw in pack.get("terms") or []:
            item = _normalize_term(raw, pack.get("name") or "扩展术语包")
            if not item:
                continue
            key = item["term"].strip().lower()
            if key in seen:
                continue
            seen.add(key)
            result.append({**item, "pack_id": pack.get("id")})
            if len(result) >= MAX_TERMS:
                return result
    return result


def merged_glossary() -> dict[str, str]:
    return {item["term"]: item["zh"] for item in merged_items()}
