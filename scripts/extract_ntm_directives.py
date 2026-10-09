#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.extract_ntm_directives`` (issue #711)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.extract_ntm_directives", run_name="__main__")
