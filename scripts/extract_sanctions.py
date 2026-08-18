#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.extract_sanctions`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.extract_sanctions", run_name="__main__")
