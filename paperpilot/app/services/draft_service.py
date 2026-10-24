from __future__ import annotations

import copy
import hashlib
import json
import re

from .. import config
from . import (
    citation_styles,
    contradictions,
    docx_export,
    manuscript,
    pdf_export,
    draft_audit, draft_export, draft_state, llm_gateway, project_service,
    project_state,
)

_CITATION_KEY_RE = re.compile(r"[A-Za-z0-9:_-]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


class DraftConflict(ValueError):
    pass


class DraftValidation(ValueError):
    pass


def _audit_high(project_id: str) -> int:
    """Return the high-issue count of the latest ``kind="audit"`` decision."""
    decisions = project_state.read_memory(project_id).get("decisions") or []
    latest: dict | None = None
    latest_key: tuple[str, int] = ("", -1)
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict) or decision.get("kind") != "audit":
            continue
        key = (str(decision.get("at") or ""), index)
        if latest is None or key > latest_key:
            latest = decision
            latest_key = key
    if latest is None:
        return 0
    choice = latest.get("choice")
    if not isinstance(choice, dict):
        return 0
    try:
        return max(0, int(choice.get("high") or 0))
    except (TypeError, ValueError):
        return 0


def summary(project_id: str) -> dict:
    data = draft_state.summary(draft_state.read_draft(project_id))
    data["audit_high"] = _audit_high(project_id)
    return data


def get_draft(project_id: str) -> dict:
    return draft_state.read_draft(project_id)


def export_draft(project_id: str, fmt: str, appendix: bool = False,
                 style: str | None = None) -> dict:
    """Render the current draft into a downloadable payload.

    Returns the encoded ``content``, a sanitized ``filename`` and the
    ``media_type`` so the API layer can stream it without re-encoding. The
    draft is read as-is; exporting never changes draft state or revision.
    """
    if fmt not in {"markdown", "latex", "bibtex", "json", "docx", "pdf"}:
        raise DraftValidation("未知导出格式。")
    project = project_service.get_project(project_id)
    if not project:
        raise KeyError(project_id)
    draft = draft_state.read_draft(project_id)
    memory = project_state.read_memory(project_id)
    meta = manuscript.read(project_id)
    style = style or meta.get("citation_style") or draft.get("citation_style") or "numeric"
    if fmt == "markdown":
        content = draft_export.export_markdown(
            draft, project, memory, appendix, style=style
        ).encode("utf-8")
        media = "text/markdown; charset=utf-8"
    elif fmt == "latex":
        content = draft_export.export_latex_zip(draft, style=style)
        media = "application/zip"
    elif fmt == "docx":
        content = docx_export.build(draft, meta, style=style)
        media = ("application/vnd.openxmlformats-officedocument"
                 ".wordprocessingml.document")
    elif fmt == "pdf":
        content = pdf_export.build(draft, meta, style=style)
        media = "application/pdf"
    elif fmt == "bibtex":
        content = draft_export.export_bibtex(draft).encode("utf-8")
        media = "application/x-bibtex; charset=utf-8"
    else:
        content = draft_export.export_json(draft, project, memory).encode("utf-8")
        media = "application/json; charset=utf-8"
    return {
        "content": content,
        "filename": draft_export.download_name(project, fmt),
        "media_type": media,
    }


def outline(project_id: str) -> list[dict]:
    draft = draft_state.read_draft(project_id)
    return [
        {
            "id": section.get("id") or "",
            "title": section.get("title") or "",
            "blocks": len(section.get("blocks") or []),
            "non_empty": sum(
                1
                for block in section.get("blocks") or []
                if str(block.get("text") or "").strip()
            ),
        }
        for section in draft.get("sections") or []
    ]


def _require_project(project_id: str) -> dict:
    project = project_service.get_project(project_id)
    if not project:
        raise KeyError(project_id)
    return project


