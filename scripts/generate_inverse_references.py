#!/usr/bin/env python3
"""
Generate estleg:referencedBy inverse links from existing estleg:references.

This script:
1. Scans all *_peep.json files for estleg:references properties
2. For each A estleg:references B, records that B estleg:referencedBy A
3. Groups inverse links by target file (which JSON-LD file contains node B)
4. Updates each target file, adding estleg:referencedBy arrays
5. Verifies symmetry: every references A->B has a matching referencedBy B<-A
6. Generates inverse_references_report.json with statistics

Idempotent: clears any existing estleg:referencedBy before regenerating.

Handles IRI alias resolution for mismatched prefixes (e.g.
"Vlaigusseadus_Par_14" -> "VOS_Par_14") so that forward references
authored with an alternate prefix still get their inverse links.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"

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

# ---------------------------------------------------------------------------
# IRI prefix aliases
#
# Some forward references were written with an alternative prefix that does
# not match any existing node @id.  Map each known alias to the list of
# canonical prefixes that should be searched (in priority order).
#
# Example: "Vlaigusseadus" (a transliteration of Võlaõigusseadus) is used
# in some references but the actual node IRIs use VOS / VOS3 / VOS_O4 / VOS7.
# ---------------------------------------------------------------------------
IRI_PREFIX_ALIASES: dict[str, list[str]] = {
    "Vlaigusseadus": ["VOS", "VOS3", "VOS_O4", "VOS7"],
}


def save_json(filepath: Path, doc: dict) -> None:
    """Write JSON-LD document to file with consistent formatting."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _build_prefix_par_index(
    iri_to_file: dict[str, Path],
) -> dict[str, dict[str, str]]:
    """
    Build {prefix: {par_number: full_iri}} from the iri_to_file map.

    Only considers IRIs of the form ``estleg:{Prefix}_Par_{Number}``.
    """
    index: dict[str, dict[str, str]] = defaultdict(dict)
    for iri in iri_to_file:
        if not iri.startswith("estleg:") or "_Par_" not in iri:
            continue
        local = iri[len("estleg:"):]
        prefix, par = local.split("_Par_", 1)
        index[prefix][par] = iri
    return dict(index)


def _resolve_alias(
    target_iri: str,
    iri_to_file: dict[str, Path],
    prefix_par_index: dict[str, dict[str, str]],
) -> str | None:
    """
    Try to resolve *target_iri* via IRI_PREFIX_ALIASES.

    If the target uses a known alias prefix **and** the paragraph number
    exists under one of the canonical prefixes, return the canonical IRI.
    Otherwise return ``None``.
    """
    if not target_iri.startswith("estleg:") or "_Par_" not in target_iri:
        return None

    local = target_iri[len("estleg:"):]
    alias_prefix, par_num = local.split("_Par_", 1)

    canonical_prefixes = IRI_PREFIX_ALIASES.get(alias_prefix)
    if canonical_prefixes is None:
        return None

    for canon in canonical_prefixes:
        pars = prefix_par_index.get(canon, {})
        if par_num in pars:
            resolved = pars[par_num]
            if resolved in iri_to_file:
                return resolved
    return None


def collect_all_references() -> tuple[
    dict[str, list[str]],
    dict[str, Path],
    dict[str, dict[str, str]],
]:
    """
    Scan all law JSON-LD files and collect forward references.

    Returns:
      - inverse_map: {target_iri: [source_iri, ...]} for estleg:referencedBy
      - iri_to_file: {iri: filepath} mapping each provision IRI to its file
      - prefix_par_index: {prefix: {par_number: iri}} for alias resolution
    """
    inverse_map: dict[str, list[str]] = defaultdict(list)
    iri_to_file: dict[str, Path] = {}

    # Collect from main krr_outputs directory
    all_files = sorted(KRR_DIR.glob("*_peep.json"))
    # Also include riigikohus files (though they typically don't have references)
    rk_dir = KRR_DIR / "riigikohus"
    if rk_dir.exists():
        all_files.extend(sorted(rk_dir.glob("*_peep.json")))

    for json_file in all_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        for node in doc.get("@graph", []):
            node_id = node.get("@id", "")
            if not node_id:
                continue

            # Register this IRI's file location
            if "_Par_" in node_id or "RK_" in node_id:
                iri_to_file[node_id] = json_file

            # Collect forward references
            refs = node.get("estleg:references")
            if refs is None:
                continue

            # Normalize to list
            if isinstance(refs, dict):
                ref_list = [refs]
            elif isinstance(refs, list):
                ref_list = refs
            else:
                continue

            for ref in ref_list:
                if isinstance(ref, dict) and "@id" in ref:
                    target_iri = ref["@id"]
                    if target_iri != node_id:  # skip self-refs
                        inverse_map[target_iri].append(node_id)

    # Build prefix-based paragraph index for alias resolution
    prefix_par_index = _build_prefix_par_index(iri_to_file)

    return dict(inverse_map), iri_to_file, prefix_par_index


