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
import sys
from datetime import datetime
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
    files: list[Path] = []
    files.extend(KRR_DIR.rglob("*.json"))
    files.extend(KRR_DIR.rglob("*.jsonld"))
    files.extend(SCRIPTS_DIR.glob("*.py"))
    shacl_dir = PROJECT_ROOT / "shacl"
    if shacl_dir.exists():
        files.extend(shacl_dir.glob("*.ttl"))
    docs_dir = PROJECT_ROOT / "docs"
    if docs_dir.exists():
        files.extend(docs_dir.rglob("*.md"))
    return sorted(set(files))


def build_rename_map(
    registry: dict, scan_paths: list[Path] | None = None
) -> dict[str, str]:
    """Build old-URI -> new-URI rename mapping from the registry."""
    # Build lookup maps
    prefix_remap: dict[str, str] = {}   # old_prefix -> new_abbrev
    slug_to_abbrev: dict[str, str] = {} # slug -> new_abbrev

    for slug, entry in registry.items():
        abbrev = entry["abbrev"]
        old_prefix = entry.get("old_prefix", "")
        slug_to_abbrev[slug] = abbrev
        if old_prefix and old_prefix != abbrev:
            prefix_remap[old_prefix] = abbrev

    sorted_prefixes = sorted(prefix_remap.keys(), key=len, reverse=True)
    sorted_slugs = sorted(slug_to_abbrev.keys(), key=len, reverse=True)

    # Scan files for all unique estleg: IRIs
    files = scan_paths if scan_paths is not None else get_all_scannable_files()
    all_iris: set[str] = set()
    for fp in files:
        try:
            content = fp.read_text(encoding="utf-8")
            all_iris.update(ESTLEG_RE.findall(content))
        except (OSError, UnicodeDecodeError):
            continue

    print(f"  Scanned {len(files)} files, found {len(all_iris)} unique IRIs")

    rename_map: dict[str, str] = {}

    for iri in sorted(all_iris):
        local = iri[7:]  # strip "estleg:"
        new_iri = None

        # 1. LegalConcept_{X} -> Concept_{X}
        if local.startswith("LegalConcept_"):
            new_iri = "estleg:Concept_" + local[13:]

        # 2. LegalProvision_{slug}[_osa{N}] -> LegalProvision_{abbrev}[_osa{N}]
        elif local.startswith("LegalProvision_"):
            rest = local[15:]
            for slug in sorted_slugs:
                if rest == slug or rest.startswith(slug + "_osa"):
                    new_rest = rest.replace(slug, slug_to_abbrev[slug], 1)
                    new_iri = "estleg:LegalProvision_" + new_rest
                    break

        # 3. Cluster_{prefix}_{label} -> Cluster_{new}_{label}
        elif local.startswith("Cluster_"):
            rest = local[8:]
            for old_prefix in sorted_prefixes:
                if rest.startswith(old_prefix + "_"):
                    new_rest = prefix_remap[old_prefix] + rest[len(old_prefix):]
                    new_iri = "estleg:Cluster_" + new_rest
                    break

        # 4. {prefix}_* patterns (Map, Par, Chapter, Division, TopicScheme, Osa)
        else:
            for old_prefix in sorted_prefixes:
                if local.startswith(old_prefix + "_"):
                    suffix = local[len(old_prefix):]
                    new_iri = "estleg:" + prefix_remap[old_prefix] + suffix
                    break

        # 5. Fix VOS-style missing underscore: _Par{N} -> _Par_{N}
        if new_iri:
            new_iri = PAR_NO_UNDERSCORE_RE.sub(r"_Par_\1", new_iri)
        elif PAR_NO_UNDERSCORE_RE.search(iri):
            new_iri = PAR_NO_UNDERSCORE_RE.sub(r"_Par_\1", iri)

        if new_iri and new_iri != iri:
            rename_map[iri] = new_iri

    return rename_map


