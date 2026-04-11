#!/usr/bin/env python3
"""
Map Estonian laws to the EU directives they transpose, using EUR-Lex SPARQL.

Data source: EUR-Lex SPARQL endpoint — national transposition measures for Estonia.
Matches transposition titles against existing Estonian law ontology entries.

Generates:
  - krr_outputs/transposition_mapping.json           (report of all matches)
  - krr_outputs/transposition_schema.json             (OWL property definitions)
  - Updates existing law JSON-LD files with estleg:transposesDirective
  - Updates EU directive entries with estleg:transposedBy
"""

from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
EURLEX_DIR = KRR_DIR / "eurlex"

NS = "https://data.riik.ee/ontology/estleg#"

SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
PAGE_SIZE = 5000
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


def save_json(filepath: Path, doc: dict):
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


def normalize_text(text: str) -> str:
    """Normalize text for fuzzy matching: lowercase, strip diacritics, collapse whitespace."""
    text = text.lower().strip()
    # Normalize unicode
    text = unicodedata.normalize("NFKD", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text


def extract_law_name(title: str) -> str | None:
    """
    Try to extract an Estonian law name from a transposition measure title.

    Patterns like:
      - "Alkoholiseadus"
      - "Isikuandmete kaitse seadus"
      - Things ending in "seadus", "seadustik", "määrus"
    """
    title_norm = title.strip()

    # Try to find explicit law name patterns
    # Pattern: title IS the law name (short titles)
    if re.search(r"seadus(?:tik)?$", title_norm, re.IGNORECASE):
        return title_norm

    # Pattern: "... seadus ..." — extract up to "seadus/seadustik"
    m = re.search(r"^(.+?\s*seadus(?:tik)?)\b", title_norm, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    return None


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


def fetch_transposition_measures() -> list[dict]:
    """
    Fetch national transposition measures for Estonia from EUR-Lex.
    Returns list of dicts with celex_dir, directive_uri, title_nat, celex_nat.
    """
    all_items: list[dict] = []
    seen: set[tuple[str, str]] = set()
    offset = 0

    while True:
        query = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT DISTINCT ?directive ?celex_dir ?national ?title_nat WHERE {{
  ?directive a cdm:directive .
  ?directive cdm:resource_legal_id_celex ?celex_dir .
  ?national cdm:resource_legal_measures_transposition_for ?directive .
  ?national cdm:resource_legal_id_celex ?celex_nat .
  FILTER(STRSTARTS(?celex_nat, "7"))
  ?exp_nat cdm:expression_belongs_to_work ?national .
  ?exp_nat cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/EST> .
  ?exp_nat cdm:expression_title ?title_nat .
}} LIMIT {PAGE_SIZE} OFFSET {offset}
"""
        print(f"  Fetching transposition measures, offset {offset}...")
        try:
            bindings = sparql_query(query)
        except Exception as e:
            print(f"  ERROR at offset {offset}: {e}")
            break

        if not bindings:
            break

        for b in bindings:
            celex_dir = b.get("celex_dir", {}).get("value", "")
            title_nat = b.get("title_nat", {}).get("value", "")
            directive_uri = b.get("directive", {}).get("value", "")

            key = (celex_dir, title_nat)
            if key in seen:
                continue
            seen.add(key)

            all_items.append({
                "celex_dir": celex_dir,
                "directive_uri": directive_uri,
                "title_nat": title_nat,
            })

        if len(bindings) < PAGE_SIZE:
            break

        offset += PAGE_SIZE
        time.sleep(RATE_DELAY)

    return all_items


def build_law_index(index_data: dict) -> dict[str, dict]:
    """
    Build a lookup from normalized law names to their file info.
    Returns: {normalized_name: {"name": ..., "files": [...], "source_act": ...}}
    """
    law_index: dict[str, dict] = {}

    for law in index_data.get("laws", []):
        name = law.get("name", "")
        files = law.get("files", [])
        if not name or not files:
            continue

        # Load the first file to get the sourceAct name
        first_file = KRR_DIR / files[0]
        source_act = ""
        if first_file.exists():
            try:
                data = load_json(first_file)
                for node in data.get("@graph", []):
                    sa = node.get("estleg:sourceAct", "")
                    if sa:
                        source_act = sa
                        break
            except Exception:
                pass

        # Index by several normalized forms
        if source_act:
            key = normalize_text(source_act)
            law_index[key] = {
                "name": name,
                "files": files,
                "source_act": source_act,
            }

        # Also index by the filename-derived name (underscores → spaces)
        name_spaced = name.replace("_", " ")
        key2 = normalize_text(name_spaced)
        if key2 not in law_index:
            law_index[key2] = {
                "name": name,
                "files": files,
                "source_act": source_act or name_spaced,
            }

    return law_index


def build_directive_index() -> dict[str, str]:
    """
    Build a lookup from CELEX number to estleg IRI for EU directives.
    Returns: {celex: "estleg:EU_XXXXX"}
    """
    directive_index: dict[str, str] = {}
    directives_file = EURLEX_DIR / "eurlex_directives_peep.json"

    if not directives_file.exists():
        print(f"  WARNING: {directives_file} not found, directive linking will be limited")
        return directive_index

    data = load_json(directives_file)
    for node in data.get("@graph", []):
        celex = node.get("estleg:celexNumber", "")
        node_id = node.get("@id", "")
        if celex and node_id:
            directive_index[celex] = node_id

    return directive_index


def match_title_to_law(title: str, law_index: dict[str, dict]) -> dict | None:
    """
    Try to match a transposition measure title to an Estonian law.
    Uses progressively looser matching.
    """
    title_norm = normalize_text(title)

    # Direct full match
    if title_norm in law_index:
        return law_index[title_norm]

    # Extract law name from title and try to match
    law_name = extract_law_name(title)
    if law_name:
        law_name_norm = normalize_text(law_name)
        if law_name_norm in law_index:
            return law_index[law_name_norm]

    # Try substring matching: check if any known law name is contained in the title
    best_match = None
    best_len = 0
    for key, info in law_index.items():
        if len(key) > 5 and key in title_norm and len(key) > best_len:
            best_match = info
            best_len = len(key)

    if best_match and best_len > 10:
        return best_match

    # Try matching against sourceAct names directly
    for key, info in law_index.items():
        source = normalize_text(info.get("source_act", ""))
        if source and len(source) > 5 and source in title_norm:
            return info

    return None


def generate_schema() -> dict:
    """Generate OWL schema definitions for transposition properties."""
    schema_nodes: list[dict] = [
        # ObjectProperty: transposesDirective
        {
            "@id": "estleg:transposesDirective",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": "võtab üle direktiivi (transposes directive)",
            "rdfs:comment": "Links an Estonian legal provision to the EU directive it transposes.",
            "rdfs:domain": {"@id": "estleg:LegalProvision"},
            "rdfs:range": {"@id": "estleg:EULegislation"},
        },
        # ObjectProperty: transposedBy (inverse)
        {
            "@id": "estleg:transposedBy",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": "üle võetud (transposed by)",
            "rdfs:comment": "Inverse of transposesDirective — links an EU directive to the national law that transposes it.",
            "rdfs:domain": {"@id": "estleg:EULegislation"},
            "rdfs:range": {"@id": "estleg:LegalProvision"},
            "owl:inverseOf": {"@id": "estleg:transposesDirective"},
        },
        # DatatypeProperty: transpositionStatus
        {
            "@id": "estleg:transpositionStatus",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "ülevõtmise staatus (transposition status)",
            "rdfs:comment": "Status of directive transposition: full, partial, or unknown.",
            "rdfs:domain": {"@id": "estleg:LegalProvision"},
            "rdfs:range": {"@id": "xsd:string"},
        },
    ]

    return {"@context": CONTEXT, "@graph": schema_nodes}


def update_law_file(filepath: Path, directive_ids: list[str]) -> bool:
    """
    Add estleg:transposesDirective to the first LegalProvision-typed node in a law file.
    Returns True if the file was modified.
    """
    try:
        data = load_json(filepath)
    except Exception as e:
        print(f"    ERROR loading {filepath.name}: {e}")
        return False

    # Ensure dcterms is in context
    ctx = data.get("@context", {})
    if "dcterms" not in ctx:
        ctx["dcterms"] = "http://purl.org/dc/terms/"
        data["@context"] = ctx

    modified = False
    graph = data.get("@graph", [])

    # Find the ontology node or first named individual to attach the directive link
    target_node = None
    for node in graph:
        types = node.get("@type", [])
        # Look for the ontology metadata node
        if "owl:Ontology" in types:
            target_node = node
            break

    if target_node is None and graph:
        target_node = graph[0]

    if target_node is None:
        return False

    # Build directive references
    dir_refs = [{"@id": did} for did in directive_ids]
    existing = target_node.get("estleg:transposesDirective", [])
    if isinstance(existing, dict):
        existing = [existing]

    existing_ids = {ref.get("@id") for ref in existing if isinstance(ref, dict)}
    new_refs = [ref for ref in dir_refs if ref["@id"] not in existing_ids]

    if not new_refs:
        return False

    all_refs = existing + new_refs
    target_node["estleg:transposesDirective"] = all_refs

    # Set transposition status as unknown (EUR-Lex doesn't tell us full vs partial)
    if "estleg:transpositionStatus" not in target_node:
        target_node["estleg:transpositionStatus"] = "unknown"

    save_json(filepath, data)
    modified = True
    return modified


def update_directive_file(directive_celex_to_laws: dict[str, list[str]]) -> int:
    """
    Add estleg:transposedBy to EU directive entries in the directives file.
    Returns count of updated directive nodes.
    """
    directives_file = EURLEX_DIR / "eurlex_directives_peep.json"
    if not directives_file.exists():
        print("  WARNING: Directives file not found, skipping inverse links")
        return 0

    try:
        data = load_json(directives_file)
    except Exception as e:
        print(f"  ERROR loading directives file: {e}")
        return 0

    updated = 0
    graph = data.get("@graph", [])

    for node in graph:
        celex = node.get("estleg:celexNumber", "")
        if celex not in directive_celex_to_laws:
            continue

        law_ids = directive_celex_to_laws[celex]
        law_refs = [{"@id": lid} for lid in law_ids]

        existing = node.get("estleg:transposedBy", [])
        if isinstance(existing, dict):
            existing = [existing]

        existing_ids = {ref.get("@id") for ref in existing if isinstance(ref, dict)}
        new_refs = [ref for ref in law_refs if ref["@id"] not in existing_ids]

        if not new_refs:
            continue

        all_refs = existing + new_refs
        node["estleg:transposedBy"] = all_refs

        updated += 1

    if updated > 0:
        save_json(directives_file, data)

    return updated


def clear_transposition_from_file(filepath: Path) -> bool:
    """
    Remove estleg:transposesDirective and estleg:transpositionStatus from all
    nodes in a law JSON-LD file. Returns True if the file was modified.
    """
    try:
        data = load_json(filepath)
    except Exception:
        return False

    modified = False
    for node in data.get("@graph", []):
        if "estleg:transposesDirective" in node:
            del node["estleg:transposesDirective"]
            modified = True
        if "estleg:transpositionStatus" in node:
            del node["estleg:transpositionStatus"]
            modified = True

    if modified:
        save_json(filepath, data)
    return modified


def main():
    print("=" * 60)
    print("Generate transposition mapping: Estonian laws ↔ EU directives")
    print(f"Endpoint: {SPARQL_ENDPOINT}")
    print("=" * 60)

    # --- Step 0: Clear existing transposition data ---
    print("\n--- Clearing existing transposition data ---")
    cleared_count = 0
    for peep_file in sorted(KRR_DIR.glob("*_peep.json")):
        if peep_file.parent != KRR_DIR:
            continue
        if clear_transposition_from_file(peep_file):
            cleared_count += 1
    print(f"  Cleared transposition data from {cleared_count} files")

    # Also clear transposedBy from directive files
    directives_path = EURLEX_DIR / "eurlex_directives_peep.json"
    if directives_path.exists():
        try:
            with open(directives_path, "r", encoding="utf-8") as df:
                dir_doc = json.load(df)
            modified = False
            for node in dir_doc.get("@graph", []):
                if "estleg:transposedBy" in node:
                    del node["estleg:transposedBy"]
                    modified = True
            if modified:
                save_json(directives_path, dir_doc)
                print("  Cleared transposedBy from directives file")
        except Exception as e:
            print(f"  Warning: could not clear directives file: {e}")

    # --- Step 1: Load existing indexes ---
    print("\n--- Loading existing indexes ---")

    index_path = KRR_DIR / "INDEX.json"
    if not index_path.exists():
        print(f"ERROR: {index_path} not found. Run generate_all_laws.py first.")
        sys.exit(1)
    index_data = load_json(index_path)
    print(f"  Loaded INDEX.json: {index_data.get('total_laws', 0)} laws")

    # Build law lookup index
    print("  Building law name index...")
    law_index = build_law_index(index_data)
    print(f"  Law index entries: {len(law_index)}")

    # Build directive lookup
    print("  Building directive CELEX index...")
    directive_index = build_directive_index()
    print(f"  Directive index entries: {len(directive_index)}")

    # --- Step 2: Generate schema ---
    print("\n--- Generating transposition schema ---")
    schema_doc = generate_schema()
    schema_path = KRR_DIR / "transposition_schema.json"
    save_json(schema_path, schema_doc)
    print(f"  Saved: {schema_path.name}")

    # --- Step 3: Fetch transposition measures from EUR-Lex ---
    print("\n--- Fetching transposition measures for Estonia ---")
    measures = fetch_transposition_measures()
    print(f"  Total transposition measures found: {len(measures)}")

    if not measures:
        print("  No transposition measures found. Check endpoint availability.")
        # Still produce an empty report
        report = {
            "generated": datetime.now().strftime("%Y-%m-%d"),
            "source": SPARQL_ENDPOINT,
            "total_measures_fetched": 0,
            "matched": 0,
            "unmatched": 0,
            "mappings": [],
        }
        save_json(KRR_DIR / "transposition_mapping.json", report)
        print("  Saved empty transposition_mapping.json")
        return

    # --- Step 4: Match measures to Estonian laws ---
    print("\n--- Matching measures to Estonian law ontology entries ---")
    matched_mappings: list[dict] = []
    unmatched_titles: list[str] = []

    # Track which law files need which directives
    law_file_directives: dict[str, list[str]] = {}  # filepath → [directive IRI, ...]
    # Track which directives are transposed by which law IRIs
    directive_celex_to_law_iris: dict[str, list[str]] = {}  # celex → [law ontology node IRI, ...]

    for measure in measures:
        celex_dir = measure["celex_dir"]
        title_nat = measure["title_nat"]

        law_match = match_title_to_law(title_nat, law_index)
        if law_match is None:
            unmatched_titles.append(title_nat)
            continue

        # Determine the directive IRI in our ontology
        directive_iri = directive_index.get(celex_dir, "")
        if not directive_iri:
            # Create a synthetic IRI
            directive_iri = f"estleg:EU_{sanitize_celex(celex_dir)}"

        # Track the mapping
        mapping_entry = {
            "directive_celex": celex_dir,
            "directive_iri": directive_iri,
            "national_title": title_nat,
            "matched_law_name": law_match["name"],
            "matched_source_act": law_match.get("source_act", ""),
            "law_files": law_match["files"],
        }
        matched_mappings.append(mapping_entry)

        # Collect directives per law file
        for law_file in law_match["files"]:
            filepath = str(KRR_DIR / law_file)
            if filepath not in law_file_directives:
                law_file_directives[filepath] = []
            if directive_iri not in law_file_directives[filepath]:
                law_file_directives[filepath].append(directive_iri)

        # Collect law IRIs for inverse links
        # Use a generic IRI pattern based on the law name
        law_name = law_match["name"]
        # The ontology node pattern from files is typically estleg:XX_Map_2026
        # But we link to the LegalProvision class node
        law_iri = f"estleg:LegalProvision_{law_name}"
        if celex_dir not in directive_celex_to_law_iris:
            directive_celex_to_law_iris[celex_dir] = []
        if law_iri not in directive_celex_to_law_iris[celex_dir]:
            directive_celex_to_law_iris[celex_dir].append(law_iri)

    print(f"  Matched: {len(matched_mappings)}")
    print(f"  Unmatched: {len(unmatched_titles)}")

    # Deduplicate matched mappings (same law + same directive)
    seen_pairs: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for m in matched_mappings:
        key = (m["directive_celex"], m["matched_law_name"])
        if key not in seen_pairs:
            seen_pairs.add(key)
            deduped.append(m)
    matched_mappings = deduped
    print(f"  Unique law-directive pairs: {len(matched_mappings)}")

    # --- Step 5: Update Estonian law JSON-LD files ---
    print("\n--- Updating Estonian law JSON-LD files ---")
    files_updated = 0
    for filepath_str, dir_iris in law_file_directives.items():
        filepath = Path(filepath_str)
        if not filepath.exists():
            print(f"    SKIP (not found): {filepath.name}")
            continue
        if update_law_file(filepath, dir_iris):
            files_updated += 1
            print(f"    Updated: {filepath.name} ({len(dir_iris)} directive(s))")

    print(f"  Total law files updated: {files_updated}")

    # --- Step 6: Update EU directive file with inverse links ---
    print("\n--- Adding inverse transposedBy links to EU directives ---")
    directives_updated = update_directive_file(directive_celex_to_law_iris)
    print(f"  Directive nodes updated: {directives_updated}")

    # --- Step 7: Generate report ---
    print("\n--- Generating transposition mapping report ---")

    # Count unique directives and laws
    unique_directives = set(m["directive_celex"] for m in matched_mappings)
    unique_laws = set(m["matched_law_name"] for m in matched_mappings)

    report = {
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "source": SPARQL_ENDPOINT,
        "country": "EST",
        "total_measures_fetched": len(measures),
        "total_matched": len(matched_mappings),
        "total_unmatched": len(unmatched_titles),
        "unique_directives": len(unique_directives),
        "unique_laws": len(unique_laws),
        "law_files_updated": files_updated,
        "directive_nodes_updated": directives_updated,
        "mappings": sorted(matched_mappings, key=lambda m: m["directive_celex"]),
        "unmatched_sample": unmatched_titles[:50],
    }

    report_path = KRR_DIR / "transposition_mapping.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")

    # --- Summary ---
    print("\n" + "=" * 60)
    print("Transposition mapping complete!")
    print(f"  Measures fetched from EUR-Lex:  {len(measures)}")
    print(f"  Matched to Estonian laws:       {len(matched_mappings)}")
    print(f"  Unmatched:                      {len(unmatched_titles)}")
    print(f"  Unique EU directives:           {len(unique_directives)}")
    print(f"  Unique Estonian laws:           {len(unique_laws)}")
    print(f"  Law files updated:              {files_updated}")
    print(f"  Directive nodes updated:        {directives_updated}")
    print(f"\nOutputs:")
    print(f"  {report_path.relative_to(REPO_ROOT)}")
    print(f"  {schema_path.relative_to(REPO_ROOT)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
