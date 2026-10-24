from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import requests

from .. import config

STATUS_LABELS = {"todo": "待读", "reading": "在读", "done": "已读"}
MAX_TAGS = 8
MAX_TAG_LENGTH = 24
MAX_NOTE_LENGTH = 20000
DOI_CACHE_TTL = 7 * 24 * 3600

_LOCK = threading.RLock()
_DOI_LOCK = threading.Lock()


def _path() -> Path:
    override = os.getenv("PAPERPILOT_LIBRARY_FILE")
    path = Path(override) if override else config.LIBRARY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _empty() -> dict:
    return {"version": 1, "items": {}}


def _load() -> dict:
    path = _path()
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        return _empty()
    return data


def _save(data: dict) -> None:
    path = _path()
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text[:limit] + "…" if len(text) > limit else text


def normalize_tags(tags: list[str] | None) -> list[str]:
    result: list[str] = []
    for raw in tags or []:
        tag = " ".join(str(raw).split()).strip("#,，;；")[:MAX_TAG_LENGTH]
        if tag and tag not in result:
            result.append(tag)
        if len(result) >= MAX_TAGS:
            break
    return result


def _metadata(paper: dict) -> dict:
    return {
        "paper_id": paper.get("id"),
        "title": paper.get("title") or "未命名论文",
        "authors": list(paper.get("authors") or [])[:12],
        "year": paper.get("year"),
        "venue": paper.get("venue") or "",
        "source": paper.get("source") or "",
        "url": paper.get("url") or "",
        "pdf_url": paper.get("pdf_url") or "",
        "doi": paper.get("doi") or "",
        "extraction_level": paper.get("extraction_level") or "摘要级",
        "abstract": _clip(paper.get("abstract") or "", 3000),
        "tldr": _clip(paper.get("tldr") or "", 600),
    }


def save(paper: dict) -> dict:
    if not paper.get("id"):
        raise ValueError("论文缺少 id。")
    with _LOCK:
        data = _load()
        items = data["items"]
        existing = items.get(paper["id"], {})
        now = _now()
        item = {
            **existing,
            **_metadata(paper),
            "status": existing.get("status", "todo"),
            "tags": normalize_tags(existing.get("tags")),
            "note": existing.get("note", ""),
            "added_at": existing.get("added_at", now),
            "updated_at": now,
        }
        items[paper["id"]] = item
        _save(data)
        return item


def get(paper_id: str) -> dict | None:
    with _LOCK:
        return _load()["items"].get(paper_id)


def update(paper_id: str, *, status=None, tags=None, note=None) -> dict | None:
    with _LOCK:
        data = _load()
        item = data["items"].get(paper_id)
        if not item:
            return None
        if status is not None:
            if status not in STATUS_LABELS:
                raise ValueError(f"无效的阅读状态：{status}")
            item["status"] = status
        if tags is not None:
            item["tags"] = normalize_tags(tags)
        if note is not None:
            item["note"] = str(note)[:MAX_NOTE_LENGTH]
        item["updated_at"] = _now()
        data["items"][paper_id] = item
        _save(data)
        return item


def remove(paper_id: str) -> bool:
    with _LOCK:
        data = _load()
        if paper_id not in data["items"]:
            return False
        del data["items"][paper_id]
        _save(data)
        return True


def list_items(query: str = "", status: str | None = None, tag: str | None = None) -> list[dict]:
    with _LOCK:
        items = list(_load()["items"].values())
    if status in STATUS_LABELS:
        items = [item for item in items if item.get("status") == status]
    if tag:
        needle = tag.strip().lower()
        items = [item for item in items if needle in [str(t).lower() for t in item.get("tags", [])]]
    if query.strip():
        needle = query.strip().lower()
        items = [
            item
            for item in items
            if needle in " ".join(
                [
                    str(item.get("title", "")),
                    " ".join(item.get("tags", [])),
                    str(item.get("note", "")),
                    str(item.get("abstract", "")),
                ]
            ).lower()
        ]
    items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return items


