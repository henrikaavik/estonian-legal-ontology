#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.check_tbox_consistency`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.check_tbox_consistency", run_name="__main__")
