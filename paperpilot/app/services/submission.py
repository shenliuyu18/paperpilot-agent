"""Submission profiles and the submission-package builder.

The package is assembled from things the project already trusts: the draft,
manuscript metadata, citation styles, pre-flight checks, the reviewer report and
the reference exporters. It never fabricates a journal's official template; the
profiles are advisory checklists ("参考要求") and every generated letter says it
must be reviewed before sending.

Blinding is a rendering option: the package can ship an anonymised DOCX/PDF while
the separate title page keeps the author details for the editor.
"""

from __future__ import annotations

import copy
import io
import zipfile

from . import (
    docx_export,
    draft_export,
    draft_state,
    manuscript,
    pdf_export,
    project_service,
    project_state,
    references_io,
    reviewer,
)

PROFILES = {
    "course": {
        "label": "课程论文",
        "description": "课程/毕设交付；GB/T 7714，无需盲审",
        "citation_style": "gb7714",
        "title_max": 200, "abstract_min_words": 100, "abstract_max_words": 500,
        "keywords_min": 3, "keywords_max": 8,
        "highlights_min": 3, "highlights_max": 5, "highlight_max_chars": 85,
        "blind": False, "authors_max": 10,
    },
    "generic-journal": {
        "label": "通用期刊（参考要求）",
        "description": "摘要 150–250 词、3–5 条亮点；数值引用样式",
        "citation_style": "numeric",
        "title_max": 180, "abstract_min_words": 150, "abstract_max_words": 250,
        "keywords_min": 4, "keywords_max": 6,
        "highlights_min": 3, "highlights_max": 5, "highlight_max_chars": 85,
        "blind": False, "authors_max": 20,
    },
    "ieee-conference": {
        "label": "IEEE 会议（参考要求）",
        "description": "摘要 150–250 词、3–5 个关键词；IEEE 引用；单盲",
        "citation_style": "ieee",
        "title_max": 150, "abstract_min_words": 150, "abstract_max_words": 250,
        "keywords_min": 3, "keywords_max": 5,
        "highlights_min": 0, "highlights_max": 0, "highlight_max_chars": 85,
        "blind": False, "authors_max": 20,
    },
    "double-blind-journal": {
        "label": "双盲期刊（参考要求）",
        "description": "稿件正文与 PDF 去除作者/单位/基金；标题页单独提交",
        "citation_style": "numeric",
        "title_max": 180, "abstract_min_words": 150, "abstract_max_words": 300,
        "keywords_min": 3, "keywords_max": 8,
        "highlights_min": 3, "highlights_max": 5, "highlight_max_chars": 85,
        "blind": True, "authors_max": 20,
    },
}


def list_profiles() -> list[dict]:
    return [{"id": key, **value} for key, value in PROFILES.items()]


def get_profile(profile_id: str) -> dict:
    profile = PROFILES.get(str(profile_id or "course"))
    if profile is None:
        raise ValueError(f"未知投稿档案：{profile_id}")
    return {"id": str(profile_id or "course"), **profile}


def _claims(draft: dict):
    for section in draft.get("sections") or []:
        for block in section.get("blocks") or []:
            for claim in block.get("claims") or []:
                text = " ".join(str(claim.get("text") or "").split())
                if text:
                    yield section, block, claim, text


def _trim(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip("，。；;,. ") + "…"


def highlights(project_id: str, profile_id: str = "course") -> list[dict]:
    """Rule-based highlights from supported claims. Empty is a valid answer."""
    profile = get_profile(profile_id)
    maximum = int(profile["highlights_max"])
    if maximum <= 0:
        return []
    draft = draft_state.read_draft(project_id)
    seen: set[str] = set()
    rows: list[dict] = []
    for section, block, claim, text in _claims(draft):
        if claim.get("stance") != "supported":
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "text": _trim(text, int(profile["highlight_max_chars"])),
            "block_id": block.get("id") or "",
            "section": section.get("title") or "",
            "source": "supported-claim",
        })
        if len(rows) >= maximum:
            break
    return rows


