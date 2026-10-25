"""Score claim-level support quality against the golden sets.

Usage:

    python scripts/eval_content.py                 # all sets, report only
    python scripts/eval_content.py --strict        # exit 1 when a threshold fails
    python scripts/eval_content.py --json out.json # write the full report
    python scripts/eval_content.py --project prj-x # also report draft coverage
    python scripts/eval_content.py --online        # reserved: real-model review (needs key)

The report separates *constructed* seeds from human annotations: a constructed
seed measures the classifier against machine-built probes and must never be
quoted as a human-annotation result.
"""
from __future__ import annotations

from _console import make_console_safe

make_console_safe()

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PAPERPILOT_DATA_DIR", str(ROOT / "data"))

from app.services import content_eval, draft_state, llm_gateway, project_state  # noqa: E402

DEFAULT_DIR = ROOT / "data" / "eval" / "golden"


def _fmt(value) -> str:
    return "n/a" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)


def _aggregate(reports: list[dict]) -> dict:
    counts = {key: 0 for key in ("claims", "supported_label", "supported_pred",
                                 "true_support", "over_strict", "numeric_cases", "numeric_ok")}
    for report in reports:
        for key in counts:
            counts[key] += int(report["counts"].get(key) or 0)
    rate = content_eval._rate
    return {
        "support_precision": rate(counts["true_support"], counts["supported_pred"]),
        "support_recall": rate(counts["true_support"], counts["supported_label"]),
        "over_strict_rate": rate(counts["over_strict"], counts["supported_label"]),
        "numeric_consistency": rate(counts["numeric_ok"], counts["numeric_cases"]),
        "accuracy": rate(sum(1 for r in reports for row in r["rows"] if row["ok"]),
                         sum(len(r["rows"]) for r in reports)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PaperPilot content-quality evaluation")
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--project", default="", help="project id for draft coverage")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--online", action="store_true", help="reserved for real-model review")
    args = parser.parse_args()

    predictor, online_stats = None, None
    if args.online:
        from app import config as app_config
        if not app_config.LLM_ENABLED:
            print("未配置模型 key，--online 保持离线基准。", flush=True)
        else:
            def chat_json(system: str, user: str):
                payload, meta = llm_gateway.audited_chat_json(
                    system, user, purpose="stance", schema=content_eval.ONLINE_SCHEMA,
                )
                if payload is None:
                    raise RuntimeError(meta.get("status") or "degraded")
                return payload
            predictor, online_stats = content_eval.online_predictor(chat_json)
            print("在线复核已启用（模型把规则分歧记入统计，规则始终优先）。", flush=True)

    all_goldens = content_eval.load_golden_dir(args.golden_dir)
    goldens, skipped = [], []
    for golden in all_goldens:
        unlabeled = golden.get("status") in {"unlabeled", "needs-adjudication"} \
            or golden.get("provenance") == "human-required"
        (skipped if unlabeled else goldens).append(golden)
    if not goldens:
        print(f"没有已标注的金标集：{args.golden_dir}（候选/待裁决 {len(skipped)} 个已跳过）")
        return 2
    if skipped:
        print(f"已跳过未标注或待裁决集合：{[g.get('topic') for g in skipped]}")

    claim_reports, contra_counts = [], {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for golden in goldens:
        claim_reports.append(content_eval.evaluate_claims(golden, predict=predictor))
        contra = content_eval.evaluate_contradictions(golden)
        for key in contra_counts:
            contra_counts[key] += contra["counts"][key]

    metrics = _aggregate(claim_reports)
    rate = content_eval._rate
    metrics["contradiction_recall"] = rate(contra_counts["tp"], contra_counts["tp"] + contra_counts["fn"])
    metrics["contradiction_fpr"] = rate(contra_counts["fp"], contra_counts["fp"] + contra_counts["tn"])

    coverage = None
    if args.project:
        try:
            coverage = content_eval.coverage(draft_state.read_draft(args.project))
        except Exception as exc:  # pragma: no cover - defensive CLI path
            print("读取项目草稿失败：", repr(exc)[:120])
    metrics["coverage"] = coverage

    print("== 金标集 ==")
    for golden, report in zip(goldens, claim_reports):
        print(f"  {golden.get('topic') or report['topic']:<22} claims={report['counts']['claims']:>3} "
              f"provenance={report['provenance'] or '-'}")
    print("\n== 指标 ==")
    for name in ("support_precision", "support_recall", "over_strict_rate",
                 "numeric_consistency", "accuracy", "coverage",
                 "contradiction_recall", "contradiction_fpr"):
        print(f"  {name:<22} {_fmt(metrics.get(name))}")

    checks = content_eval.check_thresholds(metrics)
    print("\n== 门禁 ==")
    failed = []
    for row in checks:
        state = "SKIP" if row["ok"] is None else ("PASS" if row["ok"] else "FAIL")
        if row["ok"] is False:
            failed.append(row)
        print(f"  [{state}] {row['name']:<22} value={_fmt(row['value']):<8} target={row['target']}")

    if online_stats is not None:
        print("\n== 在线复核 ==")
        for key, value in online_stats.items():
            print(f"  {key:<16} {value}")
        print("  gateway:", llm_gateway.summary())
    payload = {
        "online": online_stats,
        "gateway": llm_gateway.summary() if args.online else None,        "generated_at": project_state._now() if hasattr(project_state, "_now") else "",
        "goldens": [{"topic": g.get("topic"), "provenance": g.get("provenance"),
                     "claims": len(g.get("claims") or [])} for g in goldens],
        "metrics": metrics,
        "thresholds": checks,
        "failures": [row["name"] for row in failed],
        "per_set": [{"topic": r["topic"], "metrics": r["metrics"]} for r in claim_reports],
        "contradiction_counts": contra_counts,
    }
    if args.json:
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n报告写入", args.json)

    if args.strict and failed:
        print("\n严格模式：门禁未达标 ->", ", ".join(row["name"] for row in failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
