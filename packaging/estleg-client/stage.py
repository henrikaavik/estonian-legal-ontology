#!/usr/bin/env python3
"""Assemble the ``estleg-client`` build tree (standard layout) for ``python -m build``.

Usage: ``python packaging/estleg-client/stage.py <out-dir>``. Writes
``<out-dir>/{pyproject.toml, README.md, LICENSE, estleg_client/}`` from the
repository, skipping caches, and prints the directory. Standard library only.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PACKAGE = REPO / "estleg_client"
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".mypy_cache", ".ruff_cache")


def stage(out_dir: Path) -> Path:
    """Copy the distribution inputs into ``out_dir`` (replacing it) and return it."""
    out_dir = out_dir.resolve()
    if out_dir == REPO or REPO.is_relative_to(out_dir):
        raise SystemExit(f"refusing to stage over the repository: {out_dir}")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    shutil.copy2(HERE / "pyproject.toml", out_dir / "pyproject.toml")
    shutil.copy2(PACKAGE / "README.md", out_dir / "README.md")
    shutil.copy2(REPO / "LICENSE", out_dir / "LICENSE")
    shutil.copytree(PACKAGE, out_dir / "estleg_client", ignore=_IGNORE)
    return out_dir


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: stage.py <out-dir>", file=sys.stderr)
        return 2
    print(stage(Path(args[0])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
