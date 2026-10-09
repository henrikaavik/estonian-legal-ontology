#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.generate_kov_state_similarity`` (issue #729)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.generate_kov_state_similarity", run_name="__main__")
