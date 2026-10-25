"""Build the same-domain cross-paper hard-negative golden set.

The v5 seed quoted one paper's sentence against *another* paper's sentence, but
the two papers happened to be lexically far apart. Real failures are subtler:
two papers in the same subfield share enough vocabulary (0.20–0.45 token
overlap) that a claim about paper B used to be judged ``partial`` against paper
A's abstract even though none of B's measurements appear in A.

This script turns every such pair in the demo corpus into an ``unsupported``
probe, plus one verbatim ``supported`` anchor per paper so precision has a
positive class. Inclusion is structural (different paper, in-band overlap,
claim contains numbers); it does not consult the classifier's verdict.

The quotes are asserted to be literal substrings of the source abstract.
Provenance is ``constructed-seed`` — this is a regression set for the rule, not
a human annotation set.
"""
from __future__ import annotations

from _console import make_console_safe

make_console_safe()

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PAPERPILOT_DATA_DIR", tempfile.mkdtemp(prefix="pp-hard-"))

from app.services import grounding, project_state, search  # noqa: E402

OUT = ROOT / "data" / "eval" / "golden" / "seed-adversarial.json"
BAND_LOW, BAND_HIGH = 0.20, 0.45


def _abstract(paper: dict) -> str:
    return " ".join(str(paper.get("abstract") or "").split())


def build() -> dict:
    papers = [paper for paper in search.all_papers() if _abstract(paper)]
    papers.sort(key=lambda paper: str(paper.get("id")))
    if len(papers) < 2:
        raise SystemExit("演示语料不足，无法构造同领域硬负例。")

    anchors: list[dict] = []
    probes: list[dict] = []
    seen: set[tuple[str, str]] = set()
    counter = 0
    for left in papers:
        quote = _abstract(left)
        sentences = [item for item in grounding.sentences(quote) if grounding.numbers(item)]
        if sentences:
            counter += 1
            anchors.append({
                "id": f"h-{counter:03d}", "paper_id": left.get("id"),
                "evidence_paper_id": left.get("id"),
                "claim": sentences[0], "evidence_quote": sentences[0],
                "locator": "Abstract", "label": "supported",
                "annotator": "constructed-seed", "note": "verbatim anchor",
            })
        for right in papers:
            if right.get("id") == left.get("id"):
                continue
            for sentence in grounding.sentences(_abstract(right)):
                if not grounding.numbers(sentence):
                    continue
                shared = grounding.overlap(sentence, quote)
                if not BAND_LOW <= shared < BAND_HIGH:
                    continue
                key = (str(left.get("id")), sentence)
                if key in seen:
                    continue
                seen.add(key)
                counter += 1
                probes.append({
                    "id": f"h-{counter:03d}", "paper_id": right.get("id"),
                    "evidence_paper_id": left.get("id"),
                    "claim": sentence, "evidence_quote": quote,
                    "locator": "Abstract", "label": "unsupported",
                    "annotator": "constructed-seed",
                    "note": "same-domain sentence from another paper",
                    "signals": {"overlap": round(shared, 4)},
                })

    claims = anchors + probes
    for item in claims:
        evidence_paper = item.get("evidence_paper_id") or item["paper_id"]
        source = next((paper for paper in papers
                       if paper.get("id") == evidence_paper), None)
        if source is None or item["evidence_quote"] not in _abstract(source):
            raise SystemExit(f"引句不是原文子串：{item['id']}")
    return {
        "version": 1,
        "topic": "adversarial-same-domain",
        "provenance": "constructed-seed",
        "status": "labeled",
        "created_at": project_state._now(),
        "caveat": "Structural hard negatives; measures the rule, not human judgment.",
        "counts": {"supported": len(anchors), "unsupported": len(probes)},
        "claims": claims,
    }


def main() -> int:
    payload = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} claims={len(payload['claims'])} "
          f"supported={payload['counts']['supported']} "
          f"unsupported={payload['counts']['unsupported']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
