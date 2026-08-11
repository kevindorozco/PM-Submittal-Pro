#!/usr/bin/env python3
"""Generate a synthetic submittal-package corpus with known ground truth.

Builds a pool of unique "source documents" (cutsheets, ICC-ES reports, SDS,
warranties, mix designs, installation guides, test reports) rendered with
reportlab, then assembles packages the way real submittals are put together:
unique cover letter + a themed selection of pool docs, with core documents
(WRB, fasteners, common cutsheets) reused across most packages and a long tail
used once or twice.

Deliberately includes the hard cases: single-page docs, adjacent docs from the
same manufacturer, docs with no "Page N of M" footers, a doc with no text
layer (simulated scan), and one byte-identical duplicate package.

Writes:
  <out>/corpus/PKG-*.pdf          the packages (input for extract_corpus.py)
  <out>/ground_truth.json         per-package true boundaries + pool metadata

Usage:
    python3 tests/make_fixture_corpus.py /path/to/fixture_dir
"""

from __future__ import annotations

import json
import os
import random
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from extract_corpus import MANUFACTURERS  # noqa: E402

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = letter

FILLER = [
    "Apply in accordance with the manufacturer's published instructions and the project specifications.",
    "Store materials off the ground in a dry, covered location until time of installation.",
    "Consult the local building official for requirements specific to the jurisdiction.",
    "Field conditions shall be verified by the installing contractor prior to application.",
    "Do not apply when ambient temperature is below 40 F or when rain is expected within 24 hours.",
    "All framing members shall be free of oil, dirt, and loose mill scale at time of installation.",
    "Fastener spacing shall not exceed the maximum spacing shown in the applicable table.",
    "Control joints shall be installed per ASTM C1063 and the architectural drawings.",
    "Refer to the color chart for available standard and custom colors.",
    "Mixing water shall be clean and free of deleterious amounts of acid, alkali, and organic material.",
    "Protect adjacent surfaces from overspray and droppage during application.",
    "The product complies with the applicable requirements of the California Building Code.",
    "Job site mock-ups are recommended to establish acceptable workmanship and finish.",
    "Curing shall follow the moist curing provisions of the referenced standard.",
    "This product is intended for use as a component of a code-complying assembly.",
    "Coverage rates vary with substrate porosity, texture, and application technique.",
    "Dispose of container and unused contents in accordance with local regulations.",
    "Substrate shall be structurally sound, clean, and free of bond-inhibiting materials.",
    "Use appropriate personal protective equipment during handling and application.",
    "Periodic inspection during installation is recommended for quality assurance.",
]

