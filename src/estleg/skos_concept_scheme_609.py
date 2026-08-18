#!/usr/bin/env python3
"""Bind the legal-concept layer into ``estleg:LegalConceptScheme`` (SKOS) (#609).

The fitness eval (#609) flagged that the legal-concept layer is not
SKOS-conformant: concept instances carry ``skos:prefLabel`` / ``skos:definition``
but are not members of any ``skos:ConceptScheme``, so the corpus cannot answer
"what scheme is this concept in?" and SKOS consumers see orphaned concepts. The
scheme node ``estleg:LegalConceptScheme`` already ships in
``controlled_vocabulary.jsonld``; this script supplies the missing *instance-side*
edges that bind every concept into it.

It is a **surgical, offline, deterministic** post-process over the single file
``krr_outputs/concepts/concepts_combined.jsonld`` (no network, no regeneration of
the concept extractor). For every node in its ``@graph`` whose ``@type`` contains
``estleg:Concept`` (canonical, corpus-wide concepts) OR ``estleg:LegalConcept``
(provision-local definitions) it:

  1. sets ``skos:inScheme -> estleg:LegalConceptScheme`` (assignment — every
     concept instance is a member of the one scheme);
  2. ensures ``skos:Concept`` is in the node's ``@type`` list (appended iff
     absent, never duplicated — asserts the SKOS class so reasoners and
     SHACL ``sh:targetClass skos:Concept`` shapes see these nodes); and
  3. for canonical ``estleg:Concept`` nodes only, also sets
     ``skos:topConceptOf -> estleg:LegalConceptScheme`` (canonical concepts are
     the scheme's top concepts; provision-local definitions are not).

The scheme node itself is **not** created here — it is defined in
``controlled_vocabulary.jsonld`` and the SHACL shapes are maintained separately.

Idempotent: a concept already bound is a no-op; re-running produces a no-op diff
(no duplicate ``@type`` entries, stable result). DRY-RUN is the **default** —
pass ``--apply`` to write. ``--dry-run`` is accepted as an explicit no-op that
overrides ``--apply``. Atomic writes via ``estleg_common.save_json`` (tempfile +
``os.replace``). A file that is still a Git-LFS pointer stub is skipped (never
rewrite a pointer into real content).

Run (offline; report-only by default)::

    python3 scripts/skos_concept_scheme_609.py            # dry-run
    python3 scripts/skos_concept_scheme_609.py --apply     # write
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from estleg.estleg_common import KRR_DIR, save_json

CONCEPTS_FILE = KRR_DIR / "concepts" / "concepts_combined.jsonld"

# The scheme is defined in controlled_vocabulary.jsonld; we only reference it.
SCHEME_ID = "estleg:LegalConceptScheme"
SCHEME_REF: dict = {"@id": SCHEME_ID}

CANONICAL_TYPE = "estleg:Concept"        # corpus-wide canonical concept
PROVISION_LOCAL_TYPE = "estleg:LegalConcept"  # a single provision's definition
SKOS_CONCEPT_TYPE = "skos:Concept"

LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"


def _type_list(node: dict) -> list[str]:
    """The node's ``@type`` as a list (a bare string is wrapped, never mutated)."""
    types = node.get("@type", [])
    if isinstance(types, str):
        return [types]
    if isinstance(types, list):
        return types
    return []


def is_concept_node(node: dict) -> bool:
    """True for a canonical or provision-local concept instance."""
    types = _type_list(node)
    return CANONICAL_TYPE in types or PROVISION_LOCAL_TYPE in types


def is_canonical_concept(node: dict) -> bool:
    """True only for canonical (``estleg:Concept``) concepts — the scheme's tops."""
    return CANONICAL_TYPE in _type_list(node)


