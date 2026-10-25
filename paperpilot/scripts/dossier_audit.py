"""Offline audit for the v7.0 research-dossier format.

Checks export -> verify -> import -> tamper rejection in an isolated data
directory. Exit 0 only when every check passes; writes JSON evidence to
``out/dossier_audit.json`` by default.

Usage::

    python scripts/dossier_audit.py [--json out/dossier_audit.json]
"""
from __future__ import annotations

from _console import make_console_safe

make_console_safe()

import argparse
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(tempfile.mkdtemp(prefix="pp-dossier-audit-"))
os.environ.update({
    "PAPERPILOT_DATA_DIR": str(DATA),
    "PAPERPILOT_PROJECT_FILE": str(DATA / "projects.json"),
    "PAPERPILOT_PROJECT_DIR": str(DATA / "projects"),
    "PAPERPILOT_AGENT_FILE": str(DATA / "runs.json"),
    "PAPERPILOT_AGENT_DIR": str(DATA / "runs"),
})
sys.path.insert(0, str(ROOT))

from app.services import (  # noqa: E402
    agent_state,
    assistant_state,
    dossier,
    draft_state,
    manuscript,
    project_service,
    project_state,
)

CHECKS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + name + (f"  | {detail}" if detail else ""), flush=True)


def seed() -> tuple[str, str]:
    project = project_service.create_project(
        "档案审计项目", "档案能否被独立校验？", scope="CIM", tags=["audit"]
    )
    pid = project["id"]
    run = agent_state.create_run_state("验证档案", "demo", "auto", project_id=pid)
    rid = run["id"]
    run["status"] = "completed"
    run["steps"] = [{"id": "act-001", "tool": "read", "status": "done"}]
    run["evidence"] = [{
        "id": "ev-001", "paper_id": "P1", "title": "Paper One",
        "section": "Results", "page": 2, "quote": "能效提升 10%",
        "claim": "能效提升", "verified": True, "kind": "result",
    }]
    directory = agent_state.run_dir(rid)
    (directory / "papers.json").write_text(
        json.dumps([{"id": "P1", "title": "Paper One"}], ensure_ascii=False), encoding="utf-8"
    )
    (directory / "report.md").write_text("# 档案审计报告\n\n证据 [P1]。\n", encoding="utf-8")
    run["report"] = {"path": str(directory / "report.md"), "chars": 12, "mode": "offline"}
    store = agent_state.read_store()
    store["runs"][rid] = run
    agent_state.write_store(store)
    projects = project_state.read_projects()
    projects["projects"][pid]["run_ids"] = [rid]
    project_state.write_projects(projects)
    memory = project_state.empty_memory(pid)
    memory["evidence"] = [{
        "id": "pev-001", "paper_id": "P1", "title": "Paper One",
        "section": "Results", "page": 2, "quote": "能效提升 10%",
        "claim": "能效提升", "verified": True, "kind": "result",
        "origin": {"run_id": rid, "tool": "evidence"},
    }]
    memory["reconciled_runs"] = [rid]
    memory["papers"] = {"P1": {"id": "P1", "title": "Paper One"}}
    project_state.write_memory(pid, memory)
    draft = draft_state.empty_draft(pid)
    draft["revision"] = 2
    draft["title"] = "档案审计草稿"
    draft["sections"] = [{
        "id": "sec-001", "title": "引言", "blocks": [{
            "id": "blk-001", "type": "paragraph", "status": "checked",
            "text": "能效提升 10%", "evidence_ids": ["pev-001"], "citation_keys": [],
        }],
    }]
    draft_state.write_draft(pid, draft)
    version = draft_state.snapshot(draft, "ver-001-1", "审计快照", "manual")
    draft["versions"] = [version]
    draft_state.write_draft(pid, draft)
    manuscript.update(pid, {"title": "档案审计稿件", "authors": [{"name": "审计作者"}]})
    assistant_state.append_message(pid, {"role": "user", "text": "档案里有什么？"})
    return pid, rid


def tamper(blob: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(blob)) as source:
        files = {name: source.read(name) for name in source.namelist()}
    files["dossier.json"] = files["dossier.json"].replace(b"Paper One", b"Paper Two")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return output.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()
    pid, rid = seed()
    blob = dossier.export_project(pid)
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        payloads = {name: archive.read(name) for name in names}
    check("D1 档案只有 manifest/dossier/DOSSIER 三个条目", names == set(dossier.ZIP_ENTRIES), sorted(names))
    check("D2 manifest 逐文件哈希正确",
          all(dossier._sha256(payloads[entry["path"]]) == entry["sha256"]
              and len(payloads[entry["path"]]) == entry["bytes"] for entry in manifest["entries"]),
          len(manifest["entries"]))
    check("D3 根哈希与条目一致", manifest["root_sha256"] == dossier._root_hash(manifest["entries"]),
          manifest["root_sha256"][:16])
    verified = dossier.verify_archive(blob)
    check("D4 校验摘要包含项目与计数",
          verified["project"]["title"] == "档案审计项目"
          and verified["summary"]["runs"] == 1 and verified["summary"]["evidence"] == 1,
          verified["summary"])
    imported = dossier.import_archive(blob)
    new_pid = imported["project"]["id"]
    new_rid = project_state.read_projects()["projects"][new_pid]["run_ids"][0]
    check("D5 导入生成新项目与新运行 ID", new_pid != pid and new_rid != rid, f"{new_pid} / {new_rid}")
    imported_run = agent_state.read_store()["runs"][new_rid]
    memory = project_state.read_memory(new_pid)
    draft = draft_state.read_draft(new_pid)
    check("D6 运行/证据来源/草稿版本全部重映射",
          imported_run["project_id"] == new_pid
          and memory["evidence"][0]["origin"]["run_id"] == new_rid
          and [item["id"] for item in draft["versions"]] == ["ver-001-1"]
          and (draft_state.versions_dir(new_pid) / "ver-001-1.json").is_file(),
          f"run={imported_run['status']} versions={len(draft['versions'])}")
    check("D7 稿件与 Copilot 历史保留",
          manuscript.read(new_pid)["title"] == "档案审计稿件"
          and len(assistant_state.read_assistant(new_pid)["messages"]) == 1,
          "manuscript + assistant")
    try:
        dossier.verify_archive(tamper(blob))
        rejected = False
    except dossier.DossierError as exc:
        rejected = "哈希" in str(exc) or "大小" in str(exc)
    check("D8 篡改 dossier 被拒绝", rejected, "hash mismatch")
    with zipfile.ZipFile(io.BytesIO(blob)) as source:
        files = {name: source.read(name) for name in source.namelist()}
    files["extra.txt"] = b"x"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    try:
        dossier.verify_archive(output.getvalue())
        extra_rejected = False
    except dossier.DossierError as exc:
        extra_rejected = "条目" in str(exc)
    check("D9 额外条目被拒绝", extra_rejected, "allowlist")
    secrets = DATA / "llm_settings.secrets.json"
    secrets.write_text('{"api_key":"sk-secret"}', encoding="utf-8")
    blob2 = dossier.export_project(pid)
    with zipfile.ZipFile(io.BytesIO(blob2)) as archive:
        secret_hit = any(b"sk-secret" in archive.read(name) for name in archive.namelist())
    check("D10 档案不含密钥文件内容", not secret_hit, "secrets excluded")
    passed = sum(1 for item in CHECKS if item["ok"])
    out = args.json or (ROOT / "out" / "dossier_audit.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"checks": CHECKS, "passed": passed, "total": len(CHECKS)},
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n{passed}/{len(CHECKS)} dossier checks passed\nartifacts: {out}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
