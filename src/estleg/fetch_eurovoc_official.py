#!/usr/bin/env python3
"""Stamp official EuroVoc subjects from CELLAR onto EU act peeps (#699).

The Publications Office indexes every EU act with EuroVoc descriptors via
``cdm:work_is_about_concept_eurovoc``. Those are the authoritative subjects
for EU legislation, so EU acts take them verbatim instead of going through
the Estonian keyword classifier (``classify_eurovoc``).

For every ``estleg:EULegislation`` node in ``krr_outputs/eurlex/*_peep.json``
this pass:

* queries CELLAR in batches of CELEX numbers (POST + bounded retry from
  ``eurlex_common``), caching the answer per CELEX in
  ``data/eurovoc/cellar_eurovoc_subjects.json`` so re-runs are offline;
* stamps ``dcterms:subject`` and ``eli:is_about`` (bare
  ``http://eurovoc.europa.eu/<id>`` IRIs; ``<id>`` is numeric or, for
  newer concepts, ``c_<hex>``) plus the
  provenance marker ``estleg:subjectSource "cellar"``, which separates
  official subjects from the keyword classifier's
  ``estleg:assertionConfidence``-stamped subjects on Estonian acts.

A CELEX the cache records with an empty list was queried and CELLAR has no
EuroVoc for it. A CELEX missing from the cache was never answered (a failed
batch), so it is retried on the next run. Coverage is reported as acts with
subjects / total acts. Nothing is invented for acts CELLAR does not index.

Run ``scripts/rebuild_subcorpus_combined.py --subcorpus eurlex`` afterwards
(or pass ``--rebuild-combined``) so ``eurlex_combined.jsonld`` matches the
peeps.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from estleg.estleg_common import save_json
from estleg.eurlex_common import sparql_query, sparql_query_with_retry
from estleg.heuristic_overrides import ensure_prov_context, finalize_node, load_overrides

REPO_ROOT = Path(__file__).resolve().parents[2]
KRR_DIR = REPO_ROOT / "krr_outputs"
EURLEX_DIR = KRR_DIR / "eurlex"
CACHE_PATH = REPO_ROOT / "data" / "eurovoc" / "cellar_eurovoc_subjects.json"

EURLEX_PEEP_NAMES: tuple[str, ...] = (
    "eurlex_directives_peep.json",
    "eurlex_regulations_peep.json",
    "eurlex_decisions_peep.json",
)

CELLAR_PREDICATE = "cdm:work_is_about_concept_eurovoc"
SUBJECT_PREDICATES: tuple[str, ...] = ("dcterms:subject", "eli:is_about")
SUBJECT_SOURCE_PREDICATE = "estleg:subjectSource"
SUBJECT_SOURCE_CELLAR = "cellar"
EUROVOC_URI_BASE = "http://eurovoc.europa.eu/"
# Classic descriptors are numeric (``/1280``); concepts added since EuroVoc
# 4.x use ``c_<hex>`` ids (``/c_1c478aa5``). Both are official.
_EUROVOC_IRI_RE = re.compile(r"^http://eurovoc\.europa\.eu/([0-9]+|c_[0-9a-f]+)$")
_CELEX_SAFE_RE = re.compile(r"^[0-9A-Za-z()._-]+$")

DEFAULT_BATCH_SIZE = 500
RATE_DELAY = 1.0  # seconds between CELLAR requests

QueryFn = Callable[[str], list[dict]]


# ---------------------------------------------------------------------------
# CELLAR query
# ---------------------------------------------------------------------------


def build_eurovoc_query(celex_batch: Iterable[str]) -> str:
    """SPARQL selecting ``(celex, concept)`` for a batch of CELEX numbers.

    CELLAR stores ``cdm:resource_legal_id_celex`` as an ``xsd:string``-typed
    literal, so the VALUES terms must carry the datatype (plain literals
    match nothing). CELEX values are validated against a strict charset
    before interpolation.
    """
    terms: list[str] = []
    for celex in celex_batch:
        if not _CELEX_SAFE_RE.match(celex):
            raise ValueError(f"unsafe CELEX value: {celex!r}")
        terms.append(f'"{celex}"^^<http://www.w3.org/2001/XMLSchema#string>')
    values = " ".join(terms)
    return (
        "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
        "SELECT DISTINCT ?celex ?concept WHERE {\n"
        f"  VALUES ?celex {{ {values} }}\n"
        "  ?work cdm:resource_legal_id_celex ?celex .\n"
        f"  ?work {CELLAR_PREDICATE} ?concept .\n"
        "}"
    )


def eurovoc_id(iri: str) -> str | None:
    """EuroVoc concept id (``"1280"`` / ``"c_1c478aa5"``), or ``None``."""
    m = _EUROVOC_IRI_RE.match(iri or "")
    return m.group(1) if m else None


def eurovoc_sort_key(cid: str) -> tuple[int, int | str]:
    """Numeric ids first (by value), then ``c_<hex>`` ids lexically."""
    return (0, int(cid)) if cid.isdigit() else (1, cid)


def sorted_ids(ids: Iterable[str]) -> list[str]:
    return sorted({str(i) for i in ids}, key=eurovoc_sort_key)


def parse_bindings(
    bindings: list[dict], requested: Iterable[str]
) -> tuple[dict[str, list[str]], int]:
    """Map each requested CELEX to its sorted EuroVoc ids.

    Every requested CELEX gets an entry (empty when CELLAR returned none).
    Returns ``(mapping, invalid)``; ``invalid`` counts concept values that are
    not EuroVoc concept IRIs and were dropped.
    """
    found: dict[str, set[str]] = {c: set() for c in requested}
    invalid = 0
    for b in bindings:
        celex = b.get("celex", {}).get("value", "")
        if celex not in found:
            continue
        cid = eurovoc_id(b.get("concept", {}).get("value", ""))
        if cid is None:
            invalid += 1
            continue
        found[celex].add(cid)
    return {c: sorted_ids(ids) for c, ids in found.items()}, invalid


def fetch_official_subjects(
    celex_numbers: Iterable[str],
    *,
    cache: dict[str, list[str]] | None = None,
    refresh: bool = False,
    offline: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    query_fn: QueryFn | None = None,
    delay: float = RATE_DELAY,
    retries: int = 3,
) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Resolve official EuroVoc ids for ``celex_numbers``.

    ``cache`` is updated in place with every CELEX CELLAR answered. Cached
    CELEX are not re-queried unless ``refresh``. ``offline`` never queries.
    A batch that still fails after retries is skipped (its CELEX stay
    unresolved and are retried next run) and counted in ``failed_batches``.
    Returns ``(subjects_by_celex, stats)`` restricted to ``celex_numbers``.
    """
    cache = cache if cache is not None else {}
    fn = query_fn if query_fn is not None else sparql_query
    wanted = sorted(set(celex_numbers))
    todo = [c for c in wanted if refresh or c not in cache]
    stats = {
        "requested": len(wanted),
        "cached": len(wanted) - len(todo),
        "queried": 0,
        "batches": 0,
        "failed_batches": 0,
        "invalid_concepts": 0,
    }
    if not offline:
        for start in range(0, len(todo), batch_size):
            batch = todo[start:start + batch_size]
            if stats["batches"] and delay:
                time.sleep(delay)
            stats["batches"] += 1
            try:
                bindings = sparql_query_with_retry(
                    build_eurovoc_query(batch), query_fn=fn, retries=retries
                )
            except RuntimeError as exc:
                stats["failed_batches"] += 1
                print(f"  WARNING: CELLAR batch {start}..{start + len(batch)} failed: {exc}",
                      file=sys.stderr)
                continue
            mapping, invalid = parse_bindings(bindings, batch)
            stats["invalid_concepts"] += invalid
            stats["queried"] += len(batch)
            cache.update(mapping)
    resolved = {c: cache[c] for c in wanted if c in cache}
    return resolved, stats


