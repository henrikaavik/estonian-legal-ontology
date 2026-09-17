#!/usr/bin/env python3
"""Repair corrupt amendment dates + the ``isCurrentAmendment`` flag (#587).

Offline, idempotent, surgical post-process over ``krr_outputs/amendments/*.json``
(the amendment-chain documents). It fixes the three date defects verified for
issue #587 **without** re-running the network-free-but-heavy amendment
generator:

1. **Corrupt / implausible dates win "current".** Riigi Teataja source XML
   carries typo years far outside any real legislative timeline (verified:
   ``2918-10-17``, ``3006-03-02``) and near-future stamps. Such a date sorts
   last and wrongly wins ``estleg:isCurrentAmendment``. This script drops a
   ``estleg:amendmentDate`` / ``estleg:entryIntoForce`` whose year is outside
   ``[_MIN_PLAUSIBLE_YEAR, current_year + _MAX_FUTURE_YEAR_SLACK]``.

2. **Impossible orderings.** An ``estleg:entryIntoForce`` that precedes its
   ``estleg:amendmentDate`` by more than ``_MAX_EIF_BEFORE_DATE_DAYS`` days is
   physically impossible (a law in force ~a year before adoption). The
   conflicting ``entryIntoForce`` is nulled (the adoption date is the more
   trustworthy anchor — an act always exists before it takes effect).

3. **Wrong current-amendment flag.** After (1)/(2) the
   ``estleg:isCurrentAmendment`` flag is recomputed: it is removed from every
   ``estleg:AmendmentEvent`` node in the chain and re-stamped on the one with
   the latest VALID (still-dated) effect date — ``entryIntoForce`` when
   present, else ``amendmentDate``. A chain with no validly-dated event ends up
   with no flag (rather than a guessed one).

The matching thresholds mirror ``generate_amendment_history.py`` so a
subsequent generator re-run is a no-op against this script's output.

Scope / safety:
  * Only ``estleg:AmendmentEvent`` nodes are touched. ``estleg:ProposedAmendment``
    nodes (draft-derived) never carry these properties and are skipped.
  * ``estleg:amendingAct`` (0% coverage, #587) is NOT backfilled here: the
    amending-act identifier was discarded by the generator before it reached
    these files, so it cannot be recovered from the amendment chain alone. The
    generator now emits it from the source ``<muutmismarge>`` ``akt_viide`` /
    ``rt_reference``; populating the data needs an offline generator re-run.
  * Idempotent: a file already clean is a no-op (0 changes); a re-run reports 0.
  * Atomic writes via ``estleg_common.save_json`` (tempfile + os.replace).
  * Git-LFS-pointer guard: a file whose first line is the LFS spec marker is
    skipped (never rewrite a pointer stub into real content).
  * ``--dry-run`` reports counts without writing.

Run (offline, idempotent):

    .venv/bin/python scripts/fix_amendment_dates_587.py
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from estleg.estleg_common import REPO_ROOT, save_json

DEFAULT_AMENDMENTS_DIR = REPO_ROOT / "krr_outputs" / "amendments"

LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"

AMENDMENT_EVENT_TYPE = "estleg:AmendmentEvent"
DATE_KEY = "estleg:amendmentDate"
EIF_KEY = "estleg:entryIntoForce"
CURRENT_KEY = "estleg:isCurrentAmendment"

# Plausible-year window — kept in lock-step with generate_amendment_history.py.
_MIN_PLAUSIBLE_YEAR = 1990
_MAX_FUTURE_YEAR_SLACK = 2

# An entry-into-force more than this many days BEFORE the adoption date is
# physically impossible (mirrors generate_amendment_history._MAX_EIF_BEFORE_DATE_DAYS).
_MAX_EIF_BEFORE_DATE_DAYS = 90

# A future-dated event sorts last and wins "current"; an undated one must never
# win. Sentinels bracket the real YYYY-MM-DD range for the current-flag sort.
_SORT_SENTINEL_UNDATED = "0000-00-00"


def _max_plausible_year() -> int:
    return date.today().year + _MAX_FUTURE_YEAR_SLACK


def _typed_date_value(node: dict, key: str) -> str | None:
    """Return the ``@value`` of a typed-date property, or ``None``."""
    obj = node.get(key)
    if isinstance(obj, dict):
        val = obj.get("@value")
        return val if isinstance(val, str) and val else None
    return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def _year_is_plausible(value: str) -> bool:
    parsed = _parse_iso(value)
    if parsed is None:
        # Non-ISO shapes are left untouched here (the generator owns parsing);
        # treat as "not implausible" so we never strip a value we can't judge.
        return True
    return _MIN_PLAUSIBLE_YEAR <= parsed.year <= _max_plausible_year()


def _is_amendment_event(node: dict) -> bool:
    types = node.get("@type", [])
    if isinstance(types, str):
        types = [types]
    return AMENDMENT_EVENT_TYPE in types


def _days_eif_before_date(node: dict) -> int | None:
    """``(amendmentDate - entryIntoForce)`` in days, or ``None`` if unknown.

    A positive result means ``entryIntoForce`` precedes ``amendmentDate`` by
    that many days (the impossible-inversion direction).
    """
    d_adopt = _parse_iso(_typed_date_value(node, DATE_KEY))
    d_eif = _parse_iso(_typed_date_value(node, EIF_KEY))
    if d_adopt is None or d_eif is None:
        return None
    return (d_adopt - d_eif).days


def _event_sort_value(node: dict) -> str:
    """Latest-effect sort key for an event, mirroring the generator.

    ``entryIntoForce`` first, then ``amendmentDate``; a node with neither valid
    date sorts FIRST (low sentinel) so it can never be flagged current.
    """
    eif = _typed_date_value(node, EIF_KEY) or ""
    adopt = _typed_date_value(node, DATE_KEY) or ""
    return (eif or adopt) or _SORT_SENTINEL_UNDATED


def _has_valid_date(node: dict) -> bool:
    return bool(
        _typed_date_value(node, EIF_KEY) or _typed_date_value(node, DATE_KEY)
    )


class FixStats:
    """Mutable per-run tally."""

    __slots__ = (
        "current_flags_added",
        "current_flags_removed",
        "files_changed",
        "files_scanned",
        "files_skipped_lfs",
        "future_dates_dropped",
        "future_eif_dropped",
        "inversions_nulled",
    )

    def __init__(self) -> None:
        self.files_scanned = 0
        self.files_changed = 0
        self.files_skipped_lfs = 0
        self.future_dates_dropped = 0
        self.future_eif_dropped = 0
        self.inversions_nulled = 0
        self.current_flags_removed = 0
        self.current_flags_added = 0


def fix_doc(doc: dict, stats: FixStats) -> bool:
    """Repair one chain document in place. Returns True when it changed.

    Mutates ``stats`` with the per-defect counts. The three passes run in
    order so the recomputed ``isCurrentAmendment`` reflects the cleaned dates.
    """
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return False

    events = [
        node
        for node in graph
        if isinstance(node, dict) and _is_amendment_event(node)
    ]
    if not events:
        return False

    changed = False

    # Pass 1: drop implausible (corrupt / far-future) dates.
    for node in events:
        adopt = _typed_date_value(node, DATE_KEY)
        if adopt is not None and not _year_is_plausible(adopt):
            del node[DATE_KEY]
            stats.future_dates_dropped += 1
            changed = True
        eif = _typed_date_value(node, EIF_KEY)
        if eif is not None and not _year_is_plausible(eif):
            del node[EIF_KEY]
            stats.future_eif_dropped += 1
            changed = True

    # Pass 2: null an entry-into-force that precedes adoption impossibly.
    for node in events:
        delta = _days_eif_before_date(node)
        if delta is not None and delta > _MAX_EIF_BEFORE_DATE_DAYS:
            del node[EIF_KEY]
            stats.inversions_nulled += 1
            changed = True

    # Pass 3: recompute isCurrentAmendment over the cleaned, validly-dated
    # events. Clear every existing flag first, then re-stamp the single latest
    # validly-dated event (if any). The target is selected deterministically:
    # by (effect-date asc, then @id) so ties resolve stably and reruns are
    # idempotent.
    dated_events = [node for node in events if _has_valid_date(node)]
    target: dict | None = None
    if dated_events:
        target = max(
            dated_events,
            key=lambda n: (_event_sort_value(n), str(n.get("@id", ""))),
        )

    for node in events:
        has_flag = CURRENT_KEY in node
        if node is target:
            if not has_flag:
                node[CURRENT_KEY] = {"@value": "true", "@type": "xsd:boolean"}
                stats.current_flags_added += 1
                changed = True
        elif has_flag:
            del node[CURRENT_KEY]
            stats.current_flags_removed += 1
            changed = True

    return changed


def _is_lfs_pointer(path: Path) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            first = fh.readline()
    except OSError:
        return False
    return first.startswith(LFS_POINTER_PREFIX)


def process_file(path: Path, stats: FixStats, *, dry_run: bool) -> None:
    stats.files_scanned += 1
    if _is_lfs_pointer(path):
        stats.files_skipped_lfs += 1
        return
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if fix_doc(doc, stats):
        stats.files_changed += 1
        if not dry_run:
            save_json(path, doc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--amendments-dir",
        type=Path,
        default=DEFAULT_AMENDMENTS_DIR,
        help=(
            "Directory of amendments_*.json chain files to repair "
            "(default: krr_outputs/amendments)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing any file.",
    )
    args = parser.parse_args(argv)

    amendments_dir: Path = args.amendments_dir
    if not amendments_dir.is_dir():
        print(f"ERROR: not a directory: {amendments_dir}")
        return 1

    print("=" * 70)
    print("Repair corrupt amendment dates + isCurrentAmendment flag (#587)")
    print(f"  Directory: {amendments_dir}")
    print(f"  Plausible year window: [{_MIN_PLAUSIBLE_YEAR}, {_max_plausible_year()}]")
    if args.dry_run:
        print("  MODE: dry-run (no writes)")
    print("=" * 70)

    stats = FixStats()
    for path in sorted(amendments_dir.glob("amendments_*.json")):
        process_file(path, stats, dry_run=args.dry_run)

    verb = "would change" if args.dry_run else "changed"
    print(f"\n  Files scanned:            {stats.files_scanned}")
    print(f"  Files skipped (LFS ptr):  {stats.files_skipped_lfs}")
    print(f"  Files {verb}:           {stats.files_changed}")
    print(f"  Future/corrupt dates dropped:       {stats.future_dates_dropped}")
    print(f"  Future/corrupt entryIntoForce dropped: {stats.future_eif_dropped}")
    print(f"  Impossible inversions nulled:       {stats.inversions_nulled}")
    print(f"  isCurrentAmendment removed:         {stats.current_flags_removed}")
    print(f"  isCurrentAmendment (re)added:       {stats.current_flags_added}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
