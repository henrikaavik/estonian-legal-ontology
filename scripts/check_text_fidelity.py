#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.check_text_fidelity`` (issue #703)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.check_text_fidelity", run_name="__main__")
