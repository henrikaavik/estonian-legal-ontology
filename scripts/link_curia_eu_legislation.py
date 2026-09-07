#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.link_curia_eu_legislation`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.link_curia_eu_legislation", run_name="__main__")
