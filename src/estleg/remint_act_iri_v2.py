#!/usr/bin/env python3
"""#445 — drop ``_Map_<year>`` and ASCII-transliterate estleg IRIs.

    python3 -m estleg.remint_act_iri_v2 --apply
    python3 -m estleg.remint_act_iri_v2 --dry-run

Pass 1 rewrites every ``_Map_2026`` token to yearless ``_Map``.
Pass 2 transliterates Estonian letters inside ``estleg:`` / w3id locals.
Then writes the TsMS whole-act map peep and an old→new ``owl:sameAs`` bridge.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from estleg.estleg_common import (
    CONTEXT,
    KRR_DIR,
    MAP_IRI_YEAR,
    NS,
    REPO_ROOT,
    ascii_iri_key,
    mint_act_iri,
    save_json,
)
from estleg.ensure_multipart_map_peeps import (
    MULTIPART_MAPS,
    prepend_index_files,
    write_map_peep,
)
from estleg.migrate_uris import _atomic_write_text

MAP_YEAR_TOKEN = f"_Map_{MAP_IRI_YEAR}"
IRI_LOCAL_RE = re.compile(
    r"((?:estleg:|" + re.escape(NS) + r"))"
    r"([A-Za-z0-9_ÄÕÖÜŠŽäõöüšž]+)"
)
ESTONIAN_CHARS = frozenset("äöüõšžÄÖÜÕŠŽ")
SKIP_DIR_NAMES = frozenset({"retrieval", ".git"})
REWRITE_SUFFIXES = {".json", ".jsonld", ".ttl", ".md"}
BRIDGE_NAME = "act_iri_v2_sameas.jsonld"
OSA_IRI_RE = re.compile(r"^estleg:(.+)_Osa(\d+)$")


def remint_local(local: str) -> str:
    """Yearless ``_Map`` + ASCII key."""
    return ascii_iri_key(local.replace(MAP_YEAR_TOKEN, "_Map"))


def remint_iri(iri: str) -> str:
    if iri.startswith("estleg:"):
        return "estleg:" + remint_local(iri[7:])
    if iri.startswith(NS):
        rest = iri[len(NS) :].lstrip("/#")
        return NS + remint_local(rest)
    return remint_local(iri) if ESTONIAN_CHARS.intersection(iri) or MAP_YEAR_TOKEN in iri else iri


def rewrite_text(text: str) -> str:
    """Rewrite IRI tokens; also sweep leftover ``_Map_2026`` in prose."""

    def _repl(match: re.Match[str]) -> str:
        return match.group(1) + remint_local(match.group(2))

    text = IRI_LOCAL_RE.sub(_repl, text)
    if MAP_YEAR_TOKEN in text:
        text = text.replace(MAP_YEAR_TOKEN, "_Map")
    return text


def iter_remint_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in REWRITE_SUFFIXES:
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as handle:
            first = handle.readline()
    except (OSError, UnicodeDecodeError):
        return False
    return first.startswith("version https://git-lfs.github.com/spec/v1")


def rewrite_file(path: Path) -> int:
    if is_lfs_pointer(path):
        return 0
    try:
        original = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    updated = rewrite_text(original)
    if updated == original:
        return 0
    _atomic_write_text(path, updated)
    return original.count(MAP_YEAR_TOKEN) + sum(
        1 for ch in original if ch in ESTONIAN_CHARS
    )


def map_iri_for_osa_local(local_prefix: str) -> str | None:
    """Whole-act Map IRI for a per-osa prefix, if this is a known multipart."""
    if local_prefix in MULTIPART_MAPS:
        return MULTIPART_MAPS[local_prefix][0]
    ascii_prefix = ascii_iri_key(local_prefix)
    if ascii_prefix in MULTIPART_MAPS:
        return MULTIPART_MAPS[ascii_prefix][0]
    return mint_act_iri(local_prefix) if local_prefix in {
        "AOS",
        "KARIST_2",
        "TsUS",
        "TsMS",
        "volaoigusseadus",
    } else None


def demote_osa_file(path: Path) -> int:
    """Drop Act/Law from osa roots; point partOfAct at the whole-act Map."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return 0
    changed = 0
    osa_to_map: dict[str, str] = {}
    for node in graph:
        if not isinstance(node, dict):
            continue
        nid = node.get("@id")
        if not isinstance(nid, str):
            continue
        match = OSA_IRI_RE.match(nid)
        if not match:
            continue
        mapped = map_iri_for_osa_local(match.group(1))
        if not mapped:
            continue
        osa_to_map[nid] = mapped
        types = node.get("@type")
        type_list = [types] if isinstance(types, str) else list(types or [])
        if "estleg:Part" not in type_list:
            type_list.append("estleg:Part")
        new_types = [t for t in type_list if t not in {"estleg:Act", "estleg:Law"}]
        if new_types != type_list:
            node["@type"] = new_types
            changed += 1
        if node.get("estleg:isPartOf") != {"@id": mapped}:
            node["estleg:isPartOf"] = {"@id": mapped}
            changed += 1
    if not osa_to_map:
        return 0
    for node in graph:
        if not isinstance(node, dict):
            continue
        raw = node.get("estleg:partOfAct")
        if not raw:
            continue
        items = raw if isinstance(raw, list) else [raw]
        new_items = []
        node_changed = False
        for item in items:
            if isinstance(item, dict) and item.get("@id") in osa_to_map:
                new_items.append({"@id": osa_to_map[item["@id"]]})
                node_changed = True
            else:
                new_items.append(item)
        if node_changed:
            node["estleg:partOfAct"] = new_items if isinstance(raw, list) else new_items[0]
            changed += 1
    if changed:
        save_json(path, doc)
    return changed


