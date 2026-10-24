from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from datetime import datetime

from . import citation_styles

_CITATION_KEY_RE = re.compile(r"[^A-Za-z0-9:_-]")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _safe_key(key) -> str:
    """Return a key that is inert in both ``\\cite{}`` and BibTeX entries."""
    raw = str(key or "")
    cleaned = _CITATION_KEY_RE.sub("", raw)
    if cleaned:
        return cleaned
    return "key-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _family(author: str) -> str:
    parts = str(author or "").split()
    return parts[-1] if parts else "Anon"


def export_markdown(
    draft: dict, project: dict, memory: dict, appendix: bool = False,
    style: str | None = None,
) -> str:
    style = style or draft.get("citation_style") or "numeric"
    lines = [f"# {draft.get('title') or project.get('title') or '研究草稿'}", ""]
    brief = project.get("brief")
    if isinstance(brief, dict) and brief.get("question"):
        lines += [f"> 研究问题：{brief['question']}", ""]
    for section in draft.get("sections") or []:
        lines += [f"## {section.get('title') or '章节'}", ""]
        for block in section.get("blocks") or []:
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            marker = citation_styles.marker(
                block.get("citation_keys") or [], draft, style
            )
            if block.get("type") == "bullet":
                lines.append(f"- {text}{marker}")
            elif block.get("type") == "quote":
                lines.append(f"> {text}{marker}")
            elif block.get("type") == "note":
                lines.append(f"> [!NOTE] {text}{marker}")
            else:
                lines.append(f"{text}{marker}")
            lines.append("")
    lines += ["## 参考文献", ""]
    for row in citation_styles.references(draft, style):
        lines.append(row["text"])
    if appendix:
        lines += ["", "## 证据附录", ""]
        for item in memory.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            source = item.get("title") or item.get("paper_id") or ""
            page = item.get("page") or "—"
            lines.append(
                f"- [{item.get('id')}] {source} · {item.get('section') or ''} "
                f"p.{page}：{item.get('quote') or ''}"
            )
    return "\n".join(lines).rstrip() + "\n"


def _bib_escape(text: str) -> str:
    return str(text or "").replace("{", "").replace("}", "")


def export_bibtex(draft: dict) -> str:
    citations = {
        item.get("key"): item
        for item in (draft.get("citations") or {}).values()
        if isinstance(item, dict)
    }
    used = [key for key in citation_styles.appearance_order(draft) if key in citations]
    lines = []
    for key in used:
        item = citations[key]
        authors = " and ".join(item.get("authors") or [])
        lines += [
            f"@article{{{_safe_key(key)},",
            f"  title = {{{_bib_escape(item.get('title'))}}},",
            f"  author = {{{_bib_escape(authors)}}},",
            f"  year = {{{item.get('year') or ''}}},",
            f"  journal = {{{_bib_escape(item.get('venue'))}}},",
            f"  doi = {{{_bib_escape(item.get('doi'))}}},",
            f"  url = {{{_bib_escape(item.get('url'))}}}",
            "}",
            "",
        ]
    return "\n".join(lines)


def _tex_escape(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "$": r"\$",
        "&": r"\&",
        "#": r"\#",
        "^": r"\textasciicircum{}",
        "_": r"\_",
        "%": r"\%",
        "~": r"\textasciitilde{}",
    }
    return "".join(replacements.get(char, char) for char in str(text or ""))


def export_latex_zip(draft: dict, style: str | None = None) -> bytes:
    style = style or draft.get("citation_style") or "numeric"
    lines = [
        r"\documentclass{article}",
        r"\usepackage[UTF8]{ctex}",
        r"\usepackage{hyperref}",
        r"\title{" + _tex_escape(draft.get("title") or "Research Draft") + "}",
        r"\begin{document}",
        r"\maketitle",
    ]
    for section in draft.get("sections") or []:
        lines.append(
            r"\section{" + _tex_escape(section.get("title") or "Section") + "}"
        )
        for block in section.get("blocks") or []:
            text = _tex_escape(block.get("text") or "")
            if not text:
                continue
            cite = "".join(
                r"\cite{" + _safe_key(key) + "}"
                for key in block.get("citation_keys") or []
            )
            if block.get("type") == "bullet":
                lines.append(
                    r"\begin{itemize}\item " + text + cite + r"\end{itemize}"
                )
            elif block.get("type") in {"quote", "note"}:
                lines.append(r"\begin{quote}" + text + cite + r"\end{quote}")
            else:
                lines.append(text + cite)
    lines += [r"\bibliographystyle{plain}", r"\bibliography{refs}", r"\end{document}"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("main.tex", "\n".join(lines))
        archive.writestr("refs.bib", export_bibtex(draft))
        # The tex file stays on plain BibTeX so the archive compiles without
        # extra .bst files; the formatted list is shipped for reference.
        archive.writestr(
            "references.txt",
            "\n".join(row["text"] for row in citation_styles.references(draft, style)) + "\n",
        )
    return buffer.getvalue()


def export_json(draft: dict, project: dict, memory: dict) -> str:
    return json.dumps(
        {
            "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "project": project,
            "draft": draft,
            "evidence": memory.get("evidence") or [],
        },
        ensure_ascii=False,
        indent=2,
    )


def download_name(project: dict, fmt: str) -> str:
    title = re.sub(
        r"[^\w\u4e00-\u9fff.-]+", "_", str(project.get("title") or "paperpilot-draft")
    ).strip("_")
    title = (title or "paperpilot-draft")[:60]
    # Windows rejects trailing dots/spaces, names starting with a dot and the
    # DOS device names (also when used with an extension).
    title = title.rstrip(". ").strip("_").lstrip(".")
    if not title or title.split(".")[0].strip().upper() in _WINDOWS_RESERVED:
        title = "paperpilot-draft"
    extension = {"markdown": "md", "latex": "zip", "bibtex": "bib", "json": "json",
                 "docx": "docx", "pdf": "pdf"}[fmt]
    return f"{title}.{extension}"
