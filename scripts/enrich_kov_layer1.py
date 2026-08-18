#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.enrich_kov_layer1`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.enrich_kov_layer1", run_name="__main__")
