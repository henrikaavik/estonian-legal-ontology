#!/usr/bin/env python3
"""Serialize JSON-LD corpus files to Turtle, N-Triples, or N-Quads (#541).

Triplestore bulk loaders (Jena tdbloader, Blazegraph DataLoader, Virtuoso,
GraphDB) prefer NT/TTL/NQ over JSON-LD. Combined JSON-LD was measured at
~44 s / ~3 GB RSS / 2.2 M triples; prefer .nt or named-graph N-Quads for
bulk load.

    python3 scripts/serialize_corpus.py --input PATH --format nt|ttl|nq --output PATH

N-Quads wrap every triple in a named graph. Default graph IRI is
``https://w3id.org/estleg/graph/combined`` for combined_ontology inputs,
otherwise ``https://w3id.org/estleg/graph/<stem>`` derived from the input
filename (``_peep`` suffix stripped). Override with ``--graph IRI``.

Combined ``.nt`` / ``.nq`` (and ``.ttl`` when generated) are LFS-tracked
next to ``combined_ontology.jsonld``. A small proof export also lives at
``krr_outputs/exports/abipolitseiniku_seadus.nt``.

``--stream`` (#705) serializes a single-``@graph`` JSON-LD file in node
batches and line-sorts N-Triples / N-Quads with an external ``sort -u``, so
the 2.6 M-triple combined graph fits in a few hundred MB instead of the
10-14 GB an in-memory ``Graph`` + ``Dataset`` copy needs. One pass can write
several formats. The triple set is the same as a whole-file parse (each
top-level node is independent; duplicates are removed by ``sort -u``); only
blank-node labels differ, as they do between any two rdflib runs.

    python3 scripts/serialize_corpus.py --stream --input krr_outputs/combined_ontology.jsonld \
        --format nt --output krr_outputs/combined_ontology.nt
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from rdflib import Dataset, Graph, URIRef

from estleg.estleg_common import CONTEXT

FORMATS = ("nt", "ttl", "nq")
RDFLIB_FORMAT = {
    "nt": "nt",
    "ttl": "turtle",
    "nq": "nquads",
}

DEFAULT_GRAPH_IRI = "https://w3id.org/estleg/graph/combined"
GRAPH_IRI_PREFIX = "https://w3id.org/estleg/graph/"
LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"

_COMBINED_STEMS = frozenset({"combined", "combined_ontology"})


def is_lfs_pointer(path: Path) -> bool:
    """True iff ``path`` is a Git-LFS pointer rather than real RDF/JSON."""
    try:
        with path.open(encoding="utf-8") as handle:
            return handle.readline().startswith(LFS_POINTER_PREFIX)
    except OSError:
        return False


def graph_iri_for(input_path: Path | str, explicit: str | None = None) -> str:
    """Named-graph IRI for an N-Quads dump of ``input_path``."""
    if explicit:
        return explicit
    stem = Path(input_path).stem
    stem = stem.removesuffix("_peep")
    if stem in _COMBINED_STEMS:
        return DEFAULT_GRAPH_IRI
    if not stem:
        return DEFAULT_GRAPH_IRI
    return f"{GRAPH_IRI_PREFIX}{stem}"


def bind_context_prefixes(graph: Graph, context: object | None = None) -> None:
    """Bind the corpus prefix map (plus any extra JSON-LD ``@context`` IRIs)."""
    prefixes = dict(CONTEXT)
    if isinstance(context, dict):
        for key, value in context.items():
            if key.startswith("@"):
                continue
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                prefixes[key] = value
    for prefix, iri in prefixes.items():
        graph.bind(prefix, iri)


def _normalize_format(fmt: str) -> str:
    key = fmt.lower().strip()
    aliases = {"turtle": "ttl", "ntriples": "nt", "n-triples": "nt", "nquads": "nq", "n-quads": "nq"}
    key = aliases.get(key, key)
    if key not in RDFLIB_FORMAT:
        allowed = ", ".join(FORMATS)
        raise ValueError(f"unsupported format {fmt!r}; expected one of {allowed}")
    return key


def _sorted_line_dump(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    lines.sort()
    return "\n".join(lines) + ("\n" if lines else "")


def graph_from_jsonld(data: str | bytes | dict[str, Any]) -> Graph:
    """Parse an in-memory JSON-LD document into a bound ``Graph``."""
    context: object | None = data.get("@context") if isinstance(data, dict) else None
    payload: str | bytes
    if isinstance(data, dict):
        payload = json.dumps(data)
    else:
        payload = data
    graph = Graph()
    graph.parse(data=payload, format="json-ld")
    bind_context_prefixes(graph, context)
    return graph


def load_jsonld(path: Path | str) -> Graph:
    """Parse a JSON-LD file. Rejects Git-LFS pointer stubs."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"input not found: {source}")
    if is_lfs_pointer(source):
        raise ValueError(
            f"{source} is a Git LFS pointer; run `git lfs pull` before serializing"
        )
    graph = Graph()
    graph.parse(source=str(source), format="json-ld")
    bind_context_prefixes(graph, _peek_jsonld_context(source))
    return graph


