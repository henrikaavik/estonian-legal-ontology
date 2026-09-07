#!/usr/bin/env python3
"""Ingest Riigikogu otsus and presidential seadlus (#529).

The law pipeline queries ``dokument=seadus`` only. This module lists the
other RT act-kinds and writes:

* ``krr_outputs/resolutions/RESOLUTIONS_INDEX.json`` — every current
  RT hit (title + gid + url), so "find the otsus on X" is answerable
  even before every XML body is fetched
* ``krr_outputs/resolutions/<slug>_peep.json`` — act nodes typed
  ``ParliamentaryResolution`` or ``PresidentialDecree`` (not ``Law``)

``seadlus`` consolidations are not published; the listing uses
``tekst=algtekst``. ``otsus`` uses ``terviktekst``.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from estleg.estleg_common import (
    BUILD_EVALUATION_DATE,
    KRR_DIR,
    act_root_node,
    save_json,
    slugify,
)
from estleg.generate_all_laws import (
    fetch_xml,
    generate_law_jsonld,
    generate_law_stub_jsonld,
)
from estleg.riigiteataja_common import ln
from estleg.riigiteataja_common import fetch_acts

OUT_DIR = KRR_DIR / "resolutions"
INDEX_NAME = "RESOLUTIONS_INDEX.json"

KIND_OTSUS = {
    "dokument": "otsus",
    "text_filter": "terviktekst",
    "type_id": "estleg:ParliamentaryResolution",
    "label_et": "Riigikogu otsus",
    "label_en": "Parliamentary resolution",
}
KIND_SEADLUS = {
    "dokument": "seadlus",
    "text_filter": "algtekst",
    "type_id": "estleg:PresidentialDecree",
    "label_et": "seadlus",
    "label_en": "Presidential decree",
}
KINDS = {"otsus": KIND_OTSUS, "seadlus": KIND_SEADLUS}


def retarget_act_kind(doc: dict, extra_type: str) -> None:
    """Replace ``estleg:Law`` with the instrument class; keep ``estleg:Act``."""
    root = act_root_node(doc)
    if root is None:
        return
    types = root.get("@type") or []
    if isinstance(types, str):
        types = [types]
    kept = [item for item in types if item != "estleg:Law"]
    if extra_type not in kept:
        kept.append(extra_type)
    root["@type"] = kept


def list_kind(kind: dict, *, max_pages: int = 20) -> list[dict]:
    """Return unique latest-by-gid rows for one RT act-kind."""
    latest: dict[str, dict] = {}
    for act in fetch_acts(
        kind["dokument"],
        text_filter=kind["text_filter"],
        limiit=500,
        max_pages=max_pages,
        allow_partial=True,
        stop_on_short_page=True,
    ):
        title = (act.get("pealkiri") or "").strip()
        if not title:
            continue
        gid = str(act.get("globaalID", ""))
        if title not in latest or gid > latest[title]["gid"]:
            latest[title] = {
                "title": title,
                "gid": gid,
                "tid": str(act.get("terviktekstID", "")),
                "url": act.get("url") or "",
                "lyhend": act.get("lyhend") or "",
                "issuer": act.get("valjaandja") or "",
                "liik": act.get("liik") or kind["dokument"],
            }
    return [latest[key] for key in sorted(latest)]


def generate_kind_peep(row: dict, kind: dict) -> dict | None:
    """Build one peep from cached/fetched XML. Stub if no paragraphs."""
    slug = slugify(row["title"])[:80]
    root = fetch_xml(row["url"], slug, tid=row.get("tid"))
    if root is None:
        root = ET.Element("akt")
    paragraphs = [el for el in root.iter() if ln(el.tag) == "paragrahv"]
    if paragraphs:
        doc = generate_law_jsonld(
            row["title"], slug, root, row.get("lyhend") or "", row.get("url") or ""
        )
    else:
        doc = generate_law_stub_jsonld(
            row["title"], slug, root, row.get("lyhend") or "", row.get("url") or ""
        )
    retarget_act_kind(doc, kind["type_id"])
    return doc


def write_index(by_kind: dict[str, list[dict]], ingested: dict[str, int]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index = {
        "generated": BUILD_EVALUATION_DATE,
        "note": (
            "Non-statute RT act-kinds (#529). Titles come from the live RT "
            "search API. Peep bodies are generated for --ingest-limit rows "
            "per kind; the rest remain title-only index entries."
        ),
        "kinds": {},
    }
    for key, kind in KINDS.items():
        rows = by_kind.get(key) or []
        index["kinds"][key] = {
            "type": kind["type_id"],
            "label_et": kind["label_et"],
            "label_en": kind["label_en"],
            "dokument": kind["dokument"],
            "text_filter": kind["text_filter"],
            "rt_total": len(rows),
            "peeps_ingested": ingested.get(key, 0),
            "acts": [
                {
                    "title": row["title"],
                    "gid": row["gid"],
                    "url": row["url"],
                    "issuer": row["issuer"],
                }
                for row in rows
            ],
        }
    path = OUT_DIR / INDEX_NAME
    save_json(path, index)
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ingest-limit",
        type=int,
        default=15,
        help="XML peeps to generate per kind (0 = index only)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="RT search pages to scan per kind",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args([] if argv is None else argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    by_kind: dict[str, list[dict]] = {}
    ingested: dict[str, int] = {}
    print("Ingest RT otsus / seadlus (#529)")
    for key, kind in KINDS.items():
        print(f"  listing {key} ({kind['text_filter']})...")
        rows = list_kind(kind, max_pages=args.max_pages)
        by_kind[key] = rows
        print(f"    RT unique titles: {len(rows)}")
        count = 0
        for row in rows[: max(args.ingest_limit, 0)]:
            doc = generate_kind_peep(row, kind)
            if doc is None:
                continue
            slug = slugify(row["title"])[:80]
            save_json(OUT_DIR / f"{slug}_peep.json", doc)
            count += 1
        ingested[key] = count
        print(f"    peeps written: {count}")
    path = write_index(by_kind, ingested)
    print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
