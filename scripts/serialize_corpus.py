#!/usr/bin/env python3
"""Compatibility shim. Implementation: ``estleg.serialize_corpus`` (issue #472)."""

from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("estleg.serialize_corpus", run_name="__main__")
