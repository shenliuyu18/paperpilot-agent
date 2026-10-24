"""Citation style engine.

One place decides three things that used to be implicit and inconsistent:

* the in-text marker (`[1]`, `(Qin, 2024)`, …)
* the reference-list entry for each key
* which fields a style needs before an export is trustworthy

``numeric`` and ``author-year`` reproduce the pre-v5 output byte for byte, so
existing drafts and their regression tests stay valid. The three v5 styles
(``gb7714`` / ``apa`` / ``ieee``) are additive.

Everything here is a pure function of the draft dict: no IO, no mutation.
"""

from __future__ import annotations

import re

STYLES = {
    "numeric": {"label": "数字编号（兼容 v2）", "kind": "numeric", "order": "appearance",
                "entry_type_tag": False},
    "author-year": {"label": "作者-年（兼容 v2）", "kind": "author-year", "order": "appearance",
                    "entry_type_tag": False},
    "gb7714": {"label": "GB/T 7714-2015", "kind": "numeric", "order": "appearance",
               "entry_type_tag": True},
    "apa": {"label": "APA 7", "kind": "author-year", "order": "alphabetical",
            "entry_type_tag": False},
    "ieee": {"label": "IEEE", "kind": "numeric", "order": "appearance",
             "entry_type_tag": False},
}
DEFAULT_STYLE = "gb7714"

_TAGS = {"journal": "J", "conference": "C", "book": "M", "preprint": "EB/OL", "web": "EB/OL"}
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def is_style(style: str) -> bool:
    return str(style or "") in STYLES


def _citations(draft: dict) -> dict[str, dict]:
    return {
        str(item.get("key")): item
        for item in (draft.get("citations") or {}).values()
        if isinstance(item, dict) and item.get("key")
    }


def appearance_order(draft: dict) -> dict[str, int]:
    """Key → 1-based index by first appearance in the body."""
    order: dict[str, int] = {}
    for section in draft.get("sections") or []:
        for block in section.get("blocks") or []:
            for key in block.get("citation_keys") or []:
                key = str(key)
                if key and key not in order:
                    order[key] = len(order) + 1
    return order


def _family(author: str) -> str:
    parts = str(author or "").split()
    return parts[-1] if parts else "Anon"


def _initials(author: str) -> str:
    parts = str(author or "").split()
    if len(parts) <= 1:
        return ""
    return " ".join(f"{part[0]}." for part in parts[:-1] if part)


def _is_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(str(text or "")))


def _author_list(citation: dict, limit: int, chinese_tail: str, english_tail: str) -> str:
    authors = [str(a).strip() for a in (citation.get("authors") or []) if str(a).strip()]
    if not authors:
        return ""
    if len(authors) > limit:
        head = authors[:limit]
        tail = chinese_tail if _is_cjk("".join(authors)) else english_tail
        return ", ".join(head) + tail
    return ", ".join(authors)


def _sorted_keys(draft: dict, style: str) -> list[str]:
    citations = _citations(draft)
    order = appearance_order(draft)
    keys = [key for key in order if key in citations]
    if STYLES.get(style, {}).get("order") == "alphabetical":
        keys.sort(key=lambda key: (
            _family((citations[key].get("authors") or ["Anon"])[0]).lower(),
            str(citations[key].get("year") or ""),
            key,
        ))
    return keys


def numbering(draft: dict, style: str) -> dict[str, int]:
    """Key → displayed number (1-based). Alphabetical styles still get numbers."""
    keys = _sorted_keys(draft, style)
    return {key: index for index, key in enumerate(keys, start=1)}


def marker(keys: list[str], draft: dict, style: str) -> str:
    """In-text marker, byte-compatible with the pre-v5 implementation."""
    keys = [str(key) for key in keys or [] if str(key)]
    if not keys:
        return ""
    citations = _citations(draft)
    kind = STYLES.get(style, STYLES["numeric"])["kind"]
    if kind == "author-year":
        labels = []
        for key in keys:
            item = citations.get(key) or {}
            authors = item.get("authors") or ["Anon"]
            labels.append(f"{_family(authors[0])}, {item.get('year') or 'n.d.'}")
        return " (" + "; ".join(labels) + ")"
    order = numbering(draft, style)
    return " [" + ", ".join(str(order.get(key, 0)) for key in keys) + "]"


def _stop(text: str) -> str:
    """End a segment with exactly one period (``et al.`` must not become ``et al..``)."""
    value = str(text or "").strip()
    if not value or value.endswith((".", "。", "!", "?", "！", "？")):
        return value
    return value + "."