# doc pool spec: (manufacturer, product_line, doc_type, n_pages, csi, paginated)
# weight tiers are assigned below: first CORE_N docs are near-universal.
POOL_SPEC = [
    # --- core docs, reused in most packages ---
    ("Fortifiber",        "Super Jumbo Tex",  "cutsheet",  2, "09 22 00", True),
    ("ClarkDietrich",     "ProSTUD",          "cutsheet",  3, "09 22 16", True),
    ("AMICO",             None,               "cutsheet",  2, "09 22 36", True),
    ("Quikrete",          "Base Coat Stucco", "cutsheet",  2, "09 24 00", True),
    ("Hilti",             "Kwik Bolt",        "cutsheet",  3, "05 40 00", True),
    ("USG",               "Sheetrock",        "cutsheet",  2, "09 29 00", True),
    ("Tree Island Steel", "K-Lath",           "cutsheet",  2, "09 22 36", False),
    ("AMICO",             None,               "icc_es",    5, "09 22 36", True),
    ("ClarkDietrich",     "ProSTUD",          "icc_es",    6, "09 22 16", True),
    ("Quikrete",          "Base Coat Stucco", "sds",       4, "09 24 00", True),
    # --- mid tier ---
    ("CEMCO",             "ViperStud",        "cutsheet",  3, "09 22 16", True),
    ("National Gypsum",   "Gold Bond",        "cutsheet",  2, "09 29 00", True),
    ("Georgia-Pacific",   "DensGlass",        "cutsheet",  2, "09 29 00", True),
    ("Armstrong",         "Ultima",           "cutsheet",  2, "09 51 13", True),
    ("Armstrong",         "Prelude",          "cutsheet",  2, "09 51 13", False),
    ("Rockfon",           "Sonar",            "cutsheet",  2, "09 51 13", True),
    ("Owens Corning",     "Thermafiber",      "cutsheet",  2, "07 21 00", True),
    ("Tremco",            "Dymonic",          "cutsheet",  1, "07 92 00", True),
    ("Trim-Tex",          "Tear Away",        "cutsheet",  1, "09 29 00", False),
    ("Merlex",            "SuperBlend",       "cutsheet",  2, "09 24 00", True),
    ("Omega Products",    "ColorTek",         "cutsheet",  2, "09 24 00", True),
    ("SPEC MIX",          "Scratch and Brown","cutsheet",  2, "09 24 00", True),
    ("CEMCO",             "ViperStud",        "icc_es",    5, "09 22 16", True),
    ("Georgia-Pacific",   "DensGlass",        "icc_es",    6, "09 29 00", True),
    ("Simpson Strong-Tie","Titen",            "icc_es",    5, "05 40 00", True),
    ("USG",               "Sheetrock",        "sds",       3, "09 29 00", True),
    ("Merlex",            "SuperBlend",       "sds",       4, "09 24 00", True),
    ("Omega Products",    "ColorTek",         "warranty",  1, "09 24 00", False),
    ("Armstrong",         "Ultima",           "warranty",  2, "09 51 13", False),
    ("Quikrete",          "Base Coat Stucco", "mix_design",1, "09 24 00", False),
    ("SPEC MIX",          "Scratch and Brown","mix_design",2, "09 24 00", False),
    ("USG",               "Sheetrock",        "installation_guide", 3, "09 29 00", True),
    ("Fortifiber",        "Super Jumbo Tex",  "installation_guide", 2, "09 22 00", False),
    ("Armstrong",         "Prelude",          "installation_guide", 4, "09 51 13", True),
    # --- long tail ---
    ("Rockwool",          "Safe'n'Sound",     "cutsheet",  2, "09 81 16", True),
    ("Johns Manville",    "MinWool",          "cutsheet",  2, "07 21 00", True),
    ("Pecora",            "AC-20",            "cutsheet",  1, "07 92 00", True),
    ("Grabber",           None,               "cutsheet",  2, "09 22 16", False),
    ("Fry Reglet",        None,               "cutsheet",  2, "09 22 36", True),
    ("Stockton Products", None,               "cutsheet",  1, "09 22 36", False),
    ("PABCO Gypsum",      "QuietRock",        "cutsheet",  2, "09 29 00", True),
    ("PABCO Gypsum",      "QuietRock",        "test_report", 4, "09 29 00", True),
    ("National Gypsum",   "Gold Bond",        "test_report", 3, "09 29 00", True),
    ("LaHabra",           "Fastwall",         "cutsheet",  2, "09 24 00", True),
    ("LaHabra",           "Fastwall",         "sds",       3, "09 24 00", True),
    ("Hunter Douglas",    None,               "cutsheet",  3, "09 51 13", True),
    ("Keene Building Products", None,         "cutsheet",  2, "09 81 16", True),
    # simulated scan: no text layer at all
    ("Vinyl Corp",        None,               "cutsheet",  2, "09 22 36", False),
]
SCAN_DOC_INDEX = len(POOL_SPEC) - 1
CORE_N = 10

THEMES = {
    "plaster":  {"09 22 00", "09 22 36", "09 24 00", "07 92 00"},
    "drywall":  {"09 22 16", "09 29 00", "09 81 16", "05 40 00", "07 21 00"},
    "ceilings": {"09 51 13", "09 81 16", "07 21 00"},
}
THEME_SECTION_TITLE = {
    "plaster": ("09 24 00", "PORTLAND CEMENT PLASTERING"),
    "drywall": ("09 29 00", "GYPSUM BOARD"),
    "ceilings": ("09 51 13", "ACOUSTICAL PANEL CEILINGS"),
}
DOC_TYPE_ORDER = ["cutsheet", "icc_es", "test_report", "mix_design",
                  "installation_guide", "sds", "warranty"]

