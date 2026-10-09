#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.gold_sets`` (issue #698)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.gold_sets", run_name="__main__")
