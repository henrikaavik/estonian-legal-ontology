#!/usr/bin/env python3
"""Re-point national / EFTA decisions mis-stamped as Court of Justice (#575).

The CURIA EU-court corpus (``krr_outputs/curia/``) was mined before the
``generate_eu_court_decisions.py`` SPARQL sweep gained its sector filter
(#343). As a result the *already committed* ``curia_other_peep.json`` carries
58 decisions that are **not** Court-of-Justice (CJEU) cases, yet all 58 are
typed ``estleg:EUCourtDecision`` with ``estleg:euCourt =
estleg:EUCourt_CourtOfJustice``. A primary consumer query ("CJEU case law")
therefore returns national + EFTA decisions wearing the wrong court.

The real forum is recoverable from the CELEX **sector** (the first
character of ``estleg:celexNumber``), which is authoritative:

  * sector ``6`` — CJEU case-law (Court of Justice / General Court / Civil
    Service Tribunal). Correct EU-court decisions; left untouched.
  * sector ``8`` — *national* case-law (a member-state court citing EU law,
    e.g. ``82015EE1202(01)`` = Riigikohus, ``82008EE0319(01)`` = Tallinna
    Halduskohus). Re-pointed to ``estleg:EUCourt_NationalCourt``.
  * sector ``E`` — the EFTA Court (e.g. ``E2014J0018`` = Wow air, case
    E-18/14) and EFTA Surveillance Authority applications. Re-pointed to
    ``estleg:EUCourt_EFTACourt`` — exactly what ``classify_from_celex`` now
    emits for sector ``E`` on a fresh run.

This is a surgical, OFFLINE remediation of stale data: the decisions are
**not** deleted (their texts, dates and EUR-Lex links stay), only the
``estleg:euCourt`` edge is corrected so consumers can filter them out of CJEU
statistics. The two sentinel court individuals (``EUCourt_NationalCourt`` and
``EUCourt_EFTACourt``) are injected into ``curia_schema.json`` if absent so
the re-pointed edges resolve (graph closure); both are also emitted by
``generate_eu_court_decisions.generate_schema_nodes`` on a regen, so the
schema stays consistent.

Safety / scope:
  * Only ``estleg:EUCourtDecision`` nodes whose CELEX sector is ``8`` or
    ``E`` are touched; sector-6 (genuine CJEU) nodes are never visited.
  * Idempotent: a node already pointing at the correct sentinel is a no-op,
    so a re-run reports 0 changes.
  * git-LFS pointer files (first line ``version https://git-lfs...``) are
    skipped — they are not JSON and would raise on ``json.loads``.
  * Atomic writes via ``estleg_common.save_json`` (tempfile + os.replace).
  * ``--dry-run`` reports counts without writing.
  * ``curia_combined.jsonld`` is a *generated* aggregate and is intentionally
    NOT edited here; the maintainer rebuilds it once from the corrected peep
    files.

Run (offline, idempotent, expected 58 decisions across the peep files):

    .venv/bin/python scripts/fix_curia_court_contamination.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from estleg_common import REPO_ROOT, save_json

EU_COURT_DECISION_TYPE = "estleg:EUCourtDecision"
EU_COURT_PREDICATE = "estleg:euCourt"
CELEX_PREDICATE = "estleg:celexNumber"

COURT_OF_JUSTICE = "estleg:EUCourt_CourtOfJustice"
NATIONAL_COURT = "estleg:EUCourt_NationalCourt"
EFTA_COURT = "estleg:EUCourt_EFTACourt"

# CELEX sector (first character of the number) -> correct court individual.
# Sector 6 (CJEU) is deliberately absent: those decisions are correct and
# never re-pointed.
SECTOR_TO_COURT = {
    "8": NATIONAL_COURT,
    "E": EFTA_COURT,
}

# Schema individuals to ensure exist so the re-pointed edges resolve. Mirrors
# the ``EU_COURTS`` entries emitted by ``generate_eu_court_decisions`` so a
# regen produces byte-identical nodes.
COURT_INDIVIDUALS = {
    NATIONAL_COURT: {
        "@id": NATIONAL_COURT,
        "@type": ["owl:NamedIndividual", "estleg:EUCourt"],
        "rdfs:label": {"@value": "Liikmesriigi kohus", "@language": "et"},
        "skos:prefLabel": {"@value": "National Court", "@language": "en"},
        "estleg:euCourtCode": "NAT",
    },
    EFTA_COURT: {
        "@id": EFTA_COURT,
        "@type": ["owl:NamedIndividual", "estleg:EUCourt"],
        "rdfs:label": {"@value": "EFTA Kohus", "@language": "et"},
        "skos:prefLabel": {"@value": "EFTA Court", "@language": "en"},
        "estleg:euCourtCode": "EFTA",
    },
}

DEFAULT_CURIA_DIR = REPO_ROOT / "krr_outputs" / "curia"
# Decision corpora to scan. The generated ``curia_combined.jsonld`` aggregate is
# handled separately in main() (same surgical transform) because it is
# network-built and cannot be regenerated offline — the subcorpus-parity gate
# would otherwise flag it as stale.
PEEP_GLOB = "curia_*_peep.json"
SCHEMA_FILENAME = "curia_schema.json"

_LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"


def is_lfs_pointer(path: Path) -> bool:
    """True if ``path`` is an unmaterialised git-LFS pointer (not real JSON).

    A pointer's first line is ``version https://git-lfs...``; parsing it as
    JSON raises, so callers skip it rather than crash in a no-LFS checkout.
    """
    try:
        with path.open(encoding="utf-8") as fh:
            return fh.readline().startswith(_LFS_POINTER_PREFIX)
    except OSError:
        return False


def _node_types(node: dict) -> list[str]:
    t = node.get("@type", [])
    return [t] if isinstance(t, str) else list(t)


def _celex_value(node: dict) -> str | None:
    celex = node.get(CELEX_PREDICATE)
    if isinstance(celex, dict):
        celex = celex.get("@value")
    return celex if isinstance(celex, str) and celex else None


def correct_court(node: dict) -> bool:
    """Re-point one decision node's ``estleg:euCourt`` from the CELEX sector.

    Returns ``True`` iff the node was changed. A node qualifies only when it
    is an ``estleg:EUCourtDecision``, carries a CELEX whose sector maps to a
    correction (8 -> National, E -> EFTA), and is **not already** pointing at
    that correct court (idempotency). Sector-6 (genuine CJEU) and nodes
    without a mapped sector are left untouched.
    """
    if EU_COURT_DECISION_TYPE not in _node_types(node):
        return False
    celex = _celex_value(node)
    if celex is None:
        return False
    target_court = SECTOR_TO_COURT.get(celex[0])
    if target_court is None:
        return False
    current = node.get(EU_COURT_PREDICATE)
    current_id = current.get("@id") if isinstance(current, dict) else current
    if current_id == target_court:
        return False  # already correct — idempotent no-op
    node[EU_COURT_PREDICATE] = {"@id": target_court}
    return True


def process_peep(path: Path, dry_run: bool) -> int:
    """Correct every contaminated decision in one peep file.

    Returns the number of decision nodes re-pointed.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    graph = doc.get("@graph", [])
    if not isinstance(graph, list):
        return 0
    changed = sum(
        1 for node in graph if isinstance(node, dict) and correct_court(node)
    )
    if changed and not dry_run:
        save_json(path, doc)
    return changed


