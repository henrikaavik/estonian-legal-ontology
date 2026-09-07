#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.serialize_tabular`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.serialize_tabular", run_name="__main__")
