from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

import requests

from .. import config
from ..llm import LLMUnavailable, extract_json
from . import glossary_store, llm_gateway

TARGETS = {
    "zh": {"label": "简体中文", "code": "zh-CN"},
    "en": {"label": "English", "code": "en"},
}
PROVIDER_LABELS = {
    "llm": "模型翻译",
    "public": "公共翻译接口",
    "none": "无需翻译",
}
PUBLIC_PREVIEW_CHARS = int(os.getenv("PAPERPILOT_TRANSLATE_PUBLIC_MAX", "7000"))
PUBLIC_CHUNK_CHARS = 400
LLM_CHUNK_CHARS = 1800
PUBLIC_URL = "https://api.mymemory.translated.net/get"

_PROTECT_RE = re.compile(
    r"https?://[^\s<>\"']+"
    r"|\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b"
    r"|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"
    r"|\[\d+(?:\s*[,;，、\-–]\s*\d+)*\]"
    r"|\$[^$\n]{1,300}\$"
    r"|\\\([^\n]{1,300}\\\)"
    r"|\\\[[^\n]{1,300}\\\]"
    r"|`[^`\n]{1,200}`"
)
_LOCK = threading.RLock()


class TranslationError(RuntimeError):
    pass


class ProviderUnavailable(TranslationError):
    pass


def _target_meta(target: str) -> dict:
    if target not in TARGETS:
        raise TranslationError(f"不支持的翻译目标：{target}")
    return TARGETS[target]


def _clip_text(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text[:limit].rstrip(" ,;，；") + "…" if len(text) > limit else text


def section_catalog(paper: dict) -> list[dict]:
    sections = [
        item for item in (paper.get("sections") or [])
        if (item.get("text") or "").strip()
    ]
    abstract = (paper.get("abstract") or "").strip()
    abstract_section = next(
        (item for item in sections if (item.get("title") or "").strip().lower().startswith("abstract")),
        None,
    )
    if abstract_section and (abstract_section.get("text") or "").strip():
        abstract = (abstract_section.get("text") or "").strip()
    catalog: list[dict] = []
    seen: set[str] = set()
    if abstract:
        key = re.sub(r"\W+", "", abstract.lower())[:160]
        if key:
            seen.add(key)
            catalog.append(
                {
                    "title": (abstract_section or {}).get("title") or "Abstract",
                    "page": (abstract_section or {}).get("page") or 1,
                    "text": abstract,
                }
            )
    for item in sections:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        if (item.get("title") or "").strip().lower().startswith("abstract"):
            continue
        key = re.sub(r"\W+", "", text.lower())[:160]
        if key in seen:
            continue
        seen.add(key)
        catalog.append(
            {
                "title": item.get("title") or "正文",
                "page": item.get("page") or 1,
                "text": text,
            }
        )
    for index, item in enumerate(catalog):
        item["index"] = index
        item["source_chars"] = len(item["text"])
    return catalog


def shield_text(text: str) -> tuple[str, list[str]]:
    tokens: list[str] = []

    def replace(match: re.Match) -> str:
        tokens.append(match.group(0))
        return f"[[{len(tokens) - 1}]]"

    return _PROTECT_RE.sub(replace, text or ""), tokens


def restore_text(text: str, tokens: list[str]) -> tuple[str, int]:
    restored = text or ""
    missing = 0
    for index, token in enumerate(tokens):
        placeholder = f"[[{index}]]"
        if placeholder not in restored:
            missing += 1
            continue
        restored = restored.replace(placeholder, token)
    return restored, missing


def _split_long(text: str, limit: int) -> list[str]:
    pieces: list[str] = []
    remainder = text.strip()
    while len(remainder) > limit:
        cut = remainder.rfind(" ", int(limit * 0.55), limit)
        if cut <= 0:
            cut = limit
        pieces.append(remainder[:cut].strip())
        remainder = remainder[cut:].strip()
    if remainder:
        pieces.append(remainder)
    return pieces


def split_text(text: str, limit: int) -> list[str]:
    text = " ".join((text or "").split())
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    units = [part.strip() for part in re.split(r"(?<=[.!?;:])\s+", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    for unit in units:
        if len(unit) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long(unit, limit))
            continue
        candidate = (current + " " + unit).strip()
        if current and len(candidate) > limit:
            chunks.append(current)
            current = unit
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk]


def _preview(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    window = text[:limit]
    cut = max(window.rfind("。"), window.rfind("！"), window.rfind("？"), window.rfind(". "))
    if cut >= int(limit * 0.55):
        return text[: cut + 1].strip(), True
    cut = window.rfind(" ")
    return (text[:cut].strip() if cut > 0 else window).strip(), True


def _cjk_ratio(text: str) -> float:
    content = "".join(char for char in text or "" if not char.isspace())
    if not content:
        return 0.0
    return len(re.findall(r"[\u4e00-\u9fff]", content)) / len(content)


def _detect_source(text: str) -> str:
    return "zh" if _cjk_ratio(text) >= 0.25 else "en"


def _cache_path(cache_dir: Path | None, paper_id: str, target: str) -> Path:
    root = cache_dir or Path(os.getenv("PAPERPILOT_TRANSLATION_DIR") or (config.DATA_DIR / "translations"))
    root.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", paper_id or "paper")[:100]
    return root / f"{safe}-{target}.json"


def _read_cache(cache_dir: Path | None, paper_id: str, target: str) -> dict:
    empty = {"version": 1, "paper_id": paper_id, "target": target, "sections": {}}
    try:
        path = _cache_path(cache_dir, paper_id, target)
        if not path.exists():
            return empty
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data.get("sections"), dict):
        data["sections"] = {}
    return data


def _write_cache(cache_dir: Path | None, paper_id: str, target: str, data: dict) -> None:
    try:
        path = _cache_path(cache_dir, paper_id, target)
        data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        temp = path.with_suffix(".tmp")
        with _LOCK:
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)
    except OSError:
        # Translation must still succeed on read-only deployments.
        return


