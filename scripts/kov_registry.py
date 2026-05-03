"""Helpers for the KOV (municipal) issuer/municipality registry.

Pure functions — all I/O takes explicit Path arguments. Tested in
tests/test_kov_registry.py.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Literal, TypedDict

from estleg_common import _ESTONIAN_TRANSLITERATION


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


_TRANSLIT = str.maketrans(_ESTONIAN_TRANSLITERATION)


def _normalize_name_for_matching(name: str) -> str:
    """Lowercase, transliterate, strip type suffix for slug-matching."""
    base = name.translate(_TRANSLIT).lower().strip()
    for suffix in (" linn", " vald"):
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip()
            break
    # Compound names like "Kohtla-Järve" use a hyphen; slugs use underscore.
    return base.replace("-", "_").replace(" ", "_")


_VOWELS = set("aeiouõäöü")


def _matches_municipality_root(slug_root: str, municipality_name: str) -> bool:
    """Return True if the slug's root corresponds to the municipality's name.

    Slugs are typically in Estonian genitive form (e.g.
    ``tallinna_linnavolikogu`` — the genitive of *Tallinn*). EHAK
    registers municipalities in the nominative (e.g. ``Tallinn``).

    For most current municipality names the nominative form is already
    vowel-final (Tartu, Mulgi, Kose, ...) and the genitive coincides
    with the nominative — so a direct match works. For consonant-final
    nominatives (currently only ``Tallinn`` in the 79-municipality
    registry), the genitive appends ``-a`` (``Tallinna``), so we accept
    both shapes — but the ``+a`` rule is gated to consonant-final
    nominatives so a vowel-final name like ``Tartu`` never silently
    matches an unrelated ``tartua`` slug.

    No false-positive collisions on the current corpus (audited).
    """
    normalized = _normalize_name_for_matching(municipality_name)
    if slug_root == normalized:
        return True
    # +a genitive accommodation only applies to consonant-final nominatives.
    if normalized and normalized[-1] not in _VOWELS:
        return slug_root == normalized + "a"
    return False


def auto_match_municipality(
    parts: IssuerSlugParts,
    municipalities: dict[str, Municipality],
) -> str | None:
    """Return EHAK code if (root, municipalityType) uniquely identifies a
    current municipality. Returns None if there is no match or more than
    one. Returning None is the "fail loudly to the curated CSV" path —
    auto-match never guesses.

    Slug-vs-name matching uses ``_matches_municipality_root`` which
    accommodates the Estonian genitive case (Tallinn ↔ tallinna). See
    that helper's docstring for details.
    """
    target_root = parts["root"]
    target_type = parts["municipalityType"]
    matches: list[str] = []
    for code, mun in municipalities.items():
        if mun["type"] != target_type:
            continue
        if _matches_municipality_root(target_root, mun["name"]):
            matches.append(code)
    if len(matches) == 1:
        return matches[0]
    return None


class CuratedRow(TypedDict):
    currentMunicipalityCode: str
    mappingSource: str  # "auto-match" | "haldusreform-2017" | "manual-review" | "url:<...>"
    mappingEvidence: str


def discover_issuer_slugs(kov_root: Path) -> list[str]:
    """Return sorted list of issuer-directory slugs under regulations/kov/."""
    if not kov_root.is_dir():
        raise FileNotFoundError(f"KOV root not found: {kov_root}")
    return sorted(p.name for p in kov_root.iterdir() if p.is_dir())


class IssuerEntry(TypedDict):
    slug: str
    displayName: str
    bodyType: str                    # "volikogu" | "valitsus"
    currentMunicipalityCode: str
    mappingSource: str               # see _ALLOWED_MAPPING_SOURCES + "url:..."
    mappingEvidence: str             # may be empty for auto-match rows
    historicalMunicipalityName: str  # the issuing-time KOV unit, free text


# Layer 1 limitation (acknowledged): displayName and
# historicalMunicipalityName are derived from the slug by simple title-
# casing, which drops Estonian diacritics (ä/ö/ü/õ) and hyphens. So
# 'kohtla_jarve_linnavolikogu' becomes 'Kohtla Jarve Linnavolikogu'
# rather than the correct 'Kohtla-Järve Linnavolikogu'. These fields
# are display-only literals (rdfs:label, estleg:historicalMunicipalityName)
# — they do NOT participate in IRIs, joins, or SPARQL keys, so the
# limitation does not affect data integrity. Layer 2 will replace these
# strings with diacritic-correct lookups (probably by adding a
# 'historical_municipality_name' column to issuer_successor_map.csv and
# sourcing auto-match display names from municipalities.json's `name`).
def _slug_to_display_name(slug: str) -> str:
    """Convert ``tallinna_linnavolikogu`` → ``Tallinna Linnavolikogu``."""
    return " ".join(part.capitalize() for part in slug.split("_"))


_ALLOWED_MAPPING_SOURCES = {
    "auto-match",
    "haldusreform-2017",
    "manual-review",
}


def _validate_mapping_source(source: str) -> None:
    if source.startswith("url:"):
        return
    if source not in _ALLOWED_MAPPING_SOURCES:
        raise ValueError(
            f"invalid mapping_source: {source!r} "
            f"(allowed: {sorted(_ALLOWED_MAPPING_SOURCES)} or 'url:<...>')"
        )


def build_issuer_registry(
    slugs: list[str],
    municipalities: dict[str, Municipality],
    curated: dict[str, CuratedRow],
) -> dict[str, IssuerEntry]:
    """Build the combined issuer registry.

    Auto-match wins where (root, municipalityType) is unique. Curated
    CSV covers the rest. Unmapped issuers raise ValueError listing every
    failure. Curated rows are also validated against the municipalities
    registry (EHAK code must exist) and the allowed mapping_source set,
    so typos surface here rather than at SHACL time.
    """
    out: dict[str, IssuerEntry] = {}
    unmapped: list[str] = []
    for slug in slugs:
        parts = parse_issuer_slug(slug)
        ehak = auto_match_municipality(parts, municipalities)
        if ehak is not None:
            mapping_source = "auto-match"
            mapping_evidence = ""
        elif slug in curated:
            row = curated[slug]
            ehak = row["currentMunicipalityCode"]
            mapping_source = row["mappingSource"]
            mapping_evidence = row["mappingEvidence"]
            _validate_mapping_source(mapping_source)
            if ehak not in municipalities:
                raise ValueError(
                    f"curated row for {slug!r} references unknown EHAK "
                    f"code {ehak!r}; not present in municipalities.json"
                )
        else:
            unmapped.append(slug)
            continue

        out[slug] = {
            "slug": slug,
            "displayName": _slug_to_display_name(slug),
            "bodyType": parts["bodyType"],
            "currentMunicipalityCode": ehak,
            "mappingSource": mapping_source,
            "mappingEvidence": mapping_evidence,
            "historicalMunicipalityName": parts["root"].replace("_", " ").title(),
        }

    if unmapped:
        raise ValueError(
            "Unmapped issuers (add curated rows for each):\n  "
            + "\n  ".join(unmapped)
        )
    return out


def load_curated_map(path: Path) -> dict[str, CuratedRow]:
    """Load the curated issuer→municipality map.

    Required columns: issuer_slug, current_municipality_ehak,
    mapping_source, mapping_evidence. Every row MUST have non-empty
    mapping_evidence — this is the auditable trail.
    """
    out: dict[str, CuratedRow] = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            slug = row["issuer_slug"].strip()
            evidence = (row.get("mapping_evidence") or "").strip()
            if not evidence:
                raise ValueError(
                    f"Row for {slug!r} has empty mapping_evidence; "
                    "every curated row must cite its source."
                )
            if slug in out:
                raise ValueError(f"duplicate issuer_slug: {slug}")
            out[slug] = {
                "currentMunicipalityCode": row["current_municipality_ehak"].strip(),
                "mappingSource": row["mapping_source"].strip(),
                "mappingEvidence": evidence,
            }
    return out
