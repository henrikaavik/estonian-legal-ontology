#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.remove_spurious_annotations_598`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.remove_spurious_annotations_598", run_name="__main__")