def collect_act_root_renames(krr_dir: Path) -> list[tuple[str, str]]:
    """Unique old→new act-root pairs for the sameAs bridge."""
    pairs: set[tuple[str, str]] = set()
    token_re = re.compile(r"estleg:[A-Za-z0-9_ÄÕÖÜŠŽäõöüšž]*_Map_" + str(MAP_IRI_YEAR))
    for path in krr_dir.glob("*_peep.json"):
        if is_lfs_pointer(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for old in token_re.findall(text):
            new = remint_iri(old)
            if new != old:
                pairs.add((old, new))
    return sorted(pairs)


def write_sameas_bridge(pairs: list[tuple[str, str]], dest: Path) -> None:
    graph = []
    for old, new in pairs:
        graph.append(
            {
                "@id": new,
                "@type": ["owl:NamedIndividual"],
                "owl:sameAs": {"@id": old},
                "rdfs:comment": {
                    "@value": "estleg act-IRI v2 (#445): yearless ASCII work IRI.",
                    "@language": "en",
                },
            }
        )
    save_json(dest, {"@context": CONTEXT, "@graph": graph})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--krr-dir", type=Path, default=KRR_DIR)
    parser.add_argument(
        "--also",
        type=Path,
        action="append",
        default=[],
        help="Extra roots to rewrite (e.g. docs/, data/).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    roots = [args.krr_dir, *args.also]
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(iter_remint_files(root))
        elif root.is_file():
            files.append(root)
    print(f"candidates={len(files)}")
    if not args.apply:
        sample = next((p for p in files if p.name.endswith("_peep.json")), None)
        if sample:
            text = sample.read_text(encoding="utf-8")
            preview = rewrite_text(text)
            print(f"dry-run sample {sample.name} changed={preview != text}")
        return 0
    bridge_pairs = collect_act_root_renames(args.krr_dir)
    rewritten = 0
    for path in files:
        if rewrite_file(path):
            rewritten += 1
            if rewritten % 500 == 0:
                print(f"rewritten {rewritten}/{len(files)} last={path.name}")
    osa_changed = 0
    for path in args.krr_dir.glob("*_osa*_peep.json"):
        osa_changed += demote_osa_file(path)
    tsms = write_map_peep(args.krr_dir, "tsiviilkohtumenetluse_seadustik")
    if tsms:
        prepend_index_files(
            args.krr_dir / "INDEX.json",
            "tsiviilkohtumenetluse_seadustik",
            tsms.name,
        )
    bridge_dest = REPO_ROOT / "data" / BRIDGE_NAME
    if bridge_pairs:
        write_sameas_bridge(bridge_pairs, bridge_dest)
    print(
        f"rewritten_files={rewritten} osa_demote_edits={osa_changed} "
        f"bridge_pairs={len(bridge_pairs)} tsms_map={tsms.name if tsms else None}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
