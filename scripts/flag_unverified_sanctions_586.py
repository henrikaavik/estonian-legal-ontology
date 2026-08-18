#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.flag_unverified_sanctions_586`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.flag_unverified_sanctions_586", run_name="__main__")
