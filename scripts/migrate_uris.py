#!/usr/bin/env python3
"""Compact URI naming migration for the Estonian Legal Ontology.

Replaces long slugified IRI prefixes (e.g. estleg:alkoholi_tubaka_kutuse_ja_...)
with compact abbreviations (e.g. estleg:ATKES_Par_1).

Part of GitHub issue #83.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KRR_DIR = PROJECT_ROOT / "krr_outputs"
DATA_DIR = PROJECT_ROOT / "data"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
REGISTRY_PATH = DATA_DIR / "law_abbreviations.json"
REPORT_PATH = DATA_DIR / "uri_migration_report.json"
MIGRATION_STATE_PATH = DATA_DIR / "migration_state.json"

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
# Permissive scanner used by the post-apply format check; tolerates Estonian
# unicode characters that legitimately appear in rt_api abbreviations
# (e.g. estleg:TsÜS_Par_70, estleg:Cluster_ÕÕS_4).
ESTLEG_FULL_RE = re.compile(
    r"estleg:[A-Za-z0-9_ÄÖÕÜäöõü"
    r"ŠŽšž]+"
)
# Canonical format for a fully-migrated IRI: starts with a letter (ASCII or
# Estonian special) and continues with letters/digits/underscores. Used to
# fail-fast on any mangled rename target before we touch the corpus.
NEW_IRI_FORMAT_RE = re.compile(
    r"^estleg:[A-Za-zÄÖÕÜäöõü"
    r"ŠŽšž]"
    r"[A-Za-z0-9_ÄÖÕÜäöõü"
    r"ŠŽšž]*$"
)
PAR_NO_UNDERSCORE_RE = re.compile(r"_Par(\d)")
SHORT_PREFIX_THRESHOLD = 12
DEFAULT_REWRITE_SUFFIXES = {".json", ".jsonld", ".ttl", ".md"}
TOKEN_BOUNDARY_RE = r"(?<![A-Za-z0-9_])({})(?![A-Za-z0-9_])"


# ── Helpers: atomic IO, hashing, state ────────────────────────────────────────

def _atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically.

    Writes to a sibling tempfile and then ``os.replace``s it into place so a
    crash mid-write leaves the original file (or the new file) intact, never a
    half-written hybrid. The tempfile shares the destination directory so the
    rename stays on the same filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def compute_registry_hash(registry_path: Path = REGISTRY_PATH) -> str:
    """Return ``sha256`` of the registry file's raw bytes.

    Used to bind a dry-run report to the exact registry it was generated from
    so a stale report cannot be applied against a mutated registry.
    """
    return hashlib.sha256(registry_path.read_bytes()).hexdigest()


def compute_corpus_hash(files: list[Path]) -> str:
    """Return ``sha256`` over the sorted (path, content) pairs of ``files``.

    The hash is content-addressed: it changes if any file's bytes change or if
    the set of in-scope files changes. We sort by relative path for
    determinism across runs.
    """
    h = hashlib.sha256()
    for fp in sorted(files):
        try:
            rel = fp.relative_to(PROJECT_ROOT).as_posix().encode("utf-8")
        except ValueError:
            rel = str(fp).encode("utf-8")
        try:
            data = fp.read_bytes()
        except OSError:
            data = b""
        h.update(len(rel).to_bytes(4, "big"))
        h.update(rel)
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return h.hexdigest()


def load_migration_state() -> dict | None:
    """Return the persisted migration state, or ``None`` if it has never run."""
    if not MIGRATION_STATE_PATH.exists():
        return None
    try:
        with open(MIGRATION_STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_migration_state(state: dict) -> None:
    """Persist ``state`` atomically to ``MIGRATION_STATE_PATH``."""
    _atomic_write_text(
        MIGRATION_STATE_PATH,
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
    )


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

def assign_abbreviations(
    peep_data: dict[str, dict], rt_abbrevs: dict[str, str]
) -> tuple[dict[str, dict], dict[str, int]]:
    """Assign abbreviations to laws with strict source-priority collision rules.

    Priority (highest first):
        1. ``rt_api`` — official Riigi Teataja abbreviations always keep their
           bare form. They are reserved first so a later auto-derived law
           cannot squat on the same letters.
        2. ``existing`` — short prefixes already used in the corpus take
           precedence over auto-derived ones, but yield to rt_api.
        3. ``auto`` — derived from the title; suffixed with ``_2``, ``_3``…
           when colliding with anything already reserved.

    Within each tier we iterate in slug-sorted order for deterministic output.
    """
    registry: dict[str, dict] = {}
    used_abbrevs: dict[str, str] = {}  # abbrev -> slug
    stats = {"rt_api": 0, "existing": 0, "auto": 0}

    # Classify every slug into its source tier up front so we can do a strict
    # three-pass assignment.
    classified: dict[str, list[tuple[str, dict, str]]] = {
        "rt_api": [],
        "existing": [],
        "auto": [],
    }
    for slug, info in sorted(peep_data.items()):
        title = info["title"]
        current_prefix = info["prefix"]
        if slug in rt_abbrevs:
            classified["rt_api"].append((slug, info, rt_abbrevs[slug]))
        elif len(current_prefix) <= SHORT_PREFIX_THRESHOLD:
            classified["existing"].append((slug, info, current_prefix))
        else:
            classified["auto"].append(
                (slug, info, auto_derive_abbreviation(title, slug))
            )

    def _reserve(slug: str, info: dict, candidate: str, source: str) -> None:
        """Reserve ``candidate`` for ``slug``, suffixing on collision."""
        abbrev = candidate
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
            "title": info["title"],
            "old_prefix": info["prefix"],
        }

    # Pass 1 — rt_api claims its bare abbrevs first.
    for slug, info, candidate in classified["rt_api"]:
        _reserve(slug, info, candidate, "rt_api")
    # Pass 2 — existing short prefixes claim next; only suffixed if they
    # collide with an rt_api reservation.
    for slug, info, candidate in classified["existing"]:
        _reserve(slug, info, candidate, "existing")
    # Pass 3 — auto-derived comes last so it never displaces a higher tier.
    for slug, info, candidate in classified["auto"]:
        _reserve(slug, info, candidate, "auto")

    return registry, stats


def build_registry_cmd() -> None:
    """Build the abbreviation registry (law_abbreviations.json)."""
    print("=" * 70)
    print("Phase 1: Building abbreviation registry")
    print("=" * 70)

    peep_data = load_peep_prefixes()
    print(f"\nLoaded {len(peep_data)} laws from peep files")

    rt_abbrevs = fetch_rt_abbreviations()

    registry, stats = assign_abbreviations(peep_data, rt_abbrevs)

    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        REGISTRY_PATH,
        json.dumps(registry, indent=2, ensure_ascii=False),
    )

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

    # Stamp the report with content-addressed hashes so apply_cmd can refuse
    # to consume a report that was generated against a different registry or
    # a corpus that has since changed under our feet.
    registry_hash = compute_registry_hash(REGISTRY_PATH)
    corpus_hash = compute_corpus_hash(files)

    report = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "registry_hash": registry_hash,
        "corpus_hash": corpus_hash,
        "total_renames": len(rename_map),
        "collisions": collisions,
        "files_affected_count": len(files_affected),
        "total_replacements": sum(files_affected.values()),
        "renames": rename_map,
        "files_affected": files_affected,
        "py_hardcoded_iris": py_hardcoded,
    }

    _atomic_write_text(
        REPORT_PATH,
        json.dumps(report, indent=2, ensure_ascii=False),
    )

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
    print(f"  registry_hash: {registry_hash[:12]}…")
    print(f"  corpus_hash:   {corpus_hash[:12]}…")
    print("Zero files modified.")


def apply_renames_to_file(
    filepath: Path,
    sorted_renames: list[tuple[str, str]],
    *,
    rewrite_python: bool = False,
) -> int:
    """Apply URI renames to a single file. Returns count of replacements made."""
    if filepath.suffix == ".py" and not rewrite_python:
        return 0
    if filepath.suffix != ".py" and filepath.suffix not in DEFAULT_REWRITE_SUFFIXES:
        return 0
    try:
        content = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0

    original = content
    found_iris = set(ESTLEG_RE.findall(content))
    relevant = [(old, new) for old, new in sorted_renames if old in found_iris]
    if not relevant:
        return 0

    rename_lookup = dict(relevant)
    pattern = re.compile(TOKEN_BOUNDARY_RE.format("|".join(re.escape(old) for old, _ in relevant)))
    content, replacements = pattern.subn(lambda m: rename_lookup[m.group(1)], content)

    if content != original:
        _atomic_write_text(filepath, content)
        return replacements
    return 0


def verify_migration(rename_map: dict[str, str]) -> tuple[bool, list[str]]:
    """Verify that the migration plan and applied corpus are consistent.

    Three independent checks:

    1. **Plan-time collision check** — no two distinct old IRIs may collapse
       to the same new IRI. (We catch this via reverse-mapping the rename
       map; if any new IRI has more than one source, the plan itself is
       broken regardless of corpus state.)
    2. **Plan-time format check** — every new IRI must match the canonical
       ``estleg:<token>`` format. If a malformed IRI made it into the
       rename plan it would corrupt the corpus on apply.
    3. **Post-apply corpus check** — no old IRI should remain anywhere in
       the scanned corpus, and every new IRI we now find must also match
       the canonical format (defends against accidental token-level
       mangling during apply, e.g. partial replacement leaving
       ``estleg:_Par_1``).

    Returns ``(ok, list_of_issues)`` where ``ok`` is True iff every check
    passed. Issues are emitted with file:line context where possible.
    """
    issues: list[str] = []

    # 1. Plan-time collision check — many-to-one collapse is a planning bug,
    #    not a corpus bug, so we can detect it without reading any files.
    reverse_map: dict[str, list[str]] = {}
    for old_iri, new_iri in rename_map.items():
        reverse_map.setdefault(new_iri, []).append(old_iri)
    collapses = {new: olds for new, olds in reverse_map.items() if len(olds) > 1}
    if collapses:
        issues.append(
            f"FAIL: {len(collapses)} new IRIs have multiple old-IRI sources "
            "(plan-time collision)"
        )
        for new_iri, olds in sorted(collapses.items())[:20]:
            issues.append(f"  {new_iri} <- {sorted(olds)}")

    # 2. Plan-time format check — refuse to ship malformed targets.
    bad_format = sorted(
        v for v in set(rename_map.values()) if not NEW_IRI_FORMAT_RE.match(v)
    )
    if bad_format:
        issues.append(
            f"FAIL: {len(bad_format)} new IRIs do not match the canonical "
            f"format {NEW_IRI_FORMAT_RE.pattern!r}"
        )
        for iri in bad_format[:20]:
            issues.append(f"  {iri}")

    # 3. Post-apply corpus check.
    files = get_all_scannable_files()
    old_iris = set(rename_map.keys())
    remaining: dict[str, list[str]] = {}
    bad_corpus: dict[str, list[str]] = {}

    for fp in files:
        try:
            content = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(fp.relative_to(PROJECT_ROOT))

        # 3a. Old IRIs lingering after apply.
        found = old_iris & set(ESTLEG_RE.findall(content))
        for iri in found:
            remaining.setdefault(iri, []).append(_locate_iri(content, iri, rel))

        # 3b. New IRIs that fail the canonical format check. We use the
        #     unicode-aware scanner so Estonian-letter abbreviations (TsÜS,
        #     ÕÕS, …) round-trip cleanly; only genuine garbage trips this.
        for iri in set(ESTLEG_FULL_RE.findall(content)):
            if not NEW_IRI_FORMAT_RE.match(iri):
                bad_corpus.setdefault(iri, []).append(
                    _locate_iri(content, iri, rel)
                )

    if remaining:
        issues.append(f"FAIL: {len(remaining)} old IRIs still present in files")
        for iri, locations in sorted(remaining.items())[:20]:
            head = locations[0]
            extra = f" (+{len(locations)-1} more)" if len(locations) > 1 else ""
            issues.append(f"  {iri} in {head}{extra}")

    if bad_corpus:
        issues.append(
            f"FAIL: {len(bad_corpus)} corpus IRIs do not match the canonical "
            f"format {NEW_IRI_FORMAT_RE.pattern!r}"
        )
        for iri, locations in sorted(bad_corpus.items())[:20]:
            head = locations[0]
            extra = f" (+{len(locations)-1} more)" if len(locations) > 1 else ""
            issues.append(f"  {iri} in {head}{extra}")

    return len(issues) == 0, issues


def _locate_iri(content: str, iri: str, rel: str) -> str:
    """Return ``rel:line`` for the first occurrence of ``iri`` in ``content``.

    Falls back to the bare relative path when the IRI cannot be located on
    a line boundary (which would only happen for a freshly-modified file
    whose content is no longer in memory; defensive).
    """
    idx = content.find(iri)
    if idx < 0:
        return rel
    line_no = content.count("\n", 0, idx) + 1
    return f"{rel}:{line_no}"


def _already_migrated_per_state(state: dict | None) -> bool:
    """Return True iff ``migration_state.json`` says the corpus is current.

    "Current" means the persisted ``last_applied_registry_hash`` /
    ``last_applied_corpus_hash`` both equal what the hash helpers recompute
    over the registry and the scannable corpus *right now*. When that holds,
    a fresh ``dry-run`` would necessarily produce zero (real) renames, so
    ``apply`` has nothing to do — even if the on-disk dry-run report is
    stale, absent, or predates the hash-stamping added in #178.

    Practically this is the path that fires when someone re-runs ``apply`` on
    a corpus that has already been migrated (commits 3880cb688 / c5c0f88b2):
    the migration state confirms there is nothing outstanding, so we no-op
    instead of demanding a fresh ``dry-run`` report just to learn the same.
    """
    if state is None or not REGISTRY_PATH.exists():
        return False
    prev_registry = state.get("last_applied_registry_hash")
    prev_corpus = state.get("last_applied_corpus_hash")
    if not prev_registry or not prev_corpus:
        return False
    if compute_registry_hash(REGISTRY_PATH) != prev_registry:
        return False
    return compute_corpus_hash(get_all_scannable_files()) == prev_corpus


def apply_cmd(*, rewrite_python: bool = False, force_reapply: bool = False) -> None:
    """Apply the URI migration to all files.

    Behaviour:

    0. **Fast path** — if ``migration_state.json`` records that the corpus is
       already migrated against the *current* registry+corpus, no-op (unless
       ``--force-reapply``). This makes ``apply`` idempotent without needing a
       fresh ``dry-run`` report.
    1. Otherwise a dry-run report must exist and be hash-bound to the current
       registry and corpus (so a stale plan can't quietly clobber files).
    2. Either no migration has ever been applied, or ``--force-reapply`` was
       passed; a *different* recorded migration aborts with guidance.

    On success we update ``migration_state.json`` so subsequent runs can
    detect drift.
    """
    print("=" * 70)
    print("Phase 3: Applying migration")
    print("=" * 70)

    # Fast path: if migration_state.json records that the corpus is already
    # migrated against the *current* registry+corpus, there is nothing to do.
    # We honour ``--force-reapply`` so an operator can deliberately re-run a
    # migration even when the state says it's a no-op (e.g. to repair files).
    state = load_migration_state()
    if not force_reapply and _already_migrated_per_state(state):
        print(
            "Corpus is already migrated per "
            f"{MIGRATION_STATE_PATH.name} "
            f"(last applied at {state.get('last_applied_at')}); nothing to do."
        )
        return

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

    # Finding 1 — bind the dry-run report to the registry+corpus that produced
    # it. A stale report has the wrong hashes; refusing to consume it forces
    # the operator to re-run dry-run after any registry mutation.
    report_registry_hash = report.get("registry_hash")
    report_corpus_hash = report.get("corpus_hash")
    if not report_registry_hash or not report_corpus_hash:
        print(
            "ERROR: Dry-run report is missing hash stamps. Re-run "
            "'dry-run' to regenerate it."
        )
        sys.exit(1)

    if not REGISTRY_PATH.exists():
        print("ERROR: Registry not found. Run 'build-registry' first.")
        sys.exit(1)

    current_registry_hash = compute_registry_hash(REGISTRY_PATH)
    if current_registry_hash != report_registry_hash:
        print(
            "ERROR: Registry has changed since the dry-run was generated.\n"
            f"  expected: {report_registry_hash}\n"
            f"  actual:   {current_registry_hash}\n"
            "  Re-run 'dry-run' to regenerate the report against the "
            "current registry."
        )
        sys.exit(1)

    files = get_all_scannable_files()
    current_corpus_hash = compute_corpus_hash(files)
    if current_corpus_hash != report_corpus_hash:
        print(
            "ERROR: Corpus has changed since the dry-run was generated.\n"
            f"  expected: {report_corpus_hash}\n"
            f"  actual:   {current_corpus_hash}\n"
            "  Re-run 'dry-run' to regenerate the report against the "
            "current corpus."
        )
        sys.exit(1)

    # Finding 2 — idempotency sentinel. Without this guard, a fresh registry
    # whose ``old_prefix`` happens to collide with an already-migrated abbrev
    # would silently rename across the wrong axis on a second apply. ``state``
    # was already loaded above for the fast-path check.
    #
    # By the time we reach here the hard checks above have proven
    # ``report_registry_hash == current registry hash`` and
    # ``report_corpus_hash == current corpus hash``. If the persisted state
    # *also* matched both of those, ``_already_migrated_per_state`` would have
    # short-circuited at the top of the function — so the only way control
    # gets here with a non-None ``state`` is that the recorded run differs
    # from the one this report describes (a *new* migration stacked on an old
    # one). That requires an explicit ``--force-reapply``.
    if state is not None and not force_reapply:
        prev_registry = state.get("last_applied_registry_hash")
        prev_corpus = state.get("last_applied_corpus_hash")
        print(
            "ERROR: A different migration is already recorded in "
            f"{MIGRATION_STATE_PATH.name}.\n"
            f"  last applied at:        {state.get('last_applied_at')}\n"
            f"  last registry hash:     {prev_registry}\n"
            f"  last corpus hash:       {prev_corpus}\n"
            f"  current report hashes:  {report_registry_hash} / "
            f"{report_corpus_hash}\n"
            "  Pass --force-reapply if you really intend to apply a new "
            "migration on top of the previous one."
        )
        sys.exit(1)

    rename_map: dict[str, str] = report["renames"]
    if not rename_map:
        print("Nothing to rename.")
        # Still record a state so future runs know nothing was outstanding.
        save_migration_state(
            {
                "last_applied_at": datetime.now(timezone.utc).isoformat(),
                "last_applied_registry_hash": report_registry_hash,
                "last_applied_corpus_hash": report_corpus_hash,
                "total_renames_applied": 0,
                "files_changed": 0,
            }
        )
        return

    # Plan-time verification (collision + format) before mutating any file —
    # this is cheap and prevents writing a corrupted corpus.
    plan_ok, plan_issues = _verify_plan(rename_map)
    if not plan_ok:
        print("VERIFICATION FAILED before apply (plan-time):")
        for issue in plan_issues:
            print(f"  {issue}")
        sys.exit(1)

    sorted_renames = sorted(
        rename_map.items(), key=lambda x: len(x[0]), reverse=True
    )

    total_files_changed = 0
    total_renames_applied = 0

    for fp in files:
        count = apply_renames_to_file(fp, sorted_renames, rewrite_python=rewrite_python)
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
        save_migration_state(
            {
                "last_applied_at": datetime.now(timezone.utc).isoformat(),
                "last_applied_registry_hash": report_registry_hash,
                "last_applied_corpus_hash": report_corpus_hash,
                "total_renames_applied": total_renames_applied,
                "files_changed": total_files_changed,
            }
        )
    else:
        print("VERIFICATION FAILED:")
        for issue in issues:
            print(f"  {issue}")
        sys.exit(1)


def _verify_plan(rename_map: dict[str, str]) -> tuple[bool, list[str]]:
    """Return (ok, issues) for the plan-time half of ``verify_migration``.

    Reuses the same checks but skips the corpus scan so we can fail fast
    *before* touching any files on disk.
    """
    issues: list[str] = []
    reverse_map: dict[str, list[str]] = {}
    for old_iri, new_iri in rename_map.items():
        reverse_map.setdefault(new_iri, []).append(old_iri)
    collapses = {new: olds for new, olds in reverse_map.items() if len(olds) > 1}
    if collapses:
        issues.append(
            f"FAIL: {len(collapses)} new IRIs have multiple old-IRI sources"
        )
        for new_iri, olds in sorted(collapses.items())[:20]:
            issues.append(f"  {new_iri} <- {sorted(olds)}")

    bad_format = sorted(
        v for v in set(rename_map.values()) if not NEW_IRI_FORMAT_RE.match(v)
    )
    if bad_format:
        issues.append(
            f"FAIL: {len(bad_format)} new IRIs do not match the canonical "
            f"format {NEW_IRI_FORMAT_RE.pattern!r}"
        )
        for iri in bad_format[:20]:
            issues.append(f"  {iri}")
    return len(issues) == 0, issues


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
    parser.add_argument(
        "--rewrite-python",
        action="store_true",
        help="Allow apply to rewrite hardcoded estleg IRIs in Python files.",
    )
    parser.add_argument(
        "--force-reapply",
        action="store_true",
        help=(
            "Re-apply a migration even if migration_state.json records a "
            "different prior run. Use only when intentionally chaining "
            "migrations."
        ),
    )
    args = parser.parse_args()

    dispatch = {
        "build-registry": build_registry_cmd,
        "dry-run": dry_run_cmd,
        "apply": lambda: apply_cmd(
            rewrite_python=args.rewrite_python,
            force_reapply=args.force_reapply,
        ),
    }
    dispatch[args.command]()


if __name__ == "__main__":
    main()
