#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.strip_unknown_subsection_numbers`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.strip_unknown_subsection_numbers", run_name="__main__")
