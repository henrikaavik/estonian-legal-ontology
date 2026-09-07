#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.stamp_combined_dataset_heads`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.stamp_combined_dataset_heads", run_name="__main__")
