"""Manuscript metadata and the pre-submission check.

``manuscript.json`` holds the fields a course paper needs outside the body text
(title, authors, affiliations, abstract, keywords, funding) plus the citation
style to render with. The preflight check aggregates every "is this ready to
hand in" question into one locatable list; it never edits the draft.
"""

from __future__ import annotations

import copy
import json
import re

from . import citation_styles, contradictions, draft_state, project_state

FILE_NAME = "manuscript.json"
LANGUAGES = {"zh", "en"}
LIMITS = {
    "title": 200, "subtitle": 200, "abstract": 2000, "funding": 300,
    "authors": 20, "author_name": 60, "affiliation": 120, "email": 120,
    "orcid": 19, "contributions": 300, "availability": 500,
    "competing": 300, "acknowledgments": 1000,
    "keywords": 12, "keyword": 30,
}

_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_FIG_RE = re.compile(r"^\s*(?:图|figure|fig\.?)\s*(\d+)", re.IGNORECASE)
_TAB_RE = re.compile(r"^\s*(?:表|table)\s*(\d+)", re.IGNORECASE)
_REF_FIG_RE = re.compile(r"(?:图|figure|fig\.?)\s*(\d+)", re.IGNORECASE)
_REF_TAB_RE = re.compile(r"(?:表|table)\s*(\d+)", re.IGNORECASE)
_FORMULA_RE = re.compile(r"\$\$?[^$]+\$\$?|\\\([^)]*\\\)|\\\[[^\]]*\\\]")

_ORCID_RE = re.compile(r"^[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]$")


def _now() -> str:
    return project_state._now()


def defaults(project_id: str) -> dict:
    return {
        "version": 1,
        "project_id": project_id,
        "title": "", "subtitle": "",
        "authors": [],
        "abstract": "", "keywords": [], "funding": "",
        "contributions": "", "data_availability": "", "code_availability": "",
        "competing_interests": "", "acknowledgments": "",
        # Empty means "not chosen yet"; read() then falls back to the draft's
        # style so a v2 draft keeps its numeric/author-year rendering.
        "citation_style": "",
        "language": "zh",
        "updated_at": "",
    }


def _path(project_id: str):
    return project_state.project_dir(project_id) / FILE_NAME


def read(project_id: str) -> dict:
    data = defaults(project_id)
    path = _path(project_id)
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = {}
        if isinstance(stored, dict):
            data.update({key: stored[key] for key in data if key in stored})
    # Fall back to the draft's style so an existing draft keeps rendering the
    # way the user last chose, without forcing them to re-pick it in the panel.
    if not data.get("citation_style"):
        data["citation_style"] = draft_state.read_draft(project_id).get("citation_style") or citation_styles.DEFAULT_STYLE
    return data


def orcid_valid(value: str) -> bool:
    """ISO 7064 11,2 check used by ORCID (last char may be X)."""
    text = str(value or "").strip().upper()
    if not _ORCID_RE.fullmatch(text):
        return False
    digits = text.replace("-", "")
    total = 0
    for char in digits[:15]:
        total = (total + int(char)) * 2
    result = (12 - total % 11) % 11
    expected = "X" if result == 10 else str(result)
    return digits[15] == expected


def _clip(value, limit: int, field: str) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        raise ValueError(f"{field} 不能超过 {limit} 个字符。")
    return text


def _authors(value) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("作者必须是数组。")
    if len(value) > LIMITS["authors"]:
        raise ValueError(f"作者不能超过 {LIMITS['authors']} 人。")
    clean = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("作者格式不正确。")
        name = _clip(item.get("name"), LIMITS["author_name"], "作者姓名")
        if not name:
            raise ValueError("作者姓名不能为空。")
        orcid = _clip(item.get("orcid"), LIMITS["orcid"], "ORCID")
        if orcid and not orcid_valid(orcid):
            raise ValueError(f"ORCID 校验位不正确：{orcid}")
        clean.append({
            "name": name,
            "affiliation": _clip(item.get("affiliation"), LIMITS["affiliation"], "作者单位"),
            "email": _clip(item.get("email"), LIMITS["email"], "邮箱"),
            "orcid": orcid.upper(),
            "corresponding": bool(item.get("corresponding")),
        })
    if sum(1 for author in clean if author["corresponding"]) > 1:
        raise ValueError("只能有一位通讯作者。")
    return clean


