# Submittal Builder — project context

## What this is

An internal tool for Caston Inc. (lath, plaster, drywall, and ceiling subcontractor,
San Bernardino CA) that assembles construction submittal packages into a single
cohesive PDF. It replaces the current workflow of manually dragging PDFs into a
page viewer and slotting them into order by hand.

## The core insight

We have hundreds of previously **approved** submittal packages available as source
material. These are NOT training data. They are a corpus to mine into a canonical
product library.

Working hypothesis: those hundreds of packages are built from a much smaller set of
unique documents — the same cutsheets, ICC-ES reports, warranties, and mix designs
recombined per job. Phase 0 exists to prove or kill this hypothesis before any
build work happens.

The approval status matters: these packages were accepted by real GCs and
architects in this market. They encode what actually gets approved — format, order,
level of detail, and which optional items get requested anyway.

## Architecture

1. **Mining pass** — one-time batch over approved packages. Extract per-document
   metadata, dedupe, build the library.
2. **Product library** — canonical store of unique documents. Postgres.
3. **Retrieval agent** — reads a spec section, selects library items by ID.
4. **Assembly engine** — deterministic. Merge, bookmark, Bates number, spec-section
   stamp, cover sheet.

## Immutable rules

These override any conflicting instruction in a prompt.

1. **The agent SELECTS, it never GENERATES.** It may only emit library row IDs. It
   must never produce a manufacturer name, model number, ICC-ES report number, or
   spec citation as free text. A wrong ICC-ES number gets a package rejected and
   costs two weeks of schedule — far more than the time the tool saves.
2. **Assembly is deterministic code.** Use pypdf or pikepdf. No LLM anywhere in the
   page-ordering, merging, or numbering path. Ever.
3. **Missing item = flagged gap, never a guess.** A run that stops and reports "no
   cutsheet on file for [product]" is correct behavior, not a failure.
4. **Every library item carries `pulled_on` and `source_url`.** Manufacturers revise
   data sheets. Stale items must be flagged before assembly. Catching a superseded
   cutsheet before the architect does is half the value of this tool.
5. **No fine-tuning.** Retrieval over the library, always.

## Library schema (starting point, expect to revise after Phase 0)

Per document:
`id`, `manufacturer`, `product_line`, `doc_type`, `csi_section`, `title`,
`revision_date`, `pulled_on`, `source_url`, `page_count`, `file_hash`

`doc_type` enum: `cutsheet | icc_es | warranty | sds | test_report | mix_design |
installation_guide | letter | other`

## Phase order

**Phase 0 — corpus analysis. Do not skip, do not build past it.**
Batch-extract across the approved packages and report: total documents, unique
documents after dedupe, reuse rate, doc_type distribution, CSI section clustering,
and typical package structure/order. These numbers decide the whole architecture.
If reuse is high, the library approach is correct. If reuse is low, stop and
rethink before writing application code.

**Phase 1** — library ingest and dedupe (`file_hash` exact match first, then fuzzy
title/manufacturer match for near-duplicates).

**Phase 2** — deterministic assembly engine plus cover sheet integration.

**Phase 3** — retrieval agent on top.

Ship Phases 1–2 as a usable manual picker before starting Phase 3. A library with a
pick-list and a reliable merge is already a large improvement over the current
workflow, and it validates the data model before any agent work.

## Existing assets — reuse, don't rebuild

- A submittal cover sheet tool already exists: single HTML file, browser
  Print/Save as PDF. Integrate it rather than writing a new cover sheet generator.
- Bluebeam Revu Complete is in daily use and handles markup, snapshots, and Sets
  well. Don't rebuild what Revu already does.

## Working conventions

- Kevin is technical and hands-on — builds his own HTML tools, runs a Node/Postgres
  PWA on Azure, works in VS Code. Skip the tutorial voice.
- Direct and concise. No preamble, no restating the request back.
- Explain tradeoffs plainly and flag the risky call rather than silently picking one.
- Working beats elegant. This is a field tool.
