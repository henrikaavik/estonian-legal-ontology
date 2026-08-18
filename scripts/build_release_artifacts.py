#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.build_release_artifacts`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.build_release_artifacts", run_name="__main__")
