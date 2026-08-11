#!/usr/bin/env python3
"""Score extract_corpus.py output against the fixture's ground truth.

Reports boundary precision/recall, dedupe cluster accuracy, and
manufacturer/doc_type detection accuracy.

Usage:
    python3 tests/validate_fixture.py fixture/ground_truth.json fixture_extract.jsonl
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from analyze_corpus import cluster_documents  # noqa: E402


def main() -> int:
    truth_path, jsonl_path = sys.argv[1], sys.argv[2]
    with open(truth_path) as fh:
        truth = json.load(fh)

    docs = []
    with open(jsonl_path) as fh:
        for line in fh:
            rec = json.loads(line)
            if rec["record_type"] == "document":
                docs.append(rec)

    # ---------------- boundary precision/recall (page 1 excluded: trivial) ----
    tp = fp = fn = 0
    per_pkg_miss = []
    for pkg_id, pkg in truth["packages"].items():
        true_starts = {d["page_start"] for d in pkg["docs"]} - {1}
        pred_starts = {d["page_start"] for d in docs if d["package_id"] == pkg_id} - {1}
        tp += len(true_starts & pred_starts)
        fp += len(pred_starts - true_starts)
        fn += len(true_starts - pred_starts)
        if true_starts - pred_starts or pred_starts - true_starts:
            per_pkg_miss.append((pkg_id, sorted(true_starts - pred_starts), sorted(pred_starts - true_starts)))

    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    print("## Boundary detection")
    print(f"  precision: {prec:.3f}   recall: {rec:.3f}   (tp={tp} fp={fp} fn={fn})")
    for pkg_id, missed, spurious in per_pkg_miss[:12]:
        print(f"    {pkg_id}: missed starts {missed}  spurious starts {spurious}")

    # ---------------- map extracted docs to ground-truth docs by page overlap --
    def gt_lookup(pkg_id):
        return truth["packages"][pkg_id]["docs"]

    ext_to_gt = {}
    for i, d in enumerate(docs):
        best, best_ov = None, 0
        for g in gt_lookup(d["package_id"]):
            ov = max(0, min(d["page_end"], g["page_end"]) - max(d["page_start"], g["page_start"]) + 1)
            if ov > best_ov:
                best, best_ov = g, ov
        ext_to_gt[i] = best["doc_id"] if best else None

    # ---------------- dedupe accuracy ----------------------------------------
    text_docs = [d for d in docs if d.get("has_text")]
    td_index = [i for i, d in enumerate(docs) if d.get("has_text")]
    cl = cluster_documents(text_docs)
    clusters = defaultdict(list)
    for local_i, root in cl.items():
        clusters[root].append(td_index[local_i])

    # ground truth identity: pool doc_id, or unique per (cover, package)
    def gt_key(i):
        g = ext_to_gt[i]
        return ("cover", docs[i]["package_id"]) if g == "cover" else ("pool", g)

    n_clusters = len(clusters)
    gt_unique = len({gt_key(i) for i in ext_to_gt if docs[i].get("has_text")})
    pure = sum(1 for members in clusters.values() if len({gt_key(i) for i in members}) == 1)

    same_gt_pairs = same_gt_same_cluster = 0
    by_gt = defaultdict(list)
    for i in td_index:
        by_gt[gt_key(i)].append(i)
    root_of = {td_index[li]: r for li, r in cl.items()}
    for members in by_gt.values():
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                same_gt_pairs += 1
                if root_of[members[a]] == root_of[members[b]]:
                    same_gt_same_cluster += 1

    print("\n## Dedupe (text docs only; the no-text scan doc is excluded by design)")
    print(f"  ground-truth unique docs : {gt_unique}  (pool docs used + 1 cover per distinct package)")
    print(f"  clusters found           : {n_clusters}")
    print(f"  cluster purity           : {pure}/{n_clusters} clusters contain exactly one true doc")
    print(f"  pair completeness        : {same_gt_same_cluster}/{same_gt_pairs} same-doc pairs landed in one cluster"
          + (f" ({same_gt_same_cluster / same_gt_pairs:.3f})" if same_gt_pairs else ""))

    # ---------------- attribute detection ------------------------------------
    pool = {p["doc_id"]: p for p in truth["pool"]}
    manu_ok = manu_tot = type_ok = type_tot = 0
    type_conf = Counter()
    for i, d in enumerate(docs):
        g = ext_to_gt[i]
        if g == "cover" or g is None:
            continue
        p = pool[g]
        if p["has_text"]:
            manu_tot += 1
            if d.get("manufacturer") == p["manufacturer"]:
                manu_ok += 1
            type_tot += 1
            if d.get("doc_type") == p["doc_type"]:
                type_ok += 1
            else:
                type_conf[(p["doc_type"], d.get("doc_type"))] += 1

    print("\n## Attribute detection (docs mapped to a pool doc)")
    print(f"  manufacturer accuracy    : {manu_ok}/{manu_tot} ({manu_ok / manu_tot:.3f})" if manu_tot else "  n/a")
    print(f"  doc_type accuracy        : {type_ok}/{type_tot} ({type_ok / type_tot:.3f})" if type_tot else "  n/a")
    for (want, got), c in type_conf.most_common(8):
        print(f"    confused {want} -> {got}: {c}x")

    scan_docs = [d for d in docs if not d.get("has_text")]
    print(f"\n## No-text handling: {len(scan_docs)} extracted docs flagged has_text=false "
          f"(scan doc instances in truth: {sum(1 for pkg in truth['packages'].values() for g in pkg['docs'] if g['doc_id'] == len(pool) - 1)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
