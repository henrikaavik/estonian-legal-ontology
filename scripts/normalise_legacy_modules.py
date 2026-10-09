#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.normalise_legacy_modules`` (#709)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.normalise_legacy_modules", run_name="__main__")
