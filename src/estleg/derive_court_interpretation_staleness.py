#!/usr/bin/env python3
"""Stamp court-decision interpretation staleness from version sidecars (#618).

Issue #618 ("lightweight" option): a consumer reading a Riigikohus precedent has
no cheap way to learn that the statute text the court interpreted has since been
amended — i.e. whether the ruling construes a redaction that is no longer in
force. The answer already ships in the per-provision redaction chains under
``krr_outputs/provision_versions/*.jsonld`` (authoritative Riigi Teataja dates);
this derives it onto each ``estleg:CourtDecision`` peep so it is queryable
*without* loading the ~211k-node version layer.

For every court decision ``D`` (decision date ``dd``) it inspects the provisions
``D`` interprets (``estleg:interpretsLaw``) that have version data and stamps:

  * ``estleg:interpretsVersion`` — OBJECT property; for each interpreted provision
    the redaction in force at ``dd`` (``validFrom <= dd`` and, if bounded,
    ``validTo >= dd``; the greatest qualifying ``validFrom`` wins). Collected
    across all interpreted provisions, deduped and sorted for determinism. This is
    what the court actually construed.
  * ``estleg:interpretationOutdated`` — ``xsd:boolean`` literal; ``true`` iff ANY
    interpreted (versioned) provision gained a later redaction (``validFrom > dd``)
    after the ruling — the law the precedent reads has since changed.
  * ``estleg:earliestSupersedingDate`` — ``xsd:date`` literal; only when outdated:
    the earliest such post-ruling ``validFrom``. Omitted when not outdated.

``estleg:interpretsVersion`` points into the SEPARATE ``provision_versions`` load
surface (the hybrid release split of #561 — the version layer is far too large to
fold into the flagship/combined artifact), so the integrator STRIPS it from the
combined graph at build time; it is registered in
``estleg_common.COMBINED_STRIPPED_PREDICATES`` (alongside ``estleg:hasVersion``).
A consumer that needs to resolve the version IRIs loads the full surface, where
the edge and its target co-resolve.

This is a surgical, offline, deterministic post-process (no network). Dates are
compared as plain ISO ``YYYY-MM-DD`` strings (lexical order == chronological);
malformed/missing dates are skipped gracefully. Git-LFS pointer stand-ins are
skipped (an LFS pointer is not parseable JSON).

Idempotent: every run first DELETES the three derived props, then recomputes, so
re-running on already-stamped data is a no-op diff — and a decision that no longer
has version-backed interpretations is cleaned up rather than left stale.

Dry-run is the DEFAULT; pass ``--apply`` to write (atomic ``save_json``).
``--dry-run`` is accepted as an explicit no-op (and overrides ``--apply``):

    python3 scripts/derive_court_interpretation_staleness.py --apply
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from estleg.estleg_common import KRR_DIR, save_json

VERSION_DIR = KRR_DIR / "provision_versions"
RIIGIKOHUS_DIR = KRR_DIR / "riigikohus"

# The three derived properties this script owns end-to-end. Recomputed from
# scratch every run (strip-then-recompute), so re-stamping is a no-op diff.
PROP_VERSION = "estleg:interpretsVersion"
PROP_OUTDATED = "estleg:interpretationOutdated"
PROP_SUPERSEDING = "estleg:earliestSupersedingDate"
OUTPUT_PROPS = (PROP_VERSION, PROP_OUTDATED, PROP_SUPERSEDING)

# A version row: (validFrom, validTo_or_None, versionIRI).
VersionRow = tuple[str, str | None, str]


def _date_value(value: object) -> str | None:
    """Extract a ``YYYY-MM-DD`` string from a JSON-LD literal (or bare string)."""
    if isinstance(value, dict):
        v = value.get("@value")
        return v if isinstance(v, str) else None
    return value if isinstance(value, str) else None


def _types(node: dict) -> list:
    types = node.get("@type", [])
    return [types] if isinstance(types, str) else types


def _load_jsonld(path: Path) -> dict | None:
    """Load a JSON-LD doc, skipping Git-LFS pointer stand-ins and junk.

    Some load-surface artifacts ship via Git LFS; a not-pulled pointer file
    starts with the LFS spec banner and is not parseable JSON. Returns ``None``
    for pointers, unreadable files, malformed JSON, or non-object roots.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if text.startswith("version https://git-lfs.github.com/spec/v1"):
        return None
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return None
    return doc if isinstance(doc, dict) else None


def build_version_index() -> dict[str, list[VersionRow]]:
    """Index every ``ProvisionVersion`` by the provision it redacts.

    Returns ``provisionIRI -> sorted list of (validFrom, validTo_or_None,
    versionIRI)``. Versions without a parseable ``versionValidFrom`` or ``@id``
    are dropped (they cannot anchor a date comparison). The per-provision list is
    sorted so ``(validFrom, validTo, versionIRI)`` ordering is deterministic.
    """
    index: dict[str, list[VersionRow]] = defaultdict(list)
    for path in sorted(VERSION_DIR.glob("*.jsonld")):
        doc = _load_jsonld(path)
        if doc is None:
            continue
        for node in doc.get("@graph", []):
            if not isinstance(node, dict):
                continue
            if "estleg:ProvisionVersion" not in _types(node):
                continue
            target = node.get("estleg:versionOf")
            if not (isinstance(target, dict) and isinstance(target.get("@id"), str)):
                continue
            valid_from = _date_value(node.get("estleg:versionValidFrom"))
            version_iri = node.get("@id")
            if not valid_from or not isinstance(version_iri, str):
                continue
            valid_to = _date_value(node.get("estleg:versionValidTo"))
            index[target["@id"]].append((valid_from, valid_to, version_iri))
    for rows in index.values():
        rows.sort()
    return index


