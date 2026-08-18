#!/usr/bin/env python3
"""Normalize leaked ``<sup>N</sup>`` markup across the subcorpus (#627).

#572 fixed the literal ``<sup>N</sup>`` CDATA leak in the structured-law
peeps by routing every citable string through ``generate_all_laws.
_sup_to_unicode`` (``<sup>1</sup>`` → Unicode superscript ``¹``; any
residual bare ``<sup>``/``</sup>`` tag is stripped so no HTML survives).
That code fix only touches ``generate_all_laws`` output, so the same leak
remains in the *other* generators' committed artifacts:

  * ``krr_outputs/regulations/`` (state + KOV regulation peeps)
  * ``krr_outputs/riigikohus/`` ``krr_outputs/curia/``
    ``krr_outputs/eurlex/`` ``krr_outputs/eelnoud/`` (court / EU / drafts)
  * ``krr_outputs/provision_versions/`` (version sidecars derived from
    the law text)

This migration backfills those by applying the **same** ``_sup_to_unicode``
helper (imported from ``generate_all_laws`` — not reinvented) to every
string value in each file, recursively (list elements, dict values, and
JSON-LD ``{"@value": …}`` literals). The defect is currently present only
in ``regulations/`` and ``provision_versions/``; the court/EU/draft dirs
are scanned too so the migration stays correct if a future regen
reintroduces the markup there.

Safety / scope:
  * Only string values are rewritten, and only by ``_sup_to_unicode`` —
    keys, numbers, IRIs without ``<sup>`` and booleans are untouched.
  * A string containing no ``<sup>`` is returned unchanged by the helper,
    so the transform is a no-op everywhere the markup is absent.
  * Idempotent: after conversion the strings contain Unicode superscripts
    and no ``<sup>`` tag, so a re-run reports 0 changes.
  * Atomic writes via ``estleg_common.save_json`` (tempfile + os.replace).
  * ``--dry-run`` reports counts without writing.

Run (offline, idempotent):

    .venv/bin/python scripts/normalize_sup_markup_subcorpus.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from estleg.estleg_common import KRR_DIR, save_json

# Reuse the exact #572 conversion helper rather than reimplementing the
# <sup>N</sup> → Unicode-superscript mapping (single source of truth).
from estleg.generate_all_laws import _sup_to_unicode

# Subcorpus directories whose committed artifacts may carry the leaked
# markup. regulations/ + provision_versions/ are the populated ones today;
# the rest are included for robustness against future regenerations.
SUBCORPUS_DIRS = (
    "regulations",
    "riigikohus",
    "curia",
    "eurlex",
    "eelnoud",
    "provision_versions",
)

# Files in these dirs use either *.json (peeps) or *.jsonld (version
# sidecars); match both.
FILE_GLOBS = ("**/*.json", "**/*.jsonld")


def normalize_value(value: object, counter: list[int]) -> object:
    """Return ``value`` with every nested string ``_sup_to_unicode``-d.

    Walks lists and dicts recursively; ``counter[0]`` is incremented once
    per string whose content the conversion actually changes. Non-string
    scalars pass through untouched.
    """
    if isinstance(value, str):
        converted = _sup_to_unicode(value)
        if converted != value:
            counter[0] += 1
        return converted
    if isinstance(value, list):
        return [normalize_value(v, counter) for v in value]
    if isinstance(value, dict):
        return {k: normalize_value(v, counter) for k, v in value.items()}
    return value


def normalize_doc(doc: object) -> tuple[object, int]:
    """Normalize a whole JSON document. Returns ``(new_doc, strings_changed)``."""
    counter = [0]
    new_doc = normalize_value(doc, counter)
    return new_doc, counter[0]


def iter_subcorpus_files(krr_dir: Path) -> list[Path]:
    """Return every subcorpus JSON/JSON-LD file in deterministic order."""
    files: list[Path] = []
    for sub in SUBCORPUS_DIRS:
        base = krr_dir / sub
        if not base.exists():
            continue
        for pattern in FILE_GLOBS:
            files.extend(base.glob(pattern))
    # De-dup (a path can't match two globs here, but be defensive) and sort.
    unique = sorted(set(files), key=lambda p: p.as_posix().casefold())
    return unique


def process_file(path: Path, dry_run: bool) -> int:
    """Normalize one file. Returns the number of strings changed.

    Skips git-LFS pointer files (e.g. ``eurlex/eurlex_combined.jsonld``,
    ``curia/curia_combined.jsonld``, ``annotations/oiguskantsler_seisukohad.jsonld``
    when LFS is not pulled — the default CI ``pytest`` job): a pointer's first
    line is ``version https://git-lfs.github.com/spec/v1``, which is not JSON, so
    parsing it would crash. A pointer carries no real text to normalise anyway.
    """
    text = path.read_text(encoding="utf-8")
    if text.startswith("version https://git-lfs.github.com/spec/v1"):
        return 0
    doc = json.loads(text)
    new_doc, changed = normalize_doc(doc)
    if changed and not dry_run:
        save_json(path, new_doc)
    return changed


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

    files = iter_subcorpus_files(args.krr_dir)

    print("=" * 70)
    print("Normalize <sup>N</sup> -> Unicode superscript in subcorpus (#627)")
    print(f"  Dirs: {', '.join(SUBCORPUS_DIRS)}")
    print(f"  Files scanned: {len(files)}")
    print("=" * 70)

    files_changed = 0
    strings_changed = 0
    per_dir: dict[str, int] = {}
    for path in files:
        changed = process_file(path, args.dry_run)
        if changed:
            files_changed += 1
            strings_changed += changed
            try:
                top = path.relative_to(args.krr_dir).parts[0]
            except ValueError:
                top = path.parent.name
            per_dir[top] = per_dir.get(top, 0) + 1

    verb = "would normalize" if args.dry_run else "normalized"
    print(f"\n  Files {verb}: {files_changed}")
    print(f"  Strings {verb}: {strings_changed}")
    if per_dir:
        print("  By directory:")
        for top, count in sorted(per_dir.items()):
            print(f"    {top}: {count} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
