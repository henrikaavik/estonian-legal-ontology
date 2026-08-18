#!/usr/bin/env python3
"""Assign containers to structuredBody provisions that lack estleg:isPartOf (#371).

The generator already emits unnamed-<jagu> and pre-<peatykk> containers.
Committed peeps were not regenerated. This pass infers a container from
neighbouring provisions (or mints ``Chapter_{prefix}_Preamble``) so the
280 orphans become reachable via isPartOf / hasPart.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from estleg.estleg_common import CONTEXT, KRR_DIR, save_json

PAR_NUM_RE = re.compile(r"_Par_(\d+)")


def _types(node: dict) -> list[str]:
    raw = node.get("@type", [])
    if isinstance(raw, str):
        return [raw]
    return [t for t in raw if isinstance(t, str)]


def _is_provision(node: dict) -> bool:
    types = _types(node)
    if "estleg:Subsection" in types:
        return False
    if any(t.startswith("estleg:LegalProvision") for t in types):
        return True
    return isinstance(node.get("estleg:paragrahv"), str) and "owl:NamedIndividual" in types


def _par_sort_key(node_id: str) -> tuple[int, str]:
    match = PAR_NUM_RE.search(node_id or "")
    return (int(match.group(1)), node_id) if match else (10**9, node_id or "")


def _prefix_from_graph(graph: list[dict]) -> str | None:
    for node in graph:
        nid = node.get("@id")
        if not isinstance(nid, str):
            continue
        if nid.startswith("estleg:Chapter_"):
            rest = nid[len("estleg:Chapter_") :]
            return rest.rsplit("_", 1)[0]
        if nid.startswith("estleg:Cluster_"):
            rest = nid[len("estleg:Cluster_") :]
            return rest.rsplit("_", 1)[0]
    return None


def _ref(iri: str) -> dict:
    return {"@id": iri}


def _append_has_part(chapter: dict, provision_id: str) -> None:
    existing = chapter.get("estleg:hasPart")
    items: list = []
    if isinstance(existing, list):
        items = list(existing)
    elif isinstance(existing, dict):
        items = [existing]
    ids = {
        item.get("@id")
        for item in items
        if isinstance(item, dict) and isinstance(item.get("@id"), str)
    }
    if provision_id in ids:
        return
    items.append(_ref(provision_id))
    chapter["estleg:hasPart"] = items


def _ensure_preamble(graph: list[dict], prefix: str, act_iri: str) -> tuple[str, str]:
    chapter_id = f"estleg:Chapter_{prefix}_Preamble"
    cluster_id = f"estleg:Cluster_{prefix}_Preamble"
    by_id = {
        n.get("@id"): n
        for n in graph
        if isinstance(n, dict) and isinstance(n.get("@id"), str)
    }
    if chapter_id not in by_id:
        graph.append(
            {
                "@id": chapter_id,
                "@type": ["owl:NamedIndividual", "estleg:Chapter"],
                "rdfs:label": "Sissejuhatavad sätted",
                "estleg:partOfAct": _ref(act_iri),
                "dcterms:subject": _ref(cluster_id),  #436: chapter refers to cluster
            }
        )
    if cluster_id not in by_id:
        graph.append(
            {
                "@id": cluster_id,
                "@type": ["owl:NamedIndividual", "estleg:TopicCluster", "skos:Concept"],
                "rdfs:label": "Sissejuhatavad sätted",
                "skos:prefLabel": "Sissejuhatavad sätted",
                "skos:inScheme": _ref(f"estleg:{prefix}_TopicScheme"),
            }
        )
    return chapter_id, cluster_id


def backfill_graph(graph: list[dict]) -> int:
    """Mutate ``graph``; return the number of provisions given isPartOf."""
    provisions = [n for n in graph if isinstance(n, dict) and _is_provision(n)]
    orphans = [n for n in provisions if "estleg:isPartOf" not in n]
    if not orphans:
        return 0
    prefix = _prefix_from_graph(graph)
    if not prefix:
        return 0
    act = next(
        (
            n
            for n in graph
            if isinstance(n, dict) and "estleg:Act" in _types(n)
        ),
        None,
    )
    act_iri = act.get("@id") if act and isinstance(act.get("@id"), str) else f"estleg:{prefix}_Map_2026"
    ordered = sorted(provisions, key=lambda n: _par_sort_key(str(n.get("@id", ""))))
    last_container: str | None = None
    last_cluster: str | None = None
    seen_container = False
    assigned = 0
    by_id = {
        n.get("@id"): n
        for n in graph
        if isinstance(n, dict) and isinstance(n.get("@id"), str)
    }
    for node in ordered:
        container = node.get("estleg:isPartOf")
        if isinstance(container, dict) and isinstance(container.get("@id"), str):
            last_container = container["@id"]
            cluster = node.get("estleg:requestedCluster")
            last_cluster = (
                cluster.get("@id")
                if isinstance(cluster, dict) and isinstance(cluster.get("@id"), str)
                else last_cluster
            )
            seen_container = True
            continue
        if "estleg:isPartOf" in node:
            continue
        if seen_container and last_container:
            target = last_container
            cluster_id = last_cluster
        else:
            target, cluster_id = _ensure_preamble(graph, prefix, act_iri)
            by_id[target] = next(n for n in graph if n.get("@id") == target)
        node["estleg:isPartOf"] = _ref(target)
        if cluster_id:
            node.setdefault("estleg:requestedCluster", _ref(cluster_id))
        chapter = by_id.get(target)
        if chapter is None:
            chapter = next((n for n in graph if n.get("@id") == target), None)
        if isinstance(chapter, dict) and isinstance(node.get("@id"), str):
            _append_has_part(chapter, node["@id"])
        assigned += 1
    return assigned


def backfill_file(path: Path) -> int:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return 0
    act = next(
        (n for n in graph if isinstance(n, dict) and "estleg:Act" in _types(n)),
        None,
    )
    if not act or act.get("estleg:contentStatus") != "structuredBody":
        return 0
    if not any(
        isinstance(n, dict) and str(n.get("@id", "")).startswith("estleg:Chapter_")
        for n in graph
    ):
        return 0
    assigned = backfill_graph(graph)
    if assigned:
        if "@context" not in doc:
            doc["@context"] = CONTEXT
        save_json(path, doc)
    return assigned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--krr-dir", type=Path, default=KRR_DIR)
    args = parser.parse_args(argv)
    total = 0
    files = 0
    for path in sorted(args.krr_dir.glob("*_peep.json")):
        assigned = backfill_file(path)
        if assigned:
            files += 1
            total += assigned
            print(f"  {path.name}: {assigned}")
    print(f"assigned isPartOf on {total} provisions in {files} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
