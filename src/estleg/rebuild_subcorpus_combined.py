#!/usr/bin/env python3
"""Offline, deterministic rebuild of the sub-corpus ``*_combined.jsonld`` files.

The EUR-Lex, CURIA and EIS-draft aggregates (``eurlex/eurlex_combined.jsonld``,
``curia/curia_combined.jsonld``, ``eelnoud/eelnoud_combined.jsonld``) used to
be written only by the network-fetching generator mains. Every offline pass
that later touched a peep or a schema file (transposition enrichment, CURIA
linking, the T-Box consolidation that re-projects ``*_schema.json`` from the
controlled vocabulary) left the aggregate stale, and the parity gate in
``validate_all`` reported missing / stale / drifting IDs.

This module is the single offline producer. For one sub-corpus it emits::

    @graph = [ <Subcorpus>_Combined_Map head (stamped Dataset head),
               every node of <subcorpus>_schema.json (incl. any owl:Ontology),
               every instance node of <subcorpus>/*_peep.json (sorted by name) ]

de-duplicated by ``@id`` (first wins; divergent duplicates are reported).

The file lists are NOT hard-coded here: the peep glob, the schema files, the
per-peep head nodes that are folded into the combined head, and the combined
head ``@id`` all come from ``validate_all.SUBCORPUS_COMBINED_TARGETS`` — the
exact definition the parity gate checks against — and the Dataset label comes
from ``estleg_common.COMBINED_JSONLD_TARGETS``. The rebuild and the parity
check therefore cannot disagree about what a source is.

Determinism: no wall-clock value is written (``stamp_combined_dataset_head``
adds only the label, Dataset types, publisher, license and ``void:uriSpace``;
``stamp_version_fields`` then sets ``owl:versionInfo`` / ``owl:versionIRI``
from ``ONTOLOGY_VERSION``, #705),
inputs are read in a fixed order, and output goes through the shared
``save_json``. A second run over unchanged inputs is byte-identical.

Usage::

    python3 scripts/rebuild_subcorpus_combined.py              # all three
    python3 scripts/rebuild_subcorpus_combined.py --subcorpus curia
    python3 scripts/rebuild_subcorpus_combined.py --check      # exit 1 on drift
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from estleg.estleg_common import (
    COMBINED_JSONLD_TARGETS,
    CONTEXT,
    KRR_DIR,
    save_json,
    stamp_combined_dataset_head,
)
from estleg.stamp_combined_dataset_heads import stamp_version_fields
from estleg.validate_all import SUBCORPUS_COMBINED_TARGETS, SubcorpusCombinedSpec

# Descriptive (non-label) fields of each combined head, copied verbatim from
# the generator mains that historically wrote these files. The head ``@id``
# and the Dataset label are derived (see module docstring), not listed here.
HEAD_FIELDS: dict[str, dict[str, object]] = {
    "eurlex": {
        "dc:description": {
            "@value": "Kõik Euroopa Liidu õigusaktid eesti keeles EUR-Lexist.",
            "@language": "et",
        },
        "dc:source": "EUR-Lex – eur-lex.europa.eu",
        # The schema used to live only in eurlex_schema.json and be pulled in
        # by import. It is now embedded inline; the import is kept only while
        # its target resolves inside the combined (see _resolve_imports).
        "owl:imports": {"@id": "estleg:EURlex_Schema_2026"},
    },
    "curia": {
        "dc:description": {
            "@value": "Kõik Euroopa Liidu kohtulahendid eesti keeles EUR-Lexist.",
            "@language": "et",
        },
        "dc:source": "EUR-Lex / CURIA – eur-lex.europa.eu",
    },
    "eelnoud": {
        "dc:description": {
            "@value": "Kõik EIS eelnõud kõigist menetlusetappidest.",
            "@language": "et",
        },
        "dc:source": "Eelnõude infosüsteem (EIS) – eelnoud.valitsus.ee",
    },
}


class RebuildError(RuntimeError):
    """A source could not be read, so writing an aggregate would drop data."""


@dataclass
class RebuildResult:
    subcorpus: str
    path: Path
    nodes: int
    schema_nodes: int
    peep_nodes: int
    files: list[Path]
    duplicate_ids: int = 0
    divergent_duplicates: list[str] = field(default_factory=list)
    dropped_imports: list[str] = field(default_factory=list)
    written: bool = False
    up_to_date: bool = False

    def as_dict(self) -> dict:
        return {
            "subcorpus": self.subcorpus,
            "path": self.path,
            "nodes": self.nodes,
            "schema_nodes": self.schema_nodes,
            "peep_nodes": self.peep_nodes,
            "files": len(self.files),
            "duplicate_ids": self.duplicate_ids,
            "divergent_duplicates": list(self.divergent_duplicates),
            "dropped_imports": list(self.dropped_imports),
            "written": self.written,
            "up_to_date": self.up_to_date,
        }


def subcorpus_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in SUBCORPUS_COMBINED_TARGETS)


def subcorpus_spec(name: str) -> SubcorpusCombinedSpec:
    for spec in SUBCORPUS_COMBINED_TARGETS:
        if spec.name == name:
            return spec
    raise KeyError(f"unknown subcorpus {name!r}; expected one of {subcorpus_names()}")


def dataset_label(spec: SubcorpusCombinedSpec) -> str:
    for target in COMBINED_JSONLD_TARGETS:
        if target.get("relpath") == spec.combined_path_rel:
            label = target.get("label")
            if isinstance(label, str) and label:
                return label
    raise KeyError(f"{spec.combined_path_rel} has no label in COMBINED_JSONLD_TARGETS")


def head_id(spec: SubcorpusCombinedSpec) -> str:
    """The combined head ``@id`` is the parity gate's single expected extra."""
    if len(spec.expected_extras) != 1:
        raise ValueError(
            f"{spec.name}: expected exactly one combined head in expected_extras, "
            f"got {spec.expected_extras!r}"
        )
    return spec.expected_extras[0]


