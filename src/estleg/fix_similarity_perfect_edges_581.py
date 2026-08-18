#!/usr/bin/env python3
"""Drop boilerplate-driven perfect-1.0 KOV similarity edges (#581).

The KOV act-level similarity index
(``krr_outputs/similarity/kov_similarity_index.json``) holds 436 distinct
undirected intra-bucket act pairs scoring an exact cosine 1.0 between DISTINCT
municipal acts. **396** of those are pairs where BOTH acts have *no provision
body text* — each act's TF-IDF document is nothing but the verbatim shared
enabling-law preamble boilerplate, because the distinguishing substance of an
annex-style municipal decision (the actual placename) was never extracted into
the ontology. A 1.0 cosine between two distinct acts whose only indexed text
is shared boilerplate is a false positive, not a real "these are identical"
edge (#581).

This is the OFFLINE, deterministic surgical post-process that removes exactly
those 396 false-identical edges from the sidecar index, WITHOUT re-running the
network-backed / full-rebuild similarity pipeline:

    For every intra-bucket peer entry whose score == 1.0 (within EPSILON)
    where BOTH endpoints lack provision body text, remove that peer edge from
    BOTH directions (the intra-bucket relation is undirected: source A lists B
    and B lists A). ``regulationCount`` (bucket membership) is left untouched;
    only ``totalPairs`` (the count of *directed* peer entries written) is
    decremented by the number of directed entries removed.

"Lacks provision body text" is classified by reading the KOV peep files
READ-ONLY (``krr_outputs/regulations/kov/**/*_peep.json``): an act has
provision text iff some child ``_Par_`` node carries a non-blank
``estleg:summary`` or ``estleg:legalText``. This mirrors the generator's
:func:`generate_similarity_index._act_has_provision_text` guard so the
post-process and a fresh generator run agree on which pairs are garbage.

Root cause vs. this script:
  * Root cause is fixed in ``generate_similarity_index.py``: annex body text
    is now indexed (deferred — no annex bodies in the corpus yet) AND the
    intra-bucket pairing path drops perfect-1.0 pairs whose both endpoints are
    provision-text-empty. A full regen would therefore not re-emit these edges.
  * This script applies the same correction to the *already-committed* sidecar
    index without a regen.

Safety / scope:
  * Writes ONLY ``krr_outputs/similarity/kov_similarity_index.json``. The KOV
    peep files are opened READ-ONLY for act classification and are never
    modified.
  * Idempotent: a second run finds 0 perfect-both-empty edges and writes
    nothing.
  * Atomic write via ``estleg_common.save_json`` (tempfile + os.replace).
  * LFS-pointer guard: skips the target if its first line is the git-lfs
    pointer header (kov_similarity_index.json is NOT LFS, but the guard is the
    model script's safety contract — never rewrite a pointer stub into real
    JSON).
  * ``--dry-run`` reports counts without writing.

DEFERRED (the other half): the matching ``estleg:similarAct`` /
``estleg:Similarity_<src>_<tgt>`` reified back-links inside the KOV peep files
(``krr_outputs/regulations/kov/**/*_peep.json``) still carry the dropped edges.
Those peep files are owned by a separate change and are NOT touched here; they
must be re-synced by the KOV-peep owner or by an offline generator re-run
(``generate_similarity_index.py``, which now applies the same guard).

Run (offline, idempotent):

    .venv/bin/python scripts/fix_similarity_perfect_edges_581.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from estleg.estleg_common import REPO_ROOT, jsonld_text, save_json

KOV_INDEX_PATH = (
    REPO_ROOT / "krr_outputs" / "similarity" / "kov_similarity_index.json"
)
KOV_PEEP_GLOB_ROOT = REPO_ROOT / "krr_outputs" / "regulations" / "kov"

# A score this close to 1.0 is treated as a perfect duplicate edge. Matches
# generate_similarity_index.KOV_PERFECT_DUP_* (the index rounds scores to 3 dp,
# so the stored value is exactly 1.0 for a perfect pair; the epsilon absorbs
# any residual float wobble).
PERFECT_DUP_THRESHOLD = 1.0
PERFECT_DUP_EPSILON = 1e-9

LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"


def _is_lfs_pointer(path: Path) -> bool:
    """Return True if ``path``'s first line is a git-lfs pointer header.

    Defensive guard (the model script's safety contract): never rewrite an
    un-smudged LFS pointer stub into real JSON. ``kov_similarity_index.json``
    is not LFS-tracked, so this is expected to be False in practice.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            first_line = f.readline()
    except (OSError, UnicodeDecodeError):
        return False
    return first_line.startswith(LFS_POINTER_PREFIX)


def _act_has_provision_text(doc: dict) -> bool:
    """Return True when any ``_Par_`` node carries a non-blank summary/legalText.

    Mirrors ``generate_similarity_index._act_has_provision_text`` so this
    offline post-process classifies acts identically to the generator guard.
    """
    for node in doc.get("@graph", []):
        node_id = node.get("@id", "")
        if not isinstance(node_id, str) or "_Par_" not in node_id:
            continue
        if jsonld_text(node.get("estleg:summary", "")).strip():
            return True
        if jsonld_text(node.get("estleg:legalText", "")).strip():
            return True
    return False


def build_empty_body_act_set(peep_root: Path) -> set[str]:
    """Scan KOV peep files (READ-ONLY) for acts lacking provision body text.

    Returns the set of municipal-act IRIs (``estleg:Reg_<id>_Map_...``) whose
    document carries no ``_Par_`` provision with a non-blank
    ``estleg:summary`` / ``estleg:legalText``. These are the acts whose TF-IDF
    document is boilerplate-only; a perfect-1.0 edge between two of them is the
    #581 false positive.
    """
    empty_body: set[str] = set()
    for path in sorted(peep_root.glob("**/*_peep.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        act_iri: str | None = None
        for node in doc.get("@graph", []):
            types = node.get("@type", [])
            types = types if isinstance(types, list) else [types]
            if "estleg:MunicipalRegulation" in types:
                node_id = node.get("@id")
                if isinstance(node_id, str) and node_id.startswith("estleg:"):
                    act_iri = node_id
                break
        if act_iri is None:
            continue
        if not _act_has_provision_text(doc):
            empty_body.add(act_iri)
    return empty_body


def _is_perfect(score: object) -> bool:
    """True when ``score`` is numerically within EPSILON of a perfect 1.0."""
    if not isinstance(score, (int, float)):
        return False
    return abs(float(score) - PERFECT_DUP_THRESHOLD) <= PERFECT_DUP_EPSILON


def prune_perfect_boilerplate_edges(
    index: dict, empty_body: set[str]
) -> dict:
    """Remove perfect-1.0 intra-bucket edges between two empty-body acts.

    Mutates ``index`` in place and returns a stats dict::

        {
            "directed_peer_entries_scanned": int,
            "perfect_pairs_both_empty": int,   # undirected
            "directed_entries_removed": int,
            "buckets_touched": int,
        }

    An edge is removed (from both directions) iff its score is perfect AND both
    endpoints are in ``empty_body``. The removal is symmetric by construction:
    each undirected pair is identified by its sorted endpoint tuple, and BOTH
    directed peer entries (A->B and B->A) matching that tuple are dropped, so a
    re-run finds nothing left to remove. Now-empty ``peers`` lists drop their
    ``pairs`` row; ``regulationCount`` is never touched.
    """
    # First pass: identify the undirected boilerplate-perfect pairs.
    removable_pairs: set[tuple[str, str]] = set()
    directed_scanned = 0
    buckets = index.get("buckets", {})
    for bucket in buckets.values():
        for pair in bucket.get("pairs", []):
            source = pair.get("source")
            if not isinstance(source, str):
                continue
            for peer in pair.get("peers", []):
                directed_scanned += 1
                target = peer.get("target")
                if not isinstance(target, str) or target == source:
                    continue
                if (
                    _is_perfect(peer.get("score"))
                    and source in empty_body
                    and target in empty_body
                ):
                    removable_pairs.add(tuple(sorted((source, target))))

    # Second pass: drop every directed peer entry that belongs to a removable
    # undirected pair, then prune now-empty rows.
    directed_removed = 0
    buckets_touched = 0
    for bucket in buckets.values():
        bucket_changed = False
        new_pairs = []
        for pair in bucket.get("pairs", []):
            source = pair.get("source")
            kept_peers = []
            for peer in pair.get("peers", []):
                target = peer.get("target")
                key = (
                    tuple(sorted((source, target)))
                    if isinstance(source, str) and isinstance(target, str)
                    else None
                )
                if key is not None and key in removable_pairs:
                    directed_removed += 1
                    bucket_changed = True
                    continue
                kept_peers.append(peer)
            if kept_peers:
                if len(kept_peers) != len(pair.get("peers", [])):
                    pair = {**pair, "peers": kept_peers}
                new_pairs.append(pair)
            else:
                # Source row emptied out — drop it entirely.
                bucket_changed = True
        if bucket_changed:
            bucket["pairs"] = new_pairs
            buckets_touched += 1

    # Keep totalPairs consistent: it counts directed peer entries written.
    if "totalPairs" in index and isinstance(index["totalPairs"], int):
        index["totalPairs"] = index["totalPairs"] - directed_removed

    return {
        "directed_peer_entries_scanned": directed_scanned,
        "perfect_pairs_both_empty": len(removable_pairs),
        "directed_entries_removed": directed_removed,
        "buckets_touched": buckets_touched,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--index-path",
        type=Path,
        default=KOV_INDEX_PATH,
        help=(
            "Path to kov_similarity_index.json "
            "(default: krr_outputs/similarity/kov_similarity_index.json)."
        ),
    )
    parser.add_argument(
        "--peep-root",
        type=Path,
        default=KOV_PEEP_GLOB_ROOT,
        help=(
            "Root of KOV peep files used READ-ONLY to classify empty-body "
            "acts (default: krr_outputs/regulations/kov)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing the index.",
    )
    args = parser.parse_args(argv)

    index_path: Path = args.index_path
    peep_root: Path = args.peep_root

    print("=" * 70)
    print("Drop boilerplate perfect-1.0 KOV similarity edges (#581)")
    print(f"  Index:     {index_path}")
    print(f"  Peep root: {peep_root}")
    print("=" * 70)

    if not index_path.is_file():
        print(f"ERROR: index not found: {index_path}")
        return 1
    if _is_lfs_pointer(index_path):
        print(
            f"SKIP: {index_path} is an un-smudged git-lfs pointer; "
            "run `git lfs pull` first. Refusing to overwrite the pointer."
        )
        return 1
    if not peep_root.is_dir():
        print(f"ERROR: peep root not a directory: {peep_root}")
        return 1

    index = json.loads(index_path.read_text(encoding="utf-8"))

    print("\n  Classifying KOV acts by provision-text presence (read-only)...")
    empty_body = build_empty_body_act_set(peep_root)
    print(f"  Acts with NO provision body text: {len(empty_body)}")

    stats = prune_perfect_boilerplate_edges(index, empty_body)

    print("\n  Directed peer entries scanned: "
          f"{stats['directed_peer_entries_scanned']}")
    print("  Perfect-1.0 pairs with BOTH endpoints empty-body (undirected): "
          f"{stats['perfect_pairs_both_empty']}")
    print("  Directed peer entries removed: "
          f"{stats['directed_entries_removed']}")
    print(f"  Buckets touched: {stats['buckets_touched']}")

    if stats["directed_entries_removed"] == 0:
        print("\n  Nothing to remove — index already clean. No write.")
        return 0

    if args.dry_run:
        print("\n  --dry-run: not writing.")
        return 0

    save_json(index_path, index)
    print(f"\n  Wrote {index_path}")
    print(
        "  NOTE (deferred): estleg:similarAct / estleg:Similarity_* back-links "
        "in krr_outputs/regulations/kov/**/*_peep.json still carry the dropped "
        "edges and must be re-synced by the KOV-peep owner or an offline "
        "generator re-run."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
