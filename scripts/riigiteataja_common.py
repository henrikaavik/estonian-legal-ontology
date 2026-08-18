#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.riigiteataja_common`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.riigiteataja_common", run_name="__main__")
