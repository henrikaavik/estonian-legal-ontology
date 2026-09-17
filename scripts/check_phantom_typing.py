#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.check_phantom_typing`` (issue #709)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.check_phantom_typing", run_name="__main__")
