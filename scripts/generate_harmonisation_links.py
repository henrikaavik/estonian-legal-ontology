#!/usr/bin/env python3
"""
Link Estonian laws to parallel implementations in other EU member states.

For each transposition mapping (Estonian law → EU directive), queries EUR-Lex
for how Latvia, Lithuania, Finland, and Sweden transposed the same directive.
Creates harmonisation links showing which countries implemented the same EU law.

Requires: krr_outputs/transposition_mapping.json (from generate_transposition_mapping.py)

Generates:
  - krr_outputs/harmonisation/harmonisation_report.json    (full report)
  - krr_outputs/harmonisation/harmonisation_schema.json    (OWL property definitions)
  - krr_outputs/harmonisation/harmonisation_by_directive/   (per-directive files)
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
HARMONISATION_DIR = KRR_DIR / "harmonisation"
BY_DIRECTIVE_DIR = HARMONISATION_DIR / "harmonisation_by_directive"

NS = "https://data.riik.ee/ontology/estleg#"

SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
RATE_DELAY = 1.5  # seconds between SPARQL requests

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

# Target countries: Baltic/Nordic neighbors
TARGET_COUNTRIES = {
    "LVA": {"label_en": "Latvia", "label_et": "Läti"},
    "LTU": {"label_en": "Lithuania", "label_et": "Leedu"},
    "FIN": {"label_en": "Finland", "label_et": "Soome"},
    "SWE": {"label_en": "Sweden", "label_et": "Rootsi"},
}

# Country filter for SPARQL
COUNTRY_FILTER = ", ".join(f'"{c}"' for c in TARGET_COUNTRIES)


def save_json(filepath: Path, doc: dict | list):
    """Write a JSON document to disk with UTF-8 encoding."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_json(filepath: Path) -> dict:
    """Load a JSON file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def sanitize_celex(celex: str) -> str:
    """Create a safe ID from a CELEX number."""
    return re.sub(r"[^0-9A-Za-z]", "", celex)[:40] or "Unknown"


def sparql_query(query: str) -> list[dict]:
    """Execute a SPARQL query and return bindings."""
    resp = requests.get(
        SPARQL_ENDPOINT,
        params={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", {}).get("bindings", [])


def fetch_other_transpositions(celex_dir: str) -> list[dict]:
    """
    For a given directive CELEX, fetch transposition measures from target countries.
    Returns list of dicts with country, celex_nat, title.
    """
    query = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT DISTINCT ?directive ?celex_dir ?national ?celex_nat ?country WHERE {{
  ?directive cdm:resource_legal_id_celex "{celex_dir}" .
  ?national cdm:resource_legal_measures_transposition_for ?directive .
  ?national cdm:resource_legal_id_celex ?celex_nat .
  FILTER(STRSTARTS(?celex_nat, "7"))
  ?national cdm:work_created_by_agent ?country_uri .
  BIND(STRAFTER(STR(?country_uri), "authority/country/") AS ?country)
  FILTER(?country IN ({COUNTRY_FILTER}))
}}
"""
    try:
        bindings = sparql_query(query)
    except Exception as e:
        print(f"    ERROR querying for {celex_dir}: {e}")
        return []

    results: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for b in bindings:
        country = b.get("country", {}).get("value", "")
        celex_nat = b.get("celex_nat", {}).get("value", "")

        key = (country, celex_nat)
        if key in seen:
            continue
        seen.add(key)

        results.append({
            "country_code": country,
            "country_en": TARGET_COUNTRIES.get(country, {}).get("label_en", country),
            "country_et": TARGET_COUNTRIES.get(country, {}).get("label_et", country),
            "celex_nat": celex_nat,
        })

    return results


