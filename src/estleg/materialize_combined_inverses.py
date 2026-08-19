#!/usr/bin/env python3
"""Materialize inverse, dropped-forward, and partOfAct-closure edges (#520).

``generate_inverse_references`` writes some inverses onto peeps. Combined
never called that pass and dropped several forwards (issuedUnder,
interpretsLaw) plus the transposition / authority / subsection joins.
This module is the in-graph half: given the in-memory combined node list
it asserts both directions of :data:`INVERSE_PAIRS` (except the version
layer, #561) and a direct ``estleg:partOfAct`` from every descendant to
its act root.

    python3 -m estleg.materialize_combined_inverses --patch-combined
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path

from estleg.estleg_common import KRR_DIR
from estleg.generate_inverse_references import (
    COMBINED_SKIP_INVERSE_PAIRS,
    INVERSE_PAIRS,
)

COMBINED_NAME = "combined_ontology.jsonld"

# Invert competentAuthority only from act-like subjects so Institution
# nodes list laws, not every provision that mentioned them.
ACT_LIKE_TYPES = frozenset(
    {
        "estleg:Act",
        "estleg:Law",
        "estleg:NationalRegulation",
        "estleg:MunicipalRegulation",
        "estleg:GovernmentRegulation",
        "estleg:MinisterialRegulation",
        "estleg:ParliamentaryResolution",
        "estleg:PresidentialDecree",
        "estleg:DomesticRegulation",
    }
)

def node_types(node: dict) -> list[str]:
    value = node.get("@type") or []
    if isinstance(value, str):
        return [value]
    return [item for item in value if isinstance(item, str)]


def iri_values(value: object) -> list[str]:
    """Flatten a JSON-LD IRI slot to compact/string ids, first-seen order."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        iri = value.get("@id")
        return [iri] if isinstance(iri, str) and iri else []
    if isinstance(value, list):
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            for iri in iri_values(item):
                if iri not in seen:
                    seen.add(iri)
                    out.append(iri)
        return out
    return []


def as_iri_list(iris: Iterable[str]) -> list[dict[str, str]]:
    return [{"@id": iri} for iri in iris]


def merge_iri_list(existing: object, extra: Iterable[str]) -> list[dict[str, str]]:
    merged: list[str] = []
    seen: set[str] = set()
    for iri in iri_values(existing) + list(extra):
        if iri not in seen:
            seen.add(iri)
            merged.append(iri)
    return as_iri_list(merged)


def is_act_like(types: Iterable[str]) -> bool:
    return any(item in ACT_LIKE_TYPES for item in types)


def plan_inverse_updates(nodes: list[dict]) -> dict[str, dict]:
    """Compute property patches that close inverse/forward/partOfAct gaps.

    Returns ``{node_id: {predicate: value}}`` only for nodes that change.
    Does not create new nodes — inverses attach only when the object IRI
    already exists in ``nodes``.
    """
    by_id: dict[str, dict] = {}
    types: dict[str, list[str]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("@id")
        if not isinstance(nid, str) or not nid:
            continue
        by_id[nid] = node
        types[nid] = node_types(node)

    incoming: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    parent: dict[str, str] = {}
    direct_act: dict[str, str] = {}

    for nid, node in by_id.items():
        for target in iri_values(node.get("estleg:parentProvision")):
            parent[nid] = target
        for target in iri_values(node.get("estleg:isPartOf")):
            parent.setdefault(nid, target)
        for target in iri_values(node.get("estleg:partOfAct")):
            direct_act[nid] = target
        for fwd, inv in INVERSE_PAIRS:
            if (fwd, inv) in COMBINED_SKIP_INVERSE_PAIRS:
                continue
            if fwd == "estleg:competentAuthority" and not is_act_like(types[nid]):
                continue
            for target in iri_values(node.get(fwd)):
                incoming[inv][target].append(nid)
            for target in iri_values(node.get(inv)):
                incoming[fwd][target].append(nid)

    def resolve_act(nid: str) -> str | None:
        if nid in direct_act:
            return direct_act[nid]
        seen: set[str] = set()
        current = nid
        while current and current not in seen:
            seen.add(current)
            if current in direct_act:
                return direct_act[current]
            current = parent.get(current)
        return None

    planned: dict[str, dict] = defaultdict(dict)

    for fwd, inv in INVERSE_PAIRS:
        if (fwd, inv) in COMBINED_SKIP_INVERSE_PAIRS:
            continue
        for pred in (fwd, inv):
            for target, sources in incoming[pred].items():
                if target not in by_id:
                    continue
                merged = merge_iri_list(by_id[target].get(pred), sources)
                if iri_values(by_id[target].get(pred)) != iri_values(merged):
                    planned[target][pred] = merged

    for nid, node in by_id.items():
        if is_act_like(types[nid]) or "owl:Ontology" in types[nid]:
            continue
        act = resolve_act(nid)
        if not act or act not in by_id:
            continue
        existing = iri_values(node.get("estleg:partOfAct"))
        if existing == [act]:
            continue
        if existing:
            # Functional: keep the authored value.
            continue
        planned[nid]["estleg:partOfAct"] = {"@id": act}

    return {nid: props for nid, props in planned.items() if props}


def apply_updates(node: dict, updates: dict) -> bool:
    """Merge ``updates`` onto ``node``. Returns True if anything changed."""
    changed = False
    for key, value in updates.items():
        if node.get(key) != value:
            node[key] = value
            changed = True
    return changed


def materialize_combined_edges(
    nodes: list[dict],
    node_by_id: dict[str, dict] | None = None,
) -> dict[str, int]:
    """Mutate ``nodes`` in place. ``node_by_id`` is unused (API stability)."""
    del node_by_id
    planned = plan_inverse_updates(nodes)
    by_pred: dict[str, int] = defaultdict(int)
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("@id")
        updates = planned.get(nid) if isinstance(nid, str) else None
        if not updates:
            continue
        if apply_updates(node, updates):
            for pred in updates:
                by_pred[pred] += 1
    return {
        "nodes_updated": len(planned),
        **{f"with_{pred.split(':', 1)[-1]}": count for pred, count in sorted(by_pred.items())},
    }


def iter_combined_objects(path: Path) -> Iterator[tuple[int, int, dict]]:
    """Yield ``(start, end, obj)`` for each top-level ``@graph`` object."""
    text = path.read_text(encoding="utf-8")
    marker = text.find('"@graph"')
    if marker < 0:
        raise ValueError(f"{path}: no @graph")
    bracket = text.find("[", marker)
    if bracket < 0:
        raise ValueError(f"{path}: @graph is not an array")
    decoder = json.JSONDecoder()
    index = bracket + 1
    length = len(text)
    while True:
        while index < length and text[index] in " \t\r\n,":
            index += 1
        if index >= length:
            raise ValueError(f"{path}: unterminated @graph")
        if text[index] == "]":
            return
        obj, end = decoder.raw_decode(text, index)
        if isinstance(obj, dict):
            yield index, end, obj
        index = end


def _dump_indented(node: dict) -> str:
    dumped = json.dumps(node, ensure_ascii=False, indent=2)
    return "\n".join(
        f"    {line}" if i else line for i, line in enumerate(dumped.splitlines())
    )


def rewrite_combined(path: Path, updates: dict[str, dict]) -> int:
    """Stream-patch ``path`` with per-@id property updates. Returns nodes written."""
    if not updates or not path.exists():
        return 0
    text = path.read_text(encoding="utf-8")
    marker = text.find('"@graph"')
    if marker < 0:
        raise ValueError(f"{path}: no @graph")
    bracket = text.find("[", marker)
    if bracket < 0:
        raise ValueError(f"{path}: @graph is not an array")
    decoder = json.JSONDecoder()
    index = bracket + 1
    length = len(text)
    tmp = path.with_suffix(path.suffix + ".tmp")
    updated = 0
    first = True
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text[: bracket + 1])
        handle.write("\n")
        while True:
            while index < length and text[index] in " \t\r\n,":
                index += 1
            if index >= length:
                raise ValueError(f"{path}: unterminated @graph")
            if text[index] == "]":
                break
            obj, end = decoder.raw_decode(text, index)
            nid = obj.get("@id") if isinstance(obj, dict) else None
            payload = updates.get(nid) if isinstance(nid, str) else None
            if not first:
                handle.write(",\n")
            else:
                first = False
            handle.write("    ")
            if payload and isinstance(obj, dict) and apply_updates(obj, payload):
                handle.write(_dump_indented(obj))
                updated += 1
            else:
                handle.write(text[index:end])
            index = end
        handle.write(text[index:])
    tmp.replace(path)
    return updated


