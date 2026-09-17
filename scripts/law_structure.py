#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.law_structure`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.law_structure", run_name="__main__")