def generate_schema() -> dict:
    """Generate OWL schema definitions for harmonisation properties."""
    schema_nodes: list[dict] = [
        # HarmonisationLink class
        {
            "@id": "estleg:HarmonisationLink",
            "@type": ["owl:Class"],
            "rdfs:label": "Harmoniseerimisseos (Harmonisation link)",
            "rdfs:comment": (
                "A link between an Estonian law and national laws of other EU member states "
                "that transpose the same EU directive."
            ),
        },
        # ObjectProperty: harmonisedWith
        {
            "@id": "estleg:harmonisedWith",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": "harmoniseeritud (harmonised with)",
            "rdfs:comment": (
                "Links an Estonian law to a harmonisation record showing "
                "parallel implementations of the same EU directive in other member states."
            ),
            "rdfs:domain": {"@id": "estleg:LegalProvision"},
            "rdfs:range": {"@id": "estleg:HarmonisationLink"},
        },
        # ObjectProperty: sharedDirective
        {
            "@id": "estleg:sharedDirective",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": "ühine direktiiv (shared directive)",
            "rdfs:comment": "The EU directive that multiple member states have transposed.",
            "rdfs:domain": {"@id": "estleg:HarmonisationLink"},
            "rdfs:range": {"@id": "estleg:EULegislation"},
        },
        # DatatypeProperty: memberStateCode
        {
            "@id": "estleg:memberStateCode",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "liikmesriigi kood (member state code)",
            "rdfs:comment": "ISO 3166-1 alpha-3 country code of the EU member state.",
            "rdfs:domain": {"@id": "estleg:HarmonisationLink"},
            "rdfs:range": {"@id": "xsd:string"},
        },
        # DatatypeProperty: nationalCelex
        {
            "@id": "estleg:nationalCelex",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "riigisisene CELEX (national CELEX number)",
            "rdfs:comment": "CELEX identifier for the national transposition measure.",
            "rdfs:domain": {"@id": "estleg:HarmonisationLink"},
            "rdfs:range": {"@id": "xsd:string"},
        },
    ]

    return {"@context": CONTEXT, "@graph": schema_nodes}


def build_directive_node(
    celex_dir: str,
    directive_iri: str,
    estonian_law_name: str,
    estonian_law_iri: str,
    other_measures: list[dict],
) -> dict:
    """
    Build a JSON-LD document for harmonisation data around one directive.
    """
    safe_celex = sanitize_celex(celex_dir)
    graph: list[dict] = [
        {
            "@id": f"estleg:Harmonisation_{safe_celex}",
            "@type": ["owl:NamedIndividual", "estleg:HarmonisationLink"],
            "rdfs:label": f"Harmonisation: {celex_dir}",
            "rdfs:comment": (
                f"Harmonisation record for directive {celex_dir}, "
                f"transposed by Estonia ({estonian_law_name}) and "
                f"{len(other_measures)} other member state measure(s)."
            ),
            "estleg:sharedDirective": {"@id": directive_iri},
            "estleg:harmonisedWith": {"@id": estonian_law_iri},
        },
    ]

    # Add individual country measure nodes
    by_country: dict[str, list[dict]] = {}
    for m in other_measures:
        code = m["country_code"]
        if code not in by_country:
            by_country[code] = []
        by_country[code].append(m)

    for country_code, measures in sorted(by_country.items()):
        country_info = TARGET_COUNTRIES.get(country_code, {})
        for idx, m in enumerate(measures):
            safe_nat = sanitize_celex(m["celex_nat"])
            node_id = f"estleg:Harm_{safe_celex}_{country_code}_{idx + 1}"

            node: dict = {
                "@id": node_id,
                "@type": ["owl:NamedIndividual"],
                "rdfs:label": f"{country_info.get('label_en', country_code)}: {m['celex_nat']}",
                "estleg:memberStateCode": country_code,
                "estleg:nationalCelex": m["celex_nat"],
                "estleg:sharedDirective": {"@id": directive_iri},
            }
            graph.append(node)

    return {"@context": CONTEXT, "@graph": graph}


def update_law_file_harmonisation(filepath: Path, harmonisation_ids: list[str]) -> bool:
    """
    Add estleg:harmonisedWith references to a law file's ontology node.
    Returns True if the file was modified.
    """
    try:
        data = load_json(filepath)
    except Exception as e:
        print(f"    ERROR loading {filepath.name}: {e}")
        return False

    graph = data.get("@graph", [])

    # Find the ontology metadata node
    target_node = None
    for node in graph:
        types = node.get("@type", [])
        if "owl:Ontology" in types:
            target_node = node
            break

    if target_node is None and graph:
        target_node = graph[0]

    if target_node is None:
        return False

    # Build harmonisation references
    harm_refs = [{"@id": hid} for hid in harmonisation_ids]

    existing = target_node.get("estleg:harmonisedWith", [])
    if isinstance(existing, dict):
        existing = [existing]

    existing_ids = {ref.get("@id") for ref in existing if isinstance(ref, dict)}
    new_refs = [ref for ref in harm_refs if ref["@id"] not in existing_ids]

    if not new_refs:
        return False

    all_refs = existing + new_refs
    target_node["estleg:harmonisedWith"] = all_refs

    save_json(filepath, data)
    return True


