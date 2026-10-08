"""Corpus snapshot identity for every tool result and audit line (#714).

A ministry answer has to be reproducible: which corpus revision, which
ontology release, evaluated on which day, by which server build. This module
computes that once per process and hands out the same ``snapshot`` object for
every tool result (see ``server._wire``) and every audit line
(:mod:`estleg_mcp.audit`).

Where each field comes from:

* ``corpus_commit`` -- ``ESTLEG_CORPUS_COMMIT`` when set (the container
  entrypoint exports the commit it checked out), else the ``HEAD`` commit read
  straight from the corpus checkout's ``.git`` directory (no ``git``
  subprocess; handles detached heads, branch refs, ``packed-refs`` and
  worktree ``.git`` files). "" when the corpus is not a git checkout.
* ``corpus_ref`` -- ``ESTLEG_CORPUS_REF`` (the release tag the entrypoint
  pinned, e.g. ``v1.0.0``), else "".
* ``ontology_version`` -- ``owl:versionInfo`` of the corpus ``metadata.jsonld``.
* ``evaluation_date`` -- the UTC calendar date the call was answered on.
  Status words such as "in force" are relative to this date.
* ``server_version`` -- the ``estleg-mcp`` package version.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import __version__, data

SERVER_VERSION = __version__

_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


def _sha_or_empty(text: str) -> str:
    text = (text or "").strip()
    return text if _SHA.match(text) else ""


def _git_dir(root: Path) -> Path | None:
    """The git directory of ``root`` (follows a ``.git`` *file* to its gitdir)."""
    dot_git = root / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        try:
            text = dot_git.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if text.startswith("gitdir:"):
            target = Path(text[len("gitdir:"):].strip())
            return target if target.is_absolute() else (root / target).resolve()
    return None


def _packed_ref(git_dir: Path, ref: str) -> str:
    try:
        lines = (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        if line.startswith(("#", "^")):
            continue
        sha, _, name = line.partition(" ")
        if name.strip() == ref:
            return _sha_or_empty(sha)
    return ""


def read_git_head(root: Path) -> str:
    """The commit SHA ``HEAD`` points at in the checkout at ``root``, or ""."""
    git_dir = _git_dir(root)
    if git_dir is None:
        return ""
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not head.startswith("ref:"):
        return _sha_or_empty(head)
    ref = head[len("ref:"):].strip()
    # A linked worktree keeps its own HEAD but shares refs with the main repo.
    dirs = [git_dir]
    try:
        common = (git_dir / "commondir").read_text(encoding="utf-8").strip()
        dirs.append((git_dir / common).resolve())
    except OSError:
        pass
    for base in dirs:
        try:
            sha = _sha_or_empty((base / ref).read_text(encoding="utf-8"))
        except OSError:
            sha = ""
        if sha:
            return sha
    for base in dirs:
        sha = _packed_ref(base, ref)
        if sha:
            return sha
    return ""


@lru_cache(maxsize=1)
def corpus_identity() -> dict[str, str]:
    """``{corpus_commit, corpus_ref, ontology_version}`` -- fixed per process.

    Cached: the corpus is checked out before the server starts and is not
    swapped underneath a running process (the entrypoint updates it, then
    ``exec``s the server). Never raises; an unresolvable field is "".
    """
    commit = _sha_or_empty(os.environ.get("ESTLEG_CORPUS_COMMIT", ""))
    if not commit:
        try:
            commit = read_git_head(data.corpus_root())
        except (FileNotFoundError, OSError):
            commit = ""
    try:
        version = data.ontology_version()
    except (FileNotFoundError, OSError):
        version = ""
    return {
        "corpus_commit": commit,
        "corpus_ref": os.environ.get("ESTLEG_CORPUS_REF", "").strip(),
        "ontology_version": version,
    }


def evaluation_date() -> str:
    """Today's date in UTC (ISO ``YYYY-MM-DD``)."""
    return datetime.now(timezone.utc).date().isoformat()


def snapshot(language: str) -> dict[str, Any]:
    """The snapshot envelope attached to every tool result (#714)."""
    return {
        **corpus_identity(),
        "evaluation_date": evaluation_date(),
        "server_version": SERVER_VERSION,
        "language": language,
    }


__all__ = [
    "SERVER_VERSION",
    "corpus_identity",
    "evaluation_date",
    "read_git_head",
    "snapshot",
]
