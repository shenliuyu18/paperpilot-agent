from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from . import config

UPLOAD_PAPERS: dict[str, dict] = {}
_LOCK = threading.RLock()


def _upload_dir() -> Path:
    override = os.getenv("PAPERPILOT_UPLOAD_DIR")
    if override:
        path = Path(override)
    else:
        data_override = os.getenv("PAPERPILOT_DATA_DIR")
        path = Path(data_override) / "uploads" if data_override else config.UPLOAD_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def register_upload(paper: dict) -> None:
    paper_id = paper.get("id")
    if not paper_id:
        raise ValueError("上传论文缺少 id。")
    path = _upload_dir() / f"{paper_id}.paper.json"
    temp = path.with_suffix(".json.tmp")
    with _LOCK:
        UPLOAD_PAPERS[paper_id] = paper
        temp.write_text(json.dumps(paper, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)


def restore_uploads() -> int:
    count = 0
    for path in sorted(_upload_dir().glob("*.paper.json")):
        try:
            paper = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(paper, dict) or not paper.get("id"):
            continue
        with _LOCK:
            UPLOAD_PAPERS[paper["id"]] = paper
        count += 1
    return count