PROJECTS = [
    "Arrowhead Medical Plaza", "Redlands USD Building C", "Ontario Gateway Hotel",
    "Victorville Civic Annex", "Loma Linda Research Wing", "Fontana Logistics Office",
    "Rialto Community Center", "Chino Hills Library", "Barstow Transit Center",
    "Riverside Metro Tower", "Colton Health Pavilion", "Hesperia High Gym",
    "Palm Desert Resort Spa", "Temecula Valley Hospital", "Yucaipa Performing Arts",
    "San Bernardino Justice Center", "Upland Medical Offices", "Corona Crossings Retail",
    "Moreno Valley College Lab", "Menifee Town Hall", "Highland Fire Station 3",
    "Rancho Cucamonga Pavilion", "Adelanto Detention Expansion", "Big Bear Lodge",
    "Indio Convention Hall", "Cathedral City Plaza", "Beaumont K-8 Campus",
    "Eastvale Sports Complex", "Lake Elsinore Terminal", "Murrieta Innovation Hub",
]


def _writer_lines(c: canvas.Canvas, lines: list[str], start_y: float = None):
    y = start_y if start_y is not None else PAGE_H - 54
    for ln in lines:
        c.drawString(54, y, ln[:110])
        y -= 14
        if y < 60:
            break


def render_doc(spec_idx: int, spec: tuple, path: str, rng: random.Random):
    manufacturer, product_line, doc_type, n_pages, csi, paginated = spec
    c = canvas.Canvas(path, pagesize=letter)
    pname = product_line or f"Series {200 + spec_idx}"
    esr = f"ESR-{1500 + spec_idx * 7}"
    code = f"{manufacturer[:2].upper()}-{spec_idx:03d}"

    if spec_idx == SCAN_DOC_INDEX:
        # Simulated scanned pages: geometry only, no text layer.
        for _ in range(n_pages):
            for i in range(12):
                c.line(54, 700 - i * 40, 558, 700 - i * 40)
            c.rect(54, 80, 504, 660)
            c.showPage()
        c.save()
        return

    for pg in range(1, n_pages + 1):
        header = {
            "cutsheet": [f"{manufacturer}", f"{pname}  |  TECHNICAL DATA SHEET",
                         f"Product Code {code}   CSI Section {csi}"],
            "icc_es": ["ICC-ES Evaluation Report", f"{esr}",
                       f"Report Holder: {manufacturer}", f"{pname} — Section {csi}"],
            "sds": ["SAFETY DATA SHEET", f"{manufacturer} — {pname}",
                    "Conforms to OSHA HCS 2012 (29 CFR 1910.1200)"],
            "warranty": [f"{manufacturer}", "LIMITED WARRANTY", f"{pname}"],
            "mix_design": [f"{manufacturer}", f"PLASTER MIX DESIGN — {pname}",
                           f"ASTM C926 — Section {csi}"],
            "installation_guide": [f"{manufacturer}", f"{pname} INSTALLATION INSTRUCTIONS",
                                   f"Section {csi}"],
            "test_report": ["INTERTEK TEST REPORT", f"Report No. T{4000 + spec_idx}",
                            f"Client: {manufacturer} — {pname}"],
        }[doc_type]

        body = []
        if pg == 1:
            body.append({
                "cutsheet": f"{pname} is engineered for interior and exterior assemblies.",
                "icc_es": "1.0 EVALUATION SCOPE — Compliance with the 2022 California Building Code.",
                "sds": "SECTION 1: IDENTIFICATION — Product identifier and supplier details.",
                "warranty": f"{manufacturer} warrants this product for a period of ten years.",
                "mix_design": "Proportions per 94 lb sack of plastic cement, ASTM C1328.",
                "installation_guide": "Read all instructions before beginning installation.",
                "test_report": "Specimen tested in accordance with ASTM E119 and ASTM E90.",
            }[doc_type])
        else:
            body.append({
                "cutsheet": "PHYSICAL PROPERTIES AND SPECIFICATIONS (CONTINUED)",
                "icc_es": f"{3.0 + pg:.1f} CONDITIONS OF USE — continued.",
                "sds": f"SECTION {pg * 3}: ADDITIONAL HAZARD AND HANDLING INFORMATION",
                "warranty": "TERMS AND CONDITIONS (CONTINUED)",
                "mix_design": "FIELD TEST RESULTS (CONTINUED)",
                "installation_guide": f"STEP {pg * 4}: CONTINUED INSTALLATION SEQUENCE",
                "test_report": "TEST DATA (CONTINUED)",
            }[doc_type])
        picks = rng.sample(FILLER, k=6)
        for i, s in enumerate(picks):
            body.append(f"{s}")
            body.append(f"  Ref {code}.{pg}.{i}: value {rng.randint(100, 9999)} "
                        f"per ASTM C{rng.randint(100, 1499)}.")

        footer = []
        # real manufacturers print their real domain in the footer
        domains = MANUFACTURERS.get(manufacturer, {}).get("domains", [])
        domain = domains[0] if domains else manufacturer.lower().replace(" ", "") + ".example"
        footer.append(f"www.{domain}   (800) 555-{1000 + spec_idx:04d}")
        if paginated:
            footer.append(f"Page {pg} of {n_pages}")

        _writer_lines(c, header)
        _writer_lines(c, body, start_y=PAGE_H - 130)
        y = 72
        for ln in footer:
            c.drawString(54, y, ln)
            y -= 14
        c.showPage()
    c.save()


