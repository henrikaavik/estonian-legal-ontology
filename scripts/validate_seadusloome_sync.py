#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.validate_seadusloome_sync`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.validate_seadusloome_sync", run_name="__main__")