def _resolve(rel: str, krr_dir: Path, subcorpus_dir: Path | None) -> Path:
    """Resolve a ``<subcorpus>/<file>`` spec path.

    ``subcorpus_dir`` replaces the leading ``<subcorpus>/`` component, for
    callers (the generator wrappers, their tests) that hold the sub-corpus
    directory itself rather than the ``krr_outputs`` root.
    """
    if subcorpus_dir is None:
        return krr_dir / rel
    return subcorpus_dir.joinpath(*Path(rel).parts[1:])


def source_files(
    spec: SubcorpusCombinedSpec,
    krr_dir: Path,
    subcorpus_dir: Path | None = None,
) -> tuple[list[Path], list[Path]]:
    """Return (schema files that exist, peep files sorted by path)."""
    schemas = [_resolve(rel, krr_dir, subcorpus_dir) for rel in spec.schema_files]
    glob_path = _resolve(spec.peep_glob, krr_dir, subcorpus_dir)
    peeps = sorted(glob_path.parent.glob(glob_path.name))
    return [p for p in schemas if p.is_file()], peeps


def _load(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RebuildError(f"{path}: unreadable ({exc})") from exc
    if text.startswith("version https://git-lfs.github.com/spec/"):
        raise RebuildError(f"{path}: un-materialised Git LFS pointer; run `git lfs pull`")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RebuildError(f"{path}: invalid JSON ({exc})") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("@graph"), list):
        raise RebuildError(f"{path}: not a JSON-LD document with an @graph list")
    return doc


def _merge_context(target: dict, context: object, path: Path) -> None:
    if not isinstance(context, dict):
        return
    for prefix, iri in context.items():
        existing = target.get(prefix)
        if existing is None:
            target[prefix] = iri
        elif existing != iri:
            raise RebuildError(
                f"{path}: @context prefix {prefix!r} -> {iri!r} conflicts with "
                f"{existing!r} from an earlier source"
            )


def _canonical(node: dict) -> str:
    return json.dumps(node, sort_keys=True, ensure_ascii=False)


def _resolve_imports(head: dict, graph_ids: set[str]) -> list[str]:
    """Drop ``owl:imports`` targets that do not resolve inside the combined.

    A dangling import is a graph-closure failure on the Seadusloome load
    surface. The imported schema is embedded inline, so an import whose target
    node is absent is redundant; it returns automatically if the schema file
    regains its ontology head node.
    """
    imports = head.get("owl:imports")
    if imports is None:
        return []
    values = imports if isinstance(imports, list) else [imports]
    kept, dropped = [], []
    for value in values:
        target = value.get("@id") if isinstance(value, dict) else value
        (kept if target in graph_ids else dropped).append(value)
    if not kept:
        del head["owl:imports"]
    else:
        head["owl:imports"] = kept if isinstance(imports, list) else kept[0]
    return [v.get("@id") if isinstance(v, dict) else str(v) for v in dropped]


