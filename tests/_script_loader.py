"""Explicit loader for archived one-shots and examples (#706).

``scripts/archive/`` (spent one-shot migrations) and ``examples/`` are not
packages and are deliberately NOT on the pytest ``pythonpath``: putting them
there let any test import a spent migration by bare name, and kept those
one-shots importable from every test module. Tests that exercise one of
those scripts load it by file path instead, which makes the dependency
explicit at the call site::

    from tests._script_loader import load_script

    fdi = load_script("scripts/archive/fix_duplicate_ids.py")

The module is registered in ``sys.modules`` under its file stem so a test
that later does ``monkeypatch.setattr(fdi, ...)`` patches the same object
the script's own functions see. Loading the same file twice returns the
cached module.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent

# Only these trees may be loaded this way; production code lives in the
# ``estleg`` package and is imported normally.
ALLOWED_DIRS = (REPO_ROOT / "scripts" / "archive", REPO_ROOT / "examples")


def load_script(relpath: str) -> ModuleType:
    """Import the script at ``REPO_ROOT / relpath`` and return the module."""
    path = (REPO_ROOT / relpath).resolve()
    if not any(path.is_relative_to(d) for d in ALLOWED_DIRS):
        raise ValueError(f"{relpath}: only scripts/archive/ and examples/ load by path")
    if not path.is_file():
        raise FileNotFoundError(path)
    name = path.stem
    cached = sys.modules.get(name)
    if cached is not None and Path(getattr(cached, "__file__", "") or "").resolve() == path:
        return cached
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module