# ---------------------------------------------------------------------------
# Cache (compact, one CELEX per line, sorted — diff-friendly)
# ---------------------------------------------------------------------------


def load_cache(path: Path = CACHE_PATH) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    subjects = doc.get("subjects", {})
    return {str(k): sorted_ids(v) for k, v in subjects.items()}


def save_cache(cache: dict[str, list[str]], path: Path = CACHE_PATH) -> None:
    """Write the cache with one CELEX per line (sorted) for small git diffs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with_subjects = sum(1 for v in cache.values() if v)
    header = {
        "source": "CELLAR SPARQL https://publications.europa.eu/webapi/rdf/sparql",
        "predicate": CELLAR_PREDICATE,
        "concept_iri_base": EUROVOC_URI_BASE,
        "note": (
            "CELEX -> EuroVoc descriptor ids (#699). An empty list means "
            "CELLAR was queried and has no EuroVoc for that act. Written by "
            "estleg.fetch_eurovoc_official; re-runs read this file instead of "
            "querying CELLAR (use --refresh to re-query)."
        ),
        "celex_total": len(cache),
        "celex_with_subjects": with_subjects,
    }
    lines = ["{"]
    for key, value in header.items():
        lines.append(f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)},")
    lines.append('  "subjects": {')
    items = sorted(cache.items())
    for n, (celex, ids) in enumerate(items):
        sep = "," if n < len(items) - 1 else ""
        lines.append(f"    {json.dumps(celex)}: {json.dumps(sorted_ids(ids), separators=(',', ':'))}{sep}")
    lines.append("  }")
    lines.append("}")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Stamping
# ---------------------------------------------------------------------------


def official_subject_refs(ids: Iterable[str]) -> list[dict[str, str]]:
    """Bare EuroVoc IRI refs, sorted (numeric ids first) and de-duplicated."""
    return [{"@id": f"{EUROVOC_URI_BASE}{i}"} for i in sorted_ids(ids)]


def stamp_official_subjects(node: dict, ids: Iterable[str] | None) -> bool:
    """Set official subjects + provenance on an EU act node. True if changed.

    With ``ids`` empty/None, previously stamped CELLAR subjects are removed
    (so a re-run reflects CELLAR exactly); subjects from any other source are
    left untouched.
    """
    before = {p: node.get(p) for p in (*SUBJECT_PREDICATES, SUBJECT_SOURCE_PREDICATE)}
    refs = official_subject_refs(ids or ())
    if refs or node.get(SUBJECT_SOURCE_PREDICATE) == SUBJECT_SOURCE_CELLAR:
        for pred in SUBJECT_PREDICATES:
            old = node.get(pred, [])
            old = old if isinstance(old, list) else [old]
            kept = [r for r in old if not (
                isinstance(r, dict) and str(r.get("@id", "")).startswith(EUROVOC_URI_BASE)
            )]
            values = kept + [dict(r) for r in refs]
            if values:
                node[pred] = values
            else:
                node.pop(pred, None)
        if refs:
            node[SUBJECT_SOURCE_PREDICATE] = SUBJECT_SOURCE_CELLAR
        else:
            node.pop(SUBJECT_SOURCE_PREDICATE, None)
    after = {p: node.get(p) for p in before}
    return before != after


def _is_eu_act(node: object) -> bool:
    if not isinstance(node, dict):
        return False
    types = node.get("@type") or []
    if isinstance(types, str):
        types = [types]
    return "estleg:EULegislation" in types and isinstance(node.get("estleg:celexNumber"), str)


def iter_eu_act_nodes(graph: list) -> Iterable[dict]:
    return (n for n in graph if _is_eu_act(n))


def collect_celex(eurlex_dir: Path = EURLEX_DIR) -> list[str]:
    celex: set[str] = set()
    for name in EURLEX_PEEP_NAMES:
        path = eurlex_dir / name
        if not path.exists():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        celex.update(n["estleg:celexNumber"] for n in iter_eu_act_nodes(doc.get("@graph", [])))
    return sorted(celex)


def stamp_eurlex_peeps(
    subjects: dict[str, list[str]],
    eurlex_dir: Path = EURLEX_DIR,
    *,
    overrides=None,
) -> dict[str, int]:
    """Stamp official subjects on every EU act in the eurlex peeps.

    CELEX absent from ``subjects`` (never answered by CELLAR) keep whatever
    they had. Human overrides are applied last, including removals (#700).
    Returns coverage counters.
    """
    stats = {"acts": 0, "with_subjects": 0, "unresolved": 0, "changed": 0,
             "override_skipped": 0, "files_written": 0}
    overrides = load_overrides() if overrides is None else overrides
    for name in EURLEX_PEEP_NAMES:
        path = eurlex_dir / name
        if not path.exists():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        changed_file = False
        for node in iter_eu_act_nodes(doc.get("@graph", [])):
            stats["acts"] += 1
            changed = False
            if overrides.owns(node.get("@id"), "dcterms:subject"):
                stats["override_skipped"] += 1
                if SUBJECT_SOURCE_PREDICATE in node:
                    del node[SUBJECT_SOURCE_PREDICATE]
                    changed = True
            else:
                celex = node["estleg:celexNumber"]
                if celex not in subjects:
                    stats["unresolved"] += 1
                    continue
                if subjects[celex]:
                    stats["with_subjects"] += 1
                changed = stamp_official_subjects(node, subjects[celex])
            changed |= finalize_node(node, overrides, ("dcterms:subject",))
            if changed:
                stats["changed"] += 1
                changed_file = True
        if changed_file:
            if any("prov:wasAttributedTo" in n for n in doc.get("@graph", [])):
                ensure_prov_context(doc)
            save_json(path, doc)
            stats["files_written"] += 1
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--refresh", action="store_true",
                        help="Re-query CELLAR for every CELEX, ignoring the cache.")
    parser.add_argument("--offline", action="store_true",
                        help="Never query CELLAR; stamp from the cache only.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--cache", type=Path, default=CACHE_PATH)
    parser.add_argument("--rebuild-combined", action="store_true",
                        help="Rebuild eurlex_combined.jsonld from the peeps afterwards.")
    parser.add_argument("--overrides", type=Path, default=None,
                        help="human override store (#700; default data/heuristic_overrides.jsonl)")
    args = parser.parse_args(argv)

    from estleg.heuristic_overrides import OverrideError, load_overrides

    try:
        overrides = load_overrides(args.overrides)
    except OverrideError as exc:
        print(f"ERROR: {exc}")
        return 1

    start = time.perf_counter()
    celex = collect_celex()
    print(f"EU acts in corpus: {len(celex)}")
    cache = load_cache(args.cache)
    subjects, fstats = fetch_official_subjects(
        celex, cache=cache, refresh=args.refresh, offline=args.offline,
        batch_size=args.batch_size,
    )
    if fstats["queried"]:
        save_cache(cache, args.cache)
    print(f"CELLAR: {fstats}")
    sstats = stamp_eurlex_peeps(subjects, overrides=overrides)
    pct = 100.0 * sstats["with_subjects"] / sstats["acts"] if sstats["acts"] else 0.0
    print(
        f"Coverage: {sstats['with_subjects']}/{sstats['acts']} EU acts carry "
        f"official EuroVoc ({pct:.1f}%); unresolved {sstats['unresolved']}; "
        f"changed {sstats['changed']} nodes in {sstats['files_written']} file(s)"
    )
    if args.rebuild_combined and sstats["files_written"]:
        from estleg.rebuild_subcorpus_combined import rebuild_subcorpus_combined

        res = rebuild_subcorpus_combined("eurlex", KRR_DIR, subcorpus_dir=EURLEX_DIR).as_dict()
        print(f"Rebuilt {res['path']}: {res['nodes']} nodes")
    print(f"Done in {time.perf_counter() - start:.1f}s")
    return 2 if fstats["failed_batches"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
