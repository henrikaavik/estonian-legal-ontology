#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.fix_data_quality_588`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.fix_data_quality_588", run_name="__main__")
