#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.fetch_eurovoc_official`` (issue #699)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.fetch_eurovoc_official", run_name="__main__")
