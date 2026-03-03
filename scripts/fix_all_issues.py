#!/usr/bin/env python3
"""
Comprehensive fix script for all Estonian Legal Ontology data issues.

Fixes GitHub issues:
  #2/#17: Namespace migration (example.org → data.riik.ee)
  #3/#21: AÕS file naming normalization
  #4: @type normalization (always arrays)
  #5: Property type normalization (coversConcept, hasSection always arrays)
  #6: Duplicate @id audit and fix
  #11: Notariaadiseadus naming fix
  #12: dc:source normalization (always string)
  #13: sectionNumber normalization (always string)
  #14: Script namespace fix
"""

import json
import os
import shutil
from pathlib import Path
from collections import defaultdict

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"

OLD_NS = "https://example.org/estonian-legal#"
NEW_NS = "https://data.riik.ee/ontology/estleg#"

# Multi-valued properties that should always be arrays
MULTI_VALUED_PROPS = {
    "estleg:coversConcept", "coversConcept",
    "estleg:hasSection", "hasSection",
    "estleg:hasDivision", "hasDivision",
    "estleg:hasSubdivision", "hasSubdivision",
    "estleg:hasChapter", "hasChapter",
    "estleg:references", "references",
}

stats = {
    "namespace_replacements": 0,
    "type_normalizations": 0,
    "property_normalizations": 0,
    "dc_source_fixes": 0,
    "section_number_fixes": 0,
    "files_processed": 0,
    "files_renamed": 0,
    "files_removed": 0,
    "id_collisions_fixed": 0,
}


def normalize_type(node: dict) -> dict:
    """Ensure @type is always an array."""
    if "@type" in node:
        t = node["@type"]
        if isinstance(t, str):
            node["@type"] = [t]
            stats["type_normalizations"] += 1
    return node


def normalize_multi_valued(node: dict) -> dict:
    """Ensure multi-valued properties are always arrays."""
    for key in list(node.keys()):
        if key in MULTI_VALUED_PROPS:
            val = node[key]
            if isinstance(val, dict):
                node[key] = [val]
                stats["property_normalizations"] += 1
            elif isinstance(val, str):
                node[key] = [{"@id": val}] if val.startswith("estleg:") or val.startswith("http") else [val]
                stats["property_normalizations"] += 1
    return node


def normalize_dc_source(node: dict) -> dict:
    """Ensure dc:source is always a string (join if array)."""
    for key in ("dc:source", "source"):
        if key in node and isinstance(node[key], list):
            node[key] = "; ".join(str(s) for s in node[key])
            stats["dc_source_fixes"] += 1
    return node


def normalize_section_number(node: dict) -> dict:
    """Ensure sectionNumber is always a string."""
    for key in ("estleg:sectionNumber", "sectionNumber"):
        if key in node and isinstance(node[key], (int, float)):
            node[key] = str(int(node[key]))
            stats["section_number_fixes"] += 1
    return node


def migrate_namespace_in_value(val):
    """Recursively replace old namespace with new namespace in any value."""
    if isinstance(val, str):
        if OLD_NS in val:
            stats["namespace_replacements"] += 1
            return val.replace(OLD_NS, NEW_NS)
        return val
    elif isinstance(val, list):
        return [migrate_namespace_in_value(item) for item in val]
    elif isinstance(val, dict):
        return {migrate_namespace_in_value(k): migrate_namespace_in_value(v) for k, v in val.items()}
    return val


def process_node(node: dict) -> dict:
    """Apply all normalizations to a single graph node."""
    node = normalize_type(node)
    node = normalize_multi_valued(node)
    node = normalize_dc_source(node)
    node = normalize_section_number(node)
    return node


def process_json_file(filepath: Path) -> dict:
    """Load, transform, and return the JSON-LD document."""
    with open(filepath, "r", encoding="utf-8") as f:
        doc = json.load(f)

    # Migrate namespace
    doc = migrate_namespace_in_value(doc)

    # Process each node in @graph
    if "@graph" in doc and isinstance(doc["@graph"], list):
        doc["@graph"] = [process_node(node) for node in doc["@graph"]]

    stats["files_processed"] += 1
    return doc


