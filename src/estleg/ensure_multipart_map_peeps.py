#!/usr/bin/env python3
"""Mint whole-act ``_Map_`` peeps for multipart statutes and retarget annotations (#379).

KarS / VÕS / AÕS / TsÜS only have per-osa peeps, so annotations and drafts
resolved to an arbitrary osa (lexicographic osa10 < osa1). A dedicated Map
peep plus INDEX-first listing gives them a stable whole-act IRI.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from estleg.estleg_common import CONTEXT, KRR_DIR, save_json

# prefix on the per-osa act IRI → (map_iri, map_filename, index_name, label)
MULTIPART_MAPS: dict[str, tuple[str, str, str, str]] = {
    "KARIST_2": (
        "estleg:KARIST_2_Map_2026",
        "karistusseadustik_map_peep.json",
        "karistusseadustik",
        "Karistusseadustik",
    ),
    "volaoigusseadus": (
        "estleg:volaoigusseadus_Map_2026",
        "volaoigusseadus_map_peep.json",
        "volaoigusseadus",
        "Võlaõigusseadus",
    ),
    "VOS": (
        "estleg:volaoigusseadus_Map_2026",
        "volaoigusseadus_map_peep.json",
        "volaoigusseadus",
        "Võlaõigusseadus",
    ),
    "AOS": (
        "estleg:AOS_Map_2026",
        "asjaoigusseadus_map_peep.json",
        "asjaoigusseadus",
        "Asjaõigusseadus",
    ),
    "TsÜS": (
        "estleg:TsÜS_Map_2026",
        "tsiviilseadustiku_uldosa_seadus_map_peep.json",
        "tsiviilseadustiku_uldosa_seadus",
        "Tsiviilseadustiku üldosa seadus",
    ),
    "tsiviilkohtumenetluse_seadustik": (
        "estleg:TsMS_Map_2026",
        "tsiviilkohtumenetluse_peep.json",
        "tsiviilkohtumenetluse_seadustik",
        "Tsiviilkohtumenetluse seadustik",
    ),
}

OSA_ACT_RE = re.compile(r"^estleg:(\w+)_Osa\d+$")


def map_iri_for_osa(iri: str) -> str | None:
    """Return the whole-act Map IRI for a per-osa act node, if known."""
    match = OSA_ACT_RE.match(iri)
    if not match:
        return None
    entry = MULTIPART_MAPS.get(match.group(1))
    return entry[0] if entry else None


def _osa_parts(krr_dir: Path, prefix: str) -> list[str]:
    found: list[str] = []
    for path in krr_dir.glob("*_peep.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for node in doc.get("@graph", []):
            if not isinstance(node, dict):
                continue
            nid = node.get("@id")
            if isinstance(nid, str) and nid.startswith(f"estleg:{prefix}_Osa"):
                if OSA_ACT_RE.match(nid):
                    found.append(nid)
    return sorted(set(found), key=lambda iri: (int(re.search(r"_Osa(\d+)", iri).group(1)), iri))


def write_map_peep(krr_dir: Path, prefix: str) -> Path | None:
    spec = MULTIPART_MAPS.get(prefix)
    if not spec:
        return None
    map_iri, filename, _index_name, label = spec
    dest = krr_dir / filename
    if dest.name == "tsiviilkohtumenetluse_peep.json" and dest.is_file():
        return dest
    parts = _osa_parts(krr_dir, prefix)
    node = {
        "@id": map_iri,
        "@type": ["estleg:Act", "estleg:Law"],
        "rdfs:label": f"{label} teemakaardistus",
        "dc:source": label,
        "dcterms:title": label,
        "estleg:contentStatus": "structuredBody",
    }
    if parts:
        node["estleg:hasPart"] = [{"@id": iri} for iri in parts]
    save_json(dest, {"@context": CONTEXT, "@graph": [node]})
    return dest


def prepend_index_files(index_path: Path, index_name: str, filename: str) -> bool:
    data = json.loads(index_path.read_text(encoding="utf-8"))
    changed = False
    for law in data.get("laws", []):
        if law.get("name") != index_name:
            continue
        files = list(law.get("files") or [])
        if filename not in files:
            law["files"] = [filename, *files]
            changed = True
        elif files[0] != filename:
            files = [filename] + [f for f in files if f != filename]
            law["files"] = files
            changed = True
    if changed:
        from estleg.estleg_common import save_json

        save_json(index_path, data)
    return changed


def retarget_annotations(path: Path) -> int:
    doc = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for node in doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        raw = node.get("estleg:annotates")
        items = raw if isinstance(raw, list) else [raw] if raw else []
        new_items = []
        node_changed = False
        for item in items:
            if not isinstance(item, dict):
                new_items.append(item)
                continue
            iri = item.get("@id")
            mapped = map_iri_for_osa(iri) if isinstance(iri, str) else None
            if mapped and mapped != iri:
                new_items.append({"@id": mapped})
                node_changed = True
                changed += 1
            else:
                new_items.append(item)
        if node_changed:
            node["estleg:annotates"] = new_items[0] if len(new_items) == 1 else new_items
    if changed:
        from estleg.estleg_common import save_json

        save_json(path, doc)
    return changed


def retarget_eelnoud_amends(path: Path) -> int:
    doc = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for node in doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        raw = node.get("estleg:amendsLaw")
        if not raw:
            continue
        items = raw if isinstance(raw, list) else [raw]
        new_items = []
        node_changed = False
        for item in items:
            if not isinstance(item, dict):
                new_items.append(item)
                continue
            iri = item.get("@id")
            mapped = map_iri_for_osa(iri) if isinstance(iri, str) else None
            if mapped and mapped != iri:
                new_items.append({"@id": mapped})
                node_changed = True
                changed += 1
            else:
                new_items.append(item)
        if node_changed:
            node["estleg:amendsLaw"] = new_items[0] if len(new_items) == 1 else new_items
    if changed:
        from estleg.estleg_common import save_json

        save_json(path, doc)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--krr-dir", type=Path, default=KRR_DIR)
    args = parser.parse_args(argv)
    written = []
    for prefix in ("KARIST_2", "volaoigusseadus", "AOS", "TsÜS"):
        path = write_map_peep(args.krr_dir, prefix)
        if path:
            written.append(path.name)
            spec = MULTIPART_MAPS[prefix]
            prepend_index_files(args.krr_dir / "INDEX.json", spec[2], spec[1])
    prepend_index_files(
        args.krr_dir / "INDEX.json",
        "tsiviilkohtumenetluse_seadustik",
        "tsiviilkohtumenetluse_peep.json",
    )
    print("map peeps:", written)
    ann = args.krr_dir / "annotations" / "oiguskantsler_seisukohad.jsonld"
    if ann.is_file():
        print("annotations retargeted:", retarget_annotations(ann))
    eelnoud = args.krr_dir / "eelnoud" / "eelnoud_combined.jsonld"
    if eelnoud.is_file():
        print("eelnoud amendsLaw retargeted:", retarget_eelnoud_amends(eelnoud))
    return 0


if __name__ == "__main__":
    sys.exit(main())
