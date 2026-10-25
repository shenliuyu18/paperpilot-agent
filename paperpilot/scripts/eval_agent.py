from __future__ import annotations

from _console import make_console_safe, make_json_stdout

make_console_safe()

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TEMP = tempfile.mkdtemp(prefix="paperpilot-agent-eval-")
os.environ["PAPERPILOT_AGENT_FILE"] = str(Path(TEMP) / "agent_runs.json")
os.environ["PAPERPILOT_AGENT_DIR"] = str(Path(TEMP) / "agent_runs")

from app.services import agent_runs, agent_state  # noqa: E402


def _run_to_end(run_id: str) -> dict:
    run = agent_runs.get_run(run_id)
    for _ in range(12):
        if run["status"] in {"completed", "failed", "cancelled"}:
            return run
        if run["status"] == "awaiting_approval":
            plan = run.get("plan") or {}
            last = run["steps"][-1] if run.get("steps") else {}
            extra = {}
            if last.get("tool") == "search" and plan.get("tool") == "read":
                extra["paper_ids"] = (run.get("paper_ids") or [])[:3]
            if last.get("tool") == "topic":
                questions = (last.get("artifact") or {}).get("questions") or []
                if questions:
                    extra["topic_id"] = questions[0].get("id")
            run = agent_runs.act(run_id, "approve", autostart=False, **extra)
        run = agent_runs.execute_run(run_id)
    return run


def main() -> None:
    agent_state.write_store({"version": 2, "runs": {}})
    agent_runs._RECOVERED.clear()
    auto = agent_runs.create_run("存算一体 低功耗 ADC", mode="auto", max_papers=4, autostart=False)
    auto = _run_to_end(auto["id"])
    auto_tools = [step.get("tool") for step in auto.get("steps", [])]

    checkpoint = agent_runs.create_run("存算一体 低功耗 ADC", mode="checkpoint", max_papers=4, autostart=False)
    checkpoint = _run_to_end(checkpoint["id"])
    decisions = [item.get("kind") for item in checkpoint.get("decisions", [])]

    budget = agent_runs.create_run("存算一体 低功耗 ADC", mode="auto", max_actions=2, max_papers=3, autostart=False)
    budget = _run_to_end(budget["id"])

    result = {
        "auto": {
            "status": auto["status"],
            "actions": len(auto.get("steps", [])),
            "tools": auto_tools,
            "evidence": len(auto.get("evidence", [])),
            "open_questions": len(agent_state.open_questions(auto)),
            "report_chars": (auto.get("report") or {}).get("chars", 0),
        },
        "checkpoint": {
            "status": checkpoint["status"],
            "actions": len(checkpoint.get("steps", [])),
            "decisions": decisions,
            "evidence": len(checkpoint.get("evidence", [])),
            "report_chars": (checkpoint.get("report") or {}).get("chars", 0),
        },
        "budget": {
            "status": budget["status"],
            "actions": len(budget.get("steps", [])),
            "last_tool": (budget.get("steps") or [{}])[-1].get("tool"),
            "report_chars": (budget.get("report") or {}).get("chars", 0),
        },
        "temp_dir": TEMP,
    }
    make_json_stdout()
    print(json.dumps(result, ensure_ascii=False, indent=2))

    assert auto["status"] == "completed", result
    assert auto.get("report", {}).get("chars", 0) > 1000, result
    assert "report" in auto_tools and "search" in auto_tools, result
    assert checkpoint["status"] == "completed", result
    assert "papers" in decisions and "topic" in decisions, result
    assert budget["status"] == "completed", result
    assert (budget.get("steps") or [{}])[-1].get("tool") == "report", result
    print("agent eval: PASS")


if __name__ == "__main__":
    main()
