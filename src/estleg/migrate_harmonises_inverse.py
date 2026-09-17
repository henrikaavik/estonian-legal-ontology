#!/usr/bin/env python3
"""Rename the inverted harmonisation back-edge to estleg:harmonises (#425).

`estleg:harmonisedWith` was used in BOTH directions in the corpus:

  * Law peeps (correct):  Act node ──harmonisedWith──▶ estleg:Harmonisation_<celex>
  * Harm files (inverted): estleg:Harmonisation_<celex> ──harmonisedWith──▶ Act IRI

One predicate carrying both directions makes SPARQL direction-ambiguous and
breaks any domain/range-keyed SHACL shape. ``generate_harmonisation_links.py``
now emits the inverse property ``estleg:harmonises`` (HarmonisationLink → Act)
on the aggregate node; this migration rewrites the 158 *already committed*
``krr_outputs/harmonisation/harmonisation_by_directive/harm_<celex>.json`` files
to match — without re-running the network-backed harmonisation pipeline.

It is a pure offline string-key rename:

    on every node whose @type includes estleg:HarmonisationLink and that
    carries estleg:harmonisedWith, rename that key to estleg:harmonises
    (the object IRIs — the transposing acts — are untouched, so graph
    closure is preserved: every Act IRI that resolved under the old name
    resolves under the new one).

Safety / scope:
  * Only the inverted edge is touched: the rename fires *solely* on
    subject nodes typed estleg:HarmonisationLink. The correct-direction
    edge in the law peeps (Act → link) is never visited because this
    script only walks ``harmonisation_by_directive/``.
  * Idempotent: a node that already uses estleg:harmonises (or has no
    estleg:harmonisedWith) is a no-op, so a re-run reports 0 changes.
  * Atomic writes via ``estleg_common.save_json`` (tempfile + os.replace).
  * ``--dry-run`` reports counts without writing.

Run (offline, idempotent, expected 158 files):

    .venv/bin/python scripts/migrate_harmonises_inverse.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from estleg.estleg_common import REPO_ROOT, save_json

OLD_PREDICATE = "estleg:harmonisedWith"
NEW_PREDICATE = "estleg:harmonises"
HARMONISATION_LINK_TYPE = "estleg:HarmonisationLink"

DEFAULT_BY_DIRECTIVE_DIR = (
    REPO_ROOT / "krr_outputs" / "harmonisation" / "harmonisation_by_directive"
)


def _node_types(node: dict) -> list[str]:
    t = node.get("@type", [])
    return [t] if isinstance(t, str) else list(t)


def migrate_doc(doc: dict) -> int:
    """Rename the inverted back-edge on HarmonisationLink nodes in place.

    Returns the number of nodes rewritten. A node qualifies only when it is
    typed ``estleg:HarmonisationLink`` AND carries ``estleg:harmonisedWith``
    (the inverted direction). Preserves key order by rebuilding the node dict
    with the renamed key in the same slot, and leaves the value (the list of
    transposing-act IRI refs) untouched.
    """
    graph = doc.get("@graph", [])
    if not isinstance(graph, list):
        return 0
    changed = 0
    for idx, node in enumerate(graph):
        if not isinstance(node, dict):
            continue
        if OLD_PREDICATE not in node:
            continue
        if HARMONISATION_LINK_TYPE not in _node_types(node):
            continue
        # Rebuild preserving insertion order, swapping the key in place. If
        # both keys somehow coexist, merge the inverted value into the
        # canonical one (defensive — should not happen in practice).
        rebuilt: dict = {}
        for key, value in node.items():
            if key == OLD_PREDICATE:
                if NEW_PREDICATE in rebuilt:
                    existing = rebuilt[NEW_PREDICATE]
                    existing = existing if isinstance(existing, list) else [existing]
                    extra = value if isinstance(value, list) else [value]
                    rebuilt[NEW_PREDICATE] = existing + extra
                else:
                    rebuilt[NEW_PREDICATE] = value
            elif key == NEW_PREDICATE and OLD_PREDICATE in node:
                # New key already present alongside the old one — keep it; the
                # old key's value is merged in when we reach OLD_PREDICATE.
                rebuilt[NEW_PREDICATE] = value
            else:
                rebuilt[key] = value
        graph[idx] = rebuilt
        changed += 1
    return changed


def process_file(path: Path, dry_run: bool) -> int:
    """Migrate one harm file. Returns the number of nodes rewritten."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    changed = migrate_doc(doc)
    if changed and not dry_run:
        save_json(path, doc)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--by-directive-dir",
        type=Path,
        default=DEFAULT_BY_DIRECTIVE_DIR,
        help=(
            "Directory of harm_<celex>.json files to migrate "
            "(default: krr_outputs/harmonisation/harmonisation_by_directive)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing any file.",
    )
    args = parser.parse_args(argv)

    by_dir: Path = args.by_directive_dir
    if not by_dir.is_dir():
        print(f"ERROR: not a directory: {by_dir}")
        return 1

    print("=" * 70)
    print(f"Migrate {OLD_PREDICATE} -> {NEW_PREDICATE} on HarmonisationLink nodes (#425)")
    print(f"  Directory: {by_dir}")
    print("=" * 70)

    files = sorted(by_dir.glob("harm_*.json"))
    files_changed = 0
    nodes_changed = 0
    for path in files:
        changed = process_file(path, args.dry_run)
        if changed:
            files_changed += 1
            nodes_changed += changed

    verb = "would rename" if args.dry_run else "renamed"
    print(f"\n  Files scanned:  {len(files)}")
    print(f"  Files {verb}: {files_changed}")
    print(f"  Nodes {verb}: {nodes_changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
