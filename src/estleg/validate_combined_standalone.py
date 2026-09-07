"""Combined-ONLY SHACL regression gate for the aggregate artifact (#489).

`krr_outputs/combined_ontology.jsonld` is a public, load-everything artifact:
an application may load it ALONE, without the sibling sub-corpora. This gate
validates that file BY ITSELF against the project's SHACL shapes, so a stale or
semantically thin standalone combined cannot pass unnoticed.

It is deliberately distinct from the three other validation paths:

  * `scripts/validate_seadusloome_sync.py` validates the *Seadusloome public
    load surface* — combined PLUS the public sub-directories — where RDF graph
    merge fills a thin stub from its full source node. That gate is correct for
    consumers who follow that load surface, but it cannot catch combined-only
    drift because the sibling files repair it.
  * `scripts/shacl_validate_all.py --bucket <name>` validates a *source
    sub-corpus alone* (INDEX-driven); a finding there is genuine source-data
    loss.
  * THIS gate validates the *combined artifact alone* — a finding here is an
    AGGREGATE-ARTIFACT finding: the fix is to regenerate combined via
    `scripts/fix_all_issues.py` (the `generate_combined_jsonld` builder), NEVER
    to relax/suppress SHACL.

Enum-like controlled-vocabulary classifiers whose property shape is marked
`estleg:graphClosureExempt true` (estleg:caseType / draftType / legislativePhase
/ euDocumentType ...) are SKIPPED in ONE narrow case, exactly as the graph-closure
gate tolerates: a *missing* classifier (sh:minCount) on a closure stub, whose enum
individual lives in `controlled_vocabulary.jsonld` rather than inline, so a
combined-only load does not need it inlined. A *present but malformed* exempt value
(e.g. a caseType that is a literal, not the enum IRI — sh:nodeKind), or a missing
classifier on a non-stub full node, is NOT exempt and is still reported. Every
other violation — the semantic graph edges and metadata the app traverses — is
reported.

Findings are grouped by (focus-node type, missing path, whether the focus node
is an `estleg:isStubNode`), with a few example IDs per group, so the report
points straight at the builder bug.

SHACL min-count shapes alone cannot catch a class that has *vanished* from the
combined artifact: with zero instances present, a `sh:minCount` shape simply has
no focus nodes and reports clean — a false PASS. So on top of the SHACL run this
gate also enforces a per-class minimum INSTANCE-COUNT floor (`EXPECTED_CLASS_FLOORS`)
for the bulk classes that must be merged into the standalone artifact
(`estleg:ProvisionVersion`, `estleg:AmendmentEvent`). The floors are deliberately
loose anti-regression sanity floors — well below the sidecar census, well above a
broken near-zero count — not parity counts; see the constant for the numbers and
their provenance (#568).

Exit codes: 0 = clean, 1 = combined-only violations remain, 2 = load/parse/shapes
failure.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

# scripts/ on the path so the sibling Seadusloome validator's reusable
# machinery (shapes loading, exempt-predicate discovery, shape-name resolution)
# can be imported rather than duplicated.
from estleg import estleg_common
from estleg import validate_seadusloome_sync as seadusloome

REPO = Path(__file__).resolve().parents[2]
DEFAULT_COMBINED = REPO / "krr_outputs" / "combined_ontology.jsonld"
DEFAULT_SHAPES = REPO / "shacl"

NS = estleg_common.NS
SHACL_NS = "http://www.w3.org/ns/shacl#"

# Per-class minimum INSTANCE-COUNT floors for the standalone combined artifact
# (#568). These are bulk classes whose sidecars MUST be merged into combined; a
# SHACL min-count shape cannot detect their *absence* (zero focus nodes => clean),
# so a thin/vanished class would otherwise ship a false PASS. The floors are
# DELIBERATELY LOOSE anti-regression sanity floors, NOT parity counts: each is set
# well below the sidecar census but well above the broken near-zero state, so the
# gate trips hard on "vanished to a handful" without being brittle to normal
# corpus growth/shrinkage.
#
# Only classes that are DELIBERATELY merged into combined get a floor.
# estleg:AmendmentEvent IS merged (the amendments overlay, ~16,050 after the P0
# merge; historically ~1 — floor 1,000 fails the old near-zero state and passes
# once merged). estleg:ProvisionVersion is intentionally NOT floored: the version
# layer is a SEPARATE load surface (hasVersion is stripped from combined, #561),
# so ProvisionVersion is correctly ~0 in combined — a floor there would falsely
# fail the by-design state. The combined-standalone gate validates combined; the
# version layer is validated via validate_seadusloome_sync's full surface.
EXPECTED_CLASS_FLOORS: dict[str, int] = {
    "estleg:AmendmentEvent": 1000,
}


def _compact_path(path_term) -> str:
    """Return the compact ``estleg:foo`` form of a result-path term, else its str."""
    import rdflib

    if not isinstance(path_term, rdflib.URIRef):
        return seadusloome._local_name(path_term)
    return estleg_common.canonical_estleg_ref(str(path_term)) or seadusloome._local_name(
        path_term
    )


def _focus_types(data_graph, focus_node) -> tuple[str, ...]:
    """Sorted compact ``estleg:`` types of a focus node (for grouping)."""
    import rdflib

    types: list[str] = []
    for type_term in data_graph.objects(focus_node, rdflib.RDF.type):
        compact = estleg_common.canonical_estleg_ref(str(type_term))
        if compact is not None:
            types.append(compact)
    return tuple(sorted(set(types)))


def _is_stub(data_graph, focus_node) -> bool:
    """True when the focus node carries ``estleg:isStubNode true``."""
    import rdflib

    marker = rdflib.URIRef(NS + "isStubNode")
    value = data_graph.value(focus_node, marker)
    return bool(value) and str(value).lower() in {"true", "1"}


def _class_floor_failures(data_graph, class_floors: dict[str, int]) -> list[dict]:
    """Return floor failures for classes under-represented in ``data_graph``.

    For each ``estleg:`` class in ``class_floors``, count distinct typed
    subjects in the parsed combined graph (exact, independent of JSON-LD
    compaction) and emit a ``{class, count, floor}`` row for every class whose
    instance count is below its floor. An empty list means every expected class
    clears its sanity floor.
    """
    import rdflib

    failures: list[dict] = []
    for class_curie, floor in sorted(class_floors.items()):
        local = class_curie.split(":", 1)[-1]
        class_iri = rdflib.URIRef(NS + local)
        count = len(set(data_graph.subjects(rdflib.RDF.type, class_iri)))
        if count < floor:
            failures.append({"class": class_curie, "count": count, "floor": floor})
    return failures


def evaluate(
    combined_path: Path,
    shapes_dir: Path = DEFAULT_SHAPES,
    class_floors: dict[str, int] | None = None,
) -> dict:
    """Run combined-only SHACL and return a structured findings summary.

    ``class_floors`` maps an ``estleg:`` class to its minimum instance-count
    floor; it defaults to the module-level :data:`EXPECTED_CLASS_FLOORS`. Pass
    ``{}`` to disable the floor check (tiny synthetic fixtures use this).

    Returns a dict with:
      * ``status``: ``"ok"`` | ``"violations"`` | ``"error"``
      * ``errors``: fatal load/parse/shapes messages (status ``"error"``)
      * ``total``: count of reported (non-exempt) violation results
      * ``groups``: list of ``{focus_type, path, is_stub, count, examples,
        source_shape, severity}`` rows, sorted by descending count
      * ``skipped_exempt``: count of results suppressed because their path is
        a ``graphClosureExempt`` controlled-vocabulary predicate
      * ``class_floor_failures``: list of ``{class, count, floor}`` rows for
        every expected bulk class present below its sanity floor (#568); a
        non-empty list forces a non-``ok`` status

    ``status`` is ``"violations"`` when EITHER ``total`` SHACL violations OR
    ``class_floor_failures`` is non-empty; ``"error"`` stays reserved for
    load/parse/shapes failures, which never compute floors.
    """
    import rdflib

    if class_floors is None:
        class_floors = EXPECTED_CLASS_FLOORS

    if not combined_path.exists():
        return {
            "status": "error",
            "errors": [
                f"{combined_path} not found — regenerate it with "
                f"scripts/fix_all_issues.py before running this gate."
            ],
        }

    # The combined artifact is the ONLY input. No sibling sub-corpora are loaded,
    # so a thin stub cannot be repaired by graph merge — that is the whole point.
    data_graph = rdflib.Graph()
    try:
        data_graph.parse(str(combined_path), format="json-ld")
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "errors": [f"parse failure: {combined_path}: {exc}"]}

    shapes_graph, shapes_failures, shapes_paths = seadusloome.load_shapes(shapes_dir)
    if shapes_failures:
        return {
            "status": "error",
            "errors": [f"shapes load failure: {p}: {e}" for p, e in shapes_failures],
        }
    if not shapes_paths:
        return {"status": "error", "errors": [f"no SHACL shapes under {shapes_dir}"]}

    exempt = seadusloome.graph_closure_exempt_predicates(shapes_dir)

    import pyshacl

    _conforms, results_graph, _text = pyshacl.validate(
        data_graph,
        shacl_graph=shapes_graph,
        inference="none",
        abort_on_first=False,
    )

    SH = rdflib.Namespace(SHACL_NS)
    groups: dict[tuple, dict] = defaultdict(
        lambda: {"count": 0, "examples": [], "source_shape": "", "severity": ""}
    )
    skipped_exempt = 0

    for result in results_graph.subjects(rdflib.RDF.type, SH.ValidationResult):
        path_term = results_graph.value(result, SH.resultPath)
        compact_path = _compact_path(path_term)
        focus_node = results_graph.value(result, SH.focusNode)
        is_stub = _is_stub(data_graph, focus_node)

        # Skip enum-like controlled-vocabulary classifiers, but ONLY the case the
        # graph-closure gate actually tolerates: a *missing* classifier
        # (sh:minCount) on a closure stub, whose enum individual lives in
        # controlled_vocabulary.jsonld rather than inline. A *present but
        # malformed* exempt value (e.g. estleg:caseType as a literal, tripping
        # sh:nodeKind) — or a missing classifier on a non-stub full node — is a
        # genuine combined-only defect and is still reported; otherwise this gate
        # could pass a CourtDecision whose caseType is not even an IRI.
        if compact_path in exempt:
            component_term = results_graph.value(result, SH.sourceConstraintComponent)
            if component_term == SH.MinCountConstraintComponent and is_stub:
                skipped_exempt += 1
                continue

        severity_term = results_graph.value(result, SH.resultSeverity)
        severity = seadusloome._local_name(severity_term) if severity_term else "Violation"
        source_shape = seadusloome._resolve_named_shape(
            shapes_graph, results_graph.value(result, SH.sourceShape)
        )

        focus_types = _focus_types(data_graph, focus_node)
        key = (focus_types, compact_path, is_stub, severity)
        bucket = groups[key]
        bucket["count"] += 1
        bucket["source_shape"] = seadusloome._local_name(source_shape)
        bucket["severity"] = severity
        if len(bucket["examples"]) < 5 and focus_node is not None:
            focus_str = str(focus_node)
            if focus_str not in bucket["examples"]:
                bucket["examples"].append(focus_str)

    rows = []
    for (focus_types, path, is_stub, severity), info in groups.items():
        rows.append(
            {
                "focus_type": ", ".join(focus_types) if focus_types else "(untyped)",
                "path": path,
                "is_stub": is_stub,
                "severity": severity,
                "source_shape": info["source_shape"],
                "count": info["count"],
                "examples": info["examples"],
            }
        )
    rows.sort(key=lambda r: (-r["count"], r["focus_type"], r["path"]))

    total = sum(r["count"] for r in rows)

    # Sanity-floor check (#568): a vanished/thin bulk class produces zero SHACL
    # focus nodes and would otherwise pass clean. Computed ONLY here on the
    # success path, where data_graph is fully parsed — never in the error paths.
    class_floor_failures = _class_floor_failures(data_graph, class_floors)

    return {
        # Any SHACL violation OR any class below its floor is a failing combined
        # artifact; "error" is reserved for load/parse/shapes failures above.
        "status": "violations" if (total or class_floor_failures) else "ok",
        "errors": [],
        "total": total,
        "groups": rows,
        "skipped_exempt": skipped_exempt,
        "class_floor_failures": class_floor_failures,
    }


def format_report(summary: dict) -> list[str]:
    """Render the structured summary as printable lines."""
    lines: list[str] = []
    if summary["status"] == "error":
        lines.append("COMBINED-ONLY GATE: load/shapes ERROR")
        lines.extend(f"  {e}" for e in summary["errors"])
        return lines

    skipped = summary.get("skipped_exempt", 0)
    if summary["status"] == "ok":
        lines.append(
            "COMBINED-ONLY SHACL gate: PASS — combined_ontology.jsonld is "
            "self-contained and SHACL-conforming on its own"
        )
        if skipped:
            lines.append(
                f"  ({skipped} missing graphClosureExempt classifier(s) on closure "
                f"stubs skipped — controlled vocabulary, resolved via the external "
                f"vocab file, not a combined-only graph edge)"
            )
        return lines

    # Floor failures are the headline finding: a bulk class is missing from the
    # standalone artifact, which SHACL min-count alone cannot surface (#568).
    floor_failures = summary.get("class_floor_failures", [])
    for failure in floor_failures:
        lines.append(
            f"AGGREGATE-ARTIFACT floor check: {failure['class']} present "
            f"{failure['count']}x but expected >= {failure['floor']} — the combined "
            f"artifact is missing this class (merge the sidecar via "
            f"scripts/fix_all_issues.py)."
        )

    # SHACL violations, if any, are reported alongside the floor findings.
    if summary["total"]:
        lines.append(
            f"AGGREGATE-ARTIFACT (combined-only) SHACL findings: {summary['total']} "
            f"violation(s) in krr_outputs/combined_ontology.jsonld."
        )
        lines.append(
            "  These are findings against the STANDALONE combined artifact (no "
            "sibling sub-corpora loaded). The fix is to regenerate combined via "
            "scripts/fix_all_issues.py (generate_combined_jsonld) — do NOT relax or "
            "suppress SHACL."
        )
        if skipped:
            lines.append(
                f"  ({skipped} missing graphClosureExempt classifier(s) on closure "
                f"stubs skipped as controlled-vocabulary links, not graph edges.)"
            )
        lines.append("  Grouped (count x focus_type path=<path> stub=<bool>):")
        for row in summary["groups"]:
            examples = (
                ", ".join(row["examples"]) if row["examples"] else "(no focus nodes)"
            )
            lines.append(
                f"    {row['count']}x [{row['focus_type']}] path={row['path']} "
                f"stub={row['is_stub']} severity={row['severity']} "
                f"shape={row['source_shape']} e.g. {examples}"
            )
    return lines


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--combined",
        type=Path,
        default=DEFAULT_COMBINED,
        help=f"Combined artifact to validate alone (default: {DEFAULT_COMBINED}).",
    )
    parser.add_argument(
        "--shapes-dir",
        type=Path,
        default=DEFAULT_SHAPES,
        help=f"SHACL shapes directory (default: {DEFAULT_SHAPES}).",
    )
    parser.add_argument(
        "--no-floor",
        action="store_true",
        help=(
            "Disable the per-class minimum instance-count sanity floors "
            f"({', '.join(EXPECTED_CLASS_FLOORS)}); SHACL-only run. Intended for "
            "validating partial/sub-set artifacts, not the full combined gate."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(
        f"Validating {args.combined.name} ALONE (combined-only load surface) "
        f"against {args.shapes_dir}/ ..."
    )
    class_floors = {} if args.no_floor else EXPECTED_CLASS_FLOORS
    summary = evaluate(args.combined, args.shapes_dir, class_floors=class_floors)
    for line in format_report(summary):
        print(line)
    if summary["status"] == "error":
        return 2
    return 1 if summary["status"] == "violations" else 0


if __name__ == "__main__":
    sys.exit(main())
