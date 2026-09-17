#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.kov_pipeline_coverage`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.kov_pipeline_coverage", run_name="__main__")
