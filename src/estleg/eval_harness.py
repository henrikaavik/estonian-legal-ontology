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

5. **Accuracy (#698)** — precision / recall / F1 per heuristic layer from the
   legal gold sets in ``eval/gold_sets/`` (one file per layer, shared schema
   ``item.schema.json``). Only adjudicated items count; pending items are
   reported, never scored. ``--floors`` + ``--gate`` turn the per-layer floors
   in ``eval/accuracy_floors.json`` into a CI gate (#698): a layer with at
   least ``min_adjudicated`` adjudicated items fails the gate when it falls
   below a floor; a layer with fewer is reported as "not enough adjudicated
   items" and does not fail.

Usage::

    python3 scripts/eval_harness.py --report eval/FITNESS_REPORT.md   # regenerate
    python3 scripts/eval_harness.py --report eval/FITNESS_REPORT.md --check
    python3 scripts/eval_harness.py --gold-set eval/gold_sets \
        --floors eval/accuracy_floors.json --gate                       # CI gate
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

from estleg import estleg_common

REPO_ROOT = Path(__file__).resolve().parents[2]
KRR_DIR = REPO_ROOT / "krr_outputs"
EVAL_DIR = REPO_ROOT / "eval"
GOLD_DIR = EVAL_DIR / "gold_sets"
FLOORS_PATH = EVAL_DIR / "accuracy_floors.json"
REPORT_PATH = EVAL_DIR / "FITNESS_REPORT.md"

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
    act_temporal_known = 0
    sanction_defs = 0
    sanction_edges = 0
    law_verticals: dict[str, set[str]] = {}
    laws_loaded = 0

    for law in laws:
        name = law["name"]
        present: set[str] = set()
        act_known = False  # act-level temporalStatus is known (tracked apart from the verticals)
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
                # Act-level temporalStatus is the point-in-time signal at the act
                # layer (#128: temporalStatus lives ONLY on Act nodes — provisions
                # carry point-in-time via the version sidecar, counted separately
                # as the pointInTime vertical, NOT a provision temporalStatus).
                if _is_act(node):
                    status = node.get("estleg:temporalStatus")
                    if isinstance(status, str) and status not in ("", "unknown"):
                        act_known = True
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
                    ):
                        if _has(node, prop):
                            provision_with[prop] += 1
        if act_known:
            act_temporal_known += 1
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
            )
        },
        "point_in_time": {
            "note": (
                "Point-in-time is modelled at two layers. The act layer carries "
                "estleg:temporalStatus (#128: Act nodes ONLY — never provisions). "
                "The provision layer carries per-redaction validity windows in the "
                "version sidecars (provision_versions/). 'temporalStatus unknown on "
                "100% of provisions' is a category error — provisions are not meant "
                "to carry it; the right provision signal is version-sidecar coverage."
            ),
            "laws_with_known_act_temporalStatus": act_temporal_known,
            "act_status_known_pct": pct(act_temporal_known, len(laws)),
            "laws_with_version_sidecar": len(
                [n for n in law_verticals if n in version_sidecars]
            ),
            "version_sidecar_pct": pct(
                len([n for n in law_verticals if n in version_sidecars]), len(laws)
            ),
        },
        "crossref_edge_resolution": {
            "note": (
                "Of citation EDGES that exist, the fraction resolving to a node "
                "defined in the indexed corpus (validates the #561/#630 closure "
                "work). This measures reference integrity, not semantic precision "
                "or extraction recall — citations "
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
                "The #617 finding, refined. The verticals are present as EDGES on "
                "many laws, and on the full load surface (combined + sidecars) most "
                "resolve: sanctions are merged into combined as overlay nodes (#561) "
                "and act-level temporalStatus is now derived where version data "
                "exists (#617). The residual gap is per-PEEP self-containment — a "
                "single *_peep.json file still does not carry its sanction "
                "definitions inline (they live in the sanctions/ sidecar). The "
                "consumable answer surface is combined, not the individual peep."
            ),
            "laws_all_verticals_referenced": len(complete),
            "sanction_definitions_inline_in_peeps": sanction_defs,
            "act_temporalStatus_known_pct": pct(act_temporal_known, len(laws)),
        },
    }


