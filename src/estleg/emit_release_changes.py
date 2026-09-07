#!/usr/bin/env python3
"""Emit a machine-readable inter-release IRI change record (#549).

Diffs two IRI sets (typically two JSON-LD graphs, or INDEX law names when
``combined_ontology.jsonld`` is not materialised) and writes a
``dcat:Dataset`` / ``estleg:ReleaseDelta`` JSON-LD document.

The committed ``krr_outputs/changes-0.11.0.jsonld`` is the first published
delta: INDEX ``deprecated_laws`` (removed/replaced slugs) versus live
INDEX ``laws`` names. Listed IRIs are capped; counts stay complete.

    python3 scripts/emit_release_changes.py
    python3 scripts/emit_release_changes.py --old old.jsonld --new new.jsonld
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from estleg.estleg_common import CONTEXT, KRR_DIR, NS, ONTOLOGY_VERSION, save_json

LISTED_IRI_CAP = 50
DEFAULT_OUTPUT = KRR_DIR / f"changes-{ONTOLOGY_VERSION}.jsonld"
INDEX_PATH = KRR_DIR / "INDEX.json"

OLD_LABEL_DEFAULT = "INDEX deprecated_laws (removed/replaced)"
NEW_LABEL_DEFAULT = f"INDEX laws {ONTOLOGY_VERSION}"


def law_name_to_iri(name: str) -> str:
    """Map an INDEX law slug to a stable estleg IRI."""
    slug = name.strip()
    if slug.startswith(("http://", "https://", "estleg:")):
        return slug
    return f"{NS}{slug}"


def _collect_at_ids(value: Any, out: set[str]) -> None:
    if isinstance(value, dict):
        node_id = value.get("@id")
        if isinstance(node_id, str) and node_id:
            out.add(node_id)
        for child in value.values():
            _collect_at_ids(child, out)
    elif isinstance(value, list):
        for child in value:
            _collect_at_ids(child, out)


def index_live_law_names(doc: dict) -> list[str]:
    """Return live INDEX ``laws[].name`` slugs in document order."""
    laws = doc.get("laws")
    if not isinstance(laws, list):
        return []
    names: list[str] = []
    for entry in laws:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def index_deprecated_law_names(doc: dict) -> list[str]:
    """Return INDEX ``deprecated_laws.entries[].name`` slugs in document order."""
    block = doc.get("deprecated_laws")
    if not isinstance(block, dict):
        return []
    entries = block.get("entries")
    if not isinstance(entries, list):
        return []
    names: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def collect_iris_from_jsonld(path: Path | str) -> set[str]:
    """Collect ``@id`` values from JSON-LD; INDEX law names are a fallback.

    Walking ``@id`` covers combined graphs and ordinary peeps. When *path*
    is ``INDEX.json`` (no ``@id``s), live + deprecated law slugs are
    converted to IRIs so tests and the first published delta do not need
    ``combined_ontology.jsonld``.
    """
    source = Path(path)
    with source.open(encoding="utf-8") as handle:
        doc = json.load(handle)
    iris: set[str] = set()
    _collect_at_ids(doc, iris)
    if isinstance(doc, dict):
        for name in index_live_law_names(doc):
            iris.add(law_name_to_iri(name))
        for name in index_deprecated_law_names(doc):
            iris.add(law_name_to_iri(name))
    return iris


def snapshot_iris_from_index(
    doc: dict,
    *,
    deprecated: bool,
) -> set[str]:
    """IRIs for the committed 0.11.0 snapshot (deprecated vs live INDEX)."""
    names = index_deprecated_law_names(doc) if deprecated else index_live_law_names(doc)
    return {law_name_to_iri(name) for name in names}


def diff_iris(old: set[str], new: set[str]) -> dict[str, Any]:
    """Return added/removed IRI lists (sorted) plus full counts."""
    added = sorted(new - old)
    removed = sorted(old - new)
    return {
        "added": added,
        "removed": removed,
        "addedCount": len(added),
        "removedCount": len(removed),
    }


def build_change_record(
    old_label: str,
    new_label: str,
    diff: dict[str, Any],
    extra_counts: dict[str, Any] | None = None,
    *,
    listed_cap: int = LISTED_IRI_CAP,
    version: str = ONTOLOGY_VERSION,
) -> dict[str, Any]:
    """Build a ``dcat:Dataset`` / ``estleg:ReleaseDelta`` JSON-LD document."""
    added = list(diff.get("added") or [])
    removed = list(diff.get("removed") or [])
    added_count = int(diff.get("addedCount", len(added)))
    removed_count = int(diff.get("removedCount", len(removed)))
    record: dict[str, Any] = {
        "@context": dict(CONTEXT),
        "@id": f"{NS}dataset/changes-{version}",
        "@type": ["dcat:Dataset", "estleg:ReleaseDelta"],
        "dcterms:title": f"Estonian Legal Ontology change record {version}",
        "dcterms:description": (
            f"Machine-readable IRI delta ({old_label} → {new_label}). "
            f"Listed IRIs are capped at {listed_cap}; counts are complete."
        ),
        "owl:versionInfo": version,
        "estleg:comparedFrom": old_label,
        "estleg:comparedTo": new_label,
        "estleg:addedCount": added_count,
        "estleg:removedCount": removed_count,
        "estleg:listedIriCap": listed_cap,
        "estleg:added": added[:listed_cap],
        "estleg:removed": removed[:listed_cap],
    }
    if extra_counts:
        record.update(extra_counts)
    return record


def build_index_deprecated_vs_live_record(
    index_path: Path = INDEX_PATH,
    *,
    listed_cap: int = LISTED_IRI_CAP,
    version: str = ONTOLOGY_VERSION,
) -> dict[str, Any]:
    """Honest first published delta: deprecated INDEX slugs vs live laws."""
    with Path(index_path).open(encoding="utf-8") as handle:
        index = json.load(handle)
    if not isinstance(index, dict):
        raise ValueError(f"{index_path} is not a JSON object")
    old = snapshot_iris_from_index(index, deprecated=True)
    new = snapshot_iris_from_index(index, deprecated=False)
    return build_change_record(
        OLD_LABEL_DEFAULT,
        NEW_LABEL_DEFAULT,
        diff_iris(old, new),
        listed_cap=listed_cap,
        version=version,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--old",
        type=Path,
        help="Previous JSON-LD (or INDEX.json). Default: INDEX deprecated_laws.",
    )
    parser.add_argument(
        "--new",
        type=Path,
        help="Current JSON-LD (or INDEX.json). Default: INDEX live laws.",
    )
    parser.add_argument(
        "--index",
        type=Path,
        default=INDEX_PATH,
        help=f"INDEX.json used for the deprecated-vs-live snapshot (default: {INDEX_PATH}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Destination JSON-LD (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--cap",
        type=int,
        default=LISTED_IRI_CAP,
        help=f"Max listed IRIs per added/removed array (default: {LISTED_IRI_CAP}).",
    )
    parser.add_argument(
        "--old-label",
        default=OLD_LABEL_DEFAULT,
        help="Label stored as estleg:comparedFrom.",
    )
    parser.add_argument(
        "--new-label",
        default=NEW_LABEL_DEFAULT,
        help="Label stored as estleg:comparedTo.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if (args.old is None) ^ (args.new is None):
        print("error: --old and --new must be passed together")
        return 2
    if args.old is not None and args.new is not None:
        record = build_change_record(
            args.old_label,
            args.new_label,
            diff_iris(collect_iris_from_jsonld(args.old), collect_iris_from_jsonld(args.new)),
            listed_cap=args.cap,
        )
    else:
        record = build_index_deprecated_vs_live_record(
            args.index,
            listed_cap=args.cap,
        )
    save_json(args.output, record)
    print(
        f"Wrote {args.output} "
        f"(added={record['estleg:addedCount']}, "
        f"removed={record['estleg:removedCount']}, "
        f"listed_cap={record['estleg:listedIriCap']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