def _source_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:20]


def _glossary_hits(text: str, limit: int = 30) -> list[dict]:
    lowered = (text or "").lower()
    hits = [
        {"term": term, "zh": zh}
        for term, zh in glossary_store.merged_glossary().items()
        if term.lower() in lowered
    ]
    hits.sort(key=lambda item: (-len(item["term"]), item["term"]))
    return hits[:limit]


def _mymemory(text: str, source_lang: str, target_lang: str) -> str:
    langpair = f"{source_lang}|{target_lang}"
    params = {"q": text, "langpair": langpair}
    email = (os.getenv("PAPERPILOT_TRANSLATE_EMAIL") or "").strip()
    if email:
        params["de"] = email
    try:
        response = requests.get(
            PUBLIC_URL,
            params=params,
            timeout=(5, 20),
            headers={
                "User-Agent": (
                    f"PaperPilot/{config.APP_VERSION} "
                    f"(mailto:{email or config.CONTACT_EMAIL})"
                )
            },
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise ProviderUnavailable(f"公共翻译接口不可用：{exc}") from exc
    if payload.get("quotaFinished"):
        raise ProviderUnavailable("公共翻译接口今日额度已用完；可配置模型后继续全文翻译。")
    status = (payload.get("responseStatus") or 200)
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = 500
    translated = ((payload.get("responseData") or {}).get("translatedText") or "").strip()
    if status >= 400 or not translated:
        message = payload.get("responseDetails") or payload.get("responseData", {}).get("match") or "没有返回译文"
        raise ProviderUnavailable(f"公共翻译接口返回异常：{message}")
    return translated


def _resolved_provider(provider: str, llm_call) -> str:
    if provider == "auto":
        return "llm" if (config.LLM_ENABLED or llm_call is not None) else "public"
    if provider not in {"llm", "public"}:
        raise TranslationError(f"不支持的翻译通道：{provider}")
    if provider == "llm" and not config.LLM_ENABLED and llm_call is None:
        raise TranslationError("未配置模型，无法进行完整全文翻译。")
    return provider


def _default_llm_call(system: str, user: str):
    """Full-document translation through the audited gateway.

    The gateway never raises for a degraded call, so the failure is turned back
    into ``LLMUnavailable`` — which the caller maps to a Chinese 400 instead of a
    raw transport traceback and a 500.
    """
    data, meta = llm_gateway.audited_chat_json(
        system, user, purpose="translation_full", temperature=0.05, max_tokens=4000,
    )
    if not isinstance(data, dict):
        reason = str(meta.get("degrade_reason") or meta.get("error") or "")
        raise LLMUnavailable("模型未能返回可用译文" + (f"（{reason}）" if reason else "。"))
    return data


def _translate_public(
    text: str,
    target: str,
    fetch,
    max_chars: int,
) -> dict:
    source_lang = _detect_source(text)
    target_code = _target_meta(target)["code"]
    preview, truncated = _preview(text, max_chars)
    if source_lang == ("zh" if target == "zh" else "en"):
        return {
            "translation": text,
            "segments": [{"source": text, "translation": text}],
            "truncated": False,
            "translated_source_chars": len(text),
            "provider": "none",
            "missing_tokens": 0,
            "note": "原文已经是目标语言，未调用翻译。",
        }
    shielded, tokens = shield_text(preview)
    limit = 260 if source_lang == "zh" else PUBLIC_CHUNK_CHARS
    chunks = split_text(shielded, limit)
    translated_chunks = []
    source_chunks = []
    for chunk in chunks:
        translated = fetch(chunk, source_lang, target_code)
        translated_chunks.append(translated)
        source_chunks.append(chunk)
    joined = " ".join(translated_chunks).strip()
    restored_translation, missing = restore_text(joined, tokens)
    restored_sources = [restore_text(chunk, tokens)[0] for chunk in source_chunks]
    restored_chunk_translations = [
        restore_text(chunk, tokens)[0] for chunk in translated_chunks
    ]
    note = "公共翻译接口按额度翻译"
    if truncated:
        note += f"，单节最多 {max_chars} 字符；超出部分未翻译"
    if missing:
        note += f"；{missing} 个公式/引用占位符未在译文中保留"
    return {
        "translation": restored_translation,
        "segments": [
            {"source": source, "translation": translated}
            for source, translated in zip(restored_sources, restored_chunk_translations)
        ],
        "truncated": truncated,
        "translated_source_chars": len(preview),
        "provider": "public",
        "missing_tokens": missing,
        "note": note,
    }


def _translate_llm(
    text: str,
    target: str,
    llm_call,
) -> dict:
    source_lang = _detect_source(text)
    if source_lang == ("zh" if target == "zh" else "en"):
        return {
            "translation": text,
            "segments": [{"source": text, "translation": text}],
            "truncated": False,
            "translated_source_chars": len(text),
            "provider": "none",
            "missing_tokens": 0,
            "note": "原文已经是目标语言，未调用模型。",
        }
    shielded, tokens = shield_text(text)
    chunks = split_text(shielded, LLM_CHUNK_CHARS)
    glossary = _glossary_hits(text, 20)
    glossary_text = "；".join(f"{item['term']} → {item['zh']}" for item in glossary) or "无"
    target_label = _target_meta(target)["label"]
    system = (
        "你是严谨的学术论文翻译。把给定段落翻译成" + target_label + "。"
        "保留公式、变量、引用、数字、单位、URL 和 [[数字]] 占位符，不得改写或删除占位符。"
        "使用给定术语表；不添加解释，不扩写，不省略。输出 JSON："
        '{"segments":[{"id":0,"translation":"译文"}]}。'
    )
    body = "\n\n".join(f"[{index}]\n{chunk}" for index, chunk in enumerate(chunks))
    user = f"术语表：{glossary_text}\n\n待翻译段落：\n{body}"
    raw = llm_call(system, user)
    if isinstance(raw, dict):
        payload = raw
    else:
        payload = extract_json(str(raw))
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise TranslationError("模型没有返回可解析的段落译文。")
    by_id = {}
    for item in payload["segments"]:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        translated = item.get("translation") or item.get("zh") or item.get("text") or ""
        if isinstance(translated, str) and translated.strip():
            by_id[index] = translated.strip()
    if set(by_id) != set(range(len(chunks))):
        raise TranslationError("模型返回的段落数量不完整，请重试。")
    translations = []
    sources = []
    missing_total = 0
    for index, chunk in enumerate(chunks):
        translated, missing = restore_text(by_id[index], tokens)
        missing_total += missing
        translations.append(translated)
        sources.append(restore_text(chunk, tokens)[0])
    joined = " ".join(translations).strip()
    note = "模型翻译；公式与引用占位符已保护，仍建议对照原文核对。"
    if missing_total:
        note += f" 有 {missing_total} 个占位符未保留。"
    return {
        "translation": joined,
        "segments": [
            {"source": source, "translation": translation}
            for source, translation in zip(sources, translations)
        ],
        "truncated": False,
        "translated_source_chars": len(text),
        "provider": "llm",
        "missing_tokens": missing_total,
        "note": note,
    }


def _provider_model(provider: str) -> str | None:
    return config.LLM_MODEL if provider == "llm" else None


def _cache_valid(
    entry: dict | None,
    source: str,
    provider: str,
    target: str,
    public_max_chars: int,
) -> bool:
    if not entry:
        return False
    if entry.get("source_hash") != _source_hash(source):
        return False
    if entry.get("provider") != provider:
        return False
    if provider == "llm" and entry.get("model") != config.LLM_MODEL:
        return False
    if provider == "public" and int(entry.get("public_max_chars") or 0) != int(public_max_chars):
        return False
    return entry.get("target") == target


def _cached_payload(entry: dict, index: int, section: dict, target: str, paper_id: str) -> dict:
    return {
        "paper_id": paper_id,
        "index": index,
        "title": section["title"],
        "page": section["page"],
        "target": target,
        "target_label": _target_meta(target)["label"],
        "provider": entry.get("provider"),
        "provider_label": PROVIDER_LABELS.get(entry.get("provider"), "翻译"),
        "model": entry.get("model"),
        "source_chars": len(section["text"]),
        "translated_source_chars": entry.get("translated_source_chars", 0),
        "truncated": bool(entry.get("truncated")),
        "translation": entry.get("translation", ""),
        "segments": entry.get("segments") or [],
        "checks": entry.get("checks") or {},
        "note": entry.get("note", ""),
        "translated_at": entry.get("translated_at", ""),
        "cached": True,
    }


def translate_section(
    paper: dict,
    index: int,
    target: str = "zh",
    force: bool = False,
    provider: str = "auto",
    cache_dir: Path | None = None,
    public_fetch=None,
    llm_call=None,
    public_max_chars: int | None = None,
) -> dict:
    _target_meta(target)
    catalog = section_catalog(paper)
    if index < 0 or index >= len(catalog):
        raise TranslationError(f"章节序号超出范围：{index}")
    section = catalog[index]
    source = section["text"]
    provider = _resolved_provider(provider, llm_call)
    max_chars = PUBLIC_PREVIEW_CHARS if public_max_chars is None else max(1, int(public_max_chars))
    cache = _read_cache(cache_dir, paper.get("id") or "paper", target)
    entry = cache["sections"].get(str(index))
    if not force and _cache_valid(entry, source, provider, target, max_chars):
        return _cached_payload(entry, index, section, target, paper.get("id") or "paper")

    if provider == "public":
        result = _translate_public(
            source,
            target,
            public_fetch or _mymemory,
            max_chars,
        )
    else:
        if llm_call is None:
            try:
                result = _translate_llm(source, target, _default_llm_call)
            except LLMUnavailable as exc:
                raise TranslationError(str(exc)) from exc
        else:
            result = _translate_llm(source, target, llm_call)

    if result.get("provider") == "none":
        provider = "none"
    numbers_source = re.findall(r"\d+(?:\.\d+)?", source)
    numbers_translation = re.findall(r"\d+(?:\.\d+)?", result["translation"])
    checks = {
        "numbers_in_source": len(numbers_source),
        "numbers_in_translation": len(numbers_translation),
        "numbers_preserved": all(number in numbers_translation for number in numbers_source),
        "missing_tokens": result.get("missing_tokens", 0),
    }
    payload = {
        "paper_id": paper.get("id") or "paper",
        "index": index,
        "title": section["title"],
        "page": section["page"],
        "target": target,
        "target_label": _target_meta(target)["label"],
        "provider": provider,
        "provider_label": PROVIDER_LABELS.get(provider, "翻译"),
        "model": _provider_model(provider),
        "source_chars": len(source),
        "translated_source_chars": result["translated_source_chars"],
        "truncated": result["truncated"],
        "translation": result["translation"],
        "segments": result["segments"],
        "checks": checks,
        "note": result.get("note", ""),
        "translated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cached": False,
    }
    entry = {
        "source_hash": _source_hash(source),
        "target": target,
        "provider": provider,
        "model": _provider_model(provider),
        "public_max_chars": max_chars,
        "translation": payload["translation"],
        "segments": payload["segments"],
        "truncated": payload["truncated"],
        "translated_source_chars": payload["translated_source_chars"],
        "checks": payload["checks"],
        "note": payload["note"],
        "translated_at": payload["translated_at"],
    }
    cache["sections"][str(index)] = entry
    _write_cache(cache_dir, paper.get("id") or "paper", target, cache)
    return payload


def provider_status() -> dict:
    public_max = PUBLIC_PREVIEW_CHARS
    return {
        "auto": "llm" if config.LLM_ENABLED else "public",
        "llm": {
            "enabled": config.LLM_ENABLED,
            "label": PROVIDER_LABELS["llm"],
            "model": config.LLM_MODEL if config.LLM_ENABLED else "",
            "full": True,
            "note": "配置 OpenAI-compatible 模型后可翻译完整章节，并使用术语表保护公式/引用。",
        },
        "public": {
            "enabled": True,
            "label": PROVIDER_LABELS["public"],
            "full": False,
            "preview_chars": public_max,
            "note": f"无需密钥，按节调用公共翻译接口；单节最多 {public_max} 字符，受公共接口每日额度限制。额度耗尽时请稍后再试或配置模型。",
        },
        "targets": [{"id": key, "label": value["label"]} for key, value in TARGETS.items()],
    }


def paper_payload(
    paper: dict,
    target: str = "zh",
    cache_dir: Path | None = None,
    provider: str = "auto",
) -> dict:
    _target_meta(target)
    status = provider_status()
    current = status["auto"] if provider == "auto" else provider
    catalog = section_catalog(paper)
    cache = _read_cache(cache_dir, paper.get("id") or "paper", target)
    sections = []
    translated_sections = 0
    total_source = 0
    total_translated = 0
    for section in catalog:
        entry = cache["sections"].get(str(section["index"]))
        base = {
            "index": section["index"],
            "title": section["title"],
            "page": section["page"],
            "source": section["text"],
            "source_chars": len(section["text"]),
        }
        total_source += len(section["text"])
        if entry and entry.get("source_hash") == _source_hash(section["text"]):
            base.update(
                {
                    "translated": True,
                    "cached": True,
                    "translation": entry.get("translation", ""),
                    "segments": entry.get("segments") or [],
                    "provider": entry.get("provider"),
                    "provider_label": PROVIDER_LABELS.get(entry.get("provider"), "翻译"),
                    "truncated": bool(entry.get("truncated")),
                    "translated_source_chars": entry.get("translated_source_chars", 0),
                    "checks": entry.get("checks") or {},
                    "note": entry.get("note", ""),
                    "translated_at": entry.get("translated_at", ""),
                }
            )
            translated_sections += 1
            total_translated += len(entry.get("translation") or "")
        else:
            base.update(
                {
                    "translated": False,
                    "cached": False,
                    "translation": "",
                    "segments": [],
                    "provider": "",
                    "provider_label": "",
                    "truncated": False,
                    "checks": {},
                    "note": "",
                }
            )
        sections.append(base)
    corpus = " ".join(
        [paper.get("title") or "", paper.get("abstract") or ""]
        + [item["text"] for item in catalog]
    )
    return {
        "paper": {
            "id": paper.get("id"),
            "title": paper.get("title") or "未命名论文",
            "authors": paper.get("authors") or [],
            "year": paper.get("year"),
            "venue": paper.get("venue") or "",
            "source": paper.get("source") or "",
            "full_text": bool(paper.get("full_text")),
            "extraction_level": paper.get("extraction_level") or "摘要级",
        },
        "target": target,
        "target_label": _target_meta(target)["label"],
        "provider": current,
        "provider_label": PROVIDER_LABELS.get(current, "翻译"),
        "providers": status,
        "sections": sections,
        "glossary_hits": _glossary_hits(corpus),
        "translated_sections": translated_sections,
        "total_sections": len(sections),
        "total_source_chars": total_source,
        "total_translated_chars": total_translated,
        "note": (
            "无密钥时使用公共接口逐节翻译，受每日额度限制；配置模型可稳定完成全文翻译。"
            if not config.LLM_ENABLED
            else "模型翻译会保留公式、引用与数字；请对照原文核对术语。"
        ),
    }
