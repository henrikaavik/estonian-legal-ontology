#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.emit_release_changes`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.emit_release_changes", run_name="__main__")
