"""Portable, hash-checked research dossiers.

A dossier is a small ZIP with the project state a reader needs to audit or
resume the work: project metadata, memory, runs, draft, manuscript, Copilot
history and the model-call audit. PDFs and credentials are deliberately not
included. ``manifest.json`` records every file's size and SHA-256, and the
importer treats the archive as untrusted input.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import config, store as upload_store
from . import (
    agent_state,
    assistant_state,
    draft_state,
    manuscript,
    project_state,
)

FORMAT = "paperpilot-dossier/1"
ZIP_ENTRIES = ("manifest.json", "dossier.json", "DOSSIER.md")
MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_ENTRY_BYTES = 25 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_RUNS = agent_state.MAX_RUNS
ACTIVE_RUN_STATUSES = {"queued", "running", "awaiting_approval", "paused"}
_VERSION_ID = re.compile(r"^ver-\d+-\d+$")

BOUNDARIES = (
    "不包含 PDF 原件、上传文件、解析缓存或其他项目的数据。",
    "不包含模型 API Key、llm_settings*.json、环境变量或其他凭据。",
    "不含全局检索缓存、术语库、订阅与评测语料。",
    "非终态运行导入后会冻结为 interrupted，需要新建运行继续。",
)


class DossierError(ValueError):
    """A dossier is malformed, tampered with, or cannot be imported."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = int(status)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _json_file(path: Path, default):
    if not path.is_file():
        return copy.deepcopy(default)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(default)
    return value


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + "…" if len(text) > limit else text


def _table(text: Any, limit: int = 180) -> str:
    return _clip(text, limit).replace("|", "\\|")


def _root_hash(entries: list[dict]) -> str:
    lines = [
        f"{entry['sha256']}  {entry['path']}"
        for entry in sorted(entries, key=lambda item: item["path"])
    ]
    return _sha256(("\n".join(lines) + "\n").encode("utf-8"))


def _run_ids_for(project: dict, runs: dict) -> list[str]:
    ids = {str(item) for item in project.get("run_ids") or [] if item}
    ids.update(
        str(run_id) for run_id, run in runs.items()
        if isinstance(run, dict) and run.get("project_id") == project.get("id")
    )
    ordered = []
    for run_id in ids:
        run = runs.get(run_id)
        if isinstance(run, dict):
            ordered.append(run_id)
    ordered.sort(key=lambda item: (
        str((runs.get(item) or {}).get("created_at") or ""),
        item,
    ))
    return ordered


def _draft_versions(project_id: str, draft: dict) -> list[dict]:
    versions = []
    directory = project_state.project_dir(project_id) / "versions"
    for item in draft.get("versions") or []:
        if not isinstance(item, dict):
            continue
        version_id = str(item.get("id") or "")
        if not _VERSION_ID.fullmatch(version_id):
            continue
        data = _json_file(directory / f"{version_id}.json", None)
        if isinstance(data, dict):
            versions.append({"id": version_id, "data": data})
    return versions


def _llm_calls(run_ids: set[str]) -> list[dict]:
    path = config.DATA_DIR / "llm_calls.json"
    data = _json_file(path, [])
    if not isinstance(data, list):
        return []
    calls = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if str(item.get("run_id") or "") not in run_ids:
            continue
        calls.append({key: value for key, value in item.items() if key != "detail"})
    return calls[-2000:]


