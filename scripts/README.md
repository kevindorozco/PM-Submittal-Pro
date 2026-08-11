# Phase 0 — corpus analysis

The ~300 approved packages are not in this repo, so the mining pass runs wherever
they live. Two commands:

```bash
pip install pypdf                     # only dependency for the real run

# 1. extract (multiprocess; ~300 packages of mixed size runs in minutes)
python3 scripts/extract_corpus.py ./corpus -o corpus_extract.jsonl

# 2. analyze
python3 scripts/analyze_corpus.py corpus_extract.jsonl --json corpus_report.json
```

`extract_corpus.py` walks `./corpus` recursively, so job subfolders are fine.
Smoke-test first if you want: `--limit 5 --workers 1`.

The analyzer prints the Phase 0 report (dedupe, reuse rate, doc_type
distribution, CSI clustering, package structure, boundary health) and ends with
a verdict line keyed to the reuse-rate thresholds in CLAUDE.md. `corpus_report.json`
has the raw numbers. The JSONL carries full per-document extracted text, so
expect tens of MB; both output files are gitignored — commit `corpus_report.json`
only if you want the numbers in-repo (it contains no document text).

## What to look at first in the output

- **`pages with embedded text`** — anything much below ~90% means a chunk of the
  corpus is scans and Phase 1 needs an OCR step before those docs can join the
  library.
- **`unique after fuzzy cluster` + `reuse rate`** — the go/no-go numbers.
- **`documents > 25 pages`** — the boundary detector's likely failures; spot-check
  these page ranges in Bluebeam before trusting the totals.
- **`! identical package files`** — byte-duplicate PDFs inflate reuse; rerun after
  removing them if any show up.

## Validation harness

Boundary detection and dedupe are heuristic, so their accuracy is measured, not
assumed. `tests/make_fixture_corpus.py` builds a 31-package synthetic corpus
(47 unique docs + unique covers, known boundaries, themed CSI sections, a
simulated scan, a byte-identical duplicate package) and
`tests/validate_fixture.py` scores extraction against the ground truth:

```bash
pip install pypdf reportlab           # fixture generation needs reportlab
python3 tests/make_fixture_corpus.py /tmp/fixture
python3 scripts/extract_corpus.py /tmp/fixture/corpus -o /tmp/fixture_extract.jsonl
python3 tests/validate_fixture.py /tmp/fixture/ground_truth.json /tmp/fixture_extract.jsonl
```

Current scores: boundary precision 1.000 / recall 0.986, dedupe pair
completeness 1.000 (76 clusters found vs 77 true), manufacturer and doc_type
accuracy 1.000. Known misses: adjacent unpaginated docs with near-identical
layouts, and a no-text scan merging into its neighbor — both self-heal in
dedupe via page-hash Jaccard clustering, costing at most ~1 unique-doc count.

The fixture is born-digital with clean text layers; real-corpus results will be
noisier in exactly one dimension — scanned pages — which the report quantifies
separately rather than hiding.
