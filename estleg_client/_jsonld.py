"""Small JSON-LD helpers shared by the loaders and row builders.

The corpus files are compact JSON-LD whose ``@context`` is a flat prefix map
(``{"estleg": "https://w3id.org/estleg/", ...}``). These helpers expand
``prefix:local`` terms against that map, read ``@type`` in both of the forms the
data uses (a string or a list), stream very large ``@graph`` arrays without
loading the whole document, and turn a selection of nodes into an
``rdflib.Graph``. Nothing here imports the producer package.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from rdflib import RDF, Graph, URIRef

ESTLEG_NS = "https://w3id.org/estleg/"

#: Prefixes every corpus file declares. Used when a node is inspected without
#: its document context (``has_type`` on a bare dict, row builders).
DEFAULT_PREFIXES: dict[str, str] = {
    "estleg": ESTLEG_NS,
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "eli": "http://data.europa.eu/eli/ontology#",
    "eli-dl": "http://data.europa.eu/eli/eli-draft-legislation-ontology#",
    "void": "http://rdfs.org/ns/void#",
    "dcat": "http://www.w3.org/ns/dcat#",
    "prov": "http://www.w3.org/ns/prov#",
}

LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"


def prefixes_of(context: Any) -> dict[str, str]:
    """Return the prefix map of a document ``@context`` merged over the defaults."""
    merged = dict(DEFAULT_PREFIXES)
    contexts = context if isinstance(context, list) else [context]
    for ctx in contexts:
        if not isinstance(ctx, Mapping):
            continue
        for key, value in ctx.items():
            if isinstance(value, str) and not key.startswith("@"):
                merged[key] = value
            elif isinstance(value, Mapping) and isinstance(value.get("@id"), str):
                merged[key] = value["@id"]
    return merged


def expand(term: str, prefixes: Mapping[str, str] | None = None) -> str:
    """Expand ``prefix:local`` to a full IRI; bare names are ``estleg:`` terms.

    Full IRIs (``https://...``, ``urn:...``) and unknown prefixes are returned
    unchanged, so the function is safe on any ``@id`` / ``@type`` value.
    """
    text = term.strip()
    if not text:
        return text
    table = DEFAULT_PREFIXES if prefixes is None else prefixes
    head, sep, local = text.partition(":")
    if not sep:
        return ESTLEG_NS + text
    if local.startswith("//"):
        return text
    base = table.get(head)
    return base + local if base is not None else text


def compact(iri: str) -> str:
    """Strip the ``estleg:`` namespace from a full IRI (other IRIs unchanged)."""
    if iri.startswith(ESTLEG_NS):
        return iri[len(ESTLEG_NS) :]
    if iri.startswith("estleg:"):
        return iri[len("estleg:") :]
    return iri


def as_list(value: Any) -> list[Any]:
    """``None`` -> ``[]``, a list -> itself, anything else -> ``[value]``."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def node_types(node: Mapping[str, Any], prefixes: Mapping[str, str] | None = None) -> frozenset[str]:
    """Expanded ``@type`` IRIs of a JSON-LD node (string or list form)."""
    return frozenset(
        expand(item, prefixes) for item in as_list(node.get("@type")) if isinstance(item, str)
    )


def has_type(
    node: Mapping[str, Any] | str | URIRef,
    *types: str,
    graph: Graph | None = None,
) -> bool:
    """True when ``node`` carries at least one of ``types`` -- exact IRI match.

    ``node`` is either a JSON-LD node dict (its ``@type`` may be a string or a
    list) or, with ``graph=``, a subject IRI looked up as ``rdf:type`` triples.
    ``types`` accept ``estleg:Sanction``, bare ``Sanction`` (an estleg term) or
    a full IRI. Unlike a substring test, ``Sanction`` never matches
    ``SanctionType`` and ``LegalProvision`` never matches ``KovProvision``.
    """
    if not types:
        raise TypeError("has_type() needs at least one type")
    wanted = {expand(t) for t in types}
    if graph is not None:
        subject = URIRef(expand(str(node)))
        return any((subject, RDF.type, URIRef(t)) in graph for t in wanted)
    if not isinstance(node, Mapping):
        raise TypeError("has_type() needs a JSON-LD node dict, or graph= with an IRI")
    return not wanted.isdisjoint(node_types(node))


def is_lfs_pointer(path: Path) -> bool:
    """True when ``path`` is an un-pulled Git LFS pointer file."""
    try:
        with path.open("rb") as handle:
            return handle.read(len(LFS_POINTER_PREFIX)) == LFS_POINTER_PREFIX
    except OSError:
        return False


def is_real_file(path: Path) -> bool:
    """An existing regular file that is not a Git LFS pointer."""
    return path.is_file() and not is_lfs_pointer(path)


@lru_cache(maxsize=3)
def _read_document_cached(key: str, _mtime_ns: int, _size: int) -> dict[str, Any]:
    with open(key, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{key} is not a JSON-LD document object")
    return payload


def read_document(path: Path) -> dict[str, Any]:
    """Parse a JSON-LD document (small LRU cache keyed by path, mtime and size)."""
    if is_lfs_pointer(path):
        raise ValueError(f"{path} is a Git LFS pointer; run `git lfs pull` or fetch_corpus()")
    stat = path.stat()
    return _read_document_cached(str(path), stat.st_mtime_ns, stat.st_size)


def graph_nodes(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The ``@graph`` node dicts of a document (a lone top-level node counts)."""
    nodes = document.get("@graph")
    if isinstance(nodes, list):
        return [node for node in nodes if isinstance(node, dict)]
    if "@id" in document:
        return [dict(document)]
    return []


class _StreamReader:
    """Incremental ``raw_decode`` over a text file, refilling on demand."""

    def __init__(self, handle: Any, chunk_size: int) -> None:
        self._handle = handle
        self._chunk = chunk_size
        self._decoder = json.JSONDecoder()
        self.buf = ""
        self.pos = 0
        self.eof = False

    def _fill(self) -> bool:
        if self.eof:
            return False
        data = self._handle.read(self._chunk)
        if not data:
            self.eof = True
            return False
        if self.pos > self._chunk:
            self.buf = self.buf[self.pos :]
            self.pos = 0
        self.buf += data
        return True

    def skip_ws(self) -> str:
        """Advance past whitespace and return the next character ('' at EOF)."""
        while True:
            while self.pos < len(self.buf) and self.buf[self.pos] in " \t\r\n":
                self.pos += 1
            if self.pos < len(self.buf):
                return self.buf[self.pos]
            if not self._fill():
                return ""

    def expect(self, char: str) -> None:
        if self.skip_ws() != char:
            raise ValueError(f"malformed JSON-LD stream: expected {char!r}")
        self.pos += 1

    def value(self) -> Any:
        self.skip_ws()
        while True:
            try:
                obj, end = self._decoder.raw_decode(self.buf, self.pos)
            except json.JSONDecodeError:
                if not self._fill():
                    raise
                continue
            # A number or literal that touches the buffer end may be truncated.
            if end >= len(self.buf) and not self.eof and not isinstance(obj, (dict, list, str)):
                if self._fill():
                    continue
            self.pos = end
            return obj


def stream_document(
    path: Path,
    *,
    chunk_size: int = 1 << 20,
) -> Iterator[tuple[str, Any]]:
    """Stream a JSON-LD document as ``(key, value)`` events without loading it whole.

    Top-level keys other than ``@graph`` are yielded once as ``(key, value)``;
    every ``@graph`` element is yielded as ``("@graph", node)``. Memory stays
    proportional to one node, which keeps a 300 MB aggregate tractable.
    """
    with path.open(encoding="utf-8") as handle:
        reader = _StreamReader(handle, chunk_size)
        reader.expect("{")
        if reader.skip_ws() == "}":
            return
        while True:
            key = reader.value()
            if not isinstance(key, str):
                raise ValueError(f"{path}: malformed top-level key")
            reader.expect(":")
            if key == "@graph" and reader.skip_ws() == "[":
                reader.pos += 1
                if reader.skip_ws() == "]":
                    reader.pos += 1
                else:
                    while True:
                        yield key, reader.value()
                        nxt = reader.skip_ws()
                        reader.pos += 1
                        if nxt == "]":
                            break
                        if nxt != ",":
                            raise ValueError(f"{path}: malformed @graph array")
            else:
                yield key, reader.value()
            nxt = reader.skip_ws()
            reader.pos += 1
            if nxt == "}":
                return
            if nxt != ",":
                raise ValueError(f"{path}: malformed top-level object")


def graph_from_nodes(nodes: Iterable[Mapping[str, Any]], context: Any) -> Graph:
    """Parse ``nodes`` under ``context`` into a fresh ``rdflib.Graph``."""
    graph = Graph()
    selected = list(nodes)
    if not selected:
        return graph
    payload = json.dumps({"@context": context, "@graph": selected}, ensure_ascii=False)
    graph.parse(data=payload, format="json-ld")
    return graph


def parse_into(graph: Graph, path: Path) -> None:
    """Parse one JSON-LD file into ``graph`` (refusing LFS pointers)."""
    if is_lfs_pointer(path):
        raise ValueError(f"{path} is a Git LFS pointer; run `git lfs pull` or fetch_corpus()")
    graph.parse(source=str(path), format="json-ld")
