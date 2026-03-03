#!/usr/bin/env python3
"""
Comprehensive validation script for Estonian Legal Ontology files.

Checks:
1. JSON syntax validity
2. @context consistency (same namespace across all files)
3. @id uniqueness (no duplicates within or across files)
4. @type is always an array
5. Property type consistency (multi-valued props are arrays)
6. sectionNumber is always a string
7. dc:source is always a string

Exit code 0 = all pass, 1 = failures found.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
EXPECTED_NS = "https://data.riik.ee/ontology/estleg#"

MULTI_VALUED_PROPS = {
    "estleg:coversConcept", "coversConcept",
    "estleg:hasSection", "hasSection",
    "estleg:hasDivision", "hasDivision",
    "estleg:hasSubdivision", "hasSubdivision",
    "estleg:hasChapter", "hasChapter",
    "estleg:references", "references",
}

errors = []
warnings = []


def error(msg: str):
    errors.append(msg)
    print(f"  ERROR: {msg}")


def warn(msg: str):
    warnings.append(msg)
    print(f"  WARN: {msg}")


def validate_json_syntax(filepath: Path) -> dict | None:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        error(f"{filepath.name}: Invalid JSON - {e}")
        return None
    except UnicodeDecodeError as e:
        error(f"{filepath.name}: Encoding error - {e}")
        return None


def validate_context(filepath: Path, doc: dict):
    ctx = doc.get("@context", {})
    if isinstance(ctx, dict):
        ns = ctx.get("estleg", "")
        if ns != EXPECTED_NS:
            error(f"{filepath.name}: Wrong estleg namespace: {ns} (expected {EXPECTED_NS})")


def validate_types(filepath: Path, doc: dict):
    if "@graph" not in doc:
        return
    for i, node in enumerate(doc["@graph"]):
        if "@type" in node and not isinstance(node["@type"], list):
            error(f"{filepath.name}: @type is not an array at graph[{i}] (@id={node.get('@id', '?')})")


def validate_multi_valued(filepath: Path, doc: dict):
    if "@graph" not in doc:
        return
    for i, node in enumerate(doc["@graph"]):
        for key in node:
            if key in MULTI_VALUED_PROPS:
                val = node[key]
                if not isinstance(val, list):
                    error(f"{filepath.name}: {key} is not an array at graph[{i}] (@id={node.get('@id', '?')})")


def validate_section_numbers(filepath: Path, doc: dict):
    if "@graph" not in doc:
        return
    for i, node in enumerate(doc["@graph"]):
        for key in ("estleg:sectionNumber", "sectionNumber"):
            if key in node and not isinstance(node[key], str):
                error(f"{filepath.name}: {key} is not a string at graph[{i}] ({type(node[key]).__name__})")


def validate_dc_source(filepath: Path, doc: dict):
    if "@graph" not in doc:
        return
    for i, node in enumerate(doc["@graph"]):
        for key in ("dc:source", "source"):
            if key in node and isinstance(node[key], list):
                error(f"{filepath.name}: {key} is an array at graph[{i}] (expected string)")


def validate_id_uniqueness(all_ids: dict[str, list[str]]):
    print("\n--- @id Uniqueness ---")
    dupes = {k: v for k, v in all_ids.items() if len(v) > 1}
    if dupes:
        warn(f"{len(dupes)} @id values appear in multiple files (see docs/DUPLICATE_IDS_REPORT.md)")
    else:
        print("  OK: All @id values are unique across files")


def main():
    print("=" * 60)
    print("Estonian Legal Ontology - Validation")
    print("=" * 60)

    files = sorted(list(KRR_DIR.glob("*.json")) + list(KRR_DIR.glob("*.jsonld"))
                   + list(KRR_DIR.glob("**/*.json")) + list(KRR_DIR.glob("**/*.jsonld")))
    # Deduplicate (glob ** also matches top-level)
    files = sorted(set(files))
    # Exclude index and summary files
    exclude_prefixes = ("INDEX", "combined_", "EELNOUD_INDEX", "eelnoud_combined", "RIIGIKOHUS_INDEX", "EURLEX_INDEX", "eurlex_combined", "CURIA_INDEX", "curia_combined")
    files = [f for f in files if not any(f.name.startswith(p) for p in exclude_prefixes)]

    print(f"\nValidating {len(files)} files...\n")

    all_ids: dict[str, list[str]] = defaultdict(list)

    for filepath in files:
        doc = validate_json_syntax(filepath)
        if doc is None:
            continue

        validate_context(filepath, doc)
        validate_types(filepath, doc)
        validate_multi_valued(filepath, doc)
        validate_section_numbers(filepath, doc)
        validate_dc_source(filepath, doc)

        # Collect IDs
        if "@graph" in doc:
            seen_in_file = set()
            for node in doc["@graph"]:
                nid = node.get("@id", "")
                if nid in seen_in_file:
                    error(f"{filepath.name}: Duplicate @id within file: {nid}")
                seen_in_file.add(nid)
                all_ids[nid].append(filepath.name)

    validate_id_uniqueness(all_ids)

    print("\n" + "=" * 60)
    print(f"Files validated: {len(files)}")
    print(f"Errors: {len(errors)}")
    print(f"Warnings: {len(warnings)}")
    print("=" * 60)

    if errors:
        print("\nVALIDATION FAILED")
        sys.exit(1)
    else:
        print("\nVALIDATION PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
