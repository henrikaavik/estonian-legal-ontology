#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.shacl_validate_all`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.shacl_validate_all", run_name="__main__")