def _profile_items(meta: dict, draft: dict, profile: dict, rows: list[dict]) -> list[dict]:
    items: list[dict] = []
    title = str(meta.get("title") or "")
    if len(title) > int(profile["title_max"]):
        items.append({"level": "error", "kind": "profile_title",
                      "message": f"标题 {len(title)} 字，超过该档案的 {profile['title_max']} 字。",
                      "target": {"panel": "manuscript"}})
    abstract_words = manuscript.word_count(meta.get("abstract"))
    if abstract_words < int(profile["abstract_min_words"]):
        items.append({"level": "warn", "kind": "profile_abstract_short",
                      "message": f"摘要 {abstract_words} 词，低于参考下限 {profile['abstract_min_words']} 词。",
                      "target": {"panel": "manuscript"}})
    if abstract_words > int(profile["abstract_max_words"]):
        items.append({"level": "error", "kind": "profile_abstract_long",
                      "message": f"摘要 {abstract_words} 词，超过参考上限 {profile['abstract_max_words']} 词。",
                      "target": {"panel": "manuscript"}})
    keyword_count = len(meta.get("keywords") or [])
    if keyword_count < int(profile["keywords_min"]) or keyword_count > int(profile["keywords_max"]):
        items.append({"level": "warn", "kind": "profile_keywords",
                      "message": f"关键词 {keyword_count} 个，参考范围 {profile['keywords_min']}–{profile['keywords_max']}。",
                      "target": {"panel": "manuscript"}})
    author_count = len(meta.get("authors") or [])
    if author_count > int(profile["authors_max"]):
        items.append({"level": "warn", "kind": "profile_authors",
                      "message": f"作者 {author_count} 人，超过参考上限 {profile['authors_max']} 人。",
                      "target": {"panel": "manuscript"}})
    minimum = int(profile["highlights_min"])
    if minimum and len(rows) < minimum:
        items.append({"level": "warn", "kind": "profile_highlights",
                      "message": f"仅生成 {len(rows)} 条亮点，参考要求至少 {minimum} 条；请补充已被证据支持的主张。",
                      "target": {"panel": "audit"}})
    authors = meta.get("authors") or []
    if authors and not any(author.get("corresponding") for author in authors):
        items.append({"level": "warn", "kind": "profile_corresponding",
                      "message": "未标记通讯作者；投稿信与标题页只能回退到第一位有邮箱的作者。",
                      "target": {"panel": "manuscript"}})
    if authors and not any(author.get("orcid") for author in authors):
        items.append({"level": "info", "kind": "profile_orcid",
                      "message": "没有作者填写 ORCID；多数投稿系统会要求通讯作者 ORCID。",
                      "target": {"panel": "manuscript"}})
    if not str(meta.get("competing_interests") or "").strip():
        items.append({"level": "info", "kind": "profile_competing",
                      "message": "利益冲突声明为空；投稿信会使用“无利益冲突”的模板句，请发送前确认。",
                      "target": {"panel": "manuscript"}})
    if not str(meta.get("data_availability") or "").strip():
        items.append({"level": "info", "kind": "profile_data",
                      "message": "数据可用性声明为空；多数期刊要求填写。",
                      "target": {"panel": "manuscript"}})
    if profile.get("blind"):
        items.append({"level": "info", "kind": "profile_blind",
                      "message": "双盲档案会生成匿名的正文与 PDF（含致谢移除）；标题页单独保留作者信息。",
                      "target": {"panel": "submission"}})
    return items


def check(project_id: str, profile_id: str = "course") -> dict:
    """Profile rules layered on the existing pre-flight. Read-only."""
    profile = get_profile(profile_id)
    meta = manuscript.read(project_id)
    draft = draft_state.read_draft(project_id)
    preflight = manuscript.preflight(project_id, profile.get("citation_style"))
    rows = highlights(project_id, profile["id"])
    items = [{**item, "source": "preflight"} for item in preflight.get("items") or []]
    items.extend(_profile_items(meta, draft, profile, rows))
    order = {"error": 0, "warn": 1, "info": 2}
    items.sort(key=lambda item: order.get(item.get("level"), 3))
    counts = {level: sum(1 for item in items if item.get("level") == level)
              for level in ("error", "warn", "info")}
    blocking = counts["error"] > 0
    return {
        "project_id": project_id,
        "profile": profile,
        "items": items,
        "counts": counts,
        "blocking": blocking,
        "highlights": rows,
        "body_words": preflight.get("body_words", 0),
        "references": preflight.get("references", 0),
        "generated_at": project_state._now(),
    }


