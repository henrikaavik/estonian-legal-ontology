#!/usr/bin/env python3
"""Retarget malformed ``_Osa`` enabling-law links to the act root (#585).

Over all 19,664 ``estleg:issuedUnder`` targets in the regulation peeps,
44 use a ``<law>_Osa<part>_<startpar>_<endpar>`` IRI (e.g.
``estleg:volaoigusseadus_Osa1_1_207``). That form is the IRI of a single
**``estleg:Part``** of a multipart law, not the act itself — every other
``issuedUnder`` target is the canonical act-root ``…_Map_2026`` short
code. An enabling-law reference should point at the *act*, so these 44
need normalising to the act's ``_Map_2026`` node.

This is a pure offline retarget. For each malformed target the script
maps the ``_Osa`` base-prefix to the law's canonical short code and
rewrites the reference to ``estleg:<code>_Map_2026`` — **but only when
that act-root node actually exists in the corpus**. A rewrite is never
written for an act whose root is absent (it would just replace one
dangling IRI with another); those targets are left untouched and
reported as unresolved.

Resolution table (the multipart-law base → canonical short code). Each
entry is gated at run time against the set of real ``…_Map_2026`` Act
roots discovered in ``krr_outputs/*_peep.json``:

    tsiviilkohtumenetluse_seadustik → TsMS   (TsMS_Map_2026 present → retargets)
    volaoigusseadus                 → VÕS    (no VÕS_Map_2026 root → left, reported)
    AOS                             → AÕS    (no AÕS_Map_2026 root → left, reported)
    KARIST_2                        → KarS   (no KarS_Map_2026 root → left, reported)

For the three laws whose base act root is missing, only their *Part*
nodes (and the *rakendamise seadus* variants ``VOSRS``/``AÕSRS``/
``KarSRS`` — different acts) are in the corpus, so there is no correct
``_Map_2026`` target; retargeting them is an ingestion-gap item, not a
link-normalisation one, and is deliberately out of scope here.

Safety / scope:
  * Only ``estleg:issuedUnder`` object refs matching the strict
    ``^estleg:<base>_Osa\\d+_\\d+_\\d+$`` shape are considered; ordinary
    ``…_Map_2026`` targets and any other predicate are never touched.
  * Every rewrite is existence-gated: the destination ``_Map_2026`` node
    must be a real Act/Law root in the corpus, else the target is left.
  * Idempotent: once a target is the canonical ``_Map_2026`` IRI it no
    longer matches the malformed pattern, so a re-run reports 0 changes.
  * Atomic writes via ``estleg_common.save_json`` (tempfile + os.replace).
  * ``--dry-run`` reports counts without writing.

Run (offline, idempotent):

    .venv/bin/python scripts/retarget_osa_issued_under.py
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from estleg_common import iter_peep_files, save_json

ISSUED_UNDER_KEY = "estleg:issuedUnder"

# Strict shape of the malformed Part-IRI used as an enabling-law target:
# estleg:<base>_Osa<part>_<startpar>_<endpar>. The base is captured so we
# can map it to the law's canonical act-root short code.
OSA_TARGET_RE = re.compile(r"^estleg:(?P<base>.+)_Osa\d+_\d+_\d+$")

# Multipart-law base-prefix → canonical act-root short code. These encode
# the law's standard abbreviation; the resolver only *uses* an entry when
# the resulting estleg:<code>_Map_2026 node exists as a real Act/Law root
# in the corpus (so a missing base act can never produce a dangling
# rewrite). Derived from the four distinct malformed targets present.
OSA_BASE_TO_SHORTCODE: dict[str, str] = {
    "tsiviilkohtumenetluse_seadustik": "TsMS",
    "volaoigusseadus": "VÕS",
    "AOS": "AÕS",
    "KARIST_2": "KarS",
}


def _node_types(node: dict) -> list[str]:
    t = node.get("@type", [])
    return [t] if isinstance(t, str) else list(t)


def discover_act_roots(peep_files: list[Path]) -> set[str]:
    """Return the set of canonical ``…_Map_2026`` Act/Law root @ids.

    A canonical act root is a node whose ``@id`` ends in ``_Map_2026``,
    contains no ``_Osa`` segment, is **not** typed ``estleg:Part`` and is
    typed ``estleg:Act`` or ``estleg:Law``. This is the existence gate
    every retarget is checked against.
    """
    roots: set[str] = set()
    for path in peep_files:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for node in doc.get("@graph", []):
            if not isinstance(node, dict):
                continue
            iri = node.get("@id", "")
            if not (isinstance(iri, str) and iri.endswith("_Map_2026")):
                continue
            if "_Osa" in iri:
                continue
            types = _node_types(node)
            if "estleg:Part" in types:
                continue
            if "estleg:Act" in types or "estleg:Law" in types:
                roots.add(iri)
    return roots


def resolve_target(ref: str, act_roots: set[str]) -> str | None:
    """Resolve a malformed ``_Osa`` enabling-law IRI to its act root.

    Returns the canonical ``estleg:<code>_Map_2026`` IRI when ``ref``
    matches the malformed Part-IRI shape, its base maps to a known short
    code, and that act root exists in ``act_roots``. Returns ``None`` for
    a well-formed target, an unknown base, or a base whose act root is
    absent (those are left in place and reported as unresolved).
    """
    m = OSA_TARGET_RE.match(ref)
    if not m:
        return None
    code = OSA_BASE_TO_SHORTCODE.get(m.group("base"))
    if not code:
        return None
    canonical = f"estleg:{code}_Map_2026"
    return canonical if canonical in act_roots else None


def retarget_doc(doc: dict, act_roots: set[str], unresolved: dict[str, int]) -> int:
    """Retarget malformed ``issuedUnder`` refs in one doc, in place.

    Returns the number of object refs rewritten. Unresolved malformed
    targets (act root absent / unknown base) are tallied into
    ``unresolved`` by IRI and left untouched.
    """
    graph = doc.get("@graph", [])
    if not isinstance(graph, list):
        return 0
    changed = 0
    for node in graph:
        if not isinstance(node, dict):
            continue
        value = node.get(ISSUED_UNDER_KEY)
        if value is None:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            if not isinstance(item, dict):
                continue
            ref = item.get("@id")
            if not isinstance(ref, str) or not OSA_TARGET_RE.match(ref):
                continue
            canonical = resolve_target(ref, act_roots)
            if canonical is None:
                unresolved[ref] = unresolved.get(ref, 0) + 1
                continue
            item["@id"] = canonical
            changed += 1
    return changed


def process_file(
    path: Path, act_roots: set[str], unresolved: dict[str, int], dry_run: bool
) -> int:
    """Retarget one peep file. Returns the number of refs rewritten."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    changed = retarget_doc(doc, act_roots, unresolved)
    if changed and not dry_run:
        save_json(path, doc)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing any file.",
    )
    args = parser.parse_args(argv)

    # Discover act roots across the whole corpus (laws + regulations) so the
    # existence gate sees every canonical _Map_2026 node.
    all_peeps = iter_peep_files()
    act_roots = discover_act_roots(all_peeps)

    # Only the regulation peeps carry issuedUnder; scan those for the rewrite.
    reg_peeps = iter_peep_files(include_laws=False)

    print("=" * 70)
    print(f"Retarget malformed {ISSUED_UNDER_KEY} _Osa Part IRIs to act root (#585)")
    print(f"  Act roots discovered:    {len(act_roots)}")
    print(f"  Regulation peeps scanned: {len(reg_peeps)}")
    print("=" * 70)

    unresolved: dict[str, int] = {}
    files_changed = 0
    refs_changed = 0
    for path in reg_peeps:
        changed = process_file(path, act_roots, unresolved, args.dry_run)
        if changed:
            files_changed += 1
            refs_changed += changed

    verb = "would retarget" if args.dry_run else "retargeted"
    print(f"\n  Files {verb}: {files_changed}")
    print(f"  issuedUnder refs {verb}: {refs_changed}")
    if unresolved:
        total_unresolved = sum(unresolved.values())
        print(f"\n  Unresolved (left in place, no act root): {total_unresolved}")
        for ref, count in sorted(unresolved.items(), key=lambda kv: -kv[1]):
            print(f"    {count:4d}  {ref}")
    else:
        print("\n  Unresolved: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