def _interpreted_provisions(node: dict) -> list[str]:
    """The provision IRIs a decision interprets (``interpretsLaw`` dict or list)."""
    raw = node.get("estleg:interpretsLaw")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [
        ref["@id"]
        for ref in raw
        if isinstance(ref, dict) and isinstance(ref.get("@id"), str)
    ]


@dataclass
class NodeResult:
    """Outcome of (re)deriving one court-decision node."""

    changed: bool          # did the three derived props' state change vs before?
    version_backed: bool   # had a decision date AND >=1 interpreted versioned provision
    outdated: bool         # interpretationOutdated value
    links: int             # number of interpretsVersion edges emitted


def derive_node(node: dict, index: dict[str, list[VersionRow]]) -> NodeResult:
    """Recompute the three derived props on a court-decision node, in place.

    Strips the props first (idempotency), then recomputes from the version index.
    A node with no version-backed interpretations is left clean (props removed).
    """
    before = {k: node[k] for k in OUTPUT_PROPS if k in node}
    for k in OUTPUT_PROPS:
        node.pop(k, None)

    decision_date = _date_value(node.get("estleg:decisionDate"))
    versioned = [p for p in _interpreted_provisions(node) if p in index]
    if not decision_date or not versioned:
        # Nothing derivable: leave props removed (cleanup is the change, if any).
        return NodeResult(changed=bool(before), version_backed=False, outdated=False, links=0)

    in_force: set[str] = set()
    superseding: list[str] = []
    for provision in versioned:
        # Version in force at dd: greatest validFrom <= dd whose validTo (if set)
        # has not passed. Rows are validFrom-sorted, so the last qualifying row
        # wins. A later redaction (validFrom > dd) means the provision changed.
        in_force_iri: str | None = None
        for valid_from, valid_to, version_iri in index[provision]:
            if valid_from > decision_date:
                superseding.append(valid_from)
            elif valid_to is None or valid_to >= decision_date:
                in_force_iri = version_iri
        if in_force_iri is not None:
            in_force.add(in_force_iri)

    outdated = bool(superseding)
    node[PROP_VERSION] = [{"@id": iri} for iri in sorted(in_force)]
    node[PROP_OUTDATED] = {"@value": outdated, "@type": "xsd:boolean"}
    if outdated:
        node[PROP_SUPERSEDING] = {"@value": min(superseding), "@type": "xsd:date"}

    after = {k: node[k] for k in OUTPUT_PROPS if k in node}
    return NodeResult(
        changed=before != after,
        version_backed=True,
        outdated=outdated,
        links=len(in_force),
    )


def process_file(path: Path, index: dict[str, list[VersionRow]], apply: bool) -> Counter:
    """(Re)derive staleness on one court peep; write atomically iff changed."""
    stats: Counter = Counter()
    doc = _load_jsonld(path)
    if doc is None:
        return stats
    file_changed = False
    for node in doc.get("@graph", []):
        if not isinstance(node, dict) or "estleg:CourtDecision" not in _types(node):
            continue
        stats["scanned"] += 1
        result = derive_node(node, index)
        if result.version_backed:
            stats["version_backed"] += 1
            stats["links"] += result.links
            if result.outdated:
                stats["outdated"] += 1
        if result.changed:
            stats["nodes_changed"] += 1
            file_changed = True
    if file_changed:
        stats["files_changed"] += 1
        if apply:
            save_json(path, doc)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Write changed peeps atomically (default: dry-run, report only).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report without writing (the default; an explicit no-op that overrides --apply).",
    )
    args = parser.parse_args(argv)
    apply = args.apply and not args.dry_run

    index = build_version_index()
    provisions_with_versions = len(index)

    peeps = sorted(RIIGIKOHUS_DIR.glob("*_peep.json"))
    total: Counter = Counter()
    for path in peeps:
        total.update(process_file(path, index, apply))

    print("=" * 70)
    print("Derive court-decision interpretation staleness from version sidecars (#618)")
    print(f"  Provisions with version data: {provisions_with_versions:,}")
    print(f"  Court peeps scanned: {len(peeps)}")
    print(f"  Mode: {'APPLY (writing changed peeps)' if apply else 'DRY RUN (no writes)'}")
    print("=" * 70)
    print(f"  Court decisions scanned:                  {total['scanned']:,}")
    print(f"  ... with version-backed interpretations:  {total['version_backed']:,}")
    print(f"  ... flagged interpretationOutdated:       {total['outdated']:,}")
    print(f"  interpretsVersion links emitted:          {total['links']:,}")
    verb = "changed" if apply else "would change"
    print(f"  Court-decision nodes {verb}:             {total['nodes_changed']:,}")
    print(f"  Peeps {verb}:                            {total['files_changed']:,}")
    if not apply and total["nodes_changed"]:
        print("\n  NOTE: dry run — re-run with --apply to write these stamps.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
