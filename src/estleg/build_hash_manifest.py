#!/usr/bin/env python3
"""Per-file content-hash manifest and changed-step selection (#729).

``run_all_integration.py --only-changed`` uses this module to decide which
DAG steps a change actually reaches:

1. :func:`build_manifest` expands every ``reads`` / ``writes`` glob the DAG
   declares (relative to ``krr_outputs/``; a leading ``../`` addresses the
   repository root, as ``build_release_assets.py`` does) and records one
   SHA-256 per matched file. A previous manifest is used as a cache: a file
   whose size and ``mtime_ns`` are unchanged keeps its recorded hash, so a
   refresh after a one-file edit re-reads one file, not 3 GB.
2. :func:`diff_manifests` compares two manifests by content hash only
   (``mtime`` is a cache hint, never a change signal), returning the sorted
   added / removed / modified paths.
3. :func:`select_steps` turns the changed set into a plan: every step whose
   ``reads`` match a changed path, every writer of a changed *derived*
   artefact (a hand-edited or deleted generated file is regenerated), and all
   their transitive dependents. Ingest-tier steps stay out unless
   ``include_ingest``.

The manifest lives at ``krr_outputs/.cache/hash_manifest.json`` by default
(``.cache/`` is git-ignored: it is machine-local state). ``manifestDigest``
is a SHA-256 over the sorted ``(path, sha256)`` pairs, so two trees with the
same content have the same digest whatever their mtimes.

CLI (records the current tree as the baseline, runs no steps)::

    python3 scripts/build_hash_manifest.py [--manifest PATH]
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

MANIFEST_VERSION = 1
HASH_ALGORITHM = "sha256"
CACHE_DIRNAME = ".cache"
MANIFEST_FILENAME = "hash_manifest.json"
REPO_PREFIX = "../"
_CHUNK = 1 << 20


def default_manifest_path(krr_dir: Path) -> Path:
    return krr_dir / CACHE_DIRNAME / MANIFEST_FILENAME


# ---------------------------------------------------------------------------
# Pattern handling
# ---------------------------------------------------------------------------


def step_patterns(steps: Iterable[dict]) -> list[str]:
    """Every distinct ``reads`` / ``writes`` pattern the DAG declares, sorted."""
    patterns: set[str] = set()
    for step in steps:
        patterns.update(step.get("reads", []) or [])
        patterns.update(step.get("writes", []) or [])
    return sorted(patterns)


def _split_pattern(pattern: str, krr_dir: Path) -> tuple[Path, str, str]:
    """Return ``(base_dir, glob_relative_to_base, key_prefix)`` for a pattern."""
    if pattern.startswith(REPO_PREFIX):
        return krr_dir.parent, pattern[len(REPO_PREFIX):], REPO_PREFIX
    return krr_dir, pattern, ""


def _segments_match(path_parts: list[str], pat_parts: list[str]) -> bool:
    """Segment-wise glob match; ``**`` matches zero or more whole segments."""
    if not pat_parts:
        return not path_parts
    head, rest = pat_parts[0], pat_parts[1:]
    if head == "**":
        return any(_segments_match(path_parts[i:], rest) for i in range(len(path_parts) + 1))
    if not path_parts:
        return False
    return fnmatch.fnmatchcase(path_parts[0], head) and _segments_match(path_parts[1:], rest)


def path_matches(rel_path: str, pattern: str) -> bool:
    """True when manifest key ``rel_path`` is named by DAG glob ``pattern``.

    Both are relative to ``krr_outputs/`` (``../`` = repository root). A
    slash-free pattern is root-level only (``*_peep.json`` does not match
    ``eelnoud/x_peep.json``), the same reading ``Path.glob`` gives it.
    """
    path_repo = rel_path.startswith(REPO_PREFIX)
    pat_repo = pattern.startswith(REPO_PREFIX)
    if path_repo != pat_repo:
        return False
    if path_repo:
        rel_path, pattern = rel_path[len(REPO_PREFIX):], pattern[len(REPO_PREFIX):]
    return _segments_match(rel_path.split("/"), pattern.split("/"))


def _is_hidden(rel: str) -> bool:
    return any(part.startswith(".") for part in rel.split("/") if part != "..")


def expand_patterns(patterns: Iterable[str], krr_dir: Path) -> dict[str, Path]:
    """Map manifest key -> file path for every file a pattern matches.

    Hidden files and directories (``.cache/``, ``.tmp`` droppings from an
    atomic write in progress) are skipped.
    """
    files: dict[str, Path] = {}
    for pattern in patterns:
        base, glob, prefix = _split_pattern(pattern, krr_dir)
        if not base.is_dir():
            continue
        for path in base.glob(glob):
            if not path.is_file():
                continue
            rel = path.relative_to(base).as_posix()
            if _is_hidden(rel):
                continue
            files.setdefault(prefix + rel, path)
    return files


# ---------------------------------------------------------------------------
# Hashing + manifest IO
# ---------------------------------------------------------------------------


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_digest(files: dict[str, dict]) -> str:
    """Content digest over the sorted ``(path, sha256)`` pairs."""
    digest = hashlib.sha256()
    for rel in sorted(files):
        digest.update(f"{rel}\0{files[rel]['sha256']}\n".encode())
    return digest.hexdigest()


def build_manifest(
    steps: Iterable[dict],
    krr_dir: Path,
    previous: dict | None = None,
) -> dict:
    """Hash every file the DAG's patterns match; reuse cached hashes.

    ``previous`` (an earlier manifest) supplies hashes for files whose size
    and ``mtime_ns`` are unchanged. Returns the manifest dict; the
    ``stats`` block (files hashed vs reused) is informational.
    """
    steps = list(steps)
    patterns = step_patterns(steps)
    cached = (previous or {}).get("files", {})
    entries: dict[str, dict] = {}
    hashed = reused = 0
    for rel, path in sorted(expand_patterns(patterns, krr_dir).items()):
        try:
            st = path.stat()
        except OSError:
            continue
        old = cached.get(rel)
        if old and old.get("size") == st.st_size and old.get("mtimeNs") == st.st_mtime_ns:
            sha = old["sha256"]
            reused += 1
        else:
            try:
                sha = hash_file(path)
            except OSError:
                continue
            hashed += 1
        entries[rel] = {"sha256": sha, "size": st.st_size, "mtimeNs": st.st_mtime_ns}
    return {
        "version": MANIFEST_VERSION,
        "algorithm": HASH_ALGORITHM,
        "root": "krr_outputs",
        "patterns": patterns,
        "fileCount": len(entries),
        "manifestDigest": manifest_digest(entries),
        "stats": {"hashed": hashed, "reusedFromCache": reused},
        "files": entries,
    }


def load_manifest(path: Path) -> dict | None:
    """Load a manifest; ``None`` when absent, unreadable or another version."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
        return None
    if not isinstance(data.get("files"), dict):
        return None
    return data


