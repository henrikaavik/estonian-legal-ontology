#!/usr/bin/env python3
"""
Fix all duplicate @id values across JSON-LD files.

Categories of duplicates:
1. Abbreviation collisions: different laws got the same short prefix (AS, KS, MTS, etc.)
2. Multipart osa overlaps: same-law osa files share paragraph numbers
3. Shared ontology classes: estleg:LegalPart, estleg:Section, estleg:Provision, namespace URI
4. LegalProvision_ class dupes: slug collisions between old/new law names
5. Cluster dupes: clusters with identical names across files

Strategy:
- For abbreviation collisions: assign a unique longer prefix to each file
- For multipart osa overlaps: append osa number to paragraph IDs
- For shared ontology classes: these are legitimate shared definitions, no fix needed
- For LegalProvision_ dupes: disambiguate using file-specific slug
- For cluster dupes: prefix with the law's unique identifier
"""

from __future__ import annotations

import json
import glob
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

KRR_DIR = Path(__file__).resolve().parents[1] / "krr_outputs"

_ESTONIAN_TRANSLITERATION = {
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
}


def sanitize_id(value: str) -> str:
    s = value.replace(" ", "_")
    for old, new in _ESTONIAN_TRANSLITERATION.items():
        s = s.replace(old, new)
    s = re.sub(r"[^0-9A-Za-z_]", "", s)
    return s or "Unknown"


def detect_duplicates() -> dict[str, list[str]]:
    """Find all @id values that appear in multiple files."""
    id_files: dict[str, list[str]] = defaultdict(list)
    for f in sorted(glob.glob(str(KRR_DIR / "*_peep.json"))):
        doc = json.load(open(f))
        fname = os.path.basename(f)
        for node in doc.get("@graph", []):
            nid = node.get("@id", "")
            if nid:
                id_files[nid].append(fname)
    return {k: v for k, v in id_files.items() if len(v) > 1}


def get_file_prefix(filepath: str) -> str | None:
    """Extract the IRI prefix used in a file's provision/cluster IDs."""
    doc = json.load(open(filepath))
    for node in doc.get("@graph", []):
        nid = node.get("@id", "")
        if "_Par_" in nid:
            return nid.replace("estleg:", "").split("_Par_")[0]
        if "_Map_2026" in nid:
            return nid.replace("estleg:", "").split("_Map_2026")[0]
    # Try cluster IDs
    for node in doc.get("@graph", []):
        nid = node.get("@id", "")
        if nid.startswith("estleg:Cluster_"):
            rest = nid.replace("estleg:Cluster_", "")
            # The prefix is the part before the last underscore-separated chunk
            # but this varies, so just return None for files without provisions
            pass
    return None