def save_json(filepath: Path, doc: dict):
    """Save JSON with consistent formatting."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def fix_aos_naming():
    """Fix Asjaõigusseadus file naming (Issue #3/#21)."""
    print("\n=== Fixing AÕS file naming ===")

    # The typo variants (missing 'o')
    typo_files = sorted(KRR_DIR.glob("asjaigusseadus_osa*_peep.json"))
    correct_files = sorted(KRR_DIR.glob("asjaoigusseadus_osa*_peep.json"))

    print(f"  Typo variant files: {[f.name for f in typo_files]}")
    print(f"  Correct variant files: {[f.name for f in correct_files]}")

    # The consolidated file asjaoigusseadus_osa6-13 covers parts 6-13
    # The typo files cover parts 7-11 individually
    # We keep the consolidated file and remove the typo individual files
    for typo_file in typo_files:
        # Extract part number
        name = typo_file.name
        corrected_name = name.replace("asjaigusseadus", "asjaoigusseadus")
        corrected_path = KRR_DIR / corrected_name

        if corrected_path.exists():
            # Both variants exist - remove the typo one since correct exists
            print(f"  Removing duplicate typo file: {name} (correct variant exists)")
            typo_file.unlink()
            stats["files_removed"] += 1
        else:
            # Only typo exists - rename it
            print(f"  Renaming: {name} → {corrected_name}")
            # But also fix @id values inside the file
            doc = process_json_file(typo_file)
            # Fix internal IDs that use the wrong prefix
            raw = json.dumps(doc, ensure_ascii=False)
            raw = raw.replace("asjaigusseadus", "asjaoigusseadus")
            doc = json.loads(raw)
            save_json(corrected_path, doc)
            typo_file.unlink()
            stats["files_renamed"] += 1


def fix_notariaadiseadus_naming():
    """Fix notariaadiseadus naming (Issue #11)."""
    print("\n=== Fixing notariaadiseadus naming ===")

    old_path = KRR_DIR / "notari_seadus_peep.json"
    new_path = KRR_DIR / "notariaadiseadus_peep.json"

    if old_path.exists():
        doc = process_json_file(old_path)
        save_json(new_path, doc)
        old_path.unlink()
        print(f"  Renamed: notari_seadus_peep.json → notariaadiseadus_peep.json")
        stats["files_renamed"] += 1
    else:
        print(f"  File not found: {old_path.name}")


def audit_duplicate_ids():
    """Audit and report duplicate @id values (Issue #6)."""
    print("\n=== Auditing duplicate @id values ===")

    id_locations = defaultdict(list)

    for filepath in sorted(KRR_DIR.glob("*.json")):
        if filepath.name.endswith("_summary.json"):
            continue
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        if "@graph" not in doc:
            continue

        seen_in_file = set()
        for node in doc["@graph"]:
            if "@id" in node:
                nid = node["@id"]
                if nid in seen_in_file:
                    # Duplicate within same file!
                    print(f"  INTRA-FILE DUPLICATE: {nid} in {filepath.name}")
                seen_in_file.add(nid)
                id_locations[nid].append(filepath.name)

    # Find cross-file duplicates (excluding ontology class definitions)
    class_types = {"owl:Class", "owl:ObjectProperty", "owl:DatatypeProperty"}
    duplicates = []
    for nid, files in id_locations.items():
        if len(files) > 1:
            duplicates.append((nid, files))

    if duplicates:
        print(f"  Found {len(duplicates)} IDs appearing in multiple files")
        # Write report
        report_path = REPO_ROOT / "docs" / "DUPLICATE_IDS_REPORT.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("# Duplicate @id Report\n\n")
            f.write(f"Found {len(duplicates)} @id values appearing in multiple files.\n\n")
            f.write("| @id | Files |\n|-----|-------|\n")
            for nid, files in sorted(duplicates):
                f.write(f"| `{nid}` | {', '.join(sorted(set(files)))} |\n")
        print(f"  Report written to docs/DUPLICATE_IDS_REPORT.md")
    else:
        print("  No cross-file duplicate IDs found")

    return duplicates


def fix_intra_file_duplicates():
    """Fix duplicate @id values within the same file by appending suffix."""
    print("\n=== Fixing intra-file duplicate @id values ===")

    for filepath in sorted(KRR_DIR.glob("*.json")):
        if filepath.name.endswith("_summary.json"):
            continue
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        if "@graph" not in doc:
            continue

        seen = {}
        modified = False
        for node in doc["@graph"]:
            if "@id" in node:
                nid = node["@id"]
                if nid in seen:
                    # Append _dup suffix
                    count = seen[nid]
                    new_id = f"{nid}_dup{count}"
                    node["@id"] = new_id
                    seen[nid] = count + 1
                    modified = True
                    stats["id_collisions_fixed"] += 1
                    print(f"  Fixed: {nid} → {new_id} in {filepath.name}")
                else:
                    seen[nid] = 2

        if modified:
            save_json(filepath, doc)


def process_all_json_files():
    """Process all JSON/JSONLD files for namespace and normalization fixes."""
    print("\n=== Processing all JSON/JSONLD files ===")

    for filepath in sorted(KRR_DIR.glob("*.json")):
        if filepath.name.endswith("_summary.json"):
            # Still need namespace fix in summary files
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                doc = migrate_namespace_in_value(doc)
                save_json(filepath, doc)
                print(f"  Processed (summary): {filepath.name}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                print(f"  SKIP (parse error): {filepath.name}")
            continue

        try:
            doc = process_json_file(filepath)
            save_json(filepath, doc)
            print(f"  Processed: {filepath.name}")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"  SKIP (parse error): {filepath.name}: {e}")

    # Also process .jsonld files
    for filepath in sorted(KRR_DIR.glob("*.jsonld")):
        try:
            doc = process_json_file(filepath)
            save_json(filepath, doc)
            print(f"  Processed: {filepath.name}")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"  SKIP (parse error): {filepath.name}: {e}")