def _render_markdown(dossier: dict) -> str:
    project = dossier.get("project") or {}
    memory = dossier.get("memory") or {}
    draft = dossier.get("draft") or {}
    manuscript_data = dossier.get("manuscript") or {}
    brief = project.get("brief") or {}
    runs = dossier.get("runs") or []
    evidence = memory.get("evidence") or []
    questions = memory.get("open_questions") or []
    decisions = memory.get("decisions") or []
    lines = [
        f"# {_clip(project.get('title'), 120)} · 可复现研究档案",
        "",
        f"> 档案格式：{dossier.get('format')}",
        f"> 导出时间：{dossier.get('exported_at')}",
        f"> 应用版本：{dossier.get('app_version')}",
        f"> 源项目：{dossier.get('source_project_id')}",
        "> 完整数据：dossier.json（本文件为可读摘要）",
        "",
        "## 研究问题",
        "",
        f"- 问题：{_clip(brief.get('question'), 500)}",
        f"- 范围：{_clip(brief.get('scope') or '未填写', 300)}",
        f"- 成功标准：{_clip(brief.get('success_criteria') or '未填写', 300)}",
        f"- 阶段：{project.get('stage') or 'exploring'}",
    ]
    constraints = brief.get("constraints") or []
    if constraints:
        lines.append(f"- 约束：{_clip('；'.join(str(item) for item in constraints), 500)}")
    lines += ["", f"## 证据账本（{len(evidence)}）", ""]
    if evidence:
        lines += ["| 论文 | 位置 | 原文 | 核对 |", "|---|---|---|---|"]
        for item in evidence[:60]:
            lines.append(
                f"| {_table(item.get('title') or item.get('paper_id'), 60)} | "
                f"{_table(item.get('section'), 40)} / p.{item.get('page') if item.get('page') is not None else '—'} | "
                f"{_table(item.get('quote'), 180)} | "
                f"{'已核对' if item.get('verified') else '待核对'} |"
            )
        if len(evidence) > 60:
            lines.append(f"| … | 其余 {len(evidence) - 60} 条见 dossier.json | | |")
    else:
        lines.append("（无证据）")
    lines += ["", f"## 运行轨迹（{len(runs)}）", ""]
    for item in runs:
        run = item.get("run") or {}
        lines += [
            f"### {run.get('id')} · {_clip(run.get('goal'), 100)}",
            "",
            f"- 状态：{run.get('status')} · 模式：{run.get('mode')} · 来源：{run.get('source')}",
            f"- 动作：{len(run.get('steps') or [])} · 证据：{len(run.get('evidence') or [])} · "
            f"开放问题：{len(run.get('open_questions') or [])}",
        ]
        report = _clip(item.get("report"), 800)
        if report:
            lines += ["", "报告摘要：", "", f"> {report}"]
        lines.append("")
    lines += [f"## 草稿结构（revision {draft.get('revision', 0)}）", ""]
    sections = draft.get("sections") or []
    if sections:
        for section in sections:
            blocks = section.get("blocks") or []
            lines.append(
                f"- {_clip(section.get('title'), 80)}：{len(blocks)} 块，"
                f"{sum(len(block.get('claims') or []) for block in blocks)} 条主张"
            )
    else:
        lines.append("（无草稿）")
    lines += ["", "## 稿件元数据", ""]
    lines += [
        f"- 标题：{_clip(manuscript_data.get('title') or '未填写', 200)}",
        f"- 作者：{len(manuscript_data.get('authors') or [])} 位",
        f"- 引用样式：{manuscript_data.get('citation_style') or '未设置'}",
        f"- 关键词：{_clip('、'.join(manuscript_data.get('keywords') or []), 200) or '未填写'}",
    ]
    lines += ["", f"## 开放问题（{len(questions)}）与决策（{len(decisions)}）", ""]
    for item in questions[:20]:
        lines.append(f"- [{item.get('status') or 'open'}] {_clip(item.get('question'), 200)}")
    for item in decisions[:20]:
        choice = item.get("choice")
        if isinstance(choice, list):
            choice = "、".join(str(entry) for entry in choice)
        lines.append(f"- 决策：{_clip(item.get('kind'), 40)} / {_clip(choice, 160)}")
    calls = dossier.get("llm_calls") or []
    lines += ["", f"## 模型调用审计（{len(calls)} 条）", ""]
    if calls:
        for item in calls[-20:]:
            lines.append(
                f"- {item.get('at')} · {item.get('purpose')} · {item.get('model')} · "
                f"{'成功' if item.get('ok') else '失败'} · {item.get('latency_ms')} ms"
            )
    else:
        lines.append("（无模型调用）")
    lines += ["", "## 边界", ""]
    lines += [f"- {item}" for item in (dossier.get("boundaries") or BOUNDARIES)]
    return "\n".join(lines).rstrip() + "\n"


