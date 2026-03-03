#!/usr/bin/env python3
"""
Fetch draft legislation (eelnõud) from Estonia's EIS (Eelnõude infosüsteem)
and generate JSON-LD ontology files.

Data sources:
  - Public consultation RSS: eelnoud.valitsus.ee/main/mount/rss/home/publicConsult.rss
  - Review/coordination RSS: eelnoud.valitsus.ee/main/mount/rss/home/review.rss
  - Submission RSS: eelnoud.valitsus.ee/main/mount/rss/home/submission.rss

Generates:
  - krr_outputs/eelnoud/eelnou_*.json  (individual draft files)
  - krr_outputs/eelnoud/EELNOUD_INDEX.json  (registry of all drafts)
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
EELNOUD_DIR = KRR_DIR / "eelnoud"
EELNOUD_DIR.mkdir(parents=True, exist_ok=True)

NS = "https://data.riik.ee/ontology/estleg#"

# EIS RSS feed URLs
RSS_FEEDS = {
    "publicConsultation": {
        "url": "https://eelnoud.valitsus.ee/main/mount/rss/home/publicConsult.rss",
        "phase": "PublicConsultation",
        "label_et": "Avalik konsultatsioon",
        "label_en": "Public Consultation",
    },
    "review": {
        "url": "https://eelnoud.valitsus.ee/main/mount/rss/home/review.rss",
        "phase": "Review",
        "label_et": "Kooskõlastamine",
        "label_en": "Inter-ministerial Review",
    },
    "submission": {
        "url": "https://eelnoud.valitsus.ee/main/mount/rss/home/submission.rss",
        "phase": "Submission",
        "label_et": "Esitatud Vabariigi Valitsusele",
        "label_en": "Submitted to Government",
    },
}

# Ministry code mapping
MINISTRY_CODES = {
    "JDM": "Justiitsministeerium",
    "HTM": "Haridus- ja Teadusministeerium",
    "SIM": "Siseministeerium",
    "VÄM": "Välisministeerium",
    "REM": "Regionaalminister",
    "RAM": "Rahandusministeerium",
    "SOM": "Sotsiaalministeerium",
    "MKM": "Majandus- ja Kommunikatsiooniministeerium",
    "KLIM": "Kliimaministeerium",
    "KAM": "Kaitseministeerium",
    "KUM": "Kultuuriministeerium",
    "RK": "Riigikantselei",
    "RIIGIKOGU": "Riigikogu",
}

# JSON-LD context (same as existing ontology files + new draft properties)
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


def sanitize_id(value: str) -> str:
    """Create a safe ID from a string."""
    s = re.sub(r"[^0-9A-Za-z_]", "", value.replace(" ", "_").replace("-", "_"))
    return s[:80] or "Unknown"


def parse_eis_number(title: str) -> tuple[str, str, str]:
    """
    Parse EIS number and date from RSS title.
    Format: "Title text - MINISTRY/YY-NNNN (DD.MM.YYYY)"
    Returns: (eis_number, ministry_code, date_str)
    """
    match = re.search(r"-\s*([A-ZÄÖÜÕa-z]+/\d{2}-\d{4})\s*\((\d{2}\.\d{2}\.\d{4})\)\s*$", title)
    if match:
        eis_number = match.group(1)
        date_str = match.group(2)
        ministry_code = eis_number.split("/")[0]
        return eis_number, ministry_code, date_str
    return "", "", ""


def parse_draft_title(title: str) -> str:
    """Extract the actual title without EIS number and date suffix."""
    cleaned = re.sub(r"\s*-\s*[A-ZÄÖÜÕa-z]+/\d{2}-\d{4}\s*\(\d{2}\.\d{2}\.\d{4}\)\s*$", "", title)
    return cleaned.strip()


def extract_uuid(link: str) -> str:
    """Extract UUID from EIS link."""
    match = re.search(r"/docList/([0-9a-f-]{36})", link)
    return match.group(1) if match else ""


def classify_draft_type(title: str) -> tuple[str, str]:
    """
    Classify the draft type from its title.
    Returns: (type_id, type_label)
    """
    title_lower = title.lower()

    if "seaduse eelnõu" in title_lower or "seadus" in title_lower:
        if "muutmi" in title_lower:
            return "AmendmentBill", "Seaduse muutmise eelnõu"
        return "Bill", "Seaduseelnõu"
    elif "määrus" in title_lower:
        if "vabariigi valitsuse" in title_lower:
            return "GovernmentRegulation", "VV määruse eelnõu"
        elif "ministri" in title_lower:
            return "MinisterialRegulation", "Ministri määruse eelnõu"
        return "Regulation", "Määruse eelnõu"
    elif "korraldus" in title_lower:
        return "GovernmentOrder", "Korralduse eelnõu"
    elif "seisukoht" in title_lower or "euroopa liidu" in title_lower:
        return "EUPosition", "EL seisukoha eelnõu"
    elif "ülevaade" in title_lower:
        return "Report", "Ülevaade"
    elif "kodakondsus" in title_lower:
        return "CitizenshipDecision", "Kodakondsuse otsus"
    elif "kavatsus" in title_lower or "väljatöötamis" in title_lower:
        return "DraftIntent", "Väljatöötamiskavatsus"
    elif "tegevuskava" in title_lower or "strateegia" in title_lower:
        return "ActionPlan", "Tegevuskava"
    else:
        return "Other", "Muu eelnõu"


def detect_affected_laws(title: str) -> list[str]:
    """
    Try to detect which existing laws this draft would amend.
    Returns list of law names mentioned in the title.
    """
    affected = []
    # Common patterns: "X seaduse muutmine", "X seadustiku muutmine"
    patterns = [
        r"(\w+(?:\s+\w+)*?\s+seaduse)\s+(?:muutmi|täiendami)",
        r"(\w+(?:\s+\w+)*?\s+seadustiku)\s+(?:muutmi|täiendami)",
        r"(\w+(?:\s+\w+)*?\s+seadus)\b",
        r"(\w+(?:\s+\w+)*?\s+seadustik)\b",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, title, re.IGNORECASE)
        for m in matches:
            cleaned = m.strip()
            # Skip the draft itself references
            if "eelnõu" not in cleaned.lower() and len(cleaned) > 5:
                affected.append(cleaned)
    return list(dict.fromkeys(affected))  # deduplicate preserving order


def fetch_rss(url: str) -> list[dict]:
    """Fetch and parse an RSS feed, returning list of items."""
    print(f"  Fetching {url}...")
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        resp.encoding = "utf-8"
    except Exception as e:
        print(f"  ERROR: {e}")
        return []

    root = ET.fromstring(resp.text)
    items = []

    for item in root.iter("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        pub_date_el = item.find("pubDate")

        if title_el is None or title_el.text is None:
            continue

        raw_title = title_el.text.strip()
        # Normalize whitespace (some titles have embedded newlines)
        raw_title = re.sub(r"\s+", " ", raw_title)

        items.append({
            "raw_title": raw_title,
            "title": parse_draft_title(raw_title),
            "link": link_el.text.strip() if link_el is not None and link_el.text else "",
            "pub_date": pub_date_el.text.strip() if pub_date_el is not None and pub_date_el.text else "",
        })

    print(f"  Found {len(items)} items")
    return items


def generate_schema_nodes() -> list[dict]:
    """Generate the ontology schema nodes for DraftLegislation."""
    return [
        # DraftLegislation class
        {
            "@id": "estleg:DraftLegislation",
            "@type": ["owl:Class"],
            "rdfs:label": "Eelnõu (Draft Legislation)",
            "rdfs:comment": "Õigusakt, mis ei ole veel jõustunud, kuid on seadusandlikus menetluses.",
            "dc:description": "A legislative draft that has not yet been enacted into law but is in the legislative process.",
        },
        # LegislativePhase class
        {
            "@id": "estleg:LegislativePhase",
            "@type": ["owl:Class"],
            "rdfs:label": "Seadusandlik etapp (Legislative Phase)",
            "rdfs:comment": "Eelnõu menetlusetapp EIS süsteemis.",
        },
        # DraftType class
        {
            "@id": "estleg:DraftType",
            "@type": ["owl:Class"],
            "rdfs:label": "Eelnõu liik (Draft Type)",
            "rdfs:comment": "Eelnõu tüüp: seaduseelnõu, määruse eelnõu, korralduse eelnõu jne.",
        },
        # Phase individuals
        {
            "@id": "estleg:Phase_PublicConsultation",
            "@type": ["owl:NamedIndividual", "estleg:LegislativePhase"],
            "rdfs:label": "Avalik konsultatsioon",
            "skos:prefLabel": "Public Consultation",
            "rdfs:comment": "Eelnõu on avalikul konsultatsioonil – üldsus saab arvamust avaldada.",
            "estleg:phaseOrder": {"@value": "1", "@type": "xsd:integer"},
        },
        {
            "@id": "estleg:Phase_Review",
            "@type": ["owl:NamedIndividual", "estleg:LegislativePhase"],
            "rdfs:label": "Kooskõlastamine",
            "skos:prefLabel": "Inter-ministerial Review",
            "rdfs:comment": "Eelnõu on ministeeriumidevahelisel kooskõlastamisel.",
            "estleg:phaseOrder": {"@value": "2", "@type": "xsd:integer"},
        },
        {
            "@id": "estleg:Phase_Submission",
            "@type": ["owl:NamedIndividual", "estleg:LegislativePhase"],
            "rdfs:label": "Esitatud Vabariigi Valitsusele",
            "skos:prefLabel": "Submitted to Government",
            "rdfs:comment": "Eelnõu on esitatud Vabariigi Valitsusele otsustamiseks.",
            "estleg:phaseOrder": {"@value": "3", "@type": "xsd:integer"},
        },
        # Draft type individuals
        {
            "@id": "estleg:DraftType_Bill",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": "Seaduseelnõu",
            "skos:prefLabel": "Bill",
        },
        {
            "@id": "estleg:DraftType_AmendmentBill",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": "Seaduse muutmise eelnõu",
            "skos:prefLabel": "Amendment Bill",
        },
        {
            "@id": "estleg:DraftType_GovernmentRegulation",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": "VV määruse eelnõu",
            "skos:prefLabel": "Government Regulation Draft",
        },
        {
            "@id": "estleg:DraftType_MinisterialRegulation",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": "Ministri määruse eelnõu",
            "skos:prefLabel": "Ministerial Regulation Draft",
        },
        {
            "@id": "estleg:DraftType_GovernmentOrder",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": "Korralduse eelnõu",
            "skos:prefLabel": "Government Order Draft",
        },
        {
            "@id": "estleg:DraftType_EUPosition",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": "EL seisukoha eelnõu",
            "skos:prefLabel": "EU Position Draft",
        },
        {
            "@id": "estleg:DraftType_DraftIntent",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": "Väljatöötamiskavatsus",
            "skos:prefLabel": "Draft Intent / Pre-draft",
        },
        {
            "@id": "estleg:DraftType_Regulation",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": "Määruse eelnõu",
            "skos:prefLabel": "Regulation Draft",
        },
        {
            "@id": "estleg:DraftType_ActionPlan",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": "Tegevuskava",
            "skos:prefLabel": "Action Plan",
        },
        {
            "@id": "estleg:DraftType_Report",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": "Ülevaade",
            "skos:prefLabel": "Report",
        },
        {
            "@id": "estleg:DraftType_CitizenshipDecision",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": "Kodakondsuse otsus",
            "skos:prefLabel": "Citizenship Decision",
        },
        {
            "@id": "estleg:DraftType_Other",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": "Muu eelnõu",
            "skos:prefLabel": "Other Draft",
        },
        # Object properties
        {
            "@id": "estleg:legislativePhase",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": "seadusandlik etapp",
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "estleg:LegislativePhase"},
            "rdfs:comment": "The current legislative phase of the draft.",
        },
        {
            "@id": "estleg:draftType",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": "eelnõu liik",
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "estleg:DraftType"},
            "rdfs:comment": "The type/category of the draft.",
        },
        {
            "@id": "estleg:amendsLaw",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": "muudab seadust",
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "estleg:LegalProvision"},
            "rdfs:comment": "Links a draft to the existing law it proposes to amend.",
        },
        # Datatype properties
        {
            "@id": "estleg:eisNumber",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "EIS number",
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "xsd:string"},
            "rdfs:comment": "The EIS (Eelnõude infosüsteem) reference number.",
        },
        {
            "@id": "estleg:eisLink",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "EIS link",
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "xsd:anyURI"},
            "rdfs:comment": "Direct link to the draft in EIS.",
        },
        {
            "@id": "estleg:initiator",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "algataja",
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "xsd:string"},
            "rdfs:comment": "The ministry or institution that initiated the draft.",
        },
        {
            "@id": "estleg:publicationDate",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "avaldamiskuupäev",
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "xsd:date"},
            "rdfs:comment": "Date the draft was published/registered in EIS.",
        },
        {
            "@id": "estleg:affectedLawName",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "mõjutatud seadus",
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "xsd:string"},
            "rdfs:comment": "Name of existing law this draft proposes to amend.",
        },
    ]


def generate_draft_node(
    item: dict,
    phase_id: str,
    eis_number: str,
    ministry_code: str,
    date_str: str,
) -> dict:
    """Generate a JSON-LD node for a single draft."""
    uuid = extract_uuid(item["link"])
    draft_type_id, draft_type_label = classify_draft_type(item["title"])
    affected_laws = detect_affected_laws(item["title"])

    # Create stable ID from EIS number or UUID
    if eis_number:
        safe_id = sanitize_id(eis_number)
    elif uuid:
        safe_id = uuid.replace("-", "")[:16]
    else:
        safe_id = sanitize_id(item["title"][:40])

    node: dict = {
        "@id": f"estleg:Draft_{safe_id}",
        "@type": ["owl:NamedIndividual", "estleg:DraftLegislation"],
        "rdfs:label": item["title"],
        "estleg:legislativePhase": {"@id": f"estleg:Phase_{phase_id}"},
        "estleg:draftType": {"@id": f"estleg:DraftType_{draft_type_id}"},
    }

    if eis_number:
        node["estleg:eisNumber"] = eis_number

    if item["link"]:
        node["estleg:eisLink"] = {"@value": item["link"], "@type": "xsd:anyURI"}

    ministry_name = MINISTRY_CODES.get(ministry_code, ministry_code)
    if ministry_name:
        node["estleg:initiator"] = ministry_name

    if date_str:
        try:
            parsed = datetime.strptime(date_str, "%d.%m.%Y")
            node["estleg:publicationDate"] = {
                "@value": parsed.strftime("%Y-%m-%d"),
                "@type": "xsd:date",
            }
        except ValueError:
            pass

    if affected_laws:
        if len(affected_laws) == 1:
            node["estleg:affectedLawName"] = affected_laws[0]
        else:
            node["estleg:affectedLawName"] = affected_laws

    return node


def save_json(filepath: Path, doc: dict):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    print("=" * 60)
    print("Fetching draft legislation from EIS")
    print("=" * 60)

    all_drafts: list[dict] = []
    seen_ids: set[str] = set()

    # Fetch all RSS feeds
    for feed_key, feed_info in RSS_FEEDS.items():
        print(f"\n--- {feed_info['label_et']} ({feed_info['label_en']}) ---")
        items = fetch_rss(feed_info["url"])

        for item in items:
            eis_number, ministry_code, date_str = parse_eis_number(item["raw_title"])
            uuid = extract_uuid(item["link"])

            # Deduplicate by EIS number or UUID
            dedup_key = eis_number or uuid or item["title"][:60]
            if dedup_key in seen_ids:
                continue
            seen_ids.add(dedup_key)

            draft_node = generate_draft_node(
                item,
                phase_id=feed_info["phase"],
                eis_number=eis_number,
                ministry_code=ministry_code,
                date_str=date_str,
            )

            all_drafts.append({
                "node": draft_node,
                "feed": feed_key,
                "eis_number": eis_number,
                "title": item["title"],
                "link": item["link"],
                "phase": feed_info["phase"],
            })

    print(f"\n--- Total unique drafts: {len(all_drafts)} ---")

    # Generate schema file
    print("\n--- Generating schema file ---")
    schema_doc = {
        "@context": CONTEXT,
        "@graph": generate_schema_nodes(),
    }
    schema_path = EELNOUD_DIR / "eelnoud_schema.json"
    save_json(schema_path, schema_doc)
    print(f"  Saved: {schema_path.name} ({len(schema_doc['@graph'])} nodes)")

    # Generate individual draft files grouped by phase
    for phase_key, feed_info in RSS_FEEDS.items():
        phase_drafts = [d for d in all_drafts if d["feed"] == phase_key]
        if not phase_drafts:
            continue

        phase_id = feed_info["phase"]
        print(f"\n--- Generating {phase_id} file ({len(phase_drafts)} drafts) ---")

        graph: list[dict] = [
            {
                "@id": f"estleg:Eelnoud_{phase_id}_Map_2026",
                "@type": ["owl:Ontology"],
                "rdfs:label": f"EIS eelnõud – {feed_info['label_et']}",
                "dc:description": f"Eelnõud, mis on hetkel etapis: {feed_info['label_et']}",
                "dc:source": "Eelnõude infosüsteem (EIS) – eelnoud.valitsus.ee",
            },
        ]

        for d in phase_drafts:
            graph.append(d["node"])

        doc = {"@context": CONTEXT, "@graph": graph}
        out_path = EELNOUD_DIR / f"eelnoud_{phase_id.lower()}_peep.json"
        save_json(out_path, doc)
        print(f"  Saved: {out_path.name} ({len(graph)} nodes)")

    # Generate combined file with all drafts
    print("\n--- Generating combined drafts file ---")
    combined_graph: list[dict] = [
        {
            "@id": "estleg:Eelnoud_Combined_Map_2026",
            "@type": ["owl:Ontology"],
            "rdfs:label": "EIS eelnõud – kõik etapid (Combined)",
            "dc:description": "Kõik EIS eelnõud kõigist menetlusetappidest.",
            "dc:source": "Eelnõude infosüsteem (EIS) – eelnoud.valitsus.ee",
        },
    ]
    combined_graph.extend(generate_schema_nodes())
    for d in all_drafts:
        combined_graph.append(d["node"])

    combined_doc = {"@context": CONTEXT, "@graph": combined_graph}
    combined_path = EELNOUD_DIR / "eelnoud_combined.jsonld"
    save_json(combined_path, combined_doc)
    print(f"  Saved: {combined_path.name} ({len(combined_graph)} nodes)")

    # Generate index
    print("\n--- Generating drafts index ---")
    index = {
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "total_drafts": len(all_drafts),
        "source": "https://eelnoud.valitsus.ee",
        "phases": {},
        "drafts": [],
    }

    for phase_key, feed_info in RSS_FEEDS.items():
        phase_drafts = [d for d in all_drafts if d["feed"] == phase_key]
        index["phases"][feed_info["phase"]] = {
            "label_et": feed_info["label_et"],
            "label_en": feed_info["label_en"],
            "count": len(phase_drafts),
            "file": f"eelnoud_{feed_info['phase'].lower()}_peep.json",
        }

    for d in all_drafts:
        index["drafts"].append({
            "title": d["title"],
            "eis_number": d["eis_number"],
            "phase": d["phase"],
            "link": d["link"],
        })

    index_path = EELNOUD_DIR / "EELNOUD_INDEX.json"
    save_json(index_path, index)
    print(f"  Saved: {index_path.name}")

    # Summary
    print("\n" + "=" * 60)
    print(f"Done! Generated {len(all_drafts)} draft legislation entries.")
    print(f"Files saved to: {EELNOUD_DIR.relative_to(REPO_ROOT)}")
    print()
    for phase_key, feed_info in RSS_FEEDS.items():
        phase_count = sum(1 for d in all_drafts if d["feed"] == phase_key)
        print(f"  {feed_info['label_et']}: {phase_count} drafts")
    print("=" * 60)


if __name__ == "__main__":
    main()