def patch_combined_inverses(combined_path: Path) -> tuple[int, dict[str, int]]:
    """Two-pass remint of inverse/closure edges on the shipped combined file."""
    nodes = [obj for _, _, obj in iter_combined_objects(combined_path)]
    planned = plan_inverse_updates(nodes)
    written = rewrite_combined(combined_path, planned)
    by_pred: dict[str, int] = defaultdict(int)
    for props in planned.values():
        for pred in props:
            by_pred[pred] += 1
    return written, dict(by_pred)


def remint_combined_520_521(combined_path: Path) -> tuple[int, dict[str, int]]:
    """One collect + one rewrite: inverses then analytical flags (#520/#521)."""
    from estleg.generate_analytical_overlay import (
        load_in_force_directives,
        plan_analytical_updates,
    )

    nodes = [obj for _, _, obj in iter_combined_objects(combined_path)]
    inverse = plan_inverse_updates(nodes)
    for node in nodes:
        nid = node.get("@id")
        if isinstance(nid, str) and nid in inverse:
            apply_updates(node, inverse[nid])
    analytical = plan_analytical_updates(
        nodes, in_force_directives=load_in_force_directives()
    )
    merged: dict[str, dict] = {}
    for nid, props in inverse.items():
        merged.setdefault(nid, {}).update(props)
    for nid, props in analytical.items():
        merged.setdefault(nid, {}).update(props)
    written = rewrite_combined(combined_path, merged)
    by_pred: dict[str, int] = defaultdict(int)
    for props in merged.values():
        for pred in props:
            by_pred[pred] += 1
    return written, dict(by_pred)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--patch-combined",
        action="store_true",
        help="Offline #520 pass over krr_outputs/combined_ontology.jsonld.",
    )
    parser.add_argument(
        "--with-analytical",
        action="store_true",
        help="Also stamp #521 counts/flags in the same rewrite.",
    )
    parser.add_argument(
        "--combined",
        type=Path,
        default=KRR_DIR / COMBINED_NAME,
        help="Path to combined_ontology.jsonld.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.patch_combined:
        print("specify --patch-combined", file=sys.stderr)
        return 2
    if not args.combined.exists():
        print(f"ERROR: missing {args.combined}", file=sys.stderr)
        return 1
    if args.with_analytical:
        written, by_pred = remint_combined_520_521(args.combined)
        print(f"patched {written} combined nodes (#520+#521)")
    else:
        written, by_pred = patch_combined_inverses(args.combined)
        print(f"patched {written} combined nodes (#520)")
    for pred, count in sorted(by_pred.items()):
        print(f"  {pred}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