def apply_inverse_references(
    inverse_map: dict[str, list[str]],
    iri_to_file: dict[str, Path],
    prefix_par_index: dict[str, dict[str, str]],
) -> tuple[dict[str, int], list[str], dict[str, str]]:
    """
    Apply estleg:referencedBy to target files.

    Uses alias resolution for target IRIs that cannot be found directly.

    Returns:
      - update_counts: {filepath_name: count_of_nodes_updated}
      - unresolved_iris: list of target IRIs that could not be resolved
      - alias_resolved: {original_iri: canonical_iri} for IRIs fixed via alias
    """
    # Group inverse references by target file
    file_updates: dict[Path, dict[str, list[str]]] = defaultdict(dict)

    unresolved_iris: list[str] = []
    alias_resolved: dict[str, str] = {}

    for target_iri, source_iris in inverse_map.items():
        target_file = iri_to_file.get(target_iri)

        # If direct lookup fails, try alias resolution
        if target_file is None:
            canonical = _resolve_alias(target_iri, iri_to_file, prefix_par_index)
            if canonical is not None:
                target_file = iri_to_file.get(canonical)
                alias_resolved[target_iri] = canonical
                # Re-key: merge sources under the canonical IRI
                target_iri = canonical

        if target_file is None:
            unresolved_iris.append(target_iri)
            continue

        # Deduplicate source IRIs
        existing = file_updates[target_file].get(target_iri, [])
        merged = existing + source_iris
        unique_sources = list(dict.fromkeys(merged))
        file_updates[target_file][target_iri] = unique_sources

    if unresolved_iris:
        print(f"  Warning: {len(unresolved_iris)} target IRIs could not be resolved to files")
        for iri in sorted(unresolved_iris)[:10]:
            print(f"    {iri}")
        if len(unresolved_iris) > 10:
            print(f"    ... and {len(unresolved_iris) - 10} more")
    if alias_resolved:
        print(f"  Resolved {len(alias_resolved)} IRIs via prefix aliases")

    update_counts: dict[str, int] = {}

    for target_file, iri_sources in sorted(file_updates.items()):
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  Error reading {target_file.name}: {e}")
            continue

        modified = False
        nodes_updated = 0

        for node in doc.get("@graph", []):
            node_id = node.get("@id", "")
            if node_id not in iri_sources:
                # Clear any stale referencedBy (idempotent)
                if "estleg:referencedBy" in node:
                    del node["estleg:referencedBy"]
                    modified = True
                continue

            sources = iri_sources[node_id]
            ref_iris = [{"@id": s} for s in sources]

            node["estleg:referencedBy"] = ref_iris

            nodes_updated += 1
            modified = True

        if modified:
            save_json(target_file, doc)
            update_counts[target_file.name] = nodes_updated

    return update_counts, unresolved_iris, alias_resolved


def clear_existing_inverse_refs() -> int:
    """
    Remove all existing estleg:referencedBy from law files for idempotent re-run.

    Returns count of files cleaned.
    """
    cleaned = 0
    all_files = sorted(KRR_DIR.glob("*_peep.json"))
    rk_dir = KRR_DIR / "riigikohus"
    if rk_dir.exists():
        all_files.extend(sorted(rk_dir.glob("*_peep.json")))

    for json_file in all_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        modified = False
        for node in doc.get("@graph", []):
            if "estleg:referencedBy" in node:
                del node["estleg:referencedBy"]
                modified = True

        if modified:
            save_json(json_file, doc)
            cleaned += 1

    return cleaned


def verify_symmetry() -> list[dict]:
    """
    Post-verification: for every ``estleg:references`` A->B, verify that
    node B carries ``estleg:referencedBy`` pointing back to A.

    Returns a list of mismatch dicts:
      {"source": A, "target": B, "file": filename, "issue": description}
    """
    # Pass 1 – collect all referencedBy for quick lookup
    referenced_by: dict[str, set[str]] = defaultdict(set)

    all_files = sorted(KRR_DIR.glob("*_peep.json"))
    rk_dir = KRR_DIR / "riigikohus"
    if rk_dir.exists():
        all_files.extend(sorted(rk_dir.glob("*_peep.json")))

    iri_exists: set[str] = set()

    for json_file in all_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for node in doc.get("@graph", []):
            node_id = node.get("@id", "")
            if node_id:
                iri_exists.add(node_id)
            inv = node.get("estleg:referencedBy")
            if inv is None:
                continue
            if isinstance(inv, dict):
                inv = [inv]
            for entry in inv:
                if isinstance(entry, dict) and "@id" in entry:
                    referenced_by[node_id].add(entry["@id"])

    # Pass 2 – check every forward reference
    mismatches: list[dict] = []
    for json_file in all_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for node in doc.get("@graph", []):
            source_id = node.get("@id", "")
            refs = node.get("estleg:references")
            if refs is None:
                continue
            if isinstance(refs, dict):
                refs = [refs]
            for ref in refs:
                if not isinstance(ref, dict) or "@id" not in ref:
                    continue
                target_id = ref["@id"]
                if target_id == source_id:
                    continue
                if target_id not in iri_exists:
                    mismatches.append({
                        "source": source_id,
                        "target": target_id,
                        "file": json_file.name,
                        "issue": "target node does not exist in any file",
                    })
                elif source_id not in referenced_by.get(target_id, set()):
                    mismatches.append({
                        "source": source_id,
                        "target": target_id,
                        "file": json_file.name,
                        "issue": "target exists but missing referencedBy back-link",
                    })

    return mismatches


