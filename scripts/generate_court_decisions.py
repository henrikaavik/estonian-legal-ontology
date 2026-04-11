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
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

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


def classify_case(case_nr: str) -> tuple[str, str, str]:
    """Classify case type from case number prefix."""
    first_digit = case_nr[0] if case_nr else ""
    return CASE_TYPE_MAP.get(first_digit, ("Other", "Muu", "Other Case"))


def detect_referenced_laws(summary: str) -> list[str]:
    """Extract law references (KarS §, VÕS §, etc.) from decision summary."""
    refs = []
    # Pattern: "KarS § 113 lg 1" or "liiklusseaduse § 227 lg 2"
    patterns = [
        r"(KarS|VÕS|TsÜS|AÕS|PKS|ÄS|HMS|TsMS|KrMS|TMS|KOKS|PS|PankrS|MKS|TLS)\s*§\s*[\d\-]+",
        r"(\w+seaduse?(?:tiku?)?)\s*§\s*[\d\-]+\s*(?:lg\s*[\d]+)?",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, summary, re.IGNORECASE)
        refs.extend(matches)
    return list(dict.fromkeys(refs))  # deduplicate


def parse_html_table(html_text: str) -> list[dict]:
    """Parse decision rows from HTML table."""
    decisions = []

    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", html_text, re.DOTALL)
    for tr in trs:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.DOTALL)
        if len(tds) < 4:
            continue

        date_str = re.sub(r"<[^>]+>", "", tds[0]).strip()
        case_nr = re.sub(r"<[^>]+>", "", tds[1]).strip()
        raw_desc = re.sub(r"<[^>]+>", "", tds[2]).strip()
        raw_desc = re.sub(r"\s+", " ", raw_desc)
        raw_desc = html_mod.unescape(raw_desc)
        obj_id = re.sub(r"<[^>]+>", "", tds[3]).strip()

        if not case_nr or not date_str:
            continue

        # Extract link
        link_match = re.search(r'href="([^"]+)"', tds[1])
        link = link_match.group(1) if link_match else ""
        if link and not link.startswith("http"):
            link = f"https://rikos.rik.ee{link}"

        # Parse decision type and summary
        parts = raw_desc.split("|", 1)
        decision_type = parts[0].strip() if len(parts) > 1 else ""
        summary = parts[1].strip() if len(parts) > 1 else raw_desc

        decisions.append({
            "date": date_str,
            "case_nr": case_nr,
            "decision_type": decision_type,
            "summary": summary,
            "object_id": obj_id,
            "link": link,
        })

    return decisions


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
            "rdfs:range": {"@id": "estleg:LegalProvision"},
            "rdfs:comment": {"@value": "Links a court decision to the legal provision it interprets or applies.", "@language": "en"},
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


def decision_to_node(dec: dict, year: int, seen_ids: set[str]) -> dict:
    """Convert a decision dict to a JSON-LD node."""
    case_id = sanitize_id(dec["case_nr"])
    type_id, type_et, type_en = classify_case(dec["case_nr"])

    # Ensure unique ID: if case_nr collides, append object_id
    node_id = f"estleg:RK_{case_id}"
    if node_id in seen_ids:
        node_id = f"estleg:RK_{case_id}_{dec['object_id']}"
    seen_ids.add(node_id)

    node: dict = {
        "@id": node_id,
        "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
        "rdfs:label": {"@value": f"RK {dec['case_nr']}", "@language": "et"},
        "estleg:caseNumber": dec["case_nr"],
        "estleg:caseType": {"@id": f"estleg:CaseType_{type_id}"},
    }

    # Decision type
    if dec["decision_type"]:
        dt_info = DECISION_TYPES.get(dec["decision_type"])
        if dt_info:
            node["estleg:decisionType"] = {"@id": f"estleg:DecisionType_{dt_info[0]}"}

    # Date
    if dec["date"]:
        try:
            parsed = datetime.strptime(dec["date"], "%d.%m.%Y")
            node["estleg:decisionDate"] = {
                "@value": parsed.strftime("%Y-%m-%d"),
                "@type": "xsd:date",
            }
        except ValueError:
            pass

    # Summary
    if dec["summary"]:
        node["estleg:summary"] = {"@value": dec["summary"][:800], "@language": "et"}

    # Link
    if dec["link"]:
        riigikohus_link = f"https://www.riigikohus.ee/et/lahendid/?asjaNr={dec['case_nr']}"
        node["estleg:decisionLink"] = {"@value": riigikohus_link, "@type": "xsd:anyURI"}
        node["dcterms:source"] = {"@id": dec["link"]}

    # RIK object ID
    if dec["object_id"]:
        node["estleg:rikObjectId"] = dec["object_id"]

    # Referenced laws
    refs = detect_referenced_laws(dec["summary"])
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
            print(f"  No decisions found")
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
        for dec in decisions:
            graph.append(decision_to_node(dec, year, seen_ids))

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

    # Count by case type
    type_counts: dict[str, int] = {}
    for dec in all_decisions:
        type_id, _, _ = classify_case(dec["case_nr"])
        type_counts[type_id] = type_counts.get(type_id, 0) + 1
    index["case_type_counts"] = type_counts

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
