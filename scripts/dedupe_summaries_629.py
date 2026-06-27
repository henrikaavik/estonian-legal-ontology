#!/usr/bin/env python3
"""Collapse doubled ``estleg:summary`` strings across the corpus (#629).

Offline, idempotent, surgical post-process over every ``*_peep.json`` under
``krr_outputs/``. It repairs the *existing on-disk* doubling that the shared
``riigiteataja_common.collect_text`` / ``collect_full_text`` produced before the
generator fix: an ``el.iter()`` walk over the NESTED Riigi Teataja text tags
(``loige`` / ``lause`` / ``lauseOsa`` / ``tavatekst``) appended a subsection's
text once for the container and again for each nested sentence, so the stored
``estleg:summary`` is the provision preview repeated twice. The generator is now
fixed, but regulation (and a few other) peeps generated earlier still carry the
doubled text — and because regulation provisions are STUBBED in
``combined_ontology.jsonld``, this is a full-load-surface defect that
``get_provision`` / SPARQL previews still expose.

Detection is deliberately CONSERVATIVE — it collapses a summary only when it is
unmistakably one unit repeated, and never alters a non-repeated summary:

  * the string is ``U`` + a single-space separator + ``V`` where ``V`` is the
    whole of ``U`` (an exact double) or a leading PREFIX of ``U`` (the trailing
    copy clipped by the 500-char summary cap), and
  * BOTH ``U`` and the matched repeat ``V`` are longer than ``_MIN_UNIT`` chars.

The second bound is what separates a genuine whole-text double from a sentence
that merely begins and ends with the same short fragment — e.g. a ``"§ 1 (1) …"``
provision whose 500-char preview happens to end in a stray ``"1"`` (a one-char
"prefix" that must NOT be collapsed) — or from a repeated short word.

The repeated unit ``U`` is recovered from the string's smallest period via the
KMP prefix function; see :func:`dedouble`.

Scope / safety:
  * Only string ``estleg:summary`` values are inspected; every other field and
    node is left untouched.
  * Idempotent: collapsing ``U`` once yields ``U``, which is no longer a double,
    so a re-run (or a corpus already de-doubled) reports 0 changes.
  * Atomic writes via ``estleg_common.save_json`` (tempfile + os.replace).
  * Git-LFS-pointer guard: a file whose first line is the LFS spec marker is
    skipped (never rewrite a pointer stub into real content).
  * ``--dry-run`` is the DEFAULT and reports counts without writing; pass
    ``--apply`` to persist.

Run (offline, idempotent; dry-run by default):

    .venv/bin/python scripts/dedupe_summaries_629.py            # report only
    .venv/bin/python scripts/dedupe_summaries_629.py --apply    # write changes
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from estleg_common import KRR_DIR, save_json

LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"

SUMMARY_KEY = "estleg:summary"

# Both the repeated unit U and the (possibly truncated) second copy must EXCEED
# this many characters to be collapsed. This mirrors the ">20 chars" confidence
# bar the issue specifies for U and applies it to the second segment too, so a
# coincidental short prefix==suffix overlap (or a repeated short word) is never
# mistaken for a doubled summary.
_MIN_UNIT = 20

# Shortest string that could possibly be a valid double: two (>_MIN_UNIT)-char
# copies plus the one-char separator. Strings below this can be rejected without
# computing the period.
_MIN_DOUBLED_LEN = 2 * _MIN_UNIT + 3


def _prefix_function(s: str) -> list[int]:
    """Return the KMP prefix (failure) function of ``s``.

    ``pi[i]`` is the length of the longest proper prefix of ``s[: i + 1]`` that
    is also a suffix of it; the smallest period of ``s`` is ``len(s) - pi[-1]``.
    """
    n = len(s)
    pi = [0] * n
    for i in range(1, n):
        j = pi[i - 1]
        while j > 0 and s[i] != s[j]:
            j = pi[j - 1]
        if s[i] == s[j]:
            j += 1
        pi[i] = j
    return pi


def dedouble(summary: str) -> str | None:
    """Return the single unit ``U`` if ``summary`` is ``U`` doubled, else ``None``.

    A doubled summary has the shape ``U + " " + V`` where ``V`` is a non-empty
    leading prefix of ``U`` (``V == U`` for an untruncated double; a shorter
    prefix once the 500-char summary cap has clipped the second copy). Both ``U``
    and ``V`` must be longer than :data:`_MIN_UNIT` characters.

    For such a string the smallest period ``d = len(s) - prefix_function(s)[-1]``
    equals ``len(U) + 1`` (the unit plus its single-space separator), so
    ``U = s[: d - 1]`` and ``V = s[d:]``. ``None`` is returned for anything that
    is not an unambiguous double, so a non-repeated summary is never altered.
    """
    n = len(summary)
    if n < _MIN_DOUBLED_LEN:
        return None
    period = n - _prefix_function(summary)[-1]
    unit_len = period - 1
    second_len = n - period
    # U and the matched repeat must each clear the floor (this is what rejects
    # coincidental short borders such as a trailing stray "1", and short words).
    if unit_len <= _MIN_UNIT or second_len <= _MIN_UNIT:
        return None
    # The two copies are joined by exactly one space (the generator's join sep).
    if summary[unit_len] != " ":
        return None
    # The second copy must be no longer than U, i.e. the string is ~2x U and not
    # a longer (triple+) repetition.
    if second_len > unit_len:
        return None
    unit = summary[:unit_len]
    # Confirm explicitly that the trailing segment is a prefix of U.
    if unit[:second_len] != summary[period:]:
        return None
    return unit


def _is_lfs_pointer(path: Path) -> bool:
    """Return True when ``path`` is an un-materialised Git-LFS pointer stub."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            first = fh.readline()
    except OSError:
        return False
    return first.startswith(LFS_POINTER_PREFIX)


