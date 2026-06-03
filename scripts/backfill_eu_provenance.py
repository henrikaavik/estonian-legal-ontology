#!/usr/bin/env python3
"""Backfill CELEX-derived provenance fields onto EU nodes (#348).

The EU generators (``generate_eu_legislation.py`` / ``generate_eu_court_decisions.py``)
already emit three provenance fields, but the committed eurlex/curia artifacts
predate those commits, so the fields are missing from the corpus. Every field is
*purely* derived from the node's existing ``estleg:celexNumber`` — no network /
SPARQL call is needed — so this is a deterministic, idempotent local backfill:

    owl:sameAs      -> {"@id": "http://publications.europa.eu/resource/celex/<CELEX>"}
    dcterms:source  -> {"@id": "http://publications.europa.eu/resource/celex/<CELEX>"}
    eli:id_local    -> "<CELEX>"   (EULegislation only; case-law has no ELI)

Notes
-----
* ``owl:sameAs`` / ``dcterms:source`` both point at the CELEX *resource* PURL
  (CELLAR-resolvable, content-negotiable) — NOT the human eur-lex.europa.eu URL,
  which is emitted separately as ``estleg:eurLexLink`` / ``estleg:curiaLink`` and
  is already present. ``validate_all.validate_source_provenance`` requires
  ``dcterms:source`` to be an IRI object ``{"@id": ...}`` (a hard error), which the
  template satisfies.
* The full ELI URI (``estleg:eliIdentifier``, e.g. ``.../eli/dir/1981/643/oj``) is
  SPARQL-sourced, already present, and left untouched. ``eli:id_local`` is the bare
  CELEX string.
* The eurlex peep/combined ``@context`` lacks an ``eli`` prefix; this script injects
  ``"eli": "http://data.europa.eu/eli/ontology#"`` so the emitted ``eli:id_local``
  term resolves. Curia files emit no ``eli:id_local`` and need no context change.
* ``dcterms:source`` is a subcorpus-parity field (``PROVISION_PARITY_FIELDS``), so
  the per-subcorpus ``*_combined.jsonld`` must receive the identical backfill — both
  peep and combined files are processed.
* Idempotent: the ``not in node`` guards mean a re-run (or a partially-filled node)
  is a no-op, so this is safe to run after the enrichment pipeline settles.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from estleg_common import REPO_ROOT, save_json

EU_CELEX_RESOURCE = "http://publications.europa.eu/resource/celex/{celex}"
ELI_PREFIX_IRI = "http://data.europa.eu/eli/ontology#"

# Subcorpus files to backfill. eurlex carries EULegislation (gets eli:id_local +
# eli context); curia carries EUCourtDecision (owl:sameAs + dcterms:source only).
EURLEX_FILES = (
    "eurlex/eurlex_regulations_peep.json",
    "eurlex/eurlex_directives_peep.json",
    "eurlex/eurlex_decisions_peep.json",
    "eurlex/eurlex_combined.jsonld",
)
CURIA_FILES = (
    "curia/curia_judgments_peep.json",
    "curia/curia_orders_peep.json",
    "curia/curia_ag_opinions_peep.json",
    "curia/curia_court_opinions_peep.json",
    "curia/curia_other_peep.json",
    "curia/curia_combined.jsonld",
)


def _node_types(node: dict) -> list[str]:
    t = node.get("@type", [])
    return [t] if isinstance(t, str) else list(t)


def backfill_node(node: dict) -> bool:
    """Add CELEX-derived provenance to one EU node in place.

    Returns True iff the node was modified. Skips nodes that are not an EU
    legislation/court-decision instance or that carry no CELEX (ontology/map
    header nodes). The ``not in`` guards make this idempotent.
    """
    types = _node_types(node)
    is_leg = "estleg:EULegislation" in types
    is_dec = "estleg:EUCourtDecision" in types
    if not (is_leg or is_dec):
        return False
    celex = node.get("estleg:celexNumber")
    if not isinstance(celex, str) or not celex:
        return False
    uri = EU_CELEX_RESOURCE.format(celex=celex)
    changed = False
    if "owl:sameAs" not in node:
        node["owl:sameAs"] = {"@id": uri}
        changed = True
    if "dcterms:source" not in node:
        node["dcterms:source"] = {"@id": uri}
        changed = True
    if is_leg and "eli:id_local" not in node:
        node["eli:id_local"] = celex
        changed = True
    return changed


def process_file(path: Path, inject_eli: bool, dry_run: bool) -> tuple[int, int]:
    """Backfill one corpus file. Returns (nodes_changed, nodes_total)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    graph = doc.get("@graph", [])
    context_changed = False
    if inject_eli:
        ctx = doc.get("@context")
        if isinstance(ctx, dict) and "eli" not in ctx:
            ctx["eli"] = ELI_PREFIX_IRI
            context_changed = True
    changed = sum(1 for node in graph if isinstance(node, dict) and backfill_node(node))
    if (changed or context_changed) and not dry_run:
        save_json(path, doc)
    return changed, len(graph)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--krr-dir",
        type=Path,
        default=REPO_ROOT / "krr_outputs",
        help="Corpus root (default: <repo>/krr_outputs).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing any file.",
    )
    args = parser.parse_args(argv)

    print("=" * 70)
    print("Backfill EU provenance fields (owl:sameAs / dcterms:source / eli:id_local)")
    print("=" * 70)
    total_changed = 0
    for rel_files, inject_eli in ((EURLEX_FILES, True), (CURIA_FILES, False)):
        for rel in rel_files:
            path = args.krr_dir / rel
            if not path.exists():
                print(f"  SKIP (missing): {rel}")
                continue
            changed, total = process_file(path, inject_eli, args.dry_run)
            total_changed += changed
            print(f"  {rel}: {changed}/{total} nodes backfilled"
                  f"{' (eli context injected)' if inject_eli else ''}")
    verb = "would backfill" if args.dry_run else "backfilled"
    print(f"\n{verb} {total_changed} EU nodes total.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
