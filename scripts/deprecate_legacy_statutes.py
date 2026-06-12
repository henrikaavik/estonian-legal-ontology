#!/usr/bin/env python3
"""Deprecate duplicate legacy statute peeps and re-point inbound refs (#426).

The corpus accumulated 37 *duplicate* legacy-statute act roots: orthography /
truncation / renaming variants of a canonical act that is already represented
by a fuller, current peep file (e.g. ``estleg:ALKS_Map_2026`` "Alkoholiseaduse
teemakaardistus" is a genitive-label variant of the canonical
``estleg:AS_Map_2026`` "Alkoholiseadus teemakaardistus"). The classification
lives in ``data/legacy_statute_decisions.json``: 37 ``deprecations`` (the
duplicates) and 132 ``keeps`` (sole representations / legal-succession
predecessors, deliberately untouched).

This migration applies the agreed ``deprecate`` policy offline, against the
already-committed corpus — no network, no regeneration:

1. **Mark the duplicate root.** On the act root node of each legacy peep file
   (the node whose ``@id`` equals the decision's ``rootIri``) set:

       "owl:deprecated": true
       "dcterms:isReplacedBy": {"@id": "<canonical rootIri>"}

   ``owl:deprecated`` is emitted as a **bare JSON boolean literal** — the house
   style for this exact predicate (the three pre-existing ``owl:deprecated``
   triples in ``riigikohus_schema.json`` / ``eelnoud_schema.json`` use bare
   ``true``, and ``estleg:isStatutoryDefault`` likewise). The value-object form
   ``{"@value": "true", "@type": "xsd:boolean"}`` is reserved for the
   generator-emitted ``estleg:isCurrentAmendment`` / ``estleg:isKov`` flags and
   is intentionally NOT used here. Both predicates are inserted immediately
   after ``@type`` so reruns are byte-stable. The ``owl`` and ``dcterms``
   context prefixes are ensured on the file's ``@context`` (every act peep
   already declares both; the guard is defensive and matches the canonical
   prefix URLs).

2. **Re-point inbound references corpus-wide.** Every ``*.json`` / ``*.jsonld``
   under ``krr_outputs/`` — EXCEPT the 37 legacy peep files themselves,
   ``INDEX.json``, ``combined_ontology.jsonld``, and the operational state
   files — is walked recursively; every JSON **string value that EXACTLY
   equals** a legacy ``rootIri`` is replaced with the canonical
   ``replacedByIri``. The match is *exact string equality only*, never a
   substring replace, so an IRI like ``estleg:PS_Map_2026`` can never corrupt
   the longer ``estleg:PSJKS_Map_2026``. Both ``@id`` object positions and
   plain string occurrences are covered (an ``@id`` value is itself just a
   string at a ``"@id"`` key). Only files that actually change are rewritten,
   preserving byte-stability everywhere else.

   The legacy peep files are excluded from the re-point so their internal
   self-references (root ``@id``, provision ``estleg:partOf`` back-edges, etc.)
   stay intact — the file is *kept on disk*, only marked deprecated.

Special case — legacy→legacy canonical (``ulikooli_peep.json``): its canonical
``ulikooliseadus_peep.json`` (``estleg:ÜKS_Map_2026``) is itself in the legacy
cohort but classified ``keep``. The decisions file maps UKS directly to ÜKS, and
no ``replacedByIri`` is ever itself a legacy ``rootIri`` (verified), so there is
no transitive chain to resolve — a single pass is correct and terminal.

Idempotent: a root already carrying ``owl:deprecated`` is left untouched, and
after the first pass no string equals any legacy ``rootIri`` anymore, so a
second run reports zero changes and rewrites nothing.

Run (offline, idempotent; operates on the real corpus):

    .venv/bin/python scripts/deprecate_legacy_statutes.py

Preview without writing:

    .venv/bin/python scripts/deprecate_legacy_statutes.py --dry-run
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from estleg_common import (
    REPO_ROOT,
    is_operational_state_file,
    iter_krr_jsonld_files,
    save_json,
)

DEFAULT_DECISIONS_PATH = REPO_ROOT / "data" / "legacy_statute_decisions.json"
DEFAULT_KRR_DIR = REPO_ROOT / "krr_outputs"

# Canonical context prefix URLs (must match estleg_common.CONTEXT / the act
# peep files). Used to ensure the deprecation predicates resolve.
OWL_PREFIX_IRI = "http://www.w3.org/2002/07/owl#"
DCTERMS_PREFIX_IRI = "http://purl.org/dc/terms/"

# Files that must never be re-pointed even though they live under krr_outputs/.
# The 37 legacy peep files are excluded dynamically (see build_repoint_map);
# these are the fixed aggregate artifacts.
REPOINT_EXCLUDED_BASENAMES: frozenset[str] = frozenset(
    {
        "INDEX.json",
        "combined_ontology.jsonld",
    }
)


# ---------------------------------------------------------------------------
# Decisions loading
# ---------------------------------------------------------------------------
def load_decisions(decisions_path: Path) -> list[dict]:
    """Return the ``deprecations`` list from the decisions file.

    Each entry carries at least ``file``, ``rootIri``, ``replacedByFile`` and
    ``replacedByIri``. The ``keeps`` section is intentionally ignored — those
    roots stay canonical and counted.
    """
    doc = json.loads(decisions_path.read_text(encoding="utf-8"))
    deprecations = doc.get("deprecations", [])
    if not isinstance(deprecations, list):
        raise ValueError(
            f"{decisions_path}: 'deprecations' must be a list, "
            f"got {type(deprecations).__name__}"
        )
    return deprecations


def verify_decisions_applied(
    decisions_path: Path,
    krr_dir: Path,
) -> tuple[int, list[str]]:
    """Check that every deprecation decision is applied on disk.

    Returns ``(checked, violations)`` where ``checked`` is the number of
    decision entries whose peep file exists and ``violations`` lists one
    message per root that is missing its ``owl:deprecated: true`` /
    ``dcterms:isReplacedBy`` marks (or whose root node cannot be found).

    A missing decisions file means there is nothing to enforce (pre-#426
    checkouts, fixture corpora) — ``(0, [])``. A missing peep file is not a
    violation either: a file that does not exist cannot leak into aggregate
    artifacts.
    """
    if not decisions_path.is_file():
        return 0, []
    violations: list[str] = []
    checked = 0
    for entry in load_decisions(decisions_path):
        path = krr_dir / entry["file"]
        if not path.is_file():
            continue
        checked += 1
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            violations.append(f"{entry['file']}: unreadable ({exc})")
            continue
        graph = doc.get("@graph", [])
        root = next(
            (
                n
                for n in graph
                if isinstance(n, dict) and n.get("@id") == entry["rootIri"]
            ),
            None,
        )
        if root is None:
            violations.append(
                f"{entry['file']}: root {entry['rootIri']} not found"
            )
            continue
        if root.get("owl:deprecated") is not True:
            violations.append(
                f"{entry['file']}: root {entry['rootIri']} lacks "
                f"owl:deprecated true"
            )
            continue
        replaced_by = root.get("dcterms:isReplacedBy")
        expected = {"@id": entry["replacedByIri"]}
        if replaced_by != expected:
            violations.append(
                f"{entry['file']}: root {entry['rootIri']} "
                f"dcterms:isReplacedBy is {replaced_by!r}, expected {expected!r}"
            )
    return checked, violations


def build_repoint_map(deprecations: list[dict]) -> dict[str, str]:
    """Map every legacy ``rootIri`` to its canonical ``replacedByIri``.

    No ``replacedByIri`` is itself a legacy ``rootIri`` (the cohort has no
    transitive chains), so this flat map is terminal: applying it once
    canonicalises every inbound reference.
    """
    mapping: dict[str, str] = {}
    for entry in deprecations:
        root_iri = entry["rootIri"]
        replaced_by = entry["replacedByIri"]
        if root_iri in mapping and mapping[root_iri] != replaced_by:
            raise ValueError(
                f"conflicting replacedByIri for {root_iri}: "
                f"{mapping[root_iri]} vs {replaced_by}"
            )
        mapping[root_iri] = replaced_by
    return mapping


# ---------------------------------------------------------------------------
# Step 1 — mark the duplicate root node
# ---------------------------------------------------------------------------
def _ensure_context_prefix(doc: dict, prefix: str, iri: str) -> bool:
    """Ensure ``@context`` maps ``prefix`` to ``iri``. Returns True if added.

    Only a dict-shaped ``@context`` is mutated (every act peep uses one). A
    missing or non-dict context is left as-is — the predicates still parse via
    the combined graph's context, and we never want to clobber an unexpected
    shape.
    """
    ctx = doc.get("@context")
    if not isinstance(ctx, dict):
        return False
    if ctx.get(prefix) == iri:
        return False
    ctx[prefix] = iri
    return True


def _insert_after_type(node: dict, additions: dict) -> None:
    """Insert ``additions`` into ``node`` right after the ``@type`` key.

    Rebuilds the dict preserving insertion order so the new predicates land in
    a deterministic slot (immediately after ``@type``), keeping reruns
    byte-stable. Keys already present in ``node`` are skipped in ``additions``
    so we never duplicate or reorder existing data.
    """
    pending = {k: v for k, v in additions.items() if k not in node}
    if not pending:
        return
    rebuilt: dict = {}
    inserted = False
    for key, value in node.items():
        rebuilt[key] = value
        if key == "@type":
            rebuilt.update(pending)
            inserted = True
    if not inserted:
        # No @type key (shouldn't happen for an act root) — append at the end
        # rather than dropping the predicates.
        rebuilt.update(pending)
    node.clear()
    node.update(rebuilt)


def mark_deprecated_root(doc: dict, root_iri: str, replaced_by_iri: str) -> bool:
    """Mark the act root (``@id == root_iri``) as deprecated in place.

    Returns True iff the document was modified. Idempotent: a root that already
    carries ``owl:deprecated`` is treated as done (no write). Also ensures the
    ``owl`` / ``dcterms`` context prefixes are declared.
    """
    graph = doc.get("@graph", [])
    if not isinstance(graph, list):
        return False
    root = next(
        (n for n in graph if isinstance(n, dict) and n.get("@id") == root_iri),
        None,
    )
    if root is None:
        return False
    if root.get("owl:deprecated") is True and "dcterms:isReplacedBy" in root:
        # Already marked — idempotent no-op (do not re-touch the context either,
        # _ensure_context_prefix is a no-op when the prefix already matches).
        return False

    changed = False
    # Context prefixes first so the predicates resolve.
    changed |= _ensure_context_prefix(doc, "owl", OWL_PREFIX_IRI)
    changed |= _ensure_context_prefix(doc, "dcterms", DCTERMS_PREFIX_IRI)

    # Deterministic placement: right after @type. ``owl:deprecated`` is a bare
    # JSON boolean literal (house style for this predicate); isReplacedBy is an
    # IRI reference object.
    _insert_after_type(
        root,
        {
            "owl:deprecated": True,
            "dcterms:isReplacedBy": {"@id": replaced_by_iri},
        },
    )
    return True


# ---------------------------------------------------------------------------
# Step 2 — exact-match re-point of inbound references
# ---------------------------------------------------------------------------
def repoint_value(value, repoint_map: dict[str, str]) -> tuple[object, int]:
    """Recursively re-point exact-match IRI strings inside ``value``.

    Returns ``(new_value, replacement_count)``. Replacement fires ONLY on a
    string that is *exactly equal* to a legacy ``rootIri`` (dict key
    membership) — never a substring match — so a short IRI can never corrupt a
    longer IRI that merely shares it as a prefix. ``@id`` object values are
    plain strings at the ``"@id"`` key and are therefore covered by the same
    string branch; no special-casing is required.
    """
    if isinstance(value, str):
        replacement = repoint_map.get(value)
        if replacement is not None and replacement != value:
            return replacement, 1
        return value, 0
    if isinstance(value, dict):
        count = 0
        for key, item in value.items():
            new_item, n = repoint_value(item, repoint_map)
            if n:
                value[key] = new_item
                count += n
        return value, count
    if isinstance(value, list):
        count = 0
        for idx, item in enumerate(value):
            new_item, n = repoint_value(item, repoint_map)
            if n:
                value[idx] = new_item
                count += n
        return value, count
    return value, 0


# ---------------------------------------------------------------------------
# File-level processing
# ---------------------------------------------------------------------------
def process_legacy_file(
    path: Path,
    root_iri: str,
    replaced_by_iri: str,
    *,
    dry_run: bool,
) -> bool:
    """Mark the legacy peep file's root deprecated. Returns True if it changed.

    The legacy file is deliberately NOT re-pointed: its internal
    self-references stay so the kept-on-disk file remains internally
    consistent.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        return False
    changed = mark_deprecated_root(doc, root_iri, replaced_by_iri)
    if changed and not dry_run:
        save_json(path, doc)
    return changed