def export_project(project_id: str) -> bytes:
    """Build a portable, read-only dossier for ``project_id``."""
    project = project_state.read_projects().get("projects", {}).get(project_id)
    if not project:
        raise KeyError(project_id)
    project, _ = project_state.normalize_project(copy.deepcopy(project))
    memory = project_state.read_memory(project_id)
    draft = draft_state.read_draft(project_id)
    manuscript_data = manuscript.read(project_id)
    assistant = assistant_state.read_assistant(project_id)
    runs_store = agent_state.read_store().get("runs") or {}
    run_ids = _run_ids_for(project, runs_store)
    runs = []
    for run_id in run_ids:
        run = copy.deepcopy(runs_store[run_id])
        artifact_dir = agent_state.run_dir(run_id)
        report = ""
        report_file = artifact_dir / "report.md"
        if report_file.is_file():
            try:
                report = report_file.read_text(encoding="utf-8")
            except OSError:
                report = ""
        if isinstance(run.get("report"), dict):
            run["report"] = {**run["report"], "path": ""}
        papers = _json_file(artifact_dir / "papers.json", [])
        if not isinstance(papers, list):
            papers = []
        runs.append({"run": run, "papers": papers, "report": report})
    run_id_set = {str(item.get("run", {}).get("id")) for item in runs}
    dossier = {
        "format": FORMAT,
        "app_version": config.APP_VERSION,
        "exported_at": _now(),
        "source_project_id": project_id,
        "boundaries": list(BOUNDARIES),
        "project": project,
        "memory": memory,
        "draft": draft,
        "draft_versions": _draft_versions(project_id, draft),
        "manuscript": manuscript_data,
        "assistant": assistant,
        "runs": runs,
        "llm_calls": _llm_calls(run_id_set),
    }
    dossier_bytes = _canonical_bytes(dossier)
    markdown_bytes = _render_markdown(dossier).encode("utf-8")
    entries = [
        {"path": "DOSSIER.md", "bytes": len(markdown_bytes), "sha256": _sha256(markdown_bytes)},
        {"path": "dossier.json", "bytes": len(dossier_bytes), "sha256": _sha256(dossier_bytes)},
    ]
    manifest = {
        "format": FORMAT,
        "app_version": config.APP_VERSION,
        "created_at": dossier["exported_at"],
        "entries": entries,
        "root_sha256": _root_hash(entries),
    }
    manifest_bytes = _canonical_bytes(manifest)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in (
            ("DOSSIER.md", markdown_bytes),
            ("dossier.json", dossier_bytes),
            ("manifest.json", manifest_bytes),
        ):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, payload)
    return output.getvalue()


def _read_zip(blob: bytes) -> dict[str, bytes]:
    if not blob:
        raise DossierError("档案为空。")
    if len(blob) > MAX_ARCHIVE_BYTES:
        raise DossierError("档案超过 25 MB 上限。", 413)
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except (zipfile.BadZipFile, OSError) as exc:
        raise DossierError("不是有效的 ZIP 档案。") from exc
    infos = archive.infolist()
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise DossierError("档案包含重复条目。")
    if set(names) != set(ZIP_ENTRIES):
        raise DossierError("档案条目不是 dossier.json / DOSSIER.md / manifest.json。")
    total = 0
    for info in infos:
        if info.is_dir() or info.filename not in ZIP_ENTRIES:
            raise DossierError("档案包含不允许的路径。")
        if info.file_size > MAX_ENTRY_BYTES:
            raise DossierError("档案条目超过 25 MB 上限。", 413)
        total += info.file_size
    if total > MAX_UNCOMPRESSED_BYTES:
        raise DossierError("档案解压后超过 100 MB 上限。", 413)
    try:
        return {name: archive.read(name) for name in ZIP_ENTRIES}
    except (OSError, zipfile.BadZipFile) as exc:
        raise DossierError("档案解压失败。") from exc


