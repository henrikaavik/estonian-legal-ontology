#!/usr/bin/env python3
"""Fitness-for-purpose evaluation harness (#617).

"Done" in this repo has been *green CI*, not *fit for purpose*: the heuristic
layers are openly approximate (cross-references "resolve ~50%", EuroVoc "a
heuristic layer") yet nothing measured whether a real legal question returns a
complete, correct answer. This harness gives "useful" a number.

It computes **objective, reproducible** metrics over the indexed root-law corpus
— no hand-adjudication, so the numbers can't drift on opinion:

1. **Per-vertical coverage** — what fraction of laws / provisions actually carry
   each layer (targetGroup, normativeType, competentAuthority, interpretedBy,
   sanctions, EU-directive links, point-in-time).
2. **Vertical co-occurrence** — for how many laws do the verticals *co-occur* on
   one act, i.e. is there a law for which "what does it require, who enforces
   it, what are the sanctions, how have courts read it, what does it transpose"
   all answer? Reports the most-complete laws (the proof-of-purpose candidates).
3. **Cross-reference resolution** — what fraction of citation edges resolve to a
   node actually defined in the corpus (the "~50% resolve" claim, measured).
4. **Inline completeness** — are sanctions defined inline on the law, or only
   referenced into an unmerged sidecar.

Optionally, given a hand-verified gold set (``--gold-set``), it also reports
precision/recall for a layer — but that path is **non-blocking** and the gold
sets ship empty/templated (a measured accuracy baseline is accepted separately;
see ``eval/README.md``). This script never fails on a low number — it is a
report, not a gate.

Usage::

    python3 scripts/eval_harness.py                      # refresh eval/fitness_report.*
    python3 scripts/eval_harness.py --gold-set eval/gold_sets/targetGroup.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import estleg_common  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
KRR_DIR = REPO_ROOT / "krr_outputs"
EVAL_DIR = REPO_ROOT / "eval"

# Object-property predicates that are cross-reference edges (their targets should
# resolve to a real provision/act node).
CROSSREF_PREDICATES = {"estleg:references", "estleg:citationTarget", "estleg:cites"}

# The verticals whose co-occurrence on one act defines a "complete" law.
VERTICALS = ("sanctions", "competentAuthority", "interpretedBy", "euDirective", "pointInTime")


def _is_provision(node: dict) -> bool:
    types = node.get("@type", [])
    if isinstance(types, str):
        types = [types]
    return any("LegalProvision" in t for t in types)


def _is_act(node: dict) -> bool:
    types = node.get("@type", [])
    if isinstance(types, str):
        types = [types]
    return "estleg:Act" in types


def _is_sanction(node: dict) -> bool:
    types = node.get("@type", [])
    if isinstance(types, str):
        types = [types]
    return any(t == "estleg:Sanction" or t.endswith(":Sanction") for t in types)


def _has(node: dict, prop: str) -> bool:
    return bool(node.get(prop))


def evaluate(krr_dir: Path = KRR_DIR) -> dict:
    """Compute the fitness report from the indexed root-law corpus."""
    index = json.loads((krr_dir / "INDEX.json").read_text(encoding="utf-8"))
    laws = index["laws"]

    version_sidecars = {
        p.stem for p in (krr_dir / "provision_versions").glob("*.jsonld")
    }

    known_ids: set[str] = set()
    crossref_targets: list[str] = []
    provision_total = 0
    provision_with = Counter()
    temporal_known = 0
    sanction_defs = 0
    sanction_edges = 0
    law_verticals: dict[str, set[str]] = {}
    laws_loaded = 0

    for law in laws:
        name = law["name"]
        present: set[str] = set()
        if name in version_sidecars:
            present.add("pointInTime")
        for fname in law["files"]:
            fpath = krr_dir / fname
            if not fpath.exists():
                continue
            try:
                doc = json.loads(fpath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            laws_loaded += 1
            for node in doc.get("@graph", []):
                if not isinstance(node, dict):
                    continue
                nid = node.get("@id")
                if isinstance(nid, str):
                    known_ids.add(estleg_common.canonical_estleg_ref(nid) or nid)

                if _is_sanction(node):
                    sanction_defs += 1
                # collect cross-ref targets + vertical signals
                for predicate, target in estleg_common.iter_node_estleg_refs(node):
                    if predicate in CROSSREF_PREDICATES:
                        crossref_targets.append(target)
                    if predicate == "estleg:hasSanction":
                        sanction_edges += 1
                        present.add("sanctions")
                    elif predicate == "estleg:competentAuthority":
                        present.add("competentAuthority")
                    elif predicate == "estleg:interpretedBy":
                        present.add("interpretedBy")
                    elif predicate in ("estleg:transposesDirective", "estleg:harmonisedWith"):
                        present.add("euDirective")

                if _is_provision(node):
                    provision_total += 1
                    for prop in (
                        "estleg:targetGroup",
                        "estleg:normativeType",
                        "estleg:competentAuthority",
                        "estleg:temporalStatus",
                    ):
                        if _has(node, prop):
                            provision_with[prop] += 1
                    status = node.get("estleg:temporalStatus")
                    if isinstance(status, str) and status not in ("", "unknown"):
                        temporal_known += 1
        law_verticals[name] = present

    # cross-reference resolution against the loaded corpus
    resolved = sum(
        1
        for t in crossref_targets
        if (estleg_common.canonical_estleg_ref(t) or t) in known_ids
    )
    crossref_total = len(crossref_targets)

    # vertical co-occurrence
    cooccurrence = Counter(len(v) for v in law_verticals.values())
    complete = sorted(
        (name for name, v in law_verticals.items() if len(v) == len(VERTICALS))
    )
    ranked = sorted(law_verticals.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    most_complete = [
        {"law": name, "verticals": sorted(v), "score": len(v)}
        for name, v in ranked[:10]
    ]

    def pct(n: int, d: int) -> float:
        return round(100.0 * n / d, 1) if d else 0.0

    return {
        "generated": f"{estleg_common.BUILD_EVALUATION_DATE}T00:00:00+00:00",
        "ontology_version": estleg_common.ONTOLOGY_VERSION,
        "population": {
            "indexed_laws": len(laws),
            "law_files_loaded": laws_loaded,
            "provisions": provision_total,
        },
        "vertical_coverage_laws": {
            v: {
                "laws_with": sum(1 for s in law_verticals.values() if v in s),
                "pct": pct(sum(1 for s in law_verticals.values() if v in s), len(laws)),
            }
            for v in VERTICALS
        },
        "provision_coverage": {
            prop: {"count": provision_with[prop], "pct": pct(provision_with[prop], provision_total)}
            for prop in (
                "estleg:targetGroup",
                "estleg:normativeType",
                "estleg:competentAuthority",
                "estleg:temporalStatus",
            )
        },
        "point_in_time": {
            "provisions_with_known_temporalStatus": temporal_known,
            "pct": pct(temporal_known, provision_total),
            "laws_with_version_sidecar": len(
                [n for n in law_verticals if n in version_sidecars]
            ),
        },
        "crossref_edge_resolution": {
            "note": (
                "Of citation EDGES that exist, the fraction resolving to a node "
                "defined in the indexed corpus (validates the #561/#630 closure "
                "work). This is edge precision, NOT extraction recall — citations "
                "present in text that never became an edge are not counted here."
            ),
            "edges": crossref_total,
            "resolved_in_corpus": resolved,
            "pct": pct(resolved, crossref_total),
        },
        "inline_sanctions": {
            "sanction_node_definitions_in_peeps": sanction_defs,
            "hasSanction_edges": sanction_edges,
            "inline": sanction_defs > 0,
        },
        "vertical_cooccurrence": {
            "note": (
                "'verticals present' counts an edge being present (referenced), "
                "not the answer being inline-retrievable — see retrievability_gap."
            ),
            "histogram": {str(k): cooccurrence.get(k, 0) for k in range(len(VERTICALS) + 1)},
            "laws_with_all_verticals_referenced": len(complete),
            "most_complete_laws": most_complete,
        },
        "retrievability_gap": {
            "note": (
                "The #617 finding, quantified: even where all verticals are present "
                "as EDGES, the answer is not retrievable from one law because the "
                "data is not inline. Sanctions resolve to an unmerged sidecar (0 "
                "inline definitions) and temporalStatus is unknown on ~all "
                "provisions, so 'what does this law require, who enforces it, what "
                "are the sanctions as of today' cannot be answered from the law node."
            ),
            "laws_all_verticals_referenced": len(complete),
            "laws_with_sanctions_defined_inline": 0 if sanction_defs == 0 else None,
            "provisions_with_known_temporalStatus_pct": pct(temporal_known, provision_total),
            "fully_inline_retrievable_laws": (
                len(complete) if (sanction_defs > 0 and temporal_known > 0) else 0
            ),
        },
    }


def render_markdown(report: dict) -> str:
    pop = report["population"]
    lines = [
        "# Fitness-for-purpose evaluation",
        "",
        f"_Generated (pinned): {report['generated']} · ontology {report['ontology_version']}_",
        "",
        "Objective, reproducible metrics over the indexed root-law corpus — no "
        "hand-adjudication. Regenerate with `python3 scripts/eval_harness.py`.",
        "",
        f"**Population:** {pop['indexed_laws']} indexed laws "
        f"({pop['law_files_loaded']} files), {pop['provisions']:,} provisions.",
        "",
        "## Vertical coverage (per law)",
        "",
        "| Vertical | Laws with | % |",
        "| --- | --: | --: |",
    ]
    for v, d in report["vertical_coverage_laws"].items():
        lines.append(f"| {v} | {d['laws_with']} | {d['pct']}% |")
    cooc = report["vertical_cooccurrence"]
    gap = report["retrievability_gap"]
    lines += [
        "",
        "## Vertical co-occurrence (the 'complete vertical' question)",
        "",
        f"Laws carrying **all {len(VERTICALS)} verticals as edges**: "
        f"**{cooc['laws_with_all_verticals_referenced']}**.",
        "",
        "| Verticals present (referenced) | # laws |",
        "| --: | --: |",
    ]
    for k, n in cooc["histogram"].items():
        lines.append(f"| {k} | {n} |")
    lines += ["", "Most-complete laws (proof-of-purpose candidates to materialise inline):", ""]
    for m in cooc["most_complete_laws"]:
        lines.append(f"- `{m['law']}` — {m['score']}/{len(VERTICALS)}: {', '.join(m['verticals'])}")
    lines += [
        "",
        "### Retrievability gap (the #617 finding, quantified)",
        "",
        f"All verticals are present as edges on {gap['laws_all_verticals_referenced']} laws, but the "
        f"answer is **not inline-retrievable** on **{gap['fully_inline_retrievable_laws']}** of them: "
        f"sanctions resolve to an unmerged sidecar ({report['inline_sanctions']['sanction_node_definitions_in_peeps']} "
        f"inline definitions) and only {gap['provisions_with_known_temporalStatus_pct']}% of provisions "
        "carry a known `temporalStatus`. So no single law yet answers "
        "\"what does it require, who enforces it, what are the sanctions as of today\" from its own nodes.",
    ]
    cr = report["crossref_edge_resolution"]
    pit = report["point_in_time"]
    inl = report["inline_sanctions"]
    lines += [
        "",
        "## Other fitness metrics",
        "",
        f"- **Cross-reference edge resolution:** {cr['resolved_in_corpus']:,} / {cr['edges']:,} "
        f"({cr['pct']}%) existing citation edges resolve to an in-corpus node (edge precision, "
        "not extraction recall).",
        f"- **Point-in-time:** {pit['pct']}% of provisions carry a known `temporalStatus`; "
        f"{pit['laws_with_version_sidecar']} laws have a version sidecar.",
        f"- **Inline sanctions:** {inl['sanction_node_definitions_in_peeps']} sanction "
        f"definitions inline in peeps vs {inl['hasSanction_edges']} `hasSanction` edges "
        f"(inline: {inl['inline']}).",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fitness-for-purpose evaluation harness (#617)")
    parser.add_argument(
        "--output-dir", type=Path, default=EVAL_DIR,
        help="Where to write fitness_report.json / FITNESS_REPORT.md (default: eval/)",
    )
    parser.add_argument(
        "--gold-set", type=Path, default=None,
        help="Optional gold-set JSON to additionally compute precision/recall for a layer.",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    report = evaluate()

    if args.gold_set is not None:
        report["accuracy"] = evaluate_gold_set(args.gold_set, KRR_DIR)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "fitness_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.output_dir / "FITNESS_REPORT.md").write_text(render_markdown(report), encoding="utf-8")

    if not args.quiet:
        cooc = report["vertical_cooccurrence"]
        print(f"Fitness report → {args.output_dir}/fitness_report.json")
        print(f"  provisions: {report['population']['provisions']:,}")
        print(
            f"  laws with all {len(VERTICALS)} verticals (referenced): "
            f"{cooc['laws_with_all_verticals_referenced']}"
        )
        print(f"  cross-ref edge resolution: {report['crossref_edge_resolution']['pct']}%")
        print(f"  point-in-time provisions: {report['point_in_time']['pct']}%")
    return 0


def evaluate_gold_set(path: Path, krr_dir: Path = KRR_DIR) -> dict:
    """Precision/recall for one layer against a hand-verified gold set (#617).

    Non-blocking accuracy path. The gold set is a JSON ``{"layer": ..., "property":
    "estleg:...", "items": [{"node": "<@id>", "gold": <value>}, ...]}``; we look up
    each node's predicted value in the corpus and score exact-match. Ships empty
    until a baseline is accepted (see eval/README.md) — returns zeros for an empty
    set rather than failing.
    """
    gold = json.loads(path.read_text(encoding="utf-8"))
    prop = gold["property"]
    items = gold.get("items", [])
    predictions: dict[str, object] = {}
    wanted = {it["node"] for it in items}
    if wanted:
        for fpath in krr_dir.glob("*_peep.json"):
            try:
                doc = json.loads(fpath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for node in doc.get("@graph", []):
                nid = node.get("@id") if isinstance(node, dict) else None
                if nid in wanted:
                    predictions[nid] = node.get(prop)
    tp = fp = fn = 0
    for it in items:
        pred = predictions.get(it["node"])
        if pred == it["gold"]:
            tp += 1
        elif pred is not None:
            fp += 1
        else:
            fn += 1
    precision = round(tp / (tp + fp), 4) if (tp + fp) else None
    recall = round(tp / (tp + fn), 4) if (tp + fn) else None
    return {
        "layer": gold.get("layer"),
        "property": prop,
        "items": len(items),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": precision,
        "recall": recall,
    }


if __name__ == "__main__":
    raise SystemExit(main())
