#!/usr/bin/env python3
"""Project committed peeps into a star-schema CSV/Parquet dump (#559).

The pandas/R analytics path should not have to flatten 262 MB of JSON-LD.
This exporter walks ordinary ``*_peep.json`` files (never
``combined_ontology.jsonld``) and writes five tables:

* ``laws.csv`` — act roots
* ``provisions.csv`` — § and lõige rows (legalText truncated to 2000 chars)
* ``citations.csv`` — ``estleg:references`` / ``estleg:referencedBy`` edges
* ``sanctions.csv`` — sanction sidecar nodes
* ``court_decisions.csv`` — Riigikohus decision metadata (no full text)

CSV uses the stdlib ``csv`` module. Parquet is written only when ``pyarrow``
imports; otherwise a one-line stderr note is printed and CSV still ships.
``pyarrow`` is not a project dependency.

Default globs are a small real subset (one act + its sanctions sidecar +
one Riigikohus year) so a bare ``--out krr_outputs/exports`` run stays
cheap and is what regenerates the committed sample.

    python3 scripts/serialize_tabular.py --out krr_outputs/exports
    python3 scripts/serialize_tabular.py --out /tmp/estleg-tabular \\
      --laws-glob '*_peep.json' \\
      --sanctions-glob 'sanctions/sanctions_*.json' \\
      --court-glob 'riigikohus/riigikohus_*_peep.json'
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from estleg.estleg_common import (
    KRR_DIR,
    NS,
    REPO_ROOT,
    act_prefix_from_iri,
    jsonld_text,
)

ESTLEG_PREFIX = "estleg:"
LEGAL_TEXT_MAX = 2000
LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"

SKIP_FILENAMES = frozenset(
    {
        "combined_ontology.jsonld",
        "similarity_index.json",
    }
)

DEFAULT_LAWS_GLOBS: tuple[str, ...] = ("abipolitseiniku_seadus_peep.json",)
DEFAULT_SANCTIONS_GLOBS: tuple[str, ...] = (
    "sanctions/sanctions_abipolitseiniku_seadus.json",
)
DEFAULT_COURT_GLOBS: tuple[str, ...] = ("riigikohus/riigikohus_2020_peep.json",)

LAW_COLUMNS: tuple[str, ...] = (
    "iri",
    "slug",
    "title",
    "abbreviation",
    "temporalStatus",
    "kehtiv",
)
PROVISION_COLUMNS: tuple[str, ...] = (
    "iri",
    "act",
    "paragrahv",
    "subsection",
    "legalText",
    "in_force",
    "temporalStatus",
    "valid_from",
    "valid_to",
)
CITATION_COLUMNS: tuple[str, ...] = ("source", "target", "predicate")
SANCTION_COLUMNS: tuple[str, ...] = (
    "iri",
    "provision",
    "type",
    "min",
    "max",
    "unit",
)
COURT_COLUMNS: tuple[str, ...] = ("iri", "caseNumber", "date", "ecli", "chamber")

TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "laws": LAW_COLUMNS,
    "provisions": PROVISION_COLUMNS,
    "citations": CITATION_COLUMNS,
    "sanctions": SANCTION_COLUMNS,
    "court_decisions": COURT_COLUMNS,
}

CITATION_PREDICATES: tuple[str, ...] = (
    "estleg:references",
    "estleg:referencedBy",
)

_PROVISION_TYPE_PREFIX = "estleg:LegalProvision"
_SUBSECTION_TYPE = "estleg:Subsection"
_KOV_PROVISION_TYPE = "estleg:KovProvision"
_LAW_TYPE = "estleg:Law"
_SANCTION_TYPE = "estleg:Sanction"
_COURT_TYPE = "estleg:CourtDecision"

Tables = dict[str, list[dict[str, str]]]


def compact_iri(value: str) -> str:
    """Collapse a full estleg IRI to the ``estleg:`` prefix form."""
    if value.startswith(NS):
        return ESTLEG_PREFIX + value[len(NS) :]
    return value


def node_types(node: Mapping[str, Any]) -> list[str]:
    raw = node.get("@type") or []
    if isinstance(raw, str):
        raw = [raw]
    return [compact_iri(item) for item in raw if isinstance(item, str)]


def node_id(node: Mapping[str, Any]) -> str:
    raw = node.get("@id")
    return compact_iri(raw) if isinstance(raw, str) else ""


def ref_ids(value: Any) -> list[str]:
    """Return every ``@id`` reachable in a JSON-LD object/list/string value."""
    out: list[str] = []
    if isinstance(value, str):
        out.append(compact_iri(value))
    elif isinstance(value, dict):
        ref = value.get("@id")
        if isinstance(ref, str):
            out.append(compact_iri(ref))
    elif isinstance(value, list):
        for item in value:
            out.extend(ref_ids(item))
    return out


def ref_id(value: Any) -> str:
    ids = ref_ids(value)
    return ids[0] if ids else ""


def jsonld_scalar(value: Any) -> str:
    """Plain string for CSV: booleans, numbers, ``@value`` objects, lists."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict) and "@value" in value:
        return jsonld_scalar(value.get("@value"))
    if isinstance(value, list):
        parts = [jsonld_scalar(item) for item in value]
        return " ".join(part for part in parts if part)
    return jsonld_text(value)


