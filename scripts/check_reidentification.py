#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.check_reidentification`` (issue #720)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.check_reidentification", run_name="__main__")