def render_cover(pkg_idx: int, project: str, theme: str, doc_titles: list[str], path: str):
    sec, sec_title = THEME_SECTION_TITLE[theme]
    c = canvas.Canvas(path, pagesize=letter)
    lines = [
        "CASTON INC.",
        "Lath | Plaster | Drywall | Ceilings",
        "1055 W Mill St, San Bernardino, CA 92410",
        "",
        "SUBMITTAL PACKAGE",
        f"SECTION {sec} — {sec_title}",
        "",
        f"Project: {project}",
        f"Submittal No: {sec.replace(' ', '')}-{pkg_idx + 1:03d}",
        "Date: July 2026",
        "",
        "RE: Product data for your review and approval.",
        "",
        "Dear Plan Reviewer,",
        "Enclosed please find product data for the referenced section.",
        "",
        "CONTENTS:",
    ] + [f"  {i + 1}. {t}" for i, t in enumerate(doc_titles)]
    _writer_lines(c, lines)
    c.showPage()
    c.save()


def stamp_package(path: str, pkg_idx: int, section: str):
    """Simulate assembly-time stamps: Bates number bottom-right, spec-section
    page stamp bottom-left, on every page. These differ per package, so dedupe
    must strip them or the same source doc hashes differently everywhere."""
    reader = PdfReader(path)
    overlay_path = path + ".stamp.tmp"
    c = canvas.Canvas(overlay_path, pagesize=letter)
    for pg in range(1, len(reader.pages) + 1):
        c.setFont("Helvetica", 8)
        c.drawString(470, 30, f"CASTON-{pkg_idx:03d}{pg:04d}")
        c.drawString(54, 30, f"{section} - {pg}")
        c.showPage()
    c.save()
    stamps = PdfReader(overlay_path)
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        page.merge_page(stamps.pages[i])
        writer.add_page(page)
    with open(path, "wb") as fh:
        writer.write(fh)
    os.remove(overlay_path)


