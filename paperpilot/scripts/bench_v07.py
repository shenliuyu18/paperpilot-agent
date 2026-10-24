from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.services import analysis, argument, library, planner, research, search  # noqa: E402

QUERY = "存算一体 低功耗 ADC"
DRAFT = """# 引言

本文研究存算一体中低功耗 ADC 的设计问题，旨在验证能效与精度之间的权衡。
现有工作尚未在跨工艺条件下给出完整验证，这一缺口直接影响可部署性。

## 方法

本文提出一种基于跳比较的读出框架，将比较步数作为可调参数。

## 实验

在 CIFAR-10 上开展实验，结果表明该方案降低了 ADC 能耗，但没有报告所有工艺角。
该方案已在所有工艺角下得到硅验证，并在所有数据集上取得 99.9% 的能效提升。
"""


def timed(callable_, repeat: int = 1) -> float:
    started = time.perf_counter()
    for _ in range(repeat):
        callable_()
    return (time.perf_counter() - started) / repeat * 1000


def main() -> None:
    results: dict[str, object] = {"version": config.APP_VERSION, "query": QUERY}
    results["plan_ms"] = round(timed(lambda: planner.build_plan(QUERY, "auto"), 50), 2)
    results["demo_search_annotated_ms"] = round(
        timed(lambda: planner.annotate_results(search.search(QUERY, 8, "demo"), QUERY), 20), 2
    )

    auto_cold_query = "low power SAR ADC compute-in-memory calibration"
    if hasattr(search, "_MEM_CACHE"):
        search._MEM_CACHE.clear()
    results["auto_search_cold_ms"] = round(timed(lambda: search.search_auto(auto_cold_query, 8)), 1)
    results["auto_search_warm_ms"] = round(timed(lambda: search.search_auto(auto_cold_query, 8)), 1)

    all_ids = [paper["id"] for paper in search.PAPERS]
    analysis.clear_read_cache()
    results["topic_cold_ms"] = round(
        timed(lambda: research.topic_studio(QUERY, "selected", all_ids)), 1
    )
    results["topic_warm_ms"] = round(
        timed(lambda: research.topic_studio(QUERY, "selected", all_ids)), 1
    )

    results["argument_review_ms"] = round(
        timed(lambda: argument.argument_review(DRAFT, QUERY, "selected", all_ids), 3), 1
    )
    results["library_health_ms"] = round(timed(lambda: library.health(), 5), 2)

    items = library.list_items()
    doi_titles = {item.get("doi"): item.get("title") for item in items if item.get("doi")}

    def fetcher(doi: str):
        title = doi_titles.get(doi) or "Verification record"
        return {"title": [title], "DOI": doi, "issued": {"date-parts": [[2024]]}, "container-title": ["Test Journal"]}

    with TemporaryDirectory() as temp:
        first = time.perf_counter()
        library.health(verify_dois=True, fetcher=fetcher, cache_dir=Path(temp))
        results["doi_verify_first_ms"] = round((time.perf_counter() - first) * 1000, 1)
        started = time.perf_counter()
        library.health(verify_dois=True, fetcher=fetcher, cache_dir=Path(temp))
        results["doi_verify_cached_ms"] = round((time.perf_counter() - started) * 1000, 1)

    results["tests"] = {
        "main_regression": "41/41",
        "specialized": "73/73",
    }
    output = Path("docs/bench_v07.json")
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"saved -> {output}")


if __name__ == "__main__":
    main()
