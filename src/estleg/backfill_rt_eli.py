#!/usr/bin/env python3
"""Riigi Teataja identity on act roots, English expressions, legacy IRI bridge (#707).

    python3 scripts/backfill_rt_eli.py              # dry run: report only
    python3 scripts/backfill_rt_eli.py --apply      # rewrite law peeps + bridge

The law generator (``generate_all_laws``) stamps the same fields on every act
it writes; this module holds the shared field builders so both paths emit the
same bytes, and its CLI repairs roots the generator did not reach this run.

What it does, per root ``krr_outputs/*_peep.json`` law file:

* **Split source.** ``dcterms:source`` names the human Riigi Teataja page of
  the redaction (``https://www.riigiteataja.ee/akt/{globaalID}``);
  ``estleg:sourceXml`` names the machine manifestation the text was parsed
  from (the public-API ``/akt/{globaalID}/xml``). Before this, the source was
  the legacy ``/akt/{id}.xml`` path, which serves the RT web app's HTML shell
  since the 2026-06-01 relaunch.
* **First-class ids.** ``estleg:globalId`` and ``eli:id_local`` carry the
  globaalID already embedded in that source (the regulation generator's
  pattern). ``eli:id_local`` goes on act roots only, not on per-osa Part roots.
* **English expression.** ``estleg:officialEnglishText`` (the RT English
  consolidation IRI) is promoted to an ``eli:LegalExpression`` node with
  ``eli:language`` English, linked by ``eli:is_realized_by`` / ``eli:realizes``.
  A multipart act's whole-act map root takes the IRI from its Part roots.

**Not done, on purpose:** ``owl:sameAs`` to an Estonian ELI URI. Riigi Teataja
has not confirmed its Estonian-language ELI template, and the evidence on hand
(``english_eli.json`` maps act ``101032023045`` to English ``504042023007``)
says the globaalID is not the ELI key. That join is a maintainer-confirmed
follow-up; see docs/SCHEMA_REFERENCE.md.

``--bridge`` (implied by ``--apply``) publishes ``data/act_iri_v2_sameas.jsonld``
on the Seadusloome load surface as ``krr_outputs/bridges/act_iri_v2_sameas.jsonld``.
The source rows say ``<current> owl:sameAs <legacy _Map_2026 IRI>``; the
published copy turns each row around (``<legacy> owl:sameAs <current>``) so the
legacy IRI is the node a consumer looks up and every object reference resolves
on the load surface. Rows whose current IRI no longer resolves are dropped and
counted.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from estleg.estleg_common import (
    CONTEXT,
    KRR_DIR,
    REPO_ROOT,
    act_root_node,
    jsonld_id_values,
    save_json,
)
from estleg.riigiteataja_common import (
    build_act_page_url,
    build_xml_url,
    parse_act_metadata,
    rt_act_id,
)

SOURCE_XML_PROP = "estleg:sourceXml"
ENGLISH_TEXT_PROP = "estleg:officialEnglishText"
IS_REALIZED_BY = "eli:is_realized_by"
REALIZES = "eli:realizes"
LEGAL_EXPRESSION = "eli:LegalExpression"
# EU Publications Office language authority table — the value ELI expects.
ENGLISH_LANGUAGE_IRI = "http://publications.europa.eu/resource/authority/language/ENG"
ENGLISH_ELI_PREFIX = "https://www.riigiteataja.ee/en/eli/"
ENGLISH_ELI_PATH = REPO_ROOT / "data" / "riigiteataja" / "english_eli.json"

BRIDGE_SOURCE = REPO_ROOT / "data" / "act_iri_v2_sameas.jsonld"
BRIDGE_SUBDIR = "bridges"
BRIDGE_NAME = "act_iri_v2_sameas.jsonld"
BRIDGE_COMMENT = (
    "Legacy estleg act IRI (pre-#445 ``_Map_<year>`` / non-ASCII form); "
    "owl:sameAs names the current act IRI (#707)."
)

# Date fields the RT metadata block shares with the enrichment passes:
# ``extract_temporal_data`` (entryIntoForce / repealDate, from the same XML)
# — repealDate only ever comes from that pass (see rt_date_fields) —
# and ``generate_amendment_history`` (lastAmendmentDate = max versionValidFrom,
# gated by validate_all #429). The generator fills them only where no
# enrichment value exists, so a refresh and the DAG never overwrite each other.
ENRICHMENT_PREFERRED_FIELDS: frozenset[str] = frozenset(
    {"estleg:entryIntoForce", "estleg:repealDate", "estleg:lastAmendmentDate"}
)

_LEGACY_XML_SOURCE_RE = re.compile(r"riigiteataja\.ee/akt/[^/?#]+\.xml$")


# ---------------------------------------------------------------------------
# Field builders (shared with generate_all_laws)
# ---------------------------------------------------------------------------


def _xsd_date(value: str) -> dict:
    return {"@value": value, "@type": "xsd:date"}


def rt_identity_fields(
    *,
    global_id: str | None,
    terviktekst_id: str | None = None,
    metadata: dict | None = None,
    content_hash: str | None = None,
    act_root: bool = True,
) -> dict:
    """Generator-owned RT identity/provenance fields for an act or Part root.

    Returned in emission order. Absent inputs are omitted, never emitted empty.
    ``act_root=False`` (a per-osa Part root) leaves out ``eli:id_local``: the
    Part is a division of the act, not the ELI resource the id names.
    """
    meta = metadata or {}
    gid = str(global_id or meta.get("globalId") or "").strip()
    tid = str(terviktekst_id or meta.get("terviktekstId") or "").strip()
    fields: dict = {}
    if gid:
        # As an act path, so any id token the search API hands out maps
        # (rt_act_id only takes a bare token when it is all digits).
        act_path = f"/akt/{gid}"
        fields["dcterms:source"] = {"@id": build_act_page_url(act_path)}
        fields[SOURCE_XML_PROP] = {"@id": build_xml_url(act_path)}
        fields["estleg:globalId"] = gid
        if act_root:
            fields["eli:id_local"] = gid
    if tid:
        fields["estleg:terviktekstId"] = tid
    if meta.get("schemaName"):
        fields["estleg:skeemiNimi"] = str(meta["schemaName"])
    if content_hash:
        fields["estleg:contentHash"] = content_hash
    if meta.get("issuer"):
        fields["estleg:issuer"] = str(meta["issuer"])
    if meta.get("actNumber"):
        fields["estleg:actNumber"] = str(meta["actNumber"])
    return fields


def rt_date_fields(metadata: dict | None) -> dict:
    """RT ``<metaandmed>`` dates as ``xsd:date`` literals (#695).

    ``entryIntoForce`` follows ``extract_temporal_data``: the act's own
    ``<joustumine>``, else the redaction's ``<kehtivuseAlgus>``. A law root is
    the act (the ELI Work), so the redaction start is the wrong date for it;
    using the temporal pass's rule also keeps the two writers byte-identical.

    ``repealDate`` is deliberately not derived: ``<kehtivuseLopp>`` (what
    ``parse_act_metadata`` reports as ``repealDate``) ends the *redaction*,
    and on an in-force law it is just the eve of the next redaction
    (the 2026-10-01 Advokatuuriseadus redaction ends 2026-12-31 because the next
    one starts 2027-01-01). Stamping it would mark in-force laws repealed.
    """
    meta = metadata or {}
    fields: dict = {}
    entry = meta.get("originalEntryIntoForce") or meta.get("entryIntoForce")
    if entry:
        fields["estleg:entryIntoForce"] = _xsd_date(str(entry))
    if meta.get("lastAmendmentDate"):
        fields["estleg:lastAmendmentDate"] = _xsd_date(str(meta["lastAmendmentDate"]))
    return fields


def insert_after(node: dict, fields: dict, *, after: tuple[str, ...]) -> None:
    """Set ``fields`` on ``node``, placing new keys after the last ``after`` key.

    Existing keys are overwritten in place. New keys go right after the last
    key of ``fields`` the node already has, else after the last ``after`` key
    present (or at the end), so a regenerated root keeps a stable, readable
    key order.
    """
    new_keys = [k for k in fields if k not in node]
    for key, value in fields.items():
        if key in node:
            node[key] = value
    if not new_keys:
        return
    anchor = None
    for key in node:
        if key in after:
            anchor = key
    for key in node:
        if key in fields:
            anchor = key
    rebuilt: dict = {}
    placed = anchor is None
    for key, value in node.items():
        rebuilt[key] = value
        if key == anchor:
            for new in new_keys:
                rebuilt[new] = fields[new]
            placed = True
    if not placed or anchor is None:
        for new in new_keys:
            rebuilt[new] = fields[new]
    node.clear()
    node.update(rebuilt)


_ROOT_FIELD_ANCHORS = ("estleg:contentStatusReason", "estleg:contentStatus", "dcterms:title")


def _source_global_id(rt_url: str | None) -> str | None:
    """globaalID from a search-row ``url`` (``/akt/{id}.xml``), if it has one."""
    if not rt_url:
        return None
    try:
        return rt_act_id(rt_url)
    except ValueError:
        return None


def stamp_rt_metadata(
    node: dict,
    root,
    *,
    rt_url: str = "",
    kehtiv_value: dict | None = None,
    terviktekst_id: str | None = None,
    global_id: str | None = None,
    content_hash: str | None = None,
    act_root: bool = True,
) -> dict:
    """Stamp the RT metadata block on a law, stub or Part root; return the metadata.

    Mirrors the regulation generator's block, read from the act XML ``root``
    with ``parse_act_metadata``: ``dcterms:source`` (the human RT page) and
    ``estleg:sourceXml`` (the XML manifestation), ``globalId`` /
    ``eli:id_local``, ``kehtiv``, ``terviktekstId``, ``skeemiNimi``,
    ``contentHash``, ``issuer``, ``actNumber`` and the ``entryIntoForce`` /
    ``lastAmendmentDate`` dates (no ``repealDate``: see ``rt_date_fields``). The globaalID comes from the
    XML, else ``global_id`` (the search row), else the ``rt_url`` path.
    """
    meta = parse_act_metadata(root) if root is not None else {}
    # The XML's globaalID wins when it is the numeric RT act id. A legacy text
    # can carry a document UUID there instead (RT II treaty acts of 2010);
    # the act page and eli:id_local need the numeric id the search row has.
    xml_gid = meta.get("globalId")
    numeric_xml_gid = xml_gid if xml_gid and str(xml_gid).isdigit() else None
    gid = numeric_xml_gid or global_id or _source_global_id(rt_url) or xml_gid
    fields = rt_identity_fields(
        global_id=gid,
        terviktekst_id=terviktekst_id or meta.get("terviktekstId"),
        metadata=meta,
        content_hash=content_hash,
        act_root=act_root,
    )
    ordered: dict = {}
    kehtiv_after = "eli:id_local" if act_root else "estleg:globalId"
    for key, value in fields.items():
        ordered[key] = value
        if key == kehtiv_after and kehtiv_value:
            ordered["estleg:kehtiv"] = kehtiv_value
    if kehtiv_value and "estleg:kehtiv" not in ordered:
        ordered = {"estleg:kehtiv": kehtiv_value, **ordered}
    ordered.update(rt_date_fields(meta))
    insert_after(node, ordered, after=_ROOT_FIELD_ANCHORS)
    return meta


def stamp_map_root_doc(
    map_doc: dict,
    part_docs: list[dict],
    root,
    *,
    english_titles: dict[str, str] | None = None,
    **stamp_kwargs,
) -> bool:
    """Stamp a multipart act's whole-act map root from the act XML (#695).

    Applies only when the map root is the act the Part roots point at
    (``estleg:partOfAct``). Identity fields are overwritten; the dates in
    ``ENRICHMENT_PREFERRED_FIELDS`` keep an existing enrichment value. The map
    root also takes the Parts' English text and its expression node (#707).
    Returns True when ``map_doc`` changed.
    """
    map_root = act_root_node(map_doc)
    first_part = act_root_node(part_docs[0]) if part_docs else None
    act_iris = jsonld_id_values((first_part or {}).get("estleg:partOfAct"))
    if map_root is None or not act_iris or map_root.get("@id") != act_iris[0]:
        return False
    before = json.dumps(map_doc, sort_keys=True, ensure_ascii=False)
    preserved = {
        key: map_root[key]
        for key in ENRICHMENT_PREFERRED_FIELDS
        if map_root.get(key) not in (None, "", {}, [])
    }
    stamp_rt_metadata(map_root, root, act_root=True, **stamp_kwargs)
    map_root.update(preserved)
    backfill_map_root_english(map_doc, part_docs)
    promote_english_expression(map_doc, english_titles=english_titles)
    return json.dumps(map_doc, sort_keys=True, ensure_ascii=False) != before


def fetch_hash_row(record: dict | None, info: dict, *, kehtiv: str | None) -> dict | None:
    """One ``fetch_content_hashes.json`` row (#692), or None without a fetch.

    Attests one cached XML file: its SHA-256 and size, the public-API URL it
    was fetched from, the human RT page, and the redaction it holds
    (globaalID, terviktekst group id, validity start) for the ``--kehtiv``
    snapshot. The XML files ship as the ``rt_xml_<kehtiv>.tar.gz`` release
    asset (build_release_assets.py).
    """
    gid = info.get("xmlGlobalId") or info.get("gid")
    if not record or not gid:
        return None
    return {
        "sha256": record["sha256"],
        "bytes": record["bytes"],
        "source": build_xml_url(f"/akt/{gid}"),
        "page": build_act_page_url(f"/akt/{gid}"),
        "globalId": str(gid),
        "terviktekstId": str(info.get("tid") or ""),
        "kehtivuseAlgus": info.get("kehtivusAlgus") or None,
        "kehtiv": kehtiv,
        "cacheFile": record["cacheFile"],
    }


# ---------------------------------------------------------------------------
# English expression (#707)
# ---------------------------------------------------------------------------


def _types(node: dict) -> list[str]:
    raw = node.get("@type") or []
    return [raw] if isinstance(raw, str) else [t for t in raw if isinstance(t, str)]


def is_act_root(node: dict | None) -> bool:
    """True for an act root (``estleg:Act``), False for a Part or other node."""
    return isinstance(node, dict) and "estleg:Act" in _types(node) and "estleg:Part" not in _types(node)


def load_english_titles(path: Path = ENGLISH_ELI_PATH) -> dict[str, str]:
    """``{English RT IRI: English title}`` from ``english_eli.json``."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    acts = data.get("acts") if isinstance(data, dict) else None
    titles: dict[str, str] = {}
    if isinstance(acts, dict):
        for row in acts.values():
            if isinstance(row, dict) and row.get("url") and row.get("title_en"):
                titles[str(row["url"])] = str(row["title_en"])
    return titles


def english_text_iri(node: dict) -> str | None:
    for iri in jsonld_id_values(node.get(ENGLISH_TEXT_PROP)):
        if iri.startswith(ENGLISH_ELI_PREFIX):
            return iri
    return None


def _is_english_expression(node: dict) -> bool:
    nid = node.get("@id")
    return (
        isinstance(nid, str)
        and nid.startswith(ENGLISH_ELI_PREFIX)
        and LEGAL_EXPRESSION in _types(node)
    )


def promote_english_expression(
    doc: dict, *, english_titles: dict[str, str] | None = None
) -> bool:
    """Make the act root's English text an ``eli:LegalExpression`` node.

    Idempotent; returns True when ``doc`` changed. Only act roots are touched
    (Part roots repeat the act's English IRI but are not the Work). An
    expression node whose IRI the root no longer names is removed, so a new
    English consolidation replaces the old one instead of piling up.
    """
    graph = doc.get("@graph")
    root = act_root_node(doc)
    if not isinstance(graph, list) or not is_act_root(root):
        return False
    iri = english_text_iri(root)
    before = json.dumps(graph, sort_keys=True, ensure_ascii=False)

    kept = [n for n in graph if not (isinstance(n, dict) and _is_english_expression(n) and n.get("@id") != iri)]
    realized = [r for r in jsonld_id_values(root.get(IS_REALIZED_BY)) if not r.startswith(ENGLISH_ELI_PREFIX)]
    if iri:
        realized.append(iri)
    if realized:
        root[IS_REALIZED_BY] = (
            {"@id": realized[0]} if len(realized) == 1 else [{"@id": r} for r in realized]
        )
    else:
        root.pop(IS_REALIZED_BY, None)

    if iri:
        expression: dict = {
            "@id": iri,
            "@type": [LEGAL_EXPRESSION],
            "eli:language": {"@id": ENGLISH_LANGUAGE_IRI},
            REALIZES: {"@id": root["@id"]},
        }
        title = (english_titles or {}).get(iri)
        if title:
            expression["rdfs:label"] = {"@value": title, "@language": "en"}
        existing = next(
            (n for n in kept if isinstance(n, dict) and n.get("@id") == iri), None
        )
        if existing is None:
            kept.append(expression)
        else:
            existing.clear()
            existing.update(expression)
    graph[:] = kept
    return json.dumps(graph, sort_keys=True, ensure_ascii=False) != before


# ---------------------------------------------------------------------------
# Backfill of roots the generator has not rewritten
# ---------------------------------------------------------------------------


def legacy_source_global_id(root: dict) -> str | None:
    """The globaalID in a root's ``dcterms:source`` / ``sourceXml``, if any."""
    for key in (SOURCE_XML_PROP, "dcterms:source"):
        for iri in jsonld_id_values(root.get(key)):
            if "riigiteataja.ee" not in iri or "/en/" in iri:
                continue
            try:
                gid = rt_act_id(iri)
            except ValueError:
                continue
            if gid.isdigit():
                return gid
    return None


def backfill_root_identity(root: dict) -> bool:
    """Split source and mint ids on a root from its own RT link. Idempotent."""
    gid = legacy_source_global_id(root)
    if not gid:
        return False
    before = dict(root)
    stored_gid = root.get("estleg:globalId")
    if stored_gid and str(stored_gid) != gid:
        # The generator stamped a newer redaction than the link says; trust it.
        gid = str(stored_gid)
    fields = rt_identity_fields(global_id=gid, act_root=is_act_root(root))
    insert_after(root, fields, after=("estleg:contentStatusReason", "estleg:contentStatus", "dcterms:title"))
    return root != before


def backfill_map_root_english(map_doc: dict, part_docs: list[dict]) -> bool:
    """Give a multipart map root the English IRI its Part roots agree on."""
    root = act_root_node(map_doc)
    if not is_act_root(root) or english_text_iri(root):
        return False
    values = {english_text_iri(act_root_node(d) or {}) for d in part_docs}
    values.discard(None)
    if len(values) != 1:
        return False
    root[ENGLISH_TEXT_PROP] = {"@id": values.pop()}
    return True


def _map_part_docs(krr_dir: Path, map_doc: dict) -> list[dict]:
    root = act_root_node(map_doc) or {}
    docs: list[dict] = []
    for part_iri in jsonld_id_values(root.get("estleg:hasPart")):
        for path in krr_dir.glob("*_osa*_peep.json"):
            doc = _load(path)
            part_root = act_root_node(doc) if doc else None
            if part_root and part_root.get("@id") == part_iri:
                docs.append(doc)
                break
    return docs


def _load(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def backfill_corpus(krr_dir: Path = KRR_DIR, *, apply: bool = False) -> Counter:
    """Backfill every root law peep; returns counters (writes when ``apply``)."""
    stats: Counter = Counter()
    titles = load_english_titles()
    for path in sorted(krr_dir.glob("*_peep.json")):
        doc = _load(path)
        root = act_root_node(doc) if doc else None
        if root is None:
            stats["noRoot"] += 1
            continue
        if "estleg:Law" not in _types(root) and "estleg:Part" not in _types(root):
            continue
        stats["roots"] += 1
        changed = backfill_root_identity(root)
        if path.name.endswith("_map_peep.json"):
            changed |= backfill_map_root_english(doc, _map_part_docs(krr_dir, doc))
        changed |= promote_english_expression(doc, english_titles=titles)
        if changed:
            stats["changed"] += 1
            if apply:
                save_json(path, doc)
        root = act_root_node(doc) or {}
        for key in ("estleg:globalId", "eli:id_local", SOURCE_XML_PROP, IS_REALIZED_BY):
            if key in root:
                stats[key] += 1
    return stats


# ---------------------------------------------------------------------------
# Legacy IRI bridge on the load surface
# ---------------------------------------------------------------------------


def corpus_act_ids(krr_dir: Path = KRR_DIR) -> set[str]:
    """``@id`` of every node in root peeps and of every regulation act root."""
    ids: set[str] = set()
    for path in krr_dir.glob("*_peep.json"):
        doc = _load(path)
        for node in (doc or {}).get("@graph", []):
            if isinstance(node, dict) and isinstance(node.get("@id"), str):
                ids.add(node["@id"])
    for path in (krr_dir / "regulations").rglob("*.json"):
        doc = _load(path)
        root = act_root_node(doc) if doc else None
        if root and isinstance(root.get("@id"), str):
            ids.add(root["@id"])
    return ids


def build_bridge(
    rows: list[dict], resolvable: set[str]
) -> tuple[list[dict], list[str]]:
    """Invert ``<current> sameAs <legacy>`` rows; drop unresolvable currents."""
    nodes: dict[str, dict] = {}
    dropped: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        current = row.get("@id")
        for legacy in jsonld_id_values(row.get("owl:sameAs")):
            if not isinstance(current, str) or current not in resolvable:
                dropped.append(f"{legacy} -> {current}")
                continue
            nodes[legacy] = {
                "@id": legacy,
                "@type": ["owl:NamedIndividual"],
                "owl:sameAs": {"@id": current},
                "rdfs:comment": {"@value": BRIDGE_COMMENT, "@language": "en"},
            }
    return [nodes[k] for k in sorted(nodes)], dropped


def publish_bridge(
    krr_dir: Path = KRR_DIR,
    *,
    source: Path = BRIDGE_SOURCE,
    apply: bool = False,
) -> dict:
    """Write ``krr_outputs/bridges/act_iri_v2_sameas.jsonld``; return stats."""
    doc = _load(source)
    rows = (doc or {}).get("@graph") or []
    graph, dropped = build_bridge(rows, corpus_act_ids(krr_dir))
    dest = krr_dir / BRIDGE_SUBDIR / BRIDGE_NAME
    if apply:
        dest.parent.mkdir(parents=True, exist_ok=True)
        save_json(dest, {"@context": CONTEXT, "@graph": graph})
    return {
        "sourceRows": len(rows),
        "published": len(graph),
        "dropped": len(dropped),
        "droppedSample": dropped[:10],
        "dest": str(dest.relative_to(REPO_ROOT)) if dest.is_relative_to(REPO_ROOT) else str(dest),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run).")
    parser.add_argument("--bridge-only", action="store_true", help="Only (re)publish the legacy bridge.")
    parser.add_argument("--krr-dir", type=Path, default=KRR_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.bridge_only:
        stats = backfill_corpus(args.krr_dir, apply=args.apply)
        print(f"law roots: {dict(sorted(stats.items()))}")
    bridge = publish_bridge(args.krr_dir, apply=args.apply)
    print(f"legacy bridge: {json.dumps(bridge, ensure_ascii=False)}")
    if not args.apply:
        print("dry run: nothing written (pass --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