def truncate_legal_text(text: str, max_len: int = LEGAL_TEXT_MAX) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len]


def derive_abbrev(act_id: str) -> str:
    """Derive a law abbreviation from an act-root ``@id``."""
    return act_prefix_from_iri(compact_iri(act_id)) or compact_iri(act_id).removeprefix(
        ESTLEG_PREFIX
    )


def file_slug(path: Path) -> str:
    name = path.name
    if name.endswith("_peep.json"):
        return name[: -len("_peep.json")]
    if name.startswith("sanctions_") and name.endswith(".json"):
        return name[len("sanctions_") : -len(".json")]
    return path.stem


def is_law_node(node: Mapping[str, Any]) -> bool:
    return _LAW_TYPE in node_types(node)


def is_provision_node(node: Mapping[str, Any]) -> bool:
    for item in node_types(node):
        if item.startswith(_PROVISION_TYPE_PREFIX):
            return True
        if item in {_SUBSECTION_TYPE, _KOV_PROVISION_TYPE}:
            return True
    return False


def is_subsection_node(node: Mapping[str, Any]) -> bool:
    return _SUBSECTION_TYPE in node_types(node)


def is_sanction_node(node: Mapping[str, Any]) -> bool:
    return _SANCTION_TYPE in node_types(node)


def is_court_decision_node(node: Mapping[str, Any]) -> bool:
    return _COURT_TYPE in node_types(node)


def iter_nodes(graph: object) -> list[dict[str, Any]]:
    """Accept a JSON-LD document, an ``@graph`` list, or a single node."""
    if isinstance(graph, dict):
        raw = graph.get("@graph")
        if isinstance(raw, list):
            return [node for node in raw if isinstance(node, dict)]
        if "@id" in graph or "@type" in graph:
            return [graph]
        return []
    if isinstance(graph, Sequence) and not isinstance(graph, (str, bytes)):
        return [node for node in graph if isinstance(node, dict)]
    return []


def law_row(
    node: Mapping[str, Any],
    *,
    slug: str = "",
    abbreviation: str = "",
) -> dict[str, str]:
    iri = node_id(node)
    title = (
        jsonld_text(node.get("dcterms:title"))
        or jsonld_text(node.get("dc:source"))
        or jsonld_text(node.get("rdfs:label"))
    )
    return {
        "iri": iri,
        "slug": slug,
        "title": title,
        "abbreviation": abbreviation or derive_abbrev(iri),
        "temporalStatus": jsonld_text(node.get("estleg:temporalStatus")),
        "kehtiv": jsonld_scalar(node.get("estleg:kehtiv")),
    }


