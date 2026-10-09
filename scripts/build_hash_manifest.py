#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.build_hash_manifest`` (issue #729)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.build_hash_manifest", run_name="__main__")
