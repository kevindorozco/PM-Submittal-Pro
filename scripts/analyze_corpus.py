#!/usr/bin/env python3
"""Phase 0 corpus analysis: read the extraction JSONL, report the numbers that
decide the architecture.

Reports:
  - corpus overview (packages, pages, docs, text coverage / OCR debt)
  - unique document count after dedupe (exact text-hash, then page-hash
    Jaccard clustering to absorb boundary-detection noise)
  - reuse rate, several definitions, all printed with their formula
  - doc_type distribution (instances and unique docs)
  - manufacturer distribution
  - CSI section clustering
  - typical package structure/order
  - boundary-detection health metrics (to judge how much to trust the above)

Usage:
    python3 scripts/analyze_corpus.py corpus_extract.jsonl
    python3 scripts/analyze_corpus.py corpus_extract.jsonl --json corpus_report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from statistics import mean, median

NEAR_DUP_JACCARD = 0.6   # page-hash set similarity to call two docs the same
HUB_HASH_MAX_DOCS = 75   # page hashes in more docs than this are too generic to join on


class UnionFind:
    def __init__(self):
        self.parent: dict = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def load(path: str):
    packages, docs = [], []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            (packages if rec["record_type"] == "package" else docs).append(rec)
    return packages, docs


def cluster_documents(docs: list[dict]) -> dict[int, int]:
    """Assign every doc (by list index) a cluster id. Exact text-hash matches
    merge first; page-hash Jaccard then absorbs near-duplicates (same source
    doc with a boundary off by a page, or a stamp the normalizer missed)."""
    uf = UnionFind()
    for i in range(len(docs)):
        uf.find(i)

    by_exact: dict[str, list[int]] = defaultdict(list)
    for i, d in enumerate(docs):
        if d.get("text_sha256"):
            by_exact[d["text_sha256"]].append(i)
    for idxs in by_exact.values():
        for j in idxs[1:]:
            uf.union(idxs[0], j)

    hash_to_docs: dict[str, list[int]] = defaultdict(list)
    page_sets: dict[int, frozenset] = {}
    for i, d in enumerate(docs):
        hs = frozenset(h for h in (d.get("page_hashes") or []) if h)
        if hs:
            page_sets[i] = hs
            for h in hs:
                hash_to_docs[h].append(i)

    seen_pairs: set[tuple[int, int]] = set()
    for h, idxs in hash_to_docs.items():
        if len(idxs) < 2 or len(idxs) > HUB_HASH_MAX_DOCS:
            continue
        for a_pos in range(len(idxs)):
            for b_pos in range(a_pos + 1, len(idxs)):
                a, b = idxs[a_pos], idxs[b_pos]
                if uf.find(a) == uf.find(b):
                    continue
                pair = (min(a, b), max(a, b))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                sa, sb = page_sets[a], page_sets[b]
                jac = len(sa & sb) / len(sa | sb)
                if jac >= NEAR_DUP_JACCARD:
                    uf.union(a, b)

    return {i: uf.find(i) for i in range(len(docs))}


def _cluster_label(docs: list[dict], members: list[int]) -> str:
    manus = Counter(docs[i]["manufacturer"] for i in members if docs[i].get("manufacturer"))
    titles = Counter(docs[i]["title_guess"] for i in members if docs[i].get("title_guess"))
    manu = manus.most_common(1)[0][0] if manus else "?"
    title = titles.most_common(1)[0][0] if titles else "(no title)"
    return f"{manu} — {title[:60]}"


def pct(a: float, b: float) -> str:
    return f"{100.0 * a / b:.1f}%" if b else "n/a"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jsonl", help="Output of extract_corpus.py")
    ap.add_argument("--json", dest="json_out", default=None, help="Also write raw numbers to this JSON file")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    packages, docs = load(args.jsonl)
    ok_packages = [p for p in packages if not p.get("error")]
    failed = [p for p in packages if p.get("error")]
    report: dict = {}

    print("=" * 72)
    print("PHASE 0 — CORPUS ANALYSIS")
    print("=" * 72)

    # ------------------------------------------------------------------ overview
    total_pages = sum(p.get("page_count") or 0 for p in ok_packages)
    pages_with_text = sum(p.get("pages_with_text") or 0 for p in ok_packages)
    pkg_pages = [p["page_count"] for p in ok_packages if p.get("page_count")]
    docs_per_pkg = Counter(d["package_id"] for d in docs)
    dpp = list(docs_per_pkg.values())
    doc_pages = [d["page_count"] for d in docs]

    # Identical package files (same PDF uploaded twice) — flag, they inflate reuse.
    file_hashes = Counter(p["file_sha256"] for p in ok_packages if p.get("file_sha256"))
    dup_files = {h: c for h, c in file_hashes.items() if c > 1}

    print("\n## Corpus overview")
    print(f"  packages processed        : {len(ok_packages)}  ({len(failed)} failed to parse)")
    if dup_files:
        print(f"  ! identical package files : {sum(dup_files.values())} files are byte-identical duplicates "
              f"({len(dup_files)} distinct) — reuse numbers below include them")
    print(f"  total pages               : {total_pages}")
    print(f"  pages with embedded text  : {pages_with_text}  ({pct(pages_with_text, total_pages)})")
    print(f"  total documents detected  : {len(docs)}")
    if dpp:
        print(f"  docs per package          : min {min(dpp)} / median {median(dpp):.0f} / mean {mean(dpp):.1f} / max {max(dpp)}")
    if pkg_pages:
        print(f"  pages per package         : min {min(pkg_pages)} / median {median(pkg_pages):.0f} / mean {mean(pkg_pages):.1f} / max {max(pkg_pages)}")
    if doc_pages:
        print(f"  pages per document        : min {min(doc_pages)} / median {median(doc_pages):.0f} / mean {mean(doc_pages):.1f} / max {max(doc_pages)}")
    for p in failed[:10]:
        print(f"    failed: {p['file']}: {p['error']}")

    no_text_docs = [d for d in docs if not d.get("has_text")]
    text_docs = [d for d in docs if d.get("has_text")]
    print(f"  docs with no text layer   : {len(no_text_docs)}  ({pct(len(no_text_docs), len(docs))}) — "
          f"need OCR before they can be deduped or classified")

    report["overview"] = {
        "packages_ok": len(ok_packages), "packages_failed": len(failed),
        "duplicate_package_files": sum(dup_files.values()) if dup_files else 0,
        "total_pages": total_pages, "pages_with_text": pages_with_text,
        "total_documents": len(docs), "documents_no_text": len(no_text_docs),
    }

    # ------------------------------------------------------------------ dedupe
    clusters_map = cluster_documents(text_docs)
    clusters: dict[int, list[int]] = defaultdict(list)
    for i, root in clusters_map.items():
        clusters[root].append(i)

    n_unique = len(clusters)
    exact_unique = len({d["text_sha256"] for d in text_docs})
    multi_pkg_clusters = 0
    repeat_instances = 0
    cluster_pkg_counts: list[tuple[int, int, int]] = []  # (root, n_instances, n_packages)
    for root, members in clusters.items():
        pkgs = {text_docs[i]["package_id"] for i in members}
        cluster_pkg_counts.append((root, len(members), len(pkgs)))
        if len(pkgs) > 1:
            multi_pkg_clusters += 1
            repeat_instances += len(members)

    page_hash_pkgs: dict[str, set] = defaultdict(set)
    for d in text_docs:
        for h in d.get("page_hashes") or []:
            if h:
                page_hash_pkgs[h].add(d["package_id"])
    total_page_instances = sum(1 for d in text_docs for h in (d.get("page_hashes") or []) if h)
    reused_page_instances = sum(
        1 for d in text_docs for h in (d.get("page_hashes") or [])
        if h and len(page_hash_pkgs[h]) > 1
    )

    print("\n## Dedupe / reuse  (text-bearing documents only)")
    print(f"  document instances        : {len(text_docs)}")
    print(f"  unique by exact text hash : {exact_unique}")
    print(f"  unique after fuzzy cluster: {n_unique}   (page-hash Jaccard >= {NEAR_DUP_JACCARD})")
    print(f"  reuse rate                : {pct(len(text_docs) - n_unique, len(text_docs))}   "
          f"(1 - unique/instances: each library doc saves this many re-fetches)")
    print(f"  avg uses per unique doc   : {len(text_docs) / n_unique:.1f}" if n_unique else "")
    print(f"  docs seen in >1 package   : {multi_pkg_clusters} unique docs account for "
          f"{repeat_instances} instances ({pct(repeat_instances, len(text_docs))} of all instances)")
    print(f"  page-level reuse          : {pct(reused_page_instances, total_page_instances)} of text pages "
          f"appear in more than one package (boundary-error-proof measure)")

    print(f"\n  Top {args.top} most-reused documents:")
    cluster_pkg_counts.sort(key=lambda t: (-t[2], -t[1]))
    for root, n_inst, n_pkgs in cluster_pkg_counts[: args.top]:
        d0 = text_docs[clusters[root][0]]
        print(f"    {n_pkgs:3d} pkgs / {n_inst:3d} uses  [{d0['doc_type']:<14}] {_cluster_label(text_docs, clusters[root])}")

    report["dedupe"] = {
        "doc_instances_with_text": len(text_docs),
        "unique_exact": exact_unique,
        "unique_clusters": n_unique,
        "reuse_rate": round(1 - n_unique / len(text_docs), 4) if text_docs else None,
        "clusters_in_multiple_packages": multi_pkg_clusters,
        "repeat_instance_share": round(repeat_instances / len(text_docs), 4) if text_docs else None,
        "page_level_reuse": round(reused_page_instances / total_page_instances, 4) if total_page_instances else None,
    }

    # ------------------------------------------------------------------ doc types
    print("\n## doc_type distribution")
    inst_types = Counter(d["doc_type"] for d in docs)
    uniq_types = Counter(text_docs[clusters[root][0]]["doc_type"] for root in clusters)
    print(f"  {'type':<20}{'instances':>10}{'share':>8}{'unique':>8}")
    for t, c in inst_types.most_common():
        print(f"  {t:<20}{c:>10}{pct(c, len(docs)):>8}{uniq_types.get(t, 0):>8}")
    report["doc_types"] = {"instances": dict(inst_types), "unique": dict(uniq_types)}

    # ------------------------------------------------------------------ manufacturers
    print("\n## Manufacturer distribution (top {})".format(args.top))
    manus = Counter(d["manufacturer"] for d in docs if d.get("manufacturer"))
    undetected = sum(1 for d in docs if not d.get("manufacturer"))
    for m, c in manus.most_common(args.top):
        print(f"  {m:<28}{c:>6}  {pct(c, len(docs))}")
    print(f"  {'(undetected)':<28}{undetected:>6}  {pct(undetected, len(docs))}")
    report["manufacturers"] = {"counts": dict(manus.most_common(50)), "undetected": undetected}

    # ------------------------------------------------------------------ CSI clustering
    print("\n## CSI section clustering")
    sec_doc_counts = Counter()
    sec_pkg: dict[str, set] = defaultdict(set)
    for d in docs:
        for s in d.get("csi_sections") or []:
            sec_doc_counts[s] += 1
            sec_pkg[d["package_id"]].add(s)
    pkg_sec_counts = Counter()
    for secs in sec_pkg.values():
        for s in secs:
            pkg_sec_counts[s] += 1
    print(f"  {'section':<12}{'in #pkgs':>9}{'doc hits':>10}")
    for s, c in pkg_sec_counts.most_common(args.top):
        print(f"  {s:<12}{c:>9}{sec_doc_counts[s]:>10}")
    no_sec_pkgs = len(ok_packages) - len(sec_pkg)
    print(f"  packages with no CSI section detected: {no_sec_pkgs}")
    report["csi"] = {"by_package": dict(pkg_sec_counts.most_common(50)), "packages_without": no_sec_pkgs}

    # ------------------------------------------------------------------ structure
    print("\n## Typical package structure")
    seqs: dict[str, list[str]] = defaultdict(list)
    for d in sorted(docs, key=lambda d: (d["package_id"], d["doc_index"])):
        seqs[d["package_id"]].append(d["doc_type"])
    openers = Counter(s[0] for s in seqs.values() if s)
    shapes = Counter(" > ".join(s[:4]) for s in seqs.values() if s)
    print("  first document type       : " + ", ".join(f"{t} ({pct(c, len(seqs))})" for t, c in openers.most_common(4)))
    print("  most common opening shapes:")
    for shape, c in shapes.most_common(5):
        print(f"    {c:3d}x  {shape}")
    # average normalized position of each doc_type within its package
    pos_acc: dict[str, list[float]] = defaultdict(list)
    for s in seqs.values():
        if len(s) > 1:
            for i, t in enumerate(s):
                pos_acc[t].append(i / (len(s) - 1))
    print("  average position in package (0=first, 1=last):")
    for t, ps in sorted(pos_acc.items(), key=lambda kv: mean(kv[1])):
        print(f"    {t:<20}{mean(ps):.2f}   (n={len(ps)})")
    report["structure"] = {
        "openers": dict(openers),
        "avg_position": {t: round(mean(ps), 3) for t, ps in pos_acc.items()},
    }

    # ------------------------------------------------------------------ boundary health
    print("\n## Boundary-detection health (how much to trust the doc counts)")
    single = sum(1 for d in docs if d["page_count"] == 1)
    huge = [d for d in docs if d["page_count"] > 25]
    strong = sum(1 for d in docs if d["boundary_score"] >= 90 or "package_start" in d["boundary_signals"])
    print(f"  boundaries from bookmarks/strong signals: {pct(strong, len(docs))}")
    print(f"  single-page documents     : {single}  ({pct(single, len(docs))})")
    print(f"  documents > 25 pages      : {len(huge)}  (possible under-segmentation; inspect these)")
    for d in sorted(huge, key=lambda d: -d["page_count"])[:8]:
        print(f"    {d['package_id']}: pages {d['page_start']}-{d['page_end']} "
              f"[{d['doc_type']}] {d.get('manufacturer') or '?'}")
    report["boundary_health"] = {"single_page_docs": single, "over_25_pages": len(huge),
                                 "strong_boundary_share": round(strong / len(docs), 4) if docs else None}

    # ------------------------------------------------------------------ verdict
    print("\n" + "=" * 72)
    rr = report["dedupe"]["reuse_rate"]
    if rr is not None:
        if rr >= 0.5:
            print(f"VERDICT: reuse rate {rr:.0%} — library hypothesis SUPPORTED. "
                  f"~{n_unique} unique docs serve {len(text_docs)} placements.")
        elif rr >= 0.25:
            print(f"VERDICT: reuse rate {rr:.0%} — moderate. Library still likely worth it; "
                  f"check the top-reused list covers your bread-and-butter sections.")
        else:
            print(f"VERDICT: reuse rate {rr:.0%} — LOW. Re-read CLAUDE.md Phase 0: "
                  f"stop and rethink before building the library.")
    print("=" * 72)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nRaw numbers written to {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
