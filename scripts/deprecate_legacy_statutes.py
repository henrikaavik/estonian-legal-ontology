#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.deprecate_legacy_statutes`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.deprecate_legacy_statutes", run_name="__main__")
