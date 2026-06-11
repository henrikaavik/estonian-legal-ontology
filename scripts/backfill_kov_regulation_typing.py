#!/usr/bin/env python3
"""Backfill: strip the stray state-regulation @type from KOV roots (#424).

`estleg:MunicipalRegulation` was historically declared
``rdfs:subClassOf estleg:NationalRegulation`` and the KOV regulation
generator dual-typed every municipal act node with BOTH classes so that
the shared domestic-regulation SHACL constraints applied without OWL
inference. Issue #267 fixed the generator (``classify_issuer`` now returns
``["estleg:MunicipalRegulation"]`` only for KOV), and issue #424 fixes the
T-Box hierarchy (``MunicipalRegulation``/``NationalRegulation`` are now
sibling subclasses of the new ``estleg:DomesticRegulation`` superclass — no
entailment path makes a municipal regulation a state regulation).

The committed KOV artifacts, however, predate the #267 generator fix: all
~11k ``regulations/kov/**/*_peep.json`` roots still carry the contradictory
``estleg:NationalRegulation`` (and, defensively, the state-only subclasses
``estleg:GovernmentRegulation`` / ``estleg:MinisterialRegulation`` should one
ever have leaked in). This backfill removes those stray state-regulation
types from the KOV act (``owl:Ontology`` + ``estleg:MunicipalRegulation``)
root node, leaving it typed exactly as a fresh generator run would emit it:
``[owl:Ontology, estleg:Act, estleg:MunicipalRegulation]``.

Why a backfill rather than a regen
----------------------------------
The fix is *purely* a removal of contradictory type triples that the
current generator no longer emits — no field is derived, fetched, or
recomputed. Re-running the regulation generator corpus-wide would also work
but is far heavier (re-parses every Riigi Teataja XML) and is the
orchestrator's job; this targeted, deterministic, idempotent edit mirrors
``scripts/backfill_eu_provenance.py``.

Notes
-----
* Scope: only the KOV act ROOT node is touched — the node carrying both
  ``owl:Ontology`` and ``estleg:MunicipalRegulation``. KOV provisions
  (``estleg:KovProvision``) never carry a state-regulation type, so they are
  left untouched. Files with no such root node (e.g. an accidental index
  file matched by the glob) are skipped without error.
* ``estleg:MunicipalRegulation`` and ``estleg:Act`` are NEVER removed — they
  are the correct municipal typing. ``estleg:DomesticRegulation`` is the new
  T-Box superclass and is intentionally NOT added to instances: membership
  is conferred by RDFS entailment over the T-Box, and the shared SHACL
  coverage is provided by adding ``estleg:MunicipalRegulation`` as a second
  ``sh:targetClass`` on the domestic-regulation shapes (inference=none safe).
* Deterministic: the surviving ``@type`` list is re-sorted with
  ``sorted(set(...))`` so the on-disk order matches what
  ``enrich_kov_layer1`` / the generator produce, and a re-run is byte-stable.
* Idempotent: a root that already lacks every state-regulation type is a
  no-op (no write, so mtime does not churn) — safe to run after the KOV
  enrichment pipeline settles.
* Atomic writes via ``estleg_common.save_json`` (tempfile + ``os.replace``).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from estleg_common import REPO_ROOT, save_json

# State-level regulation types that must NOT appear on a municipal (KOV) act
# root. ``estleg:NationalRegulation`` is the documented dual-typing artifact
# (issue #267); the two concrete state subclasses are included defensively so
# any future leak is also scrubbed. ``estleg:MunicipalRegulation`` and
# ``estleg:Act`` are deliberately absent — they are the correct typing.
STRAY_STATE_TYPES: frozenset[str] = frozenset(
    {
        "estleg:NationalRegulation",
        "estleg:GovernmentRegulation",
        "estleg:MinisterialRegulation",
    }
)


def _node_types(node: dict) -> list[str]:
    t = node.get("@type", [])
    return [t] if isinstance(t, str) else list(t)


def is_kov_root(node: dict) -> bool:
    """True iff this node is a KOV regulation act root.

    The root is the ``owl:Ontology`` metadata node that also carries
    ``estleg:MunicipalRegulation`` (the per-file act node). KOV provisions
    and annexes lack ``owl:Ontology``; state regulations lack
    ``estleg:MunicipalRegulation``; so this predicate isolates exactly the
    KOV act roots.
    """
    types = set(_node_types(node))
    return "owl:Ontology" in types and "estleg:MunicipalRegulation" in types


def backfill_node(node: dict) -> bool:
    """Strip stray state-regulation types from one KOV root in place.

    Returns True iff the node was modified. Non-root nodes and roots that
    carry no stray state type are left untouched (the ``intersection``
    guard makes this idempotent).
    """
    if not is_kov_root(node):
        return False
    types = _node_types(node)
    stray = STRAY_STATE_TYPES.intersection(types)
    if not stray:
        return False
    # Drop every stray state type; re-sort the survivors for a stable,
    # generator-matching on-disk order.
    node["@type"] = sorted(t for t in set(types) if t not in STRAY_STATE_TYPES)
    return True


def process_file(path: Path, dry_run: bool) -> tuple[int, int]:
    """Backfill one KOV peep file. Returns (roots_changed, roots_total).

    ``roots_total`` counts KOV act roots in the file (normally exactly 1);
    ``roots_changed`` counts those that had a stray state type removed.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        return 0, 0
    graph = doc.get("@graph", [])
    roots = [n for n in graph if isinstance(n, dict) and is_kov_root(n)]
    changed = sum(1 for n in roots if backfill_node(n))
    if changed and not dry_run:
        save_json(path, doc)
    return changed, len(roots)


def collect_kov_peep_files(kov_dir: Path) -> list[Path]:
    """Sorted list of KOV regulation peep files (the isKov=true roots).

    ``REGULATIONS_KOV_INDEX.json`` is not a ``*_peep.json`` file, so the
    glob already excludes it; the explicit filter is a belt-and-braces
    guard mirroring the other KOV consumers.
    """
    return sorted(
        p
        for p in kov_dir.glob("**/*_peep.json")
        if p.is_file() and p.name != "REGULATIONS_KOV_INDEX.json"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kov-dir",
        type=Path,
        default=REPO_ROOT / "krr_outputs" / "regulations" / "kov",
        help="KOV regulations root (default: <repo>/krr_outputs/regulations/kov).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing any file.",
    )
    args = parser.parse_args(argv)

    print("=" * 70)
    print("Backfill KOV regulation typing — strip stray state-regulation @type (#424)")
    print("=" * 70)

    files = collect_kov_peep_files(args.kov_dir)
    if not files:
        print(f"  No KOV peep files found under {args.kov_dir}.")
        return 0

    files_changed = 0
    roots_changed = 0
    roots_total = 0
    for path in files:
        changed, total = process_file(path, args.dry_run)
        roots_total += total
        roots_changed += changed
        if changed:
            files_changed += 1

    verb = "would scrub" if args.dry_run else "scrubbed"
    print(f"  scanned {len(files)} KOV peep files ({roots_total} act roots)")
    print(f"  {verb} stray state-regulation @type from {roots_changed} root(s) "
          f"across {files_changed} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
