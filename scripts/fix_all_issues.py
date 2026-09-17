#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.fix_all_issues`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.fix_all_issues", run_name="__main__")