def _validate_dossier(dossier: dict) -> list[str]:
    if not isinstance(dossier, dict):
        raise DossierError("dossier.json 不是对象。")
    if dossier.get("format") != FORMAT:
        raise DossierError("不支持的档案格式。")
    if not isinstance(dossier.get("app_version"), str):
        raise DossierError("档案缺少应用版本。")
    project = dossier.get("project")
    if not isinstance(project, dict):
        raise DossierError("档案缺少项目元数据。")
    title = str(project.get("title") or "").strip()
    question = str((project.get("brief") or {}).get("question") or "").strip()
    if not 2 <= len(title) <= 80:
        raise DossierError("档案中的项目标题不合法。")
    if not 2 <= len(question) <= 500:
        raise DossierError("档案中的研究问题不合法。")
    for key, limit in (("memory", dict), ("draft", dict), ("manuscript", dict), ("assistant", dict)):
        if not isinstance(dossier.get(key), limit):
            raise DossierError(f"档案缺少 {key}。")
    runs = dossier.get("runs")
    if not isinstance(runs, list):
        raise DossierError("档案缺少运行列表。")
    if len(runs) > MAX_RUNS:
        raise DossierError(f"档案包含超过 {MAX_RUNS} 个运行。")
    seen = set()
    warnings = []
    for item in runs:
        if not isinstance(item, dict) or not isinstance(item.get("run"), dict):
            raise DossierError("运行条目格式错误。")
        run = item["run"]
        run_id = str(run.get("id") or "")
        if not run_id or run_id in seen:
            raise DossierError("运行 ID 缺失或重复。")
        seen.add(run_id)
        if not isinstance(item.get("papers", []), list) or not isinstance(item.get("report", ""), str):
            raise DossierError("运行附件格式错误。")
        if str(run.get("status") or "") in ACTIVE_RUN_STATUSES:
            warnings.append(f"运行 {run_id} 处于 {run.get('status')}，导入后将冻结为 interrupted。")
    if not isinstance(dossier.get("draft_versions", []), list):
        raise DossierError("草稿版本列表格式错误。")
    if not isinstance(dossier.get("llm_calls", []), list):
        raise DossierError("模型调用审计格式错误。")
    return warnings