def main():
    print("=" * 60)
    print("Generate harmonisation links: Estonian laws ↔ neighboring EU states")
    countries = ', '.join(f"{v['label_en']} ({k})" for k, v in TARGET_COUNTRIES.items())
    print(f"Target countries: {countries}")
    print(f"Endpoint: {SPARQL_ENDPOINT}")
    print("=" * 60)

    # --- Step 0: Clear existing harmonisation data ---
    print("\n--- Clearing existing harmonisation data ---")
    cleared = 0
    for peep_file in sorted(KRR_DIR.glob("*_peep.json")):
        try:
            with open(peep_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
            modified = False
            for node in doc.get("@graph", []):
                if "estleg:harmonisedWith" in node:
                    del node["estleg:harmonisedWith"]
                    modified = True
            if modified:
                save_json(peep_file, doc)
                cleared += 1
        except Exception:
            continue
    print(f"  Cleared harmonisedWith from {cleared} files")

    # Clear old per-directive harmonisation files
    if BY_DIRECTIVE_DIR.exists():
        old_files = list(BY_DIRECTIVE_DIR.glob("harm_*.json"))
        if old_files:
            shutil.rmtree(BY_DIRECTIVE_DIR)
            BY_DIRECTIVE_DIR.mkdir(parents=True, exist_ok=True)
            print(f"  Cleared {len(old_files)} old files from {BY_DIRECTIVE_DIR.name}/")
        else:
            print(f"  {BY_DIRECTIVE_DIR.name}/ already empty")
    else:
        print(f"  {BY_DIRECTIVE_DIR.name}/ does not exist yet")

    # --- Step 1: Load transposition mapping ---
    print("\n--- Loading transposition mapping ---")
    mapping_path = KRR_DIR / "transposition_mapping.json"
    if not mapping_path.exists():
        print(f"ERROR: {mapping_path} not found.")
        print("Run generate_transposition_mapping.py first.")
        sys.exit(1)

    mapping_data = load_json(mapping_path)
    mappings = mapping_data.get("mappings", [])
    print(f"  Transposition mappings loaded: {len(mappings)}")

    if not mappings:
        print("  No transposition mappings found. Nothing to harmonise.")
        return

    # Deduplicate by directive CELEX (we only need to query each directive once)
    directives_to_process: dict[str, dict] = {}
    for m in mappings:
        celex = m["directive_celex"]
        if celex not in directives_to_process:
            directives_to_process[celex] = {
                "directive_iri": m["directive_iri"],
                "estonian_laws": [],
            }
        law_entry = {
            "name": m["matched_law_name"],
            "source_act": m.get("matched_source_act", ""),
            "files": m.get("law_files", []),
        }
        # Avoid duplicate law entries per directive
        existing_names = {l["name"] for l in directives_to_process[celex]["estonian_laws"]}
        if law_entry["name"] not in existing_names:
            directives_to_process[celex]["estonian_laws"].append(law_entry)

    print(f"  Unique directives to query: {len(directives_to_process)}")

    # --- Step 2: Create output directories ---
    HARMONISATION_DIR.mkdir(parents=True, exist_ok=True)
    BY_DIRECTIVE_DIR.mkdir(parents=True, exist_ok=True)

    # --- Step 3: Generate schema ---
    print("\n--- Generating harmonisation schema ---")
    schema_doc = generate_schema()
    schema_path = HARMONISATION_DIR / "harmonisation_schema.json"
    save_json(schema_path, schema_doc)
    print(f"  Saved: {schema_path.name}")

    # --- Step 4: Query EUR-Lex for parallel transpositions ---
    print("\n--- Querying EUR-Lex for parallel transpositions ---")

    harmonisation_data: list[dict] = []
    total_parallel_measures = 0
    directives_with_parallels = 0
    country_totals: dict[str, int] = {c: 0 for c in TARGET_COUNTRIES}
    # Track which law files get which harmonisation node IDs
    law_file_harmonisation: dict[str, list[str]] = {}
    errors = 0

    directive_list = sorted(directives_to_process.keys())

    for i, celex_dir in enumerate(directive_list):
        info = directives_to_process[celex_dir]
        directive_iri = info["directive_iri"]
        estonian_laws = info["estonian_laws"]

        if (i + 1) % 10 == 0 or i == 0:
            print(f"  Processing directive {i + 1}/{len(directive_list)}: {celex_dir}")

        # Query for other countries' transpositions
        try:
            other_measures = fetch_other_transpositions(celex_dir)
        except Exception as e:
            print(f"    ERROR: {e}")
            errors += 1
            time.sleep(RATE_DELAY)
            continue

        if other_measures:
            directives_with_parallels += 1
            total_parallel_measures += len(other_measures)

            # Count by country
            for m in other_measures:
                cc = m["country_code"]
                if cc in country_totals:
                    country_totals[cc] += 1

            # Generate per-directive harmonisation file
            safe_celex = sanitize_celex(celex_dir)
            primary_law = estonian_laws[0] if estonian_laws else {"name": "unknown", "files": []}
            law_iri = f"estleg:LegalProvision_{primary_law['name']}"

            directive_doc = build_directive_node(
                celex_dir=celex_dir,
                directive_iri=directive_iri,
                estonian_law_name=primary_law.get("source_act", primary_law["name"]),
                estonian_law_iri=law_iri,
                other_measures=other_measures,
            )

            directive_file = BY_DIRECTIVE_DIR / f"harm_{safe_celex}.json"
            save_json(directive_file, directive_doc)

            # Build by-country breakdown for the report
            by_country: dict[str, list[str]] = {}
            for m in other_measures:
                cc = m["country_code"]
                if cc not in by_country:
                    by_country[cc] = []
                by_country[cc].append(m["celex_nat"])

            harmonisation_id = f"estleg:Harmonisation_{safe_celex}"

            harmonisation_entry = {
                "directive_celex": celex_dir,
                "directive_iri": directive_iri,
                "estonian_laws": [
                    {"name": law["name"], "source_act": law.get("source_act", "")}
                    for law in estonian_laws
                ],
                "parallel_measures": len(other_measures),
                "countries": by_country,
                "harmonisation_file": f"harmonisation_by_directive/harm_{safe_celex}.json",
                "harmonisation_id": harmonisation_id,
            }
            harmonisation_data.append(harmonisation_entry)

            # Track which law files need harmonisation links
            for law in estonian_laws:
                for law_file in law.get("files", []):
                    filepath_str = str(KRR_DIR / law_file)
                    if filepath_str not in law_file_harmonisation:
                        law_file_harmonisation[filepath_str] = []
                    if harmonisation_id not in law_file_harmonisation[filepath_str]:
                        law_file_harmonisation[filepath_str].append(harmonisation_id)

        # Rate limiting
        time.sleep(RATE_DELAY)

    print(f"\n  Directives queried: {len(directive_list)}")
    print(f"  Directives with parallel measures: {directives_with_parallels}")
    print(f"  Total parallel measures found: {total_parallel_measures}")
    print(f"  Query errors: {errors}")

    # --- Step 5: Update Estonian law files with harmonisation links ---
    print("\n--- Updating Estonian law files with harmonisation links ---")
    files_updated = 0
    for filepath_str, harm_ids in law_file_harmonisation.items():
        filepath = Path(filepath_str)
        if not filepath.exists():
            continue
        if update_law_file_harmonisation(filepath, harm_ids):
            files_updated += 1

    print(f"  Law files updated: {files_updated}")

    # --- Step 6: Generate report ---
    print("\n--- Generating harmonisation report ---")

    report = {
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "source": SPARQL_ENDPOINT,
        "estonian_country_code": "EST",
        "target_countries": {
            code: info for code, info in TARGET_COUNTRIES.items()
        },
        "total_directives_queried": len(directive_list),
        "directives_with_parallels": directives_with_parallels,
        "total_parallel_measures": total_parallel_measures,
        "query_errors": errors,
        "law_files_updated": files_updated,
        "measures_by_country": {
            code: {
                "label_en": TARGET_COUNTRIES[code]["label_en"],
                "label_et": TARGET_COUNTRIES[code]["label_et"],
                "total_measures": count,
            }
            for code, count in sorted(country_totals.items(), key=lambda x: -x[1])
        },
        "harmonisation_entries": sorted(
            harmonisation_data, key=lambda h: h["directive_celex"]
        ),
    }

    report_path = HARMONISATION_DIR / "harmonisation_report.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")

    # --- Summary ---
    print("\n" + "=" * 60)
    print("Harmonisation linking complete!")
    print(f"  Directives queried:             {len(directive_list)}")
    print(f"  Directives with parallels:      {directives_with_parallels}")
    print(f"  Total parallel measures:        {total_parallel_measures}")
    print(f"  Query errors:                   {errors}")
    print(f"  Law files updated:              {files_updated}")
    print(f"\n  Measures by country:")
    for code, count in sorted(country_totals.items(), key=lambda x: -x[1]):
        label = TARGET_COUNTRIES[code]["label_en"]
        print(f"    {label:20s} ({code}): {count:4d}")
    print(f"\nOutputs:")
    print(f"  {report_path.relative_to(REPO_ROOT)}")
    print(f"  {schema_path.relative_to(REPO_ROOT)}")
    print(f"  {BY_DIRECTIVE_DIR.relative_to(REPO_ROOT)}/ ({len(harmonisation_data)} files)")
    print("=" * 60)


if __name__ == "__main__":
    main()
