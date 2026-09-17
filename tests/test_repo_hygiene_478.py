"""Repo hygiene: agent-process scaffolding stays out of git (#478)."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SCAFFOLDING_PATHS = ("HEARTBEAT.md", "memory/", "reviews/")


def _gitignore_entries() -> set[str]:
    text = (REPO / ".gitignore").read_text(encoding="utf-8")
    entries: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entries.add(line)
    return entries


def _path_is_ignored(entry: str) -> bool:
    """True when .gitignore lists the path or an equivalent directory form."""
    entries = _gitignore_entries()
    token = entry.rstrip("/")
    candidates = {entry, token, f"{token}/", f"/{token}", f"/{token}/"}
    return bool(entries & candidates)


def _tracked_scaffolding() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", *SCAFFOLDING_PATHS],
        cwd=REPO,
        check=True,
        capture_output=True,
    )
    if not result.stdout:
        return []
    return [p.decode() for p in result.stdout.split(b"\0") if p]


def test_agent_scaffolding_not_tracked() -> None:
    tracked = _tracked_scaffolding()
    assert tracked == [], (
        "agent-process scaffolding must not be tracked (#478): "
        + ", ".join(tracked)
    )


def test_agent_scaffolding_ignored_or_absent() -> None:
    for path in SCAFFOLDING_PATHS:
        token = path.rstrip("/")
        on_disk = (REPO / token).exists()
        assert _path_is_ignored(path) or not on_disk, (
            f"{path} must be listed in .gitignore or absent from the tree (#478)"
        )
        assert _path_is_ignored(path), (
            f"{path} must stay ignored so it is not re-committed (#478)"
        )
