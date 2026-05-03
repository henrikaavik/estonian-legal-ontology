"""Helpers for the KOV (municipal) issuer/municipality registry.

Pure functions — all I/O takes explicit Path arguments. Tested in
tests/test_kov_registry.py.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, TypedDict


class Municipality(TypedDict):
    ehakCode: str
    name: str
    county: str
    type: Literal["linn", "vald"]


def load_municipalities(path: Path) -> dict[str, Municipality]:
    """Load the municipalities registry, keyed by EHAK code."""
    with open(path, "r", encoding="utf-8") as fh:
        rows = json.load(fh)
    out: dict[str, Municipality] = {}
    for row in rows:
        code = row["ehakCode"]
        if code in out:
            raise ValueError(f"Duplicate EHAK code: {code}")
        if row["type"] not in {"linn", "vald"}:
            raise ValueError(f"Invalid type for {code}: {row['type']!r}")
        if not (len(code) == 4 and code.isdigit()):
            raise ValueError(
                f"EHAK code must be a 4-digit string, got {code!r}"
            )
        out[code] = row
    return out
