#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.screen_court_personal_data`` (issue #683)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.screen_court_personal_data", run_name="__main__")