def render_markdown(report: dict) -> str:
    pop = report["population"]
    lines = [
        "# Fitness-for-purpose evaluation",
        "",
        f"_Generated (pinned): {report['generated']} · ontology {report['ontology_version']}_",
        "",
        "Generated from numbers by `python3 scripts/eval_harness.py --report "
        "eval/FITNESS_REPORT.md`; `--check` fails CI-style when this file is stale. "
        "Coverage metrics are objective counts over the indexed root-law corpus; "
        "the accuracy block comes from the adjudicated items of the legal gold sets "
        "(`eval/gold_sets/`, #698).",
        "",
        f"**Corpus fingerprint:** `{report.get('corpus_fingerprint', 'n/a')}` "
        "(sha256 of `krr_outputs/INDEX.json`, first 12 hex).",
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
    pit = report["point_in_time"]
    lines += [
        "",
        "### Retrievability gap (the #617 finding, refined)",
        "",
        f"The verticals are present as edges on {gap['laws_all_verticals_referenced']} laws, and on the "
        "full load surface (combined + version sidecars) most resolve — sanctions are merged into "
        f"combined as overlay nodes (#561) and act-level `temporalStatus` is now known on "
        f"**{pit['act_status_known_pct']}%** of laws (derived from version data, #617). The residual gap "
        f"is per-PEEP self-containment: a single `*_peep.json` still carries "
        f"{gap['sanction_definitions_inline_in_peeps']} inline sanction definitions (they live in the "
        "`sanctions/` sidecar, merged only into combined). **The consumable answer surface is combined, "
        "not the individual peep.**",
    ]
    cr = report["crossref_edge_resolution"]
    inl = report["inline_sanctions"]
    lines += [
        "",
        "## Other fitness metrics",
        "",
        f"- **Cross-reference edge resolution:** {cr['resolved_in_corpus']:,} / {cr['edges']:,} "
        f"({cr['pct']}%) existing citation edges resolve to an in-corpus node "
        "(reference integrity, not semantic precision or extraction recall).",
        "- **Point-in-time (two layers, #128):** act-level `temporalStatus` known on "
        f"{pit['act_status_known_pct']}% of laws ({pit['laws_with_known_act_temporalStatus']}); "
        f"provision-level validity via version sidecars on {pit['version_sidecar_pct']}% "
        f"({pit['laws_with_version_sidecar']} laws). Provisions never carry `temporalStatus`.",
        f"- **Inline sanctions (per peep):** {inl['sanction_node_definitions_in_peeps']} inline "
        f"definitions vs {inl['hasSanction_edges']} `hasSanction` edges — sanctions are in combined "
        "via overlay (#561), not inline in the peep.",
        "",
    ]
    if report.get("accuracy"):
        lines += render_accuracy_markdown(report["accuracy"], report.get("accuracy_gate"))
    return "\n".join(lines)


def corpus_fingerprint(krr_dir: Path = KRR_DIR) -> str:
    index = krr_dir / "INDEX.json"
    if not index.exists():
        return "n/a"
    return hashlib.sha256(index.read_bytes()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Accuracy: legal gold sets (#698)
# ---------------------------------------------------------------------------

def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def wilson_lower(successes: float, total: float, z: float = 1.96) -> float | None:
    """Lower bound of the 95% Wilson interval for a proportion."""
    if not total:
        return None
    p = successes / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return round((centre - margin) / denom, 4)


def score_items(items: list[dict], predictions: dict[str, set[str]] | None = None) -> dict:
    """Value-level precision / recall over adjudicated gold items (#698).

    * A positive item (the system asserts ``system``) adjudicated ``correct``
      has gold = system unless the reviewer recorded gold; ``incorrect`` has
      gold = the recorded gold (empty = nothing is right); ``partial`` must
      record gold. Then TP = |S∩G|, FP = |S−G|, FN = |G−S|.
    * A negative item (the system asserts nothing) adjudicated ``correct`` is a
      true negative; ``incorrect`` / ``partial`` is a recall miss counting
      |gold| false negatives (at least one).
    * ``pending`` items are excluded and counted.
    """
    tp = fp = fn = tn = 0
    verdicts: Counter = Counter()
    sources: Counter = Counter()
    by_value: dict[str, Counter] = defaultdict(Counter)
    negatives = pending = correct = 0
    for it in items:
        verdict = it.get("verdict", "pending")
        verdicts[verdict] += 1
        negatives += bool(it.get("negative"))
        if verdict == "pending":
            pending += 1
            continue
        sources[it.get("verdict_source") or "unknown"] += 1
        sampled_system = set(it.get("system") or [])
        system = sampled_system if predictions is None else predictions[it["id"]]
        gold = set(it.get("gold") or [])
        value = (it.get("stratum") or {}).get("value", "")
        if it.get("negative") and (predictions is None or (verdict != "correct" and not gold)):
            if verdict == "correct":
                tn += 1
                correct += 1
                by_value[value]["tn"] += 1
            else:
                miss = max(1, len(gold - system))
                fn += miss
                by_value[value]["fn"] += miss
            continue
        if verdict == "correct" and not gold:
            gold = sampled_system
        t, f, n = len(system & gold), len(system - gold), len(gold - system)
        if not system and not gold:
            tn += 1
            by_value[value]["tn"] += 1
        correct += system == gold
        tp, fp, fn = tp + t, fp + f, fn + n
        by_value[value]["tp"] += t
        by_value[value]["fp"] += f
        by_value[value]["fn"] += n
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = round(2 * precision * recall / (precision + recall), 4)
    adjudicated = len(items) - pending
    return {
        "items": len(items),
        "adjudicated": adjudicated,
        "pending": pending,
        "negatives": negatives,
        "adjudicated_by": dict(sorted(sources.items())),
        "verdicts": {v: verdicts.get(v, 0) for v in ("correct", "incorrect", "partial", "pending")},
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives": tn,
        "precision": precision,
        "precision_lower95": wilson_lower(tp, tp + fp),
        "recall": recall,
        "f1": f1,
        "item_accuracy": _ratio(verdicts["correct"] if predictions is None else correct, adjudicated),
        "by_value": {
            k: {
                "precision": _ratio(c["tp"], c["tp"] + c["fp"]),
                "recall": _ratio(c["tp"], c["tp"] + c["fn"]),
                **{m: c[m] for m in ("tp", "fp", "fn", "tn")},
            }
            for k, c in sorted(by_value.items())
        },
    }


def load_gold_dir(gold_dir: Path) -> tuple[dict[str, dict], list[str]]:
    """Every ``<layer>.json`` gold set under ``gold_dir`` + validation errors."""
    from estleg import gold_sets

    schema_path = (gold_dir.parent if gold_dir.is_file() else gold_dir) / "item.schema.json"
    schema = gold_sets.load_schema(schema_path if schema_path.exists() else gold_sets.SCHEMA_PATH)
    docs: dict[str, dict] = {}
    errors: list[str] = []
    paths = [gold_dir] if gold_dir.is_file() else sorted(gold_dir.glob("*.json"))
    if not paths:
        errors.append(f"{gold_dir}: no gold sets found")
    for path in paths:
        if path.name == "item.schema.json":
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}: unreadable ({exc})")
            continue
        problems = gold_sets.validate_gold_document(doc, schema)
        errors.extend(f"{path.name}: {p}" for p in problems[:20])
        if not problems and doc["layer"] in docs:
            errors.append(f"{path.name}: duplicate layer {doc['layer']}")
        elif not problems:
            docs[doc["layer"]] = doc
    return docs, errors


def current_predictions(docs: dict[str, dict], krr_dir: Path) -> tuple[dict[str, set[str]], list[str]]:
    """Read current assertions for the fixed gold probes, never re-sample them.

    Most positive items adjudicate one edge, not every value on a node. Score
    only that item's system/gold values; whole-node negatives and deontic
    items inspect the entire current value set. Court/fold negatives carry a
    specific probe. Sanction amounts are part of the reviewed assertion too.
    """
    from estleg.gold_sets import as_list, literal, ref_ids, section_iri

    items = [it for doc in docs.values() for it in doc["items"] if it["verdict"] != "pending"]
    wanted = {it["node"] for it in items}
    sections = {it["node"] for it in items if it["predicate"] == "estleg:interpretedBy" and it["negative"]}
    predicates = {it["predicate"] for it in items} - {"dcterms:subject", "skos:altLabel"}
    values: dict[tuple[str, str], set[str]] = defaultdict(set)
    present: set[str] = set()
    sanctions: dict[str, dict] = {}
    errors: list[str] = []

    def read_graph(path: Path) -> list[dict]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))["@graph"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"{_display(path)}: cannot read prediction source ({exc})")
            return []

    paths = sorted(krr_dir.glob("*_peep.json")) + sorted((krr_dir / "regulations").rglob("*_peep.json"))
    for path in paths:
        for node in read_graph(path):
            nid = node.get("@id") if isinstance(node, dict) else None
            if not isinstance(nid, str):
                continue
            if nid in wanted:
                present.add(nid)
                for prop in predicates:
                    values[nid, prop].update(ref_ids(node.get(prop)))
            parent = section_iri(nid)
            if parent in sections:
                values[parent, "estleg:interpretedBy"].update(ref_ids(node.get("estleg:interpretedBy")))
    if "sanctions" in docs:
        for path in sorted((krr_dir / "sanctions").glob("*.json")):
            for node in read_graph(path):
                if not isinstance(node, dict) or "estleg:Sanction" not in as_list(node.get("@type")):
                    continue
                for nid in ref_ids(node.get("estleg:applicableProvision")):
                    if nid in wanted:
                        values[nid, "estleg:hasSanction"].add(node["@id"])
                        sanctions[node["@id"]] = node
    for layer, rel, prop in (
        ("eurovoc", "eurovoc/eurovoc_overlay.jsonld", "dcterms:subject"),
        ("altLabelFolds", "concepts/concepts_combined.jsonld", "skos:altLabel"),
    ):
        if layer not in docs:
            continue
        for node in read_graph(krr_dir / rel):
            if not isinstance(node, dict) or node.get("@id") not in wanted:
                continue
            nid = node["@id"]
            if layer == "altLabelFolds":
                present.add(nid)
                values[nid, prop].update(v for x in as_list(node.get(prop)) if (v := literal(x)))
            else:
                values[nid, prop].update(ref_ids(node.get(prop)))
    predictions = {}
    for it in items:
        nid, prop = it["node"], it["predicate"]
        if it["negative"] and it["verdict"] != "correct" and not it["gold"]:
            errors.append(f"{it['id']}: record the missing gold value before scoring current predictions")
        if nid not in present:
            errors.append(f"{it['id']}: gold subject {nid} is missing from the current corpus")
        actual = values[nid, prop]
        if it.get("probe"):
            probe = it.get("probe_label") if prop == "skos:altLabel" else it["probe"]
            actual = actual & {probe}
        elif not it["negative"] and prop != "estleg:normativeType":
            actual = actual & set(it["system"] + it["gold"])
        predictions[it["id"]] = set(actual)
        if prop == "estleg:hasSanction" and it.get("context"):
            for sid in actual:
                sanction = sanctions.get(sid, {})
                context = {key: literal(sanction.get(f"estleg:{key}")) for key in it["context"]}
                if context != it["context"]:
                    errors.append(f"{it['id']}: sanction values changed; re-adjudicate {sid}")
    return predictions, errors