def provision_row(
    node: Mapping[str, Any],
    *,
    act: str = "",
    paragrahv: str = "",
    in_force: str = "",
) -> dict[str, str]:
    text = jsonld_text(node.get("estleg:legalText")) or jsonld_text(
        node.get("estleg:summary")
    )
    subsection = (
        jsonld_text(node.get("estleg:subsectionNumber"))
        if is_subsection_node(node)
        else ""
    )
    return {
        "iri": node_id(node),
        "act": compact_iri(act) if act else ref_id(node.get("estleg:partOfAct")),
        "paragrahv": paragrahv or jsonld_text(node.get("estleg:paragrahv")),
        "subsection": subsection,
        "legalText": truncate_legal_text(text),
        "in_force": in_force or jsonld_scalar(node.get("estleg:inForce")),
        "temporalStatus": jsonld_text(node.get("estleg:temporalStatus")),
        "valid_from": jsonld_scalar(
            node.get("estleg:validFrom") or node.get("estleg:versionValidFrom")
        ),
        "valid_to": jsonld_scalar(
            node.get("estleg:validTo") or node.get("estleg:versionValidTo")
        ),
    }


def citation_rows(node: Mapping[str, Any]) -> list[dict[str, str]]:
    source = node_id(node)
    if not source:
        return []
    rows: list[dict[str, str]] = []
    for predicate in CITATION_PREDICATES:
        for target in ref_ids(node.get(predicate)):
            if not target:
                continue
            rows.append(
                {
                    "source": source,
                    "target": target,
                    "predicate": predicate,
                }
            )
    return rows


def sanction_row(node: Mapping[str, Any]) -> dict[str, str]:
    unit = jsonld_text(node.get("estleg:minPenaltyUnit")) or jsonld_text(
        node.get("estleg:maxPenaltyUnit")
    )
    return {
        "iri": node_id(node),
        "provision": ref_id(node.get("estleg:applicableProvision")),
        "type": jsonld_text(node.get("estleg:sanctionType")),
        "min": jsonld_scalar(node.get("estleg:minPenaltyAmount")),
        "max": jsonld_scalar(node.get("estleg:maxPenaltyAmount")),
        "unit": unit,
    }


def court_decision_row(node: Mapping[str, Any]) -> dict[str, str]:
    return {
        "iri": node_id(node),
        "caseNumber": jsonld_text(node.get("estleg:caseNumber")),
        "date": jsonld_scalar(node.get("estleg:decisionDate")),
        "ecli": jsonld_text(node.get("estleg:ecliIdentifier")),
        "chamber": jsonld_text(node.get("estleg:chamber")),
    }


def _in_force_from_status(status: str) -> str:
    if status == "inForce":
        return "true"
    if status in {"repealed", "notYetEffective"}:
        return "false"
    return ""


def _lookup_chain(
    node: Mapping[str, Any],
    by_id: Mapping[str, Mapping[str, Any]],
    field: str,
) -> str:
    """Walk ``parentProvision`` until ``field`` (or ``partOfAct``) resolves."""
    seen: set[str] = set()
    current: Mapping[str, Any] | None = dict(node)
    while current is not None:
        iri = node_id(current)
        if iri:
            if iri in seen:
                break
            seen.add(iri)
        if field == "estleg:partOfAct":
            found = ref_id(current.get("estleg:partOfAct"))
        else:
            found = jsonld_text(current.get(field))
        if found:
            return found
        parent = ref_id(current.get("estleg:parentProvision"))
        current = by_id.get(parent) if parent else None
    return ""


