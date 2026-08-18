#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.run_all_integration`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.run_all_integration", run_name="__main__")
