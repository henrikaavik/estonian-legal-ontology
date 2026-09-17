#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.dedupe_summaries_629`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.dedupe_summaries_629", run_name="__main__")
