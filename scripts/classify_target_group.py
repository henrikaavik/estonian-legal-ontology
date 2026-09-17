#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.classify_target_group`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.classify_target_group", run_name="__main__")
