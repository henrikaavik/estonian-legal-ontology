#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.normalize_sup_markup_subcorpus`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.normalize_sup_markup_subcorpus", run_name="__main__")
