#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.heuristic_overrides`` (#700).

Validate, list, or stale-check ``data/heuristic_overrides.jsonl``::

    python3 scripts/heuristic_overrides.py validate
    python3 scripts/heuristic_overrides.py list --classifier deontic
    python3 scripts/heuristic_overrides.py stale
"""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.heuristic_overrides", run_name="__main__")
