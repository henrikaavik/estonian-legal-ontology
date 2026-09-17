#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.estleg_common`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.estleg_common", run_name="__main__")
