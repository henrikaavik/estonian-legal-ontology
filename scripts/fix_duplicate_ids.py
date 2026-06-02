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
import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from estleg_common import iter_peep_files

KRR_DIR = Path(__file__).resolve().parents[1] / "krr_outputs"


def _atomic_write_json(filepath: str | Path, doc: object) -> None:
    """Write ``doc`` to ``filepath`` as UTF-8 JSON atomically (#280).

    Serialises to a sibling tempfile in the destination directory and then
    ``os.replace``s it into place. A crash, ``KeyboardInterrupt``, or
    disk-full mid-write therefore leaves the original file fully intact (or
    the new file fully written) — never a truncated/empty hybrid. The
    tempfile shares the destination directory so the rename stays on one
    filesystem; on failure it is unlinked best-effort before the original
    exception propagates.
    """
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    tmp_name = tmp.name
    try:
        with tmp as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise

_ESTONIAN_TRANSLITERATION = {
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
}

# Single-integer osa extractor from a peep filename (#279). Matches ONLY a
# single integer terminated by ``_`` or end-of-stem — i.e. ``..._osa11_peep``
# yields ``11`` but range-form ``..._osa6-13_peep`` does NOT match, because
# ``Osa6-13`` would produce a hyphenated local name that fails the canonical
# ``estleg:`` IRI grammar (no hyphen allowed). This mirrors
# ``migrate_multipart_iri_scheme.OSA_FILENAME_RE``'s single-integer policy.
SINGLE_OSA_RE = re.compile(r"_osa(\d+)(?:_|$)")
# Detects a range-form osa segment (``osa6-13``) anywhere in a filename so the
# same-law branch can skip it and surface it for human review rather than
# minting a malformed ``estleg:..._Osa6-13_Par_n`` IRI.
RANGE_OSA_RE = re.compile(r"_osa\d+-\d+(?:_|$|\.)")


def _is_range_osa_file(fname: str) -> bool:
    """Return True if ``fname`` carries a range-form osa segment (``osa6-13``).

    Range-form osa files cannot be disambiguated into a single ``Osa<N>``
    component, so the same-law dedup branch skips them (#279), matching the
    policy in ``migrate_multipart_iri_scheme``.
    """
    return RANGE_OSA_RE.search(fname) is not None


def sanitize_id(value: str) -> str:
    s = value.replace(" ", "_")
    for old, new in _ESTONIAN_TRANSLITERATION.items():
        s = s.replace(old, new)
    s = re.sub(r"[^0-9A-Za-z_]", "", s)
    return s or "Unknown"


