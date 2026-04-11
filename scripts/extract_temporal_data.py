#!/usr/bin/env python3
"""
Extract temporal validity data from cached Riigi Teataja XML files
and enrich existing law JSON-LD files with temporal properties.

This script:
1. Scans data/riigiteataja/*.xml for temporal metadata
2. Extracts entry-into-force dates, repeal dates, amendment dates, etc.
3. Adds estleg:entryIntoForce, estleg:repealDate, etc. to law JSON-LD files
4. Falls back to INDEX.json metadata when XML fields are missing
5. Generates temporal_data_report.json with statistics
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
DATA_DIR = REPO_ROOT / "data" / "riigiteataja"

NS = "https://data.riik.ee/ontology/estleg#"

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


def ln(tag: str) -> str:
    """Strip XML namespace prefix."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def ct(el: ET.Element, name: str) -> str | None:
    """Get child element text by local tag name."""
    for c in el:
        if ln(c.tag) == name and c.text:
            return c.text.strip()
    return None


def find_element_recursive(root: ET.Element, name: str) -> ET.Element | None:
    """Find first element with given local tag name anywhere in tree."""
    for el in root.iter():
        if ln(el.tag) == name:
            return el
    return None


def find_all_elements(root: ET.Element, name: str) -> list[ET.Element]:
    """Find all elements with given local tag name."""
    return [el for el in root.iter() if ln(el.tag) == name]


def parse_date(value: str) -> str | None:
    """
    Parse various date formats from Riigi Teataja XML into ISO date string.
    Handles: YYYY-MM-DD, YYYY-MM-DD+TZ, DD.MM.YYYY, etc.
    Also handles malformed dates like "2011+02:00-01-01" where timezone offset
    is embedded in the middle of the date string.
    """
    if not value:
        return None
    value = value.strip()
    # Strip timezone offsets like +02:00 or +03:00 anywhere in the string
    # (handles both trailing offsets and mid-string offsets like "2011+02:00-01-01")
    value = re.sub(r'\+\d{2}:\d{2}', '', value)
    # Also strip trailing negative offsets (e.g. -02:00) but only at end to avoid
    # eating date separators
    value = re.sub(r'-\d{2}:\d{2}$', '', value)
    # Try ISO format first
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def extract_temporal_from_xml(xml_path: Path) -> dict:
    """
    Extract all temporal metadata from a Riigi Teataja XML file.
    Returns dict with parsed date fields.
    """
    result: dict[str, str | None] = {
        "entry_into_force": None,
        "valid_from": None,
        "valid_until": None,
        "invalidation_date": None,
        "last_amendment_date": None,
        "publication_date": None,
        "adoption_date": None,
    }

    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except (ET.ParseError, OSError):
        return result

    # Look in <metaandmed> section
    metaandmed = find_element_recursive(root, "metaandmed")

    # Look in <kehtivus> block within metaandmed
    kehtivus = find_element_recursive(root, "kehtivus")
    if kehtivus is not None:
        # kehtivuseAlgus / kehtivAlates — effective from
        for tag in ("kehtivuseAlgus", "kehtivAlates"):
            val = ct(kehtivus, tag)
            if val:
                result["valid_from"] = parse_date(val)
                break
        # kehtivuseLopp / kehtivKuni — valid until
        for tag in ("kehtivuseLopp", "kehtivKuni"):
            val = ct(kehtivus, tag)
            if val:
                result["valid_until"] = parse_date(val)
                break

    # Look in <vastuvoetud> block for adoption and entry into force
    vastuvoetud = find_element_recursive(root, "vastuvoetud")
    if vastuvoetud is not None:
        # aktikuupaev — adoption date
        val = ct(vastuvoetud, "aktikuupaev")
        if val:
            result["adoption_date"] = parse_date(val)
        # joustumine — entry into force
        val = ct(vastuvoetud, "joustumine")
        if val:
            result["entry_into_force"] = parse_date(val)

    # Direct children of root or metaandmed for other fields
    search_root = metaandmed if metaandmed is not None else root

    # joustumisKuupaev — explicit entry into force date
    for el in search_root.iter():
        tag = ln(el.tag)
        if tag == "joustumisKuupaev" and el.text:
            parsed = parse_date(el.text.strip())
            if parsed:
                result["entry_into_force"] = parsed
                break

    # kehtetuKuupaev — date of invalidation
    for el in search_root.iter():
        tag = ln(el.tag)
        if tag == "kehtetuKuupaev" and el.text:
            parsed = parse_date(el.text.strip())
            if parsed:
                result["invalidation_date"] = parsed
                break

    # avaldamiseKuupaev or avaldamismarge — publication date
    for el in root.iter():
        tag = ln(el.tag)
        if tag == "avaldamiseKuupaev" and el.text:
            parsed = parse_date(el.text.strip())
            if parsed:
                result["publication_date"] = parsed
                break

    # If no explicit publication date, try to get from first avaldamismarge
    if not result["publication_date"]:
        avaldamismarge = find_element_recursive(root, "avaldamismarge")
        if avaldamismarge is not None:
            rt_aasta = ct(avaldamismarge, "RTaasta")
            if rt_aasta:
                try:
                    result["publication_date"] = f"{rt_aasta}-01-01"
                except ValueError:
                    pass

    # Last amendment date: find the latest muutmismarge
    muutmismarked = find_all_elements(root, "muutmismarge")
    latest_amendment: str | None = None
    for mm in muutmismarked:
        aktikp = ct(mm, "aktikuupaev")
        if aktikp:
            parsed = parse_date(aktikp)
            if parsed:
                if latest_amendment is None or parsed > latest_amendment:
                    latest_amendment = parsed
    if latest_amendment:
        result["last_amendment_date"] = latest_amendment

    # muutmisKuupaev — explicit last amendment date field
    for el in root.iter():
        tag = ln(el.tag)
        if tag == "muutmisKuupaev" and el.text:
            parsed = parse_date(el.text.strip())
            if parsed:
                if not result["last_amendment_date"] or parsed > result["last_amendment_date"]:
                    result["last_amendment_date"] = parsed
            break

    # Use valid_from as entry_into_force fallback
    if not result["entry_into_force"] and result["valid_from"]:
        result["entry_into_force"] = result["valid_from"]

    return result


