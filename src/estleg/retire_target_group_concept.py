#!/usr/bin/env python3
"""Retire ``estleg:targetGroupConcept`` from the peeps (#709).

#609 materialised ``estleg:targetGroupConcept`` as a "SKOS counterpart" of
``estleg:targetGroup`` while the latter still carried bare enum strings. Since
#460 every ``estleg:targetGroup`` value is itself an ``estleg:TargetGroup_*``
IRI (120,421 objects on the root law peeps, 0 strings), so the second property
only restates a subset of the first: 27,609 law nodes carried it, against
91,780 carrying ``targetGroup``. #709 makes ``targetGroup`` the
``owl:ObjectProperty`` (range ``estleg:TargetGroup``) and deprecates
``targetGroupConcept`` in the CV; this migration removes the duplicate edges.

The transform only deletes the ``estleg:targetGroupConcept`` key. It refuses to
drop a concept link that ``estleg:targetGroup`` on the same node does not also
carry, so no addressee fact is lost: such a node is reported, not rewritten.

Default scope is the root law peeps (``krr_outputs/*_peep.json``). Pass
``--glob`` (relative to ``krr_outputs``, repeatable) for other sets, e.g.
``--glob 'regulations/**/*_peep.json'``. Dry run by default; ``--apply``
rewrites changed files atomically with ``save_json`` (same byte format as the
generators, so an unchanged node does not churn). A second run is a no-op.

    python3 scripts/retire_target_group_concept.py
    python3 scripts/retire_target_group_concept.py --apply
    python3 scripts/retire_target_group_concept.py --glob 'regulations/**/*_peep.json'
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from estleg.estleg_common import KRR_DIR, save_json

TARGET_GROUP_KEY = "estleg:targetGroup"
TARGET_GROUP_CONCEPT_KEY = "estleg:targetGroupConcept"
DEFAULT_GLOBS = ("*_peep.json",)
LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"
_FULL_PREFIX = "https://w3id.org/estleg/"


@dataclass
class Stats:
    files_scanned: int = 0
    files_skipped: int = 0
    files_changed: int = 0
    nodes_with_concept: int = 0
    nodes_stripped: int = 0
    nodes_kept_not_covered: list[str] = field(default_factory=list)


def _iris(value: object) -> set[str]:
    """Compact ``estleg:`` IRIs in a JSON-LD value (refs, strings, or lists)."""
    out: set[str] = set()
    items = value if isinstance(value, list) else [value]
    for item in items:
        raw = item.get("@id") if isinstance(item, dict) else item
        if not isinstance(raw, str):
            continue
        if raw.startswith(_FULL_PREFIX):
            raw = "estleg:" + raw[len(_FULL_PREFIX):]
        out.add(raw)
    return out


def retire_node(node: dict) -> bool | None:
    """Drop ``targetGroupConcept`` when ``targetGroup`` already carries every link.

    Returns True when the key was removed, False when the node has no such key,
    and None when it was kept because ``targetGroup`` does not cover it.
    """
    if TARGET_GROUP_CONCEPT_KEY not in node:
        return False
    concepts = _iris(node[TARGET_GROUP_CONCEPT_KEY])
    if not concepts <= _iris(node.get(TARGET_GROUP_KEY)):
        return None
    del node[TARGET_GROUP_CONCEPT_KEY]
    return True


def iter_paths(krr_dir: Path, globs: Iterable[str]) -> list[Path]:
    paths: set[Path] = set()
    for pattern in globs:
        paths.update(path for path in krr_dir.glob(pattern) if path.is_file())
    return sorted(paths)


def run(
    *,
    apply: bool,
    krr_dir: Path | None = None,
    globs: Iterable[str] = DEFAULT_GLOBS,
) -> Stats:
    root = krr_dir if krr_dir is not None else KRR_DIR
    stats = Stats()
    for path in iter_paths(root, globs):
        text = path.read_text(encoding="utf-8")
        if text.startswith(LFS_POINTER_PREFIX) or TARGET_GROUP_CONCEPT_KEY not in text:
            stats.files_skipped += 1
            continue
        try:
            doc = json.loads(text)
        except ValueError:
            stats.files_skipped += 1
            continue
        graph = doc.get("@graph") if isinstance(doc, dict) else None
        if not isinstance(graph, list):
            stats.files_skipped += 1
            continue
        stats.files_scanned += 1
        changed = False
        for node in graph:
            if not isinstance(node, dict):
                continue
            outcome = retire_node(node)
            if outcome is False:
                continue
            stats.nodes_with_concept += 1
            if outcome is None:
                stats.nodes_kept_not_covered.append(str(node.get("@id")))
                continue
            stats.nodes_stripped += 1
            changed = True
        if changed:
            stats.files_changed += 1
            if apply:
                save_json(path, doc)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Rewrite changed files.")
    parser.add_argument(
        "--glob",
        action="append",
        dest="globs",
        help="Path glob relative to krr_outputs (repeatable). Default: *_peep.json",
    )
    args = parser.parse_args(argv)
    globs = tuple(args.globs) if args.globs else DEFAULT_GLOBS
    stats = run(apply=args.apply, globs=globs)
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"retire estleg:targetGroupConcept ({mode}; globs={list(globs)})")
    print(f"  files with the key: {stats.files_scanned}")
    print(f"  nodes with the key: {stats.nodes_with_concept}")
    print(f"  nodes stripped: {stats.nodes_stripped}")
    print(f"  files {'changed' if args.apply else 'to change'}: {stats.files_changed}")
    kept = stats.nodes_kept_not_covered
    print(f"  nodes kept (targetGroup lacks a concept link): {len(kept)}")
    for nid in kept[:10]:
        print(f"    {nid}")
    return 1 if kept else 0


if __name__ == "__main__":
    raise SystemExit(main())
