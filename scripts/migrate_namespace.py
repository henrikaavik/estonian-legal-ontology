#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.migrate_namespace`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.migrate_namespace", run_name="__main__")
