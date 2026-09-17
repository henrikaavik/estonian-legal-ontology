#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.backfill_orphan_is_part_of`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.backfill_orphan_is_part_of", run_name="__main__")
