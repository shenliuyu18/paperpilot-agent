"""Build a constructed golden set for claim-level support evaluation.

The seed is built from the built-in demo corpus (real arXiv metadata/abstracts).
Each source sentence yields four probes:

* ``supported``    the sentence itself, quoted verbatim
* ``partial``      the sentence plus an extra clause the quote does not contain
* ``missing_number`` the sentence with one number changed (probe for numeric checks)
* ``unsupported``  a sentence from a *different* paper, quoted against this evidence

Every generated quote is asserted to be a literal substring of the source text,
so the set can never drift into fabricated evidence. Provenance is recorded as
``constructed-seed``: this measures the classifier, it is **not** a human
annotation set and is not reported as one.
"""
from __future__ import annotations

from _console import make_console_safe

make_console_safe()

import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("PAPERPILOT_DATA_DIR", tempfile.mkdtemp(prefix="pp-golden-"))

from app.services import search  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data" / "eval" / "golden" / "seed-constructed.json"
SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _source_text(paper: dict) -> str:
    parts = [str(paper.get("abstract") or ""), str(paper.get("full_text") or "")]
    return " ".join(part for part in parts if part)


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in SENTENCE_RE.split(text)]
    return [part for part in parts if len(part) >= 60 and NUMBER_RE.search(part)]


def _bump_number(sentence: str) -> tuple[str, bool]:
    match = NUMBER_RE.search(sentence)
    if not match:
        return sentence, False
    value = match.group(0)
    try:
        bumped = f"{float(value) * 3 + 1:.2f}".rstrip("0").rstrip(".")
    except ValueError:
        return sentence, False
    return sentence[:match.start()] + bumped + sentence[match.end():], True


def build() -> dict:
    papers = [p for p in search.all_papers() if _source_text(p)]
    papers.sort(key=lambda p: str(p.get("id")))
    if len(papers) < 3:
        raise SystemExit("演示语料不足，无法构造金标集。")

    claims: list[dict] = []
    pairs: list[dict] = []
    counter = 0
    for index, paper in enumerate(papers[:5]):
        text = _source_text(paper)
        sentences = _sentences(text)[:2]
        if not sentences:
            continue
        other = papers[(index + 1) % len(papers)]
        other_sentences = _sentences(_source_text(other))[:1]
        for sentence in sentences:
            counter += 1
            claims.append({
                "id": f"g-{counter:03d}", "paper_id": paper.get("id"),
                "claim": sentence, "evidence_quote": sentence,
                "locator": "Abstract", "label": "supported",
                "annotator": "constructed-seed", "note": "verbatim sentence",
            })
            counter += 1
            claims.append({
                "id": f"g-{counter:03d}", "paper_id": paper.get("id"),
                "claim": sentence + " 该方法还适用于其他任务与硬件平台，并已在工业部署中验证。",
                "evidence_quote": sentence, "locator": "Abstract", "label": "partial",
                "annotator": "constructed-seed", "note": "claim adds a clause absent from the quote",
            })
            bumped, changed = _bump_number(sentence)
            if changed:
                counter += 1
                claims.append({
                    "id": f"g-{counter:03d}", "paper_id": paper.get("id"),
                    "claim": bumped, "evidence_quote": sentence,
                    "locator": "Abstract", "label": "partial", "probe": "missing_number",
                    "annotator": "constructed-seed", "note": "one number changed",
                })
        if other_sentences:
            counter += 1
            claims.append({
                "id": f"g-{counter:03d}", "paper_id": paper.get("id"),
                "claim": other_sentences[0], "evidence_quote": sentences[0],
                "locator": "Abstract", "label": "unsupported",
                "annotator": "constructed-seed", "note": "claim quoted from a different paper",
            })

    # Contradiction probes: same metric name, explicit opposite / same directions.
    def paper(pid: str, title: str, energy: str) -> dict:
        return {"id": pid, "title": title, "year": "2026", "venue": "arXiv preprint",
                "metrics": {"ADC 能耗": energy}, "results": []}

    pairs = [
        {"id": "cp-001", "expected": True,
         "paper_a": paper("X1", "Paper X1", "降低 27.1%"),
         "paper_b": paper("X2", "Paper X2", "提升 12.5%")},
        {"id": "cp-002", "expected": False,
         "paper_a": paper("Y1", "Paper Y1", "降低 27.1%"),
         "paper_b": paper("Y2", "Paper Y2", "降低 91.7%")},
        {"id": "cp-003", "expected": False,
         "paper_a": paper("Z1", "Paper Z1", "降低 27.1%"),
         "paper_b": {"id": "Z2", "title": "Paper Z2", "metrics": {"吞吐": "提升 2.0×"}, "results": []}},
    ]

    # Refuse to ship a quote the corpus cannot verify.
    texts = {str(p.get("id")): _source_text(p) for p in papers}
    invalid = [c["id"] for c in claims if c["evidence_quote"] not in texts.get(str(c["paper_id"]), "")]
    if invalid:
        raise SystemExit(f"引句无法在原文中校验：{invalid[:5]}")

    return {
        "version": 1,
        "topic": "constructed-seed",
        "provenance": "constructed-seed",
        "generated_from": "demo corpus (real arXiv metadata/abstracts)",
        "papers": [{"id": p.get("id"), "title": p.get("title")} for p in papers[:5]],
        "claims": claims,
        "contradiction_pairs": pairs,
        "disagreements": [],
    }


def main() -> None:
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)
    print("claims:", len(data["claims"]), "| pairs:", len(data["contradiction_pairs"]))


if __name__ == "__main__":
    main()
