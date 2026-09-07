#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.migrate_osa_range_ids`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.migrate_osa_range_ids", run_name="__main__")
