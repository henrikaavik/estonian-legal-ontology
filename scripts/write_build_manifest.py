#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.write_build_manifest`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.write_build_manifest", run_name="__main__")
