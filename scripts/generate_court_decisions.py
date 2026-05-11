#!/usr/bin/env python3
"""
Fetch Supreme Court (Riigikohus) decisions from RIK and generate JSON-LD ontology files.

Data source: https://rikos.rik.ee (Supreme Court decisions search)
API: GET https://rikos.rik.ee/?aasta=YYYY&pageSize=100&lk=N

Generates:
  - krr_outputs/riigikohus/riigikohus_YYYY_peep.json  (per-year files)
  - krr_outputs/riigikohus/riigikohus_schema.json      (schema definitions)
  - krr_outputs/riigikohus/RIIGIKOHUS_INDEX.json        (registry)
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import math
import re
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

import requests

try:  # Optional dep — falls back to regex parser when unavailable.
    from bs4 import BeautifulSoup  # type: ignore[import-not-found]

    _BS4_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when bs4 missing
    BeautifulSoup = None  # type: ignore[assignment]
    _BS4_AVAILABLE = False

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
RK_DIR = KRR_DIR / "riigikohus"
RK_DIR.mkdir(parents=True, exist_ok=True)

NS = "https://data.riik.ee/ontology/estleg#"

SEARCH_URL = "https://rikos.rik.ee/"
PAGE_SIZE = 100
RATE_DELAY = 0.3  # seconds between requests

CONTEXT = {
    "estleg": NS,
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dcterms": "http://purl.org/dc/terms/",
}

# Case type classification based on case number prefix
CASE_TYPE_MAP = {
    "1": ("Criminal", "Kriminaalasi", "Criminal Case"),
    "2": ("Civil", "Tsiviilasi", "Civil Case"),
    "3": ("Administrative", "Haldusasi", "Administrative Case"),
    "4": ("Misdemeanor", "Väärteoasi", "Misdemeanor Case"),
    "5": ("ConstitutionalReview", "Põhiseaduslikkuse järelevalve", "Constitutional Review"),
    "Other": ("Other", "Muu", "Other Case"),
}

# Decision type mapping
DECISION_TYPES = {
    "Kohtuotsus": ("Judgment", "Kohtuotsus"),
    "Kohtumäärus": ("Ruling", "Kohtumäärus"),
    "Kohtu resolutsioon": ("Resolution", "Kohtu resolutsioon"),
    "Määrus": ("OrderRuling", "Määrus"),
}


_ESTONIAN_TRANSLITERATION: dict[str, str] = {
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
}
_TRANSLIT_TABLE = str.maketrans(_ESTONIAN_TRANSLITERATION)


def sanitize_id(value: str) -> str:
    """Create a safe ID from a string."""
    s = value.replace("/", "_").replace("-", "_")
    # Transliterate Estonian diacritics before stripping non-ASCII
    s = s.translate(_TRANSLIT_TABLE)
    s = re.sub(r"[^0-9A-Za-z_]", "_", s)
    return s[:80] or "Unknown"


# ``\s`` does not match the byte-order mark (U+FEFF); strip it
# explicitly before matching so legacy fetched feeds with a stray
# BOM don't fall through to the ``Other`` bucket.
_CASE_PREFIX_RE = re.compile(r"^[\s﻿]*(\d)")


def classify_case(case_nr: str) -> tuple[str, str, str]:
    """Classify case type from the first numeric token in ``case_nr``.

    The Riigikohus convention is: case numbers begin with the case-type
    digit (1=criminal, 2=civil, ...). Real-world inputs sometimes carry
    leading whitespace, BOM, or other invisible characters, so we
    skip leading whitespace and pick the FIRST decimal digit. Inputs
    with no leading digit (or empty inputs) are logged at WARNING
    and classified as ``Other`` rather than mis-mapped silently.
    """
    if not case_nr:
        logger.warning("classify_case: empty case_nr")
        return CASE_TYPE_MAP["Other"]
    match = _CASE_PREFIX_RE.match(case_nr)
    if match is None:
        logger.warning(
            "classify_case: no leading digit in case_nr=%r — classifying as Other",
            case_nr,
        )
        return CASE_TYPE_MAP["Other"]
    return CASE_TYPE_MAP.get(match.group(1), CASE_TYPE_MAP["Other"])


# Known law abbreviations — anchored matches must lowercase before
# deduplication so "KarS" / "kars" / "KARS" collapse into the canonical
# "KarS" form rather than producing duplicate entries.
_KNOWN_LAW_ABBREVS: tuple[str, ...] = (
    "KarS",
    "VÕS",
    "TsÜS",
    "AÕS",
    "PKS",
    "ÄS",
    "HMS",
    "TsMS",
    "KrMS",
    "TMS",
    "KOKS",
    "PS",
    "PankrS",
    "MKS",
    "TLS",
)
_LAW_ABBREV_LOOKUP: dict[str, str] = {
    abbrev.casefold(): abbrev for abbrev in _KNOWN_LAW_ABBREVS
}

# Word-boundary anchored: prevents ``\w+seaduse`` from absorbing
# trailing characters into a longer match (greediness).
_KNOWN_LAW_PATTERN = re.compile(
    r"\b(" + "|".join(_KNOWN_LAW_ABBREVS) + r")\b\s*§\s*[\d\-]+",
    re.IGNORECASE,
)
_GENITIVE_LAW_PATTERN = re.compile(
    r"\b(\w+seadus(?:e|tiku?)?)\b\s*§\s*[\d\-]+\s*(?:lg\s*[\d]+)?",
    re.IGNORECASE,
)


def detect_referenced_laws(summary: str) -> list[str]:
    """Extract law references (KarS §, VÕS §, etc.) from decision summary.

    Returns canonicalised law identifiers in first-occurrence order.
    Known abbreviations resolve through ``_LAW_ABBREV_LOOKUP`` so
    case variants collapse (e.g. ``Kars`` and ``KarS`` both become
    ``KarS``). Genitive long-form references (``liiklusseaduse``,
    ``perekonnaseaduse``) are lowercased before deduplication so the
    output is case-stable across runs.
    """
    if not summary:
        return []
    refs: list[str] = []
    seen: set[str] = set()

    for match in _KNOWN_LAW_PATTERN.finditer(summary):
        raw = match.group(1)
        canonical = _LAW_ABBREV_LOOKUP.get(raw.casefold(), raw)
        if canonical not in seen:
            seen.add(canonical)
            refs.append(canonical)

    for match in _GENITIVE_LAW_PATTERN.finditer(summary):
        canonical = match.group(1).lower()
        if canonical not in seen:
            seen.add(canonical)
            refs.append(canonical)

    return refs


# Number of <td> columns we expect on every search-result row. The
# Riigikohus search result table follows: date | case_nr (with link) |
# description | object_id. Any row deviating from this shape is a
# parser-mismatch signal and is skipped with a logger warning.
_EXPECTED_COLUMN_COUNT = 4


def _extract_row_fields(tds_text: list[str], link: str = "") -> dict | None:
    """Build a decision dict from already-extracted text columns.

    Shared between the BeautifulSoup and regex parsers so behaviour
    stays identical regardless of code path. Returns ``None`` for
    rows missing required fields.
    """
    if len(tds_text) < _EXPECTED_COLUMN_COUNT:
        return None
    date_str = tds_text[0].strip()
    case_nr = tds_text[1].strip()
    raw_desc = re.sub(r"\s+", " ", tds_text[2].strip())
    raw_desc = html_mod.unescape(raw_desc)
    obj_id = tds_text[3].strip()

    if not case_nr or not date_str:
        return None

    parts = raw_desc.split("|", 1)
    decision_type = parts[0].strip() if len(parts) > 1 else ""
    summary = parts[1].strip() if len(parts) > 1 else raw_desc

    return {
        "date": date_str,
        "case_nr": case_nr,
        "decision_type": decision_type,
        "summary": summary,
        "object_id": obj_id,
        "link": link,
    }


def _parse_with_bs4(html_text: str) -> list[dict]:
    """Preferred path: BeautifulSoup-based row extraction.

    Targets rows in any ``<table>`` and validates each row has
    exactly ``_EXPECTED_COLUMN_COUNT`` ``<td>`` cells before
    accepting. Rows with unexpected column counts are logged at
    WARNING for visibility into parser regressions.
    """
    decisions: list[dict] = []
    soup = BeautifulSoup(html_text, "html.parser")
    # Prefer the dedicated search-result table when present; fall
    # back to scanning all tables otherwise.
    tables = soup.select("table.search-result") or soup.find_all("table")
    for table in tables:
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if not tds:
                continue
            if len(tds) != _EXPECTED_COLUMN_COUNT:
                logger.warning(
                    "parse_html_table: skipping row with %d columns (expected %d)",
                    len(tds),
                    _EXPECTED_COLUMN_COUNT,
                )
                continue
            tds_text = [td.get_text(" ", strip=True) for td in tds]
            link = ""
            anchor = tds[1].find("a", href=True)
            if anchor:
                link = anchor["href"]
                if link and not link.startswith("http"):
                    link = f"https://rikos.rik.ee{link}"
            row = _extract_row_fields(tds_text, link=link)
            if row is not None:
                decisions.append(row)
    return decisions


def _parse_with_regex(html_text: str) -> list[dict]:
    """Fallback path used when BeautifulSoup is unavailable.

    The regex approach is fragile against nested markup; we add a
    column-count assertion and warning logging to surface parser
    drift instead of silently miscounting rows.
    """
    decisions: list[dict] = []
    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", html_text, re.DOTALL)
    for tr in trs:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.DOTALL)
        if not tds:
            continue
        if len(tds) != _EXPECTED_COLUMN_COUNT:
            logger.warning(
                "parse_html_table: skipping row with %d columns (expected %d)",
                len(tds),
                _EXPECTED_COLUMN_COUNT,
            )
            continue

        tds_text = [re.sub(r"<[^>]+>", "", td) for td in tds]
        link = ""
        link_match = re.search(r'href="([^"]+)"', tds[1])
        if link_match:
            link = link_match.group(1)
            if link and not link.startswith("http"):
                link = f"https://rikos.rik.ee{link}"

        row = _extract_row_fields(tds_text, link=link)
        if row is not None:
            decisions.append(row)
    return decisions


def parse_html_table(html_text: str) -> list[dict]:
    """Parse decision rows from the Riigikohus search-result HTML.

    Prefers BeautifulSoup with strict column-count validation; falls
    back to a regex-based parser when ``bs4`` is unavailable. Both
    parsers reject rows whose column count differs from
    ``_EXPECTED_COLUMN_COUNT`` (4) so summaries cannot silently
    absorb misaligned cell content.
    """
    if _BS4_AVAILABLE:
        return _parse_with_bs4(html_text)
    return _parse_with_regex(html_text)


def fetch_year(year: int) -> list[dict]:
    """Fetch all decisions for a given year."""
    # First request to get total count
    resp = requests.get(
        SEARCH_URL,
        params={"aasta": year, "pageSize": PAGE_SIZE},
        timeout=30,
    )
    resp.raise_for_status()

    total_match = re.search(r"Tulemusi leiti kokku:\s*(\d+)", resp.text)
    total = int(total_match.group(1)) if total_match else 0

    if total == 0:
        return []

    total_pages = math.ceil(total / PAGE_SIZE)
    print(f"  Year {year}: {total} decisions, {total_pages} pages")

    all_decisions = parse_html_table(resp.text)

    for page in range(2, total_pages + 1):
        time.sleep(RATE_DELAY)
        resp = requests.get(
            SEARCH_URL,
            params={"aasta": year, "pageSize": PAGE_SIZE, "lk": page},
            timeout=30,
        )
        resp.raise_for_status()
        page_decisions = parse_html_table(resp.text)
        all_decisions.extend(page_decisions)
        if page % 5 == 0:
            print(f"    Page {page}/{total_pages} ({len(all_decisions)} so far)")

    return all_decisions


def generate_schema_nodes() -> list[dict]:
    """Generate OWL schema nodes for CourtDecision."""
    nodes: list[dict] = [
        # CourtDecision class
        {
            "@id": "estleg:CourtDecision",
            "@type": ["owl:Class"],
            "rdfs:label": {"@value": "Kohtulahend (Court Decision)", "@language": "et"},
            "rdfs:comment": {"@value": "Riigikohtu lahend – kohtuotsus, kohtumäärus või resolutsioon.", "@language": "et"},
            "dc:description": {"@value": "A Supreme Court (Riigikohus) decision, including judgments, rulings, and resolutions.", "@language": "en"},
        },
        # CaseType class
        {
            "@id": "estleg:CaseType",
            "@type": ["owl:Class"],
            "rdfs:label": {"@value": "Kohtuasja liik (Case Type)", "@language": "et"},
            "rdfs:comment": {"@value": "Kohtuasja klassifikatsioon: kriminaalasi, tsiviilasi, haldusasi, väärteoasi, põhiseaduslikkuse järelevalve.", "@language": "et"},
        },
        # DecisionType class
        {
            "@id": "estleg:DecisionType",
            "@type": ["owl:Class"],
            "rdfs:label": {"@value": "Lahendi liik (Decision Type)", "@language": "et"},
            "rdfs:comment": {"@value": "Lahendi tüüp: kohtuotsus, kohtumäärus, resolutsioon.", "@language": "et"},
        },
    ]

    # Case type individuals
    for code, (type_id, label_et, label_en) in CASE_TYPE_MAP.items():
        nodes.append({
            "@id": f"estleg:CaseType_{type_id}",
            "@type": ["owl:NamedIndividual", "estleg:CaseType"],
            "rdfs:label": {"@value": label_et, "@language": "et"},
            "skos:prefLabel": {"@value": label_en, "@language": "en"},
            "estleg:caseTypeCode": code,
        })

    # Decision type individuals
    for label_et, (type_id, _) in DECISION_TYPES.items():
        nodes.append({
            "@id": f"estleg:DecisionType_{type_id}",
            "@type": ["owl:NamedIndividual", "estleg:DecisionType"],
            "rdfs:label": {"@value": label_et, "@language": "et"},
            "skos:prefLabel": {"@value": type_id, "@language": "en"},
        })

    # Object properties
    nodes.extend([
        {
            "@id": "estleg:caseType",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "kohtuasja liik", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {"@id": "estleg:CaseType"},
        },
        {
            "@id": "estleg:decisionType",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "lahendi liik", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {"@id": "estleg:DecisionType"},
        },
        {
            "@id": "estleg:interpretsLaw",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "tõlgendab seadust", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {
                "@type": "owl:Class",
                "owl:unionOf": {
                    "@list": [
                        {"@id": "estleg:LegalProvision"},
                        {"@id": "estleg:Act"},
                    ],
                },
            },
            "rdfs:comment": {
                "@value": "Links a court decision to the legal provision or whole act it interprets or applies.",
                "@language": "en",
            },
        },
    ])

    # Datatype properties
    nodes.extend([
        {
            "@id": "estleg:caseNumber",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "kohtuasja number", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {"@id": "xsd:string"},
        },
        {
            "@id": "estleg:decisionDate",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "lahendi kuupäev", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {"@id": "xsd:date"},
        },
        {
            "@id": "estleg:rikObjectId",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "RIK objekti ID",
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {"@id": "xsd:string"},
        },
        {
            "@id": "estleg:decisionLink",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "lahendi link", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {"@id": "xsd:anyURI"},
        },
        {
            "@id": "estleg:referencedLaw",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "viidatud seadus", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {"@id": "xsd:string"},
            "rdfs:comment": {"@value": "Name or abbreviation of law referenced in the decision.", "@language": "en"},
        },
    ])

    return nodes


def _build_node_id(dec: dict) -> str | None:
    """Build a deterministic ``@id`` for a decision.

    The IRI is built from ``(case_nr, object_id)`` so two decisions
    sharing the same ``case_nr`` always produce distinct, stable
    IRIs across runs (no run-order-dependent flipping). When
    ``object_id`` is missing we log a WARNING and skip the
    decision because there is no other reliable disambiguator.
    """
    case_nr = (dec.get("case_nr") or "").strip()
    if not case_nr:
        logger.warning("decision_to_node: skipping decision with empty case_nr: %r", dec)
        return None
    case_id = sanitize_id(case_nr)
    obj_id = (dec.get("object_id") or "").strip()
    if not obj_id:
        logger.warning(
            "decision_to_node: skipping decision %s — missing object_id required for stable IRI",
            case_nr,
        )
        return None
    return f"estleg:RK_{case_id}_{sanitize_id(obj_id)}"


def decision_to_node(dec: dict, year: int, seen_ids: set[str]) -> dict | None:
    """Convert a decision dict to a JSON-LD node.

    Returns ``None`` for decisions that cannot be assigned a stable
    IRI (missing ``case_nr`` or ``object_id``). Callers MUST handle
    ``None`` and skip the row.
    """
    node_id = _build_node_id(dec)
    if node_id is None:
        return None
    if node_id in seen_ids:
        # Same (case_nr, object_id) twice in one feed — extremely
        # rare. Keep the first; surface the dup at WARNING.
        logger.warning(
            "decision_to_node: duplicate node id %s — keeping first occurrence",
            node_id,
        )
        return None
    seen_ids.add(node_id)

    type_id, _type_et, _type_en = classify_case(dec["case_nr"])

    node: dict = {
        "@id": node_id,
        "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
        "rdfs:label": {"@value": f"RK {dec['case_nr']}", "@language": "et"},
        "estleg:caseNumber": dec["case_nr"],
        "estleg:caseType": {"@id": f"estleg:CaseType_{type_id}"},
    }

    # Decision type
    if dec.get("decision_type"):
        dt_info = DECISION_TYPES.get(dec["decision_type"])
        if dt_info:
            node["estleg:decisionType"] = {"@id": f"estleg:DecisionType_{dt_info[0]}"}

    # Date
    if dec.get("date"):
        try:
            parsed = datetime.strptime(dec["date"], "%d.%m.%Y")
            node["estleg:decisionDate"] = {
                "@value": parsed.strftime("%Y-%m-%d"),
                "@type": "xsd:date",
            }
        except ValueError:
            pass

    # Summary
    if dec.get("summary"):
        node["estleg:summary"] = {"@value": dec["summary"][:800], "@language": "et"}

    # Link — URL-encoded query string so case_nr containing '#'/'/'/spaces
    # never silently breaks the resulting URL. ``urlencode`` plus
    # ``quote(safe='')`` is belt-and-suspenders here: ``urlencode``
    # alone already escapes '#', '/', and spaces in values.
    if dec.get("link"):
        encoded_query = urllib.parse.urlencode(
            {"asjaNr": dec["case_nr"]}, quote_via=urllib.parse.quote
        )
        riigikohus_link = (
            f"https://www.riigikohus.ee/et/lahendid/?{encoded_query}"
        )
        node["estleg:decisionLink"] = {"@value": riigikohus_link, "@type": "xsd:anyURI"}
        node["dcterms:source"] = {"@id": dec["link"]}

    # RIK object ID
    if dec.get("object_id"):
        node["estleg:rikObjectId"] = dec["object_id"]

    # Referenced laws
    refs = detect_referenced_laws(dec.get("summary") or "")
    if refs:
        node["estleg:referencedLaw"] = refs

    return node


def save_json(filepath: Path, doc: dict):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    start_year = 1993
    end_year = 2026

    print("=" * 60)
    print("Fetching Supreme Court decisions from RIK")
    print(f"Years: {start_year}–{end_year}")
    print("=" * 60)

    # Generate schema
    print("\n--- Generating schema ---")
    schema_doc = {"@context": CONTEXT, "@graph": generate_schema_nodes()}
    schema_path = RK_DIR / "riigikohus_schema.json"
    save_json(schema_path, schema_doc)
    print(f"  Saved: {schema_path.name} ({len(schema_doc['@graph'])} nodes)")

    all_decisions: list[dict] = []
    year_stats: dict[int, int] = {}

    for year in range(end_year, start_year - 1, -1):
        print(f"\n--- Year {year} ---")
        try:
            decisions = fetch_year(year)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        if not decisions:
            print("  No decisions found")
            continue

        year_stats[year] = len(decisions)

        # Generate per-year file
        graph: list[dict] = [
            {
                "@id": f"estleg:Riigikohus_{year}_Map",
                "@type": ["owl:Ontology"],
                "rdfs:label": {"@value": f"Riigikohtu lahendid {year}", "@language": "et"},
                "dc:description": {"@value": f"Riigikohtu lahendid aastast {year} ({len(decisions)} lahendit)", "@language": "et"},
                "dc:source": "Riigikohus – rikos.rik.ee",
            },
        ]

        seen_ids: set[str] = set()
        skipped_in_year = 0
        for dec in decisions:
            node = decision_to_node(dec, year, seen_ids)
            if node is None:
                skipped_in_year += 1
                continue
            graph.append(node)
        if skipped_in_year:
            print(f"  Skipped {skipped_in_year} unsalvageable decisions for {year}")

        doc = {"@context": CONTEXT, "@graph": graph}
        out_path = RK_DIR / f"riigikohus_{year}_peep.json"
        save_json(out_path, doc)
        print(f"  Saved: {out_path.name} ({len(graph)} nodes)")

        all_decisions.extend(decisions)
        time.sleep(RATE_DELAY)

    # Generate index
    print("\n--- Generating index ---")
    index = {
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "source": "https://rikos.rik.ee",
        "total_decisions": len(all_decisions),
        "years": {str(y): c for y, c in sorted(year_stats.items(), reverse=True)},
        "case_type_counts": {},
    }

    # Count by case type — overall AND per-year breakdown so we can
    # validate the prefix distribution looks sensible (e.g. roughly
    # 30% Civil + 30% Criminal + 30% Administrative; an Other-heavy
    # year likely indicates a parser regression).
    type_counts: dict[str, int] = {}
    per_year_type_counts: dict[str, dict[str, int]] = {}
    for dec in all_decisions:
        type_id, _, _ = classify_case(dec["case_nr"])
        type_counts[type_id] = type_counts.get(type_id, 0) + 1
        # Year is captured implicitly in the decision feed; recompute
        # from case_nr (format is e.g. ``3-21-2176/52``).
        year_key = "unknown"
        match = re.search(r"\d-(\d{2})-", dec.get("case_nr", ""))
        if match:
            year_two_digit = int(match.group(1))
            # Two-digit year: 2000-99 maps to 2000-99, < 2000 not in use.
            year_key = str(2000 + year_two_digit)
        per_year_type_counts.setdefault(year_key, {})
        per_year_type_counts[year_key][type_id] = (
            per_year_type_counts[year_key].get(type_id, 0) + 1
        )
    index["case_type_counts"] = type_counts
    index["case_type_counts_per_year"] = per_year_type_counts

    index_path = RK_DIR / "RIIGIKOHUS_INDEX.json"
    save_json(index_path, index)
    print(f"  Saved: {index_path.name}")

    # Summary
    print("\n" + "=" * 60)
    print(f"Done! Fetched {len(all_decisions)} Supreme Court decisions.")
    print(f"Years covered: {start_year}–{end_year}")
    print(f"Files saved to: {RK_DIR.relative_to(REPO_ROOT)}")
    print()
    for type_id, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {type_id}: {count}")
    print("=" * 60)


if __name__ == "__main__":
    main()
