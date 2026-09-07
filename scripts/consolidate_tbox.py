#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.consolidate_tbox`` (issue #433)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.consolidate_tbox", run_name="__main__")
