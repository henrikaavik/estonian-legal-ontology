#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.backfill_official_english``."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.backfill_official_english", run_name="__main__")
