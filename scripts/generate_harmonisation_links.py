#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.generate_harmonisation_links`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.generate_harmonisation_links", run_name="__main__")
