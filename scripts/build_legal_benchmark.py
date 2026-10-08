#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.build_legal_benchmark`` (issue #727)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.build_legal_benchmark", run_name="__main__")