def main() -> int:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "fixture"
    corpus_dir = os.path.join(out_dir, "corpus")
    pool_dir = os.path.join(out_dir, "pool")
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(corpus_dir)
    os.makedirs(pool_dir)
    rng = random.Random(20260810)

    pool_paths = []
    for i, spec in enumerate(POOL_SPEC):
        path = os.path.join(pool_dir, f"doc_{i:03d}.pdf")
        render_doc(i, spec, path, rng)
        pool_paths.append(path)

    weights = [10.0] * CORE_N + [3.0] * (len(POOL_SPEC) - CORE_N - 14) + [1.0] * 14

    truth: dict = {"pool": [], "packages": {}}
    for i, (manu, pl, dt, np_, csi, pag) in enumerate(POOL_SPEC):
        truth["pool"].append({
            "doc_id": i, "manufacturer": manu, "product_line": pl, "doc_type": dt,
            "pages": np_, "csi_section": csi, "paginated": pag,
            "has_text": i != SCAN_DOC_INDEX,
        })

    theme_names = list(THEMES)
    usage = [0] * len(POOL_SPEC)
    for p, project in enumerate(PROJECTS):
        theme = theme_names[p % len(theme_names)]
        theme_secs = THEMES[theme]
        candidates = [i for i, s in enumerate(POOL_SPEC) if s[4] in theme_secs]
        n_docs = rng.randint(5, 9)
        chosen: list[int] = []
        while len(chosen) < min(n_docs, len(candidates)):
            pick = rng.choices(candidates, weights=[weights[i] for i in candidates], k=1)[0]
            if pick not in chosen:
                chosen.append(pick)
        # realistic submittal order, stable within type
        chosen.sort(key=lambda i: (DOC_TYPE_ORDER.index(POOL_SPEC[i][2]), POOL_SPEC[i][0]))
        for i in chosen:
            usage[i] += 1

        pkg_id = f"PKG-{p + 1:03d}"
        cover_path = os.path.join(pool_dir, f"cover_{p:03d}.pdf")
        titles = [f"{POOL_SPEC[i][0]} {POOL_SPEC[i][1] or ''} ({POOL_SPEC[i][2]})".strip() for i in chosen]
        render_cover(p, project, theme, titles, cover_path)

        writer = PdfWriter()
        gt_docs = []
        page_cursor = 1
        for src, doc_id in [(cover_path, "cover")] + [(pool_paths[i], i) for i in chosen]:
            reader = PdfReader(src)
            n = len(reader.pages)
            for page in reader.pages:
                writer.add_page(page)
            gt_docs.append({"doc_id": doc_id, "page_start": page_cursor, "page_end": page_cursor + n - 1})
            page_cursor += n
        out_path = os.path.join(corpus_dir, f"{pkg_id}.pdf")
        with open(out_path, "wb") as fh:
            writer.write(fh)
        stamped = p % 3 == 0
        if stamped:
            stamp_package(out_path, p, THEME_SECTION_TITLE[theme][0])
        truth["packages"][pkg_id] = {"theme": theme, "project": project,
                                     "stamped": stamped, "docs": gt_docs}

    # byte-identical duplicate package (same file submitted twice)
    dup_src = os.path.join(corpus_dir, "PKG-001.pdf")
    dup_dst = os.path.join(corpus_dir, "PKG-031-DUP-OF-001.pdf")
    shutil.copy(dup_src, dup_dst)
    truth["packages"]["PKG-031-DUP-OF-001"] = dict(truth["packages"]["PKG-001"])

    truth["usage"] = {str(i): u for i, u in enumerate(usage)}
    truth["unique_docs_used"] = sum(1 for u in usage if u > 0)
    with open(os.path.join(out_dir, "ground_truth.json"), "w") as fh:
        json.dump(truth, fh, indent=2)

    n_pages_total = sum(
        d["page_end"] - d["page_start"] + 1
        for pkg in truth["packages"].values() for d in pkg["docs"]
    )
    print(f"Fixture: {len(truth['packages'])} packages, {truth['unique_docs_used']} unique pool docs used "
          f"(+1 unique cover each), {n_pages_total} pages -> {corpus_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
