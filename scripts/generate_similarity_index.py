#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.generate_similarity_index`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.generate_similarity_index", run_name="__main__")