def transform_node(node: dict) -> bool:
    """Bind one concept instance into ``estleg:LegalConceptScheme`` in place.

    Returns ``True`` iff the node was modified. Non-concept nodes are left
    untouched (returns ``False``). Idempotent: a node already fully bound is a
    no-op, and ``skos:Concept`` is never duplicated in ``@type``.
    """
    if not isinstance(node, dict):
        return False
    types = _type_list(node)
    is_canonical = CANONICAL_TYPE in types
    is_provision_local = PROVISION_LOCAL_TYPE in types
    if not (is_canonical or is_provision_local):
        return False

    changed = False

    # (1) Every concept instance is a member of the one scheme (assignment).
    if node.get("skos:inScheme") != SCHEME_REF:
        node["skos:inScheme"] = dict(SCHEME_REF)
        changed = True

    # (2) Assert the SKOS class: a concept IS a skos:Concept (append iff absent).
    if SKOS_CONCEPT_TYPE not in types:
        node["@type"] = list(types) + [SKOS_CONCEPT_TYPE]
        changed = True

    # (3) Canonical concepts are the scheme's top concepts (assignment).
    if is_canonical and node.get("skos:topConceptOf") != SCHEME_REF:
        node["skos:topConceptOf"] = dict(SCHEME_REF)
        changed = True

    return changed


def process_doc(doc: dict) -> dict[str, int]:
    """Bind every concept node in ``doc['@graph']`` in place; return counts.

    Counts: ``total`` concept nodes, ``canonical`` (``estleg:Concept``),
    ``provision_local`` (``estleg:LegalConcept``), and ``changed`` nodes.
    """
    counts = {"total": 0, "canonical": 0, "provision_local": 0, "changed": 0}
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return counts
    for node in graph:
        if not isinstance(node, dict) or not is_concept_node(node):
            continue
        types = _type_list(node)
        counts["total"] += 1
        if CANONICAL_TYPE in types:
            counts["canonical"] += 1
        if PROVISION_LOCAL_TYPE in types:
            counts["provision_local"] += 1
        if transform_node(node):
            counts["changed"] += 1
    return counts


def _is_lfs_pointer(path: Path) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            first = fh.readline()
    except OSError:
        return False
    return first.startswith(LFS_POINTER_PREFIX)


def process_file(path: Path, *, write: bool) -> dict[str, int] | None:
    """Bind concept instances in ``path`` into the scheme.

    Returns the per-run counts, or ``None`` when ``path`` is a Git-LFS pointer
    stub (skipped — never rewrite a pointer into real content). Writes only when
    ``write`` is True and at least one node changed.
    """
    if _is_lfs_pointer(path):
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    counts = process_doc(doc)
    if write and counts["changed"]:
        save_json(path, doc)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        type=Path,
        default=CONCEPTS_FILE,
        help=f"Concept file to bind into the scheme (default: {CONCEPTS_FILE}).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the SKOS bindings to disk. Default is a dry run (report only).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit no-op: report only. Overrides --apply if both are given.",
    )
    args = parser.parse_args(argv)

    # DRY-RUN is the default; --apply enables writing; --dry-run always wins.
    write = args.apply and not args.dry_run

    path: Path = args.path
    if not path.exists():
        print(f"ERROR: file not found: {path}")
        return 1

    print("=" * 70)
    print("Bind legal-concept layer into estleg:LegalConceptScheme (SKOS) (#609)")
    print(f"  File: {path}")
    print(f"  MODE: {'APPLY (writing)' if write else 'dry-run (no writes)'}")
    print("=" * 70)

    counts = process_file(path, write=write)
    if counts is None:
        print("  SKIPPED: file is a Git-LFS pointer stub (run `git lfs pull`).")
        return 0

    verb = "changed" if write else "would change"
    print(f"\n  Total concept nodes:                   {counts['total']}")
    print(f"  Canonical (estleg:Concept):            {counts['canonical']}")
    print(f"  Provision-local (estleg:LegalConcept): {counts['provision_local']}")
    print(f"  Nodes {verb}:                       {counts['changed']}")
    if write and counts["changed"]:
        print(
            "\n  NOTE: rebuild combined_ontology.jsonld so the load surface carries "
            "the new skos:inScheme / topConceptOf bindings."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
