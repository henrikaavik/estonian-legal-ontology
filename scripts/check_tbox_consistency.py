#!/usr/bin/env python3
"""Detect ABox contradictions the #522 T-Box axioms are meant to catch.

Does **not** run owlrl over ``combined_ontology.jsonld``. It inspects JSON-LD
instance graphs for two local inconsistencies that a reasoner would only see
after rewriting tokens to IRIs / closing FunctionalProperty:

  * a node whose ``estleg:temporalStatus`` list contains both ``inForce`` and
    ``repealed`` (the T-Box individuals are ``owl:disjointWith``);
  * a node with two distinct ``estleg:partOfAct`` IRIs (the property is
    ``owl:FunctionalProperty``).

ABox values stay the SHACL string tokens. Run on a fixture document or a
handful of committed peeps — never the 265 MB combined graph.

    python3 scripts/check_tbox_consistency.py fixture.jsonld
    python3 scripts/check_tbox_consistency.py --sample-peeps
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from estleg_common import KRR_DIR, canonical_estleg_ref

TEMPORAL_STATUS_PROP = "estleg:temporalStatus"
PART_OF_ACT_PROP = "estleg:partOfAct"
TOKEN_IN_FORCE = "inForce"
TOKEN_REPEALED = "repealed"

KIND_TEMPORAL_CONFLICT = "temporalStatus_inForce_and_repealed"
KIND_PARTOFACT_NOT_FUNCTIONAL = "partOfAct_not_functional"

# High-traffic committed peeps (same trio as the #531 staleness canary).
SAMPLE_PEEPS: tuple[str, ...] = (
    "perekonnaseadus_peep.json",
    "karistusseadustik_osa1_peep.json",
    "eesti_vabariigi_pohiseadus_peep.json",
)

_LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"

# Provision-family types: FunctionalProperty applies to every subject, but
# the #522 report names "a provision in two acts" when the node is one of these.
_PROVISION_TYPES = frozenset(
    {
        "estleg:LegalProvision",
        "estleg:KovProvision",
        "estleg:Subsection",
    }
)


@dataclass(frozen=True)
class Violation:
    kind: str
    node_id: str
    message: str
    values: tuple[str, ...] = ()


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def _node_types(node: dict) -> set[str]:
    return {item for item in _as_list(node.get("@type")) if isinstance(item, str)}


def _literal_tokens(value: object) -> list[str]:
    tokens: list[str] = []
    for item in _as_list(value):
        if isinstance(item, str):
            tokens.append(item)
        elif isinstance(item, dict) and item.get("@value") is not None:
            tokens.append(str(item["@value"]))
    return tokens


def _canonical_iri(iri: str) -> str:
    return canonical_estleg_ref(iri) or iri


def _iri_ids(value: object) -> list[str]:
    ids: list[str] = []
    for item in _as_list(value):
        if isinstance(item, str) and item:
            ids.append(_canonical_iri(item))
        elif isinstance(item, dict):
            ref = item.get("@id")
            if isinstance(ref, str) and ref:
                ids.append(_canonical_iri(ref))
    return ids


def iter_graph_nodes(doc: object) -> list[dict]:
    """Yield JSON-LD node dicts from a document, ``@graph``, or node list."""
    if isinstance(doc, list):
        return [item for item in doc if isinstance(item, dict)]
    if not isinstance(doc, dict):
        return []
    graph = doc.get("@graph")
    if isinstance(graph, list):
        return [item for item in graph if isinstance(item, dict)]
    if "@id" in doc or TEMPORAL_STATUS_PROP in doc or PART_OF_ACT_PROP in doc:
        return [doc]
    return []


def check_nodes(nodes: Iterable[dict]) -> list[Violation]:
    """Return T-Box consistency violations for in-memory JSON-LD nodes."""
    violations: list[Violation] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("@id")
        if not isinstance(node_id, str) or not node_id:
            node_id = "<no @id>"

        tokens = set(_literal_tokens(node.get(TEMPORAL_STATUS_PROP)))
        if TOKEN_IN_FORCE in tokens and TOKEN_REPEALED in tokens:
            ordered = tuple(sorted(tokens))
            violations.append(
                Violation(
                    kind=KIND_TEMPORAL_CONFLICT,
                    node_id=node_id,
                    message=(
                        f"{node_id} has {TEMPORAL_STATUS_PROP} both "
                        f"{TOKEN_IN_FORCE} and {TOKEN_REPEALED}: {ordered}"
                    ),
                    values=ordered,
                )
            )

        act_iris = tuple(dict.fromkeys(_iri_ids(node.get(PART_OF_ACT_PROP))))
        if len(act_iris) > 1:
            label = "provision" if (_node_types(node) & _PROVISION_TYPES) else "node"
            violations.append(
                Violation(
                    kind=KIND_PARTOFACT_NOT_FUNCTIONAL,
                    node_id=node_id,
                    message=(
                        f"{label} {node_id} has {len(act_iris)} distinct "
                        f"{PART_OF_ACT_PROP} IRIs: {act_iris}"
                    ),
                    values=act_iris,
                )
            )
    return violations


def check_document(doc: object) -> list[Violation]:
    """Check a parsed JSON-LD document (dict with ``@graph``, node, or list)."""
    return check_nodes(iter_graph_nodes(doc))


def _is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as handle:
            first = handle.readline()
    except (OSError, UnicodeDecodeError):
        return False
    return first.startswith(_LFS_POINTER_PREFIX)


def check_file(path: Path) -> list[Violation]:
    """Load one JSON-LD file and check it. LFS pointers and bad JSON are skipped."""
    if _is_lfs_pointer(path):
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return []
    return check_document(doc)


def sample_peep_paths(krr_dir: Path | None = None) -> list[Path]:
    root = krr_dir if krr_dir is not None else KRR_DIR
    return [root / name for name in SAMPLE_PEEPS]


def check_sample_peeps(krr_dir: Path | None = None) -> list[tuple[Path, Violation]]:
    """Walk the committed sample peeps; skip missing / LFS-pointer files."""
    found: list[tuple[Path, Violation]] = []
    for path in sample_peep_paths(krr_dir):
        if not path.is_file():
            continue
        for violation in check_file(path):
            found.append((path, violation))
    return found


def _print_violations(
    labeled: Sequence[tuple[str, Violation]],
) -> None:
    for label, violation in labeled:
        print(f"    {label}: {violation.message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="JSON-LD fixture or peep files to check",
    )
    parser.add_argument(
        "--sample-peeps",
        action="store_true",
        help="Also walk a few committed high-traffic peeps under krr_outputs/",
    )
    args = parser.parse_args(argv)
    if not args.paths and not args.sample_peeps:
        parser.error("provide JSON-LD path(s) and/or --sample-peeps")

    labeled: list[tuple[str, Violation]] = []
    scanned = 0

    for path in args.paths:
        scanned += 1
        for violation in check_file(path):
            labeled.append((str(path), violation))

    if args.sample_peeps:
        print("Sample peeps:")
        for path in sample_peep_paths():
            if not path.is_file():
                print(f"  skip (missing): {path.name}")
                continue
            if _is_lfs_pointer(path):
                print(f"  skip (LFS pointer): {path.name}")
                continue
            scanned += 1
            hits = check_file(path)
            print(f"  {path.name}: {len(hits)} violation(s)")
            for violation in hits:
                labeled.append((path.name, violation))

    print("=" * 70)
    print("T-Box consistency checker (#522)")
    print(f"  Graphs scanned: {scanned}")
    print(f"  Violations:     {len(labeled)}")
    print("=" * 70)

    if labeled:
        print()
        _print_violations(labeled)
        return 1

    print("\n  OK: no inForce+repealed temporalStatus lists; no multi-act partOfAct.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
