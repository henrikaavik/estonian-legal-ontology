#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.derive_kov_enabling_staleness`` (issue #712)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.derive_kov_enabling_staleness", run_name="__main__")
