#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.fix_concept_definition_splices`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.fix_concept_definition_splices", run_name="__main__")
