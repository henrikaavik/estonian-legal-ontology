#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.migrate_malformed_iris`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.migrate_malformed_iris", run_name="__main__")
