#!/usr/bin/env python3
"""Resolve / drop genitive dead-tokens in court ``referencedLaw`` (#596).

``generate_court_decisions.py`` (``detect_referenced_laws``) emits
``match.group(1).lower()`` — the raw Estonian **genitive** law-name
fragment (e.g. ``"liiklusseaduse"``) — verbatim as
``estleg:referencedLaw``, instead of first resolving it through
``FULLNAME_GENITIVE`` to a canonical abbreviation the way the IRI
resolver (``extract_court_provision_links``) does. The genitive forms
therefore never join to an ``interpretsLaw`` IRI: they are dead tokens.

This migration normalises the committed riigikohus decision peeps
offline, mirroring the resolver exactly:

  * value already a canonical abbreviation (``KarS``, ``TsMS`` …)
    → kept;
  * value a case-variant of a known abbreviation (``Kars`` → ``KarS``)
    → rewritten to the canonical case;
  * value a genitive whose lowercase form is in ``FULLNAME_GENITIVE``
    (``liiklusseaduse`` → ``LS``, ``Põhiseaduse`` → ``PS``)
    → rewritten to the mapped abbreviation;
  * any other value (a genitive with no table entry, e.g.
    ``kalapüügiseaduse``, ``jahiseaduse``) → **dropped**, because it
    cannot resolve to a law IRI under the tables the resolver uses.

Resolution mirrors ``extract_court_provision_links`` Pattern 1/2:
``KNOWN_ABBREVIATIONS`` is matched case-insensitively (the abbreviation
table), ``FULLNAME_GENITIVE`` is matched on the lower-cased value (the
``re.IGNORECASE`` genitive pattern lower-cases its match before lookup).

Deduplication: if dropping/rewriting collapses a node's
``referencedLaw`` list to duplicates, the list is de-duplicated
preserving first-occurrence order. A node whose list becomes empty has
the ``estleg:referencedLaw`` key removed entirely (an empty list is not
an honest "references nothing"); its ``estleg:interpretsLaw`` IRI edges,
if any, are untouched.

Safety / scope:
  * Only ``estleg:referencedLaw`` string values are touched; IRI-valued
    ``interpretsLaw`` edges and every other field are left as-is.
  * Idempotent: a canonical abbreviation is kept verbatim and is not a
    genitive, so a re-run reports 0 changes.
  * Atomic writes via ``estleg_common.save_json`` (tempfile + os.replace).
  * ``--dry-run`` reports counts without writing.

NOTE: this fixes the *committed data*. The generator emit at
``generate_court_decisions.py`` is patched in the same change so a future
court-corpus regen does not reintroduce the dead tokens; that regen is
network-backed and is **not** run here.

Run (offline, idempotent):

    .venv/bin/python scripts/normalize_court_referenced_law.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from estleg_common import FULLNAME_GENITIVE, KNOWN_ABBREVIATIONS, KRR_DIR, save_json

REFERENCED_LAW_KEY = "estleg:referencedLaw"

# Case-folded abbreviation lookup → canonical-cased abbreviation, mirroring
# the resolver's case-insensitive Pattern 1 match (so ``Kars`` → ``KarS``).
_ABBREV_BY_CASEFOLD: dict[str, str] = {a.casefold(): a for a in KNOWN_ABBREVIATIONS}


def resolve_referenced_law(value: str) -> str | None:
    """Resolve one ``referencedLaw`` string to a canonical abbreviation.

    Returns the canonical abbreviation to keep/rewrite to, or ``None``
    when the value is an unresolvable dead-token that should be dropped.
    Mirrors ``extract_court_provision_links`` resolution order:
    abbreviation (case-insensitive) first, then genitive via
    ``FULLNAME_GENITIVE`` on the lower-cased value.
    """
    if not isinstance(value, str):
        return None
    canonical = _ABBREV_BY_CASEFOLD.get(value.casefold())
    if canonical is not None:
        return canonical
    return FULLNAME_GENITIVE.get(value.lower())


def normalize_referenced_law_value(value: object) -> tuple[object | None, int, int]:
    """Normalize a node's ``referencedLaw`` value.

    Accepts the scalar-or-list shape and returns
    ``(new_value, rewritten, dropped)`` where ``new_value`` is:
      * the de-duplicated, resolved list (or scalar) to store, or
      * ``None`` when nothing resolves and the key should be removed.
    ``rewritten`` counts string values whose canonical form differs from
    the original; ``dropped`` counts unresolvable values removed.
    """
    was_list = isinstance(value, list)
    items = list(value) if was_list else [value]

    resolved: list[str] = []
    rewritten = 0
    dropped = 0
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, str):
            # Non-string entries are left as-is (defensive; not seen in data).
            if isinstance(item, (dict, list)):
                key = json.dumps(item, sort_keys=True, ensure_ascii=False)
            else:
                key = repr(item)
            if key not in seen:
                seen.add(key)
                resolved.append(item)  # type: ignore[arg-type]
            continue
        canonical = resolve_referenced_law(item)
        if canonical is None:
            dropped += 1
            continue
        if canonical != item:
            rewritten += 1
        if canonical not in seen:
            seen.add(canonical)
            resolved.append(canonical)

    if not resolved:
        return None, rewritten, dropped
    # Preserve the original container shape (scalar stays scalar when it
    # resolves to a single value).
    if not was_list and len(resolved) == 1:
        return resolved[0], rewritten, dropped
    return resolved, rewritten, dropped


def normalize_doc(doc: dict) -> tuple[int, int, int]:
    """Normalize all ``referencedLaw`` values in one doc, in place.

    Returns ``(values_rewritten, values_dropped, keys_removed)``.
    """
    graph = doc.get("@graph", [])
    if not isinstance(graph, list):
        return 0, 0, 0
    rewritten = dropped = keys_removed = 0
    for node in graph:
        if not isinstance(node, dict):
            continue
        if REFERENCED_LAW_KEY not in node:
            continue
        original = node[REFERENCED_LAW_KEY]
        new_value, n_rw, n_dr = normalize_referenced_law_value(original)
        rewritten += n_rw
        dropped += n_dr
        if new_value is None:
            del node[REFERENCED_LAW_KEY]
            keys_removed += 1
        else:
            node[REFERENCED_LAW_KEY] = new_value
    return rewritten, dropped, keys_removed


def process_file(path: Path, dry_run: bool) -> tuple[int, int, int]:
    """Normalize one decision peep. Returns (rewritten, dropped, keys_removed)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    rewritten, dropped, keys_removed = normalize_doc(doc)
    if (rewritten or dropped or keys_removed) and not dry_run:
        save_json(path, doc)
    return rewritten, dropped, keys_removed


