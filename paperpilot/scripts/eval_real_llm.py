"""Exercise the online paths against a real OpenAI-compatible endpoint.

This is deliberately separate from ``eval_content.py``: the normal evaluator is
offline and deterministic, while this script only proves that the *real model*
path works and records what it did. Without ``PAPERPILOT_LLM_API_KEY`` it exits
2 and writes nothing — a skipped real-model run must never look like a pass.

Usage::

    python scripts/eval_real_llm.py --check
    python scripts/eval_real_llm.py --claims --limit 20 --json out/real-llm.json
    python scripts/eval_real_llm.py --contradictions prj-xxxxxxxxxx
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.services import content_eval, contradictions, llm_gateway  # noqa: E402

GOLDEN_DIR = ROOT / "data" / "eval" / "golden"
UNLABELED = {"unlabeled", "needs-adjudication"}


def _configured() -> bool:
    return bool(config.LLM_ENABLED)


def check() -> int:
    print("LLM_ENABLED:", _configured())
    print("base_url:", config.LLM_BASE_URL or "(SDK 默认)")
    print("model:", config.LLM_MODEL)
    print("routes:", config.LLM_ROUTES or "(所有用途共用 LLM_MODEL)")
    if not _configured():
        print("未配置 PAPERPILOT_LLM_API_KEY，真机评测不执行。")
        return 2
    return 0


def _labeled_goldens() -> list[dict]:
    rows = []
    for golden in content_eval.load_golden_dir(GOLDEN_DIR):
        if golden.get("status") in UNLABELED:
            continue
        if golden.get("provenance") == "human-required":
            continue
        rows.append(golden)
    return rows


def _chat_json(system: str, user: str):
    payload, meta = llm_gateway.audited_chat_json(
        system, user, purpose="stance", schema=content_eval.ONLINE_SCHEMA,
        max_tokens=600,
    )
    if payload is None:
        raise RuntimeError(str(meta.get("degrade_reason") or meta.get("status") or "degraded"))
    return payload


def run_claims(limit: int = 0, out_path: Path | None = None) -> int:
    if not _configured():
        print("未配置 PAPERPILOT_LLM_API_KEY，不能运行真机评测。")
        return 2
    goldens = _labeled_goldens()
    if not goldens:
        print("没有已标注金标集；先完成 docs/金标集标注指南.md 的标注流程。")
        return 2
    predictor, online = content_eval.online_predictor(_chat_json)
    started = time.perf_counter()
    reports = []
    remaining = max(0, int(limit or 0))
    for golden in goldens:
        data = copy.deepcopy(golden)
        if remaining:
            data["claims"] = (data.get("claims") or [])[:remaining]
            remaining -= len(data["claims"])
        reports.append(content_eval.evaluate_claims(data, predict=predictor))
    elapsed = round(time.perf_counter() - started, 3)
    payload = {
        "real_model": True,
        "sets": [{"topic": report["topic"], "claims": report["counts"]["claims"],
                  "metrics": report["metrics"]} for report in reports],
        "online": online,
        "gateway": llm_gateway.summary(),
        "elapsed_s": elapsed,
    }
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def run_contradictions(project_id: str, limit: int = 8,
                       out_path: Path | None = None) -> int:
    if not _configured():
        print("未配置 PAPERPILOT_LLM_API_KEY，不能运行真机复核。")
        return 2
    result = contradictions.review(project_id, use_llm=True, limit=limit)
    payload = {"real_model": True, **result}
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not result.get("degraded") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="PaperPilot real-model evaluation")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--claims", action="store_true")
    parser.add_argument("--contradictions", default="", metavar="PROJECT_ID")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()
    if args.check:
        return check()
    if args.claims:
        return run_claims(args.limit, args.json)
    if args.contradictions:
        return run_contradictions(args.contradictions, args.limit or 8, args.json)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
