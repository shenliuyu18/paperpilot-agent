"""Paper fingerprints and the incremental index.

The index answers one question cheaply: *have we seen this paper before, and
which projects/subscriptions already know about it?* It stores fingerprints and
pointers only — never full text, never abstracts.

Fingerprint order: DOI → arXiv id → hash(title + year). The first two are stable
across sources, so the same paper arriving from Crossref and arXiv collapses to
one entry.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

import os
from pathlib import Path

from . import project_state

FILE_NAME = "index.json"
MAX_PAPERS = 5000
MAX_PROJECTS_PER_PAPER = 20

_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")
_ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
_NORMALIZE_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def _now() -> str:
    return project_state._now()


def path():
    # Resolved per call (like project_state) so tests and deployments can point
    # the index at another file without re-importing the module.
    from .. import config
    override = os.getenv("PAPERPILOT_INDEX_FILE")
    return Path(override) if override else config.DATA_DIR / FILE_NAME


def fingerprint(paper: dict) -> str:
    doi = str(paper.get("doi") or "").strip().lower()
    if not doi:
        match = _DOI_RE.search(str(paper.get("url") or ""))
        doi = match.group(0).lower() if match else ""
    if doi:
        return "doi:" + doi.rstrip(".")
    for candidate in (str(paper.get("id") or ""), str(paper.get("url") or "")):
        match = _ARXIV_RE.search(candidate)
        if match:
            return "arxiv:" + match.group(1)
    title = _NORMALIZE_RE.sub("", str(paper.get("title") or "").lower())
    year = str(paper.get("year") or "")
    return "title:" + hashlib.sha1((title + "|" + year).encode("utf-8")).hexdigest()[:16]


def content_hash(paper: dict) -> str:
    blob = "|".join(str(paper.get(key) or "") for key in ("title", "abstract", "year"))
    return "sha1:" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def empty() -> dict:
    return {"version": 1, "papers": {}, "updated_at": ""}


def read() -> dict:
    file_path = path()
    if not file_path.exists():
        return empty()
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty()
    if not isinstance(data, dict) or not isinstance(data.get("papers"), dict):
        return empty()
    data.setdefault("version", 1)
    data.setdefault("updated_at", "")
    return data


def write(data: dict) -> None:
    from .. import config
    with project_state.project_lock():
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        file_path = path()
        temp = file_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(file_path)


def _prune(papers: dict) -> dict:
    if len(papers) <= MAX_PAPERS:
        return papers
    ranked = sorted(papers.items(), key=lambda item: str(item[1].get("last_seen") or ""))
    for paper_id, _ in ranked[: len(papers) - MAX_PAPERS]:
        papers.pop(paper_id, None)
    return papers


def sync(papers: list[dict], *, project_id: str = "", subscription_id: str = "") -> dict:
    """Insert/update papers and report which ones were new."""
    data = read()
    known = {item.get("fingerprint"): paper_id for paper_id, item in data["papers"].items()}
    added, updated = [], []
    stamp = _now()
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        paper_id = str(paper.get("id") or "").strip()
        if not paper_id:
            continue
        fp = fingerprint(paper)
        effective_id = known.get(fp) or paper_id
        entry = data["papers"].get(effective_id)
        if entry is None:
            entry = {
                "fingerprint": fp, "content_hash": content_hash(paper),
                "title": str(paper.get("title") or ""), "year": str(paper.get("year") or ""),
                "source": str(paper.get("source") or ""), "first_seen": stamp,
                "projects": [], "subscriptions": [],
            }
            data["papers"][effective_id] = entry
            known[fp] = effective_id
            added.append({"paper_id": effective_id, "fingerprint": fp, "title": entry["title"]})
        else:
            new_hash = content_hash(paper)
            if new_hash != entry.get("content_hash"):
                entry["content_hash"] = new_hash
                entry["title"] = str(paper.get("title") or entry.get("title") or "")
                updated.append({"paper_id": effective_id, "fingerprint": fp, "title": entry["title"]})
        entry["last_seen"] = stamp
        if project_id:
            projects = entry.setdefault("projects", [])
            if project_id not in projects and len(projects) < MAX_PROJECTS_PER_PAPER:
                projects.append(project_id)
        if subscription_id:
            subs = entry.setdefault("subscriptions", [])
            if subscription_id not in subs:
                subs.append(subscription_id)
    data["papers"] = _prune(data["papers"])
    data["updated_at"] = stamp
    write(data)
    return {"added": added, "updated": updated, "total": len(data["papers"]), "at": stamp}


def status() -> dict:
    data = read()
    linked = sum(1 for item in data["papers"].values() if item.get("projects"))
    return {
        "total": len(data["papers"]),
        "linked_projects": linked,
        "updated_at": data.get("updated_at") or "",
        "max": MAX_PAPERS,
    }


def find(paper_id: str) -> dict | None:
    return copy.deepcopy(read()["papers"].get(paper_id))
