#!/usr/bin/env python3
"""Materialize reified SKOS target-group concepts onto provisions (#609).

The usefulness review (#609) flagged that a provision's addressee category ships
only as the legacy *bare-string* ``estleg:targetGroup`` enum (``"citizen"``,
``"business"``, ``"public_body"``, ``"official"``, ``"ngo"``). A string is opaque
to SPARQL/SKOS consumers: it cannot carry a label, a definition, or a
``skos:broader`` link, and it cannot be the object of an addressee query the way a
first-class ``skos:Concept`` can. This post-process makes the addressee category a
first-class node: for every provision carrying ``estleg:targetGroup`` it stamps a
companion ``estleg:targetGroupConcept`` pointing at the reified concept IRI for
each mapped enum value.

The transform is **purely additive**: the legacy ``estleg:targetGroup`` string(s)
are left untouched (consumers that read the enum keep working), and a parallel
``estleg:targetGroupConcept`` list of ``{"@id": …}`` references is written
alongside, deduped and sorted by IRI for determinism.

Idempotent by construction: every node's existing ``estleg:targetGroupConcept`` is
stripped *first*, then recomputed from the current ``estleg:targetGroup`` — so a
re-run is a no-op diff, and a node whose ``estleg:targetGroup`` was later removed
gets its now-orphaned ``estleg:targetGroupConcept`` cleaned up too. Unknown /
unmapped enum strings are skipped (counted and sampled in the report), never
fatal.

This is a **surgical, offline, deterministic** rewrite of the law/regulation
peeps (no network). It does NOT touch the legal-review-gated fields. Writes are
opt-in: the default run is a DRY RUN that only reports; ``--apply`` performs the
atomic in-place rewrite of changed peeps via ``save_json``. ``--dry-run`` is an
explicit no-op that overrides ``--apply`` (so ``--apply --dry-run`` writes
nothing), mirroring the report-only safety valve other repair passes use.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from estleg_common import KRR_DIR, save_json

# Legacy bare-string enum -> reified SKOS concept IRI. This is the EXACT,
# authoritative map for #609; the five values are the closed enum the
# target-group classifier emits (see classify_target_group.TARGET_GROUP_ORDER).
TARGET_GROUP_CONCEPT_IRI: dict[str, str] = {
    "citizen": "estleg:TargetGroup_Citizen",
    "business": "estleg:TargetGroup_Business",
    "public_body": "estleg:TargetGroup_PublicBody",
    "official": "estleg:TargetGroup_Official",
    "ngo": "estleg:TargetGroup_NGO",
}

TARGET_GROUP_KEY = "estleg:targetGroup"
TARGET_GROUP_CONCEPT_KEY = "estleg:targetGroupConcept"

# A Git-LFS pointer file begins with this line instead of JSON; loading it as
# JSON would crash, so it is skipped (the corpus keeps a handful of large
# artifacts in LFS — peeps normally are not, but the guard is cheap and safe).
LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"


@dataclass
class NodeChange:
    """Outcome of materializing one node (returned by :func:`materialize_node`)."""

    had_target_group: bool = False
    links_emitted: int = 0
    changed: bool = False
    unknown_values: list[str] = field(default_factory=list)


@dataclass
class Stats:
    """Run-wide counters surfaced in the report."""

    peeps_scanned: int = 0
    peeps_skipped_lfs: int = 0
    peeps_unreadable: int = 0
    provisions_with_target_group: int = 0
    links_emitted: int = 0
    nodes_changed: int = 0
    peeps_changed: int = 0
    unknown_values: Counter = field(default_factory=Counter)


_CONCEPT_IRIS = frozenset(TARGET_GROUP_CONCEPT_IRI.values())
_FULL_IRI_PREFIX = "https://w3id.org/estleg/"


def _target_group_strings(value: object) -> list[str]:
    """Coerce an ``estleg:targetGroup`` value to tokens or compact IRIs.

    Accepts a legacy enum string, a compact/full ``TargetGroup_*`` IRI, a
    JSON-LD ``{"@id": …}`` object, or a list of any of those (#460 / #609).
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        iri = value.get("@id")
        return [iri] if isinstance(iri, str) else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_target_group_strings(item))
        return out
    return []


def _concept_iri_for(value: str) -> str | None:
    """Map a legacy token or already-IRI value to ``estleg:TargetGroup_*``."""
    if value in TARGET_GROUP_CONCEPT_IRI:
        return TARGET_GROUP_CONCEPT_IRI[value]
    raw = value
    if raw.startswith(_FULL_IRI_PREFIX):
        raw = "estleg:" + raw[len(_FULL_IRI_PREFIX) :]
    if raw in _CONCEPT_IRIS:
        return raw
    return None


