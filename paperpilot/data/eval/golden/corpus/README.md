# Golden-set corpus

24 real paper abstracts (4 topics × 6 papers), fetched on 2026-10-22 from:

- **Crossref** `https://api.crossref.org/works` with `filter=has-abstract:true`
- **Europe PMC** `https://www.ebi.ac.uk/europepmc/webservices/rest/search` with `resultType=core`

Each `<topic>.json` records the query, fetch time, sources and papers
(`id / title / abstract / year / venue / authors / source`). `all.json` is the
flattened list.

These abstracts are used only to build candidate claim/quote pairs and
constructed evaluation probes. They are not redistributed as full text; check
the publisher's terms before using an abstract outside this evaluation context.
Crossref metadata is provided under its public API terms; Europe PMC content
retains the original publisher's rights.

Regenerate (needs network; results may change):

```bash
python scripts/fetch_golden_corpus.py
```

Build the candidate pool from this corpus:

```bash
python scripts/golden_workbench.py build --corpus-dir data/eval/golden/corpus
python scripts/build_golden_multitopic.py
```
