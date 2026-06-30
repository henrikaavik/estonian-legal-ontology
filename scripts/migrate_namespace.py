#!/usr/bin/env python3
"""Migrate the IRI namespace ``data.riik.ee/ontology/estleg#`` -> ``w3id.org/estleg/`` (#516).

Offline, idempotent, byte-preserving string-swap over every tracked text file.
Node ``@id``s are stored compact (``estleg:Local``), so this is a ``@context``
prefix + path-IRI swap, not a per-IRI rewrite. See ``docs/NAMESPACE_MIGRATION.md``.

Three ordered replacements (order matters: #1 consumes the hash form before #2
catches the bare ontology/version IRIs):

  1. https://data.riik.ee/ontology/estleg#  -> https://w3id.org/estleg/
  2. https://data.riik.ee/ontology/estleg   -> https://w3id.org/estleg
  3. https://data.riik.ee/dataset/          -> https://w3id.org/estleg/dataset/

NOT handled here (do these around this run):
  * ``krr_outputs/combined_ontology.jsonld`` (Git-LFS) is REGENERATED via
    ``generate_combined_jsonld()`` after this swap, not edited in place.
  * Two bare-prose ``data.riik.ee`` docstrings (``fix_all_issues.py``,
    ``tests/test_fix_all_issues.py``) are reworded by hand — they have no single
    safe replacement target.
  * Run ``git lfs pull`` first so the LFS sidecars are materialised (a pointer
    stub is skipped + reported, never half-swapped).

Dry-run is the DEFAULT (report only); pass ``--apply`` to write. Idempotent:
a second run finds nothing to replace.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = REPO_ROOT / "data" / "namespace_migration_state.json"

# Ordered: #1 (hash form) must run before #2 (bare ontology/version IRI).
REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("https://data.riik.ee/ontology/estleg#", "https://w3id.org/estleg/"),
    ("https://data.riik.ee/ontology/estleg", "https://w3id.org/estleg"),
    ("https://data.riik.ee/dataset/", "https://w3id.org/estleg/dataset/"),
)
NEW_NAMESPACE = "https://w3id.org/estleg/"
LEGACY_HOST = "data.riik.ee"

# Excluded: historical records + this migration's own doc/state (all legitimately
# hold the legacy string; they are also excluded from the data.riik.ee==0 guard).
EXCLUDE_PREFIXES: tuple[str, ...] = (
    "CHANGELOG.md",
    "docs/superpowers/plans/",
    "docs/NAMESPACE_MIGRATION.md",
    "data/namespace_migration_state.json",
    # The migration machinery itself legitimately references the legacy host.
    "scripts/migrate_namespace.py",
    "tests/test_no_legacy_namespace.py",
    ".github/workflows/validate.yml",
)
# Regenerated separately (Git-LFS), never string-swapped.
SKIP_REGENERATED: frozenset[str] = frozenset(
    {"krr_outputs/combined_ontology.jsonld"}
)
LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"


def _atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` via a sibling tempfile + ``os.replace``."""
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())  # durably flush before replace (flaky volumes)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _is_lfs_pointer(text: str) -> bool:
    return text.startswith(LFS_POINTER_PREFIX)


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=str(REPO_ROOT), capture_output=True, text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def _excluded(rel: str) -> bool:
    return rel in SKIP_REGENERATED or any(
        rel == p or rel.startswith(p) for p in EXCLUDE_PREFIXES
    )


def _apply_replacements(text: str) -> tuple[str, int]:
    total = 0
    for old, new in REPLACEMENTS:
        if old in text:
            total += text.count(old)
            text = text.replace(old, new)
    return text, total


def migrate(*, apply: bool) -> dict:
    files_changed: list[str] = []
    skipped_lfs: list[str] = []
    unreadable: list[str] = []
    total_replacements = 0
    for rel in _tracked_files():
        if _excluded(rel):
            continue
        path = REPO_ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError):
            continue  # binary / submodule
        except OSError:
            # EIO / damaged blocks (flaky volume) — record, don't crash the run.
            # Restore the file from git and re-run; the guard catches any miss.
            unreadable.append(rel)
            continue
        if LEGACY_HOST not in text:
            continue
        if _is_lfs_pointer(text):
            skipped_lfs.append(rel)  # un-materialised — run `git lfs pull`
            continue
        new_text, n = _apply_replacements(text)
        if new_text == text:
            continue
        files_changed.append(rel)
        total_replacements += n
        if apply:
            _atomic_write_text(path, new_text)
    return {
        "files_changed": files_changed,
        "files_changed_count": len(files_changed),
        "total_replacements": total_replacements,
        "skipped_lfs_pointers": skipped_lfs,
        "unreadable": unreadable,
    }


def _write_state(stats: dict) -> None:
    # Records ONLY the new namespace + counts (never the legacy literal), so the
    # state file cannot itself trip the data.riik.ee==0 guard.
    STATE_PATH.write_text(
        json.dumps(
            {
                "migration": "namespace #516",
                "to_namespace": NEW_NAMESPACE,
                "files_changed": stats["files_changed_count"],
                "total_replacements": stats["total_replacements"],
                "note": (
                    "combined_ontology.jsonld is regenerated separately; two "
                    "bare-prose docstrings are reworded by hand. See "
                    "docs/NAMESPACE_MIGRATION.md."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write the swap to disk")
    mode.add_argument("--dry-run", action="store_true", help="report only (default)")
    parser.add_argument("--verbose", action="store_true", help="list every changed file")
    args = parser.parse_args(argv)
    apply = args.apply

    print("=" * 70)
    print("Namespace migration data.riik.ee/ontology/estleg# -> w3id.org/estleg/ (#516)")
    print(f"  MODE: {'apply (writing)' if apply else 'dry-run (no writes)'}")
    print("=" * 70)
    stats = migrate(apply=apply)
    verb = "changed" if apply else "would change"
    if args.verbose:
        for rel in stats["files_changed"]:
            print(f"  {verb}: {rel}")
    if stats["skipped_lfs_pointers"]:
        print(f"\n  !! {len(stats['skipped_lfs_pointers'])} un-materialised LFS "
              "pointer(s) skipped — run `git lfs pull` and re-run:")
        for rel in stats["skipped_lfs_pointers"]:
            print(f"       {rel}")
    if stats["unreadable"]:
        print(f"\n  !! {len(stats['unreadable'])} UNREADABLE file(s) (OSError/EIO "
              "— damaged blocks on a flaky volume). Restore from git and re-run:")
        print("       git checkout -- " + " ".join(stats["unreadable"][:3])
              + (" ..." if len(stats["unreadable"]) > 3 else ""))
    print(f"\n  Files {verb}:        {stats['files_changed_count']}")
    print(f"  Replacements {verb}: {stats['total_replacements']}")
    if apply:
        _write_state(stats)
        print(f"  State written:       {STATE_PATH.relative_to(REPO_ROOT)}")
    else:
        print("\n  (dry-run — pass --apply to write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
