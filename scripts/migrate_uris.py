#!/usr/bin/env python3
"""Compact URI naming migration for the Estonian Legal Ontology.

Replaces long slugified IRI prefixes (e.g. estleg:alkoholi_tubaka_kutuse_ja_...)
with compact abbreviations (e.g. estleg:ATKES_Par_1).

Part of GitHub issue #83.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KRR_DIR = PROJECT_ROOT / "krr_outputs"
DATA_DIR = PROJECT_ROOT / "data"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
REGISTRY_PATH = DATA_DIR / "law_abbreviations.json"
REPORT_PATH = DATA_DIR / "uri_migration_report.json"

# ── Constants ──────────────────────────────────────────────────────────────────

STOP_WORDS = frozenset({
    "seadus", "seaduse", "seadustik", "aasta", "ja", "ning",
    "kohta", "vahel", "vahelise", "rakendamise",
})
TREATY_MARKERS = frozenset({"konventsiooni", "lepingu", "protokolli"})

_ESTONIAN_TRANSLITERATION = {
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
}
_TRANSLIT_TABLE = str.maketrans(_ESTONIAN_TRANSLITERATION)

ESTLEG_RE = re.compile(r"estleg:[A-Za-z0-9_]+")
PAR_NO_UNDERSCORE_RE = re.compile(r"_Par(\d)")
SHORT_PREFIX_THRESHOLD = 12


# ── Task 2: auto_derive_abbreviation ──────────────────────────────────────────

def auto_derive_abbreviation(title: str, slug: str) -> str:
    """Derive a compact abbreviation from a law title.

    Strategy:
    1. Transliterate Estonian characters to ASCII.
    2. Extract significant words (filtering stop words).
    3. Build an acronym from first letters.
    4. For treaties, append the year.
    5. Fall back to slug prefix if result is too short.
    6. Cap at 12 characters.
    """
    clean = title.translate(_TRANSLIT_TABLE)
    words = re.findall(r"[A-Za-z]+", clean)
    significant = [w for w in words if w.lower() not in STOP_WORDS]
    abbrev = "".join(w[0].upper() for w in significant)

    is_treaty = any(marker in slug for marker in TREATY_MARKERS)
    if is_treaty:
        year_match = re.search(r"(\d{4})", title)
        if year_match:
            year = year_match.group(1)
            max_base = 12 - len(year)
            abbrev = abbrev[:max_base] + year

    if len(abbrev) < 3:
        fallback = re.sub(r"[^a-z0-9]", "", slug[:6]).upper()
        abbrev = fallback if len(fallback) >= 3 else slug[:8].upper()

    return abbrev[:12]


# ── Task 3: load_peep_prefixes ─────────────────────────────────────────────────

def load_peep_prefixes() -> dict[str, dict]:
    """Scan krr_outputs/*_peep.json and extract existing IRI prefixes and titles.

    Returns a dict keyed by base slug (without _osaN suffix), containing
    ``{"prefix": ..., "title": ...}`` for each law.
    """
    result: dict[str, dict] = {}
    for path in sorted(KRR_DIR.glob("*_peep.json")):
        name = path.name.replace("_peep.json", "")
        base_slug = re.sub(r"_osa\d+$", "", name)
        if base_slug in result:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        prefix = None
        title = None
        for node in data.get("@graph", []):
            node_type = node.get("@type", [])
            if isinstance(node_type, str):
                node_type = [node_type]
            if "owl:Ontology" not in node_type:
                continue
            iri = node.get("@id", "")
            if not iri.startswith("estleg:"):
                continue
            local = iri[7:]
            if "_Map_2026" in local:
                prefix = local.split("_Map_2026")[0]
            else:
                m = re.match(r"(.+?)_Osa\d+", local)
                if m:
                    prefix = m.group(1)
            title = node.get("dc:source") or node.get("dcterms:title", "")
            break
        if prefix and title:
            result[base_slug] = {"prefix": prefix, "title": title}
    return result


# ── Task 4: fetch_rt_abbreviations ────────────────────────────────────────────

def fetch_rt_abbreviations() -> dict[str, str]:
    """Fetch official law abbreviations from the Riigi Teataja API.

    Returns a dict mapping slugified law title -> official abbreviation.
    Requires generate_all_laws.py to be importable.
    """
    try:
        from generate_all_laws import get_all_laws, slugify
    except ImportError:
        print("WARNING: Could not import generate_all_laws. Skipping RT API abbreviations.")
        return {}
    print("Fetching law metadata from Riigi Teataja API...")
    all_laws = get_all_laws()
    rt_abbrevs: dict[str, str] = {}
    for title, info in all_laws.items():
        lyhend = (info.get("lyhend") or "").strip()
        if lyhend:
            slug = slugify(title)
            rt_abbrevs[slug] = lyhend
    print(f"  Found {len(rt_abbrevs)} official abbreviations from RT API")
    return rt_abbrevs


# ── Stub functions (to be implemented in later tasks) ─────────────────────────

def build_registry_cmd() -> None:
    """Build the abbreviation registry (law_abbreviations.json)."""
    print("=" * 70)
    print("Phase 1: Building abbreviation registry")
    print("=" * 70)

    peep_data = load_peep_prefixes()
    print(f"\nLoaded {len(peep_data)} laws from peep files")

    rt_abbrevs = fetch_rt_abbreviations()

    registry: dict[str, dict] = {}
    used_abbrevs: dict[str, str] = {}  # abbrev -> slug (collision detection)
    stats = {"rt_api": 0, "existing": 0, "auto": 0}

    for slug, info in sorted(peep_data.items()):
        title = info["title"]
        current_prefix = info["prefix"]

        if slug in rt_abbrevs:
            abbrev = rt_abbrevs[slug]
            source = "rt_api"
        elif len(current_prefix) <= SHORT_PREFIX_THRESHOLD:
            abbrev = current_prefix
            source = "existing"
        else:
            abbrev = auto_derive_abbreviation(title, slug)
            source = "auto"

        if abbrev in used_abbrevs and used_abbrevs[abbrev] != slug:
            base = abbrev
            counter = 2
            while f"{base}_{counter}" in used_abbrevs:
                counter += 1
            abbrev = f"{base}_{counter}"
            print(f"  COLLISION resolved: {base} -> {abbrev} for {slug}")

        used_abbrevs[abbrev] = slug
        stats[source] += 1

        registry[slug] = {
            "abbrev": abbrev,
            "source": source,
            "title": title,
            "old_prefix": current_prefix,
        }

    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)

    changes = sum(
        1 for e in registry.values() if e["old_prefix"] != e["abbrev"]
    )
    print(f"\nRegistry written to {REGISTRY_PATH}")
    print(f"  Total laws: {len(registry)}")
    print(f"  RT API abbreviations: {stats['rt_api']}")
    print(f"  Existing short prefixes: {stats['existing']}")
    print(f"  Auto-derived: {stats['auto']}")
    print(f"  Prefix changes needed: {changes}")


def get_all_scannable_files() -> list[Path]:
    """Return all files that should be scanned for URI references."""
    raise NotImplementedError("get_all_scannable_files will be implemented in a later task")


def build_rename_map(
    registry: dict, scan_paths: list[Path] | None = None
) -> dict[str, str]:
    """Build old-URI -> new-URI rename mapping from the registry."""
    raise NotImplementedError("build_rename_map will be implemented in a later task")


def dry_run_cmd() -> None:
    """Preview what the migration would change without modifying files."""
    raise NotImplementedError("dry_run_cmd will be implemented in a later task")


def apply_renames_to_file(
    filepath: Path, sorted_renames: list[tuple[str, str]]
) -> int:
    """Apply URI renames to a single file. Returns count of replacements made."""
    raise NotImplementedError("apply_renames_to_file will be implemented in a later task")


def verify_migration(rename_map: dict[str, str]) -> tuple[bool, list[str]]:
    """Verify that migration was applied correctly. Returns (ok, list_of_issues)."""
    raise NotImplementedError("verify_migration will be implemented in a later task")


def apply_cmd() -> None:
    """Apply the URI migration to all files."""
    raise NotImplementedError("apply_cmd will be implemented in a later task")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    """Parse command-line arguments and dispatch to the appropriate command."""
    parser = argparse.ArgumentParser(
        description="Compact URI naming migration for Estonian Legal Ontology",
    )
    parser.add_argument(
        "command",
        choices=["build-registry", "dry-run", "apply"],
        help="Command to run: build-registry, dry-run, or apply",
    )
    args = parser.parse_args()

    dispatch = {
        "build-registry": build_registry_cmd,
        "dry-run": dry_run_cmd,
        "apply": apply_cmd,
    }
    dispatch[args.command]()


if __name__ == "__main__":
    main()