def iter_court_files(krr_dir: Path) -> list[Path]:
    """Return the riigikohus decision peeps (the only source of the defect)."""
    rk_dir = krr_dir / "riigikohus"
    if not rk_dir.exists():
        return []
    return sorted(
        rk_dir.glob("riigikohus_*_peep.json"),
        key=lambda p: p.as_posix().casefold(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--krr-dir",
        type=Path,
        default=KRR_DIR,
        help="Root krr_outputs directory (default: repo krr_outputs).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing any file.",
    )
    args = parser.parse_args(argv)

    files = iter_court_files(args.krr_dir)

    print("=" * 70)
    print(f"Normalize court {REFERENCED_LAW_KEY} genitive dead-tokens (#596)")
    print(f"  Decision peeps scanned: {len(files)}")
    print("=" * 70)

    files_changed = 0
    total_rewritten = 0
    total_dropped = 0
    total_keys_removed = 0
    for path in files:
        rewritten, dropped, keys_removed = process_file(path, args.dry_run)
        if rewritten or dropped or keys_removed:
            files_changed += 1
            total_rewritten += rewritten
            total_dropped += dropped
            total_keys_removed += keys_removed

    verb = "would change" if args.dry_run else "changed"
    print(f"\n  Files {verb}: {files_changed}")
    print(f"  Values rewritten (genitive/case-variant -> abbrev): {total_rewritten}")
    print(f"  Values dropped (unresolvable dead-tokens): {total_dropped}")
    print(f"  Keys removed (list emptied): {total_keys_removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
