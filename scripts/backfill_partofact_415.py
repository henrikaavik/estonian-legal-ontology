#!/usr/bin/env python3
"""Backfill ``estleg:partOfAct`` onto existing provision/chapter nodes (issue #415).

Act roots were graph-disconnected from their own provisions: provisions carried
only the literal string ``estleg:sourceAct`` plus an ``estleg:isPartOf`` link to a
Chapter, and Chapters had no link up to the act root. SPARQL could not answer
"all provisions of act X" without unreliable title string-joins.

The generators (``generate_all_laws.py``, ``generate_regulations.py``) now emit an
IRI-valued ``estleg:partOfAct`` on every provision and chapter. This script is the
one-time data catch-up for the *already-committed* corpus: it stamps the same edge
onto existing ``*_peep.json`` files **in place**, touching nothing else, so the
diff is ``partOfAct``-only and free of the unrelated extraction/IRI drift a full
``--force`` regen would drag in (that drift belongs to #445/#448, not here).

Every peep file holds exactly one ``estleg:Act`` node (multipart osas live in
separate files), so the target root is unambiguous. Idempotent: nodes that already
carry ``estleg:partOfAct`` (e.g. KOV provisions stamped by ``enrich_kov_layer1.py``)
are left untouched. ``partOfAct`` is inserted in the generators' exact key position
(provision: right after ``sourceAct``; chapter: right before ``owl:sameAs``) so a
later full regen produces no key-order churn.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from estleg_common import save_json  # noqa: E402

import json  # noqa: E402

KRR = Path(__file__).resolve().parent.parent / "krr_outputs"


def _types(node: dict) -> list[str]:
    t = node.get("@type")
    if isinstance(t, str):
        return [t]
    return t or []


def _act_root(graph: list[dict]) -> str | None:
    roots = [n["@id"] for n in graph if "estleg:Act" in _types(n)]
    if len(roots) != 1:
        return None  # 0 = empty stub; >1 should never happen (one Act per file)
    return roots[0]


def _insert_after(node: dict, after_key: str, new_key: str, new_val) -> dict:
    """Return a copy of ``node`` with ``new_key`` inserted right after
    ``after_key`` (or appended if ``after_key`` is absent), preserving order."""
    out: dict = {}
    inserted = False
    for k, v in node.items():
        out[k] = v
        if k == after_key:
            out[new_key] = new_val
            inserted = True
    if not inserted:
        out[new_key] = new_val
    return out


def _insert_before(node: dict, before_key: str, new_key: str, new_val) -> dict:
    """Return a copy of ``node`` with ``new_key`` inserted right before
    ``before_key`` (or appended if ``before_key`` is absent)."""
    if before_key not in node:
        out = dict(node)
        out[new_key] = new_val
        return out
    out = {}
    for k, v in node.items():
        if k == before_key:
            out[new_key] = new_val
        out[k] = v
    return out


def backfill_file(path: Path, *, write: bool = True) -> tuple[int, int]:
    """Stamp partOfAct on provisions+chapters in one peep file.

    Returns (provisions_stamped, chapters_stamped). (0, 0) means already
    complete or no act root (stub) — file is not rewritten.

    With ``write=False`` (dry-run) the planned edges are still computed and
    counted but ``save_json`` is NOT called, so the file is left untouched.
    The default ``write=True`` preserves the original write-in-place behaviour
    for existing callers/tests.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return (0, 0)
    root = _act_root(graph)
    if root is None:
        return (0, 0)
    link = {"@id": root}

    prov_n = chap_n = 0
    new_graph: list[dict] = []
    for node in graph:
        if not isinstance(node, dict):
            new_graph.append(node)
            continue
        if "estleg:partOfAct" in node:
            new_graph.append(node)
            continue
        types = _types(node)
        # Provision: any node carrying estleg:paragrahv (matches the SHACL
        # LegalProvisionShape target sh:targetSubjectsOf estleg:paragrahv).
        if "estleg:paragrahv" in node:
            after = "estleg:sourceAct" if "estleg:sourceAct" in node else "rdfs:label"
            new_graph.append(_insert_after(node, after, "estleg:partOfAct", link))
            prov_n += 1
        elif "estleg:Chapter" in types:
            new_graph.append(
                _insert_before(node, "owl:sameAs", "estleg:partOfAct", link)
            )
            chap_n += 1
        else:
            new_graph.append(node)

    if prov_n or chap_n:
        doc["@graph"] = new_graph
        if write:
            save_json(path, doc)
    return (prov_n, chap_n)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Stamp estleg:partOfAct in place. Without this flag the script "
             "runs in dry-run mode (the default): it counts the edges it would "
             "add but rewrites NOTHING.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing any file (the default; "
             "accepted explicitly for symmetry with the sibling backfills).",
    )
    args = parser.parse_args(argv)
    # Dry-run is the default; --apply is the only switch that writes. A
    # contradictory ``--apply --dry-run`` is rejected rather than silently
    # writing.
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")
    write = args.apply

    peeps = sorted(KRR.rglob("*_peep.json"))
    files_changed = prov_total = chap_total = stubs = 0
    for p in peeps:
        prov, chap = backfill_file(p, write=write)
        if prov or chap:
            files_changed += 1
            prov_total += prov
            chap_total += chap
        else:
            stubs += 1
    verb = "would stamp" if not write else "stamped"
    if not write:
        print("[DRY-RUN] previewing only — pass --apply to write")
    print(f"Scanned {len(peeps)} peep files")
    print(f"  Files {verb}:        {files_changed}")
    print(f"  Provisions {verb}:   {prov_total}")
    print(f"  Chapters {verb}:     {chap_total}")
    print(f"  Unchanged (complete/stub): {stubs}")
    if not write:
        print(
            f"[DRY-RUN] would stamp partOfAct on {prov_total} provisions + "
            f"{chap_total} chapters across {files_changed} files; "
            f"re-run with --apply to write."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