def project_graph(
    graph: object,
    *,
    slug: str = "",
    abbreviation: str = "",
) -> Tables:
    """Project a JSON-LD graph into the five star-schema row lists."""
    nodes = iter_nodes(graph)
    by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        iri = node_id(node)
        if iri:
            by_id[iri] = node

    act_status: dict[str, str] = {}
    for node in nodes:
        if is_law_node(node):
            act_status[node_id(node)] = jsonld_text(node.get("estleg:temporalStatus"))

    laws: list[dict[str, str]] = []
    provisions: list[dict[str, str]] = []
    citations: list[dict[str, str]] = []
    sanctions: list[dict[str, str]] = []
    court_decisions: list[dict[str, str]] = []

    for node in nodes:
        if is_law_node(node) and node_id(node):
            laws.append(
                law_row(node, slug=slug, abbreviation=abbreviation)
            )
        if is_provision_node(node) and node_id(node):
            act = _lookup_chain(node, by_id, "estleg:partOfAct")
            paragrahv = _lookup_chain(node, by_id, "estleg:paragrahv")
            own_force = jsonld_scalar(node.get("estleg:inForce"))
            own_status = jsonld_text(node.get("estleg:temporalStatus"))
            in_force = own_force or _in_force_from_status(own_status)
            if not in_force:
                in_force = _in_force_from_status(act_status.get(act, ""))
            provisions.append(
                provision_row(
                    node,
                    act=act,
                    paragrahv=paragrahv,
                    in_force=in_force,
                )
            )
        if is_sanction_node(node) and node_id(node):
            sanctions.append(sanction_row(node))
        if is_court_decision_node(node) and node_id(node):
            court_decisions.append(court_decision_row(node))
        citations.extend(citation_rows(node))

    return _sort_tables(
        {
            "laws": laws,
            "provisions": provisions,
            "citations": citations,
            "sanctions": sanctions,
            "court_decisions": court_decisions,
        }
    )


def _sort_tables(tables: Tables) -> Tables:
    def iri_key(row: Mapping[str, str]) -> str:
        return row.get("iri", "")

    citations = sorted(
        tables.get("citations", []),
        key=lambda row: (row.get("source", ""), row.get("target", ""), row.get("predicate", "")),
    )
    # Drop duplicate citation triples while preserving sort order.
    seen: set[tuple[str, str, str]] = set()
    unique_citations: list[dict[str, str]] = []
    for row in citations:
        key = (row.get("source", ""), row.get("target", ""), row.get("predicate", ""))
        if key in seen:
            continue
        seen.add(key)
        unique_citations.append(row)
    return {
        "laws": sorted(tables.get("laws", []), key=iri_key),
        "provisions": sorted(tables.get("provisions", []), key=iri_key),
        "citations": unique_citations,
        "sanctions": sorted(tables.get("sanctions", []), key=iri_key),
        "court_decisions": sorted(tables.get("court_decisions", []), key=iri_key),
    }


def empty_tables() -> Tables:
    return {name: [] for name in TABLE_COLUMNS}


def merge_tables(chunks: Iterable[Tables]) -> Tables:
    merged = empty_tables()
    laws: dict[str, dict[str, str]] = {}
    provisions: dict[str, dict[str, str]] = {}
    sanctions: dict[str, dict[str, str]] = {}
    courts: dict[str, dict[str, str]] = {}
    citations: dict[tuple[str, str, str], dict[str, str]] = {}
    for chunk in chunks:
        for row in chunk.get("laws", []):
            iri = row.get("iri", "")
            if iri and iri not in laws:
                laws[iri] = row
        for row in chunk.get("provisions", []):
            iri = row.get("iri", "")
            if iri and iri not in provisions:
                provisions[iri] = row
        for row in chunk.get("sanctions", []):
            iri = row.get("iri", "")
            if iri and iri not in sanctions:
                sanctions[iri] = row
        for row in chunk.get("court_decisions", []):
            iri = row.get("iri", "")
            if iri and iri not in courts:
                courts[iri] = row
        for row in chunk.get("citations", []):
            key = (row.get("source", ""), row.get("target", ""), row.get("predicate", ""))
            if key[0] and key[1] and key not in citations:
                citations[key] = row
    merged["laws"] = list(laws.values())
    merged["provisions"] = list(provisions.values())
    merged["sanctions"] = list(sanctions.values())
    merged["court_decisions"] = list(courts.values())
    merged["citations"] = list(citations.values())
    return _sort_tables(merged)