def evaluate_gold_dir(gold_dir: Path, krr_dir: Path | None = None) -> dict:
    """Accuracy block: per-layer scores over every gold set in ``gold_dir``."""
    docs, errors = load_gold_dir(gold_dir)
    predictions = None
    if krr_dir is not None:
        predictions, prediction_errors = current_predictions(docs, krr_dir)
        errors.extend(prediction_errors)
    layers = {}
    for layer, doc in sorted(docs.items()):
        score = score_items(doc["items"], predictions)
        score["property"] = doc["property"]
        score["title"] = doc["title"]
        score["corpus_commit"] = doc["sampling"]["corpus_commit"]
        score["seed"] = doc["sampling"]["seed"]
        layers[layer] = score
    commits = sorted({d["sampling"]["corpus_commit"] for d in docs.values()})
    return {
        "gold_dir": _display(gold_dir),
        "corpus_commits": commits,
        "layers": layers,
        "errors": errors,
    }


def apply_floors(accuracy: dict, floors: dict) -> dict:
    """Gate verdict per layer: ``pass`` / ``fail`` / ``insufficient`` / ``no_floor``."""
    results = {}
    for layer, score in accuracy["layers"].items():
        floor = floors.get("layers", {}).get(layer)
        if not floor:
            results[layer] = {"status": "no_floor", "reasons": []}
            continue
        need = int(floor.get("min_adjudicated", 0))
        if score["adjudicated"] < need:
            results[layer] = {
                "status": "insufficient",
                "reasons": [f"not enough adjudicated items ({score['adjudicated']} < {need})"],
                "floor": floor,
            }
            continue
        reasons = []
        for metric in ("precision", "recall"):
            want = floor.get(metric)
            got = score.get(metric)
            if want is not None:
                if got is None:
                    reasons.append(f"{metric} is unmeasurable (floor {want})")
                elif got < want:
                    reasons.append(f"{metric} {got:.4f} < floor {want}")
        results[layer] = {"status": "fail" if reasons else "pass", "reasons": reasons, "floor": floor}
    for layer in sorted(set(floors.get("layers", {})) - set(accuracy["layers"])):
        results[layer] = {"status": "fail", "reasons": ["floor set but no gold set found"],
                          "floor": floors["layers"][layer]}
    return dict(sorted(results.items()))


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def render_accuracy_markdown(accuracy: dict, gate: dict | None = None) -> list[str]:
    lines = [
        "## Accuracy (legal gold sets, #698)",
        "",
        "Precision / recall / F1 over **adjudicated** items only (value-level; "
        "negatives confirmed empty are true negatives, negatives with a missing "
        "value are false negatives). Pending items are counted, never scored. "
        "Mechanical verdicts are pre-filled only where a documented rule decides "
        "the item from the quoted evidence (see each file's `mechanical_rules`); "
        "they cover the easy cases, so these numbers are an upper bound until a "
        "legal reviewer adjudicates the pending items.",
        "",
        f"Gold sets sampled at corpus commit(s): "
        f"{', '.join(f'`{c[:10]}`' for c in accuracy['corpus_commits']) or 'n/a'}.",
        "",
        "| Layer | Items | Adjudicated (mech / reviewer) | Pending | Negatives | TP | FP | FN | TN "
        "| Precision | P 95% low | Recall | F1 | Gate |",
        "| --- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --- |",
    ]
    for layer, sc in accuracy["layers"].items():
        by = sc["adjudicated_by"]
        status = ""
        if gate and layer in gate:
            g = gate[layer]
            status = {
                "pass": "pass",
                "fail": "**FAIL**",
                "insufficient": "not enough adjudicated items",
                "no_floor": "no floor",
            }[g["status"]]
        lines.append(
            f"| {layer} | {sc['items']} | {sc['adjudicated']} ({by.get('mechanical', 0)} / "
            f"{by.get('reviewer', 0)}) | {sc['pending']} | {sc['negatives']} | "
            f"{sc['true_positives']} | {sc['false_positives']} | {sc['false_negatives']} | "
            f"{sc['true_negatives']} | {_fmt(sc['precision'])} | {_fmt(sc['precision_lower95'])} | "
            f"{_fmt(sc['recall'])} | {_fmt(sc['f1'])} | {status} |"
        )
    if gate:
        lines += ["", "Floors (`eval/accuracy_floors.json`):", ""]
        for layer, g in gate.items():
            floor = g.get("floor")
            if floor:
                parts = [
                    f"{metric} ≥ {floor[metric]}" if floor.get(metric) is not None
                    else f"{metric} not gated"
                    for metric in ("precision", "recall")
                ]
                lines.append(
                    f"- `{layer}`: {', '.join(parts)}; gated from "
                    f"{floor.get('min_adjudicated')} adjudicated items — {g['status']}"
                    + (f" ({'; '.join(g['reasons'])})" if g["reasons"] else "")
                )
    if accuracy.get("errors"):
        lines += ["", "Invalid gold-set files:", ""] + [f"- {e}" for e in accuracy["errors"]]
    lines.append("")
    return lines