class Stats:
    """Mutable per-run tally."""

    __slots__ = (
        "files_scanned",
        "files_changed",
        "files_skipped_lfs",
        "summaries_seen",
        "summaries_dedoubled",
        "by_subdir",
    )

    def __init__(self) -> None:
        self.files_scanned = 0
        self.files_changed = 0
        self.files_skipped_lfs = 0
        self.summaries_seen = 0
        self.summaries_dedoubled = 0
        self.by_subdir: Counter[str] = Counter()


def _top_subdir(path: Path, krr_dir: Path) -> str:
    """Return ``path``'s top-level ``krr_outputs`` subdir (``"(root)"`` if direct)."""
    try:
        rel = path.resolve().relative_to(krr_dir.resolve())
    except ValueError:
        return "(other)"
    return rel.parts[0] if len(rel.parts) > 1 else "(root)"


def dedouble_doc(doc: object, stats: Stats, subdir: str) -> bool:
    """Collapse doubled ``estleg:summary`` strings in ``doc`` in place.

    Returns True when at least one summary changed. Every string summary seen is
    counted, and every one collapsed is tallied overall and under ``subdir``.
    """
    graph = doc.get("@graph") if isinstance(doc, dict) else None
    if not isinstance(graph, list):
        return False
    changed = False
    for node in graph:
        if not isinstance(node, dict):
            continue
        summary = node.get(SUMMARY_KEY)
        if not isinstance(summary, str):
            continue
        stats.summaries_seen += 1
        unit = dedouble(summary)
        if unit is not None and unit != summary:
            node[SUMMARY_KEY] = unit
            stats.summaries_dedoubled += 1
            stats.by_subdir[subdir] += 1
            changed = True
    return changed


def process_file(path: Path, stats: Stats, krr_dir: Path, *, dry_run: bool) -> None:
    """Scan and (unless ``dry_run``) rewrite one peep file."""
    stats.files_scanned += 1
    if _is_lfs_pointer(path):
        stats.files_skipped_lfs += 1
        return
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if dedouble_doc(doc, stats, _top_subdir(path, krr_dir)):
        stats.files_changed += 1
        if not dry_run:
            save_json(path, doc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--krr-dir",
        type=Path,
        default=KRR_DIR,
        help="Corpus root to scan for *_peep.json (default: krr_outputs/).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing (DEFAULT).",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Write the de-doubled summaries back to disk.",
    )
    args = parser.parse_args(argv)

    krr_dir: Path = args.krr_dir
    if not krr_dir.is_dir():
        print(f"ERROR: not a directory: {krr_dir}")
        return 1
    dry_run = not args.apply

    print("=" * 70)
    print("De-double estleg:summary strings (#629)")
    print(f"  Corpus: {krr_dir}")
    print(f"  MODE:   {'dry-run (no writes)' if dry_run else 'APPLY (writing)'}")
    print("=" * 70)

    stats = Stats()
    for path in sorted(krr_dir.rglob("*_peep.json")):
        process_file(path, stats, krr_dir, dry_run=dry_run)

    verb = "would de-double" if dry_run else "de-doubled"
    changed_label = "would change" if dry_run else "changed"
    print(f"\n  Peeps scanned:            {stats.files_scanned}")
    print(f"  Peeps skipped (LFS ptr):  {stats.files_skipped_lfs}")
    print(f"  Files {changed_label}:{'':<7}{stats.files_changed}")
    print(f"  Summaries seen:           {stats.summaries_seen}")
    print(f"  Summaries {verb}:  {stats.summaries_dedoubled}")
    print("  Breakdown by top-level subdir:")
    if stats.by_subdir:
        for subdir, count in sorted(stats.by_subdir.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"    {subdir:<24} {count}")
    else:
        print("    (none)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