def _verify(blob: bytes) -> tuple[dict, dict, list[str]]:
    files = _read_zip(blob)
    try:
        manifest = json.loads(files["manifest.json"].decode("utf-8"))
        dossier = json.loads(files["dossier.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DossierError("档案 JSON 无法解析。") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise DossierError("manifest.json 格式不支持。")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise DossierError("manifest.json 缺少条目。")
    expected = {"DOSSIER.md", "dossier.json"}
    found = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise DossierError("manifest.json 条目格式错误。")
        path = str(entry.get("path") or "")
        if path not in expected or path in found:
            raise DossierError("manifest.json 条目路径不合法。")
        found.add(path)
        payload = files[path]
        if int(entry.get("bytes") or -1) != len(payload):
            raise DossierError(f"{path} 大小与清单不一致。")
        if str(entry.get("sha256") or "") != _sha256(payload):
            raise DossierError(f"{path} 哈希与清单不一致。")
    if found != expected:
        raise DossierError("manifest.json 缺少 DOSSIER.md 或 dossier.json。")
    if str(manifest.get("root_sha256") or "") != _root_hash(entries):
        raise DossierError("档案根哈希不一致。")
    warnings = _validate_dossier(dossier)
    return dossier, manifest, warnings


def verify_archive(blob: bytes) -> dict:
    """Validate an archive without writing anything."""
    dossier, manifest, warnings = _verify(blob)
    project = dossier["project"]
    memory = dossier["memory"]
    draft = dossier["draft"]
    runs = dossier["runs"]
    return {
        "ok": True,
        "format": dossier.get("format"),
        "app_version": dossier.get("app_version"),
        "exported_at": dossier.get("exported_at"),
        "root_sha256": manifest.get("root_sha256"),
        "project": {
            "title": project.get("title"),
            "question": (project.get("brief") or {}).get("question"),
            "stage": project.get("stage"),
        },
        "summary": {
            "runs": len(runs),
            "evidence": len(memory.get("evidence") or []),
            "draft_sections": len(draft.get("sections") or []),
            "draft_blocks": sum(len(section.get("blocks") or []) for section in draft.get("sections") or []),
            "assistant_messages": len((dossier.get("assistant") or {}).get("messages") or []),
            "llm_calls": len(dossier.get("llm_calls") or []),
            "draft_versions": len(dossier.get("draft_versions") or []),
        },
        "warnings": warnings,
    }


def _remap(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_remap(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: _remap(item, mapping) for key, item in value.items()}
    return value


def _freeze_run(run: dict) -> None:
    if str(run.get("status") or "") in ACTIVE_RUN_STATUSES:
        run["status"] = "interrupted"
        run["pause_requested"] = False
        run["replan_requested"] = False
        agent_state.add_event(run, "imported", "档案导入时冻结了未完成的运行。")


def _write_versions(project_id: str, versions: list[dict]) -> None:
    directory = project_state.project_dir(project_id) / "versions"
    directory.mkdir(parents=True, exist_ok=True)
    for item in versions:
        version_id = str(item.get("id") or "")
        if not _VERSION_ID.fullmatch(version_id):
            raise DossierError("草稿版本 ID 不合法。")
        data = item.get("data")
        if not isinstance(data, dict):
            raise DossierError("草稿版本内容格式错误。")
        temp = directory / f"{version_id}.json.tmp"
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(directory / f"{version_id}.json")


def import_archive(blob: bytes) -> dict:
    """Validate and import an archive as a new project."""
    dossier, manifest, warnings = _verify(blob)
    project = dossier["project"]
    memory = dossier["memory"]
    draft = dossier["draft"]
    manuscript_data = dossier["manuscript"]
    assistant = dossier["assistant"]
    runs = dossier["runs"]
    if len(memory.get("evidence") or []) > project_state.MAX_PROJECT_EVIDENCE:
        raise DossierError("档案证据数量超过项目上限。")
    if len(memory.get("open_questions") or []) > project_state.MAX_PROJECT_QUESTIONS:
        raise DossierError("档案开放问题数量超过项目上限。")
    if len(memory.get("decisions") or []) > project_state.MAX_PROJECT_DECISIONS:
        raise DossierError("档案决策数量超过项目上限。")
    if len(draft.get("sections") or []) > draft_state.MAX_SECTIONS:
        raise DossierError("档案草稿章节超过上限。")
    if sum(len(section.get("blocks") or []) for section in draft.get("sections") or []) > draft_state.MAX_TOTAL_BLOCKS:
        raise DossierError("档案草稿段落超过上限。")
    existing = agent_state.read_store().get("runs") or {}
    if len(existing) + len(runs) > MAX_RUNS:
        raise DossierError("运行数量超过实例上限，无法导入。", 409)

    old_project_id = str(project.get("id") or dossier.get("source_project_id") or "")
    title = str(project.get("title") or "").strip()
    brief = project.get("brief") or {}
    tags = [
        str(tag) for tag in (project.get("tags") or [])
        if str(tag).strip()
    ][:7]
    if "档案导入" not in tags:
        tags.append("档案导入")
    try:
        created = project_state.create_project(
            title,
            str(brief.get("question") or ""),
            scope=str(brief.get("scope") or ""),
            constraints=list(brief.get("constraints") or []),
            success_criteria=str(brief.get("success_criteria") or ""),
            tags=tags,
        )
    except ValueError as exc:
        status = 409 if "上限" in str(exc) else 422
        raise DossierError(str(exc), status) from exc
    new_project_id = created["id"]
    run_mapping: dict[str, str] = {}
    used = set(existing)
    for item in runs:
        old_id = str((item.get("run") or {}).get("id") or "")
        new_id = agent_state.new_run_id(str((item.get("run") or {}).get("goal") or ""))
        while new_id in used:
            new_id = agent_state.new_run_id(str((item.get("run") or {}).get("goal") or ""))
        used.add(new_id)
        run_mapping[old_id] = new_id
    id_mapping = {old_project_id: new_project_id, **run_mapping}
    created_runs: list[str] = []
    try:
        with project_state.project_lock():
            store = agent_state.read_store()
            for item in runs:
                run = _remap(copy.deepcopy(item["run"]), id_mapping)
                old_id = str((item.get("run") or {}).get("id") or "")
                new_id = run_mapping[old_id]
                run["id"] = new_id
                run["project_id"] = new_project_id
                _freeze_run(run)
                if isinstance(run.get("report"), dict):
                    run["report"] = {**run["report"], "path": ""}
                directory = agent_state.run_dir(new_id)
                papers = item.get("papers") or []
                if papers:
                    (directory / "papers.json").write_text(
                        json.dumps(papers, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                report = str(item.get("report") or "")
                if report:
                    report_path = directory / "report.md"
                    report_path.write_text(report, encoding="utf-8")
                    run.setdefault("report", {})["path"] = str(report_path)
                store.setdefault("runs", {})[new_id] = run
                created_runs.append(new_id)
            agent_state.write_store(store)

            imported_memory = _remap(copy.deepcopy(memory), id_mapping)
            imported_memory["project_id"] = new_project_id
            imported_memory["reconciled_runs"] = [
                run_mapping.get(str(item), str(item))
                for item in (memory.get("reconciled_runs") or [])
            ]
            project_state.write_memory(new_project_id, imported_memory)

            imported_draft = _remap(copy.deepcopy(draft), id_mapping)
            imported_draft["project_id"] = new_project_id
            draft_state.write_draft(new_project_id, imported_draft)
            _write_versions(new_project_id, dossier.get("draft_versions") or [])

            manuscript.write(new_project_id, _remap(copy.deepcopy(manuscript_data), id_mapping))

            imported_assistant = _remap(copy.deepcopy(assistant), id_mapping)
            imported_assistant["project_id"] = new_project_id
            assistant_state.write_assistant(new_project_id, imported_assistant)

            data = project_state.read_projects()
            stored = data["projects"].get(new_project_id)
            if not stored:
                raise DossierError("导入时项目写入失败。")
            stored["stage"] = project.get("stage") if project.get("stage") in project_state.PROJECT_STAGES else "exploring"
            stored["status"] = "active"
            stored["run_ids"] = [run_mapping[str((item.get("run") or {}).get("id"))] for item in runs]
            stored["active_run_id"] = None
            stored["imported_from"] = {
                "project_id": old_project_id,
                "exported_at": dossier.get("exported_at"),
                "app_version": dossier.get("app_version"),
                "root_sha256": manifest.get("root_sha256"),
            }
            stored["counts"] = project_state.project_counts(stored, imported_memory, imported_draft)
            stored["updated_at"] = project_state._now()
            project_state.write_projects(data)
        restored_uploads = 0
        for item in runs:
            for paper in item.get("papers") or []:
                if not isinstance(paper, dict):
                    continue
                paper_id = str(paper.get("id") or "")
                if paper.get("source") != "upload" and not paper_id.startswith("upload-"):
                    continue
                if paper_id in upload_store.UPLOAD_PAPERS:
                    continue
                try:
                    upload_store.register_upload(paper)
                    restored_uploads += 1
                except (OSError, ValueError):
                    warnings.append(f"上传 PDF {paper_id} 的元数据未恢复。")
    except Exception:
        with project_state.project_lock():
            data = project_state.read_projects()
            data.get("projects", {}).pop(new_project_id, None)
            project_state.write_projects(data)
            store = agent_state.read_store()
            for run_id in created_runs:
                store.get("runs", {}).pop(run_id, None)
            agent_state.write_store(store)
            shutil.rmtree(project_state.project_dir(new_project_id), ignore_errors=True)
            for run_id in created_runs:
                shutil.rmtree(agent_state.run_dir(run_id), ignore_errors=True)
        raise

    imported = project_state.read_projects()["projects"][new_project_id]
    return {
        "ok": True,
        "project": {
            "id": imported["id"],
            "title": imported["title"],
            "stage": imported.get("stage"),
            "counts": imported.get("counts"),
            "imported_from": imported.get("imported_from"),
        },
        "summary": {
            "runs": len(runs),
            "evidence": len(imported_memory.get("evidence") or []),
            "draft_sections": len(imported_draft.get("sections") or []),
            "draft_blocks": sum(len(section.get("blocks") or []) for section in imported_draft.get("sections") or []),
            "assistant_messages": len((imported_assistant.get("messages") or [])),
        },
        "warnings": warnings,
        "restored_uploads": restored_uploads if "restored_uploads" in locals() else 0,
    }
