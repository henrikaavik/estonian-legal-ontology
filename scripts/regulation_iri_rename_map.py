#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.regulation_iri_rename_map`` (issue #722)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.regulation_iri_rename_map", run_name="__main__")