def _author_line(meta: dict) -> dict:
    authors = [a for a in meta.get("authors") or [] if str(a.get("name") or "").strip()]
    if not authors:
        return {"name": "", "affiliation": "", "email": "", "orcid": ""}
    corresponding = [a for a in authors if a.get("corresponding")]
    pool = corresponding or authors
    with_email = next((a for a in pool if str(a.get("email") or "").strip()), pool[0])
    return {
        "name": str(with_email.get("name") or ""),
        "affiliation": str(with_email.get("affiliation") or ""),
        "email": str(with_email.get("email") or ""),
        "orcid": str(with_email.get("orcid") or ""),
    }


def cover_letter(project_id: str, profile_id: str = "course") -> str:
    profile = get_profile(profile_id)
    meta = manuscript.read(project_id)
    rows = highlights(project_id, profile["id"])
    corresponding = _author_line(meta)
    title = str(meta.get("title") or "（未命名稿件）")
    abstract = " ".join(str(meta.get("abstract") or "").split())
    summary = abstract[:600] + ("…" if len(abstract) > 600 else "")
    bullet = "\n".join(f"- {row['text']}" for row in rows) or "- （尚未生成亮点：请先补充被证据支持的主张）"
    lines = [
        "# Cover Letter（模板，发送前请人工核对）",
        "",
        "Dear Editor,",
        "",
        f'We submit the manuscript “{title}” for consideration as a {profile["label"]} contribution.',
        "",
        "**Summary**",
        "",
        summary or "（摘要尚未填写）",
        "",
        "**Highlights**",
        "",
        bullet,
        "",
        "The manuscript is original, has not been published elsewhere, and is not under consideration "
        "by another journal. All authors have approved this submission.",
        "",
        "**Competing interests**",
        "",
        str(meta.get("competing_interests") or "The authors declare no competing interests.（模板句，发送前请确认）"),
        "",
        "Sincerely,",
        "",
        corresponding["name"] or "（通讯作者姓名）",
        corresponding["affiliation"] or "（单位）",
        corresponding["email"] or "（邮箱）",
        (f"ORCID: {corresponding['orcid']}" if corresponding.get("orcid") else ""),
        "",
        "---",
        "本文件由 PaperPilot 依据稿件元数据与已被证据支持的主张生成，是模板而非最终投稿信。",
    ]
    return "\n".join(lines) + "\n"


def title_page(project_id: str) -> str:
    meta = manuscript.read(project_id)
    lines = [
        "# Title Page",
        "",
        f"**Title:** {meta.get('title') or '（未命名稿件）'}",
        f"**Subtitle:** {meta.get('subtitle') or ''}".rstrip(),
        "",
        "**Authors**",
        "",
    ]
    for author in meta.get("authors") or []:
        if not str(author.get("name") or "").strip():
            continue
        lines.append(
            f"- {author.get('name')} · {author.get('affiliation') or '（单位）'}"
            f" · {author.get('email') or '（邮箱）'}"
            + (f" · ORCID {author['orcid']}" if author.get("orcid") else "")
            + (" · **通讯作者**" if author.get("corresponding") else "")
        )
    if not any(str(a.get("name") or "").strip() for a in meta.get("authors") or []):
        lines.append("- （未填写作者）")
    lines += [
        "",
        f"**Keywords:** {', '.join(meta.get('keywords') or []) or '（未填写）'}",
        f"**Funding:** {meta.get('funding') or '（未填写）'}",
        "",
        f"**Author contributions:** {meta.get('contributions') or '（未填写）'}",
        f"**Data availability:** {meta.get('data_availability') or '（未填写）'}",
        f"**Code availability:** {meta.get('code_availability') or '（未填写）'}",
        f"**Competing interests:** {meta.get('competing_interests') or '（未填写）'}",
        "",
    ]
    return "\n".join(lines)


def highlights_markdown(project_id: str, profile_id: str = "course") -> str:
    profile = get_profile(profile_id)
    rows = highlights(project_id, profile["id"])
    lines = [f"# Highlights（{profile['label']}，每条 ≤{profile['highlight_max_chars']} 字符）", ""]
    if not rows:
        lines.append("（没有可用的已支持主张；请先运行语义核查或补充证据。）")
    for row in rows:
        lines.append(f"- {row['text']}")
    lines.append("")
    lines.append("> 由被证据支持的主张自动截取，发送前请人工改写。")
    return "\n".join(lines) + "\n"