def reference_entry(citation: dict, style: str, index: int) -> str:
    """One reference-list line. Legacy styles must match the old exporter exactly."""
    authors_list = [str(a).strip() for a in (citation.get("authors") or []) if str(a).strip()]
    authors = ", ".join(authors_list)
    year = str(citation.get("year") or "")
    title = str(citation.get("title") or "")
    venue = str(citation.get("venue") or "")
    doi = str(citation.get("doi") or "")
    url = str(citation.get("url") or "")
    entry_type = str(citation.get("entry_type") or ("journal" if venue else "preprint"))
    volume = str(citation.get("volume") or "")
    issue = str(citation.get("issue") or "")
    pages = str(citation.get("pages") or "")

    if style in ("numeric", "author-year"):
        return f"{index}. {authors}. {title}. {venue}, {year}. {doi}"

    if style == "gb7714":
        tag = _TAGS.get(entry_type, "EB/OL")
        names = _author_list(citation, 3, ", 等", ", et al.")
        parts = []
        if names:
            parts.append(_stop(names))
        parts.append(_stop(f"{title}[{tag}]") if title else "")
        if entry_type in ("preprint", "web"):
            tail = venue or "arXiv"
            serial = f"{tail}, {year}" if year else tail
            parts.append(_stop(serial))
            link = url or (f"https://doi.org/{doi}" if doi else "")
            if link:
                parts.append(_stop(link))
        else:
            serial = []
            if year:
                serial.append(year)
            if volume:
                serial.append(volume + (f"({issue})" if issue else ""))
            head = f"{venue}," if venue else ""
            body = ", ".join(serial)
            line = f"{head} {body}".strip()
            if pages:
                line = f"{line}: {pages}" if line else pages
            parts.append((line + ".").strip())
            if doi:
                parts.append(f"DOI: {doi}.")
        return f"[{index}] " + " ".join(part for part in parts if part).strip()

    if style == "ieee":
        names = _author_list(citation, 3, ", 等", ", et al.")
        parts = []
        if names:
            parts.append(names + ",")
        if title:
            parts.append(f'"{title},"')
        if venue:
            parts.append(venue + ",")
        if volume:
            parts.append(f"vol. {volume},")
        if issue:
            parts.append(f"no. {issue},")
        if pages:
            parts.append(f"pp. {pages},")
        if year:
            parts.append(year + ".")
        if doi:
            parts.append(f"doi: {doi}.")
        elif url:
            parts.append(url + ".")
        return f"[{index}] " + " ".join(parts).strip()

    # APA
    names = []
    for author in authors_list:
        if _is_cjk(author):
            names.append(author)
        else:
            family = _family(author)
            initials = _initials(author)
            names.append(f"{family}, {initials}".strip().rstrip(","))
    if names:
        joined = ", ".join(names[:-1])
        names_text = f"{joined}, & {names[-1]}" if len(names) > 1 else names[0]
        # Initials already end with ".", so only add the list terminator when
        # the last name does not already end with one ("Author, A." not "A..").
        if not names_text.endswith("."):
            names_text += "."
    else:
        names_text = ""
    year_text = f"({year})." if year else "(n.d.)."
    parts = [names_text, year_text]
    if title:
        parts.append(f"{title}.")
    serial = []
    if venue:
        serial.append(venue)
    if volume:
        serial.append(volume + (f"({issue})" if issue else ""))
    if pages:
        serial.append(pages)
    if serial:
        parts.append(", ".join(serial) + ".")
    link = f"https://doi.org/{doi}" if doi else url
    if link:
        parts.append(link)
    return " ".join(part for part in parts if part).strip()


def references(draft: dict, style: str) -> list[dict]:
    """Ordered reference entries with their display index."""
    citations = _citations(draft)
    rows = []
    for key, index in sorted(numbering(draft, style).items(), key=lambda item: item[1]):
        citation = citations.get(key) or {}
        rows.append({
            "key": key,
            "index": index,
            "text": reference_entry(citation, style, index),
            "title": str(citation.get("title") or ""),
        })
    return rows


def validate(draft: dict, style: str) -> list[dict]:
    """Field completeness and cross-reference consistency for one style."""
    if not is_style(style):
        return [{"key": "", "field": "style", "level": "error",
                 "message": f"未知引用样式：{style}"}]
    citations = _citations(draft)
    order = appearance_order(draft)
    issues: list[dict] = []

    for key in sorted(set(order) - set(citations)):
        issues.append({"key": key, "field": "key", "level": "error",
                       "message": f"正文引用了不存在的引用键：{key}"})
    for key in sorted(set(citations) - set(order)):
        issues.append({"key": key, "field": "unused", "level": "warn",
                       "message": f"参考文献未被正文引用：{key}"})

    for key in sorted(citations):
        item = citations[key]
        if not str(item.get("title") or "").strip():
            issues.append({"key": key, "field": "title", "level": "error",
                           "message": f"{key} 缺题名"})
        if not [a for a in (item.get("authors") or []) if str(a).strip()]:
            issues.append({"key": key, "field": "authors", "level": "error",
                           "message": f"{key} 缺作者"})
        if not str(item.get("year") or "").strip():
            issues.append({"key": key, "field": "year", "level": "error",
                           "message": f"{key} 缺年份"})
        entry_type = str(item.get("entry_type") or ("journal" if item.get("venue") else "preprint"))
        if entry_type in ("journal", "conference") and not str(item.get("venue") or "").strip():
            issues.append({"key": key, "field": "venue", "level": "warn",
                           "message": f"{key} 缺出版信息（刊名/会议名）"})
        if entry_type == "journal" and style == "gb7714":
            if not str(item.get("volume") or "").strip():
                issues.append({"key": key, "field": "volume", "level": "warn",
                               "message": f"{key} 缺卷号（GB/T 7714 要求）"})
            if not str(item.get("pages") or "").strip():
                issues.append({"key": key, "field": "pages", "level": "warn",
                               "message": f"{key} 缺页码（GB/T 7714 要求）"})
        if entry_type in ("preprint", "web") and not (item.get("url") or item.get("doi")):
            issues.append({"key": key, "field": "url", "level": "warn",
                           "message": f"{key} 缺链接（预印本/网页建议给出 URL 或 DOI）"})
    return issues


def styles_meta() -> list[dict]:
    """Style catalogue for the UI, including the fields each style cares about."""
    required = ["title", "authors", "year"]
    return [
        {"id": key, "label": value["label"], "kind": value["kind"],
         "order": value["order"], "required": required,
         "recommended": ["venue", "volume", "issue", "pages", "doi"]}
        for key, value in STYLES.items()
    ]