def dry_run_cmd() -> None:
    """Preview what the migration would change without modifying files."""
    print("=" * 70)
    print("Phase 2: Dry run")
    print("=" * 70)

    if not REGISTRY_PATH.exists():
        print("ERROR: Registry not found. Run 'build-registry' first.")
        sys.exit(1)

    with open(REGISTRY_PATH, encoding="utf-8") as f:
        registry = json.load(f)

    rename_map = build_rename_map(registry)

    # Check for collisions
    new_iris = list(rename_map.values())
    collision_set: set[str] = set()
    seen: set[str] = set()
    for v in new_iris:
        if v in seen:
            collision_set.add(v)
        seen.add(v)
    collisions = sorted(collision_set)

    # Count per-file impact
    files = get_all_scannable_files()
    files_affected: dict[str, int] = {}
    py_hardcoded: dict[str, list[str]] = {}

    for fp in files:
        try:
            content = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        count = sum(content.count(old) for old in rename_map)
        if count > 0:
            rel = str(fp.relative_to(PROJECT_ROOT))
            files_affected[rel] = count
            if fp.suffix == ".py":
                py_iris = [old for old in rename_map if old in content]
                if py_iris:
                    py_hardcoded[rel] = py_iris

    report = {
        "generated": datetime.now().isoformat(),
        "total_renames": len(rename_map),
        "collisions": collisions,
        "files_affected_count": len(files_affected),
        "total_replacements": sum(files_affected.values()),
        "renames": rename_map,
        "files_affected": files_affected,
        "py_hardcoded_iris": py_hardcoded,
    }

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\nRename map: {len(rename_map)} unique IRIs to change")
    print(f"Total replacements: {sum(files_affected.values())}")
    print(f"Files affected: {len(files_affected)}")
    print(f"Collisions: {len(collisions)}")
    if collisions:
        print("  COLLISION DETAILS (must be resolved before apply):")
        for c in collisions[:10]:
            sources = [k for k, v in rename_map.items() if v == c]
            print(f"    {c} <- {sources}")
    if py_hardcoded:
        print(f"Python files with hardcoded IRIs: {len(py_hardcoded)}")
        for fp, iris in py_hardcoded.items():
            print(f"  {fp}: {len(iris)} IRIs")
    print(f"\nReport written to {REPORT_PATH}")
    print("Zero files modified.")


def apply_renames_to_file(
    filepath: Path, sorted_renames: list[tuple[str, str]]
) -> int:
    """Apply URI renames to a single file. Returns count of replacements made."""
    try:
        content = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0

    original = content
    found_iris = set(ESTLEG_RE.findall(content))
    relevant = [(old, new) for old, new in sorted_renames if old in found_iris]

    for old_iri, new_iri in relevant:
        content = content.replace(old_iri, new_iri)

    if content != original:
        filepath.write_text(content, encoding="utf-8")
        return len(relevant)
    return 0


def verify_migration(rename_map: dict[str, str]) -> tuple[bool, list[str]]:
    """Verify that migration was applied correctly. Returns (ok, list_of_issues)."""
    issues: list[str] = []
    files = get_all_scannable_files()
    old_iris = set(rename_map.keys())
    remaining: dict[str, list[str]] = {}

    for fp in files:
        try:
            content = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found = old_iris & set(ESTLEG_RE.findall(content))
        if found:
            rel = str(fp.relative_to(PROJECT_ROOT))
            for iri in found:
                remaining.setdefault(iri, []).append(rel)

    if remaining:
        issues.append(f"FAIL: {len(remaining)} old IRIs still present in files")
        for iri, fps in sorted(remaining.items())[:20]:
            issues.append(f"  {iri} in {fps[0]} (+{len(fps)-1} more)")

    return len(issues) == 0, issues


def apply_cmd() -> None:
    """Apply the URI migration to all files."""
    print("=" * 70)
    print("Phase 3: Applying migration")
    print("=" * 70)

    if not REPORT_PATH.exists():
        print("ERROR: Dry-run report not found. Run 'dry-run' first.")
        sys.exit(1)

    with open(REPORT_PATH, encoding="utf-8") as f:
        report = json.load(f)

    if report.get("collisions"):
        print("ERROR: Dry-run detected collisions. Fix registry and re-run dry-run.")
        for c in report["collisions"]:
            print(f"  {c}")
        sys.exit(1)

    rename_map: dict[str, str] = report["renames"]
    if not rename_map:
        print("Nothing to rename.")
        return

    sorted_renames = sorted(
        rename_map.items(), key=lambda x: len(x[0]), reverse=True
    )

    files = get_all_scannable_files()
    total_files_changed = 0
    total_renames_applied = 0

    for fp in files:
        count = apply_renames_to_file(fp, sorted_renames)
        if count > 0:
            total_files_changed += 1
            total_renames_applied += count
            rel = fp.relative_to(PROJECT_ROOT)
            print(f"  {rel}: {count} IRIs renamed")

    print(f"\nApplied renames to {total_files_changed} files")
    print(f"Total IRI types renamed: {total_renames_applied}")

    print("\nRunning post-migration verification...")
    passed, issues = verify_migration(rename_map)

    if passed:
        print("VERIFICATION PASSED: All old IRIs replaced successfully.")
    else:
        print("VERIFICATION FAILED:")
        for issue in issues:
            print(f"  {issue}")
        sys.exit(1)


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