def build_remap_table(dupes: dict[str, list[str]]) -> dict[str, dict[str, str]]:
    """Build a per-file ID remap table: {filename: {old_id: new_id}}."""
    remap: dict[str, dict[str, str]] = defaultdict(dict)

    # ─── Categorize duplicates ───────────────────────────────────────────
    # Group by file sets to find collision groups
    file_group_dupes: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for dupe_id, files in dupes.items():
        file_group_dupes[tuple(sorted(files))].append(dupe_id)

    # ─── Shared ontology class definitions (no fix needed) ───────────────
    shared_classes = {
        "estleg:LegalPart", "estleg:Provision", "estleg:Section",
        "estleg:LegalConcept",
        "https://data.riik.ee/ontology/estleg#",
        "https://data.riik.ee/ontology/estleg#LegalPart",
        "https://data.riik.ee/ontology/estleg#Chapter",
        "https://data.riik.ee/ontology/estleg#Division",
        "https://data.riik.ee/ontology/estleg#Section",
        "https://data.riik.ee/ontology/estleg#LegalConcept",
    }

    for file_group, dupe_ids in file_group_dupes.items():
        # Filter out shared ontology classes
        dupe_ids = [d for d in dupe_ids if d not in shared_classes]
        if not dupe_ids:
            continue

        # Determine if this is a same-law multipart overlap or different-law collision
        base_names = set()
        for f in file_group:
            base = re.sub(r"_osa\d+(-\d+)?_peep\.json", "", f).replace("_peep.json", "")
            base_names.add(base)

        is_same_law = len(base_names) == 1

        if is_same_law:
            # ─── Multipart osa overlaps ──────────────────────────────────
            # For these, add osa number to the IDs in the file that doesn't already have it
            for dupe_id in dupe_ids:
                for fname in file_group:
                    # Extract osa number from filename
                    osa_match = re.search(r"_osa(\d+(-\d+)?)", fname)
                    if not osa_match:
                        continue
                    osa_nr = osa_match.group(1)

                    # Check if this ID already has osa disambiguation
                    if f"_osa{osa_nr}" in dupe_id or f"osa{osa_nr}" in dupe_id:
                        continue

                    if "_Par_" in dupe_id:
                        # estleg:AOS_Par_70 -> estleg:AOS_Osa11_Par_70
                        prefix, par_part = dupe_id.split("_Par_", 1)
                        new_id = f"{prefix}_Osa{osa_nr}_Par_{par_part}"
                        remap[fname][dupe_id] = new_id
                    elif "Cluster_" in dupe_id:
                        # estleg:Cluster_AsjaoigusteKaitse -> estleg:Cluster_AOS_Osa5_AsjaoigusteKaitse
                        # Need to insert osa number
                        new_id = dupe_id.replace("estleg:Cluster_", f"estleg:Cluster_Osa{osa_nr}_")
                        remap[fname][dupe_id] = new_id
        else:
            # ─── Different-law abbreviation collisions ───────────────────
            # Each file gets a unique prefix based on its filename slug
            # Use progressively longer prefixes until all files in the group are unique
            file_prefixes: dict[str, str] = {}
            for fname in file_group:
                slug = fname.replace("_peep.json", "")
                # Try progressively longer prefixes
                for length in (50, 60, 70, 80, len(slug)):
                    candidate = sanitize_id(slug[:length])
                    # Check it doesn't collide with other files in this group
                    if candidate not in file_prefixes.values():
                        file_prefixes[fname] = candidate
                        break
                else:
                    # If all lengths collide, append a counter
                    base = sanitize_id(slug)
                    counter = 2
                    candidate = f"{base}_{counter}"
                    while candidate in file_prefixes.values():
                        counter += 1
                        candidate = f"{base}_{counter}"
                    file_prefixes[fname] = candidate

            for fname in file_group:
                new_prefix = file_prefixes[fname]

                for dupe_id in dupe_ids:
                    if dupe_id in remap[fname]:
                        continue  # Already remapped

                    if "_Par_" in dupe_id:
                        old_prefix = dupe_id.replace("estleg:", "").split("_Par_")[0]
                        par_part = dupe_id.split("_Par_", 1)[1]
                        new_id = f"estleg:{new_prefix}_Par_{par_part}"
                        if new_id != dupe_id:
                            remap[fname][dupe_id] = new_id

                    elif "_Map_2026" in dupe_id:
                        new_id = f"estleg:{new_prefix}_Map_2026"
                        if new_id != dupe_id:
                            remap[fname][dupe_id] = new_id

                    elif dupe_id.startswith("estleg:Cluster_"):
                        # estleg:Cluster_Muugipiirangud -> estleg:Cluster_{new_prefix}_Muugipiirangud
                        rest = dupe_id.replace("estleg:Cluster_", "")
                        # Remove old prefix if present
                        old_prefix_candidates = set()
                        for other_dupe in dupe_ids:
                            if "_Par_" in other_dupe:
                                old_prefix_candidates.add(
                                    other_dupe.replace("estleg:", "").split("_Par_")[0]
                                )
                        for old_p in old_prefix_candidates:
                            if rest.startswith(old_p + "_"):
                                rest = rest[len(old_p) + 1:]
                                break
                        new_id = f"estleg:Cluster_{new_prefix}_{rest}"
                        if new_id != dupe_id:
                            remap[fname][dupe_id] = new_id

                    elif dupe_id.startswith("estleg:LegalProvision_"):
                        # estleg:LegalProvision_alkoholiseadus -> estleg:LegalProvision_{slug}
                        new_id = f"estleg:LegalProvision_{new_prefix}"
                        if new_id != dupe_id:
                            remap[fname][dupe_id] = new_id

    return dict(remap)


def apply_remap_to_file(filepath: str, id_map: dict[str, str]) -> int:
    """Apply ID remapping to a single file. Returns count of replacements made."""
    with open(filepath, "r", encoding="utf-8") as f:
        doc = json.load(f)

    count = 0
    graph = doc.get("@graph", [])

    for node in graph:
        # Remap @id
        if node.get("@id") in id_map:
            node["@id"] = id_map[node["@id"]]
            count += 1

        # Remap all string values and @id references in properties
        for key in list(node.keys()):
            val = node[key]
            if isinstance(val, str) and val in id_map:
                node[key] = id_map[val]
                count += 1
            elif isinstance(val, dict) and val.get("@id") in id_map:
                val["@id"] = id_map[val["@id"]]
                count += 1
            elif isinstance(val, list):
                for i, item in enumerate(val):
                    if isinstance(item, str) and item in id_map:
                        val[i] = id_map[item]
                        count += 1
                    elif isinstance(item, dict) and item.get("@id") in id_map:
                        item["@id"] = id_map[item["@id"]]
                        count += 1

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")

    return count


