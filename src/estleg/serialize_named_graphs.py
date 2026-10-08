#!/usr/bin/env python3
"""Build a multi-corpus named-graph N-Quads dump (#474).

Each public load surface is a SPARQL GRAPH:

    https://w3id.org/estleg/graph/laws
    https://w3id.org/estleg/graph/regulations
    https://w3id.org/estleg/graph/riigikohus
    https://w3id.org/estleg/graph/eurlex
    https://w3id.org/estleg/graph/curia
    https://w3id.org/estleg/graph/drafts
    https://w3id.org/estleg/graph/enrichment-layers

    python3 -m estleg.serialize_named_graphs --write
    python3 -m estleg.serialize_named_graphs --write-sample

The full dump (``krr_outputs/estleg_all.nq.gz``) is generated, gitignored,
and is the release-asset payload for #473 (``build_release_assets`` writes it
to the repo-root ``release/``). The committed sample at
``krr_outputs/exports/estleg_all_sample.nq.gz`` is what
``docker compose up`` loads by default.

Fail-closed (#705): ``--write`` exits 1 without writing when any slot has no
source, unless ``--allow-partial`` is given. A slot source is either a single
file (the first one present wins) or a glob over a peep tree; the regulations
and riigikohus slots read their peep trees directly because neither corpus has
a combined aggregate. Large single-``@graph`` JSON-LD sources are parsed in
node batches so the dump stays within a few GB of memory.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from estleg.estleg_common import KRR_DIR
from estleg.serialize_corpus import (
    GRAPH_IRI_PREFIX,
    _peek_jsonld_context,
    graph_from_jsonld,
    is_lfs_pointer,
    iter_graph_nodes,
    nquad_from_ntriple,
    serialize_graph,
)

GRAPH_LAWS = f"{GRAPH_IRI_PREFIX}laws"
GRAPH_REGULATIONS = f"{GRAPH_IRI_PREFIX}regulations"
GRAPH_RIIGIKOHUS = f"{GRAPH_IRI_PREFIX}riigikohus"
GRAPH_EURLEX = f"{GRAPH_IRI_PREFIX}eurlex"
GRAPH_CURIA = f"{GRAPH_IRI_PREFIX}curia"
GRAPH_DRAFTS = f"{GRAPH_IRI_PREFIX}drafts"
GRAPH_ENRICHMENT = f"{GRAPH_IRI_PREFIX}enrichment-layers"

SAMPLE_DUMP = KRR_DIR / "exports" / "estleg_all_sample.nq.gz"
FULL_DUMP = KRR_DIR / "estleg_all.nq.gz"

_GRAPH_TAIL = re.compile(r"<https://w3id\.org/estleg/graph/[^>\s]+>\s*\.\s*$")

# Files larger than this are parsed in node batches (combined aggregates).
STREAM_THRESHOLD_BYTES = 64 * 1024 * 1024
STREAM_BATCH_NODES = 5000
_GLOB_CHARS = frozenset("*?[")


class MissingSlotError(RuntimeError):
    """A named-graph slot has no usable source (#705 fail-closed)."""


@dataclass(frozen=True)
class CorpusSlot:
    """One named graph in the #474 dump.

    ``sources`` are alternatives tried in order. A plain path is one file; a
    glob (``riigikohus/*_peep.json``) contributes every match, sorted. The first
    alternative that resolves to at least one real (non-LFS-pointer) file wins.
    """

    name: str
    graph_iri: str
    sources: tuple[str, ...]


SLOTS: tuple[CorpusSlot, ...] = (
    CorpusSlot("laws", GRAPH_LAWS, ("combined_ontology.nq", "combined_ontology.jsonld")),
    # #705: neither corpus has a combined aggregate (the old
    # REGULATIONS_COMBINED.jsonld / riigikohus_combined.jsonld targets never
    # existed, so these slots were silently empty); read the peep trees.
    CorpusSlot("regulations", GRAPH_REGULATIONS, ("regulations/**/*_peep.json",)),
    CorpusSlot("riigikohus", GRAPH_RIIGIKOHUS, ("riigikohus/*_peep.json",)),
    CorpusSlot("eurlex", GRAPH_EURLEX, ("eurlex/eurlex_combined.jsonld",)),
    CorpusSlot("curia", GRAPH_CURIA, ("curia/curia_combined.jsonld",)),
    CorpusSlot("drafts", GRAPH_DRAFTS, ("eelnoud/eelnoud_combined.jsonld",)),
    CorpusSlot(
        "enrichment-layers",
        GRAPH_ENRICHMENT,
        ("analytical/analytical_overlay.jsonld",),
    ),
)


def retarget_nquad_line(line: str, graph_iri: str) -> str:
    """Force the N-Quads graph term of one line onto ``graph_iri``."""
    raw = line.rstrip("\n\r")
    if not raw.strip() or raw.lstrip().startswith("#"):
        return raw + "\n"
    replacement = f"<{graph_iri}> ."
    if _GRAPH_TAIL.search(raw):
        return _GRAPH_TAIL.sub(replacement, raw) + "\n"
    if raw.endswith(" ."):
        return f"{raw[:-2]} <{graph_iri}> .\n"
    if raw.endswith("."):
        return f"{raw[:-1].rstrip()} <{graph_iri}> .\n"
    return f"{raw} <{graph_iri}> .\n"


def retarget_nquads(text: str, graph_iri: str) -> str:
    return "".join(retarget_nquad_line(line, graph_iri) for line in text.splitlines(True))


def nquads_from_jsonld(data: dict, graph_iri: str) -> str:
    """Serialize an in-memory JSON-LD document into one named graph."""
    graph = graph_from_jsonld(data)
    return serialize_graph(graph, "nq", graph_iri=graph_iri)


def _is_glob(relpath: str) -> bool:
    return any(ch in relpath for ch in _GLOB_CHARS)


def _resolve_source(krr_dir: Path, relpath: str) -> Path | None:
    path = krr_dir / relpath
    if path.is_file() and not is_lfs_pointer(path):
        return path
    return None


def resolve_slot_files(
    slot: CorpusSlot,
    krr_dir: Path,
    overrides: dict[str, Path] | None = None,
) -> list[Path]:
    """Files backing ``slot``: the first source alternative that resolves.

    ``overrides`` maps a source relpath (e.g. ``combined_ontology.nq``) to a
    file outside ``krr_dir``; the release-asset step uses it to feed the laws
    slot the dump it just regenerated under ``release/`` instead of the
    committed copy (#705). A glob alternative containing an LFS pointer is
    rejected outright (raising :class:`MissingSlotError`) rather than dumped
    partially.
    """
    for relpath in slot.sources:
        override = (overrides or {}).get(relpath)
        if override is not None and override.is_file() and not is_lfs_pointer(override):
            return [override]
        if _is_glob(relpath):
            matches = sorted(p for p in krr_dir.glob(relpath) if p.is_file())
            if not matches:
                continue
            pointers = [p for p in matches if is_lfs_pointer(p)]
            if pointers:
                raise MissingSlotError(
                    f"slot {slot.name!r}: {len(pointers)} LFS pointer(s) under "
                    f"{relpath} (e.g. {pointers[0]}); run `git lfs pull`"
                )
            return matches
        path = _resolve_source(krr_dir, relpath)
        if path is not None:
            return [path]
    return []


def missing_slots(
    krr_dir: Path,
    slots: Iterable[CorpusSlot] = SLOTS,
    overrides: dict[str, Path] | None = None,
) -> list[str]:
    """Names of slots with no usable source under ``krr_dir`` (or ``overrides``)."""
    return [slot.name for slot in slots if not resolve_slot_files(slot, krr_dir, overrides)]


def _jsonld_file_nquads(path: Path, graph_iri: str) -> Iterator[str]:
    if path.stat().st_size >= STREAM_THRESHOLD_BYTES:
        context = _peek_jsonld_context(path)
        batch: list[dict] = []

        def flush(nodes: list[dict]) -> Iterator[str]:
            graph = graph_from_jsonld({"@context": context or {}, "@graph": nodes})
            for line in graph.serialize(format="nt").splitlines():
                if line.strip():
                    yield nquad_from_ntriple(line, graph_iri)

        for node in iter_graph_nodes(path):
            batch.append(node)
            if len(batch) >= STREAM_BATCH_NODES:
                yield from flush(batch)
                batch = []
        if batch:
            yield from flush(batch)
        return
    graph = graph_from_jsonld(json.loads(path.read_text(encoding="utf-8")))
    yield from serialize_graph(graph, "nq", graph_iri=graph_iri).splitlines(True)


def iter_slot_nquads(
    slot: CorpusSlot,
    krr_dir: Path,
    overrides: dict[str, Path] | None = None,
) -> Iterator[str]:
    """Yield N-Quads lines for every file of the first available source of ``slot``."""
    for path in resolve_slot_files(slot, krr_dir, overrides):
        suffix = path.suffix.lower()
        if suffix == ".nq":
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    yield retarget_nquad_line(line, slot.graph_iri)
        elif suffix in {".json", ".jsonld"}:
            yield from _jsonld_file_nquads(path, slot.graph_iri)
        else:
            raise MissingSlotError(f"slot {slot.name!r}: unsupported source {path}")


def write_nquads_gz(
    dest: Path,
    chunks: Iterable[str],
) -> int:
    """Write gzipped N-Quads. Returns the number of non-empty lines."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with gzip.open(dest, "wt", encoding="utf-8") as handle:
        for chunk in chunks:
            if chunk.strip():
                count += 1
            handle.write(chunk if chunk.endswith("\n") else f"{chunk}\n")
    return count


def sample_documents() -> dict[str, dict]:
    """Tiny per-corpus JSON-LD used for the committed sample dump."""
    return {
        "laws": {
            "@context": {"estleg": "https://w3id.org/estleg/", "rdfs": "http://www.w3.org/2000/01/rdf-schema#"},
            "@graph": [
                {
                    "@id": "estleg:ABIPOL_Map_2026",
                    "@type": "estleg:Act",
                    "rdfs:label": "Abipolitseiniku seadus",
                }
            ],
        },
        "regulations": {
            "@context": {"estleg": "https://w3id.org/estleg/", "rdfs": "http://www.w3.org/2000/01/rdf-schema#"},
            "@graph": [
                {
                    "@id": "estleg:Reg_sample_Map_2026",
                    "@type": "estleg:NationalRegulation",
                    "rdfs:label": "Sample regulation",
                }
            ],
        },
        "riigikohus": {
            "@context": {"estleg": "https://w3id.org/estleg/", "rdfs": "http://www.w3.org/2000/01/rdf-schema#"},
            "@graph": [
                {
                    "@id": "estleg:RK_sample",
                    "@type": "estleg:CourtDecision",
                    "rdfs:label": "Sample Riigikohus decision",
                }
            ],
        },
        "eurlex": {
            "@context": {"estleg": "https://w3id.org/estleg/", "rdfs": "http://www.w3.org/2000/01/rdf-schema#"},
            "@graph": [
                {
                    "@id": "estleg:EU_32000L0060",
                    "@type": "estleg:EULegislation",
                    "rdfs:label": "Sample directive",
                }
            ],
        },
        "curia": {
            "@context": {"estleg": "https://w3id.org/estleg/", "rdfs": "http://www.w3.org/2000/01/rdf-schema#"},
            "@graph": [
                {
                    "@id": "estleg:CURIA_sample",
                    "@type": "estleg:EUCourtDecision",
                    "rdfs:label": "Sample CURIA decision",
                }
            ],
        },
        "drafts": {
            "@context": {"estleg": "https://w3id.org/estleg/", "rdfs": "http://www.w3.org/2000/01/rdf-schema#"},
            "@graph": [
                {
                    "@id": "estleg:Draft_sample",
                    "@type": "estleg:DraftLegislation",
                    "rdfs:label": "Sample draft",
                }
            ],
        },
        "enrichment-layers": {
            "@context": {"estleg": "https://w3id.org/estleg/", "rdfs": "http://www.w3.org/2000/01/rdf-schema#"},
            "@graph": [
                {
                    "@id": "estleg:AnalyticalOverlay",
                    "@type": "owl:Ontology",
                    "rdfs:label": "Analytical overlay sample",
                }
            ],
        },
    }


def write_sample_dump(dest: Path | None = None) -> dict[str, int]:
    """Write the committed 7-graph sample. Returns lines per graph IRI."""
    dest = dest or SAMPLE_DUMP
    counts: dict[str, int] = {}
    chunks: list[str] = []
    docs = sample_documents()
    for slot in SLOTS:
        text = nquads_from_jsonld(docs[slot.name], slot.graph_iri)
        lines = [line for line in text.splitlines(True) if line.strip()]
        counts[slot.graph_iri] = len(lines)
        chunks.extend(lines)
    write_nquads_gz(dest, chunks)
    return counts


def write_full_dump(
    krr_dir: Path | None = None,
    dest: Path | None = None,
    *,
    allow_partial: bool = False,
    source_overrides: dict[str, Path] | None = None,
) -> dict[str, int]:
    """Concatenate every corpus slot into ``estleg_all.nq.gz``.

    Fail-closed (#705): unless ``allow_partial``, a slot without a source (or
    one whose source yields no statements) raises :class:`MissingSlotError`;
    the up-front check runs before anything is written, and a slot that turns
    out empty while streaming removes the partial file.
    """
    root = krr_dir or KRR_DIR
    dest = dest or FULL_DUMP
    if not allow_partial:
        absent = missing_slots(root, overrides=source_overrides)
        if absent:
            raise MissingSlotError(f"no source for slot(s): {', '.join(absent)}")
    counts: dict[str, int] = {}

    def chunks() -> Iterator[str]:
        for slot in SLOTS:
            n = 0
            for line in iter_slot_nquads(slot, root, source_overrides):
                if line.strip():
                    n += 1
                yield line
            if n:
                counts[slot.graph_iri] = n

    tmp = dest.with_name(dest.name + ".tmp")
    try:
        write_nquads_gz(tmp, chunks())
        if not allow_partial:
            empty = [slot.name for slot in SLOTS if slot.graph_iri not in counts]
            if empty:
                raise MissingSlotError(f"slot(s) produced no statements: {', '.join(empty)}")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return counts


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write krr_outputs/estleg_all.nq.gz from available corpus files.",
    )
    parser.add_argument(
        "--write-sample",
        action="store_true",
        help="Write the committed 7-graph sample dump.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Override dump path.",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="With --write: dump the slots that have sources and exit 0 even "
        "when some slot has none (default: exit 1 without writing).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.write_sample:
        dest = args.output or SAMPLE_DUMP
        counts = write_sample_dump(dest)
        print(f"wrote sample {dest} graphs={len(counts)} lines={sum(counts.values())}")
        for iri, n in counts.items():
            print(f"  {iri}: {n}")
        return 0
    if args.write:
        dest = args.output or FULL_DUMP
        try:
            counts = write_full_dump(dest=dest, allow_partial=args.allow_partial)
        except MissingSlotError as exc:
            print(f"ERROR: {exc}; nothing written (pass --allow-partial to "
                  "dump the available slots)", file=sys.stderr)
            return 1
        print(f"wrote {dest} graphs={len(counts)} lines={sum(counts.values())}")
        for iri, n in sorted(counts.items()):
            print(f"  {iri}: {n}")
        missing = [slot.name for slot in SLOTS if slot.graph_iri not in counts]
        if missing:
            print(f"PARTIAL (--allow-partial) — no source: {', '.join(missing)}")
        return 0
    print("specify --write or --write-sample", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