def _revision(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise DraftConflict("缺少有效的草稿修订号。") from None


def _citation_key(paper: dict, existing: set[str]) -> str:
    authors = paper.get("authors") or []
    first = str(authors[0] if authors else "").strip()
    parts = first.split()
    family = re.sub(r"[^A-Za-z0-9]", "", parts[-1] if parts else "")
    if not family:
        paper_id = str(paper.get("id") or "")
        family = "paper" + hashlib.sha1(paper_id.encode("utf-8")).hexdigest()[:6]
    year = str(paper.get("year") or "")
    base = f"{family}{year}".lower() or "paper"
    key = base
    index = 1
    while key in existing:
        suffix = chr(96 + index) if index <= 26 else str(index)
        key = f"{base}{suffix}"
        index += 1
    return key


def _string_list(value, message: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise DraftValidation(message)
    return [str(item) for item in value]


def _validate_sections(
    sections,
    memory: dict,
    statuses: dict[str, str] | None = None,
    claims_map: dict[str, list[dict]] | None = None,
) -> list[dict]:
    if not isinstance(sections, list):
        raise DraftValidation("sections 必须是数组。")
    if len(sections) > draft_state.MAX_SECTIONS:
        raise DraftValidation("章节数量超过上限。")
    statuses = statuses or {}
    claims_map = claims_map or {}
    evidence_ids = {
        str(item.get("id"))
        for item in memory.get("evidence") or []
        if isinstance(item, dict) and item.get("id")
    }
    seen_sections: set[str] = set()
    seen_blocks: set[str] = set()
    total_blocks = 0
    total_chars = 0
    clean: list[dict] = []
    for section in sections:
        if not isinstance(section, dict):
            raise DraftValidation("章节格式不正确。")
        section_id = str(section.get("id") or "").strip()
        if section_id and section_id in seen_sections:
            raise DraftValidation("章节 ID 重复。")
        if section_id:
            seen_sections.add(section_id)
        blocks = section.get("blocks")
        if blocks is None:
            blocks = []
        if not isinstance(blocks, list):
            raise DraftValidation("段落格式不正确。")
        if len(blocks) > draft_state.MAX_BLOCKS_PER_SECTION:
            raise DraftValidation("单节块数超过上限。")
        clean_blocks: list[dict] = []
        for block in blocks:
            if not isinstance(block, dict):
                raise DraftValidation("段落格式不正确。")
            block_id = str(block.get("id") or "").strip()
            if block_id and block_id in seen_blocks:
                raise DraftValidation("段落 ID 重复。")
            if block_id:
                seen_blocks.add(block_id)
            text = str(block.get("text") or "")
            if len(text) > draft_state.MAX_BLOCK_CHARS:
                raise DraftValidation("单块文本超过上限。")
            total_chars += len(text)
            if total_chars > draft_state.MAX_TOTAL_CHARS:
                raise DraftValidation("草稿文本超过总上限。")
            total_blocks += 1
            if total_blocks > draft_state.MAX_TOTAL_BLOCKS:
                raise DraftValidation("草稿块数超过总上限。")
            evidence = _string_list(block.get("evidence_ids"), "证据列表格式不正确。")
            unknown = [item for item in evidence if item not in evidence_ids]
            if unknown:
                raise DraftValidation(f"证据不存在：{unknown[0]}")
            clean_block = {
                **copy.deepcopy(block),
                "id": block_id or None,
                "text": text,
                "evidence_ids": evidence,
                "citation_keys": _string_list(
                    block.get("citation_keys"), "引用列表格式不正确。"
                ),
            }
            # Claims are written only by audits/models, never by the editor.
            # A UI save therefore carries no claims; keep the stored ones.
            if "claims" in block:
                clean_block["claims"] = draft_state.normalize_claims(block.get("claims"))
            else:
                clean_block["claims"] = copy.deepcopy(claims_map.get(block_id) or [])
            status = block.get("status")
            if isinstance(status, str) and status in draft_state.DRAFT_STATUSES:
                clean_block["status"] = status
            elif status is None:
                # An omitted status on an existing block keeps its audit
                # result; only genuinely new blocks default to "draft".
                preserved = statuses.get(block_id)
                clean_block["status"] = (
                    preserved
                    if preserved in draft_state.DRAFT_STATUSES
                    else "draft"
                )
            else:
                # Unknown or unhashable (list/dict) values from the client fall
                # back to "draft" instead of raising TypeError on set lookup.
                clean_block["status"] = "draft"
            clean_blocks.append(clean_block)
        clean.append(
            {
                **copy.deepcopy(section),
                "id": section_id or None,
                "blocks": clean_blocks,
            }
        )
    return clean


def _block_claims(draft: dict) -> dict[str, list[dict]]:
    return {
        str(block.get("id")): copy.deepcopy(block.get("claims") or [])
        for section in draft.get("sections") or []
        for block in section.get("blocks") or []
        if block.get("id")
    }


def _block_statuses(draft: dict) -> dict[str, str]:
    return {
        str(block.get("id")): block.get("status")
        for section in draft.get("sections") or []
        for block in section.get("blocks") or []
        if block.get("id")
        and isinstance(block.get("status"), str)
        and block.get("status") in draft_state.DRAFT_STATUSES
    }


def _has_text(draft: dict) -> bool:
    return any(
        str(block.get("text") or "").strip()
        for section in draft.get("sections") or []
        for block in section.get("blocks") or []
    )


def _write(project_id: str, draft: dict) -> dict:
    draft["updated_at"] = project_state._now()
    draft_state.write_draft(project_id, draft)
    project_service.sync_counts(project_id)
    if _has_text(draft):
        project_service.advance_stage(project_id, "writing")
    return copy.deepcopy(draft)


def update_citation_style(project_id: str, style: str) -> dict | None:
    """Mirror the manuscript's style picker into the draft without bumping revision.

    The style is presentation only: touching the revision here would 409 an edit
    that is already in flight from the other drawer.
    """
    if not citation_styles.is_style(style):
        return None
    with project_state.project_lock():
        _require_project(project_id)
        draft = draft_state.read_draft(project_id)
        if draft.get("citation_style") == style:
            return None
        draft["citation_style"] = style
        draft["updated_at"] = project_state._now()
        draft_state.write_draft(project_id, draft)
        return draft


def save_draft(project_id: str, payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise DraftValidation("草稿格式不正确。")
    with project_state.project_lock():
        _require_project(project_id)
        draft = draft_state.read_draft(project_id)
        current_revision = int(draft.get("revision") or 0)
        if _revision(payload.get("revision")) != current_revision:
            raise DraftConflict("草稿已被修改，请刷新后重试。")
        memory = project_state.read_memory(project_id)
        sections = _validate_sections(
            payload.get("sections") or [], memory, _block_statuses(draft), _block_claims(draft)
        )
        if "citations" in payload:
            citations = {} if payload["citations"] is None else copy.deepcopy(payload["citations"])
        else:
            citations = copy.deepcopy(draft.get("citations") or {})
        if not isinstance(citations, dict):
            raise DraftValidation("引用格式不正确。")
        for item in citations.values():
            if not isinstance(item, dict):
                raise DraftValidation("引用条目格式不正确。")
            key = str(item.get("key") or "")
            if key and not _CITATION_KEY_RE.fullmatch(key):
                raise DraftValidation(f"引用键格式不正确：{key}")
        known_keys = {
            str(item["key"])
            for item in citations.values()
            if isinstance(item, dict) and item.get("key")
        }
        for section in sections:
            for block in section.get("blocks") or []:
                unknown = [
                    key for key in block.get("citation_keys") or []
                    if key not in known_keys
                ]
                if unknown:
                    raise DraftValidation(f"引用不存在：{unknown[0]}")
        normalized, _ = draft_state.normalize_draft(
            {**draft, "sections": sections}
        )
        draft["sections"] = normalized["sections"]
        draft["title"] = str(
            payload.get("title") or draft.get("title") or ""
        )[:200]
        style = payload.get("citation_style") or draft.get("citation_style") or "numeric"
        if not isinstance(style, str) or style not in draft_state.DRAFT_STYLES:
            raise DraftValidation("引用样式不正确。")
        draft["citation_style"] = style
        draft["revision"] = current_revision + 1
        draft["citations"] = citations
        return _write(project_id, draft)


def add_citation(project_id: str, paper_id: str, revision: int) -> dict:
    with project_state.project_lock():
        _require_project(project_id)
        draft = draft_state.read_draft(project_id)
        current_revision = int(draft.get("revision") or 0)
        if _revision(revision) != current_revision:
            raise DraftConflict("草稿已被修改，请刷新后重试。")
        memory = project_state.read_memory(project_id)
        paper = (memory.get("papers") or {}).get(paper_id)
        if not paper:
            raise KeyError(paper_id)
        citations = draft.get("citations") or {}
        existing = {
            str(item["key"])
            for item in citations.values()
            if isinstance(item, dict) and item.get("key")
        }
        current = citations.get(paper_id)
        if isinstance(current, dict) and current.get("key"):
            return {
                "key": str(current["key"]),
                "citation": copy.deepcopy(current),
                "revision": current_revision,
            }
        key = _citation_key(paper, existing)
        venue = paper.get("venue") or ""
        entry_type = str(paper.get("entry_type") or "").strip()
        if entry_type not in {"journal", "conference", "book", "preprint", "web"}:
            lowered = venue.lower()
            if not venue or "arxiv" in lowered or "preprint" in lowered or "biorxiv" in lowered:
                entry_type = "preprint"
            elif "proceedings" in lowered or "conference" in lowered or "workshop" in lowered:
                entry_type = "conference"
            else:
                entry_type = "journal"
        citation = {
            "key": key,
            "title": paper.get("title") or "",
            "authors": list(paper.get("authors") or []),
            "year": paper.get("year"),
            "venue": venue,
            "doi": paper.get("doi") or "",
            "url": paper.get("url") or "",
            # v5: fields the reference styles need. Sources that do not supply
            # them leave them empty, and the style validator downgrades to a
            # warning instead of inventing values.
            "entry_type": entry_type,
            "volume": str(paper.get("volume") or ""),
            "issue": str(paper.get("issue") or ""),
            "pages": str(paper.get("pages") or ""),
            "publisher": str(paper.get("publisher") or ""),
            "language": str(paper.get("language") or ("zh" if _CJK_RE.search(str(paper.get("title") or "")) else "en")),
        }
        draft.setdefault("citations", {})[paper_id] = citation
        draft["revision"] = current_revision + 1
        _write(project_id, draft)
        return {
            "key": key,
            "citation": copy.deepcopy(citation),
            "revision": draft["revision"],
        }


def ensure_from_markdown(project_id: str, markdown: str) -> dict:
    """Import a writing skeleton into the project draft when it is empty.

    ``##``/``###`` headings become sections and unrecognized non-empty lines
    become paragraph blocks with no evidence anchor. An existing structured
    draft is never overwritten; the current draft is returned unchanged.
    """
    with project_state.project_lock():
        draft = draft_state.read_draft(project_id)
        if draft.get("sections"):
            return draft
        sections: list[dict] = []
        current: dict | None = None
        for raw in str(markdown or "").splitlines():
            line = raw.strip()
            heading = re.match(r"^(#{2,3})\s+(.+)$", line)
            if heading:
                current = {"title": heading.group(2).strip()[:120], "blocks": []}
                sections.append(current)
            elif line and current and not line.startswith("#"):
                current["blocks"].append({
                    "type": "paragraph",
                    "text": line[:20000],
                    "evidence_ids": [],
                    "citation_keys": [],
                    "status": "needs_evidence",
                })
        if not sections and str(markdown or "").strip():
            sections = [{
                "title": "草稿",
                "blocks": [{
                    "type": "paragraph",
                    "text": line.strip(),
                    "evidence_ids": [],
                    "citation_keys": [],
                    "status": "needs_evidence",
                } for line in str(markdown).splitlines() if line.strip()],
            }]
        payload = {
            "revision": draft.get("revision", 0),
            "title": draft.get("title") or "研究草稿",
            "citation_style": draft.get("citation_style", "numeric"),
            "sections": sections,
        }
        return save_draft(project_id, payload)


def _next_block_id(draft: dict) -> str:
    used = {
        str(block.get("id"))
        for section in draft.get("sections") or []
        for block in section.get("blocks") or []
        if block.get("id")
    }
    index = sum(
        len(section.get("blocks") or []) for section in draft.get("sections") or []
    ) + 1
    while f"blk-{index:03d}" in used:
        index += 1
    return f"blk-{index:03d}"


def write_section(
    project_id: str,
    section_id: str,
    max_blocks: int = 3,
    use_llm: bool = False,
    run: dict | None = None,
) -> dict:
    """Append evidence-anchored paragraphs to one draft section.

    Offline mode quotes verified project evidence verbatim and marks the block
    as needing rewriting; it never invents data or citations. With ``use_llm``
    the configured LLM may rewrite the paragraph, but the block still carries
    its evidence id, is marked ``needs_evidence`` (its prose is not verified),
    and falls back to the offline text on any failure. A section without unused
    verified evidence gets no body at all.
    """
    draft = draft_state.read_draft(project_id)
    section = next(
        (
            item for item in draft.get("sections") or []
            if item.get("id") == section_id
        ),
        None,
    )
    if not section:
        raise KeyError(section_id)
    memory = project_state.read_memory(project_id)
    used = {
        eid for block in section.get("blocks") or []
        for eid in block.get("evidence_ids") or []
    }
    candidates = [
        item for item in memory.get("evidence") or []
        if item.get("verified")
        and item.get("id") not in used
        and str(item.get("quote") or "").strip()
    ]
    title_words = set(str(section.get("title") or "").lower().split())
    candidates.sort(
        key=lambda item: (
            len(set(str(item.get("quote") or "").lower().split()) & title_words),
            item.get("updated_at") or "",
        ),
        reverse=True,
    )
    try:
        limit = max(1, min(int(max_blocks), 3))
    except (TypeError, ValueError):
        limit = 3
    selected = candidates[:limit]
    if not selected:
        return {"section_id": section_id, "blocks_added": 0, "no_evidence": True}
    citations = draft.setdefault("citations", {})
    existing_keys = {
        str(item.get("key"))
        for item in citations.values()
        if isinstance(item, dict) and item.get("key")
    }
    mode = "offline"
    llm_blocks = 0
    for item in selected:
        paper_id = item.get("paper_id") or ""
        paper = (memory.get("papers") or {}).get(paper_id) or {}
        current = citations.get(paper_id)
        key = current.get("key") if isinstance(current, dict) else None
        if not key and paper:
            key = _citation_key(paper, existing_keys)
            existing_keys.add(key)
            citations[paper_id] = {
                "key": key,
                "title": paper.get("title") or item.get("title") or "",
                "authors": list(paper.get("authors") or []),
                "year": paper.get("year"),
                "venue": paper.get("venue") or "",
                "doi": paper.get("doi") or "",
                "url": paper.get("url") or "",
            }
        quote = str(item.get("quote") or "").strip()[:360]
        text = f"现有研究指出：“{quote}”（待结合研究问题改写）。"
        generated_by_llm = False
        if use_llm and config.LLM_ENABLED:
            try:
                generated, _ = llm_gateway.audited_chat(
                    [
                        {
                            "role": "system",
                            "content": "只用给定证据写一段中文学术草稿，不要添加证据中没有的数据或引用。",
                        },
                        {
                            "role": "user",
                            "content": (
                                f"章节：{section.get('title')}\n证据：{quote}\n"
                                "请写一段 80–160 字的草稿，并保留“待核对”提示。"
                            ),
                        },
                    ],
                    purpose="draft_write",
                    max_tokens=400,
                    run=run,
                )
                generated = generated.strip()
                if generated:
                    text = generated
                    mode = "llm"
                    generated_by_llm = True
                    llm_blocks += 1
            except Exception:
                pass
        section.setdefault("blocks", []).append({
            "id": _next_block_id(draft),
            "type": "paragraph",
            "text": text,
            "evidence_ids": [item["id"]],
            "citation_keys": [key] if key else [],
            # LLM prose is not evidence-supported until reviewed; only the
            # offline template quotes the evidence verbatim.
            "status": "needs_evidence" if generated_by_llm else "draft",
        })
    payload = {
        "revision": draft.get("revision", 0),
        "title": draft.get("title") or "研究草稿",
        "citation_style": draft.get("citation_style", "numeric"),
        "sections": draft.get("sections") or [],
        "citations": draft.get("citations") or {},
    }
    saved = save_draft(project_id, payload)
    return {
        "section_id": section_id,
        "blocks_added": len(selected),
        "evidence_ids": [item["id"] for item in selected],
        "mode": mode,
        "llm_blocks": llm_blocks,
        "draft_revision": saved.get("revision", 0),
    }


def audit_draft(project_id: str) -> dict:
    """Audit the draft, apply block statuses and record a summary decision.

    The decision stores counts only (never issue text) so the project overview
    and stage checks can read the latest audit result cheaply. A clean audit
    of a draft that has blocks moves the project to ``review``; the audit
    itself never rewrites block text.
    """
    with project_state.project_lock():
        _require_project(project_id)
        draft = draft_state.read_draft(project_id)
        memory = project_state.read_memory(project_id)
        result = draft_audit.audit_draft(draft, memory)
        stored_contradictions = contradictions.read(project_id).get("items") or []
        extra = []
        for item in stored_contradictions:
            if item.get("status") == "dismissed":
                continue
            block_ids = [bid for bid in (item.get("draft_blocks") or []) if bid]
            other = (item.get("paper_b") or {}).get("title") or "另一篇论文"
            extra.append({
                "section_id": "",
                "block_id": block_ids[0] if block_ids else "",
                "kind": "contradiction",
                "severity": item.get("severity") or "medium",
                "message": f"与《{other}》在同名指标上方向相反：{item.get('why') or ''}"[:300],
                "evidence_ids": [],
                "contradiction_id": item.get("id"),
            })
        if extra:
            result["issues"].extend(extra)
            counts = {sev: sum(1 for i in result["issues"] if i["severity"] == sev)
                      for sev in ("high", "medium", "low")}
            counts["total"] = len(result["issues"])
            result["counts"] = counts
        for section in draft.get("sections") or []:
            for block in section.get("blocks") or []:
                status = result["status_map"].get(block.get("id"))
                if status in draft_state.DRAFT_STATUSES:
                    block["status"] = status
        draft["revision"] = int(draft.get("revision") or 0) + 1
        draft["updated_at"] = project_state._now()
        draft_state.write_draft(project_id, draft)
        project_service.sync_counts(project_id)
        project_service.add_decision(
            project_id,
            "audit",
            {
                "high": result["counts"]["high"],
                "medium": result["counts"]["medium"],
                "total": result["counts"]["total"],
            },
            "草稿核查",
        )
        if result["counts"]["high"] == 0 and any(
            section.get("blocks") for section in draft.get("sections") or []
        ):
            project_service.advance_stage(project_id, "review")
        result["revision"] = draft["revision"]
        return result


def _next_version_id(versions: list[dict]) -> str:
    highest = 0
    for item in versions:
        if not isinstance(item, dict):
            continue
        match = re.fullmatch(r"ver-(\d+)", str(item.get("id") or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"ver-{highest + 1:03d}"


def create_version(
    project_id: str, label: str = "手动保存", reason: str = "manual"
) -> dict:
    with project_state.project_lock():
        _require_project(project_id)
        draft = draft_state.read_draft(project_id)
        versions = draft.get("versions") or []
        version_id = _next_version_id(versions)
        if not isinstance(reason, str) or reason not in draft_state.VERSION_REASONS:
            reason = "manual"
        version = draft_state.snapshot(
            draft,
            version_id,
            str(label or "").strip()[:40] or "手动保存",
            reason,
        )
        versions.append(version)
        kept = versions[-draft_state.MAX_VERSIONS:]
        pruned = [
            str(item.get("id"))
            for item in versions[: len(versions) - len(kept)]
            if isinstance(item, dict) and item.get("id")
        ]
        draft["versions"] = kept
        draft_state.write_draft(project_id, draft)
        # The snapshot just written is always inside the retained window, so
        # only versions that truly fell out of the index have files removed.
        draft_state.remove_snapshots(project_id, pruned)
        return copy.deepcopy(version)


def list_versions(project_id: str) -> list[dict]:
    return list(reversed(draft_state.read_draft(project_id).get("versions") or []))


def get_version(project_id: str, version_id: str) -> dict:
    draft = draft_state.read_draft(project_id)
    version = next(
        (
            item
            for item in draft.get("versions") or []
            if isinstance(item, dict) and item.get("id") == version_id
        ),
        None,
    )
    if not version:
        raise KeyError(version_id)
    path = draft_state.versions_dir(project_id) / f"{version_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise KeyError(version_id) from None
    if not isinstance(data, dict):
        raise KeyError(version_id)
    return data


def restore_version(project_id: str, version_id: str) -> dict:
    with project_state.project_lock():
        _require_project(project_id)
        snapshot = get_version(project_id, version_id)
        create_version(project_id, "恢复前自动保存", "before_restore")
        current = draft_state.read_draft(project_id)
        restored = copy.deepcopy(snapshot)
        restored["project_id"] = project_id
        restored["revision"] = int(current.get("revision") or 0) + 1
        restored["versions"] = copy.deepcopy(current.get("versions") or [])
        restored["created_at"] = (
            current.get("created_at")
            or snapshot.get("created_at")
            or project_state._now()
        )
        restored["updated_at"] = project_state._now()
        return _write(project_id, restored)