def update_cross_references(remap: dict[str, dict[str, str]]) -> int:
    """Update references in OTHER files that point to remapped IDs."""
    # Build a global old->new map and track which file the old ID belongs to
    global_remap: dict[str, str] = {}
    id_home_file: dict[str, str] = {}  # old_id -> filename it was defined in

    for fname, id_map in remap.items():
        for old_id, new_id in id_map.items():
            global_remap[old_id] = new_id
            id_home_file[old_id] = fname

    if not global_remap:
        return 0

    # Scan all files for references to remapped IDs
    all_files = sorted(glob.glob(str(KRR_DIR / "*_peep.json")))
    total_updated = 0

    for filepath in all_files:
        fname = os.path.basename(filepath)

        with open(filepath, "r", encoding="utf-8") as f:
            doc = json.load(f)

        changed = False
        graph = doc.get("@graph", [])

        for node in graph:
            for key in list(node.keys()):
                if key == "@id":
                    continue  # Don't remap @id here - only references
                val = node[key]
                if isinstance(val, str) and val in global_remap:
                    # Only update if the reference is to a DIFFERENT file's ID
                    # and the reference matches the home file's remapped ID
                    home = id_home_file.get(val)
                    if home and home != fname:
                        node[key] = global_remap[val]
                        changed = True
                        total_updated += 1
                elif isinstance(val, dict) and val.get("@id") in global_remap:
                    home = id_home_file.get(val["@id"])
                    if home and home != fname:
                        val["@id"] = global_remap[val["@id"]]
                        changed = True
                        total_updated += 1
                elif isinstance(val, list):
                    for item in val:
                        if isinstance(item, str) and item in global_remap:
                            home = id_home_file.get(item)
                            if home and home != fname:
                                idx = val.index(item)
                                val[idx] = global_remap[item]
                                changed = True
                                total_updated += 1
                        elif isinstance(item, dict) and item.get("@id") in global_remap:
                            home = id_home_file.get(item["@id"])
                            if home and home != fname:
                                item["@id"] = global_remap[item["@id"]]
                                changed = True
                                total_updated += 1

        if changed:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
                f.write("\n")

    return total_updated


def main():
    print("=" * 70)
    print("Fix Duplicate @id Values Across JSON-LD Files")
    print("=" * 70)

    # Step 1: Detect
    print("\n[1/5] Detecting duplicates...")
    dupes = detect_duplicates()
    print(f"  Found {len(dupes)} duplicate @id values")

    if not dupes:
        print("\n  No duplicates found!")
        return

    # Step 2: Categorize
    shared_classes = {
        "estleg:LegalPart", "estleg:Provision", "estleg:Section",
        "estleg:LegalConcept",
        "https://data.riik.ee/ontology/estleg#",
        "https://data.riik.ee/ontology/estleg#LegalPart",
        "https://data.riik.ee/ontology/estleg#Chapter",
        "https://data.riik.ee/ontology/estleg#Division",
        "https://data.riik.ee/ontology/estleg#Section",
        "https://data.riik.ee/ontology/estleg#LegalConcept",
    }
    real_dupes = {k: v for k, v in dupes.items() if k not in shared_classes}
    print(f"  Shared ontology classes (no fix needed): {len(dupes) - len(real_dupes)}")
    print(f"  Real duplicates to fix: {len(real_dupes)}")

    # Step 3: Build remap
    print("\n[2/5] Building remap table...")
    remap = build_remap_table(dupes)
    total_remaps = sum(len(m) for m in remap.values())
    print(f"  Files to modify: {len(remap)}")
    print(f"  Total ID remappings: {total_remaps}")

    if not remap:
        print("\n  No remappings needed!")
        return

    # Step 4: Apply remaps to files
    print("\n[3/5] Applying ID remaps to files...")
    for fname, id_map in sorted(remap.items()):
        filepath = str(KRR_DIR / fname)
        count = apply_remap_to_file(filepath, id_map)
        print(f"  {fname}: {count} replacements ({len(id_map)} IDs remapped)")

    # Step 5: Update cross-references
    print("\n[4/5] Updating cross-file references...")
    xref_count = update_cross_references(remap)
    print(f"  Updated {xref_count} cross-file references")

    # Step 6: Verify
    print("\n[5/5] Verifying fix...")
    remaining = detect_duplicates()
    remaining_real = {k: v for k, v in remaining.items() if k not in shared_classes}
    print(f"  Remaining shared ontology class dupes: {len(remaining) - len(remaining_real)}")
    print(f"  Remaining real duplicates: {len(remaining_real)}")

    if remaining_real:
        print("\n  REMAINING DUPLICATES:")
        for k, v in sorted(remaining_real.items()):
            print(f"    {k}: {v}")

    print("\n" + "=" * 70)
    print(f"DONE: Fixed {total_remaps} duplicate IDs, updated {xref_count} cross-references")
    if remaining_real:
        print(f"WARNING: {len(remaining_real)} duplicates remain")
        sys.exit(1)
    else:
        print("SUCCESS: All duplicates resolved")
    print("=" * 70)


if __name__ == "__main__":
    main()