def _keywords(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("关键词必须是数组。")
    if len(value) > LIMITS["keywords"]:
        raise ValueError(f"关键词不能超过 {LIMITS['keywords']} 个。")
    clean = []
    for item in value:
        word = _clip(item, LIMITS["keyword"], "关键词")
        if word and word not in clean:
            clean.append(word)
    return clean


def update(project_id: str, payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("稿件信息格式不正确。")
    with project_state.project_lock():
        if project_id not in (project_state.read_projects().get("projects") or {}):
            raise KeyError(project_id)
        current = read(project_id)
        data = copy.deepcopy(current)
        data["title"] = _clip(payload.get("title", data.get("title")), LIMITS["title"], "标题")
        data["subtitle"] = _clip(payload.get("subtitle", data.get("subtitle")), LIMITS["subtitle"], "副标题")
        data["abstract"] = _clip(payload.get("abstract", data.get("abstract")), LIMITS["abstract"], "摘要")
        data["funding"] = _clip(payload.get("funding", data.get("funding")), LIMITS["funding"], "基金")
        data["authors"] = _authors(payload.get("authors", data.get("authors")))
        data["keywords"] = _keywords(payload.get("keywords", data.get("keywords")))
        for key, limit, label in (
            ("contributions", LIMITS["contributions"], "作者贡献"),
            ("data_availability", LIMITS["availability"], "数据可用性"),
            ("code_availability", LIMITS["availability"], "代码可用性"),
            ("competing_interests", LIMITS["competing"], "利益冲突"),
            ("acknowledgments", LIMITS["acknowledgments"], "致谢"),
        ):
            data[key] = _clip(payload.get(key, data.get(key)), limit, label)
        style = payload.get("citation_style", data.get("citation_style"))
        if not citation_styles.is_style(style):
            raise ValueError(f"未知引用样式：{style}")
        data["citation_style"] = style
        language = payload.get("language", data.get("language"))
        if language not in LANGUAGES:
            raise ValueError("语言只能是 zh 或 en。")
        data["language"] = language
        data["updated_at"] = _now()
        _save(project_id, data)
    return copy.deepcopy(data)


def _save(project_id: str, data: dict) -> None:
    path = _path(project_id)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def write(project_id: str, data: dict) -> dict:
    """Replace the manuscript with a previously exported snapshot."""
    clean = defaults(project_id)
    for key in clean:
        if key in data:
            clean[key] = copy.deepcopy(data[key])
    clean["project_id"] = project_id
    if not citation_styles.is_style(str(clean.get("citation_style") or "")):
        clean["citation_style"] = citation_styles.DEFAULT_STYLE
    clean["updated_at"] = _now()
    with project_state.project_lock():
        _save(project_id, clean)
    return copy.deepcopy(clean)


def update_citation_style(project_id: str, style: str) -> dict | None:
    """Keep the manuscript's export style in step with the draft's selector.

    Both drawers expose a style picker and exports read the manuscript first, so
    without this sync changing the draft's picker looked like a no-op.
    """
    if not citation_styles.is_style(style):
        return None
    with project_state.project_lock():
        if project_id not in (project_state.read_projects().get("projects") or {}):
            return None
        current = read(project_id)
        if current.get("citation_style") == style:
            return None
        current["citation_style"] = style
        current["updated_at"] = _now()
        _save(project_id, current)
        return current


def word_count(text: str) -> int:
    """CJK characters count as one word each; Latin runs count as one word."""
    value = str(text or "")
    cjk = len(_CJK_RE.findall(value))
    latin = len(_WORD_RE.findall(_CJK_RE.sub(" ", value)))
    return cjk + latin


def _body_stats(draft: dict) -> dict:
    chars = words = blocks = 0
    captions = {"figure": [], "table": []}
    references = {"figure": set(), "table": set()}
    formulas = 0
    for section in draft.get("sections") or []:
        for block in section.get("blocks") or []:
            text = str(block.get("text") or "")
            if not text.strip():
                continue
            blocks += 1
            chars += len(text)
            words += word_count(text)
            formulas += len(_FORMULA_RE.findall(text))
            fig = _FIG_RE.match(text)
            tab = _TAB_RE.match(text)
            if fig:
                captions["figure"].append(int(fig.group(1)))
                continue
            if tab:
                captions["table"].append(int(tab.group(1)))
                continue
            for match in _REF_FIG_RE.finditer(text):
                references["figure"].add(int(match.group(1)))
            for match in _REF_TAB_RE.finditer(text):
                references["table"].add(int(match.group(1)))
    return {"blocks": blocks, "chars": chars, "words": words,
            "formulas": formulas, "captions": captions, "references": references}


def _caption_issues(stats: dict) -> list[dict]:
    items = []
    for kind, label in (("figure", "图"), ("table", "表")):
        numbers = stats["captions"][kind]
        if numbers and sorted(numbers) != list(range(1, len(numbers) + 1)):
            items.append({"level": "warn", "kind": "caption_numbering",
                          "message": f"{label}题注编号不连续：{sorted(numbers)}",
                          "target": {}})
        missing = sorted(stats["references"][kind] - set(numbers))
        if missing:
            items.append({"level": "warn", "kind": "cross_reference",
                          "message": f"正文引用了不存在的{label}：{missing}",
                          "target": {}})
    return items


def preflight(project_id: str, style: str | None = None) -> dict:
    """One locatable checklist for "can this be handed in". Read-only."""
    draft = draft_state.read_draft(project_id)
    meta = read(project_id)
    style = style or meta.get("citation_style") or citation_styles.DEFAULT_STYLE
    items: list[dict] = []

    if not str(meta.get("title") or "").strip():
        items.append({"level": "error", "kind": "metadata",
                      "message": "未填写稿件标题。", "target": {"panel": "manuscript"}})
    if not meta.get("authors"):
        items.append({"level": "error", "kind": "metadata",
                      "message": "未填写作者信息。", "target": {"panel": "manuscript"}})
    elif not any(author.get("corresponding") for author in meta.get("authors") or []):
        items.append({"level": "warn", "kind": "corresponding_author",
                      "message": "未标记通讯作者；投稿包无法确定通讯联系人。",
                      "target": {"panel": "manuscript"}})
    for author in meta.get("authors") or []:
        orcid = str(author.get("orcid") or "").strip()
        if orcid and not orcid_valid(orcid):
            items.append({"level": "warn", "kind": "orcid",
                          "message": f"{author.get('name') or '作者'} 的 ORCID 校验位不正确。",
                          "target": {"panel": "manuscript"}})
    if not str(meta.get("abstract") or "").strip():
        items.append({"level": "warn", "kind": "metadata",
                      "message": "未填写摘要。", "target": {"panel": "manuscript"}})
    if not meta.get("keywords"):
        items.append({"level": "warn", "kind": "metadata",
                      "message": "未填写关键词。", "target": {"panel": "manuscript"}})

    for issue in citation_styles.validate(draft, style):
        items.append({
            "level": issue["level"], "kind": f"citation_{issue['field']}",
            "message": issue["message"], "target": {"citation": issue["key"]},
        })

    stats = _body_stats(draft)
    items.extend(_caption_issues(stats))

    unbound = []
    unsupported = partial = 0
    for section in draft.get("sections") or []:
        for block in section.get("blocks") or []:
            if not str(block.get("text") or "").strip():
                continue
            if str(block.get("type") or "paragraph") == "paragraph" and not block.get("evidence_ids"):
                unbound.append(block.get("id"))
            for claim in block.get("claims") or []:
                if claim.get("stance") == "unsupported":
                    unsupported += 1
                elif claim.get("stance") == "partial":
                    partial += 1
    if unbound:
        items.append({"level": "warn", "kind": "unbound_blocks",
                      "message": f"{len(unbound)} 个正文块未绑定证据。",
                      "target": {"blocks": unbound[:20]}})
    if unsupported:
        items.append({"level": "error", "kind": "claim_unsupported",
                      "message": f"{unsupported} 条主张未被所绑证据支持。",
                      "target": {"panel": "audit"}})
    if partial:
        items.append({"level": "warn", "kind": "claim_partial",
                      "message": f"{partial} 条主张只被部分证据覆盖。",
                      "target": {"panel": "audit"}})

    stored = contradictions.read(project_id)
    if stored.get("items"):
        items.append({"level": "warn", "kind": "contradictions",
                      "message": f"项目内检测到 {len(stored['items'])} 条跨论文矛盾。",
                      "target": {"panel": "audit"}})

    if stats["formulas"]:
        items.append({"level": "info", "kind": "formulas",
                      "message": f"正文含 {stats['formulas']} 处公式；DOCX 保留 LaTeX 源码文本，不排版公式。",
                      "target": {}})

    counts = {level: sum(1 for item in items if item["level"] == level)
              for level in ("error", "warn", "info")}
    return {
        "project_id": project_id,
        "style": style,
        "style_label": citation_styles.STYLES.get(style, {}).get("label", style),
        "body_words": stats["words"],
        "body_chars": stats["chars"],
        "body_blocks": stats["blocks"],
        "abstract_words": word_count(meta.get("abstract")),
        "figures": sorted(stats["captions"]["figure"]),
        "tables": sorted(stats["captions"]["table"]),
        "formulas": stats["formulas"],
        "references": len(draft.get("citations") or {}),
        "items": items,
        "counts": counts,
        "blocking": counts["error"] > 0,
    }


def references_preview(project_id: str, style: str | None = None) -> dict:
    """What the in-text markers and reference list would look like, plus issues."""
    draft = draft_state.read_draft(project_id)
    style = style or read(project_id).get("citation_style") or citation_styles.DEFAULT_STYLE
    if not citation_styles.is_style(style):
        raise ValueError(f"未知引用样式：{style}")
    markers = []
    for section in draft.get("sections") or []:
        for block in section.get("blocks") or []:
            keys = [str(key) for key in block.get("citation_keys") or [] if str(key)]
            if not keys or not str(block.get("text") or "").strip():
                continue
            markers.append({
                "block_id": block.get("id"),
                "section_id": section.get("id"),
                "excerpt": str(block.get("text"))[:60],
                "marker": citation_styles.marker(keys, draft, style),
            })
    return {
        "style": style,
        "label": citation_styles.STYLES.get(style, {}).get("label", style),
        "markers": markers[:20],
        "references": citation_styles.references(draft, style),
        "issues": citation_styles.validate(draft, style),
        "counts": {
            level: sum(1 for item in citation_styles.validate(draft, style) if item["level"] == level)
            for level in ("error", "warn")
        },
    }
