"""Build a multi-topic constructed golden set from the fetched 4×6 corpus.

Four probes per source sentence:

* ``supported``      the sentence verbatim
* ``partial``        the sentence plus a clause the quote does not contain
* ``missing_number`` the sentence with one number changed (probe=missing_number)
* ``unsupported``    the sentence quoted against a *different* paper's abstract

This is a **constructed** set: the labels follow from how each probe was made,
not from human judgement. It is useful for regression and per-topic coverage,
and must not be reported as a human-annotation result. The provenance field says
``constructed-multitopic`` for that reason.
"""
from __future__ import annotations

from _console import make_console_safe

make_console_safe()

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import grounding, project_state  # noqa: E402

CORPUS = ROOT / "data" / "eval" / "golden" / "corpus"
OUT = ROOT / "data" / "eval" / "golden"
SENTENCES_PER_PAPER = 4
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
CLAUSES = (
    "该方法还可推广到其他数据集、模型规模与硬件平台，并已在真实生产部署中验证。",
    "作者进一步讨论了标注成本、推理延迟、能耗开销与维护复杂度等因素的影响。",
    "实验覆盖了不同批次、不同温度与不同负载条件，并报告了重复实验的方差。",
    "该框架支持增量更新、故障恢复与多租户隔离，适用于长期运行的在线服务。",
    "与现有方案相比，本文还分析了失败案例、边界条件与可能的负迁移风险。",
    "未来工作包括跨语言迁移、低资源场景适配以及与外部知识库的联合推理。",
    "作者建议在实际部署前进行消融实验，并记录数据版本与超参数配置。",
    "上述结论仍需独立复现，尤其是在不同软件栈与硬件加速器上的表现。",
)


def _papers(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [paper for paper in (data.get("papers") or []) if paper.get("abstract")]


def _usable(sentence: str) -> bool:
    # The tokenizer covers ASCII and CJK only; Greek/Arabic sentences would make
    # every probe look "unsupported" for the wrong reason.
    return len(sentence) >= 60 and len(grounding.tokens(sentence)) >= 12


def _sentences(paper: dict) -> list[str]:
    parts = [part.strip() for part in grounding.sentences(paper["abstract"])
             if _usable(part)]
    numeric = [part for part in parts if NUMBER_RE.search(part)]
    other = [part for part in parts if part not in numeric]
    return (numeric + other)[:SENTENCES_PER_PAPER]


def _bump(sentence: str) -> tuple[str, bool]:
    match = NUMBER_RE.search(sentence)
    if not match:
        return sentence, False
    existing = set(NUMBER_RE.findall(sentence))
    try:
        value = float(match.group(0)) * 3 + 1
    except ValueError:
        return sentence, False
    for _ in range(6):
        candidate = f"{value:.2f}".rstrip("0").rstrip(".")
        if candidate not in existing:
            return sentence[:match.start()] + candidate + sentence[match.end():], True
        value += 101
    return sentence, False


def _extra_clause(sentence: str) -> str:
    # ``tokens`` is a set, so repeating one clause adds nothing. Append distinct
    # clauses until the claim has materially more unique tokens than the quote.
    target = max(len(grounding.tokens(sentence)) + 8,
                 int(len(grounding.tokens(sentence)) * 1.5) + 1)
    claim = sentence
    for clause in CLAUSES:
        if len(grounding.tokens(claim)) > target:
            break
        claim = claim + " " + clause
    return claim


def _best_other(sentence: str, paper: dict, papers: list[dict]) -> dict | None:
    best, score = None, 2.0
    for other in papers:
        if other.get("id") == paper.get("id"):
            continue
        value = grounding.overlap(sentence, other["abstract"])
        if value < score:
            best, score = other, value
    return best


def build() -> dict[str, dict]:
    reports = {}
    for path in sorted(CORPUS.glob("*.json")):
        if path.name == "all.json":
            continue
        papers = _papers(path)
        topic = path.stem
        claims: list[dict] = []
        for paper in papers:
            for sentence in _sentences(paper):
                def add(label: str, claim: str, quote: str, evidence_paper: str,
                        note: str, probe: str = "") -> None:
                    item = {
                        "id": f"mt-{topic}-{len(claims) + 1:03d}",
                        "paper_id": paper.get("id"),
                        "evidence_paper_id": evidence_paper,
                        "claim": claim, "evidence_quote": quote,
                        "locator": "Abstract", "label": label,
                        "annotator": "constructed-multitopic", "note": note,
                    }
                    if probe:
                        item["probe"] = probe
                    claims.append(item)

                add("supported", sentence, sentence, paper.get("id"), "verbatim sentence")
                add("partial", _extra_clause(sentence), sentence, paper.get("id"),
                    "claim adds a clause absent from the quote")
                bumped, changed = _bump(sentence)
                if changed:
                    add("partial", bumped, sentence, paper.get("id"),
                        "one number changed", probe="missing_number")
                other = _best_other(sentence, paper, papers)
                if other is not None:
                    quote = other["abstract"]
                    if grounding.classify_claim(sentence, quote)["stance"] == "unsupported":
                        add("unsupported", sentence, quote, other.get("id"),
                            "same-topic sentence from another paper")
        reports[topic] = {
            "version": 1, "topic": topic, "provenance": "constructed-multitopic",
            "status": "labeled", "created_at": project_state._now(),
            "caveat": "Labels follow from probe construction; not human annotation.",
            "papers": [{"id": paper.get("id"), "title": paper.get("title")}
                       for paper in papers],
            "claims": claims,
        }
    return reports


def main() -> int:
    reports = build()
    for topic, payload in reports.items():
        path = OUT / f"multitopic-{topic}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        counts: dict[str, int] = {}
        for item in payload["claims"]:
            counts[item["label"]] = counts.get(item["label"], 0) + 1
        print(topic, len(payload["claims"]), counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
