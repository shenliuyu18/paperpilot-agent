from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TEMP = tempfile.mkdtemp(prefix="paperpilot-workspace-eval-")
os.environ["PAPERPILOT_PROJECT_FILE"] = str(Path(TEMP) / "projects.json")
os.environ["PAPERPILOT_PROJECT_DIR"] = str(Path(TEMP) / "projects")
os.environ["PAPERPILOT_AGENT_FILE"] = str(Path(TEMP) / "agent_runs.json")
os.environ["PAPERPILOT_AGENT_DIR"] = str(Path(TEMP) / "agent_runs")

from app.services import (  # noqa: E402
    agent_compare,
    agent_runs,
    draft_service,
    project_service,
    project_state,
)


def main() -> int:
    start = time.time()
    project = project_service.create_project(
        "面向存算一体的低功耗 ADC 设计",
        "如何在保持精度的同时降低 ADC 功耗？",
        scope="CIM/ADC 架构与电路实现",
        constraints=["优先开放获取论文"],
    )
    first = agent_runs.create_run(
        "面向存算一体的低功耗 ADC 设计",
        mode="auto", max_actions=20, max_papers=4,
        project_id=project["id"], autostart=False,
    )
    first = agent_runs.execute_run(first["id"])
    assert first["status"] == "completed", first["status"]
    assert first.get("report", {}).get("path"), "第一轮没有报告"
    memory = project_state.read_memory(project["id"])
    assert memory["evidence"], "项目记忆没有证据"

    second = agent_runs.create_run(
        "继续验证校准开销",
        mode="auto", max_actions=12, max_papers=4,
        project_id=project["id"], seed_memory=True, autostart=False,
    )
    second = agent_runs.execute_run(second["id"])
    assert second["seed_summary"]["evidence"] > 0, "第二轮没有带入项目证据"
    assert second["status"] == "completed", second["status"]

    draft_service.ensure_from_markdown(project["id"], (
        "## 引言\n\n"
        "存内计算通过把计算搬到存储阵列附近降低数据搬运开销。\n\n"
        "## 结果\n\n"
    ))
    write = draft_service.write_section(project["id"], "sec-002", max_blocks=3)
    assert write["blocks_added"] > 0, "没有生成草稿块"
    audit = draft_service.audit_draft(project["id"])
    markdown = draft_service.export_draft(project["id"], "markdown")["content"].decode("utf-8")
    latex = draft_service.export_draft(project["id"], "latex")["content"]
    bibtex = draft_service.export_draft(project["id"], "bibtex")["content"].decode("utf-8")
    assert "# " in markdown and "## 参考文献" in markdown
    assert latex[:2] == b"PK"
    assert "@article{" in bibtex

    compare = agent_compare.compare_runs(project["id"], [first["id"], second["id"]])
    assert len(compare["runs"]) == 2
    metrics = {
        "first_actions": len(first.get("steps") or []),
        "first_evidence": len(first.get("evidence") or []),
        "second_seed_evidence": second["seed_summary"]["evidence"],
        "second_actions": len(second.get("steps") or []),
        "project_evidence": len(memory.get("evidence") or []),
        "draft_blocks": write["blocks_added"],
        "audit_high": audit["counts"]["high"],
        "compare_shared": len(compare["evidence"]["shared"]),
        "seconds": round(time.time() - start, 2),
    }
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