def build_subcorpus_combined(
    name: str,
    krr_dir: Path = KRR_DIR,
    *,
    subcorpus_dir: Path | None = None,
) -> tuple[dict, RebuildResult]:
    """Assemble the combined document for ``name`` without writing it."""
    spec = subcorpus_spec(name)
    schemas, peeps = source_files(spec, krr_dir, subcorpus_dir)
    if not peeps:
        pattern = _resolve(spec.peep_glob, krr_dir, subcorpus_dir)
        raise RebuildError(f"{name}: no peep files match {pattern}")
    for rel in spec.schema_files:
        if not _resolve(rel, krr_dir, subcorpus_dir).is_file():
            print(f"  WARNING: {name}: schema file {rel} not found; rebuilding from peeps only")

    head = {"@id": head_id(spec), "@type": ["owl:Ontology"], **json.loads(
        json.dumps(HEAD_FIELDS.get(name, {}), ensure_ascii=False)
    )}
    skip_ids = set(spec.expected_missing)

    context: dict = {}
    graph: list[dict] = [head]
    seen: dict[str, str] = {head["@id"]: _canonical(head)}
    duplicate_ids = 0
    divergent: list[str] = []
    counts = {"schema": 0, "peep": 0}

    def ingest(path: Path, kind: str) -> None:
        nonlocal duplicate_ids
        doc = _load(path)
        _merge_context(context, doc.get("@context"), path)
        for node in doc["@graph"]:
            if not isinstance(node, dict):
                continue
            nid = node.get("@id")
            if not isinstance(nid, str) or not nid:
                continue
            if kind == "peep" and nid in skip_ids:
                continue  # per-peep head, folded into the combined head
            canonical = _canonical(node)
            previous = seen.get(nid)
            if previous is not None:
                duplicate_ids += 1
                if previous != canonical:
                    divergent.append(nid)
                    print(
                        f"  WARNING: {name}: divergent duplicate {nid} in "
                        f"{path.name}; keeping the first occurrence"
                    )
                continue
            seen[nid] = canonical
            graph.append(node)
            counts[kind] += 1

    for path in schemas:
        ingest(path, "schema")
    for path in peeps:
        ingest(path, "peep")

    dropped = _resolve_imports(head, set(seen))
    for target in dropped:
        print(f"  NOTE: {name}: dropped owl:imports {target} (not in the combined graph)")

    doc = {"@context": context or dict(CONTEXT), "@graph": graph}
    stamp_combined_dataset_head(doc, label=dataset_label(spec))
    # #705: the head carries the release version. Build-derived, so it is
    # overwritten on every rebuild (not curated).
    stamp_version_fields(doc["@graph"][0])
    result = RebuildResult(
        subcorpus=name,
        path=_resolve(spec.combined_path_rel, krr_dir, subcorpus_dir),
        nodes=len(graph),
        schema_nodes=counts["schema"],
        peep_nodes=counts["peep"],
        files=[*schemas, *peeps],
        duplicate_ids=duplicate_ids,
        divergent_duplicates=divergent,
        dropped_imports=dropped,
    )
    return doc, result


def serialize(doc: dict) -> str:
    """Exactly the bytes ``save_json`` writes."""
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def rebuild_subcorpus_combined(
    name: str,
    krr_dir: Path = KRR_DIR,
    *,
    subcorpus_dir: Path | None = None,
    check: bool = False,
) -> RebuildResult:
    """Rebuild (or, with ``check``, compare) one sub-corpus aggregate.

    Writes only when the content changed, so an up-to-date file keeps its
    mtime as well as its bytes.
    """
    doc, result = build_subcorpus_combined(name, krr_dir, subcorpus_dir=subcorpus_dir)
    try:
        current = result.path.read_text(encoding="utf-8")
    except OSError:
        current = None
    result.up_to_date = current == serialize(doc)
    if not check and not result.up_to_date:
        save_json(result.path, doc)
        result.written = True
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--subcorpus",
        choices=[*subcorpus_names(), "all"],
        default="all",
        help="Which aggregate to rebuild (default: all).",
    )
    parser.add_argument(
        "--krr-dir",
        type=Path,
        default=KRR_DIR,
        help="krr_outputs root (default: the repository's).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; exit 1 if any committed aggregate differs from a rebuild.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    names = subcorpus_names() if args.subcorpus == "all" else (args.subcorpus,)
    stale: list[str] = []
    for name in names:
        try:
            result = rebuild_subcorpus_combined(name, args.krr_dir, check=args.check)
        except RebuildError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        state = (
            "up to date" if result.up_to_date
            else "STALE" if args.check
            else "rewritten"
        )
        print(
            f"{name}: {result.path.relative_to(args.krr_dir)} {state} — "
            f"{result.nodes} nodes (1 head + {result.schema_nodes} schema + "
            f"{result.peep_nodes} peep) from {len(result.files)} files; "
            f"{result.duplicate_ids} duplicate @id(s), "
            f"{len(result.divergent_duplicates)} divergent"
        )
        if not result.up_to_date:
            stale.append(name)
    if args.check and stale:
        print(f"Stale aggregates: {', '.join(stale)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