def is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8") as handle:
            return handle.readline().startswith(LFS_POINTER_PREFIX)
    except OSError:
        return False


def load_abbrev_registry(path: Path | None = None) -> dict[str, str]:
    registry_path = path or (REPO_ROOT / "data" / "law_abbreviations.json")
    try:
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for slug, entry in raw.items():
        if isinstance(entry, dict):
            abbrev = entry.get("abbrev")
            if isinstance(abbrev, str) and abbrev:
                out[str(slug)] = abbrev
    return out


def expand_pattern(root: Path, pattern: str) -> list[Path]:
    """Resolve a glob or literal path relative to ``root`` (then cwd)."""
    if not pattern:
        return []
    candidate = Path(pattern)
    if candidate.is_file():
        return [candidate]
    rooted = root / pattern
    if rooted.is_file():
        return [rooted]
    matches = [path for path in root.glob(pattern) if path.is_file()]
    if matches:
        return sorted(matches)
    cwd_matches = [path for path in Path().glob(pattern) if path.is_file()]
    return sorted(cwd_matches)


def resolve_globs(root: Path, patterns: Sequence[str]) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for path in expand_pattern(root, pattern):
            if path.name in SKIP_FILENAMES:
                continue
            if path.suffix.lower() not in {".json", ".jsonld"}:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(path)
    return found


def resolve_input_paths(
    krr_dir: Path,
    *,
    laws_globs: Sequence[str] | None = None,
    sanctions_globs: Sequence[str] | None = None,
    court_globs: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[Path]:
    """Law files first (honouring ``--limit``), then sidecars, then courts."""
    law_paths = resolve_globs(krr_dir, laws_globs or DEFAULT_LAWS_GLOBS)
    if limit is not None:
        law_paths = law_paths[: max(limit, 0)]
    sidecar_paths = resolve_globs(
        krr_dir, sanctions_globs or DEFAULT_SANCTIONS_GLOBS
    )
    court_paths = resolve_globs(krr_dir, court_globs or DEFAULT_COURT_GLOBS)
    ordered: list[Path] = []
    seen: set[Path] = set()
    for path in (*law_paths, *sidecar_paths, *court_paths):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(path)
    return ordered


def _load_jsonld(path: Path) -> dict[str, Any] | list[Any] | None:
    if is_lfs_pointer(path):
        print(f"WARN: skip LFS pointer {path}", file=sys.stderr)
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"WARN: skip {path}: {exc}", file=sys.stderr)
        return None


def project_files(
    paths: Sequence[Path],
    *,
    registry: Mapping[str, str] | None = None,
) -> Tables:
    abbrev_map = dict(registry or {})
    chunks: list[Tables] = []
    for path in paths:
        doc = _load_jsonld(path)
        if doc is None:
            continue
        slug = file_slug(path)
        chunks.append(
            project_graph(
                doc,
                slug=slug,
                abbreviation=abbrev_map.get(slug, ""),
            )
        )
    return merge_tables(chunks) if chunks else empty_tables()


def _write_csv(
    path: Path,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, str]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(columns),
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _write_parquet(
    path: Path,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, str]],
) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    data = {column: [row.get(column, "") for row in rows] for column in columns}
    table = pa.table(data, schema=pa.schema([(column, pa.string()) for column in columns]))
    pq.write_table(table, path)


