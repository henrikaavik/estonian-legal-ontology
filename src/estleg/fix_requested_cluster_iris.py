#!/usr/bin/env python3
"""Mint proper IRIs for bare-string ``estleg:requestedCluster`` targets (#564).

A subset of ``estleg:requestedCluster`` edges (on provision / enforcement-Norm
individuals in the law peeps) place a plain cluster LABEL in ``@id`` position::

    "estleg:requestedCluster": {"@id": "politsei ülesanded"}   # space in @id!
    "estleg:requestedCluster": {"@id": "law enforcement"}      # no scheme
    "estleg:requestedCluster": {"@id": "military_service"}     # bare slug

Two defects on a single predicate:

  1. **Malformed IRIs.** ``{"@id": "politsei ülesanded"}`` is a *relative* IRI;
     the embedded space violates RFC 3987 and strict RDF parsers reject/mangle
     it.
  2. **Dangling references.** None of the 130 distinct bare targets is defined
     as a subject anywhere in the corpus, so every such edge points at nothing.

The corpus convention (every well-formed cluster, ~3,165 of them) is::

    {"@id": "estleg:Cluster_<slug>"}            # the edge target
    {                                            # …and the defining node
        "@id": "estleg:Cluster_<slug>",
        "@type": ["owl:NamedIndividual", "estleg:TopicCluster", "skos:Concept"],
        "rdfs:label": "<human label>",
        "skos:prefLabel": "<human label>",
    }

This migration brings the bare edges onto that convention **without inventing a
mechanical label→existing-cluster mapping** (the bare labels — ``politsei
ülesanded`` — and the file's defined clusters — ``estleg:Cluster_PoliceFunctions``
— are semantically related but not 1:1, so guessing would be unsafe). Instead,
per file:

  * Rewrite every bare ``requestedCluster`` ``@id`` to
    ``estleg:Cluster_<sanitize_id(label)>`` (the same minting rule the law
    generators use), preserving the original label.
  * Define the minted cluster node (``estleg:TopicCluster`` / ``skos:Concept``)
    if it is not already a subject in that file, carrying the original bare
    string as its ``rdfs:label`` / ``skos:prefLabel`` so no information is lost.

After this runs, the 926 malformed edges become well-formed CURIEs and every one
resolves to a defined ``estleg:TopicCluster`` node — both defects cleared.

Safety / scope:
  * A target already in CURIE form (``estleg:Cluster_…``) and free of spaces is
    left untouched — only ``@id`` values that are NOT a CURIE (no ``:``) or that
    contain a space are rewritten. So the 3,165 well-formed edges are no-ops.
  * Idempotent: a re-run finds the minted CURIEs already well-formed and the
    cluster nodes already defined, so it reports 0 changes.
  * Atomic writes via ``estleg_common.save_json`` (tempfile + os.replace).
  * ``--dry-run`` reports counts without writing.
  * Only ``krr_outputs/*_peep.json`` law peeps are scanned (where the defect
    lives); git-LFS pointer files are skipped before ``json.loads``.

Run (offline, idempotent)::

    python3 scripts/fix_requested_cluster_iris.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from estleg.estleg_common import REPO_ROOT, sanitize_id, save_json

REQUESTED_CLUSTER = "estleg:requestedCluster"
CLUSTER_IRI_PREFIX = "estleg:Cluster_"
TOPIC_CLUSTER_TYPE = "estleg:TopicCluster"

DEFAULT_PEEP_GLOB = "*_peep.json"
DEFAULT_KRR_DIR = REPO_ROOT / "krr_outputs"


def _is_lfs_pointer(path: Path) -> bool:
    """True if ``path`` is a git-LFS pointer stub rather than real JSON."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            return fh.read(60).startswith("version https://git-lfs")
    except OSError:
        return True


def is_bare_cluster_id(value: str) -> bool:
    """True if ``value`` is a bare cluster label (not a usable IRI/CURIE).

    A well-formed target is a CURIE (``estleg:Cluster_…``) with no whitespace.
    Anything lacking a ``:`` scheme separator, or carrying a space, is the
    malformed bare-string form #564 describes.
    """
    return (":" not in value) or (" " in value)


