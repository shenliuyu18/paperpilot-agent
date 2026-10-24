"""PDF export through an optional XeLaTeX toolchain.

This renders the same content model as the DOCX exporter: the draft supplies
sections and blocks, ``manuscript`` supplies the front matter, and
``citation_styles`` supplies reference entries. The difference is output
fidelity — XeLaTeX resolves in-text ``\\cite`` markers against a manual
``thebibliography`` list, so one toolchain (no BibTeX pass, no .bst files) is
enough for a course-paper-level PDF.

The toolchain is optional by design: importing this module and serving the app
never require TeX. When XeLaTeX is missing, ``build`` raises
``PDFUnavailable`` and the API answers 503 with an installation hint.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import citation_styles, draft_export

TIMEOUT_SECONDS = 120
FALLBACK_TITLE = "Research Draft"


class PDFUnavailable(RuntimeError):
    """No XeLaTeX binary is configured on this host."""


class PDFBuildError(RuntimeError):
    """XeLaTeX was found but the document did not compile."""


def tool_path() -> str:
    configured = (os.getenv("PAPERPILOT_XELATEX") or "").strip()
    if configured:
        return configured if Path(configured).exists() else ""
    return shutil.which("xelatex") or ""


def available() -> bool:
    return bool(tool_path())


def _escape(text) -> str:
    return draft_export._tex_escape(text)


def _author_line(authors) -> str:
    names = []
    for author in authors or []:
        if not isinstance(author, dict):
            continue
        name = str(author.get("name") or "").strip()
        affiliation = str(author.get("affiliation") or "").strip()
        if not name:
            continue
        names.append(name + (r"\\" + _escape(affiliation) if affiliation else ""))
    return r" \and ".join(names)


def latex_document(draft: dict, manuscript: dict, style: str | None = None,
                   blinded: bool = False) -> str:
    """Build the complete ``.tex`` source. Pure string work; no toolchain needed."""
    style = style or manuscript.get("citation_style") or draft.get("citation_style") \
        or citation_styles.DEFAULT_STYLE
    title = str(manuscript.get("title") or draft.get("title") or FALLBACK_TITLE)
    subtitle = str(manuscript.get("subtitle") or "")
    authors = "" if blinded else _author_line(manuscript.get("authors"))
    abstract = str(manuscript.get("abstract") or "")
    keywords = [str(item).strip() for item in (manuscript.get("keywords") or []) if str(item).strip()]
    funding = str(manuscript.get("funding") or "")
    fontset = (os.getenv("PAPERPILOT_CTEX_FONTSET") or "").strip()
    class_options = "12pt,a4paper" + (f",fontset={fontset}" if fontset else "")

    lines = [
        r"\documentclass[" + class_options + r"]{ctexart}",
        r"\usepackage[margin=2.5cm]{geometry}",
        r"\usepackage{hyperref}",
        r"\setlength{\parskip}{0.5em}",
        r"\setlength{\parindent}{2em}",
        r"\title{" + _escape(title) + "}",
    ]
    lines.append(r"\author{" + (authors or r"\mbox{}") + "}")
    lines.append(r"\date{}")
    lines.append(r"\begin{document}")
    lines.append(r"\maketitle")
    if subtitle:
        lines.append(r"\begin{center}\large " + _escape(subtitle) + r"\end{center}")
    if abstract:
        lines += [r"\begin{abstract}", _escape(abstract), r"\end{abstract}"]
    if keywords:
        lines.append(r"\noindent\textbf{关键词：}" + "，".join(_escape(item) for item in keywords))
    if funding and not blinded:
        lines.append(r"\noindent\textbf{基金项目：}" + _escape(funding))
    lines.append("")

    style_kind = citation_styles.STYLES.get(style, citation_styles.STYLES["numeric"])["kind"]
    for section in draft.get("sections") or []:
        lines.append(r"\section{" + _escape(section.get("title") or "Section") + "}")
        for block in section.get("blocks") or []:
            text = _escape(block.get("text") or "")
            if not text.strip():
                continue
            keys = [str(key) for key in block.get("citation_keys") or [] if str(key)]
            if style_kind == "author-year":
                # Plain LaTeX always brackets \cite labels, which would render
                # "(Author, 2024)" as "[(Author, 2024)]". Use the same marker
                # text the Markdown/DOCX exporters produce instead.
                cites = _escape(citation_styles.marker(keys, draft, style))
            else:
                cites = "".join(r"\cite{" + draft_export._safe_key(key) + "}" for key in keys)
            kind = block.get("type")
            if kind == "bullet":
                lines.append(r"\begin{itemize}\item " + text + cites + r"\end{itemize}")
            elif kind in {"quote", "note"}:
                lines.append(r"\begin{quote}" + text + cites + r"\end{quote}")
            else:
                lines.append(text + cites)
        lines.append("")

    metadata_sections = [
        ("acknowledgments", "致谢", not blinded),
        ("data_availability", "数据可用性", True),
        ("code_availability", "代码可用性", True),
        ("competing_interests", "利益冲突", True),
    ]
    for field, heading, visible in metadata_sections:
        value = str(manuscript.get(field) or "").strip()
        if value and visible:
            lines += [r"\section*{" + heading + "}", _escape(value), ""]

    entries = citation_styles.references(draft, style)
    if entries:
        lines.append(r"\begin{thebibliography}{99}")
        for entry in entries:
            lines.append(
                r"\bibitem{" + draft_export._safe_key(entry["key"]) + "} "
                + _escape(entry.get("text") or "")
            )
        lines.append(r"\end{thebibliography}")

    lines.append(r"\end{document}")
    return "\n".join(lines) + "\n"


def _log(proc: subprocess.CompletedProcess) -> str:
    raw = b"".join(part or b"" for part in (proc.stdout, proc.stderr))
    text = raw.decode("utf-8", "replace")
    return " ".join(text.split())[-600:]


def build(draft: dict, manuscript: dict, style: str | None = None,
          blinded: bool = False) -> bytes:
    """Compile the draft to PDF bytes, or raise a typed error."""
    binary = tool_path()
    if not binary:
        raise PDFUnavailable(
            "PDF 导出需要 XeLaTeX。请安装 TeX Live/MiKTeX，"
            "或把 PAPERPILOT_XELATEX 指向 xelatex 可执行文件。"
        )
    source = latex_document(draft, manuscript, style, blinded=blinded)
    with tempfile.TemporaryDirectory(prefix="pp-pdf-") as folder:
        workdir = Path(folder)
        (workdir / "main.tex").write_text(source, encoding="utf-8")
        command = [
            binary, "-interaction=nonstopmode", "-halt-on-error",
            "-no-shell-escape", "-file-line-error", "main.tex",
        ]
        # Two passes: the first resolves \cite numbers and page references used
        # by hyperref, the second writes them into the final PDF.
        for _ in range(2):
            try:
                proc = subprocess.run(
                    command,
                    cwd=str(workdir),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=TIMEOUT_SECONDS,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise PDFBuildError(f"XeLaTeX 编译超时（{TIMEOUT_SECONDS}s）。") from exc
            if proc.returncode != 0:
                raise PDFBuildError("XeLaTeX 编译失败：" + _log(proc))
        output = workdir / "main.pdf"
        if not output.exists():
            raise PDFBuildError("XeLaTeX 未生成 PDF 文件。")
        payload = output.read_bytes()
    if not payload.startswith(b"%PDF"):
        raise PDFBuildError("XeLaTeX 产物不是有效的 PDF。")
    return payload
