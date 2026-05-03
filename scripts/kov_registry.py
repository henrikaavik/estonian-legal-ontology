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


_BODY_SUFFIXES: dict[str, tuple[Literal["volikogu", "valitsus"], Literal["linn", "vald"]]] = {
    "linnavolikogu": ("volikogu", "linn"),
    "linnavalitsus": ("valitsus", "linn"),
    "vallavolikogu": ("volikogu", "vald"),
    "vallavalitsus": ("valitsus", "vald"),
    "alevivolikogu": ("volikogu", "vald"),  # historical: Vändra alev → successor vald
}


class IssuerSlugParts(TypedDict):
    slug: str
    root: str
    body: str
    bodyType: Literal["volikogu", "valitsus"]
    municipalityType: Literal["linn", "vald"]


def parse_issuer_slug(slug: str) -> IssuerSlugParts:
    """Split an issuer directory slug into its components.

    Slugs look like ``tallinna_linnavolikogu`` or
    ``kohtla_jarve_linnavolikogu`` (compound root). The body suffix is
    one of ``linnavolikogu``, ``linnavalitsus``, ``vallavolikogu``,
    ``vallavalitsus``.
    """
    for body, (body_type, mun_type) in _BODY_SUFFIXES.items():
        suffix = f"_{body}"
        if slug.endswith(suffix):
            root = slug[: -len(suffix)]
            return {
                "slug": slug,
                "root": root,
                "body": body,
                "bodyType": body_type,
                "municipalityType": mun_type,
            }
    raise ValueError(f"unknown body suffix in slug: {slug!r}")


_TRANSLIT = str.maketrans({
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
})


def _normalize_name_for_matching(name: str) -> str:
    """Lowercase, transliterate, strip type suffix for slug-matching."""
    base = name.translate(_TRANSLIT).lower().strip()
    for suffix in (" linn", " vald"):
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip()
            break
    # Compound names like "Kohtla-Järve" use a hyphen; slugs use underscore.
    return base.replace("-", "_").replace(" ", "_")


def auto_match_municipality(
    parts: IssuerSlugParts,
    municipalities: dict[str, Municipality],
) -> str | None:
    """Return EHAK code if (root, municipalityType) uniquely identifies a
    municipality. Returns None if there is no match or more than one.

    Returning None is the "fail loudly to the curated CSV" path — auto-match
    never guesses.
    """
    target_root = parts["root"]
    target_type = parts["municipalityType"]
    matches: list[str] = []
    for code, mun in municipalities.items():
        if mun["type"] != target_type:
            continue
        normalized = _normalize_name_for_matching(mun["name"])
        # Slugs use the Estonian genitive form (e.g. "tallinna" for Tallinn);
        # for consonant-final names like Tallinn this adds an "a" suffix.
        if target_root == normalized or target_root == normalized + "a":
            matches.append(code)
    if len(matches) == 1:
        return matches[0]
    return None
