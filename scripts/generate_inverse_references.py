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
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from estleg_common import iter_peep_files
from kov_pipeline_coverage import (
    CoverageReport,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)

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

    # Collect from main krr_outputs directory. KOV inclusion is now the
    # baseline — Layer 2b body-text references in KOV peep files must be
    # collected so they gain referencedBy on their targets (Round-3 fix #7).
    all_files = iter_peep_files()
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

            # Register this IRI's file location. Three node-id shapes
            # carry references in the corpus:
            #   1. <prefix>_Par_<n>      provisions in laws / regulations
            #   2. RK_*                  riigikohus decisions
            #   3. owl:Ontology-typed    act-level IRIs (laws, state regs,
            #                            KOV regs). Layer 2b body-text refs
            #                            resolve to these directly, so they
            #                            must be indexed too — otherwise
            #                            referencedBy on KOV act targets
            #                            silently drops.
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            is_act_level = any(t in {"owl:Ontology", "estleg:Act",
                                       "estleg:Law",
                                       "estleg:NationalRegulation",
                                       "estleg:GovernmentRegulation",
                                       "estleg:MinisterialRegulation",
                                       "estleg:MunicipalRegulation"}
                                for t in types)
            if "_Par_" in node_id or "RK_" in node_id or is_act_level:
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
) -> tuple[dict[Path, int], list[str], dict[str, str]]:
    """
    Apply estleg:referencedBy to target files.

    Uses alias resolution for target IRIs that cannot be found directly.

    Returns:
      - update_counts: {target_filepath: count_of_nodes_updated}
        (Path-keyed so nested-directory targets — e.g. KOV peeps under
        regulations/kov/<municipality>/ — never collide on basename;
        also matches apply_implemented_by's signature so the coverage
        accumulator can resolve target paths losslessly)
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

    update_counts: dict[Path, int] = {}

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
            update_counts[target_file] = nodes_updated

    return update_counts, unresolved_iris, alias_resolved


# ---------------------------------------------------------------------------
# Layer 2b: implementedBy filtered projection
#
# Distinct from estleg:referencedBy (body-text refs). estleg:implementedBy
# is drawn ONLY from preamble citations:
#   1. estleg:issuedUnder         — act node's direct enabling-act link
#   2. estleg:implementsCitation  — reified Citation whose
#                                   estleg:citationTarget gives the target
# Body-text estleg:references are explicitly NOT consulted here.
# ---------------------------------------------------------------------------


def collect_preamble_back_references(
    json_files: list[Path],
) -> dict[str, list[str]]:
    """Collect inverse links derived ONLY from preamble citations.

    Two sources:
      1. estleg:issuedUnder  → enabling_act_iri receives the source act
      2. estleg:implementsCitation → reified Citation node's
                                       estleg:citationTarget receives
                                       the source act

    Body-text references (estleg:references) are explicitly NOT
    consulted. They feed estleg:referencedBy via the existing
    inverse pass.
    """
    inverse: dict[str, list[str]] = defaultdict(list)
    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        graph = doc.get("@graph", [])
        # Build a quick map of citation_iri → target_iri inside this file
        citation_target: dict[str, str] = {}
        for n in graph:
            types = n.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "estleg:Citation" in types:
                cid = n.get("@id")
                tgt = n.get("estleg:citationTarget")
                if isinstance(tgt, dict):
                    tgt = tgt.get("@id")
                if cid and tgt:
                    citation_target[cid] = tgt

        for n in graph:
            types = n.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if not any(t in {"owl:Ontology", "estleg:Act"} for t in types):
                continue
            source_act_iri = n.get("@id")
            if not source_act_iri:
                continue

            # issuedUnder targets
            iu = n.get("estleg:issuedUnder", [])
            if isinstance(iu, dict):
                iu = [iu]
            for ref in iu:
                if isinstance(ref, dict) and "@id" in ref:
                    inverse[ref["@id"]].append(source_act_iri)

            # implementsCitation → resolve to citationTarget
            ic = n.get("estleg:implementsCitation", [])
            if isinstance(ic, dict):
                ic = [ic]
            for ref in ic:
                if isinstance(ref, dict) and "@id" in ref:
                    target = citation_target.get(ref["@id"])
                    if target:
                        inverse[target].append(source_act_iri)
    # Dedupe per target
    return {k: sorted(set(v)) for k, v in inverse.items()}


def clear_stale_implemented_by(json_files: list[Path]) -> int:
    """Idempotency pass: scan every peep file once and remove any
    pre-existing estleg:implementedBy / estleg:implementedByCount
    triples. Returns the number of files modified.

    Required because apply_implemented_by writes ONLY to nodes still
    present in the current preamble_inverse map. Without this clear
    pass, a stale target whose source act lost its issuedUnder triple
    (e.g. preamble parser improvement, or act removal) would retain
    a now-incorrect implementedBy across re-runs.
    """
    n_modified = 0
    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        modified = False
        for node in doc.get("@graph", []):
            if "estleg:implementedBy" in node:
                node.pop("estleg:implementedBy")
                modified = True
            if "estleg:implementedByCount" in node:
                node.pop("estleg:implementedByCount")
                modified = True
        if modified:
            with open(json_file, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            n_modified += 1
    return n_modified


def apply_implemented_by(
    inverse: dict[str, list[str]],
    iri_to_file: dict[str, Path],
) -> dict[Path, int]:
    """Write estleg:implementedBy + estleg:implementedByCount onto
    target nodes. Returns {target_filepath: count_of_nodes_updated}.

    NOTE: callers MUST run clear_stale_implemented_by() over the same
    file set BEFORE this function. Otherwise stale targets that no
    longer appear in `inverse` retain old triples.

    The Path-keyed return matches apply_inverse_references' new
    signature so the coverage accumulator (Task 11) can resolve the
    target file path losslessly even when peeps live in nested
    regulations/kov/<municipality>/ directories with colliding
    basenames.
    """
    file_updates: dict[Path, dict[str, list[str]]] = defaultdict(dict)
    for target_iri, sources in inverse.items():
        target_file = iri_to_file.get(target_iri)
        if target_file is None:
            continue
        file_updates[target_file][target_iri] = sources

    counts: dict[Path, int] = {}
    for target_file, by_iri in sorted(file_updates.items()):
        try:
            with open(target_file, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        nodes_updated = 0
        for node in doc.get("@graph", []):
            nid = node.get("@id")
            if not nid or nid not in by_iri:
                continue
            sources = by_iri[nid]
            node["estleg:implementedBy"] = [{"@id": s} for s in sources]
            # JSON-LD 1.1 expands a plain integer to xsd:integer per
            # spec, with no @context coercion needed. We deliberately
            # use a plain int here (not {"@type": "xsd:integer",
            # "@value": ...}) to match the project's existing literal
            # convention; SPARQL queries can still filter via
            # xsd:integer. If a future SHACL constraint requires the
            # expanded form, refactor to typed-literal at that point.
            node["estleg:implementedByCount"] = len(sources)
            nodes_updated += 1
        if nodes_updated:
            with open(target_file, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            counts[target_file] = nodes_updated
    return counts


def compute_implemented_by_count(
    inverse: dict[str, list[str]],
) -> dict[str, int]:
    """Aggregate implementedByCount per target IRI."""
    return {target: len(set(sources)) for target, sources in inverse.items()}


def clear_existing_inverse_refs() -> int:
    """
    Remove all existing estleg:referencedBy from law files for idempotent re-run.

    Returns count of files cleaned.
    """
    cleaned = 0
    # KOV inclusion is now the baseline — KOV targets that received
    # referencedBy in a previous run must be cleared too so this pass
    # stays idempotent (Layer 2b).
    all_files = iter_peep_files()
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

    # KOV inclusion is now the baseline — KOV targets carrying
    # referencedBy must be reachable for symmetry verification (Layer 2b).
    all_files = iter_peep_files()
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


def main() -> int:
    print("=" * 70)
    print("Estonian Legal Ontology - Generate Inverse References (referencedBy)")
    print("=" * 70)

    # ---------- coverage instrumentation ----------
    _start = time.perf_counter()
    _files_processed: set[Path] = set()
    _files_processed_kov: set[Path] = set()
    _files_with_output: set[Path] = set()
    _files_with_output_kov: set[Path] = set()
    _triples = 0
    _triples_kov = 0
    _failures: list[str] = []

    # Layer 2a parity: every scanned file counts as processed.
    # collect_all_references() opened all of these; mark them all,
    # regardless of whether they ended up emitting an inverse triple.
    for path in iter_peep_files():
        _files_processed.add(path)
        if "regulations/kov/" in str(path):
            _files_processed_kov.add(path)

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
    print(f"\nUpdated {sum(update_counts.values())} nodes across "
          f"{len(update_counts)} files")

    # Target-side attribution: each (target_path, count) pair is one
    # peep file that received >=1 referencedBy triple in this pass.
    for target_path, n in update_counts.items():
        _files_with_output.add(target_path)
        if "regulations/kov/" in str(target_path):
            _files_with_output_kov.add(target_path)
        _triples += n
        if "regulations/kov/" in str(target_path):
            _triples_kov += n

    print("\nLayer 2b: collecting preamble back-references...")
    files = list(iter_peep_files())
    preamble_inverse = collect_preamble_back_references(files)

    # Idempotent clear before re-applying. Strips any stale
    # implementedBy / implementedByCount triples from prior runs so
    # corrected preamble parses and removed acts can't leave behind
    # now-incorrect triples.
    cleared = clear_stale_implemented_by(files)
    print(f"  cleared stale implementedBy/Count from {cleared} files")

    impl_counts = apply_implemented_by(preamble_inverse, iri_to_file)
    print(f"  implementedBy emitted to {sum(impl_counts.values())} nodes "
          f"across {len(impl_counts)} files")

    # Layer 2b implementedBy attribution — target side only. The
    # source-side processed-set already covers every scanned input
    # file (the loop at the top of main() runs once at startup) and
    # doesn't need additional bookkeeping here.
    for target_path, n in impl_counts.items():
        _files_with_output.add(target_path)
        if "regulations/kov/" in str(target_path):
            _files_with_output_kov.add(target_path)
        _triples += n
        if "regulations/kov/" in str(target_path):
            _triples_kov += n

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
            # Path keys aren't JSON-serializable. Render each target
            # as its corpus-relative path (falling back to the absolute
            # POSIX form if the file lives outside KRR_DIR — defensive,
            # shouldn't happen in normal runs).
            (
                str(path.relative_to(KRR_DIR))
                if path.is_absolute() and KRR_DIR in path.parents
                else path.as_posix()
            ): count
            for path, count in sorted(update_counts.items(), key=lambda x: -x[1])
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

    # ---------- coverage report (KOV-aware schema) ----------
    all_input_files = list(iter_peep_files())
    _kov_files = [p for p in all_input_files
                  if "regulations/kov/" in str(p)]

    _wall, _rate, _peak_mb = measure_runtime(_start, len(_files_processed))
    out_path = KRR_DIR / "reports" / "kov" / "generate_inverse_references_coverage.json"
    write_coverage_report(
        CoverageReport(
            pipeline="generate_inverse_references",
            run_timestamp=datetime.now(timezone.utc).isoformat(),
            pipeline_version=resolve_pipeline_version(),
            input_files_total=len(all_input_files),
            input_files_kov=len(_kov_files),
            files_processed=len(_files_processed),
            files_processed_kov=len(_files_processed_kov),
            files_with_output=len(_files_with_output),
            files_with_output_kov=len(_files_with_output_kov),
            triples_emitted=_triples,
            triples_emitted_kov=_triples_kov,
            unresolved_references=len(unresolved_iris),
            wall_time_seconds=round(_wall, 2),
            items_per_second=round(_rate, 2),
            peak_memory_mb=round(_peak_mb, 1),
            error_count=len(_failures),
            failure_samples=_failures,
        ),
        out_path,
    )
    print(f"\nCoverage report: {out_path}")
    print(f"  files_with_output: {len(_files_with_output)} "
          f"(KOV: {len(_files_with_output_kov)})")
    print(f"  triples_emitted: {_triples} (KOV: {_triples_kov})")

    # Gate check — fails the run when the KOV side produced nothing
    # despite the corpus being present.
    if len(_kov_files) >= 11000 and len(_files_with_output_kov) == 0:
        print("\nGATE FAIL: KOV files were processed but none received output.")
        return 1
    print("\nGATE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