def checklist_markdown(result: dict) -> str:
    profile = result["profile"]
    lines = [
        f"# 投稿检查 — {profile['label']}",
        "",
        f"- 正文字数：{result.get('body_words', 0)}",
        f"- 参考文献：{result.get('references', 0)}",
        f"- 错误/提醒/提示：{result['counts']['error']} / {result['counts']['warn']} / {result['counts']['info']}",
        f"- 阻断导出：{'是' if result.get('blocking') else '否'}",
        "",
        "## 检查项",
        "",
    ]
    if not result["items"]:
        lines.append("- [x] 没有发现问题")
    for item in result["items"]:
        mark = " " if item.get("level") == "error" else "x"
        lines.append(f"- [{mark}] **{item.get('level')}** {item.get('message')}")
    lines.append("")
    return "\n".join(lines)


def _meta_for_export(meta: dict, blinded: bool) -> dict:
    payload = copy.deepcopy(meta)
    if blinded:
        payload["authors"] = []
        payload["funding"] = ""
        payload["acknowledgments"] = ""
    return payload


def _safe_name(title: str) -> str:
    value = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in str(title or "")).strip()
    value = "_".join(value.split())
    return (value or "paperpilot")[:60]


def filename(project: dict, suffix: str) -> str:
    return f"{_safe_name(project.get('title'))}-{suffix}"


def package(project_id: str, profile_id: str = "course") -> dict:
    """Build the submission ZIP in memory. PDF is optional, never blocking."""
    project = project_service.get_project(project_id)
    if not project:
        raise KeyError(project_id)
    profile = get_profile(profile_id)
    meta = manuscript.read(project_id)
    draft = draft_state.read_draft(project_id)
    style = profile.get("citation_style") or meta.get("citation_style") or "numeric"
    blinded = bool(profile.get("blind"))
    export_meta = _meta_for_export(meta, blinded)
    check_result = check(project_id, profile["id"])

    files: list[str] = []
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        def add(name: str, payload: bytes | str) -> None:
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
            archive.writestr(name, payload)
            files.append(name)

        add("manuscript.docx", docx_export.build(draft, export_meta, style=style, blinded=blinded))
        pdf_note = ""
        try:
            add("manuscript.pdf", pdf_export.build(draft, export_meta, style=style, blinded=blinded))
        except pdf_export.PDFUnavailable as exc:
            pdf_note = f"PDF 未包含：{exc}"
        except pdf_export.PDFBuildError as exc:
            pdf_note = f"PDF 编译失败：{exc}"
        if pdf_note:
            add("PDF-NOTE.txt", pdf_note + "\n其余文件不受影响。\n")
        add("cover-letter.md", cover_letter(project_id, profile["id"]))
        add("highlights.md", highlights_markdown(project_id, profile["id"]))
        add("title-page.md", title_page(project_id))
        add("checklist.md", checklist_markdown(check_result))
        add("references.ris", references_io.to_ris(draft))
        add("references.bib", draft_export.export_bibtex(draft))
        add("response-template.md", reviewer.response_template(project_id))
        add("README.txt", "\n".join([
            f"PaperPilot submission package · {profile['label']}",
            f"project: {project.get('title') or project_id}",
            f"blinded manuscript: {'yes' if blinded else 'no'}",
            f"citation style: {style}",
            f"generated: {project_state._now()}",
            "",
            "files: " + ", ".join(files),
            "",
            "cover-letter.md / highlights.md / checklist.md are templates or rule-based summaries;",
            "review them before sending. response-template.md is not a substitute for a real peer review.",
        ]) + "\n")
    return {
        "content": buffer.getvalue(),
        "filename": f"{_safe_name(project.get('title'))}-submission.zip",
        "media_type": "application/zip",
        "files": files,
        "pdf_included": "manuscript.pdf" in files,
        "blinded": blinded,
        "profile": profile,
        "check": {"counts": check_result["counts"], "blocking": check_result["blocking"]},
    }