def stats() -> dict:
    with _LOCK:
        items = list(_load()["items"].values())
    counts = {key: 0 for key in STATUS_LABELS}
    tag_counts: dict[str, int] = {}
    for item in items:
        status = item.get("status", "todo")
        counts[status] = counts.get(status, 0) + 1
        for tag in item.get("tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    top_tags = sorted(tag_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:8]
    return {
        "total": len(items),
        "statuses": counts,
        "tags": [{"name": name, "count": count} for name, count in top_tags],
    }


def as_paper(item: dict) -> dict:
    abstract = item.get("abstract") or ""
    sections = [{"title": "摘要", "page": 1, "text": abstract}] if abstract else []
    return {
        "id": item.get("paper_id"),
        "title": item.get("title"),
        "authors": item.get("authors", []),
        "year": item.get("year"),
        "venue": item.get("venue"),
        "source": item.get("source") or "library",
        "url": item.get("url"),
        "pdf_url": item.get("pdf_url"),
        "abstract": abstract,
        "tldr": item.get("tldr") or "",
        "sections": sections,
        "full_text": bool(sections),
        "extraction_level": item.get("extraction_level", "摘要级"),
        "demo": False,
        "library": True,
        "metrics": {},
        "results": [],
        "evidence": [],
        "novelty": [],
        "limitations": [],
        "assumptions": [],
        "figures": [],
        "translation": [],
        "reviewer_questions": [],
    }


def _normalize_title(value: str) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def _normalize_doi(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    return text.strip()


def _title_similarity(left: str, right: str) -> float:
    a = _normalize_title(left)
    b = _normalize_title(right)
    if not a or not b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _doi_cache_path(cache_dir: Path, doi: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / (hashlib.sha1(doi.encode("utf-8")).hexdigest()[:24] + ".json")


def _cached_doi_metadata(cache_dir: Path, doi: str) -> dict | None:
    path = _doi_cache_path(cache_dir, doi)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if time.time() - float(data.get("fetched_at") or 0) > DOI_CACHE_TTL:
        return None
    metadata = data.get("metadata")
    return metadata if isinstance(metadata, dict) else None


def _store_doi_metadata(cache_dir: Path, doi: str, metadata: dict) -> None:
    path = _doi_cache_path(cache_dir, doi)
    with _DOI_LOCK:
        try:
            path.write_text(
                json.dumps(
                    {"fetched_at": time.time(), "doi": doi, "metadata": metadata},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass


def _fetch_crossref(doi: str) -> dict | None:
    try:
        response = requests.get(
            f"https://api.crossref.org/works/{doi}",
            headers={"User-Agent": f"PaperPilot/{config.APP_VERSION} (mailto:{config.CONTACT_EMAIL})"},
            timeout=(4, 10),
        )
        response.raise_for_status()
        return (response.json().get("message") or {})
    except Exception:
        return None


def _normalize_doi_metadata(message: dict, doi: str) -> dict:
    titles = message.get("title") or []
    venues = message.get("container-title") or []
    year = None
    issued = message.get("issued") or {}
    parts = issued.get("date-parts") or []
    if parts and parts[0]:
        try:
            year = int(parts[0][0])
        except (TypeError, ValueError, IndexError):
            year = None
    return {
        "doi": _normalize_doi(message.get("DOI") or doi),
        "title": " ".join(str(item) for item in titles if item)[:400],
        "year": year,
        "venue": " ".join(str(item) for item in venues if item)[:240],
    }


def _issue(issue_id: str, label: str, severity: str, action: str) -> dict:
    return {"id": issue_id, "label": label, "severity": severity, "action": action}


def _verify_dois(
    records: list[dict],
    fetcher,
    cache_dir: Path,
    limit: int,
) -> dict[str, dict]:
    candidates = [record for record in records if record.get("doi")][:limit]
    results: dict[str, dict] = {}
    pending: list[dict] = []
    for record in candidates:
        doi = _normalize_doi(record["doi"])
        cached = _cached_doi_metadata(cache_dir, doi)
        if cached is not None:
            results[record["paper_id"]] = {
                "metadata": cached,
                "status": "pending",
                "cached": True,
            }
        else:
            pending.append(record)

    def run(record: dict) -> tuple[str, dict | None]:
        doi = _normalize_doi(record["doi"])
        try:
            metadata = fetcher(doi)
        except Exception:
            metadata = None
        if metadata:
            normalized = _normalize_doi_metadata(metadata, doi)
            _store_doi_metadata(cache_dir, doi, normalized)
            return record["paper_id"], normalized
        return record["paper_id"], None

    if pending:
        with ThreadPoolExecutor(max_workers=min(4, len(pending))) as executor:
            for paper_id, metadata in executor.map(run, pending):
                results[paper_id] = {
                    "metadata": metadata,
                    "status": "pending",
                    "cached": False,
                }
    for record in candidates:
        entry = results.get(record["paper_id"])
        if not entry:
            continue
        metadata = entry.get("metadata")
        if not metadata:
            entry["status"] = "unavailable"
            continue
        similarity = _title_similarity(record.get("title") or "", metadata.get("title") or "")
        entry["similarity"] = round(similarity, 2)
        entry["status"] = "mismatch" if similarity < 0.55 else "verified"
    return results


def health(
    verify_dois: bool = False,
    fetcher=None,
    cache_dir: Path | None = None,
    max_checks: int = 12,
) -> dict:
    with _LOCK:
        records = [dict(item) for item in _load()["items"].values()]
    cache_root = Path(cache_dir) if cache_dir else config.CACHE_DIR / "doi"
    verified = (
        _verify_dois(records, fetcher or _fetch_crossref, cache_root, max_checks)
        if verify_dois
        else {}
    )

    title_groups: dict[str, list[str]] = {}
    doi_groups: dict[str, list[str]] = {}
    for record in records:
        paper_id = record.get("paper_id")
        title_key = _normalize_title(record.get("title") or "")
        if title_key:
            title_groups.setdefault(title_key, []).append(paper_id)
        doi_key = _normalize_doi(record.get("doi") or "")
        if doi_key:
            doi_groups.setdefault(doi_key, []).append(paper_id)

    duplicates: dict[str, str] = {}
    groups: list[dict] = []
    seen_groups: set[frozenset] = set()

    def add_group(kind: str, key: str, ids: list[str], title: str) -> None:
        unique_ids = list(dict.fromkeys(ids))
        if len(unique_ids) < 2:
            return
        signature = frozenset(unique_ids)
        if signature in seen_groups:
            return
        seen_groups.add(signature)
        group_id = f"dup-{len(groups) + 1}"
        groups.append(
            {
                "id": group_id,
                "kind": kind,
                "title": title,
                "paper_ids": unique_ids,
                "suggestion": "保留题录最完整的一条，其余合并或移除，避免重复引用。",
            }
        )
        for paper_id in unique_ids:
            duplicates[paper_id] = group_id

    for key, ids in title_groups.items():
        if len(ids) > 1:
            sample = next(
                (record.get("title") for record in records if record.get("paper_id") == ids[0]),
                key,
            )
            add_group("title", key, ids, sample or key)
    for key, ids in doi_groups.items():
        if len(ids) > 1:
            sample = next(
                (record.get("title") for record in records if record.get("paper_id") == ids[0]),
                key,
            )
            add_group("doi", key, ids, sample or key)

    items = []
    for record in records:
        paper_id = record.get("paper_id")
        title = str(record.get("title") or "").strip()
        authors = record.get("authors") or []
        year = record.get("year")
        venue = str(record.get("venue") or "").strip()
        doi = _normalize_doi(record.get("doi") or "")
        url = str(record.get("url") or "").strip()
        abstract = str(record.get("abstract") or "").strip()
        issues: list[dict] = []
        if not title:
            issues.append(_issue("missing_title", "缺少标题", "high", "补全标题后再导出引用。"))
        if not authors:
            issues.append(_issue("missing_authors", "缺少作者", "medium", "补全作者，避免引用格式不完整。"))
        if not isinstance(year, int):
            issues.append(_issue("missing_year", "缺少年份", "medium", "通过 DOI 或原文补全年份。"))
        elif year < 1900 or year > datetime.now().year + 1:
            issues.append(_issue("year_out_of_range", "年份异常", "medium", "核对出版年份与在线发表年份。"))
        if not venue:
            issues.append(_issue("missing_venue", "缺少来源", "low", "补全期刊/会议名称。"))
        if not doi:
            issues.append(_issue("missing_doi", "缺少 DOI", "medium", "优先使用 DOI 作为可追溯引用键。"))
        if not url:
            issues.append(_issue("missing_url", "缺少链接", "low", "补充 DOI 链接或原文地址。"))
        if not abstract:
            issues.append(_issue("missing_abstract", "缺少摘要", "low", "补摘要便于之后筛选与复核。"))
        if paper_id in duplicates:
            issues.append(
                _issue(
                    "duplicate",
                    "疑似重复条目",
                    "high",
                    f"与同组条目合并，保留题录最完整的一条（{duplicates[paper_id]}）。",
                )
            )
        doi_status = "unchecked"
        doi_metadata = None
        doi_cached = False
        if doi and verify_dois:
            entry = verified.get(paper_id) or {}
            doi_status = entry.get("status") or "unavailable"
            doi_metadata = entry.get("metadata")
            doi_cached = bool(entry.get("cached"))
            if doi_status == "mismatch":
                issues.append(
                    _issue(
                        "doi_mismatch",
                        "DOI 与题录不一致",
                        "high",
                        f"Crossref 返回题名“{(doi_metadata or {}).get('title', '')[:80]}”，请核对。",
                    )
                )
            elif doi_status == "unavailable":
                issues.append(
                    _issue("doi_unavailable", "DOI 暂时无法核验", "low", "检查网络或手动打开 DOI 复核。")
                )
        score = (
            (20 if title else 0)
            + (15 if authors else 0)
            + (10 if isinstance(year, int) and 1900 <= year <= datetime.now().year + 1 else 0)
            + (10 if venue else 0)
            + (15 if doi else 0)
            + (10 if url else 0)
            + (10 if abstract else 0)
            + (5 if paper_id not in duplicates else 0)
            + (5 if doi_status == "verified" else 0)
        )
        score = max(0, min(100, score))
        level = "高" if score >= 80 else "中" if score >= 55 else "低"
        ready = score >= 75 and paper_id not in duplicates and doi_status != "mismatch" and bool(doi or url)
        items.append(
            {
                "paper_id": paper_id,
                "title": title or "未命名记录",
                "authors": authors,
                "year": year,
                "venue": venue,
                "doi": doi,
                "url": url,
                "score": score,
                "level": level,
                "ready": ready,
                "duplicate": paper_id in duplicates,
                "duplicate_group": duplicates.get(paper_id, ""),
                "doi_status": doi_status,
                "doi_metadata": doi_metadata,
                "doi_cached": doi_cached,
                "issues": issues,
            }
        )
    items.sort(key=lambda item: (item["score"], item["title"]))
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    actions: list[str] = []
    for item in items:
        for issue in sorted(item["issues"], key=lambda value: severity_rank.get(value["severity"], 3)):
            action = f"{item['title'][:48]}：{issue['action']}"
            if action not in actions:
                actions.append(action)
    if groups:
        actions.insert(0, f"先处理 {len(groups)} 组重复条目，避免同一文献被多次引用。")
    if not actions:
        actions.append("题录已较完整；写作前仍建议抽查 DOI 与作者顺序。")
    doi_checked = sum(1 for item in items if item["doi"] and verify_dois)
    doi_verified = sum(1 for item in items if item["doi_status"] == "verified")
    doi_mismatch = sum(1 for item in items if item["doi_status"] == "mismatch")
    doi_unavailable = sum(1 for item in items if item["doi_status"] == "unavailable")
    ready_count = sum(1 for item in items if item["ready"])
    average = round(sum(item["score"] for item in items) / len(items), 1) if items else 0
    if not items:
        level = "无记录"
    elif average >= 80:
        level = "较完整"
    elif average >= 55:
        level = "需补充"
    else:
        level = "风险较高"
    return {
        "total": len(items),
        "ready": ready_count,
        "needs_fix": len(items) - ready_count,
        "average_score": average,
        "level": level,
        "duplicate_groups": len(groups),
        "groups": groups,
        "doi_checked": doi_checked,
        "doi_verified": doi_verified,
        "doi_mismatch": doi_mismatch,
        "doi_unavailable": doi_unavailable,
        "doi_cached": sum(1 for item in items if item.get("doi_cached")),
        "items": items,
        "actions": actions[:8],
        "note": "引用体检检查题录完整度与重复项；DOI 核验仅在手动触发时访问 Crossref，核验结果为题名相似度提示，不替代人工判断。",
    }


def export_json() -> dict:
    items = list_items()
    return {
        "version": 1,
        "exported_at": _now(),
        "count": len(items),
        "items": items,
    }


def import_items(payload: dict | list) -> dict:
    if isinstance(payload, dict):
        items = payload.get("items")
    else:
        items = payload
    if not isinstance(items, list):
        raise ValueError("备份文件缺少 items 列表。")
    if len(items) > 500:
        raise ValueError("单次最多导入 500 条研究库记录。")
    imported = 0
    skipped = 0
    with _LOCK:
        data = _load()
        for raw in items:
            if not isinstance(raw, dict):
                skipped += 1
                continue
            paper_id = str(raw.get("paper_id") or "").strip()
            title = str(raw.get("title") or "").strip()
            if not paper_id or not title:
                skipped += 1
                continue
            existing = data["items"].get(paper_id, {})
            status = raw.get("status")
            if status not in STATUS_LABELS:
                status = existing.get("status", "todo")
            note = str(raw.get("note") or existing.get("note", ""))[:MAX_NOTE_LENGTH]
            item = {
                **existing,
                **{
                    key: raw[key]
                    for key in (
                        "title", "authors", "year", "venue", "source", "url",
                        "pdf_url", "doi", "extraction_level", "abstract", "tldr",
                    )
                    if key in raw
                },
                "paper_id": paper_id,
                "status": status,
                "tags": normalize_tags(raw.get("tags") if isinstance(raw.get("tags"), list) else existing.get("tags")),
                "note": note,
                "added_at": existing.get("added_at") or str(raw.get("added_at") or _now()),
                "updated_at": _now(),
            }
            data["items"][paper_id] = item
            imported += 1
        _save(data)
    return {
        "imported": imported,
        "skipped": skipped,
        "total": len(data["items"]),
        "stats": stats(),
    }


def _bibtex_escape(value: str) -> str:
    text = str(value or "")
    text = text.replace(chr(92), chr(92) * 2)
    for char in ("&", "%", "#", "_", "{", "}"):
        text = text.replace(char, chr(92) + char)
    return text


def export_bibtex() -> str:
    entries = []
    nl = chr(10)
    for item in list_items():
        title = item.get("title") or "Untitled"
        authors = item.get("authors") or []
        year = item.get("year") or "n.d."
        venue = item.get("venue") or ""
        doi = item.get("doi") or ""
        url = item.get("url") or ""
        author_text = " and ".join(authors) or "Unknown"
        tags_text = ", ".join(item.get("tags") or [])
        note_text = (item.get("note") or "")[:500]
        first_author = (authors[0].split()[-1] if authors else "paperpilot").lower()
        first_author = re.sub(r"[^a-z0-9]", "", first_author) or "paperpilot"
        suffix = re.sub(r"[^a-z0-9]", "", item.get("paper_id", "").lower())[-6:] or "entry"
        key = f"{first_author}{year}{suffix}"
        fields = [
            f"  title = {{{_bibtex_escape(title)}}}",
            f"  author = {{{_bibtex_escape(author_text)}}}",
            f"  year = {{{year}}}",
        ]
        if venue:
            fields.append(f"  journal = {{{_bibtex_escape(venue)}}}")
        if doi:
            fields.append(f"  doi = {{{_bibtex_escape(doi)}}}")
        if url:
            fields.append(f"  url = {{{url}}}")
        if tags_text:
            fields.append(f"  keywords = {{{_bibtex_escape(tags_text)}}}")
        if note_text:
            fields.append(f"  note = {{{_bibtex_escape(note_text)}}}")
        entries.append("@article{" + key + "," + nl + ("," + nl).join(fields) + nl + "}")
    return (nl + nl).join(entries) + (nl if entries else "")


def export_markdown() -> str:
    items = list_items()
    lines = [
        "# 我的研究库",
        "",
        f"> 导出时间：{_now()} · 共 {len(items)} 篇",
        "",
    ]
    if not items:
        lines.append("研究库为空。")
        return "\n".join(lines)
    for item in items:
        status = STATUS_LABELS.get(item.get("status"), "待读")
        tags = "、".join(item.get("tags", [])) or "未打标签"
        meta = " · ".join(
            part
            for part in [
                str(item.get("year") or ""),
                item.get("venue") or "",
                " / ".join(item.get("authors", [])[:3]),
            ]
            if part
        )
        lines.extend(
            [
                f"## {item.get('title')}",
                "",
                f"- 状态：{status}",
                f"- 标签：{tags}",
                f"- 元数据：{meta or '未提供'}",
            ]
        )
        if item.get("url"):
            lines.append(f"- 原文：{item['url']}")
        if item.get("tldr"):
            lines.extend(["", f"**TL;DR**：{item['tldr']}"])
        if item.get("note"):
            lines.extend(["", "**研究笔记**", "", item["note"]])
        lines.append("")
    return "\n".join(lines).strip() + "\n"