def write_manifest(manifest: dict, path: Path) -> None:
    """Atomically write ``manifest`` (sorted keys, one entry per line)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def diff_manifests(old: dict, new: dict) -> dict[str, list[str]]:
    """Content diff of two manifests: ``added`` / ``removed`` / ``modified``."""
    old_files, new_files = old.get("files", {}), new.get("files", {})
    added = sorted(set(new_files) - set(old_files))
    removed = sorted(set(old_files) - set(new_files))
    modified = sorted(
        rel for rel in set(old_files) & set(new_files)
        if old_files[rel].get("sha256") != new_files[rel].get("sha256")
    )
    return {"added": added, "removed": removed, "modified": modified}


def changed_paths(diff: dict[str, list[str]]) -> list[str]:
    return sorted({*diff["added"], *diff["removed"], *diff["modified"]})


# ---------------------------------------------------------------------------
# Step selection
# ---------------------------------------------------------------------------


def _dependents_closure(steps: list[dict]) -> dict[str, set[str]]:
    adj: dict[str, list[str]] = {s["name"]: [] for s in steps}
    for s in steps:
        for dep in s.get("depends_on", []):
            adj.setdefault(dep, []).append(s["name"])
    closure: dict[str, set[str]] = {}

    def dfs(node: str) -> set[str]:
        if node in closure:
            return closure[node]
        closure[node] = set()
        reach: set[str] = set()
        for succ in adj.get(node, []):
            reach.add(succ)
            reach |= dfs(succ)
        closure[node] = reach
        return reach

    for name in adj:
        dfs(name)
    return closure


def select_steps(
    steps: list[dict],
    topo: list[str],
    changed: Iterable[str],
    committed_inputs: Iterable[str],
    *,
    include_ingest: bool = False,
    ingest_tier: str = "ingest",
) -> dict:
    """Plan an incremental run for the ``changed`` manifest keys.

    Seeds are (a) every step with a ``reads`` pattern matching a changed
    path and (b) every writer of a changed path that no committed-input
    pattern covers (a derived artefact edited or deleted out of band). The
    plan is the seeds plus all transitive dependents, in ``topo`` order.
    Ingest-tier steps are dropped (and do not seed dependents) unless
    ``include_ingest``. Returns ``{"selected", "seeds", "excludedIngest"}``
    where ``seeds`` maps step name -> sorted reasons.
    """
    changed = sorted(set(changed))
    committed = tuple(committed_inputs)
    by_name = {s["name"]: s for s in steps}

    def is_ingest(name: str) -> bool:
        return by_name[name].get("tier") == ingest_tier

    seeds: dict[str, set[str]] = {}
    for step in steps:
        for pattern in step.get("reads", []) or []:
            if any(path_matches(rel, pattern) for rel in changed):
                seeds.setdefault(step["name"], set()).add(f"reads {pattern}")
    derived = [rel for rel in changed if not any(path_matches(rel, c) for c in committed)]
    for step in steps:
        for pattern in step.get("writes", []) or []:
            if any(path_matches(rel, pattern) for rel in derived):
                seeds.setdefault(step["name"], set()).add(f"regenerates {pattern}")

    excluded = sorted(n for n in seeds if is_ingest(n) and not include_ingest)
    active = {n for n in seeds if n not in excluded}
    closure = _dependents_closure(steps)
    selected: set[str] = set(active)
    for name in active:
        selected |= closure.get(name, set())
    if not include_ingest:
        selected = {n for n in selected if not is_ingest(n)}
    return {
        "selected": [n for n in topo if n in selected],
        "seeds": {n: sorted(seeds[n]) for n in sorted(active)},
        "excludedIngest": excluded,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    from estleg import run_all_integration as rai

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--manifest", type=Path, default=None,
                        help="Manifest path (default krr_outputs/.cache/hash_manifest.json).")
    args = parser.parse_args(argv)
    path = args.manifest or default_manifest_path(rai.KRR_DIR)
    manifest = build_manifest(rai.STEPS, rai.KRR_DIR, previous=load_manifest(path))
    write_manifest(manifest, path)
    print(f"Wrote {path}: {manifest['fileCount']} files "
          f"({manifest['stats']['hashed']} hashed, "
          f"{manifest['stats']['reusedFromCache']} cached), "
          f"digest {manifest['manifestDigest'][:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
