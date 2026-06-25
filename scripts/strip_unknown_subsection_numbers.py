#!/usr/bin/env python3
"""Drop the ``Unknown_N`` IRI-dedup placeholder from subsectionNumber (#583).

``generate_all_laws.py`` builds each ``estleg:Subsection`` node's
``estleg:subsectionNumber`` from ``display_nr or suffix``. When a
``<loige>`` carries no display number, ``_loige_numbers`` falls back to
the literal ``"Unknown"`` and ``_dedupe_subsection_suffix`` makes it
unique as ``"Unknown_1"``, ``"Unknown_2"`` — a token meant only to keep
the *IRI* distinct, which then leaks into the human-facing number field.

7,755 subsection nodes (~6.9% of all Subsection nodes, ~600 law peeps)
store such a value. The subsections themselves are valid (they have real
``estleg:legalText``); only the number is bogus. A missing number is
honest; a fabricated ``"Unknown_5"`` is not — so this migration *removes*
the ``estleg:subsectionNumber`` key on any node whose value matches
``^Unknown_\\d+$``. The node's ``@id`` (which embeds the same dedup
suffix, e.g. ``…_Lg_Unknown_1``) is left untouched so graph closure /
cross-references are preserved.

Scope: top-level law peeps (``krr_outputs/*_peep.json``) plus the
state/KOV regulation peeps under ``krr_outputs/regulations/`` — i.e.
everything ``estleg_common.iter_peep_files`` yields (the defect is only
present in the root law peeps today, but scanning the regulation peeps
too makes the migration robust to future regenerations).

Safety / scope:
  * Only the exact ``^Unknown_\\d+$`` placeholder is removed; any real
    number (``"1"``, ``"1²"``, ``"5¹"`` …) is never touched.
  * Idempotent: a node with no ``estleg:subsectionNumber`` (or a real
    one) is a no-op, so a re-run reports 0 changes.
  * Atomic writes via ``estleg_common.save_json`` (tempfile + os.replace).
  * ``--dry-run`` reports counts without writing.

Run (offline, idempotent):

    .venv/bin/python scripts/strip_unknown_subsection_numbers.py
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from estleg_common import iter_peep_files, save_json

SUBSECTION_NUMBER_KEY = "estleg:subsectionNumber"

# The IRI-dedup placeholder: the literal word "Unknown" followed by the
# numeric suffix _dedupe_subsection_suffix appends. A real number never
# starts with "Unknown", so this can only match the leaked placeholder.
UNKNOWN_PLACEHOLDER_RE = re.compile(r"^Unknown_\d+$")


def _is_unknown_placeholder(value: object) -> bool:
    """True iff ``value`` is the ``Unknown_<digits>`` dedup placeholder.

    Handles both the plain-string form and the rare JSON-LD value-object
    form ``{"@value": "Unknown_1"}`` so the strip is shape-agnostic.
    """
    if isinstance(value, str):
        return bool(UNKNOWN_PLACEHOLDER_RE.match(value))
    if isinstance(value, dict):
        inner = value.get("@value")
        return isinstance(inner, str) and bool(UNKNOWN_PLACEHOLDER_RE.match(inner))
    return False


def strip_doc(doc: dict) -> int:
    """Remove placeholder ``subsectionNumber`` keys in place.

    Returns the number of nodes from which the key was removed. A node
    qualifies only when its ``estleg:subsectionNumber`` value matches the
    ``Unknown_<digits>`` placeholder; the value's slot is deleted
    entirely (a missing number is the honest representation). The node's
    ``@id`` and every other field are untouched.
    """
    graph = doc.get("@graph", [])
    if not isinstance(graph, list):
        return 0
    changed = 0
    for node in graph:
        if not isinstance(node, dict):
            continue
        if SUBSECTION_NUMBER_KEY not in node:
            continue
        if _is_unknown_placeholder(node[SUBSECTION_NUMBER_KEY]):
            del node[SUBSECTION_NUMBER_KEY]
            changed += 1
    return changed


def process_file(path: Path, dry_run: bool) -> int:
    """Strip placeholders in one peep file. Returns nodes changed."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    changed = strip_doc(doc)
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

    files = iter_peep_files()

    print("=" * 70)
    print(f"Strip {SUBSECTION_NUMBER_KEY} == 'Unknown_<N>' placeholder (#583)")
    print(f"  Peep files scanned: {len(files)}")
    print("=" * 70)

    files_changed = 0
    nodes_changed = 0
    for path in files:
        changed = process_file(path, args.dry_run)
        if changed:
            files_changed += 1
            nodes_changed += changed

    verb = "would strip" if args.dry_run else "stripped"
    print(f"\n  Files {verb}: {files_changed}")
    print(f"  Subsection numbers {verb}: {nodes_changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
