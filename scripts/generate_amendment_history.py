#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.generate_amendment_history`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.generate_amendment_history", run_name="__main__")