def _peek_jsonld_context(path: Path) -> object | None:
    """Read a top-level ``@context`` object from the start of ``path``.

    Combined JSON-LD is hundreds of MB; never ``json.load`` the whole file
    just to bind prefixes. The corpus writes ``@context`` first.
    """
    try:
        with path.open(encoding="utf-8") as handle:
            head = handle.read(256_000)
    except OSError:
        return None
    marker = '"@context"'
    start = head.find(marker)
    if start < 0:
        return None
    colon = head.find(":", start + len(marker))
    if colon < 0:
        return None
    snippet = head[colon + 1 :].lstrip()
    if not snippet.startswith("{"):
        return None
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(snippet)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def dataset_from_graph(graph: Graph, graph_iri: str) -> Dataset:
    """Copy ``graph`` triples into a Dataset named graph at ``graph_iri``."""
    dataset = Dataset()
    named = dataset.graph(identifier=URIRef(graph_iri))
    for triple in graph:
        named.add(triple)
    bind_context_prefixes(named)
    return dataset


def named_graph_iris(dataset: Dataset) -> list[str]:
    """IRI strings of non-empty named graphs in ``dataset``."""
    found: list[str] = []
    for named in dataset.graphs():
        identifier = named.identifier
        if not isinstance(identifier, URIRef):
            continue
        iri = str(identifier)
        if iri.startswith("urn:x-rdflib:"):
            continue
        if len(named) == 0:
            continue
        found.append(iri)
    return found


def serialize_graph(
    graph: Graph,
    fmt: str,
    *,
    graph_iri: str | None = None,
    destination: Path | str | None = None,
) -> str:
    """Serialize ``graph`` to ``nt`` / ``ttl`` / ``nq``. Return the text.

    For ``nq``, triples are wrapped in ``graph_iri`` (default
    :data:`DEFAULT_GRAPH_IRI`). N-Triples and N-Quads are line-sorted so
    committed dumps stay deterministic.
    """
    key = _normalize_format(fmt)
    if key == "nq":
        iri = graph_iri or DEFAULT_GRAPH_IRI
        target: Graph | Dataset = dataset_from_graph(graph, iri)
    else:
        bind_context_prefixes(graph)
        target = graph
    text = target.serialize(format=RDFLIB_FORMAT[key])
    if key in {"nt", "nq"}:
        text = _sorted_line_dump(text)
    if destination is not None:
        out = Path(destination)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    return text


def serialize_file(
    input_path: Path | str,
    output_path: Path | str,
    fmt: str,
    *,
    graph_iri: str | None = None,
) -> dict[str, Any]:
    """Load JSON-LD from ``input_path`` and write ``fmt`` to ``output_path``."""
    source = Path(input_path)
    dest = Path(output_path)
    graph = load_jsonld(source)
    iri = graph_iri_for(source, graph_iri)
    text = serialize_graph(graph, fmt, graph_iri=iri, destination=dest)
    return {
        "triples": len(graph),
        "graph_iri": iri,
        "bytes": dest.stat().st_size,
        "output": dest,
        "text": text,
    }


def iter_graph_nodes(path: Path) -> Iterator[dict]:
    """Yield each top-level ``@graph`` object of ``path`` without parsing RDF."""
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
            yield obj
        index = end


def nquad_from_ntriple(line: str, graph_iri: str) -> str:
    """Turn one N-Triples line into the N-Quads line for ``graph_iri``."""
    raw = line.rstrip("\r\n").rstrip()
    if not raw.endswith("."):
        raise ValueError(f"not an N-Triples statement: {line!r}")
    return f"{raw[:-1].rstrip()} <{graph_iri}> .\n"


def external_sort_unique(source: Path, dest: Path, *, work_dir: Path) -> None:
    """``LC_ALL=C sort -u`` (byte order == code-point order for UTF-8).

    Matches :func:`_sorted_line_dump` ordering while keeping memory bounded.
    ``-u`` restores RDF set semantics across batches.
    """
    env = dict(os.environ, LC_ALL="C")
    subprocess.run(
        ["sort", "-u", "-S", "512M", "-T", str(work_dir), "-o", str(dest), str(source)],
        check=True,
        env=env,
    )


