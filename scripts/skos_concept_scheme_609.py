#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.skos_concept_scheme_609`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.skos_concept_scheme_609", run_name="__main__")
