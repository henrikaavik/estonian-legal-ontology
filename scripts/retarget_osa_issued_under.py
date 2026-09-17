#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.retarget_osa_issued_under`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.retarget_osa_issued_under", run_name="__main__")