def _display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def build_report(krr_dir: Path, gold_dir: Path | None, floors_path: Path | None,
                 *, current_corpus: bool = False) -> dict:
    report = evaluate(krr_dir)
    report["corpus_fingerprint"] = corpus_fingerprint(krr_dir)
    if gold_dir is not None:
        report["accuracy"] = evaluate_gold_dir(gold_dir, krr_dir if current_corpus else None)
        if floors_path is not None and floors_path.exists():
            floors = json.loads(floors_path.read_text(encoding="utf-8"))
            report["accuracy_gate"] = apply_floors(report["accuracy"], floors)
    return report


def _report_texts(report: dict) -> tuple[str, str]:
    return (
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        render_markdown(report),
    )


def _gate_status(accuracy: dict, gate: dict | None) -> int:
    if gate is None:
        print("::error::accuracy floors were not loaded", file=sys.stderr)
        return 1
    failed = [layer for layer, result in (gate or {}).items() if result["status"] == "fail"]
    if accuracy.get("errors"):
        print(f"::error::{len(accuracy['errors'])} gold-set validation error(s)", file=sys.stderr)
    if failed:
        print(f"::error::accuracy floor not met: {', '.join(failed)}", file=sys.stderr)
    return int(bool(failed or accuracy.get("errors")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fitness-for-purpose evaluation harness (#617, #698)")
    parser.add_argument(
        "--report", type=Path, default=None,
        help="Write the Markdown report here (fitness_report.json goes next to it). "
             "Default when no gold-set-only flags are given: eval/FITNESS_REPORT.md.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Legacy: directory for fitness_report.json / FITNESS_REPORT.md.",
    )
    parser.add_argument("--check", action="store_true",
                        help="With --report: fail (exit 1) when the committed report is stale.")
    parser.add_argument(
        "--gold-set", type=Path, default=None,
        help="Gold-set directory (every <layer>.json is scored) or a legacy single file.",
    )
    parser.add_argument("--floors", type=Path, default=None,
                        help="Per-layer accuracy floors JSON (eval/accuracy_floors.json).")
    parser.add_argument("--gate", action="store_true",
                        help="Exit 1 when a layer with enough adjudicated items is below its floor "
                             "or a gold-set file is invalid.")
    parser.add_argument("--current-corpus", action="store_true",
                        help="Score fixed gold probes against current corpus assertions, not saved predictions.")
    parser.add_argument("--krr-dir", type=Path, default=KRR_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    # Legacy single-file gold set (pre-#698 format: items without verdicts).
    if args.gold_set is not None and args.gold_set.is_file():
        doc = json.loads(args.gold_set.read_text(encoding="utf-8"))
        if "schema_version" not in doc:
            if args.gate:
                parser.error("--gate requires a schema-versioned gold set")
            result = evaluate_gold_set(args.gold_set, args.krr_dir)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0

    gold_only = args.gold_set is not None and args.report is None and args.output_dir is None
    if gold_only:
        accuracy = evaluate_gold_dir(args.gold_set, args.krr_dir if args.current_corpus else None)
        gate = None
        floors_path = args.floors or (FLOORS_PATH if args.gate else None)
        if floors_path is not None:
            gate = apply_floors(accuracy, json.loads(floors_path.read_text(encoding="utf-8")))
        if not args.quiet:
            print("\n".join(render_accuracy_markdown(accuracy, gate)))
        if args.gate:
            return _gate_status(accuracy, gate)
        return 0

    if args.report is not None:
        md_path = args.report
    else:
        md_path = (args.output_dir or EVAL_DIR) / "FITNESS_REPORT.md"
    json_path = md_path.parent / "fitness_report.json"
    gold_dir = args.gold_set if args.gold_set is not None else GOLD_DIR
    floors = args.floors if args.floors is not None else FLOORS_PATH
    report = build_report(args.krr_dir, gold_dir, floors, current_corpus=args.current_corpus)
    json_text, md_text = _report_texts(report)
    gate_status = _gate_status(report.get("accuracy", {}), report.get("accuracy_gate")) if args.gate else 0

    if args.check:
        stale = [
            str(p) for p, text in ((json_path, json_text), (md_path, md_text))
            if not p.exists() or p.read_text(encoding="utf-8") != text
        ]
        if stale:
            print("Fitness report is stale; regenerate with "
                  f"`python3 scripts/eval_harness.py --report {_display(md_path)}`: "
                  + ", ".join(stale), file=sys.stderr)
            return 1
        if not args.quiet:
            print(f"Fitness report is current: {_display(md_path)}")
        return gate_status

    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json_text, encoding="utf-8")
    md_path.write_text(md_text, encoding="utf-8")
    if not args.quiet:
        cooc = report["vertical_cooccurrence"]
        print(f"Fitness report → {_display(json_path)}")
        print(f"  provisions: {report['population']['provisions']:,}")
        print(
            f"  laws with all {len(VERTICALS)} verticals (referenced): "
            f"{cooc['laws_with_all_verticals_referenced']}"
        )
        print(f"  cross-ref edge resolution: {report['crossref_edge_resolution']['pct']}%")
        print(f"  act temporalStatus known: {report['point_in_time']['act_status_known_pct']}%")
        for layer, sc in report.get("accuracy", {}).get("layers", {}).items():
            print(f"  accuracy {layer}: P={_fmt(sc['precision'])} R={_fmt(sc['recall'])} "
                  f"adjudicated {sc['adjudicated']}/{sc['items']}")
    return gate_status


def evaluate_gold_set(path: Path, krr_dir: Path = KRR_DIR) -> dict:
    """Legacy single-file scorer (#617): compare corpus values to ``gold``.

    The pre-#698 format is ``{"property": "estleg:...", "items": [{"node":
    "<@id>", "gold": <value>}, ...]}``: each node's predicted value is looked up
    in the corpus (root AND regulation peeps, ``rglob``) and scored by
    exact match. A #698 gold set (items with ``verdict``) is scored from its
    verdicts instead (:func:`score_items`).
    """
    gold = json.loads(path.read_text(encoding="utf-8"))
    items = gold.get("items", [])
    if items and "verdict" in items[0]:
        result = score_items(items)
        result.update({"layer": gold.get("layer"), "property": gold.get("property")})
        return result
    prop = gold["property"]
    predictions: dict[str, object] = {}
    wanted = {it["node"] for it in items}
    if wanted:
        for fpath in sorted(krr_dir.rglob("*_peep.json")):
            try:
                doc = json.loads(fpath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for node in doc.get("@graph", []) if isinstance(doc, dict) else []:
                nid = node.get("@id") if isinstance(node, dict) else None
                if nid in wanted:
                    predictions[nid] = node.get(prop)
    tp = fp = fn = 0
    for it in items:
        pred = predictions.get(it["node"])
        if pred == it["gold"]:
            tp += 1
        elif pred is not None:
            # A wrong NON-EMPTY prediction is both a false positive (the model
            # predicted the wrong value) AND a false negative (it missed the
            # correct one) — otherwise recall ignores confident-but-wrong
            # predictions and reports None instead of 0.0.
            fp += 1
            fn += 1
        else:
            # No prediction at all: a false negative only (nothing was asserted,
            # so there is no false positive).
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
