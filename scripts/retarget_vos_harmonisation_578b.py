#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.retarget_vos_harmonisation_578b`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.retarget_vos_harmonisation_578b", run_name="__main__")
