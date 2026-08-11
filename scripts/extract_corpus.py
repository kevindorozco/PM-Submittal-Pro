#!/usr/bin/env python3
"""Phase 0 mining pass: walk approved submittal packages, emit per-document metadata.

Walks a directory of package PDFs and writes one JSONL file with two record types:

  {"record_type": "package", ...}   one per PDF (page count, file hash, errors)
  {"record_type": "document", ...}  one per detected document segment within a PDF

Document boundaries are detected from layered signals (PDF outline/bookmarks,
"Page 1 of N" resets, pagination continuity, inter-page text similarity,
doc-start markers, manufacturer changes). Every document record carries the
signals that fired at its start boundary so bad splits can be audited.

Dedupe identity is carried two ways: a sha256 of the document's normalized text,
and a per-page hash list so the analyzer can match documents across packages
even when a boundary is off by a page.

Usage:
    python3 scripts/extract_corpus.py ./corpus -o corpus_extract.jsonl
    python3 scripts/extract_corpus.py ./corpus -o smoke.jsonl --limit 5 --workers 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone

from pypdf import PdfReader

EXTRACTOR_VERSION = "0.1.0"

# ---------------------------------------------------------------------------
# Lexicons — manufacturers seen in lath/plaster/drywall/ceiling submittals.
# alias strings are matched case-insensitively on word boundaries; domains are
# matched anywhere (covers footers like "www.clarkdietrich.com").
# ---------------------------------------------------------------------------

MANUFACTURERS: dict[str, dict] = {
    "ClarkDietrich": {"aliases": ["clarkdietrich", "clark dietrich", "clarkwestern dietrich"], "domains": ["clarkdietrich.com"]},
    "CEMCO": {"aliases": ["cemco", "california expanded metal"], "domains": ["cemcosteel.com"]},
    "SCAFCO": {"aliases": ["scafco"], "domains": ["scafco.com"]},
    "Marino\\WARE": {"aliases": ["marino\\ware", "marino ware", "marinoware"], "domains": ["marinoware.com"]},
    "Super Stud": {"aliases": ["super stud"], "domains": ["buysuperstud.com"]},
    "Telling Industries": {"aliases": ["telling industries"], "domains": ["tellingindustries.com"]},
    "The Steel Network": {"aliases": ["steel network"], "domains": ["steelnetwork.com"]},
    "Phillips Manufacturing": {"aliases": ["phillips manufacturing"], "domains": ["phillipsmfg.com"]},
    "AMICO": {"aliases": ["amico", "alabama metal industries"], "domains": ["amico-lath.com", "amicoglobal.com"]},
    "Tree Island Steel": {"aliases": ["tree island", "k-lath", "k lath"], "domains": ["treeisland.com"]},
    "Structa Wire": {"aliases": ["structa wire", "structalath"], "domains": ["structawire.com"]},
    "Davis Wire": {"aliases": ["davis wire"], "domains": ["daviswire.com"]},
    "USG": {"aliases": ["usg", "united states gypsum", "u.s. gypsum"], "domains": ["usg.com"]},
    "Georgia-Pacific": {"aliases": ["georgia-pacific", "georgia pacific", "densglass", "densarmor", "densshield", "toughrock"], "domains": ["gp.com", "buildgp.com", "densglass.com"]},
    "National Gypsum": {"aliases": ["national gypsum", "gold bond"], "domains": ["nationalgypsum.com", "goldbondbuilding.com"]},
    "CertainTeed": {"aliases": ["certainteed"], "domains": ["certainteed.com"]},
    "American Gypsum": {"aliases": ["american gypsum"], "domains": ["americangypsum.com"]},
    "PABCO Gypsum": {"aliases": ["pabco"], "domains": ["pabcogypsum.com"]},
    "James Hardie": {"aliases": ["james hardie", "hardiebacker"], "domains": ["jameshardie.com"]},
    "Quikrete": {"aliases": ["quikrete"], "domains": ["quikrete.com"]},
    "SPEC MIX": {"aliases": ["spec mix", "spec-mix"], "domains": ["specmix.com"]},
    "Merlex": {"aliases": ["merlex"], "domains": ["merlex.com"]},
    "Omega Products": {"aliases": ["omega products"], "domains": ["omega-products.com"]},
    "LaHabra": {"aliases": ["lahabra", "la habra"], "domains": ["lahabrastucco.com"]},
    "Parex": {"aliases": ["parex", "parexusa"], "domains": ["parex.com", "parexusa.com"]},
    "Sto": {"aliases": ["sto corp", "stocorp", "sto powerwall"], "domains": ["stocorp.com"]},
    "Dryvit": {"aliases": ["dryvit"], "domains": ["dryvit.com"]},
    "Master Builders Solutions": {"aliases": ["master builders", "masterseal", "basf construction"], "domains": ["master-builders-solutions.com"]},
    "Sika": {"aliases": ["sika"], "domains": ["sika.com", "usa.sika.com"]},
    "CTS Cement": {"aliases": ["cts cement", "rapid set"], "domains": ["ctscement.com"]},
    "CalPortland": {"aliases": ["calportland", "cal portland"], "domains": ["calportland.com"]},
    "Westpac Materials": {"aliases": ["westpac"], "domains": ["westpacmaterials.com"]},
    "Hamilton Materials": {"aliases": ["hamilton materials"], "domains": ["hamiltonmaterials.com"]},
    "El Rey": {"aliases": ["el rey stucco"], "domains": ["elrey.com"]},
    "Expo Stucco": {"aliases": ["expo stucco"], "domains": ["expostucco.com"]},
    "BMI Products": {"aliases": ["bmi products"], "domains": ["bmiproducts.com"]},
    "Armstrong": {"aliases": ["armstrong", "armstrong world industries", "armstrong ceilings"], "domains": ["armstrongceilings.com"]},
    "Rockfon": {"aliases": ["rockfon"], "domains": ["rockfon.com"]},
    "Chicago Metallic": {"aliases": ["chicago metallic"], "domains": ["chicagometallic.com"]},
    "Hunter Douglas": {"aliases": ["hunter douglas"], "domains": ["hunterdouglas.com"]},
    "9Wood": {"aliases": ["9wood"], "domains": ["9wood.com"]},
    "Rulon International": {"aliases": ["rulon"], "domains": ["rulonco.com"]},
    "Arktura": {"aliases": ["arktura"], "domains": ["arktura.com"]},
    "Owens Corning": {"aliases": ["owens corning", "thermafiber"], "domains": ["owenscorning.com"]},
    "Johns Manville": {"aliases": ["johns manville"], "domains": ["jm.com"]},
    "Knauf": {"aliases": ["knauf"], "domains": ["knaufinsulation.com", "knaufinsulation.us"]},
    "Rockwool": {"aliases": ["rockwool", "roxul"], "domains": ["rockwool.com"]},
    "Fortifiber": {"aliases": ["fortifiber", "jumbo tex", "super jumbo tex"], "domains": ["fortifiber.com"]},
    "DuPont": {"aliases": ["dupont", "tyvek"], "domains": ["dupont.com", "tyvek.com"]},
    "VaproShield": {"aliases": ["vaproshield"], "domains": ["vaproshield.com"]},
    "Prosoco": {"aliases": ["prosoco"], "domains": ["prosoco.com"]},
    "Henry Company": {"aliases": ["henry company"], "domains": ["henry.com"]},
    "Carlisle": {"aliases": ["carlisle coatings", "carlisle ccw"], "domains": ["carlisleccw.com"]},
    "GCP Applied Technologies": {"aliases": ["gcp applied", "perm-a-barrier"], "domains": ["gcpat.com"]},
    "Tamlyn": {"aliases": ["tamlyn"], "domains": ["tamlyn.com"]},
    "Huber": {"aliases": ["zip system", "huber engineered woods"], "domains": ["huberwood.com"]},
    "Tremco": {"aliases": ["tremco"], "domains": ["tremcosealants.com"]},
    "Pecora": {"aliases": ["pecora"], "domains": ["pecora.com"]},
    "DOWSIL": {"aliases": ["dowsil", "dow corning"], "domains": ["dow.com"]},
    "Hilti": {"aliases": ["hilti"], "domains": ["hilti.com"]},
    "Simpson Strong-Tie": {"aliases": ["simpson strong-tie", "simpson strong tie"], "domains": ["strongtie.com"]},
    "Grabber": {"aliases": ["grabber construction"], "domains": ["grabberman.com"]},
    "ITW Buildex": {"aliases": ["itw buildex", "teks"], "domains": ["itwbuildex.com"]},
    "DeWalt Anchors": {"aliases": ["powers fasteners", "dewalt anchors"], "domains": ["dewalt.com", "powers.com"]},
    "Trim-Tex": {"aliases": ["trim-tex", "trim tex"], "domains": ["trim-tex.com"]},
    "Vinyl Corp": {"aliases": ["vinyl corp"], "domains": ["vinylcorp.com"]},
    "Plastic Components": {"aliases": ["plastic components"], "domains": ["plasticomponents.com"]},
    "Stockton Products": {"aliases": ["stockton products"], "domains": ["stocktonproducts.com"]},
    "Fry Reglet": {"aliases": ["fry reglet"], "domains": ["fryreglet.com"]},
    "MM Systems": {"aliases": ["mm systems"], "domains": ["mmsystemscorp.com"]},
    "Keene Building Products": {"aliases": ["keene building"], "domains": ["keenebuilding.com"]},
    "Kinetics Noise Control": {"aliases": ["kinetics noise"], "domains": ["kineticsnoise.com"]},
    "PAC International": {"aliases": ["pac international", "rsic-1"], "domains": ["pac-intl.com"]},
    "Pliteq": {"aliases": ["pliteq", "genieclip"], "domains": ["pliteq.com"]},
    "Custom Building Products": {"aliases": ["custom building products"], "domains": ["custombuildingproducts.com"]},
    "ICC Evaluation Service": {"aliases": ["icc evaluation service", "icc-es"], "domains": ["icc-es.org"]},
}

# Product families worth naming outright; everything else falls back to the
# title guess. Keyed by canonical manufacturer.
PRODUCT_LINES: dict[str, list[str]] = {
    "USG": ["Sheetrock", "Durock", "Securock", "Fiberock", "Imperial", "Diamond", "Structo-Lite", "Red Top", "Donn", "Mars", "Radar", "Eclipse", "Halcyon"],
    "Georgia-Pacific": ["DensGlass", "DensArmor", "DensShield", "DensDeck", "ToughRock"],
    "National Gypsum": ["Gold Bond", "eXP", "XP", "Hi-Abuse", "Hi-Impact", "PermaBase"],
    "CertainTeed": ["AirRenew", "Easi-Lite", "Extreme", "GlasRoc", "Symphony", "Adagio"],
    "ClarkDietrich": ["ProSTUD", "ProTRAK", "MaxTrak", "SLP-TRK", "BlazeFrame", "RedHeader", "Strait-Flex", "E-Flange", "Danback", "Fast Bridge"],
    "CEMCO": ["Sure-Board", "FAS Track", "ViperStud", "ViperTrack", "Smart Stud"],
    "Armstrong": ["Ultima", "Optima", "Cirrus", "Dune", "Calla", "Lyra", "Prelude", "Suprafine", "Silhouette", "Axiom", "MetalWorks", "WoodWorks", "Tectum"],
    "Rockfon": ["Sonar", "Koral", "Cinema Black", "Chicago Metallic"],
    "Quikrete": ["Base Coat Stucco", "Finish Coat Stucco", "FastSet"],
    "SPEC MIX": ["Scratch and Brown", "Stucco Base Coat", "Fiber Base Coat"],
    "Omega Products": ["OmegaFlex", "ColorTek", "AkroFlex", "Diamond Wall"],
    "Merlex": ["SuperBlend", "P-100", "Acoustic Plaster"],
    "LaHabra": ["Fastwall", "Perma-Flex", "Exterior Stucco Color Coat"],
    "Parex": ["Standard Stucco Base", "La Habra", "Armourwall"],
    "Fortifiber": ["Jumbo Tex", "Super Jumbo Tex", "WeatherSmart", "Moistop"],
    "DuPont": ["Tyvek", "StuccoWrap", "CommercialWrap", "FlexWrap"],
    "Owens Corning": ["Thermafiber", "SAFB", "EcoTouch", "703", "705"],
    "Johns Manville": ["Formaldehyde-free", "MinWool", "Sound-SHIELD"],
    "Rockwool": ["Safe'n'Sound", "Comfortbatt", "AFB"],
    "Simpson Strong-Tie": ["Titen", "Strong-Drive", "SUBH", "DBC"],
    "Hilti": ["Kwik Bolt", "X-U", "HDA", "KH-EZ"],
    "Tremco": ["Dymonic", "Spectrem", "Vulkem", "ExoAir"],
    "Pecora": ["Dynatrol", "AC-20", "890NST"],
    "Trim-Tex": ["Chamfer", "Bullnose", "Tear Away", "Magic Corner"],
    "PABCO Gypsum": ["FlameCurb", "QuietRock"],
    "James Hardie": ["HardieBacker", "HardiePanel"],
}

DOC_START_MARKERS = [
    "safety data sheet",
    "material safety data sheet",
    "icc-es evaluation report",
    "evaluation report",
    "technical data sheet",
    "product data sheet",
    "submittal sheet",
    "technical bulletin",
    "installation instructions",
    "installation guide",
    "limited warranty",
    "warranty certificate",
    "mix design",
    "test report",
    "letter of certification",
    "certificate of compliance",
]

CSI_DIVISIONS = {
    "01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12",
    "13", "14", "21", "22", "23", "25", "26", "27", "28", "31", "32", "33",
}

CSI_RE = re.compile(r"(?<![\d.])(\d{2})[ .]?(\d{2})[ .]?(\d{2})(?![\d.])")
ESR_RE = re.compile(r"\bES[RL]-\d{3,5}\b", re.I)
PAGE_OF_RE = re.compile(r"\b(?:page|pg\.?|sheet)\s*(\d{1,3})\s*(?:of|/)\s*(\d{1,3})\b", re.I)
N_OF_M_RE = re.compile(r"(?<![\w/.-])(\d{1,3})\s+of\s+(\d{1,3})(?![\w/.-])", re.I)
PAGE_STAMP_LINE_RE = re.compile(r"(?:page|pg\.?|sheet)?\s*\d{1,4}\s*(?:(?:of|/)\s*\d{1,4})?", re.I)
DOMAIN_RE = re.compile(r"\b(?:www\.)?([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:com|net|org|us|biz))\b", re.I)
TOKEN_RE = re.compile(r"[a-z0-9]{2,}")

MIN_PAGE_TEXT_CHARS = 25          # below this a page is treated as "no text"
BOUNDARY_THRESHOLD = 50           # summed signal score that declares a new document

# Compiled alias patterns, built once per process.
_ALIAS_PATTERNS: list[tuple[str, re.Pattern]] | None = None


def _alias_patterns() -> list[tuple[str, re.Pattern]]:
    global _ALIAS_PATTERNS
    if _ALIAS_PATTERNS is None:
        pats = []
        for canonical, spec in MANUFACTURERS.items():
            for alias in spec["aliases"]:
                pats.append((canonical, re.compile(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", re.I)))
            for domain in spec.get("domains", []):
                pats.append((canonical, re.compile(re.escape(domain), re.I)))
        _ALIAS_PATTERNS = pats
    return _ALIAS_PATTERNS


# ---------------------------------------------------------------------------
# Per-page analysis
# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """Normalization used for hashing: case/whitespace-insensitive, page-stamp
    lines dropped (assembly stamps differ between packages for the same source
    document). Revision dates are kept — they are part of document identity."""
    t = unicodedata.normalize("NFKC", text).lower()
    kept = []
    for line in t.splitlines():
        line = line.strip()
        if not line:
            continue
        if PAGE_STAMP_LINE_RE.fullmatch(line):
            continue
        kept.append(line)
    return re.sub(r"\s+", " ", " ".join(kept)).strip()


def page_features(raw_text: str) -> dict:
    text = raw_text or ""
    norm = normalize_text(text)
    has_text = len(norm) >= MIN_PAGE_TEXT_CHARS
    lower = text.lower()

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    head = " ".join(lines[:5]).lower()
    tail = " ".join(lines[-3:]).lower()

    def edge_tokens(s: str) -> set:
        # page edges are compared as doc-identity fingerprints: phone digits
        # and url plumbing are shared boilerplate, not identity
        return {t for t in TOKEN_RE.findall(s)
                if not t.isdigit() and t not in ("www", "com", "net", "org", "inc", "llc")}

    # "Page 2 of 6" anywhere; bare "2 of 6" only in the header/footer region
    # (mid-text "1 of 3 coats" must not read as pagination).
    pagination = None
    m = PAGE_OF_RE.search(lower)
    if not m:
        edge = " ".join(lines[:2] + lines[-3:]).lower()
        m = N_OF_M_RE.search(edge)
    if m:
        k, n = int(m.group(1)), int(m.group(2))
        if 1 <= k <= n <= 500:
            pagination = (k, n)

    manu_counts: dict[str, int] = {}
    for canonical, pat in _alias_patterns():
        c = len(pat.findall(text))
        if c:
            manu_counts[canonical] = manu_counts.get(canonical, 0) + c

    markers = {mk for mk in DOC_START_MARKERS if mk in lower}

    csi = set()
    for m2 in CSI_RE.finditer(text):
        div = m2.group(1)
        if div in CSI_DIVISIONS:
            csi.add(f"{m2.group(1)} {m2.group(2)} {m2.group(3)}")

    return {
        "norm": norm,
        "has_text": has_text,
        "hash": hashlib.sha256(norm.encode()).hexdigest() if has_text else None,
        "tokens": set(TOKEN_RE.findall(norm)) if has_text else set(),
        "head_tokens": edge_tokens(head),
        "tail_tokens": edge_tokens(tail),
        "pagination": pagination,
        "manu_counts": manu_counts,
        "markers": markers,
        "csi": csi,
        "esr": set(x.upper() for x in ESR_RE.findall(text)),
        "lines": lines,
    }


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def boundary_signals(prev: dict, cur: dict, outline_start: bool) -> tuple[int, list[str]]:
    """Score the transition prev-page -> cur-page. >= BOUNDARY_THRESHOLD means
    cur starts a new document."""
    score, signals = 0, []

    if outline_start:
        score += 100
        signals.append("outline_bookmark")

    pp, cp = prev["pagination"], cur["pagination"]
    pagination_informed = False
    if cp and cp[0] == 1 and cp[1] > 1:
        score += 70
        signals.append("pagination_reset")
        pagination_informed = True
    if cp == (1, 1):
        score += 50
        signals.append("pagination_single_page")
        pagination_informed = True
    if pp and pp[0] == pp[1]:
        # previous page was the LAST page of its sequence ("Page 2 of 2"):
        # the current page provably cannot continue that document
        score += 50
        signals.append("pagination_terminal")
        pagination_informed = True
    if pp and cp and cp[1] == pp[1] and cp[0] == pp[0] + 1:
        score -= 80
        signals.append("pagination_continues")
        pagination_informed = True

    if prev["has_text"] and cur["has_text"]:
        head_j = _jaccard(prev["head_tokens"], cur["head_tokens"])
        tail_j = _jaccard(prev["tail_tokens"], cur["tail_tokens"])
        sim = max(_jaccard(prev["tokens"], cur["tokens"]), head_j, tail_j)
        if head_j < 0.10 and tail_j < 0.10:
            # header AND footer both changed completely — different docs share
            # body boilerplate, but almost never both page edges
            score += 25
            signals.append("edge_fingerprint_change")
        if not pagination_informed:
            # Similarity only arbitrates when pagination says nothing: different
            # docs from one manufacturer share enough boilerplate that a high
            # score must not override explicit page numbering.
            if sim < 0.12:
                score += 35
                signals.append(f"similarity_drop({sim:.2f})")
            elif sim > 0.55:
                score -= 40
                signals.append(f"similarity_high({sim:.2f})")
            elif sim > 0.35:
                score -= 15
                signals.append(f"similarity_mid({sim:.2f})")
    else:
        # One side has no text layer: similarity is unknowable. Lean on the
        # other signals; a text->no-text transition is weakly a boundary.
        if prev["has_text"] != cur["has_text"]:
            score += 35
            signals.append("text_layer_transition")

    new_markers = cur["markers"] - prev["markers"]
    if new_markers:
        score += 30
        signals.append("doc_marker:" + sorted(new_markers)[0])

    prev_manu = _top_manu(prev["manu_counts"])
    cur_manu = _top_manu(cur["manu_counts"])
    if prev_manu and cur_manu:
        if prev_manu != cur_manu:
            solid = prev["manu_counts"][prev_manu] >= 2 and cur["manu_counts"][cur_manu] >= 2
            score += 45 if solid else 35
            signals.append(f"manufacturer_change({prev_manu}->{cur_manu})")
        elif not pagination_informed:
            # same manufacturer suggests continuation, but never against
            # pagination evidence: adjacent docs from one manufacturer are
            # the norm in submittals, not the exception
            score -= 15
            signals.append("manufacturer_same")

    return score, signals


def _top_manu(counts: dict[str, int]) -> str | None:
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


# ---------------------------------------------------------------------------
# Document classification
# ---------------------------------------------------------------------------

def classify_doc_type(full_lower: str, first_page_lower: str) -> tuple[str, str]:
    """Returns (doc_type, confidence). Order matters: SDS and ICC-ES documents
    mention ASTM/product names constantly, so they are checked first."""
    if "safety data sheet" in first_page_lower or "material safety data" in first_page_lower:
        return "sds", "high"
    if "icc-es" in first_page_lower or re.search(r"\besr-\d", first_page_lower):
        return "icc_es", "high"
    if "icc-es evaluation report" in full_lower:
        return "icc_es", "medium"
    if "safety data sheet" in full_lower and "hazard" in full_lower:
        return "sds", "medium"
    if "warranty" in first_page_lower and ("limited warranty" in full_lower or full_lower.count("warranty") >= 3):
        return "warranty", "high"
    if "mix design" in first_page_lower:
        return "mix_design", "high"
    if "mix design" in full_lower:
        return "mix_design", "medium"
    if "test report" in first_page_lower or ("report no" in first_page_lower and "astm" in full_lower):
        return "test_report", "medium"
    if any(k in first_page_lower for k in ("installation instructions", "installation guide", "application instructions", "application guide")):
        return "installation_guide", "high"
    if re.search(r"\bdear\b|\bre:\s", first_page_lower) and len(full_lower) < 4000:
        return "letter", "medium"
    if any(k in first_page_lower for k in ("technical data", "product data", "submittal sheet", "data sheet", "product profile")):
        return "cutsheet", "high"
    if any(k in full_lower for k in ("features", "specifications", "physical properties", "astm")):
        return "cutsheet", "low"
    return "other", "low"


def guess_title(first_page_lines: list[str], manufacturer: str | None = None) -> str | None:
    manu_aliases = set()
    if manufacturer and manufacturer in MANUFACTURERS:
        manu_aliases = {a.lower() for a in MANUFACTURERS[manufacturer]["aliases"]}
        manu_aliases.add(manufacturer.lower())
    for ln in first_page_lines[:10]:
        if len(ln) < 4 or len(ln) > 90:
            continue
        low = ln.lower()
        if low.strip(" .,®™©") in manu_aliases:  # bare company-name line; the product line follows
            continue
        if PAGE_STAMP_LINE_RE.fullmatch(ln):
            continue
        if DOMAIN_RE.search(low) or low.startswith(("http", "tel", "fax", "phone")):
            continue
        if re.fullmatch(r"[\d\s/.\-:]+", ln):  # bare dates / numbers
            continue
        return ln
    return None


def detect_product_line(manufacturer: str | None, text: str) -> str | None:
    if not manufacturer or manufacturer not in PRODUCT_LINES:
        return None
    hits = [(pl, len(re.findall(r"(?<!\w)" + re.escape(pl) + r"(?!\w)", text, re.I)))
            for pl in PRODUCT_LINES[manufacturer]]
    hits = [(pl, c) for pl, c in hits if c > 0]
    if not hits:
        return None
    return max(hits, key=lambda kv: kv[1])[0]


# ---------------------------------------------------------------------------
# Package processing
# ---------------------------------------------------------------------------

def _outline_pages(reader: PdfReader) -> set[int]:
    pages: set[int] = set()

    def walk(items):
        for item in items:
            if isinstance(item, list):
                walk(item)
            else:
                try:
                    pages.add(reader.get_destination_page_number(item))
                except Exception:
                    pass

    try:
        walk(reader.outline)
    except Exception:
        pass
    return pages


def process_package(path: str, corpus_root: str) -> list[dict]:
    # corpus-relative id: job folders routinely contain identically-named PDFs,
    # and colliding ids would silently merge packages in the analyzer
    rel = os.path.relpath(path, corpus_root)
    package_id = os.path.splitext(rel)[0].replace(os.sep, "/")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with open(path, "rb") as fh:
        file_sha = hashlib.sha256(fh.read()).hexdigest()

    pkg_record = {
        "record_type": "package",
        "package_id": package_id,
        "file": rel,
        "file_sha256": file_sha,
        "file_size": os.path.getsize(path),
        "page_count": None,
        "pages_with_text": None,
        "has_outline": False,
        "error": None,
        "extracted_at": now,
        "extractor_version": EXTRACTOR_VERSION,
    }

    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            reader.decrypt("")
        n_pages = len(reader.pages)
        outline_pages = _outline_pages(reader)
        feats = []
        for page in reader.pages:
            try:
                raw = page.extract_text() or ""
            except Exception:
                raw = ""
            feats.append(page_features(raw))
    except Exception as exc:
        pkg_record["error"] = f"{type(exc).__name__}: {exc}"
        return [pkg_record]

    pkg_record["page_count"] = n_pages
    pkg_record["pages_with_text"] = sum(1 for f in feats if f["has_text"])
    pkg_record["has_outline"] = bool(outline_pages)

    # Segment: page 0 always starts a document.
    starts: list[tuple[int, int, list[str]]] = [(0, 100, ["package_start"])]
    for i in range(1, n_pages):
        score, signals = boundary_signals(feats[i - 1], feats[i], i in outline_pages)
        if score >= BOUNDARY_THRESHOLD:
            starts.append((i, score, signals))

    records = [pkg_record]
    boundaries = [s[0] for s in starts] + [n_pages]
    for di, (start, score, signals) in enumerate(starts):
        end = boundaries[di + 1] - 1  # inclusive
        seg = feats[start:end + 1]
        seg_norms = [f["norm"] for f in seg if f["has_text"]]
        seg_text = "\n\f\n".join((f["norm"] for f in seg))  # page-separated normalized text
        raw_full = " ".join(seg_norms)
        first_lower = seg[0]["norm"] if seg[0]["has_text"] else (seg_norms[0] if seg_norms else "")

        manu_counts: dict[str, int] = {}
        csi: set[str] = set()
        esr: set[str] = set()
        for f in seg:
            for k, v in f["manu_counts"].items():
                manu_counts[k] = manu_counts.get(k, 0) + v
            csi |= f["csi"]
            esr |= f["esr"]

        manufacturer = _top_manu(manu_counts)
        total_manu_hits = sum(manu_counts.values())
        if manufacturer:
            share = manu_counts[manufacturer] / total_manu_hits
            manu_conf = "high" if (manu_counts[manufacturer] >= 3 and share >= 0.6) else ("medium" if share >= 0.5 else "low")
        else:
            manu_conf = None

        doc_type, type_conf = classify_doc_type(raw_full, first_lower)
        has_text = bool(seg_norms)

        records.append({
            "record_type": "document",
            "package_id": package_id,
            "doc_index": di,
            "page_start": start + 1,          # 1-based, inclusive
            "page_end": end + 1,
            "page_count": end - start + 1,
            "boundary_score": score,
            "boundary_signals": signals,
            "has_text": has_text,
            "text": seg_text,
            "text_sha256": hashlib.sha256(raw_full.encode()).hexdigest() if has_text else None,
            "page_hashes": [f["hash"] for f in seg],
            "doc_type": doc_type,
            "doc_type_confidence": type_conf,
            "manufacturer": manufacturer,
            "manufacturer_confidence": manu_conf,
            "product_line": detect_product_line(manufacturer, raw_full),
            "title_guess": guess_title(seg[0]["lines"], manufacturer),
            "csi_sections": sorted(csi),
            "icc_esr_numbers": sorted(esr),
        })
    return records


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpus_dir", help="Directory containing approved package PDFs (searched recursively)")
    ap.add_argument("-o", "--output", default="corpus_extract.jsonl")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--limit", type=int, default=None, help="Only process the first N packages (smoke test)")
    args = ap.parse_args()

    pdfs = []
    for root, _dirs, files in os.walk(args.corpus_dir):
        for name in sorted(files):
            if name.lower().endswith(".pdf"):
                pdfs.append(os.path.join(root, name))
    pdfs.sort()
    if args.limit:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        print(f"No PDFs found under {args.corpus_dir}", file=sys.stderr)
        return 1

    print(f"Extracting {len(pdfs)} packages with {args.workers} workers -> {args.output}", file=sys.stderr)
    done = errors = 0
    with open(args.output, "w", encoding="utf-8") as out:
        if args.workers == 1:
            results = (process_package(p, args.corpus_dir) for p in pdfs)
            for records in results:
                done += 1
                errors += sum(1 for r in records if r["record_type"] == "package" and r["error"])
                for r in records:
                    out.write(json.dumps(r, ensure_ascii=False) + "\n")
                if done % 10 == 0 or done == len(pdfs):
                    print(f"  {done}/{len(pdfs)} packages ({errors} errors)", file=sys.stderr)
        else:
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                futs = {pool.submit(process_package, p, args.corpus_dir): p for p in pdfs}
                for fut in as_completed(futs):
                    done += 1
                    try:
                        records = fut.result()
                    except Exception as exc:
                        rel = os.path.relpath(futs[fut], args.corpus_dir)
                        records = [{
                            "record_type": "package",
                            "package_id": os.path.splitext(rel)[0].replace(os.sep, "/"),
                            "file": rel,
                            "error": f"worker crash: {type(exc).__name__}: {exc}",
                            "extractor_version": EXTRACTOR_VERSION,
                        }]
                    errors += sum(1 for r in records if r["record_type"] == "package" and r.get("error"))
                    for r in records:
                        out.write(json.dumps(r, ensure_ascii=False) + "\n")
                    if done % 10 == 0 or done == len(pdfs):
                        print(f"  {done}/{len(pdfs)} packages ({errors} errors)", file=sys.stderr)

    print(f"Done: {done} packages, {errors} errors -> {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