def determine_temporal_status(temporal: dict) -> str:
    """
    Determine the temporal status of a law.
    Returns one of: "inForce", "repealed", "notYetEffective"
    """
    today = date.today().isoformat()

    # If there is an invalidation date or valid_until in the past, it is repealed
    if temporal.get("invalidation_date"):
        if temporal["invalidation_date"] <= today:
            return "repealed"
    if temporal.get("valid_until"):
        if temporal["valid_until"] <= today:
            return "repealed"

    # If entry_into_force is in the future, not yet effective
    if temporal.get("entry_into_force"):
        if temporal["entry_into_force"] > today:
            return "notYetEffective"

    return "inForce"


def make_xsd_date(iso_date: str) -> dict:
    """Create an xsd:date typed value for JSON-LD."""
    return {"@value": iso_date, "@type": "xsd:date"}


def load_index_metadata() -> dict[str, dict]:
    """
    Load INDEX.json for fallback metadata.
    Returns dict keyed by law slug with any available kehtivus data.
    """
    index_path = KRR_DIR / "INDEX.json"
    if not index_path.exists():
        return {}
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    result = {}
    for law in data.get("laws", []):
        name = law.get("name", "")
        if name:
            result[name] = law
    return result


def save_json(filepath: Path, doc: dict):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    print("=" * 70)
    print("Estonian Legal Ontology - Extract Temporal Validity Data")
    print("=" * 70)

    # Step 1: Find all XML files
    print("\n[1/4] Scanning XML files...")
    xml_files = sorted(DATA_DIR.glob("*.xml")) if DATA_DIR.exists() else []
    print(f"  Found {len(xml_files)} XML files in {DATA_DIR.relative_to(REPO_ROOT)}")

    # Step 2: Extract temporal data from each XML
    print("\n[2/4] Extracting temporal metadata from XML...")
    temporal_by_slug: dict[str, dict] = {}
    extracted = 0
    parse_errors = 0

    for xml_path in xml_files:
        slug = xml_path.stem
        try:
            temporal = extract_temporal_from_xml(xml_path)
            # Only count as extracted if we got at least one date
            has_data = any(v is not None for v in temporal.values())
            if has_data:
                extracted += 1
            temporal_by_slug[slug] = temporal
        except Exception as e:
            parse_errors += 1
            print(f"  ERROR parsing {xml_path.name}: {e}")

    print(f"  Extracted temporal data from {extracted} files ({parse_errors} errors)")

    # Step 3: Load INDEX.json for fallback data
    print("\n[3/4] Loading INDEX.json for fallback metadata...")
    index_meta = load_index_metadata()
    print(f"  Loaded metadata for {len(index_meta)} laws from INDEX.json")

    # Step 4: Enrich law JSON-LD files
    print("\n[4/4] Enriching law JSON-LD files with temporal properties...")
    law_files = sorted(KRR_DIR.glob("*_peep.json"))
    # Exclude files in subdirectories
    law_files = [f for f in law_files if f.parent == KRR_DIR]
    print(f"  Found {len(law_files)} law JSON-LD files")

    # --- Clearing pass: remove old temporal data from ontology nodes ---
    TEMPORAL_KEYS_TO_CLEAR = [
        "estleg:entryIntoForce",
        "estleg:repealDate",
        "estleg:lastAmendmentDate",
        "estleg:publicationDate",
        "estleg:temporalStatus",
    ]
    print("  Clearing old temporal data from ontology nodes...")
    for law_file in law_files:
        try:
            with open(law_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        graph = doc.get("@graph", [])
        if not graph:
            continue
        cleared = False
        for node in graph:
            types = node.get("@type", [])
            if "owl:Ontology" in types:
                for key in TEMPORAL_KEYS_TO_CLEAR:
                    if key in node:
                        del node[key]
                        cleared = True
                break
        if cleared:
            save_json(law_file, doc)
    print("  Done clearing.")

    enriched = 0
    skipped = 0
    status_counts = {"inForce": 0, "repealed": 0, "notYetEffective": 0, "unknown": 0}
    report_entries: list[dict] = []

    for law_file in law_files:
        # Derive the slug: remove _peep.json, also handle _osa variants
        stem = law_file.stem.replace("_peep", "")

        # Try to find matching XML slug
        # For multi-part laws like "vos_osa1", the XML is "vos" or full name
        xml_slug = stem
        # Remove osa suffix for lookup
        base_slug = re.sub(r"_osa\d+$", "", stem)

        temporal = temporal_by_slug.get(xml_slug) or temporal_by_slug.get(base_slug)

        # Fallback: try INDEX.json
        if temporal is None or not any(v for v in temporal.values()):
            idx_data = index_meta.get(stem) or index_meta.get(base_slug)
            if idx_data and not temporal:
                temporal = {
                    "entry_into_force": None,
                    "valid_from": None,
                    "valid_until": None,
                    "invalidation_date": None,
                    "last_amendment_date": None,
                    "publication_date": None,
                    "adoption_date": None,
                }

        if temporal is None:
            skipped += 1
            status_counts["unknown"] += 1
            report_entries.append({
                "file": law_file.name,
                "slug": stem,
                "status": "no_xml_data",
                "temporal": {},
            })
            continue

        # Determine temporal status
        status = determine_temporal_status(temporal)
        status_counts[status] += 1

        # Load existing JSON-LD
        try:
            with open(law_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ERROR reading {law_file.name}: {e}")
            skipped += 1
            continue

        # Ensure context has dcterms
        ctx = doc.get("@context", {})
        if isinstance(ctx, dict) and "dcterms" not in ctx:
            ctx["dcterms"] = "http://purl.org/dc/terms/"
            doc["@context"] = ctx

        # Find the ontology node (first node with owl:Ontology type) to add temporal data
        graph = doc.get("@graph", [])
        if not graph:
            skipped += 1
            continue

        ontology_node = None
        for node in graph:
            types = node.get("@type", [])
            if "owl:Ontology" in types:
                ontology_node = node
                break

        if ontology_node is None:
            # Use the first node as fallback
            ontology_node = graph[0]

        # Add temporal properties to the ontology node
        modified = False

        if temporal.get("entry_into_force"):
            ontology_node["estleg:entryIntoForce"] = make_xsd_date(temporal["entry_into_force"])
            modified = True

        if temporal.get("valid_until") and status == "repealed":
            ontology_node["estleg:repealDate"] = make_xsd_date(temporal["valid_until"])
            modified = True
        elif temporal.get("invalidation_date"):
            ontology_node["estleg:repealDate"] = make_xsd_date(temporal["invalidation_date"])
            modified = True

        if temporal.get("last_amendment_date"):
            ontology_node["estleg:lastAmendmentDate"] = make_xsd_date(temporal["last_amendment_date"])
            modified = True

        if temporal.get("publication_date"):
            ontology_node["estleg:publicationDate"] = make_xsd_date(temporal["publication_date"])
            modified = True

        ontology_node["estleg:temporalStatus"] = status
        modified = True

        if modified:
            save_json(law_file, doc)
            enriched += 1

        report_entries.append({
            "file": law_file.name,
            "slug": stem,
            "status": status,
            "temporal": {k: v for k, v in temporal.items() if v is not None},
        })

    # Generate report
    print("\n  Generating temporal_data_report.json...")
    report = {
        "generated": date.today().isoformat(),
        "summary": {
            "total_xml_files": len(xml_files),
            "xml_with_temporal_data": extracted,
            "xml_parse_errors": parse_errors,
            "total_law_files": len(law_files),
            "enriched": enriched,
            "skipped_no_data": skipped,
            "status_counts": status_counts,
        },
        "laws": report_entries,
    }
    report_path = KRR_DIR / "temporal_data_report.json"
    save_json(report_path, report)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  XML files scanned:     {len(xml_files)}")
    print(f"  With temporal data:    {extracted}")
    print(f"  Law files enriched:    {enriched}")
    print(f"  Skipped (no data):     {skipped}")
    print(f"  Status breakdown:")
    for status, count in status_counts.items():
        print(f"    {status}: {count}")
    print(f"\n  Report: {report_path.relative_to(REPO_ROOT)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