def main() -> None:
    print("=" * 70)
    print("Estonian Legal Ontology - Generate Inverse References (referencedBy)")
    print("=" * 70)

    # Step 1: Clear existing inverse references for idempotent re-run
    print("\n[1/5] Clearing existing estleg:referencedBy properties...")
    cleaned = clear_existing_inverse_refs()
    print(f"  Cleaned {cleaned} files")

    # Step 2: Collect all forward references
    print("\n[2/5] Collecting estleg:references from all files...")
    inverse_map, iri_to_file, prefix_par_index = collect_all_references()
    total_inverse_links = sum(len(v) for v in inverse_map.values())
    print(f"  Found {len(inverse_map)} target IRIs with {total_inverse_links} inverse links")
    print(f"  IRI index: {len(iri_to_file)} provision/decision IRIs mapped to files")
    print(f"  Prefix index: {len(prefix_par_index)} prefixes")
    print(f"  Alias rules: {len(IRI_PREFIX_ALIASES)} alias prefixes configured")

    # Step 3: Apply inverse references (with alias resolution)
    print("\n[3/5] Applying estleg:referencedBy to target files...")
    update_counts, unresolved_iris, alias_resolved = apply_inverse_references(
        inverse_map, iri_to_file, prefix_par_index,
    )
    total_nodes_updated = sum(update_counts.values())
    print(f"  Updated {len(update_counts)} files with {total_nodes_updated} nodes")

    # Step 4: Verify symmetry
    print("\n[4/5] Verifying references/referencedBy symmetry...")
    mismatches = verify_symmetry()
    if mismatches:
        # Categorise
        missing_node = [m for m in mismatches if "does not exist" in m["issue"]]
        missing_back = [m for m in mismatches if "missing referencedBy" in m["issue"]]
        print(f"  Mismatches found: {len(mismatches)}")
        if missing_node:
            print(f"    - target node missing from all files: {len(missing_node)}")
        if missing_back:
            print(f"    - referencedBy back-link missing:     {len(missing_back)}")
            for m in missing_back[:5]:
                print(f"      {m['source']} -> {m['target']} (in {m['file']})")
            if len(missing_back) > 5:
                print(f"      ... and {len(missing_back) - 5} more")
    else:
        print("  All forward references have matching referencedBy links (or target does not exist)")

    # Step 5: Generate report
    print("\n[5/5] Generating inverse references report...")
    report = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "target_iris_with_inverse_links": len(inverse_map),
            "total_inverse_links": total_inverse_links,
            "files_updated": len(update_counts),
            "total_nodes_updated": total_nodes_updated,
            "iris_indexed": len(iri_to_file),
            "alias_resolved": len(alias_resolved),
            "unresolved_target_iris": len(unresolved_iris),
            "symmetry_mismatches": len(mismatches),
        },
        "alias_resolutions": {
            orig: canon
            for orig, canon in sorted(alias_resolved.items())
        },
        "unresolved_target_iris": sorted(unresolved_iris),
        "symmetry_mismatches": mismatches[:100],
        "top_referenced_provisions": sorted(
            [
                {"iri": iri, "referenced_by_count": len(sources)}
                for iri, sources in inverse_map.items()
            ],
            key=lambda x: x["referenced_by_count"],
            reverse=True,
        )[:50],
        "per_file_updates": {
            name: count
            for name, count in sorted(update_counts.items(), key=lambda x: -x[1])
        },
    }

    report_path = KRR_DIR / "inverse_references_report.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Target provisions with inverse links: {len(inverse_map)}")
    print(f"  Total inverse link edges:             {total_inverse_links}")
    print(f"  Files updated:                        {len(update_counts)}")
    print(f"  Total nodes with referencedBy:        {total_nodes_updated}")
    print(f"  Alias-resolved IRIs:                  {len(alias_resolved)}")
    print(f"  Unresolved target IRIs:               {len(unresolved_iris)}")
    print(f"  Symmetry mismatches:                  {len(mismatches)}")

    if inverse_map:
        top = max(inverse_map.items(), key=lambda x: len(x[1]))
        print(f"  Most referenced provision:            {top[0]} ({len(top[1])} refs)")

    print(f"  Report: {report_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