def process_repoint_file(
    path: Path,
    repoint_map: dict[str, str],
    *,
    dry_run: bool,
) -> int:
    """Re-point exact-match references in one corpus file. Returns the count.

    Only rewrites the file when at least one replacement occurred, so
    byte-stability is preserved everywhere a legacy IRI is not referenced.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    _, count = repoint_value(doc, repoint_map)
    if count and not dry_run:
        save_json(path, doc)
    return count


def iter_repoint_files(
    krr_dir: Path,
    legacy_paths: set[Path],
) -> list[Path]:
    """Return the corpus files eligible for re-pointing, deterministically sorted.

    Routes through ``iter_krr_jsonld_files`` (the shared anti-drift enumerator,
    which already excludes ``OPERATIONAL_STATE_FILES``) and additionally drops
    the 37 legacy peep files, ``INDEX.json`` and ``combined_ontology.jsonld``.
    """
    eligible: list[Path] = []
    for path in iter_krr_jsonld_files(krr_dir):
        if path in legacy_paths:
            continue
        if path.name in REPOINT_EXCLUDED_BASENAMES:
            continue
        # iter_krr_jsonld_files already filters operational state files, but
        # re-assert defensively in case the enumerator is ever swapped out.
        if is_operational_state_file(path):
            continue
        eligible.append(path)
    return eligible


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run(
    decisions_path: Path,
    krr_dir: Path,
    *,
    dry_run: bool,
) -> dict:
    """Execute the deprecation + re-point migration. Returns a summary dict."""
    deprecations = load_decisions(decisions_path)
    repoint_map = build_repoint_map(deprecations)

    # Resolve the 37 legacy peep file paths (sorted for deterministic output).
    legacy_entries = sorted(deprecations, key=lambda e: e["file"])
    legacy_paths = {krr_dir / entry["file"] for entry in legacy_entries}

    # --- Step 1: mark duplicate roots deprecated ---
    roots_deprecated = 0
    missing_legacy_files: list[str] = []
    for entry in legacy_entries:
        path = krr_dir / entry["file"]
        if not path.is_file():
            missing_legacy_files.append(entry["file"])
            continue
        if process_legacy_file(
            path,
            entry["rootIri"],
            entry["replacedByIri"],
            dry_run=dry_run,
        ):
            roots_deprecated += 1

    # --- Step 2: corpus-wide exact-match re-point ---
    files_repointed = 0
    total_replacements = 0
    per_file_counts: dict[str, int] = {}
    for path in iter_repoint_files(krr_dir, legacy_paths):
        count = process_repoint_file(path, repoint_map, dry_run=dry_run)
        if count:
            files_repointed += 1
            total_replacements += count
            per_file_counts[str(path.relative_to(krr_dir))] = count

    return {
        "dry_run": dry_run,
        "legacy_entries": len(legacy_entries),
        "roots_deprecated": roots_deprecated,
        "missing_legacy_files": missing_legacy_files,
        "files_repointed": files_repointed,
        "total_replacements": total_replacements,
        "per_file_counts": per_file_counts,
    }


def print_summary(summary: dict) -> None:
    """Print the deterministic migration summary."""
    dry = summary["dry_run"]
    mark_verb = "would deprecate" if dry else "deprecated"
    point_verb = "would re-point" if dry else "re-pointed"

    print("=" * 70)
    print("Deprecate duplicate legacy statutes + re-point inbound refs (#426)")
    if dry:
        print("  MODE: --dry-run (no files written)")
    print("=" * 70)
    print(f"  Legacy decisions:        {summary['legacy_entries']}")
    print(f"  Roots {mark_verb}:   {summary['roots_deprecated']}")
    print(f"  Files {point_verb}:    {summary['files_repointed']}")
    print(f"  Total IRI replacements:  {summary['total_replacements']}")

    if summary["missing_legacy_files"]:
        print("\n  WARNING: legacy peep file(s) not found on disk:")
        for name in summary["missing_legacy_files"]:
            print(f"    - {name}")

    per_file = summary["per_file_counts"]
    if per_file:
        print("\n  Per-file replacement counts:")
        for rel_path in sorted(per_file):
            print(f"    {per_file[rel_path]:>5}  {rel_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--decisions",
        type=Path,
        default=DEFAULT_DECISIONS_PATH,
        help=(
            "Path to legacy_statute_decisions.json "
            "(default: data/legacy_statute_decisions.json)."
        ),
    )
    parser.add_argument(
        "--krr-dir",
        type=Path,
        default=DEFAULT_KRR_DIR,
        help="Corpus root to deprecate/re-point (default: <repo>/krr_outputs).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report per-file change counts without writing any file.",
    )
    args = parser.parse_args(argv)

    if not args.decisions.is_file():
        print(f"ERROR: decisions file not found: {args.decisions}")
        return 1
    if not args.krr_dir.is_dir():
        print(f"ERROR: not a directory: {args.krr_dir}")
        return 1

    summary = run(args.decisions, args.krr_dir, dry_run=args.dry_run)
    print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
