#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.generate_lower_court_decisions``."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.generate_lower_court_decisions", run_name="__main__")