def materialize_node(node: dict) -> NodeChange:
    """Strip-then-recompute ``estleg:targetGroupConcept`` on a single node.

    Mutates ``node`` in place and returns a :class:`NodeChange`. The existing
    concept list is always removed first (idempotency + orphan cleanup); a fresh
    deduped, IRI-sorted list of ``{"@id": …}`` references is then written iff the
    node still has at least one mappable ``estleg:targetGroup`` value. The legacy
    ``estleg:targetGroup`` string(s) are never touched (purely additive).
    """
    result = NodeChange()

    # Remove any prior materialization BEFORE recomputing: this makes a re-run a
    # no-op diff and cleans up a stale link on a node whose targetGroup was
    # removed since the last run.
    previous = node.pop(TARGET_GROUP_CONCEPT_KEY, None)

    raw = node.get(TARGET_GROUP_KEY)
    new_links: list[dict] | None = None
    if raw is not None:
        result.had_target_group = True
        iris: list[str] = []
        seen: set[str] = set()
        for value in _target_group_strings(raw):
            iri = _concept_iri_for(value)
            if iri is None:
                result.unknown_values.append(value)
                continue
            if iri not in seen:
                seen.add(iri)
                iris.append(iri)
        iris.sort()  # deterministic ordering by IRI string
        if iris:
            new_links = [{"@id": iri} for iri in iris]

    if new_links is not None:
        node[TARGET_GROUP_CONCEPT_KEY] = new_links
        result.links_emitted = len(new_links)

    # `previous` and `new_links` are both either None or a list of {"@id": …};
    # list/dict equality is order- and value-sensitive, so this correctly detects
    # add / remove / modify / no-op.
    result.changed = new_links != previous
    return result


def process_document(doc: dict, stats: Stats) -> bool:
    """Materialize every node in ``doc['@graph']``; update ``stats``.

    Returns True iff at least one node changed (i.e. the file needs rewriting).
    """
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return False
    file_changed = False
    for node in graph:
        if not isinstance(node, dict):
            continue
        change = materialize_node(node)
        if change.had_target_group:
            stats.provisions_with_target_group += 1
        stats.links_emitted += change.links_emitted
        for value in change.unknown_values:
            stats.unknown_values[value] += 1
        if change.changed:
            stats.nodes_changed += 1
            file_changed = True
    return file_changed


def run(should_write: bool, krr_dir: Path | None = None) -> Stats:
    """Scan every ``*_peep.json`` under ``krr_dir`` and materialize concepts.

    ``krr_dir`` defaults to the module-level ``KRR_DIR`` (read at call time so a
    test can monkeypatch it). When ``should_write`` is True, changed peeps are
    rewritten atomically via ``save_json``; otherwise the run is read-only.
    """
    root = krr_dir if krr_dir is not None else KRR_DIR
    stats = Stats()
    for path in sorted(root.rglob("*_peep.json")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            stats.peeps_unreadable += 1
            continue
        if text.startswith(LFS_POINTER_PREFIX):
            stats.peeps_skipped_lfs += 1
            continue
        try:
            doc = json.loads(text)
        except ValueError:
            stats.peeps_unreadable += 1
            continue
        if not isinstance(doc, dict):
            stats.peeps_unreadable += 1
            continue
        stats.peeps_scanned += 1
        file_changed = process_document(doc, stats)
        if file_changed:
            stats.peeps_changed += 1
            if should_write:
                save_json(path, doc)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changed peeps atomically. Default is a read-only DRY RUN.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force a report-only no-op even if --apply is given (overrides it).",
    )
    args = parser.parse_args(argv)
    should_write = args.apply and not args.dry_run

    print("=" * 70)
    print("Materialize SKOS target-group concepts onto provisions (#609)")
    print(f"  Mode: {'APPLY — writing changed peeps' if should_write else 'DRY RUN — no writes'}")
    print("=" * 70)

    stats = run(should_write)

    print(f"  Peeps scanned: {stats.peeps_scanned}")
    if stats.peeps_skipped_lfs:
        print(f"  Peeps skipped (Git-LFS pointer): {stats.peeps_skipped_lfs}")
    if stats.peeps_unreadable:
        print(f"  Peeps skipped (unreadable/not JSON): {stats.peeps_unreadable}")
    print(f"  Provisions with targetGroup: {stats.provisions_with_target_group}")
    print(f"  targetGroupConcept links emitted: {stats.links_emitted}")
    print(f"  Nodes changed: {stats.nodes_changed}")
    verb = "changed" if should_write else "that would change"
    print(f"  Peeps {verb}: {stats.peeps_changed}")

    distinct_unknown = len(stats.unknown_values)
    print(f"  Distinct unknown targetGroup values: {distinct_unknown}")
    if distinct_unknown:
        total_unknown = sum(stats.unknown_values.values())
        print(f"    ({total_unknown} occurrence(s); sample: {stats.unknown_values.most_common(10)})")

    if should_write and stats.peeps_changed:
        print(
            "\n  NOTE: rebuild combined_ontology.jsonld so the load surface carries "
            "the new estleg:targetGroupConcept links "
            "(fix_all_issues.generate_combined_jsonld)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
