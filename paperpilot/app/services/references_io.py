"""RIS / EndNote XML import and export for draft citations.

Export mirrors the fields the draft already stores. Import is deliberately
strict: only title-bearing records are accepted, duplicates (DOI or normalized
title) are skipped, and the draft's optimistic-lock revision is enforced.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET

from . import draft_state, project_state

MAX_IMPORT = 200
MAX_CITATIONS = 500
TITLE_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
KEY_RE = re.compile(r"[^A-Za-z0-9]")

_RIS_TYPES = {
    "JOUR": "journal", "CONF": "conference", "CPAPER": "conference",
    "BOOK": "book", "CHAP": "book", "ELEC": "web", "GEN": "preprint",
}
_ENDNOTE_TYPES = {
    "Journal Article": "journal", "Conference Proceedings": "conference",
    "Conference Paper": "conference", "Book": "book", "Book Section": "book",
    "Web Page": "web", "Generic": "preprint",
}
_ENDNOTE_NAMES = {
    "journal": ("Journal Article", "17"), "conference": ("Conference Proceedings", "10"),
    "book": ("Book", "6"), "web": ("Web Page", "12"), "preprint": ("Generic", "13"),
}


class ReferenceConflict(ValueError):
    pass


class ReferenceValidation(ValueError):
    pass


def _normalize_title(title: str) -> str:
    return TITLE_RE.sub("", str(title or "").lower())


def _authors(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "")
    parts = re.split(r"\s*(?:;|\band\b|\n)\s*", text)
    return [part.strip() for part in parts if part.strip()]


def _base(key: str, index: int) -> dict:
    return {
        "key": key, "index": index, "title": "", "authors": [], "year": "",
        "venue": "", "doi": "", "url": "", "entry_type": "journal",
        "volume": "", "issue": "", "pages": "", "publisher": "", "language": "en",
    }


def to_ris(draft: dict) -> str:
    records = []
    for key, item in sorted((draft.get("citations") or {}).items()):
        if not isinstance(item, dict):
            continue
        entry_type = str(item.get("entry_type") or "journal")
        ris_type = next((code for code, kind in _RIS_TYPES.items() if kind == entry_type), "GEN")
        lines = [f"TY  - {ris_type}"]
        for author in item.get("authors") or []:
            lines.append(f"AU  - {author}")
        if item.get("title"):
            lines.append(f"TI  - {item['title']}")
        if item.get("venue"):
            lines.append(f"JO  - {item['venue']}" if ris_type == "JOUR" else f"T2  - {item['venue']}")
        if item.get("year"):
            lines.append(f"PY  - {item['year']}")
        if item.get("volume"):
            lines.append(f"VL  - {item['volume']}")
        if item.get("issue"):
            lines.append(f"IS  - {item['issue']}")
        pages = str(item.get("pages") or "")
        if pages:
            start, _, end = pages.partition("-")
            lines.append(f"SP  - {start.strip()}")
            if end.strip():
                lines.append(f"EP  - {end.strip()}")
        if item.get("doi"):
            lines.append(f"DO  - {item['doi']}")
        if item.get("url"):
            lines.append(f"UR  - {item['url']}")
        lines.append("ER  - ")
        records.append("\n".join(lines))
    return "\n\n".join(records) + ("\n" if records else "")


def parse_ris(text: str) -> list[dict]:
    records = re.split(r"(?m)^ER\s{2}-\s*$", str(text or ""))
    parsed: list[dict] = []
    for raw in records:
        fields: dict[str, list[str]] = {}
        for line in raw.splitlines():
            match = re.match(r"^([A-Z0-9]{2})\s{2}-\s?(.*)$", line)
            if not match:
                continue
            fields.setdefault(match.group(1), []).append(match.group(2).strip())
        title = (fields.get("TI") or fields.get("T1") or [""])[0]
        if not title:
            continue
        pages = ""
        start = (fields.get("SP") or [""])[0]
        end = (fields.get("EP") or [""])[0]
        if start:
            pages = start + (f"-{end}" if end else "")
        ris_type = (fields.get("TY") or ["GEN"])[0]
        parsed.append({
            "title": title,
            "authors": _authors(fields.get("AU") or fields.get("A1") or []),
            "year": (fields.get("PY") or fields.get("Y1") or [""])[0],
            "venue": (fields.get("JO") or fields.get("JF") or fields.get("T2") or [""])[0],
            "doi": (fields.get("DO") or [""])[0],
            "url": (fields.get("UR") or [""])[0],
            "entry_type": _RIS_TYPES.get(ris_type, "preprint"),
            "volume": (fields.get("VL") or [""])[0],
            "issue": (fields.get("IS") or [""])[0],
            "pages": pages,
        })
    return parsed


def to_endnote(draft: dict) -> str:
    root = ET.Element("xml")
    records = ET.SubElement(root, "records")
    for key, item in sorted((draft.get("citations") or {}).items()):
        if not isinstance(item, dict):
            continue
        entry_type = str(item.get("entry_type") or "journal")
        name, code = _ENDNOTE_NAMES.get(entry_type, _ENDNOTE_NAMES["preprint"])
        record = ET.SubElement(records, "record")
        ET.SubElement(record, "rec-number").text = key
        ET.SubElement(record, "ref-type", {"name": name}).text = code
        contributors = ET.SubElement(record, "contributors")
        authors = ET.SubElement(contributors, "authors")
        for author in item.get("authors") or []:
            ET.SubElement(authors, "author").text = str(author)
        titles = ET.SubElement(record, "titles")
        ET.SubElement(titles, "title").text = str(item.get("title") or "")
        if item.get("venue"):
            ET.SubElement(titles, "secondary-title").text = str(item["venue"])
        dates = ET.SubElement(record, "dates")
        ET.SubElement(dates, "year").text = str(item.get("year") or "")
        for tag, field in (("volume", "volume"), ("number", "issue"), ("pages", "pages")):
            if item.get(field):
                ET.SubElement(record, tag).text = str(item[field])
        if item.get("doi"):
            ET.SubElement(record, "electronic-resource-num").text = str(item["doi"])
        if item.get("url"):
            urls = ET.SubElement(record, "urls")
            related = ET.SubElement(urls, "related-urls")
            ET.SubElement(related, "url").text = str(item["url"])
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_endnote(text: str) -> list[dict]:
    raw = str(text or "")
    if "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper():
        raise ReferenceValidation("EndNote XML 不允许 DTD/实体声明。")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ReferenceValidation("EndNote XML 解析失败。") from exc
    parsed: list[dict] = []
    for record in root.iter():
        if _local(record.tag) != "record":
            continue
        fields: dict[str, str] = {}
        authors: list[str] = []
        for node in record.iter():
            name = _local(node.tag)
            value = (node.text or "").strip()
            if name == "author" and value:
                authors.append(value)
            elif name in {"title", "secondary-title", "year", "volume", "number",
                          "pages", "electronic-resource-num", "url"} and value:
                fields.setdefault(name, value)
            elif name == "ref-type":
                fields["ref_type"] = str(node.get("name") or "")
        title = fields.get("title") or ""
        if not title:
            continue
        parsed.append({
            "title": title,
            "authors": authors,
            "year": fields.get("year") or "",
            "venue": fields.get("secondary-title") or "",
            "doi": fields.get("electronic-resource-num") or "",
            "url": fields.get("url") or "",
            "entry_type": _ENDNOTE_TYPES.get(fields.get("ref_type") or "", "preprint"),
            "volume": fields.get("volume") or "",
            "issue": fields.get("number") or "",
            "pages": fields.get("pages") or "",
        })
    return parsed


def _unique_key(item: dict, used: set[str]) -> str:
    authors = item.get("authors") or []
    family = ""
    if authors:
        parts = str(authors[0]).split()
        family = KEY_RE.sub("", parts[-1] if parts else "")
    base = (family or "ref") + str(item.get("year") or "")
    if not family:
        base += hashlib.sha1(str(item.get("title") or "").encode("utf-8")).hexdigest()[:6]
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def import_into_draft(project_id: str, fmt: str, text: str, revision: int) -> dict:
    fmt = str(fmt or "").lower()
    if fmt not in {"ris", "endnote"}:
        raise ReferenceValidation("参考文献格式只能是 ris 或 endnote。")
    if len(str(text or "")) > 2_000_000:
        raise ReferenceValidation("导入文件过大。")
    parsed = parse_ris(text) if fmt == "ris" else parse_endnote(text)
    if not parsed:
        raise ReferenceValidation("没有解析到带题名的参考文献。")
    if len(parsed) > MAX_IMPORT:
        raise ReferenceValidation(f"单次最多导入 {MAX_IMPORT} 条。")
    draft = draft_state.read_draft(project_id)
    if int(revision) != int(draft.get("revision") or 0):
        raise ReferenceConflict("草稿已被修改，请刷新后重试。")
    citations = draft.get("citations") or {}
    existing_dois = {str(item.get("doi") or "").lower() for item in citations.values()
                     if isinstance(item, dict) and item.get("doi")}
    existing_titles = {_normalize_title(item.get("title"))
                       for item in citations.values() if isinstance(item, dict)}
    used = set(citations.keys())
    added: list[str] = []
    skipped = 0
    for item in parsed:
        doi = str(item.get("doi") or "").lower()
        title_key = _normalize_title(item.get("title"))
        if (doi and doi in existing_dois) or (title_key and title_key in existing_titles):
            skipped += 1
            continue
        entry = _base("", 0)
        key = _unique_key(item, used)
        entry.update({
            "key": key, "title": str(item.get("title") or ""),
            "authors": _authors(item.get("authors")),
            "year": str(item.get("year") or ""), "venue": str(item.get("venue") or ""),
            "doi": str(item.get("doi") or ""), "url": str(item.get("url") or ""),
            "entry_type": str(item.get("entry_type") or "journal"),
            "volume": str(item.get("volume") or ""), "issue": str(item.get("issue") or ""),
            "pages": str(item.get("pages") or ""),
        })
        citations[key] = entry
        if doi:
            existing_dois.add(doi)
        if title_key:
            existing_titles.add(title_key)
        added.append(key)
    if len(citations) > MAX_CITATIONS:
        raise ReferenceValidation(f"参考文献不能超过 {MAX_CITATIONS} 条。")
    if added:
        draft["citations"] = citations
        draft["revision"] = int(draft.get("revision") or 0) + 1
        draft["updated_at"] = project_state._now()
        draft_state.write_draft(project_id, draft)
    return {
        "added": len(added), "skipped": skipped, "keys": added,
        "revision": int(draft.get("revision") or 0), "citations": len(citations),
    }
