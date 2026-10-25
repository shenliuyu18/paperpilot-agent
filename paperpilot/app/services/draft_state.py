from __future__ import annotations

import copy
import json

from . import identifiers, project_state

DRAFT_TYPES = {"paragraph", "bullet", "quote", "note"}
DRAFT_STATUSES = {"draft", "needs_evidence", "checked"}
DRAFT_STYLES = {"numeric", "author-year", "gb7714", "apa", "ieee"}
VERSION_REASONS = {"manual", "before_agent_write", "before_restore"}
MAX_SECTIONS = 30
MAX_BLOCKS_PER_SECTION = 80
MAX_TOTAL_BLOCKS = 400
MAX_BLOCK_CHARS = 20000
MAX_TOTAL_CHARS = 200000
MAX_VERSIONS = 20
CLAIM_STANCES = {"supported", "partial", "unsupported", "insufficient", "stale"}
MAX_CLAIMS_PER_BLOCK = 12


def draft_path(project_id: str):
    return project_state.project_dir(project_id) / "draft.json"


def versions_dir(project_id: str):
    path = project_state.project_dir(project_id) / "versions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def empty_draft(project_id: str) -> dict:
    now = project_state._now()
    return {
        "version": 1,
        "project_id": project_id,
        "revision": 0,
        "title": "",
        "citation_style": "numeric",
        "sections": [],
        "citations": {},
        "versions": [],
        "created_at": now,
        "updated_at": now,
    }


def normalize_claims(raw) -> list[dict]:
    """Clamp per-claim records read from clients or older files.

    Claims are optional: a block without them keeps working exactly as before.
    Unknown stances degrade to ``insufficient`` instead of raising, so a
    hand-edited file can never break the draft reader.
    """
    if not isinstance(raw, list):
        return []
    clean: list[dict] = []
    used: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        claim_id = str(item.get("id") or "").strip() or f"clm-{index:03d}"
        while claim_id in used:
            claim_id = f"{claim_id}x"
        used.add(claim_id)
        stance = item.get("stance")
        if stance not in CLAIM_STANCES:
            stance = "insufficient"
        clean.append({
            "id": claim_id,
            "text": text[:300],
            "citation_keys": [str(key) for key in (item.get("citation_keys") or []) if str(key)][:8],
            "evidence_ids": [str(key) for key in (item.get("evidence_ids") or []) if str(key)][:8],
            "stance": stance,
            "reason": str(item.get("reason") or "")[:300],
            "counter_quote": str(item.get("counter_quote") or "")[:300],
            "checked_at": str(item.get("checked_at") or ""),
            "model_disagreed": bool(item.get("model_disagreed")),
        })
        if len(clean) >= MAX_CLAIMS_PER_BLOCK:
            break
    return clean


def _next_id(prefix: str, used: set[str], start: int) -> tuple[str, int]:
    """Return the first free ``<prefix>-NNN`` id at or after ``start``."""
    index = max(1, start)
    while True:
        candidate = f"{prefix}-{index:03d}"
        index += 1
        if candidate not in used:
            return candidate, index


def _normalize_id(
    item: dict, prefix: str, used: set[str], position: int
) -> tuple[str, bool]:
    raw = item.get("id")
    item_id = raw.strip() if isinstance(raw, str) else ""
    if not item_id or item_id in used:
        item_id, _ = _next_id(prefix, used, max(len(used) + 1, position))
        item["id"] = item_id
        return item_id, True
    if item.get("id") != item_id:
        item["id"] = item_id
        return item_id, True
    return item_id, False


