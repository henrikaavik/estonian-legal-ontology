"""Seadusloome-compatible zero-warning SHACL validator.

Mirrors the load path used by the Seadusloome sync converter
(`app/sync/converter.py` in the Seadusloome project) so the warning count
this script reports matches what consumers see when they ingest the
ontology. Specifically:

1. Prioritize ``krr_outputs/combined_ontology.jsonld`` if present.
2. Append every public JSON-LD data file under the documented section 2
   load-surface directories: ``eelnoud``, ``riigikohus``, ``curia``,
   ``eurlex``, ``concepts``, ``sanctions``, ``amendments``,
   ``institutions``, ``provision_versions``, ``annotations``,
   ``harmonisation``, and ``regulations``.
3. Parse all inputs into a single ``rdflib.Graph()`` via the JSON-LD
   parser.
4. Load every ``*.ttl`` and ``*.jsonld`` under ``shacl/`` into a
   separate shapes graph.
5. Run ``pyshacl.validate(..., inference='none', abort_on_first=False)``
   with ``inference='none'`` to mirror Seadusloome 1:1 — the
   ``shacl_validate_all.py`` bucket validator uses ``inference='rdfs'``
   instead, which yields different warning counts and is not what the
   downstream consumer applies.

The script groups validation results by source NodeShape, result path,
and severity, then prints a compact actionable summary plus exit codes
suitable for CI gating (0 = clean, 1 = warnings exceed threshold,
2 = parse or shapes load failure).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from estleg_common import (
    NS,
    PUBLIC_LOAD_SUBDIRS,
    iter_public_load_files,
)

REPO = Path(__file__).resolve().parents[1]
DEFAULT_KRR = REPO / "krr_outputs"
DEFAULT_SHAPES = REPO / "shacl"

# Sub-directories Seadusloome's converter pulls in addition to the
# combined artifact at the krr root. Order is intentional and matches
# the upstream loader.
SEADUSLOOME_SUBDIRS: tuple[str, ...] = PUBLIC_LOAD_SUBDIRS

# JSON-LD inputs the Seadusloome loader recognises (matches both raw
# .json and .jsonld extensions in the listed sub-directories).
JSONLD_SUFFIXES: tuple[str, ...] = (".json", ".jsonld")

# Shapes graph file extensions.
SHAPES_SUFFIXES: tuple[str, ...] = (".ttl", ".jsonld")

# These properties are encoded as compact IRI-like controlled values, but the
# values are not independently loadable graph nodes today. They are enum-ish
# classifications, not followable enrichment links, so they are outside the
# public graph-closure contract.
GRAPH_CLOSURE_EXEMPT_PREDICATES: frozenset[str] = frozenset(
    {"estleg:caseType", "estleg:decisionType"}
)


def collect_inputs(krr_dir: Path) -> tuple[list[Path], list[str], list[str]]:
    """Build the sorted, de-duplicated list of JSON-LD inputs.

    Returns ``(inputs, warnings, errors)``. ``errors`` is fatal — a
    missing ``combined_ontology.jsonld`` belongs here because it is the
    only delivery path for the root ``*_peep.json`` corpus, so a partial
    run would silently drop hundreds of enacted-law files.
    """
    warnings: list[str] = []
    errors: list[str] = []
    combined = krr_dir / "combined_ontology.jsonld"
    if not combined.exists():
        errors.append(
            f"ERROR: {combined} not found — refusing to run a partial gate that "
            f"would skip every root *_peep.json file. Regenerate the combined "
            f"artifact (scripts/fix_all_issues.py) before re-running."
        )

    for subdir_name in SEADUSLOOME_SUBDIRS:
        subdir = krr_dir / subdir_name
        if not subdir.is_dir():
            warnings.append(f"WARNING: missing input subdirectory {subdir}")

    return iter_public_load_files(krr_dir), warnings, errors


def _canonical_estleg_id(value: str) -> str | None:
    """Return compact ``estleg:`` form for internal IDs, else ``None``."""
    if value.startswith("estleg:"):
        return value
    if value.startswith(NS):
        return "estleg:" + value[len(NS):]
    return None


def _iter_graph_nodes(doc: object) -> Iterable[dict]:
    if not isinstance(doc, dict):
        return
    graph = doc.get("@graph")
    if isinstance(graph, list):
        for node in graph:
            if isinstance(node, dict):
                yield node


def _walk_jsonld_refs(value: object, predicate: str) -> Iterable[tuple[str, str]]:
    """Yield ``(predicate, ref)`` pairs for JSON-LD object references."""
    if isinstance(value, dict):
        ref = value.get("@id")
        if isinstance(ref, str):
            yield predicate, ref
        for key, child in value.items():
            if key in {"@context", "@id"}:
                continue
            yield from _walk_jsonld_refs(child, key)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_jsonld_refs(item, predicate)


def validate_graph_closure(paths: Iterable[Path]) -> dict:
    """Validate internal ``estleg:`` object-reference closure.

    Returns a summary dict with ``total``, ``by_predicate``, ``samples``, and
    ``load_errors``. The check is intentionally JSON-LD-shape based rather than
    rdflib-based so it can report the source predicate as it appears in the
    corpus files.
    """
    docs: list[tuple[Path, object]] = []
    load_errors: list[tuple[str, str]] = []
    defined_ids: set[str] = set()

    for path in paths:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            load_errors.append((str(path), str(exc)))
            continue
        docs.append((path, doc))
        for node in _iter_graph_nodes(doc):
            node_id = node.get("@id")
            if isinstance(node_id, str):
                canonical = _canonical_estleg_id(node_id)
                if canonical:
                    defined_ids.add(canonical)

    by_predicate: Counter[str] = Counter()
    samples: dict[str, list[dict[str, str]]] = defaultdict(list)

    for path, doc in docs:
        for node in _iter_graph_nodes(doc):
            source = str(node.get("@id", "(anonymous)"))
            for key, value in node.items():
                if key in {"@context", "@id"}:
                    continue
                for predicate, ref in _walk_jsonld_refs(value, key):
                    if predicate in GRAPH_CLOSURE_EXEMPT_PREDICATES:
                        continue
                    canonical = _canonical_estleg_id(ref)
                    if canonical is None or canonical in defined_ids:
                        continue
                    by_predicate[predicate] += 1
                    if len(samples[predicate]) < 3:
                        samples[predicate].append(
                            {
                                "file": str(path),
                                "source": source,
                                "target": ref,
                            }
                        )

    return {
        "total": sum(by_predicate.values()),
        "by_predicate": dict(by_predicate),
        "samples": dict(samples),
        "load_errors": load_errors,
    }


def _format_closure_summary(summary: dict) -> list[str]:
    lines: list[str] = []
    load_errors = summary.get("load_errors", [])
    if load_errors:
        lines.append(f"GRAPH CLOSURE LOAD FAILURES: {len(load_errors)}")
        for path, exc in load_errors[:10]:
            lines.append(f"  {path}: {exc}")
        return lines

    total = summary.get("total", 0)
    if total == 0:
        lines.append("Graph closure: PASS")
        return lines

    lines.append(f"GRAPH CLOSURE FAILURES: {total} unresolved internal estleg:* refs")
    for predicate, count in sorted(
        summary.get("by_predicate", {}).items(),
        key=lambda item: (-item[1], item[0]),
    ):
        sample_text = "; ".join(
            f"{sample['target']} from {Path(sample['file']).name}"
            for sample in summary.get("samples", {}).get(predicate, [])
        )
        lines.append(f"  {predicate}: {count}" + (f" e.g. {sample_text}" if sample_text else ""))
    return lines


def collect_shapes(shapes_dir: Path) -> list[Path]:
    """Sorted list of every shapes file under the shapes directory."""
    out: set[Path] = set()
    for suffix in SHAPES_SUFFIXES:
        for path in shapes_dir.glob(f"*{suffix}"):
            if path.is_file():
                out.add(path)
    return sorted(out)


def load_graph(paths: Iterable[Path]):
    """Load each path as JSON-LD into a single rdflib graph.

    Returns ``(graph, parse_failures)`` where ``parse_failures`` is a
    list of ``(path, exception_message)`` tuples.
    """
    import rdflib

    graph = rdflib.Graph()
    parse_failures: list[tuple[str, str]] = []
    for path in paths:
        try:
            graph.parse(str(path), format="json-ld")
        except Exception as exc:  # noqa: BLE001 — capture and report
            parse_failures.append((str(path), str(exc)))
    return graph, parse_failures


def load_shapes(shapes_dir: Path):
    """Load every TTL and JSON-LD file under shapes_dir.

    Returns ``(graph, load_failures, paths)``.
    """
    import rdflib

    paths = collect_shapes(shapes_dir)
    graph = rdflib.Graph()
    failures: list[tuple[str, str]] = []
    for path in paths:
        suffix = path.suffix.lower()
        fmt = "turtle" if suffix == ".ttl" else "json-ld"
        try:
            graph.parse(str(path), format=fmt)
        except Exception as exc:  # noqa: BLE001
            failures.append((str(path), str(exc)))
    return graph, failures, paths


def _local_name(term) -> str:
    """Compact display label for an rdflib term.

    Uses the IRI fragment after ``#`` or last path segment for IRIs;
    returns ``<bnode>`` for anonymous shapes; otherwise the literal.
    """
    import rdflib

    if term is None:
        return "(none)"
    if isinstance(term, rdflib.BNode):
        return "<bnode>"
    if isinstance(term, rdflib.URIRef):
        s = str(term)
        for sep in ("#", "/"):
            if sep in s:
                tail = s.rsplit(sep, 1)[-1]
                if tail:
                    return tail
        return s
    return str(term)


def _resolve_named_shape(shapes_graph, source_shape):
    """Return the named NodeShape that owns ``source_shape``.

    Property-shape blank nodes are linked from a parent NodeShape via
    ``sh:property``. Walk up to the first named ancestor; if the source
    shape is already a named IRI, return it as-is. If no named ancestor
    is reachable, return the original term so the caller can still group
    on the bnode identity.
    """
    import rdflib

    SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    if source_shape is None:
        return None
    if isinstance(source_shape, rdflib.URIRef):
        return source_shape
    seen: set = set()
    current = source_shape
    while isinstance(current, rdflib.BNode) and current not in seen:
        seen.add(current)
        parent = next(shapes_graph.subjects(SH.property, current), None)
        if parent is None:
            break
        current = parent
    return current


def summarize_results(results_graph, shapes_graph) -> dict:
    """Group SHACL validation results into actionable rows.

    Returns a dict with:
      - ``total``: total ``sh:ValidationResult`` count
      - ``by_severity``: ``{severity_localname: count}``
      - ``groups``: list of dicts describing each group, sorted by
        descending count then group key, with up to 3 sample focus
        nodes per group.
    """
    import rdflib

    SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")

    by_group: dict[tuple[str, str, str], dict] = defaultdict(
        lambda: {"count": 0, "focus_nodes": [], "messages": set()}
    )
    by_severity: dict[str, int] = defaultdict(int)
    total = 0

    for result in results_graph.subjects(rdflib.RDF.type, SH.ValidationResult):
        total += 1
        severity_term = results_graph.value(result, SH.resultSeverity)
        severity = _local_name(severity_term) if severity_term else "Violation"
        by_severity[severity] += 1

        source_shape = results_graph.value(result, SH.sourceShape)
        named_shape = _resolve_named_shape(shapes_graph, source_shape)
        result_path = results_graph.value(result, SH.resultPath)
        focus_node = results_graph.value(result, SH.focusNode)
        message = results_graph.value(result, SH.resultMessage)

        key = (
            _local_name(named_shape),
            _local_name(result_path),
            severity,
        )
        bucket = by_group[key]
        bucket["count"] += 1
        if len(bucket["focus_nodes"]) < 3 and focus_node is not None:
            focus_str = str(focus_node)
            if focus_str not in bucket["focus_nodes"]:
                bucket["focus_nodes"].append(focus_str)
        if message is not None:
            bucket["messages"].add(str(message))

    groups = []
    for (shape, path, severity), info in by_group.items():
        groups.append(
            {
                "source_shape": shape,
                "result_path": path,
                "severity": severity,
                "count": info["count"],
                "focus_nodes": list(info["focus_nodes"]),
                "messages": sorted(info["messages"]),
            }
        )
    groups.sort(key=lambda g: (-g["count"], g["source_shape"], g["result_path"]))

    return {
        "total": total,
        "by_severity": dict(by_severity),
        "groups": groups,
    }


def _format_summary(summary: dict) -> list[str]:
    """Render the grouped summary as printable lines."""
    lines: list[str] = []
    by_sev = summary["by_severity"]
    sev_parts = []
    for sev in ("Violation", "Warning", "Info"):
        if sev in by_sev:
            sev_parts.append(f"{sev}={by_sev[sev]}")
    for sev, count in sorted(by_sev.items()):
        if sev not in {"Violation", "Warning", "Info"}:
            sev_parts.append(f"{sev}={count}")
    lines.append(
        "Severity totals: " + (", ".join(sev_parts) if sev_parts else "(none)")
    )

    if not summary["groups"]:
        lines.append("No grouped results.")
        return lines

    lines.append("Grouped results (count x source_shape path=<path> severity=<sev>):")
    for group in summary["groups"]:
        sample = ", ".join(group["focus_nodes"]) if group["focus_nodes"] else "(no focus nodes)"
        lines.append(
            f"  {group['count']}x {group['source_shape']} "
            f"path={group['result_path']} severity={group['severity']} "
            f"e.g. {sample}"
        )
    return lines


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--krr-dir",
        type=Path,
        default=DEFAULT_KRR,
        help=f"KRR outputs directory (default: {DEFAULT_KRR}).",
    )
    parser.add_argument(
        "--shapes-dir",
        type=Path,
        default=DEFAULT_SHAPES,
        help=f"SHACL shapes directory (default: {DEFAULT_SHAPES}).",
    )
    parser.add_argument(
        "--max-warnings",
        type=int,
        default=0,
        help=(
            "Maximum number of Violation+Warning results allowed before "
            "exiting non-zero. Default 0 (zero-warning gate)."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional path to write the SHACL results graph as Turtle.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    inputs, input_warnings, input_errors = collect_inputs(args.krr_dir)
    for warning in input_warnings:
        print(warning)
    for err in input_errors:
        print(err)
    if input_errors:
        return 2

    if not inputs:
        print(f"ERROR: no JSON-LD inputs discovered under {args.krr_dir}.")
        return 2

    closure_summary = validate_graph_closure(inputs)
    for line in _format_closure_summary(closure_summary):
        print(line)
    if closure_summary.get("load_errors"):
        return 2
    if closure_summary.get("total", 0):
        return 1

    print(f"Loading {len(inputs)} JSON-LD inputs into a single graph...")
    data_graph, parse_failures = load_graph(inputs)
    if parse_failures:
        print(f"PARSE FAILURES: {len(parse_failures)}")
        for path, exc in parse_failures[:10]:
            print(f"  {path}: {exc}")
        return 2

    shapes_graph, shapes_failures, shapes_paths = load_shapes(args.shapes_dir)
    if shapes_failures:
        print(f"SHAPES LOAD FAILURES: {len(shapes_failures)}")
        for path, exc in shapes_failures[:10]:
            print(f"  {path}: {exc}")
        return 2
    if not shapes_paths:
        print(f"ERROR: no SHACL shapes files discovered under {args.shapes_dir}.")
        return 2

    print(
        f"Loaded {len(data_graph)} data triples; "
        f"{len(shapes_graph)} shape triples from {len(shapes_paths)} shapes file(s)."
    )

    import pyshacl

    conforms, results_graph, _report_text = pyshacl.validate(
        data_graph,
        shacl_graph=shapes_graph,
        inference="none",
        abort_on_first=False,
    )

    print("SHACL conformity:", "PASS" if conforms else "FAIL")

    summary = summarize_results(results_graph, shapes_graph)
    for line in _format_summary(summary):
        print(line)

    if args.report:
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            results_graph.serialize(destination=str(args.report), format="turtle")
            print(f"Wrote SHACL results graph to {args.report}")
        except Exception as exc:  # noqa: BLE001 — non-fatal
            print(f"WARNING: could not write report to {args.report}: {exc}")

    actionable = summary["by_severity"].get("Violation", 0) + summary["by_severity"].get(
        "Warning", 0
    )

    if actionable == 0 and conforms:
        return 0

    if actionable > args.max_warnings:
        if args.max_warnings > 0:
            print(
                f"DIAGNOSTIC: {args.max_warnings} warnings allowed, found {actionable}"
            )
        else:
            print(
                f"DIAGNOSTIC: zero-warning gate failed — found {actionable} "
                f"Violation/Warning result(s)."
            )
        return 1

    if args.max_warnings > 0:
        print(
            f"DIAGNOSTIC: {args.max_warnings} warnings allowed, found {actionable}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
