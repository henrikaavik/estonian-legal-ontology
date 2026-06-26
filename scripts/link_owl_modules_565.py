#!/usr/bin/env python3
"""Bridge the two hand-built OWL modules to canonical provisions (#565).

Two hand-authored OWL modules ship alongside the generated corpus:

* ``krr_outputs/karistusseadustik_eriosa_owl.jsonld`` — KarS Eriosa (§88-§451)
* ``krr_outputs/tsus_osa7_138_169_owl.jsonld``        — TsÜS Osa 7 (§138-§169)

They are *orphaned islands*: their ``estleg:Section`` IRIs use bespoke local
fragments (``KarS_Par_<N>`` / ``TsUS_Par_<N>``) that are **disjoint** from the
canonical law peeps, whose provision nodes use ``KARIST_2_Osa2_Par_<N>`` and
``TsÜS_Osa7_Par_<N>``. Nothing in the graph connects the two, so a consumer
that finds a module section can't reach the canonical provision (or vice
versa).

This joins them with ``owl:sameAs``: for every module Section it computes the
canonical provision fragment for the same paragraph, and — *only when that
canonical node actually exists in the corpus* — stamps
``"owl:sameAs": {"@id": "estleg:<canonical-fragment>"}`` onto the module node.
The canonical set is discovered by scanning every ``*_peep.json`` (Step 1), so
the bridge is minted against real provision nodes rather than a hard-assumed
spelling — important for TsÜS, whose canonical prefix carries the Estonian
letter ``Õ`` (``TsÜS``) while the module side is ASCII (``TsUS``).

The hand-built modules also contain irregular ``Par<N>_<M>`` Section fragments
whose ``_<M>`` suffix is an *enumeration index*, not the canonical superscript
number (e.g. module ``Par93_2`` is labelled "§ 93¹", and no
``KARIST_2_Osa2_Par_93_2`` exists). Those cannot be mapped by a fragment
transform, so they are reported as **unmatched** and left untouched rather than
mis-linked.

This is a **surgical, offline, deterministic** post-process (no network) and is
**idempotent** — re-running produces a no-op diff. DRY-RUN is the default;
``--apply`` writes the changed module files atomically (via ``save_json``), and
an explicit ``--dry-run`` forces a no-op report even alongside ``--apply``.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from estleg_common import KRR_DIR, save_json

NS = "https://data.riik.ee/ontology/estleg#"

# The two orphaned hand-built OWL modules to bridge.
MODULE_FILES: tuple[str, ...] = (
    "karistusseadustik_eriosa_owl.jsonld",
    "tsus_osa7_138_169_owl.jsonld",
)

# Module Section fragments follow ``<Prefix>_Par_<N>``. The canonical provision
# node for the same paragraph uses a different prefix + part infix. We never
# hard-assume the spelling that shipped: each transform yields ordered candidate
# fragments and only a candidate present in the scanned canonical set is used.
_KARS_RE = re.compile(r"^KarS_Par_(.+)$")
_TSUS_RE = re.compile(r"^TsUS_Par_(.+)$")

# Cap on the per-module unmatched-fragment sample surfaced in the report.
_UNMATCHED_SAMPLE_CAP = 10


def local_fragment(iri: object) -> str | None:
    """Return the local fragment of an estleg IRI, compact or full, else ``None``.

    Handles both the compact ``estleg:Foo`` form and the expanded
    ``https://data.riik.ee/ontology/estleg#Foo`` form. Any other value
    (external IRI, blank-node, non-string) yields ``None``.
    """
    if not isinstance(iri, str):
        return None
    if iri.startswith(NS):
        return iri[len(NS):]
    if iri.startswith("estleg:"):
        return iri[len("estleg:"):]
    return None


def is_section(node: dict) -> bool:
    """Return True when ``node``'s ``@type`` list contains ``estleg:Section``."""
    types = node.get("@type", [])
    if isinstance(types, str):
        types = [types]
    return "estleg:Section" in types


def canonical_candidates(module_fragment: str) -> list[str]:
    """Ordered canonical-fragment candidates for a module Section fragment.

    * KarS module: ``KarS_Par_<N>`` -> ``KARIST_2_Osa2_Par_<N>``
    * TsÜS module: ``TsUS_Par_<N>`` -> ``TsÜS_Osa7_Par_<N>`` (Õ spelling first),
      with an ASCII ``TsUS_Osa7_Par_<N>`` fallback.

    Returns ``[]`` for any fragment that does not follow the regular
    ``<Prefix>_Par_<N>`` shape (e.g. the modules' irregular ``Par<N>_<M>``
    enumeration nodes), so such nodes are reported unmatched rather than
    mis-linked.
    """
    m = _KARS_RE.match(module_fragment)
    if m:
        return [f"KARIST_2_Osa2_Par_{m.group(1)}"]
    m = _TSUS_RE.match(module_fragment)
    if m:
        n = m.group(1)
        return [f"TsÜS_Osa7_Par_{n}", f"TsUS_Osa7_Par_{n}"]
    return []


def map_to_canonical(module_fragment: str, canonical: set[str]) -> str | None:
    """Return the canonical fragment for a module Section fragment, or ``None``.

    The first candidate (see :func:`canonical_candidates`) that exists in
    ``canonical`` is returned, so a bridge is minted only when the canonical
    provision node really exists and the correct spelling is selected from what
    actually shipped.
    """
    for candidate in canonical_candidates(module_fragment):
        if candidate in canonical:
            return candidate
    return None