def normalize_draft(draft: dict) -> tuple[dict, bool]:
    """Fill defaults and repair ids so a draft is safe to read and save."""
    if not isinstance(draft, dict):
        return empty_draft(""), True
    changed = False
    base = empty_draft(str(draft.get("project_id") or ""))
    for key, value in base.items():
        if key not in draft:
            draft[key] = copy.deepcopy(value)
            changed = True
    if not isinstance(draft.get("project_id"), str):
        draft["project_id"] = str(draft.get("project_id") or "")
        changed = True
    if not isinstance(draft.get("title"), str):
        draft["title"] = str(draft.get("title") or "")
        changed = True
    try:
        revision = max(0, int(draft.get("revision") or 0))
    except (TypeError, ValueError):
        revision = 0
    if revision != draft.get("revision"):
        draft["revision"] = revision
        changed = True
    citation_style = draft.get("citation_style")
    if not isinstance(citation_style, str) or citation_style not in DRAFT_STYLES:
        draft["citation_style"] = "numeric"
        changed = True
    if not isinstance(draft.get("citations"), dict):
        draft["citations"] = {}
        changed = True
    if not isinstance(draft.get("versions"), list):
        draft["versions"] = []
        changed = True
    if not isinstance(draft.get("sections"), list):
        draft["sections"] = []
        changed = True

    used_sections: set[str] = set()
    used_blocks: set[str] = set()
    for section_index, section in enumerate(draft["sections"], start=1):
        if not isinstance(section, dict):
            section = {}
            draft["sections"][section_index - 1] = section
            changed = True
        _, section_changed = _normalize_id(section, "sec", used_sections, section_index)
        changed = changed or section_changed
        used_sections.add(section["id"])
        title = str(section.get("title") or "")[:120]
        if not title:
            title = f"章节 {section_index}"
        if title != section.get("title"):
            section["title"] = title
            changed = True
        blocks = section.get("blocks")
        if not isinstance(blocks, list):
            blocks = []
            section["blocks"] = blocks
            changed = True
        for block_index, block in enumerate(blocks, start=1):
            if not isinstance(block, dict):
                block = {}
                blocks[block_index - 1] = block
                changed = True
            _, block_changed = _normalize_id(block, "blk", used_blocks, block_index)
            changed = changed or block_changed
            used_blocks.add(block["id"])
            # Guard the set membership with isinstance: an unhashable value
            # (list/dict) from a hand-edited or corrupted file must normalize
            # instead of raising TypeError while reading the draft.
            block_type = block.get("type")
            if not isinstance(block_type, str) or block_type not in DRAFT_TYPES:
                block["type"] = "paragraph"
                changed = True
            block_status = block.get("status")
            if not isinstance(block_status, str) or block_status not in DRAFT_STATUSES:
                block["status"] = "draft"
                changed = True
            text = str(block.get("text") or "")
            if text != block.get("text"):
                block["text"] = text
                changed = True
            for key in ("evidence_ids", "citation_keys"):
                values = block.get(key)
                cleaned = (
                    [str(item) for item in values] if isinstance(values, list) else []
                )
                if cleaned != values:
                    block[key] = cleaned
                    changed = True
            claims = normalize_claims(block.get("claims"))
            if claims != block.get("claims"):
                block["claims"] = claims
                changed = True
    return draft, changed


def read_draft(project_id: str) -> dict:
    path = draft_path(project_id)
    with project_state.project_lock():
        if not path.exists():
            return empty_draft(project_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return empty_draft(project_id)
    if not isinstance(data, dict):
        return empty_draft(project_id)
    draft, _ = normalize_draft(data)
    if draft.get("project_id") != project_id:
        # A draft file copied between project directories must never let one
        # project's content leak into another project's path.
        draft["project_id"] = project_id
    return draft


def write_draft(project_id: str, draft: dict) -> None:
    path = draft_path(project_id)
    with project_state.project_lock():
        temp = path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp.replace(path)


def snapshot(draft: dict, version_id: str, label: str, reason: str) -> dict:
    """Persist a full copy of the draft (without its version list)."""
    data = copy.deepcopy(draft)
    data.pop("versions", None)
    project_id = str(draft.get("project_id") or "")
    # Version ids normally come from _next_version_id, but a restored archive supplies
    # its own; an unvalidated one walks out of the versions directory.
    version_id = identifiers.require_safe_id(version_id, "版本标识符")
    path = versions_dir(project_id) / f"{version_id}.json"
    with project_state.project_lock():
        temp = path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp.replace(path)
    sections = draft.get("sections") or []
    return {
        "id": version_id,
        "label": label,
        "reason": reason,
        "revision": int(draft.get("revision") or 0),
        "created_at": project_state._now(),
        "summary": (
            f"{len(sections)} 节 / "
            f"{sum(len(section.get('blocks') or []) for section in sections)} 块"
        ),
        "path": f"versions/{version_id}.json",
    }


def remove_snapshots(project_id: str, version_ids: list[str]) -> None:
    """Delete snapshot files for versions pruned from the draft index.

    The draft index is already the source of truth for which versions are
    restorable, so a failed delete only leaves an unreachable file behind and
    must never turn a successful version creation into an error.
    """
    directory = versions_dir(project_id)
    with project_state.project_lock():
        for version_id in version_ids:
            name = str(version_id or "").strip()
            prefix, _, number = name.partition("-")
            # Only ids this module generates may reach the filesystem, so a
            # hand-edited index cannot turn a prune into a path traversal.
            if prefix != "ver" or not number.isdigit():
                continue
            try:
                (directory / f"{name}.json").unlink(missing_ok=True)
            except OSError:
                pass


def summary(draft: dict) -> dict:
    sections = draft.get("sections") or []
    return {
        "sections": len(sections),
        "blocks": sum(len(section.get("blocks") or []) for section in sections),
        "updated_at": draft.get("updated_at") or "",
        "revision": int(draft.get("revision") or 0),
    }