def write_tables(
    out_dir: Path | str,
    tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    *,
    graph: object | None = None,
    slug: str = "",
    abbreviation: str = "",
    write_parquet: bool | None = None,
) -> dict[str, int]:
    """Write the five star-schema tables as CSV (and Parquet if available).

    ``tables`` is the dict returned by :func:`project_graph`. Alternatively
    pass ``graph=`` a tiny JSON-LD document / ``@graph`` list and the
    projector runs first. Returns ``{table_name: row_count}``.
    """
    if tables is None and graph is None:
        raise TypeError("write_tables() requires tables or graph=")
    projected: Tables
    if graph is not None:
        projected = project_graph(graph, slug=slug, abbreviation=abbreviation)
        if tables is not None:
            overlay = {
                name: [dict(row) for row in tables.get(name, [])]
                for name in TABLE_COLUMNS
            }
            projected = merge_tables([projected, overlay])
    else:
        assert tables is not None
        projected = _sort_tables(
            {
                name: [dict(row) for row in tables.get(name, [])]
                for name in TABLE_COLUMNS
            }
        )

    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)

    parquet_ok = False
    if write_parquet is not False:
        try:
            import pyarrow
            import pyarrow.parquet  # noqa: F401
        except ImportError:
            print("parquet skipped: pyarrow is not installed", file=sys.stderr)
        else:
            parquet_ok = True

    counts: dict[str, int] = {}
    for name, columns in TABLE_COLUMNS.items():
        rows = projected.get(name, [])
        _write_csv(dest / f"{name}.csv", columns, rows)
        if parquet_ok:
            _write_parquet(dest / f"{name}.parquet", columns, rows)
        counts[name] = len(rows)
    return counts


def serialize(
    *,
    krr_dir: Path | None = None,
    out_dir: Path | None = None,
    laws_globs: Sequence[str] | None = None,
    sanctions_globs: Sequence[str] | None = None,
    court_globs: Sequence[str] | None = None,
    limit: int | None = None,
    write_parquet: bool | None = None,
    abbrev_registry: Path | Mapping[str, str] | None = None,
) -> dict[str, int]:
    """Load the selected peeps and write star-schema tables under ``out_dir``."""
    root = Path(krr_dir) if krr_dir is not None else KRR_DIR
    dest = Path(out_dir) if out_dir is not None else (root / "exports")
    if isinstance(abbrev_registry, Mapping):
        registry = dict(abbrev_registry)
    else:
        registry = load_abbrev_registry(
            Path(abbrev_registry) if abbrev_registry is not None else None
        )
    paths = resolve_input_paths(
        root,
        laws_globs=laws_globs,
        sanctions_globs=sanctions_globs,
        court_globs=court_globs,
        limit=limit,
    )
    tables = project_files(paths, registry=registry)
    return write_tables(dest, tables, write_parquet=write_parquet)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=KRR_DIR / "exports",
        help="output directory (default: krr_outputs/exports)",
    )
    parser.add_argument(
        "--krr-dir",
        type=Path,
        default=KRR_DIR,
        help="corpus root used to resolve globs (default: krr_outputs)",
    )
    parser.add_argument(
        "--laws-glob",
        action="append",
        dest="laws_globs",
        metavar="GLOB",
        help=(
            "law peep glob relative to --krr-dir (repeatable). "
            f"Default: {DEFAULT_LAWS_GLOBS[0]}"
        ),
    )
    parser.add_argument(
        "--sanctions-glob",
        action="append",
        dest="sanctions_globs",
        metavar="GLOB",
        help="sanctions sidecar glob relative to --krr-dir (repeatable)",
    )
    parser.add_argument(
        "--court-glob",
        action="append",
        dest="court_globs",
        metavar="GLOB",
        help="court peep glob relative to --krr-dir (repeatable)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="process at most N law peeps (after glob sort)",
    )
    parser.add_argument(
        "--no-parquet",
        action="store_true",
        help="do not attempt a Parquet write even if pyarrow is installed",
    )
    parser.add_argument(
        "--abbrev-registry",
        type=Path,
        default=REPO_ROOT / "data" / "law_abbreviations.json",
        help="law abbreviation registry (slug → abbrev)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    counts = serialize(
        krr_dir=args.krr_dir,
        out_dir=args.out,
        laws_globs=args.laws_globs,
        sanctions_globs=args.sanctions_globs,
        court_globs=args.court_globs,
        limit=args.limit,
        write_parquet=False if args.no_parquet else None,
        abbrev_registry=args.abbrev_registry,
    )
    parts = ", ".join(f"{counts[name]} {name}" for name in TABLE_COLUMNS)
    print(f"tabular export: {parts}")
    print(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
