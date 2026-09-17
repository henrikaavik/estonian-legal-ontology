#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.eval_harness`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.eval_harness", run_name="__main__")
