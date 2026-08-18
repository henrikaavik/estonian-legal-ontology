#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.rederive_riigikohus_case_types`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.rederive_riigikohus_case_types", run_name="__main__")
