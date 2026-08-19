#!/usr/bin/env python3
"""Drop per-document provision classes (#434).

Rewrites peeps and ``combined_ontology.jsonld`` so provisions are typed
``estleg:LegalProvision`` (plus existing ``estleg:KovProvision``) and the
mechanical ``LegalProvision_<slug>`` / ``Regulation_<id>`` ``owl:Class``
nodes are removed.

    python3 scripts/remint_per_document_classes.py
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from estleg.consolidate_tbox import _skip_ws_comma
from estleg.estleg_common import KRR_DIR

CANONICAL_PROVISION_TYPE = "estleg:LegalProvision"
PER_DOC_CLASS_PREFIXES = ("estleg:LegalProvision_", "estleg:Regulation_")


def node_types(node: dict) -> list[str]:
    value = node.get("@type")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def is_per_doc_class_id(nid: object) -> bool:
    return isinstance(nid, str) and nid.startswith(PER_DOC_CLASS_PREFIXES)


def is_per_doc_class_node(node: dict) -> bool:
    return "owl:Class" in node_types(node) and is_per_doc_class_id(node.get("@id"))


def rewrite_type_list(types: Iterable[object]) -> list[Any]:
    rewritten: list[Any] = []
    seen: set[str] = set()
    for item in types:
        if isinstance(item, str) and item.startswith(PER_DOC_CLASS_PREFIXES):
            if CANONICAL_PROVISION_TYPE not in seen:
                rewritten.append(CANONICAL_PROVISION_TYPE)
                seen.add(CANONICAL_PROVISION_TYPE)
            continue
        if isinstance(item, str):
            if item in seen:
                continue
            seen.add(item)
        rewritten.append(item)
    return rewritten


def rewrite_node(node: dict) -> bool:
    """Rewrite one instance node. Returns True if the node changed."""
    raw = node.get("@type")
    if raw is None:
        return False
    original = raw if isinstance(raw, list) else [raw]
    rewritten = rewrite_type_list(original)
    if rewritten == original:
        if (
            "estleg:paragrahv" in node
            and CANONICAL_PROVISION_TYPE not in node_types(node)
            and "owl:Class" not in node_types(node)
        ):
            types = node_types(node)
            types.append(CANONICAL_PROVISION_TYPE)
            node["@type"] = types
            return True
        return False
    node["@type"] = rewritten[0] if not isinstance(raw, list) and len(rewritten) == 1 else rewritten
    if (
        "estleg:paragrahv" in node
        and CANONICAL_PROVISION_TYPE not in node_types(node)
        and "owl:Class" not in node_types(node)
    ):
        types = node_types(node)
        types.append(CANONICAL_PROVISION_TYPE)
        node["@type"] = types
    return True


def remint_document(doc: dict) -> tuple[int, int]:
    """Drop per-doc classes and rewrite instance types. Returns (dropped, rewritten)."""
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return 0, 0
    dropped = 0
    rewritten = 0
    kept: list[Any] = []
    for node in graph:
        if isinstance(node, dict) and is_per_doc_class_node(node):
            dropped += 1
            continue
        if isinstance(node, dict) and rewrite_node(node):
            rewritten += 1
        kept.append(node)
    doc["@graph"] = kept
    return dropped, rewritten


def remint_peeps(krr_dir: Path = KRR_DIR) -> dict[str, int]:
    files = 0
    dropped = 0
    rewritten = 0
    for path in sorted(krr_dir.rglob("*")):
        if not path.is_file() or path.suffix not in {".json", ".jsonld"}:
            continue
        if path.name in {"combined_ontology.jsonld", "controlled_vocabulary.jsonld"}:
            continue
        if path.name.endswith("_combined.jsonld"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not any(prefix in text for prefix in PER_DOC_CLASS_PREFIXES):
            continue
        try:
            doc = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(doc, dict):
            continue
        drop, rewrite = remint_document(doc)
        if drop or rewrite:
            path.write_text(
                json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            files += 1
            dropped += drop
            rewritten += rewrite
    return {"files": files, "dropped": dropped, "rewritten": rewritten}


def remint_combined(path: Path) -> dict[str, int]:
    """Stream-rewrite combined_ontology.jsonld without a full in-memory parse."""
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
    dropped = rewritten = kept = 0
    first = True
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text[: bracket + 1])
        first_gap = bracket + 1
        while True:
            index = _skip_ws_comma(text, index)
            if index >= length:
                raise ValueError(f"{path}: unterminated @graph")
            if text[index] == "]":
                break
            obj, end = decoder.raw_decode(text, index)
            keep_slice = True
            if isinstance(obj, dict):
                if is_per_doc_class_node(obj):
                    dropped += 1
                    keep_slice = False
                elif rewrite_node(obj):
                    rewritten += 1
                    dumped = json.dumps(obj, ensure_ascii=False, indent=2)
                    indented = "\n".join(
                        f"    {line}" if line else line for line in dumped.splitlines()
                    )
                    if not first:
                        handle.write(",\n")
                    else:
                        handle.write("\n")
                        first = False
                    handle.write(indented)
                    keep_slice = False
                    kept += 1
            if keep_slice:
                if first:
                    handle.write(text[first_gap:index] or "\n    ")
                    first = False
                else:
                    handle.write(",\n")
                handle.write(text[index:end])
                kept += 1
            index = end
        handle.write("\n")
        handle.write(text[index:])
    tmp.replace(path)
    return {"dropped": dropped, "rewritten": rewritten, "kept": kept}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-combined", action="store_true")
    parser.add_argument("--skip-peeps", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    stats: dict[str, object] = {}
    if not args.skip_peeps:
        stats["peeps"] = remint_peeps()
        print(f"peeps: {stats['peeps']}")
    combined = KRR_DIR / "combined_ontology.jsonld"
    if not args.skip_combined and combined.is_file():
        head = combined.read_text(encoding="utf-8", errors="replace")[:80]
        if not head.startswith("version https://git-lfs.github.com/spec/v1"):
            stats["combined"] = remint_combined(combined)
            print(f"combined: {stats['combined']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
