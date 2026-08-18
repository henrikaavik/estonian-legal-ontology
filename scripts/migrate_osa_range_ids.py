#!/usr/bin/env python3
"""Rewrite volatile multipart act IRIs ``_OsaN_min_max`` → ``_OsaN`` (#309).

``generate_multipart_law`` already mints ``estleg:{prefix}_Osa{n}``. Committed
peeps still carry the §-range in the ``@id``, so a later amendment shifts the
IRI and drops enrichments. This pass remaps every compact (and expanded)
range-encoded osa IRI across the corpus, including combined.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from estleg_common import KRR_DIR, NS
from migrate_malformed_iris import iter_corpus_json, rewrite_text

# estleg:KARIST_2_Osa1_1_87  /  estleg:volaoigusseadus_Osa2_208_270
OSA_RANGE_RE = re.compile(
    r"(estleg:\w+_Osa\d+)_\d+_\d+\b"
)
EXPANDED_OSA_RANGE_RE = re.compile(
    re.escape(NS) + r"(\w+_Osa\d+)_\d+_\d+\b"
)


def stable_osa_iri(iri: str) -> str | None:
    """Return the snapshot-stable ``_OsaN`` form, or None if not range-encoded."""
    if not isinstance(iri, str):
        return None
    if iri.startswith(NS):
        compact = "estleg:" + iri[len(NS) :]
        mapped = stable_osa_iri(compact)
        return (NS + mapped[len("estleg:") :]) if mapped else None
    match = re.fullmatch(r"(estleg:\w+_Osa\d+)_\d+_\d+", iri)
    return match.group(1) if match else None


def collect_osa_range_remap(krr_dir: Path) -> dict[str, str]:
    """Map every observed range-encoded osa IRI to its stable ``_OsaN`` form."""
    remap: dict[str, str] = {}
    for path in iter_corpus_json(krr_dir):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in OSA_RANGE_RE.finditer(text):
            old = match.group(0)
            new = match.group(1)
            if old != new:
                remap[old] = new
        for match in EXPANDED_OSA_RANGE_RE.finditer(text):
            old = match.group(0)
            new = NS + match.group(1)
            if old != new:
                remap[old] = new
    return remap


def apply_osa_range_iris(krr_dir: Path, remap: dict[str, str]) -> int:
    written = 0
    for path in iter_corpus_json(krr_dir):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not any(old in text for old in remap):
            continue
        new_text = rewrite_text(text, remap)
        if new_text == text:
            continue
        path.write_text(new_text, encoding="utf-8")
        written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--krr-dir", type=Path, default=KRR_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    remap = collect_osa_range_remap(args.krr_dir)
    print(f"remap entries: {len(remap)}")
    for old, new in sorted(remap.items())[:15]:
        print(f"  {old} -> {new}")
    if len(remap) > 15:
        print(f"  ... {len(remap) - 15} more")
    if args.dry_run:
        return 0
    written = apply_osa_range_iris(args.krr_dir, remap)
    print(f"files rewritten: {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