def detect_duplicates() -> dict[str, list[str]]:
    """Find all @id values that appear in multiple files.

    Scans the full pipeline corpus via ``iter_peep_files()`` — top-level
    laws PLUS ``regulations/riik/`` and ``regulations/kov/**`` (#332).
    The previous ``KRR_DIR.glob('*_peep.json')`` saw only top-level law
    files, so regulation-side duplicate @ids (and the regulation half of
    any law/regulation collision) were invisible to the deduplicator.
    """
    id_files: dict[str, list[str]] = defaultdict(list)
    for f in iter_peep_files():
        with open(f, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        fname = f.name
        for node in doc.get("@graph", []):
            nid = node.get("@id", "")
            if nid:
                id_files[nid].append(fname)
    return {k: v for k, v in id_files.items() if len(v) > 1}


def get_file_prefix(filepath: str) -> str | None:
    """Extract the IRI prefix used in a file's provision/cluster IDs.

    Returns the first non-empty prefix found by scanning `_Par_` /
    `_Map_2026` IDs in the file's `@graph`. The previous implementation
    contained an unused dead-code fallback (issue #159); that block has
    been removed and the function now returns ``None`` directly when no
    recognisable id pattern is present.
    """
    with open(filepath, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    for node in doc.get("@graph", []):
        nid = node.get("@id", "")
        if "_Par_" in nid:
            return nid.replace("estleg:", "").split("_Par_")[0]
        if "_Map_2026" in nid:
            return nid.replace("estleg:", "").split("_Map_2026")[0]
    return None


def build_remap_table(
    dupes: dict[str, list[str]],
    skipped_range_files: set[str] | None = None,
) -> dict[str, dict[str, str]]:
    """Build a per-file ID remap table: {filename: {old_id: new_id}}.

    When ``skipped_range_files`` is provided, the names of any range-form
    osa files (``..._osa6-13_peep.json``) encountered in the same-law
    branch are recorded into it. Such files are intentionally NOT remapped
    (#279): a range osa cannot be collapsed to a single ``Osa<N>`` segment
    without minting a hyphenated — and therefore invalid — IRI, so they are
    surfaced for human review instead (matching the policy in
    ``migrate_multipart_iri_scheme``).
    """
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
                    # Range-form osa files (``osa6-13``) cannot be collapsed
                    # to a single ``Osa<N>`` component without producing a
                    # hyphenated, invalid IRI — skip and record them (#279).
                    if _is_range_osa_file(fname):
                        if skipped_range_files is not None:
                            skipped_range_files.add(fname)
                        continue
                    # Extract a SINGLE-integer osa number from the filename.
                    # The trailing ``(?:_|$)`` boundary rejects range forms
                    # so ``osa6-13`` never yields ``6-13`` here (#279).
                    osa_match = SINGLE_OSA_RE.search(fname)
                    if not osa_match:
                        continue
                    osa_nr = osa_match.group(1)

                    # Check if this ID already has osa disambiguation
                    if f"_Osa{osa_nr}_" in dupe_id or f"Osa{osa_nr}_" in dupe_id:
                        continue

                    if "_Par_" in dupe_id:
                        # estleg:AOS_Par_70 -> estleg:AOS_Osa11_Par_70
                        prefix, par_part = dupe_id.split("_Par_", 1)
                        new_id = f"{prefix}_Osa{osa_nr}_Par_{par_part}"
                        if new_id != dupe_id:
                            remap[fname][dupe_id] = new_id
                    elif "Cluster_" in dupe_id:
                        # estleg:Cluster_AsjaoigusteKaitse -> estleg:Cluster_Osa5_AsjaoigusteKaitse
                        # Need to insert osa number
                        new_id = dupe_id.replace("estleg:Cluster_", f"estleg:Cluster_Osa{osa_nr}_")
                        if new_id != dupe_id:
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
                        # Idempotency no-op guard (#279): if the cluster id
                        # already carries THIS file's new prefix (e.g. it was
                        # disambiguated on a prior run), re-prepending it would
                        # double the slug
                        # (``Cluster_alpha_seadus_alpha_seadus_X``). Skip.
                        if rest == new_prefix or rest.startswith(new_prefix + "_"):
                            continue
                        # Remove an old prefix if present so the slug isn't
                        # stacked on top of a stale one. Strippable prefixes
                        # are derived structurally from (a) the ``_Par_``
                        # siblings in this collision group and (b) every other
                        # file's assigned slug-prefix — the latter covers a
                        # cluster that already embeds a sibling file's slug
                        # from an earlier run.
                        old_prefix_candidates: set[str] = set()
                        for other_dupe in dupe_ids:
                            if "_Par_" in other_dupe:
                                old_prefix_candidates.add(
                                    other_dupe.replace("estleg:", "").split("_Par_")[0]
                                )
                        old_prefix_candidates.update(file_prefixes.values())
                        old_prefix_candidates.discard(new_prefix)
                        # Strip the LONGEST matching prefix first so a short
                        # candidate doesn't shadow a longer, more specific one.
                        for old_p in sorted(old_prefix_candidates, key=len, reverse=True):
                            if rest.startswith(old_p + "_"):
                                rest = rest[len(old_p) + 1:]
                                break
                        new_id = f"estleg:Cluster_{new_prefix}_{rest}"
                        if new_id != dupe_id:
                            remap[fname][dupe_id] = new_id

                    elif dupe_id.startswith("estleg:LegalProvision_"):
                        # estleg:LegalProvision_AS -> estleg:LegalProvision_{slug}
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

    _atomic_write_json(filepath, doc)

    return count


def _transitive_closure(remap: dict[str, str]) -> dict[str, str]:
    """Apply chained renames until none of the values is also a key.

    Computes the fixed point of the rename map so that an old-id chain
    `A -> B -> C` collapses to `A -> C, B -> C`. This eliminates the
    bug (#159) where a partial substitution would leave references
    pointing at a now-stale intermediate id.

    Asserts no cycles (which would be a corpus authoring bug). Stops
    after a generous fixed-point cap to defend against pathological
    inputs.
    """
    closed = dict(remap)
    for _ in range(64):
        next_closed: dict[str, str] = {}
        for key, value in closed.items():
            seen = {key}
            current = value
            while current in closed and current not in seen:
                seen.add(current)
                current = closed[current]
                if current in seen and current != value:
                    raise RuntimeError(
                        f"Cycle in cross-file rename map starting at {key!r}"
                    )
            next_closed[key] = current
        if next_closed == closed:
            break
        closed = next_closed
    # Sanity check: no key may also be a value of the closed map.
    values = set(closed.values())
    overlap = set(closed) & values
    if overlap:
        raise RuntimeError(
            f"Cross-file rename map is not closed: {sorted(overlap)} appear "
            f"as both keys and values"
        )
    return closed


def update_cross_references(remap: dict[str, dict[str, str]]) -> int:
    """Update references in OTHER files that point to remapped IDs.

    Three correctness fixes versus the previous implementation
    (issues #159, #332):

    1. **Index-based list iteration.** Previously, `val.index(item)`
       was used while iterating `val`, which silently misbehaves on
       lists with duplicate string entries (it always returned the
       first match's index). We now use ``for i, item in
       enumerate(val):`` and assign by index.

    2. **Transitive closure.** Chained renames (A -> B and B -> C)
       are now collapsed to (A -> C, B -> C) before being applied so
       references never resolve to a stale intermediate id. We also
       assert the closed map has no key that is also a value.

    3. **Full-corpus scan (#332).** We rewrite references across the
       entire pipeline corpus via ``iter_peep_files()`` — top-level
       laws PLUS ``regulations/riik/`` and ``regulations/kov/**`` —
       not just top-level ``*_peep.json``. Regulations carry
       ``estleg:references`` to law provisions, so a remapped law IRI
       must rewrite those regulation-side refs too; otherwise dedup
       leaves dangling cross-references in the regulation subcorpora.
    """
    # Build a global old->new map and track which file the old ID belongs to
    raw_remap: dict[str, str] = {}
    id_home_file: dict[str, str] = {}  # old_id -> filename it was defined in

    for fname, id_map in remap.items():
        for old_id, new_id in id_map.items():
            raw_remap[old_id] = new_id
            id_home_file[old_id] = fname

    if not raw_remap:
        return 0

    global_remap = _transitive_closure(raw_remap)

    # Scan ALL corpus files (laws + riik + kov regulations) for references
    # to remapped IDs. ``iter_peep_files()`` returns an already-sorted list
    # spanning the regulation subcorpora the old narrow glob missed (#332).
    all_files = iter_peep_files()
    total_updated = 0

    for filepath in all_files:
        fname = filepath.name

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
                    # Iterate by index so we never trip over `list.index()`
                    # returning the first occurrence on duplicates (#159).
                    for i, item in enumerate(val):
                        if isinstance(item, str) and item in global_remap:
                            home = id_home_file.get(item)
                            if home and home != fname:
                                val[i] = global_remap[item]
                                changed = True
                                total_updated += 1
                        elif isinstance(item, dict) and item.get("@id") in global_remap:
                            home = id_home_file.get(item["@id"])
                            if home and home != fname:
                                item["@id"] = global_remap[item["@id"]]
                                changed = True
                                total_updated += 1

        if changed:
            _atomic_write_json(filepath, doc)

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
    skipped_range_files: set[str] = set()
    remap = build_remap_table(dupes, skipped_range_files=skipped_range_files)
    total_remaps = sum(len(m) for m in remap.values())
    print(f"  Files to modify: {len(remap)}")
    print(f"  Total ID remappings: {total_remaps}")
    if skipped_range_files:
        print(
            "  Skipped range-form osa files (cannot mint single Osa<N> IRI; "
            "needs manual review):"
        )
        for fname in sorted(skipped_range_files):
            print(f"    {fname}")

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
