#!/usr/bin/env python3
"""Mint an ``estleg:ActExpression`` per consolidation date of each act (#608).

FRBR phase 1. The corpus models a law as one timeless act node (e.g.
``estleg:VKVS_Map_2026``) plus a per-provision redaction chain in
``krr_outputs/provision_versions/<slug>.jsonld``. There is no addressable node
for *"the law as it stood on date D"* — so a consumer cannot dereference a
particular consolidated text, only the act-as-such or an individual provision
redaction. In FRBR terms the corpus ships the *Work* (the act) and the
provision-level *Expressions*, but not the act-level *Expression* a citation
like "VÕS, seisuga 2005-03-09" actually denotes.

This script fills that gap. For each act it reads the DISTINCT consolidation
dates recorded on its provision redactions (``estleg:versionValidFrom``) and,
per date, mints one ``estleg:ActExpression`` node:

    estleg:<act-fragment>_Expr_<YYYYMMDD>
        a owl:NamedIndividual, estleg:ActExpression ;
        estleg:expressionOf      <the act node> ;
        estleg:consolidationDate "YYYY-MM-DD"^^xsd:date ;
        rdfs:label               "<act label> (seisuga YYYY-MM-DD)" .

All expressions across the corpus are collected into ONE file,
``krr_outputs/act_expressions_combined.jsonld``, with the ``@graph`` sorted by
``@id`` so re-running yields a byte-stable artifact.

The act-root IRI (and its label) come from the matching law peep
``krr_outputs/<slug>_peep.json`` — the node whose ``@type`` contains
``estleg:Act``. A sidecar whose peep is missing or carries no ``estleg:Act``
node (~6 of 742) is skipped and counted.

This is a **surgical, offline, deterministic** derivation from authoritative
Riigi Teataja redaction dates (no network). Git-LFS pointer files (first line
``version https://git-lfs.github.com/spec/v1``) are skipped rather than parsed.

DRY-RUN is the DEFAULT: it computes everything and reports counts but writes
nothing. Pass ``--apply`` to write the artifact via ``save_json`` (atomic);
``--dry-run`` is an explicit no-op for symmetry. Idempotent.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from estleg_common import KRR_DIR, NS, jsonld_text, save_json

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Per-act redaction chains live under ``<krr>/provision_versions/<slug>.jsonld``;
# the collector derives this from its ``krr_dir`` argument (so tests can rebind
# KRR_DIR) rather than reading a module-level path constant.
OUTPUT_FILENAME = "act_expressions_combined.jsonld"

# First line of a Git-LFS pointer placeholder (skip — not real JSON).
_LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"

# The minimal context the combined artifact publishes (exactly four prefixes).
OUTPUT_CONTEXT: dict[str, str] = {
    "estleg": NS,
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}

# Skip reasons returned by :func:`build_for_sidecar`.
SKIP_NO_ACT = "no_act_root"   # peep missing / unreadable / no estleg:Act node
SKIP_LFS = "lfs_pointer"      # sidecar is an LFS pointer (or otherwise unreadable)


# ---------------------------------------------------------------------------
# Pure helpers (no I/O) — factored for unit testing
# ---------------------------------------------------------------------------
def act_local_fragment(act_iri: str) -> str:
    """Return the local fragment of an act IRI (the part identifying the node).

    Handles both the compact form (``estleg:VKVS_Map_2026`` -> ``VKVS_Map_2026``)
    and the expanded form -- the final segment after the namespace, whether the
    namespace is slash-separated (``https://w3id.org/estleg/VKVS_Map_2026``) or
    the legacy hash form (``…/estleg#VKVS_Map_2026``) -> ``VKVS_Map_2026``.
    """
    if act_iri.startswith("estleg:"):
        return act_iri[len("estleg:"):]
    # Separator-agnostic: the local name is the final path/fragment segment.
    return act_iri.replace("#", "/").rsplit("/", 1)[-1]


def date_digits(date_value: str) -> str:
    """Return the digits of an ISO date: ``2005-03-09`` -> ``20050309``."""
    return "".join(ch for ch in date_value if ch.isdigit())


def expression_iri(act_iri: str, date_value: str) -> str:
    """Build the compact Expression IRI for an (act, consolidation-date) pair.

    ``estleg:VKVS_Map_2026`` + ``2005-03-09`` -> ``estleg:VKVS_Map_2026_Expr_20050309``.
    """
    return f"estleg:{act_local_fragment(act_iri)}_Expr_{date_digits(date_value)}"


def expression_label(label_text: str | None, slug: str, date_value: str) -> str:
    """Build the Expression's ``rdfs:label``: ``<act label or slug> (seisuga D)``."""
    base = label_text if label_text else slug
    return f"{base} (seisuga {date_value})"


def build_expression_node(
    act_iri: str, label_text: str | None, slug: str, date_value: str
) -> dict:
    """Mint a single ``estleg:ActExpression`` node for one consolidation date."""
    return {
        "@id": expression_iri(act_iri, date_value),
        "@type": ["owl:NamedIndividual", "estleg:ActExpression"],
        "estleg:expressionOf": {"@id": act_iri},
        "estleg:consolidationDate": {"@value": date_value, "@type": "xsd:date"},
        "rdfs:label": expression_label(label_text, slug, date_value),
    }


def build_act_expression_nodes(
    act_iri: str, label_text: str | None, slug: str, dates: list[str]
) -> list[dict]:
    """Mint one Expression node per consolidation date for a single act, chained
    in chronological order (#608 phase 2).

    ``dates`` is sorted ascending, so each expression is superseded by the next
    consolidation (estleg:supersededByExpression) and supersedes the previous one
    (estleg:supersedesExpression). This makes the act's consolidation history a
    navigable FRBR temporal chain; the per-provision membership at a given date is
    derivable on demand from the provision_versions sidecars (versionValidFrom <=
    consolidationDate, latest per provision), so it is intentionally NOT
    materialised here (it would be ~1.3M edges)."""
    nodes = [
        build_expression_node(act_iri, label_text, slug, date_value)
        for date_value in dates
    ]
    for current, nxt in zip(nodes, nodes[1:]):
        current["estleg:supersededByExpression"] = {"@id": nxt["@id"]}
        nxt["estleg:supersedesExpression"] = {"@id": current["@id"]}
    return nodes


def distinct_consolidation_dates(version_doc: dict) -> list[str]:
    """Return the act's sorted DISTINCT consolidation dates from a sidecar doc.

    Collects ``estleg:versionValidFrom`` ``@value`` from every node whose
    ``@type`` contains ``estleg:ProvisionVersion``. Nodes without a usable date
    contribute nothing. Sorted for determinism.
    """
    dates: set[str] = set()
    for node in version_doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        types = node.get("@type")
        if isinstance(types, str):
            types = [types]
        if "estleg:ProvisionVersion" not in (types or []):
            continue
        valid_from = node.get("estleg:versionValidFrom")
        if isinstance(valid_from, dict):
            valid_from = valid_from.get("@value")
        if isinstance(valid_from, str) and valid_from:
            dates.add(valid_from)
    return sorted(dates)


def find_act_root(peep_doc: dict) -> tuple[str | None, str | None]:
    """Return ``(act_iri, label_text)`` for the peep's ``estleg:Act`` node.

    Returns ``(None, None)`` when no node is typed ``estleg:Act`` (or the peep
    is not a graph document). ``label_text`` is the plain-string form of the
    act's ``rdfs:label`` (via ``jsonld_text``), or ``None`` when absent.
    """
    if not isinstance(peep_doc, dict):
        return None, None
    for node in peep_doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        types = node.get("@type")
        if isinstance(types, str):
            types = [types]
        if "estleg:Act" not in (types or []):
            continue
        act_iri = node.get("@id")
        if not isinstance(act_iri, str) or not act_iri:
            continue
        label_text = jsonld_text(node.get("rdfs:label")) or None
        return act_iri, label_text
    return None, None


def build_output_document(nodes: list[dict]) -> dict:
    """Wrap Expression nodes in the combined artifact, ``@graph`` sorted by @id."""
    return {
        "@context": OUTPUT_CONTEXT,
        "@graph": sorted(nodes, key=lambda node: node["@id"]),
    }


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------
def _is_lfs_pointer(path: Path) -> bool:
    """Return True if ``path``'s first line is a Git-LFS pointer header."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.readline().startswith(_LFS_POINTER_PREFIX)
    except (OSError, ValueError):
        return False


def _load_json(path: Path) -> dict | None:
    """Load a JSON document, returning ``None`` if missing/LFS-pointer/corrupt."""
    if not path.exists() or _is_lfs_pointer(path):
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build_for_sidecar(
    sidecar_path: Path, peep_path: Path
) -> tuple[list[dict], str | None]:
    """Build the Expression nodes for one sidecar/peep pair.

    Returns ``(nodes, skip_reason)``. ``skip_reason`` is ``None`` on success,
    :data:`SKIP_LFS` when the sidecar can't be read (LFS pointer / corrupt), or
    :data:`SKIP_NO_ACT` when the peep is missing/unreadable or has no
    ``estleg:Act`` node. The slug (for the label fallback) is the sidecar stem.
    """
    if _is_lfs_pointer(sidecar_path):
        return [], SKIP_LFS
    version_doc = _load_json(sidecar_path)
    if version_doc is None:
        return [], SKIP_LFS
    peep_doc = _load_json(peep_path)
    if peep_doc is None:
        return [], SKIP_NO_ACT
    act_iri, label_text = find_act_root(peep_doc)
    if not act_iri:
        return [], SKIP_NO_ACT
    dates = distinct_consolidation_dates(version_doc)
    nodes = build_act_expression_nodes(act_iri, label_text, sidecar_path.stem, dates)
    return nodes, None


def collect_all_expressions(krr_dir: Path | None = None) -> tuple[list[dict], dict]:
    """Walk every provision-versions sidecar and collect all Expression nodes.

    Returns ``(nodes, stats)``. ``stats`` carries the report counters. Reads the
    module-level ``KRR_DIR`` by default so tests can monkeypatch it; pass
    ``krr_dir`` explicitly to override.
    """
    krr_dir = krr_dir or KRR_DIR
    version_dir = krr_dir / "provision_versions"
    sidecars = sorted(version_dir.glob("*.jsonld")) if version_dir.is_dir() else []

    nodes: list[dict] = []
    scanned = 0
    skipped_no_act = 0
    skipped_lfs = 0
    for sidecar_path in sidecars:
        peep_path = krr_dir / f"{sidecar_path.stem}_peep.json"
        sidecar_nodes, reason = build_for_sidecar(sidecar_path, peep_path)
        if reason == SKIP_LFS:
            skipped_lfs += 1
            continue
        if reason == SKIP_NO_ACT:
            skipped_no_act += 1
            continue
        scanned += 1
        nodes.extend(sidecar_nodes)

    distinct_acts = {node["estleg:expressionOf"]["@id"] for node in nodes}
    stats = {
        "sidecars_total": len(sidecars),
        "sidecars_scanned": scanned,
        "sidecars_skipped_no_act": skipped_no_act,
        "sidecars_skipped_lfs": skipped_lfs,
        "distinct_acts": len(distinct_acts),
        "total_expressions": len(nodes),
    }
    return nodes, stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Write the combined artifact (default: DRY RUN — compute + report only).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Explicit no-op: compute and report without writing (the default).",
    )
    args = parser.parse_args(argv)
    do_write = args.apply and not args.dry_run

    nodes, stats = collect_all_expressions()
    document = build_output_document(nodes)
    output_path = KRR_DIR / OUTPUT_FILENAME

    print("=" * 70)
    print("Mint estleg:ActExpression per consolidation date (#608, FRBR phase 1)")
    print(f"  Mode: {'APPLY (writing)' if do_write else 'DRY RUN (no write)'}")
    print("=" * 70)
    print(f"  Sidecars scanned:            {stats['sidecars_scanned']}")
    print(f"  Sidecars skipped (no act):   {stats['sidecars_skipped_no_act']}")
    print(f"  Sidecars skipped (LFS):      {stats['sidecars_skipped_lfs']}")
    print(f"  Distinct acts:               {stats['distinct_acts']}")
    print(f"  Total Expression nodes:      {stats['total_expressions']}")

    if do_write:
        save_json(output_path, document)
        print(f"\n  Wrote {output_path.relative_to(KRR_DIR.parent)}")
    else:
        print(f"\n  [DRY RUN] Would write {output_path.relative_to(KRR_DIR.parent)} "
              f"({stats['total_expressions']} nodes). Pass --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
