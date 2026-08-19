#!/usr/bin/env python3
"""Align the two hand-built OWL modules with the canonical T-Box (#438).

    python3 scripts/align_owl_modules.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from estleg.consolidate_tbox import apply_class_alignment, _skip_ws_comma
from estleg.estleg_common import KRR_DIR

MODULE_FILES = (
    "karistusseadustik_eriosa_owl.jsonld",
    "tsus_osa7_138_169_owl.jsonld",
)
DROP_CLASS_FRAGMENTS = frozenset(
    {
        "LegalPart",
        "Chapter",
        "Division",
        "Subdivision",
        "Section",
        "Provision",
        "LegalConcept",
    }
)


def _types(node: dict) -> list[str]:
    value = node.get("@type")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _fragment(nid: object) -> str:
    if not isinstance(nid, str):
        return ""
    if nid.startswith("estleg:"):
        return nid[len("estleg:") :]
    if nid.startswith("https://w3id.org/estleg/"):
        return nid[len("https://w3id.org/estleg/") :]
    return nid


HEADER_IDS = frozenset({"estleg:KarS_Eriosa_v1", "estleg:TsUS_Osa7_v1"})


def _is_module_header(node: dict) -> bool:
    nid = node.get("@id")
    if nid in HEADER_IDS:
        return True
    if not isinstance(nid, str):
        return False
    return nid.endswith("KarS_Eriosa_v1") or nid.endswith("TsUS_Osa7_v1")


def align_header_source(node: dict) -> bool:
    """Move a resolvable URL off ``dc:source`` onto ``dcterms:source`` (#308/#438)."""
    source = node.get("dc:source")
    if not isinstance(source, str) or not source.startswith("http"):
        return False
    node["dcterms:source"] = {"@id": source}
    title = node.get("dc:title")
    node["dc:source"] = title if isinstance(title, str) else "Karistusseadustik"
    return True


def align_node(node: dict) -> bool:
    """Rewrite one instance or header node. Returns True if changed."""
    types = _types(node)
    changed = False
    if types:
        if "estleg:Section" in types and "estleg:LegalProvision" not in types:
            types.append("estleg:LegalProvision")
            changed = True
        if changed:
            node["@type"] = types
    if _is_module_header(node) and align_header_source(node):
        changed = True
    return changed


def align_document(doc: dict) -> dict[str, int]:
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return {"dropped": 0, "rewritten": 0}
    dropped = rewritten = 0
    kept: list[object] = []
    for node in graph:
        if not isinstance(node, dict):
            kept.append(node)
            continue
        types = _types(node)
        frag = _fragment(node.get("@id"))
        if "owl:Class" in types and frag in DROP_CLASS_FRAGMENTS:
            dropped += 1
            continue
        if align_node(node):
            rewritten += 1
        kept.append(node)
    doc["@graph"] = kept
    return {"dropped": dropped, "rewritten": rewritten}


def align_modules(krr_dir: Path = KRR_DIR) -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = {}
    for name in MODULE_FILES:
        path = krr_dir / name
        doc = json.loads(path.read_text(encoding="utf-8"))
        stats[name] = align_document(doc)
        path.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return stats


def align_combined(path: Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8")
    marker = text.find('"@graph"')
    if marker < 0:
        raise ValueError(f"{path}: no @graph")
    bracket = text.find("[", marker)
    decoder = json.JSONDecoder()
    index = bracket + 1
    length = len(text)
    tmp = path.with_suffix(path.suffix + ".tmp")
    rewritten = dropped = kept = 0
    first = True
    first_gap = bracket + 1
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text[: bracket + 1])
        while True:
            index = _skip_ws_comma(text, index)
            if index >= length:
                raise ValueError(f"{path}: unterminated @graph")
            if text[index] == "]":
                break
            obj, end = decoder.raw_decode(text, index)
            keep_slice = True
            if isinstance(obj, dict):
                types = _types(obj)
                frag = _fragment(obj.get("@id"))
                nid = obj.get("@id")
                # Modules used expanded IRIs; the CV keeps compact estleg:Chapter.
                if (
                    "owl:Class" in types
                    and frag in DROP_CLASS_FRAGMENTS
                    and isinstance(nid, str)
                    and nid.startswith("https://w3id.org/estleg/")
                ):
                    dropped += 1
                    keep_slice = False
                elif align_node(obj):
                    rewritten += 1
                    dumped = json.dumps(obj, ensure_ascii=False, indent=2)
                    indented = "\n".join(
                        f"    {line}" if line else line for line in dumped.splitlines()
                    )
                    if first:
                        handle.write("\n")
                        first = False
                    else:
                        handle.write(",\n")
                    handle.write(indented)
                    keep_slice = False
                    kept += 1
            if keep_slice:
                lead = index
                while lead > bracket and text[lead - 1] in " \t":
                    lead -= 1
                if first:
                    handle.write(text[first_gap:lead] or "\n")
                    first = False
                else:
                    handle.write(",\n")
                handle.write(text[lead:end])
                kept += 1
            index = end
        handle.write("\n")
        handle.write(text[index:])
    tmp.replace(path)
    return {"rewritten": rewritten, "dropped": dropped, "kept": kept}


def stamp_cv_axioms(vocab_path: Path) -> None:
    doc = json.loads(vocab_path.read_text(encoding="utf-8"))
    for node in doc.get("@graph", []):
        if isinstance(node, dict):
            apply_class_alignment(node)
    vocab_path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-combined", action="store_true")
    args = parser.parse_args(argv)
    stamp_cv_axioms(KRR_DIR / "controlled_vocabulary.jsonld")
    print("cv: Section⊑LegalProvision")
    print("modules:", align_modules())
    combined = KRR_DIR / "combined_ontology.jsonld"
    if not args.skip_combined and combined.is_file():
        head = combined.read_text(encoding="utf-8", errors="replace")[:80]
        if not head.startswith("version https://git-lfs.github.com/spec/v1"):
            print("combined:", align_combined(combined))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
