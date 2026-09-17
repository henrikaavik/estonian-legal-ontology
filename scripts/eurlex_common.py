#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.eurlex_common`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.eurlex_common", run_name="__main__")