def mint_cluster_iri(label: str, file_stem: str) -> str:
    """Mint ``estleg:Cluster_<slug>_<file_stem>`` from a bare label.

    ``sanitize_id`` transliterates Estonian diacritics, maps spaces to ``_`` and
    drops non-``[0-9A-Za-z_]`` — the exact rule ``generate_all_laws`` /
    ``generate_missing_parts`` use to mint cluster IRIs.

    The ``file_stem`` (peep filename slug, e.g. ``keskkonnaseadus_peep``) is
    appended so the minted IRI is GLOBALLY unique. Without it, the same bare
    label appearing in two files (``keskkonnakaitse`` in both ``keskkonnaseadus``
    and ``maapoue_seadus``; ``müük`` in both ``alkoholi`` and ``tubaka``) would
    mint the same ``@id`` and DEFINE it as a node in BOTH files — a cross-file
    ``@id`` collision that ``validate_all.validate_id_uniqueness`` rejects. This
    mirrors the corpus's own disambiguation precedent
    (``estleg:Cluster_Keskkonnakaitse_maapoueseadus`` already exists in
    maapoue_seadus_peep.json). The same ``(label, file_stem)`` always yields the
    same IRI, so the edge rewrite and the node definition agree, and re-runs are
    stable.
    """
    return f"{CLUSTER_IRI_PREFIX}{sanitize_id(label)}_{sanitize_id(file_stem)}"


def _node_types(node: dict) -> list[str]:
    t = node.get("@type", [])
    return [t] if isinstance(t, str) else list(t)


def migrate_doc(doc: dict, file_stem: str) -> tuple[int, int]:
    """Rewrite bare requestedCluster ids + define missing clusters in place.

    Returns ``(edges_rewritten, cluster_nodes_added)``. ``file_stem`` (the peep
    filename stem) namespaces the minted IRIs so they are globally unique.

    Pass 1 collects every cluster ``@id`` already defined as a subject in this
    document. Pass 2 rewrites bare ``requestedCluster`` targets to their minted
    CURIE and records ``(minted_iri -> original_label)`` for any cluster not yet
    defined. Finally the missing cluster nodes are appended.
    """
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return 0, 0

    defined_subjects: set[str] = {
        node["@id"]
        for node in graph
        if isinstance(node, dict) and isinstance(node.get("@id"), str)
    }

    edges_rewritten = 0
    # minted IRI -> the original label to put on the freshly-defined node.
    # dict (not set) so the human label survives; first label wins for a given
    # minted IRI (deterministic — graph order is stable).
    to_define: dict[str, str] = {}

    for node in graph:
        if not isinstance(node, dict):
            continue
        rc = node.get(REQUESTED_CLUSTER)
        if rc is None:
            continue
        items = rc if isinstance(rc, list) else [rc]
        for item in items:
            if not isinstance(item, dict):
                continue
            tgt = item.get("@id")
            if not isinstance(tgt, str) or not is_bare_cluster_id(tgt):
                continue
            minted = mint_cluster_iri(tgt, file_stem)
            item["@id"] = minted
            edges_rewritten += 1
            if minted not in defined_subjects and minted not in to_define:
                to_define[minted] = tgt

    for minted, label in to_define.items():
        graph.append(
            {
                "@id": minted,
                "@type": ["owl:NamedIndividual", TOPIC_CLUSTER_TYPE, "skos:Concept"],
                "rdfs:label": label,
                "skos:prefLabel": label,
            }
        )

    return edges_rewritten, len(to_define)


def process_file(path: Path, dry_run: bool) -> tuple[int, int]:
    """Migrate one peep file. Returns ``(edges_rewritten, clusters_added)``."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    edges, clusters = migrate_doc(doc, path.stem)
    if edges and not dry_run:
        save_json(path, doc)
    return edges, clusters


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--krr-dir",
        type=Path,
        default=DEFAULT_KRR_DIR,
        help="Directory of *_peep.json files to scan (default: krr_outputs).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing any file.",
    )
    args = parser.parse_args(argv)

    krr_dir: Path = args.krr_dir
    if not krr_dir.is_dir():
        print(f"ERROR: not a directory: {krr_dir}")
        return 1

    print("=" * 70)
    print(f"Fix bare {REQUESTED_CLUSTER} IRIs -> {CLUSTER_IRI_PREFIX}<slug> (#564)")
    print(f"  Directory: {krr_dir}")
    print("=" * 70)

    files = sorted(krr_dir.glob(DEFAULT_PEEP_GLOB))
    files_changed = 0
    total_edges = 0
    total_clusters = 0
    for path in files:
        if _is_lfs_pointer(path):
            continue
        edges, clusters = process_file(path, args.dry_run)
        if edges:
            files_changed += 1
            total_edges += edges
            total_clusters += clusters

    verb = "would rewrite" if args.dry_run else "rewrote"
    define_verb = "would define" if args.dry_run else "defined"
    print(f"\n  Files scanned:        {len(files)}")
    print(f"  Files {verb}:     {files_changed}")
    print(f"  Edges {verb}:     {total_edges}")
    print(f"  Cluster nodes {define_verb}: {total_clusters}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
