#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.backfill_rt_eli`` (issue #707)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.backfill_rt_eli", run_name="__main__")