def ensure_schema_individuals(path: Path, dry_run: bool) -> int:
    """Inject the National/EFTA court individuals into the schema if absent.

    Returns the number of individuals added (0, 1 or 2). Idempotent: an
    already-present ``@id`` is not re-added.
    """
    if not path.exists():
        print(f"  WARNING: schema not found, skipping individual injection: {path}")
        return 0
    doc = json.loads(path.read_text(encoding="utf-8"))
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        print(f"  WARNING: schema has no @graph list, skipping: {path}")
        return 0
    present = {
        node.get("@id")
        for node in graph
        if isinstance(node, dict) and isinstance(node.get("@id"), str)
    }
    added = 0
    for court_id, individual in COURT_INDIVIDUALS.items():
        if court_id not in present:
            graph.append(individual)
            added += 1
    if added and not dry_run:
        save_json(path, doc)
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--curia-dir",
        type=Path,
        default=DEFAULT_CURIA_DIR,
        help="Directory of curia peep + schema files (default: krr_outputs/curia).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing any file.",
    )
    args = parser.parse_args(argv)

    curia_dir: Path = args.curia_dir
    if not curia_dir.is_dir():
        print(f"ERROR: not a directory: {curia_dir}")
        return 1

    print("=" * 70)
    print("Fix CURIA national/EFTA decisions mis-stamped as Court of Justice (#575)")
    print(f"  Directory: {curia_dir}")
    print("=" * 70)

    files = sorted(curia_dir.glob(PEEP_GLOB))
    files_changed = 0
    nodes_changed = 0
    for path in files:
        if is_lfs_pointer(path):
            print(f"  SKIP (git-LFS pointer): {path.name}")
            continue
        changed = process_peep(path, args.dry_run)
        if changed:
            files_changed += 1
            nodes_changed += changed
            print(f"  {path.name}: {changed} re-pointed")

    schema_path = curia_dir / SCHEMA_FILENAME
    individuals_added = 0
    if is_lfs_pointer(schema_path):
        print(f"  SKIP (git-LFS pointer): {schema_path.name}")
    else:
        individuals_added = ensure_schema_individuals(schema_path, args.dry_run)

    # The generated curia_combined.jsonld aggregate is git-LFS and network-built
    # (SPARQL), so it cannot be regenerated offline. It is a straight merge of the
    # peeps, so apply the SAME surgical transform here — re-point its euCourt edges
    # AND inject the National/EFTA court individuals — so the subcorpus-parity gate
    # (curia_combined vs the source peeps) stays satisfied without a network regen.
    # Skipped when it is an un-materialised LFS pointer (the no-LFS CI checkout).
    combined_path = curia_dir / "curia_combined.jsonld"
    if combined_path.exists() and not is_lfs_pointer(combined_path):
        combined_changed = process_peep(combined_path, args.dry_run)
        individuals_added += ensure_schema_individuals(combined_path, args.dry_run)
        if combined_changed:
            files_changed += 1
            nodes_changed += combined_changed
            print(f"  {combined_path.name}: {combined_changed} re-pointed (aggregate)")
    elif combined_path.exists():
        print(f"  SKIP (git-LFS pointer): {combined_path.name}")

    verb = "would re-point" if args.dry_run else "re-pointed"
    verb_add = "would add" if args.dry_run else "added"
    print(f"\n  Files scanned:    {len(files)}")
    print(f"  Files {verb}:  {files_changed}")
    print(f"  Decisions {verb}: {nodes_changed}")
    print(f"  Schema individuals {verb_add}: {individuals_added}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
