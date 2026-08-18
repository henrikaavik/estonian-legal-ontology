#!/usr/bin/env python3
"""Stamp an in-band VoID/DCAT Dataset head into every combined JSON-LD (#517).

Idempotent. The flagship ``combined_ontology.jsonld`` gets
``estleg_common.combined_ontology_header()`` at ``@graph[0]``. Other combined
files keep an existing ``owl:Ontology`` head (upgraded with Dataset types and
the CC BY 4.0 compilation license) or receive a thin
``combined_dataset_header()`` variant.

Git-LFS pointer stubs are skipped (or pulled once for the flagship file).
Writes go through ``estleg_common.save_json`` (atomic).

    python3 scripts/stamp_combined_dataset_heads.py            # write
    python3 scripts/stamp_combined_dataset_heads.py --dry-run  # report only
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from estleg_common import (
    COMBINED_JSONLD_TARGETS,
    KRR_DIR,
    save_json,
    stamp_combined_dataset_head,
)

LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"


def is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8") as handle:
            return handle.readline().startswith(LFS_POINTER_PREFIX)
    except OSError:
        return False


def try_lfs_pull(relpath: str) -> bool:
    """Pull one LFS path from the repo root. Return True if the file is now JSON."""
    repo_root = KRR_DIR.parent
    include = f"krr_outputs/{relpath}"
    print(f"  LFS pointer — running git lfs pull --include={include}")
    try:
        completed = subprocess.run(
            ["git", "lfs", "pull", f"--include={include}"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        print(f"  LFS pull failed to start: {exc}")
        return False
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        print(f"  LFS pull failed (exit {completed.returncode}): {stderr}")
        return False
    path = KRR_DIR / relpath
    return path.is_file() and not is_lfs_pointer(path)


def stamp_file(
    path: Path,
    *,
    flagship: bool,
    label: str | None,
    ontology_id: str | None,
    dry_run: bool,
) -> str:
    with path.open(encoding="utf-8") as handle:
        doc = json.load(handle)
    stamp_combined_dataset_head(
        doc,
        flagship=flagship,
        label=label,
        ontology_id=ontology_id,
    )
    if dry_run:
        return "dry-run (would write)"
    save_json(path, doc)
    return "stamped"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load and stamp in memory but do not write.",
    )
    args = parser.parse_args(argv)

    print("Stamp in-band Dataset heads on combined JSON-LD (#517)")
    print(f"  Mode: {'DRY RUN' if args.dry_run else 'WRITE'}")
    results: list[tuple[str, str, int]] = []

    for spec in COMBINED_JSONLD_TARGETS:
        relpath = str(spec["relpath"])
        path = KRR_DIR / relpath
        flagship = bool(spec["flagship"])
        raw_label = spec.get("label")
        raw_ontology_id = spec.get("ontology_id")
        label = raw_label if isinstance(raw_label, str) else None
        ontology_id = raw_ontology_id if isinstance(raw_ontology_id, str) else None

        if not path.is_file():
            print(f"  SKIP {relpath}: not present")
            results.append((relpath, "missing", 0))
            continue

        size = path.stat().st_size
        if is_lfs_pointer(path):
            if flagship and not args.dry_run:
                if not try_lfs_pull(relpath):
                    print(
                        f"  SKIP {relpath}: still an LFS pointer after pull "
                        f"({size} bytes)"
                    )
                    results.append((relpath, "lfs-pointer", size))
                    continue
                size = path.stat().st_size
            else:
                print(f"  SKIP {relpath}: LFS pointer ({size} bytes)")
                results.append((relpath, "lfs-pointer", size))
                continue

        action = stamp_file(
            path,
            flagship=flagship,
            label=label,
            ontology_id=ontology_id,
            dry_run=args.dry_run,
        )
        size_after = path.stat().st_size
        print(f"  {action.upper()} {relpath} ({size_after} bytes)")
        results.append((relpath, action, size_after))

    print("\nSummary:")
    for relpath, action, size in results:
        print(f"  {relpath}: {action} ({size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