def fix_generator_script():
    """Fix namespace in generate_kars_eriosa_jsonld.py (Issue #14)."""
    print("\n=== Fixing generator script namespace ===")
    script_path = REPO_ROOT / "scripts" / "generate_kars_eriosa_jsonld.py"
    if script_path.exists():
        content = script_path.read_text(encoding="utf-8")
        if OLD_NS in content:
            content = content.replace(OLD_NS, NEW_NS)
            script_path.write_text(content, encoding="utf-8")
            print(f"  Fixed namespace in {script_path.name}")
        else:
            print(f"  Namespace already correct in {script_path.name}")


def fix_docs_namespace():
    """Fix namespace references in documentation files."""
    print("\n=== Fixing namespace in documentation ===")
    for doc_file in (REPO_ROOT / "docs").glob("*.md"):
        content = doc_file.read_text(encoding="utf-8")
        if OLD_NS in content:
            content = content.replace(OLD_NS, NEW_NS)
            doc_file.write_text(content, encoding="utf-8")
            print(f"  Fixed namespace in docs/{doc_file.name}")


def generate_index():
    """Generate master registry/index file (Issue #19)."""
    print("\n=== Generating master registry ===")

    laws = {}
    for filepath in sorted(KRR_DIR.glob("*_peep.json")):
        name = filepath.stem.replace("_peep", "")

        # Group by base law name (remove _osa* suffix)
        import re
        match = re.match(r"^(.+?)(_osa\d+.*)?$", name)
        if match:
            base_name = match.group(1)
            part_suffix = match.group(2)
        else:
            base_name = name
            part_suffix = None

        if base_name not in laws:
            laws[base_name] = {
                "base_name": base_name,
                "files": [],
                "parts": [],
            }

        laws[base_name]["files"].append(filepath.name)
        if part_suffix:
            laws[base_name]["parts"].append(part_suffix.replace("_osa", ""))

    # Also add .jsonld files
    for filepath in sorted(KRR_DIR.glob("*.jsonld")):
        name = filepath.stem
        if name not in laws:
            laws[name] = {"base_name": name, "files": [], "parts": []}
        laws[name]["files"].append(filepath.name)

    index = {
        "generated": "2026-03-02",
        "total_files": sum(len(l["files"]) for l in laws.values()),
        "total_laws": len(laws),
        "laws": []
    }

    for base_name, info in sorted(laws.items()):
        entry = {
            "name": base_name,
            "files": sorted(info["files"]),
        }
        if info["parts"]:
            entry["parts_mapped"] = sorted(info["parts"])
        index["laws"].append(entry)

    index_path = KRR_DIR / "INDEX.json"
    save_json(index_path, index)
    print(f"  Generated {index_path.name} with {len(laws)} laws")


def generate_combined_jsonld():
    """Generate combined JSON-LD file (Issue #26)."""
    print("\n=== Generating combined JSON-LD ===")

    combined_context = {
        "estleg": NEW_NS,
        "owl": "http://www.w3.org/2002/07/owl#",
        "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
        "xsd": "http://www.w3.org/2001/XMLSchema#",
        "dc": "http://purl.org/dc/elements/1.1/",
        "skos": "http://www.w3.org/2004/02/skos/core#",
        "schema": "http://schema.org/",
    }

    all_nodes = []
    seen_ids = set()

    for filepath in sorted(KRR_DIR.glob("*_peep.json")):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                doc = json.load(f)
            if "@graph" in doc:
                for node in doc["@graph"]:
                    nid = node.get("@id", "")
                    if nid not in seen_ids:
                        all_nodes.append(node)
                        seen_ids.add(nid)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

    for filepath in sorted(KRR_DIR.glob("*.jsonld")):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                doc = json.load(f)
            if "@graph" in doc:
                for node in doc["@graph"]:
                    nid = node.get("@id", "")
                    if nid not in seen_ids:
                        all_nodes.append(node)
                        seen_ids.add(nid)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

    combined = {
        "@context": combined_context,
        "@graph": all_nodes,
    }

    out_path = KRR_DIR / "combined_ontology.jsonld"
    save_json(out_path, combined)
    print(f"  Generated {out_path.name} with {len(all_nodes)} nodes from {len(seen_ids)} unique IDs")


def main():
    print("=" * 60)
    print("Estonian Legal Ontology - Comprehensive Fix Script")
    print("=" * 60)

    # Step 1: File naming fixes (before processing content)
    fix_aos_naming()
    fix_notariaadiseadus_naming()

    # Step 2: Process all JSON files (namespace, @type, properties, etc.)
    process_all_json_files()

    # Step 3: Fix intra-file duplicate @id values
    fix_intra_file_duplicates()

    # Step 4: Audit cross-file duplicate IDs
    audit_duplicate_ids()

    # Step 5: Fix generator script
    fix_generator_script()

    # Step 6: Fix docs namespace
    fix_docs_namespace()

    # Step 7: Generate index
    generate_index()

    # Step 8: Generate combined JSON-LD
    generate_combined_jsonld()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for key, val in stats.items():
        print(f"  {key}: {val}")


if __name__ == "__main__":
    main()