def add_same_as(node: dict, target: dict) -> bool:
    """Idempotently add an ``owl:sameAs`` edge to ``target``.

    ``owl:sameAs`` may be absent, a single object, or a list. Returns ``True``
    when the edge was newly added and ``False`` when an edge to the same
    ``@id`` already existed (no duplication, no mutation). A pre-existing edge
    to a *different* target is preserved and the new one appended
    (``owl:sameAs`` is symmetric and multi-valued).
    """
    target_id = target.get("@id")
    existing = node.get("owl:sameAs")
    if existing is None:
        node["owl:sameAs"] = {"@id": target_id}
        return True
    items = existing if isinstance(existing, list) else [existing]
    for item in items:
        if isinstance(item, dict) and item.get("@id") == target_id:
            return False  # already linked — idempotent no-op, leave node as-is
    items = [*items, {"@id": target_id}]
    node["owl:sameAs"] = items
    return True


def _load_jsonld(path: Path) -> dict | None:
    """Load a JSON-LD document; return ``None`` for LFS pointers / unparseable files.

    A Git-LFS pointer is a tiny text stub (``version https://git-lfs...``), not
    a JSON document, so it is skipped explicitly; any other parse/IO failure is
    likewise treated as "nothing to collect / nothing to link".
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if text.lstrip().startswith("version https://git-lfs.github.com/spec/"):
        return None
    try:
        doc = json.loads(text)
    except (ValueError, UnicodeDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def build_canonical_fragments(krr_dir: Path = KRR_DIR) -> set[str]:
    """Scan every ``*_peep.json`` and collect provision local-fragments (Step 1).

    Returns the set of ``@id`` local-fragments containing ``_Par_`` — the
    canonical provision nodes the modules bridge onto. LFS pointer files and
    unparseable JSON are skipped.
    """
    fragments: set[str] = set()
    for path in krr_dir.rglob("*_peep.json"):
        doc = _load_jsonld(path)
        if doc is None:
            continue
        for node in doc.get("@graph", []):
            if not isinstance(node, dict):
                continue
            frag = local_fragment(node.get("@id"))
            if frag and "_Par_" in frag:
                fragments.add(frag)
    return fragments


def _new_stats() -> dict:
    return {
        "sections_scanned": 0,
        "sameas_added": 0,
        "already_linked": 0,
        "unmatched": 0,
        "unmatched_sample": [],
    }


def link_module(path: Path, canonical: set[str]) -> tuple[dict | None, dict]:
    """Add ``owl:sameAs`` bridges to a module's Section nodes (in memory).

    Returns ``(doc, stats)``. ``doc`` is the mutated document (the caller
    decides whether to persist it) or ``None`` when the file could not be read.
    ``stats`` carries ``sections_scanned``, ``sameas_added``, ``already_linked``,
    ``unmatched`` and a capped ``unmatched_sample`` of module fragments.
    """
    stats = _new_stats()
    doc = _load_jsonld(path)
    if doc is None:
        return None, stats
    for node in doc.get("@graph", []):
        if not isinstance(node, dict) or not is_section(node):
            continue
        stats["sections_scanned"] += 1
        frag = local_fragment(node.get("@id"))
        canonical_frag = map_to_canonical(frag, canonical) if frag else None
        if canonical_frag is None:
            stats["unmatched"] += 1
            if frag and len(stats["unmatched_sample"]) < _UNMATCHED_SAMPLE_CAP:
                stats["unmatched_sample"].append(frag)
            continue
        if add_same_as(node, {"@id": f"estleg:{canonical_frag}"}):
            stats["sameas_added"] += 1
        else:
            stats["already_linked"] += 1
    return doc, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write owl:sameAs bridges into the module files (atomic). "
        "Default is a dry-run report.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force a no-op report even if --apply is given (overrides --apply).",
    )
    args = parser.parse_args(argv)
    write = args.apply and not args.dry_run

    canonical = build_canonical_fragments()

    print("=" * 70)
    print("Link orphaned OWL modules to canonical provisions via owl:sameAs (#565)")
    print(f"  Mode: {'APPLY (writing changed files)' if write else 'DRY RUN (no files written)'}")
    print(f"  Canonical provision fragments scanned: {len(canonical)}")
    print("=" * 70)

    total_added = 0
    for fname in MODULE_FILES:
        path = KRR_DIR / fname
        if not path.exists():
            print(f"\n  {fname}: MISSING — skipped")
            continue
        doc, stats = link_module(path, canonical)
        if doc is None:
            print(f"\n  {fname}: UNREADABLE (LFS pointer / parse error) — skipped")
            continue
        total_added += stats["sameas_added"]
        verb = "added" if write else "would add"
        print(f"\n  {fname}:")
        print(f"    Sections scanned : {stats['sections_scanned']}")
        print(
            f"    owl:sameAs {verb:9}: {stats['sameas_added']}"
            f"   (already linked: {stats['already_linked']})"
        )
        sample = ", ".join(stats["unmatched_sample"])
        suffix = f"   e.g. {sample}" if sample else ""
        print(f"    Unmatched        : {stats['unmatched']}{suffix}")
        if write and stats["sameas_added"]:
            save_json(path, doc)

    print(f"\n  Total owl:sameAs {'added' if write else 'to add'}: {total_added}")
    if not write:
        print("  [DRY RUN] re-run with --apply to write the bridges.")
    elif total_added:
        print(
            "\n  NOTE: rebuild combined_ontology.jsonld so the load surface "
            "carries the new owl:sameAs edges."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
