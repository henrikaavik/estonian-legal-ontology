#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.fix_curia_court_contamination`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.fix_curia_court_contamination", run_name="__main__")
