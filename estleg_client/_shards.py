"""Per-law shards cut from ``combined_ontology.jsonld`` for release downloads.

A release ships the enacted-law corpus as one ~300 MB aggregate, not as the
1,195 per-law peep files of a git checkout. ``build_law_shards`` streams the
aggregate twice (constant memory per node): pass one maps every node to the
``estleg:Law`` act it belongs to (via ``partOfAct`` / ``parentProvision`` /
``applicableProvision`` / ``citationSource`` / ``isPartOf`` / ``inPart``),
pass two appends each owned node to ``krr_outputs/_client/laws/<prefix>.jsonl``.
``load_law`` then reads one shard instead of the aggregate. Nodes that belong
to no single law (stubs, institutions, concept schemes) are not sharded.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from estleg_client._jsonld import (
    as_list,
    compact,
    expand,
    is_real_file,
    node_types,
    prefixes_of,
    stream_document,
)

SHARD_DIR = "_client"
SHARD_INDEX = "laws_index.json"
COMBINED_NAME = "combined_ontology.jsonld"
LAW_TYPE = "https://w3id.org/estleg/Law"
_OWNER_LINKS = (
    "partOfAct",
    "parentProvision",
    "applicableProvision",
    "citationSource",
    "isPartOf",
    "inPart",
)
_FLUSH_LINES = 20_000
_TRANSLIT = str.maketrans(
    {"ö": "o", "ä": "a", "ü": "u", "õ": "o", "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
     "š": "s", "ž": "z", "Š": "S", "Ž": "Z"}
)


def slugify(text: str, max_len: int = 80) -> str:
    """Same rule as the producer's ``estleg_common.slugify`` (INDEX slugs)."""
    text = unicodedata.normalize("NFC", text).translate(_TRANSLIT).lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")[:max_len].rstrip("_")


def shard_index_path(krr: Path) -> Path:
    return krr / SHARD_DIR / SHARD_INDEX


def _links(node: Mapping[str, Any], prefixes: Mapping[str, str]) -> list[str]:
    out: list[str] = []
    for local in _OWNER_LINKS:
        for value in as_list(node.get(f"estleg:{local}")):
            ref = value.get("@id") if isinstance(value, Mapping) else None
            if isinstance(ref, str):
                out.append(expand(ref, prefixes))
    return out


def _title(node: Mapping[str, Any]) -> str:
    fallback = ""
    for value in as_list(node.get("dcterms:title")):
        if isinstance(value, Mapping):
            text = value.get("@value")
            if isinstance(text, str):
                if value.get("@language") == "et":
                    return text
                fallback = fallback or text
        elif isinstance(value, str):
            fallback = fallback or value
    if fallback:
        return fallback
    for key in ("dc:source", "rdfs:label"):
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def build_law_shards(krr: Path) -> Path | None:
    """Shard ``krr/combined_ontology.jsonld``; return the index path (or None)."""
    combined = krr / COMBINED_NAME
    if not is_real_file(combined):
        return None
    context: Any = {}
    prefixes = prefixes_of({})
    links: dict[str, list[str]] = {}
    acts: dict[str, dict[str, Any]] = {}
    for key, value in stream_document(combined):
        if key == "@context":
            context = value
            prefixes = prefixes_of(value)
            continue
        if key != "@graph" or not isinstance(value, Mapping):
            continue
        node_id = value.get("@id")
        if not isinstance(node_id, str):
            continue
        iri = expand(node_id, prefixes)
        if LAW_TYPE in node_types(value, prefixes):
            prefix = compact(iri)
            prefix = prefix[: -len("_Map")] if prefix.endswith("_Map") else prefix
            title = _title(value)
            acts[iri] = {"act": iri, "prefix": prefix, "title": title, "slug": slugify(title)}
        node_links = _links(value, prefixes)
        if node_links:
            links[iri] = node_links

    owner_cache: dict[str, str | None] = {}

    def owner(iri: str, seen: frozenset[str] = frozenset()) -> str | None:
        if iri in owner_cache:
            return owner_cache[iri]
        if iri in acts:
            return iri
        if iri in seen:
            return None
        result: str | None = None
        for nxt in links.get(iri, ()):
            result = owner(nxt, seen | {iri})
            if result is not None:
                break
        owner_cache[iri] = result
        return result

    out_dir = krr / SHARD_DIR / "laws"
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.jsonl"):
        stale.unlink()
    file_of = {iri: f"laws/{meta['prefix']}.jsonl" for iri, meta in acts.items()}
    counts: dict[str, int] = defaultdict(int)
    pending: dict[str, list[str]] = defaultdict(list)
    buffered = 0

    def flush() -> None:
        nonlocal buffered
        for act_iri, lines in pending.items():
            with (krr / SHARD_DIR / file_of[act_iri]).open("a", encoding="utf-8") as handle:
                handle.writelines(lines)
        pending.clear()
        buffered = 0

    for key, value in stream_document(combined):
        if key != "@graph" or not isinstance(value, Mapping):
            continue
        node_id = value.get("@id")
        if not isinstance(node_id, str):
            continue
        act_iri = owner(expand(node_id, prefixes))
        if act_iri is None:
            continue
        pending[act_iri].append(json.dumps(value, ensure_ascii=False) + "\n")
        counts[act_iri] += 1
        buffered += 1
        if buffered >= _FLUSH_LINES:
            flush()
    flush()

    entries = [
        {**meta, "file": file_of[iri], "nodes": counts.get(iri, 0)}
        for iri, meta in sorted(acts.items(), key=lambda item: item[1]["prefix"])
    ]
    index = shard_index_path(krr)
    index.write_text(
        json.dumps({"context": context, "laws": entries}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return index


def read_shard_index(krr: Path) -> dict[str, Any] | None:
    path = shard_index_path(krr)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def read_shard_nodes(krr: Path, rel_file: str) -> list[dict[str, Any]]:
    path = krr / SHARD_DIR / rel_file
    nodes: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                nodes.append(json.loads(line))
    return nodes