def serialize_jsonld_streaming(
    input_path: Path | str,
    outputs: dict[str, Path | str],
    *,
    graph_iri: str | None = None,
    batch_size: int = 5000,
) -> dict[str, Any]:
    """Serialize a large single-``@graph`` JSON-LD file in bounded memory (#705).

    ``outputs`` maps a format (``nt`` / ``nq`` / ``ttl``) to its destination.
    N-Triples and N-Quads are line-sorted and de-duplicated exactly like
    :func:`serialize_graph`'s output; Turtle is a concatenation of per-batch
    Turtle documents (repeated ``@prefix`` directives are legal Turtle).
    Writes go to ``<dest>.tmp`` first and are renamed into place on success.
    Returns ``{"triples": <distinct N-Triples lines or parsed count>, "graph_iri",
    "outputs": {fmt: {"path", "bytes"}}}``.
    """
    source = Path(input_path)
    if not source.is_file():
        raise FileNotFoundError(f"input not found: {source}")
    if is_lfs_pointer(source):
        raise ValueError(
            f"{source} is a Git LFS pointer; run `git lfs pull` before serializing"
        )
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    targets = {_normalize_format(fmt): Path(dest) for fmt, dest in outputs.items()}
    if not targets:
        raise ValueError("no output formats requested")
    context = _peek_jsonld_context(source) or dict(CONTEXT)
    iri = graph_iri_for(source, graph_iri)

    unsorted: dict[str, Path] = {}
    handles: dict[str, Any] = {}
    for key, dest in targets.items():
        dest.parent.mkdir(parents=True, exist_ok=True)
        scratch = dest.with_name(dest.name + (".unsorted" if key in {"nt", "nq"} else ".tmp"))
        unsorted[key] = scratch
        handles[key] = scratch.open("w", encoding="utf-8")

    parsed = 0

    def flush(batch: list[dict]) -> None:
        nonlocal parsed
        graph = graph_from_jsonld({"@context": context, "@graph": batch})
        parsed += len(graph)
        if "nt" in handles or "nq" in handles:
            lines = [
                line + "\n"
                for line in graph.serialize(format="nt").splitlines()
                if line.strip()
            ]
            if "nt" in handles:
                handles["nt"].writelines(lines)
            if "nq" in handles:
                handles["nq"].writelines(nquad_from_ntriple(line, iri) for line in lines)
        if "ttl" in handles:
            handles["ttl"].write(graph.serialize(format="turtle"))
            handles["ttl"].write("\n")

    try:
        batch: list[dict] = []
        for node in iter_graph_nodes(source):
            batch.append(node)
            if len(batch) >= batch_size:
                flush(batch)
                batch = []
        if batch:
            flush(batch)
    finally:
        for handle in handles.values():
            handle.close()

    result: dict[str, Any] = {"triples": parsed, "graph_iri": iri, "outputs": {}}
    try:
        for key, dest in targets.items():
            scratch = unsorted[key]
            if key in {"nt", "nq"}:
                staged = dest.with_name(dest.name + ".tmp")
                external_sort_unique(scratch, staged, work_dir=dest.parent)
                scratch.unlink()
                staged.replace(dest)
                if key == "nt":
                    with dest.open("rb") as handle:
                        result["triples"] = sum(1 for _ in handle)
            else:
                scratch.replace(dest)
            result["outputs"][key] = {"path": dest, "bytes": dest.stat().st_size}
    finally:
        for scratch in unsorted.values():
            scratch.unlink(missing_ok=True)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", type=Path, required=True, help="JSON-LD file to convert")
    parser.add_argument(
        "--stream",
        action="store_true",
        help=(
            "Bounded-memory batch serialization for large single-@graph files "
            "(combined_ontology.jsonld); see serialize_jsonld_streaming (#705)."
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Top-level nodes per batch with --stream (default 5000).",
    )
    parser.add_argument(
        "--format",
        choices=FORMATS,
        required=True,
        help="Output syntax: nt (N-Triples), ttl (Turtle), nq (N-Quads)",
    )
    parser.add_argument("--output", type=Path, required=True, help="Destination path")
    parser.add_argument(
        "--graph",
        default=None,
        help=(
            "Named graph IRI for --format nq "
            f"(default: {DEFAULT_GRAPH_IRI} for combined, else derived from filename)"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.stream:
        try:
            result = serialize_jsonld_streaming(
                args.input,
                {args.format: args.output},
                graph_iri=args.graph,
                batch_size=args.batch_size,
            )
        except (FileNotFoundError, ValueError, OSError, subprocess.CalledProcessError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"Wrote {result['triples']} triples to {args.output} (streamed)")
        if args.format == "nq":
            print(f"Named graph: {result['graph_iri']}")
        return 0
    try:
        result = serialize_file(args.input, args.output, args.format, graph_iri=args.graph)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {result['triples']} triples to {args.output}")
    if args.format == "nq":
        print(f"Named graph: {result['graph_iri']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
