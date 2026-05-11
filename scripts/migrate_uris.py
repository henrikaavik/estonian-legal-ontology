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
AMENDMENTS_DIR = KRR_DIR / "amendments"
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

# ── Amendment IRI family (issue #192) ─────────────────────────────────────────
# The amendment-history generator mints three IRI families whose length comes
# from a long ``<base_slug>`` segment rather than the compact abbreviation used
# elsewhere:
#   estleg:Amendment_<base_slug>_<n>           (and ``_<n>_<disambig>``)
#   estleg:AmendmentChain_<base_slug>
#   estleg:AmendmentLink_<draft_id>_<base_slug>
# where ``<base_slug>`` is the law/regulation slug with any trailing ``_osaN``
# already stripped (per the #173 chain aggregation). ``<n>`` is either the
# legacy sequential counter the corpus currently uses, or — for output minted
# by the post-#173 generator — ``md5(rt_reference)[0:10]``; both are accepted.
AMENDMENT_PREFIXES = ("Amendment_", "AmendmentChain_", "AmendmentLink_")
# Suffix shapes that may legitimately follow ``Amendment_<base_slug>_``: the
# legacy decimal counter (optionally with a ``_<disambig>`` tail) or the new
# md5-prefix hash (likewise). Anything else is too ambiguous to decompose.
_AMEND_SUFFIX_RE = re.compile(r"^(?:\d+|[0-9a-f]{10})(?:_\d+)+$|^(?:\d+|[0-9a-f]{10})$")
# A compact prefix the amendment IRIs may carry after migration: an
# abbreviation (ASCII letters/digits/underscore, starts with a letter) that is
# meaningfully shorter than the slug it replaces. ``Reg_<digits>`` (the
# regulation IRI stem) and the rt_api/auto abbreviations all match this.
_COMPACT_PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
# Suffixes stripped from an ``estleg:amends`` target to recover the compact
# stem the amendment IRIs should adopt: ``_Map_<year>`` (or bare ``_Map``) and
# the per-provision ``_Osa<N>...`` / ``_Par_<N>...`` tails.
_AMENDS_STEM_STRIP_RE = re.compile(
    r"_(?:Map_\d+|Map|Osa\d+(?:_.*)?|Par_?\d+(?:_.*)?|Chapter\d+(?:_.*)?"
    r"|Division\d+(?:_.*)?|TopicScheme\d+(?:_.*)?)$"
)
# Hard ceiling on how long a recovered "compact" stem may be before we give up
# and skip the IRI — a stem longer than this is no improvement over the slug.
_AMEND_STEM_MAX_LEN = 24
# Soft length budget surfaced (not enforced) by ``verify_migration``: the
# amendment shortening evicts the worst offenders, but raw-slug ``Concept_*`` /
# ``Institution_*`` nodes and a few long auto-derived law abbreviations remain
# above this and are explicitly out of scope for issue #192 — so the check only
# *reports* the longest remaining ``@id``s rather than failing on them.
ID_LENGTH_REPORT_THRESHOLD = 80


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


# ── Amendment IRI shortening (issue #192) ─────────────────────────────────────


def _stem_from_amends_target(amends_iri: str) -> str | None:
    """Recover a compact prefix from an ``estleg:amends`` target IRI.

    ``estleg:Reg_1052132_Map_2026`` → ``Reg_1052132``;
    ``estleg:KARIST_2_Osa2_88_451`` → ``KARIST_2``;
    ``estleg:AVRS_Map_2026``        → ``AVRS``.

    Returns ``None`` when the target isn't an ``estleg:`` IRI or the
    recovered stem doesn't look like a compact token.
    """
    if not amends_iri.startswith("estleg:"):
        return None
    local = amends_iri[len("estleg:"):]
    # Strip the well-known structural tails. One pass is enough in practice
    # (``_Map_2026`` etc. only appear once), but loop defensively in case a
    # future IRI nests them.
    prev = None
    while prev != local:
        prev = local
        local = _AMENDS_STEM_STRIP_RE.sub("", local)
    if not local or not _COMPACT_PREFIX_RE.match(local):
        return None
    return local


def build_amendment_prefix_map(
    registry: dict, amendments_dir: Path | None = None
) -> tuple[dict[str, str], dict[str, str]]:
    """Map each amendment-chain ``base_slug`` to a compact replacement prefix.

    For every ``amendments_<base_slug>.json`` chain document the resolution
    order is:

    1. ``data/law_abbreviations.json`` — if ``<base_slug>`` is a registered
       law, use its ``abbrev`` (the same compact form used everywhere else).
    2. Otherwise, recover a stem from the chain's ``estleg:amends`` target
       (``Reg_<digits>`` for regulations, the law's compact stem for the
       handful of slug-spelling mismatches). This is the *authoritative*
       binding — the amendment node literally points at the act it amends —
       so it stays correct even when the slug's ``_t<id>`` suffix disagrees
       with the act's numeric id.
    3. If neither yields a token that is a genuine improvement over the slug,
       the slug is left unchanged and the reason recorded.

    Returns ``(base_slug -> compact_prefix, base_slug -> skip_reason)``. A
    ``base_slug`` appears in exactly one of the two maps.
    """
    amendments_dir = amendments_dir if amendments_dir is not None else AMENDMENTS_DIR
    prefix_map: dict[str, str] = {}
    skipped: dict[str, str] = {}
    if not amendments_dir.is_dir():
        return prefix_map, skipped

    for path in sorted(amendments_dir.glob("amendments_*.json")):
        base_slug = path.name[len("amendments_"):-len(".json")]
        if not base_slug:
            continue
        # 1. Registry hit — the canonical compact abbreviation.
        reg_entry = registry.get(base_slug)
        if isinstance(reg_entry, dict) and reg_entry.get("abbrev"):
            prefix_map[base_slug] = reg_entry["abbrev"]
            continue
        # 2. Derive from the chain's amends target.
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            skipped[base_slug] = f"unreadable chain file: {type(exc).__name__}"
            continue
        amends_target = None
        for node in doc.get("@graph", []):
            if not isinstance(node, dict):
                continue
            amends = node.get("estleg:amends")
            if isinstance(amends, dict) and isinstance(amends.get("@id"), str):
                amends_target = amends["@id"]
                break
        if not amends_target:
            skipped[base_slug] = "no estleg:amends target in chain document"
            continue
        stem = _stem_from_amends_target(amends_target)
        if not stem:
            skipped[base_slug] = (
                f"amends target {amends_target!r} did not yield a compact stem"
            )
            continue
        if len(stem) > _AMEND_STEM_MAX_LEN or len(stem) >= len(base_slug):
            skipped[base_slug] = (
                f"recovered stem {stem!r} ({len(stem)} chars) is no shorter "
                f"than the slug ({len(base_slug)} chars)"
            )
            continue
        prefix_map[base_slug] = stem

    return prefix_map, skipped


def _shorten_amendment_iri(
    iri: str,
    base_slugs_by_len: list[str],
    prefix_map: dict[str, str],
    unmatched: set[str] | None = None,
    known_prefixes: set[str] | None = None,
) -> str | None:
    """Return the shortened form of an ``Amendment_*`` family IRI, or ``None``.

    ``iri`` is the full ``estleg:`` IRI. Decomposition:

    * ``AmendmentChain_<base_slug>`` → ``AmendmentChain_<prefix>``.
    * ``Amendment_<base_slug>_<n>``  → ``Amendment_<prefix>_<n>`` where ``<n>``
      matches :data:`_AMEND_SUFFIX_RE` (the legacy decimal counter or the
      md5-prefix hash, optionally followed by a ``_<disambig>`` tail).
    * ``AmendmentLink_<draft_id>_<base_slug>`` →
      ``AmendmentLink_<draft_id>_<prefix>`` where ``<draft_id>`` starts with
      ``Draft_`` (matching how the generator mints these IRIs).

    ``base_slugs_by_len`` must be sorted longest-first so the most specific
    slug wins (``karistusseadustiku_rakendamise_seadus`` before
    ``karistusseadustik``). ``known_prefixes`` (when given) is the set of
    *already-compact* replacement prefixes; an IRI that already ends in one of
    those is treated as migrated and *not* recorded as unmatched (this keeps
    re-running the dry-run on an already-shortened corpus quiet). When the IRI
    looks like an unmigrated amendment IRI but no confident decomposition is
    possible, it is recorded in ``unmatched`` (if provided) and ``None``
    returned.
    """
    if not iri.startswith("estleg:"):
        return None
    local = iri[len("estleg:"):]

    def _record_unmatched(slug_candidate: str) -> None:
        # Only flag IRIs that genuinely *look* unshortened: a long, mostly
        # lowercase segment. Already-compact prefixes (uppercase abbreviations,
        # ``Reg_<digits>``) and ``ESTLEG_RE``-truncated Unicode fragments
        # (``..._`` with nothing after, or anything in ``known_prefixes``) are
        # not anomalies and stay silent.
        if unmatched is None:
            return
        if known_prefixes is not None and slug_candidate in known_prefixes:
            return
        if re.fullmatch(r"Reg_\d+", slug_candidate):
            return
        # Heuristic for "this still has a long slug in it".
        if len(slug_candidate) >= 12 and any(c.islower() for c in slug_candidate):
            unmatched.add(iri)

    for family in AMENDMENT_PREFIXES:
        if not local.startswith(family):
            continue
        rest = local[len(family):]
        # ``rest == ""`` only happens when the "IRI" is actually a Python
        # f-string fragment like ``f"estleg:Amendment_{prefix}_..."`` picked up
        # by the permissive scanner. ``rest`` ending in ``_`` means ``ESTLEG_RE``
        # truncated a Unicode-letter abbreviation (``..._ÕÕS``). Neither is a
        # corpus IRI — ignore silently.
        if not rest or rest.endswith("_"):
            return None
        if family == "AmendmentChain_":
            prefix = prefix_map.get(rest)
            if prefix and prefix != rest:
                return "estleg:" + family + prefix
            return None
        if family == "Amendment_":
            for bs in base_slugs_by_len:
                if rest == bs:
                    # An Amendment_<base_slug> with no numeric tail — not a
                    # shape the generator produces; leave it.
                    break
                marker = bs + "_"
                if rest.startswith(marker):
                    tail = rest[len(marker):]
                    if not _AMEND_SUFFIX_RE.match(tail):
                        continue
                    prefix = prefix_map.get(bs)
                    if prefix and prefix != bs:
                        return "estleg:" + family + prefix + "_" + tail
                    return None
            m = re.match(r"^(.*?)_(?:\d+|[0-9a-f]{10})(?:_\d+)*$", rest)
            _record_unmatched(m.group(1) if m else rest)
            return None
        if family == "AmendmentLink_":
            # rest == "<draft_id>_<base_slug>"; the draft id starts with
            # "Draft_". Match the longest base_slug that is a trailing
            # underscore-delimited segment with a Draft_-prefixed remainder.
            for bs in base_slugs_by_len:
                marker = "_" + bs
                if rest.endswith(marker) and len(rest) > len(marker):
                    draft_id = rest[: -len(marker)]
                    if not draft_id.startswith("Draft_"):
                        continue
                    prefix = prefix_map.get(bs)
                    if prefix and prefix != bs:
                        return "estleg:" + family + draft_id + "_" + prefix
                    return None
            # The trailing segment is the slug-or-prefix. For ``Reg_<digits>``
            # the regulation stem spans two underscore-segments, so peel both.
            m = re.search(r"_(Reg_\d+)$", rest)
            _record_unmatched(m.group(1) if m else rest.rsplit("_", 1)[-1])
            return None
    return None


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


# IRI-family selectors recognised by ``build_rename_map``'s ``families`` arg.
RENAME_FAMILY_LEGACY = "legacy"   # LegalConcept_/LegalProvision_/Cluster_/<prefix>_*
RENAME_FAMILY_AMENDMENT = "amendment"  # Amendment_/AmendmentChain_/AmendmentLink_
RENAME_FAMILY_SANCTION = "sanction"    # Sanction_/Sanctions_
ALL_RENAME_FAMILIES = frozenset(
    {RENAME_FAMILY_LEGACY, RENAME_FAMILY_AMENDMENT, RENAME_FAMILY_SANCTION}
)


def build_rename_map(
    registry: dict,
    scan_paths: list[Path] | None = None,
    *,
    amendments_dir: Path | None = None,
    skipped_amendments: dict[str, str] | None = None,
    families: frozenset[str] | set[str] | None = None,
) -> dict[str, str]:
    """Build old-URI -> new-URI rename mapping from the registry.

    Covers up to three IRI families (selectable via ``families``):

    * ``legacy`` — the original compact-abbreviation remap for law/regulation
      ``LegalProvision_`` / ``Cluster_`` / ``<prefix>_Par_`` / ``_Map_`` IRIs
      (and the ``LegalConcept_`` → ``Concept_`` rename),
    * ``amendment`` (issue #192) — shortening the ``Amendment_`` /
      ``AmendmentChain_`` / ``AmendmentLink_`` family by substituting the law's
      compact abbreviation — or, for regulations, the ``Reg_<id>`` stem — for
      the long ``<base_slug>`` segment,
    * ``sanction`` — the analogous fix for the long ``Sanctions_<slug>_Map`` /
      ``Sanction_<slug>_Par_…`` IRIs (NOT in the CLI default; its definitions
      live in ``krr_outputs/sanctions/``, outside issue #192's scope).

    ``families=None`` (passed as ``ALL_RENAME_FAMILIES``) means *all* families
    — the historical full remap. The issue #192 migration layer restricts to
    ``{"amendment"}`` so it does not re-touch the already-applied legacy remap
    (the corpus has since drifted in ways that would make blindly re-running
    it on already-compact IRIs corrupt them) nor the sanction definitions.

    ``skipped_amendments`` (if provided) is populated with ``base_slug ->
    reason`` for chains whose slug could not be confidently shortened, so the
    dry-run report can record why.
    """
    if families is None:
        families = ALL_RENAME_FAMILIES
    do_legacy = RENAME_FAMILY_LEGACY in families
    do_amend = RENAME_FAMILY_AMENDMENT in families
    do_sanction = RENAME_FAMILY_SANCTION in families

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

    # Amendment-family base_slug -> compact prefix (issue #192).
    amend_prefix_map: dict[str, str] = {}
    amend_skipped: dict[str, str] = {}
    if do_amend:
        amend_prefix_map, amend_skipped = build_amendment_prefix_map(
            registry, amendments_dir=amendments_dir
        )
        if skipped_amendments is not None:
            skipped_amendments.update(amend_skipped)
    amend_base_slugs_by_len = sorted(amend_prefix_map.keys(), key=len, reverse=True)
    amend_known_prefixes = set(amend_prefix_map.values()) | set(slug_to_abbrev.values())
    unmatched_amendments: set[str] = set()

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
        if do_legacy and local.startswith("LegalConcept_"):
            new_iri = "estleg:Concept_" + local[13:]

        # 2. LegalProvision_{slug}[_osa{N}] -> LegalProvision_{abbrev}[_osa{N}]
        elif do_legacy and local.startswith("LegalProvision_"):
            rest = local[15:]
            for slug in sorted_slugs:
                if rest == slug or rest.startswith(slug + "_osa"):
                    new_rest = rest.replace(slug, slug_to_abbrev[slug], 1)
                    new_iri = "estleg:LegalProvision_" + new_rest
                    break

        # 3. Cluster_{prefix}_{label} -> Cluster_{new}_{label}
        elif do_legacy and local.startswith("Cluster_"):
            rest = local[8:]
            for old_prefix in sorted_prefixes:
                if rest.startswith(old_prefix + "_"):
                    new_rest = prefix_remap[old_prefix] + rest[len(old_prefix):]
                    new_iri = "estleg:Cluster_" + new_rest
                    break

        # 3b. Amendment_ / AmendmentChain_ / AmendmentLink_ family (issue #192):
        #     substitute the law abbreviation (or Reg_<id> stem) for the long
        #     <base_slug> segment.
        elif do_amend and local.startswith(AMENDMENT_PREFIXES):
            new_iri = _shorten_amendment_iri(
                iri,
                amend_base_slugs_by_len,
                amend_prefix_map,
                unmatched=unmatched_amendments,
                known_prefixes=amend_known_prefixes,
            )

        # 3c. Sanctions_<slug>_Map (and singular Sanction_<slug>_…): the long
        #     offenders embed the law's *full slug* (not its old_prefix), so we
        #     match against the slug→abbrev map. Already-compact
        #     Sanction_<ABBREV>_Par_… pass through untouched because <ABBREV>
        #     is not a slug; a no-op rename is dropped by the != guard below.
        elif do_sanction and (
            local.startswith("Sanctions_") or local.startswith("Sanction_")
        ):
            fam = "Sanctions_" if local.startswith("Sanctions_") else "Sanction_"
            rest = local[len(fam):]
            for slug in sorted_slugs:
                if rest.startswith(slug + "_"):
                    new_rest = slug_to_abbrev[slug] + rest[len(slug):]
                    new_iri = "estleg:" + fam + new_rest
                    break

        # 4. {prefix}_* patterns (Map, Par, Chapter, Division, TopicScheme, Osa)
        elif do_legacy:
            for old_prefix in sorted_prefixes:
                if local.startswith(old_prefix + "_"):
                    suffix = local[len(old_prefix):]
                    new_iri = "estleg:" + prefix_remap[old_prefix] + suffix
                    break

        # 5. Fix VOS-style missing underscore: _Par{N} -> _Par_{N}. Only when
        #    the legacy family is in scope — the amendment/sanction-only layer
        #    must not opportunistically rewrite unrelated _Par{N} IRIs.
        if new_iri:
            new_iri = PAR_NO_UNDERSCORE_RE.sub(r"_Par_\1", new_iri)
        elif do_legacy and PAR_NO_UNDERSCORE_RE.search(iri):
            new_iri = PAR_NO_UNDERSCORE_RE.sub(r"_Par_\1", iri)

        if new_iri and new_iri != iri:
            rename_map[iri] = new_iri

    if unmatched_amendments and skipped_amendments is not None:
        for u in sorted(unmatched_amendments):
            skipped_amendments.setdefault(
                u, "amendment IRI did not match any known base_slug shape"
            )

    return rename_map


# Default IRI families the CLI ``dry-run`` / ``apply`` operate on. Issue #192
# is purely the Amendment_/AmendmentChain_/AmendmentLink_ shortening; the
# legacy law/regulation prefix remap (and the analogous ``sanction`` cleanup,
# whose definitions live in ``krr_outputs/sanctions/`` outside #192's file
# scope) are *not* re-run by default — re-running the legacy remap on the
# drifted corpus would corrupt already-compact IRIs. Pass ``--legacy`` to
# include them.
DEFAULT_MIGRATION_FAMILIES = frozenset({RENAME_FAMILY_AMENDMENT})


def dry_run_cmd(families: frozenset[str] | set[str] | None = None) -> None:
    """Preview what the migration would change without modifying files.

    ``families`` selects which IRI families to consider; the CLI passes
    :data:`DEFAULT_MIGRATION_FAMILIES` (amendment only — issue #192). ``None``
    means *all* families — the historical full remap (legacy prefixes +
    amendment + sanction), kept for completeness / power use.
    """
    if families is None:
        families = ALL_RENAME_FAMILIES
    print("=" * 70)
    print("Phase 2: Dry run")
    print("=" * 70)

    if not REGISTRY_PATH.exists():
        print("ERROR: Registry not found. Run 'build-registry' first.")
        sys.exit(1)

    with open(REGISTRY_PATH, encoding="utf-8") as f:
        registry = json.load(f)

    skipped_amendments: dict[str, str] = {}
    rename_map = build_rename_map(
        registry,
        skipped_amendments=skipped_amendments,
        families=families,
    )

    # Check for collisions
    new_iris = list(rename_map.values())
    collision_set: set[str] = set()
    seen: set[str] = set()
    for v in new_iris:
        if v in seen:
            collision_set.add(v)
        seen.add(v)
    collisions = sorted(collision_set)

    # Count per-file impact. The naive ``sum(content.count(old) for old in
    # rename_map)`` is O(files × renames) — fine when there were ~15k renames,
    # crippling now that the amendment family pushes that toward ~100k. Instead
    # find the IRIs actually present in each file via the same regex used to
    # *build* the map, intersect with the rename keys, and count only those —
    # turning the inner loop into O(occurrences-in-file).
    rename_keys = set(rename_map)
    files = get_all_scannable_files()
    files_affected: dict[str, int] = {}
    py_hardcoded: dict[str, list[str]] = {}

    for fp in files:
        try:
            content = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        present_iris = set(ESTLEG_RE.findall(content)) & rename_keys
        if not present_iris:
            continue
        count = sum(content.count(old) for old in present_iris)
        if count > 0:
            rel = str(fp.relative_to(PROJECT_ROOT))
            files_affected[rel] = count
            if fp.suffix == ".py":
                py_hardcoded[rel] = sorted(present_iris)

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
        "skipped_amendment_slugs": dict(sorted(skipped_amendments.items())),
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
    if skipped_amendments:
        print(
            f"Amendment chains left unshortened (recorded reasons): "
            f"{len(skipped_amendments)}"
        )
        for slug, reason in list(sorted(skipped_amendments.items()))[:10]:
            print(f"  {slug}: {reason}")
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


def _amendment_base_slug_in_iri(
    iri: str, base_slugs_by_len: list[str]
) -> str | None:
    """Return the (still-present, mappable) base_slug embedded in ``iri``.

    Mirrors :func:`_shorten_amendment_iri`'s decomposition but only cares
    *whether* a mappable slug is present — used by ``verify_migration`` to
    fail-fast if any ``Amendment_*`` family IRI escaped the migration with a
    long slug still in it. ``base_slugs_by_len`` is the set of slugs that have
    a compact replacement, sorted longest-first.
    """
    if not iri.startswith("estleg:"):
        return None
    local = iri[len("estleg:"):]
    base_slug_set = set(base_slugs_by_len)
    for family in AMENDMENT_PREFIXES:
        if not local.startswith(family):
            continue
        rest = local[len(family):]
        if not rest:  # Python f-string fragment, not a corpus IRI.
            return None
        if family == "AmendmentChain_":
            return rest if rest in base_slug_set else None
        if family == "Amendment_":
            for bs in base_slugs_by_len:
                if rest == bs:
                    return None
                if rest.startswith(bs + "_") and _AMEND_SUFFIX_RE.match(
                    rest[len(bs) + 1:]
                ):
                    return bs
            return None
        if family == "AmendmentLink_":
            for bs in base_slugs_by_len:
                marker = "_" + bs
                if rest.endswith(marker) and len(rest) > len(marker):
                    draft_id = rest[: -len(marker)]
                    if draft_id.startswith("Draft_"):
                        return bs
            return None
    return None


def verify_migration(rename_map: dict[str, str]) -> tuple[bool, list[str]]:
    """Verify that the migration plan and applied corpus are consistent.

    Checks:

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
    4. **Amendment-shortening check (issue #192)** — no ``Amendment_*`` /
       ``AmendmentChain_*`` / ``AmendmentLink_*`` IRI in the corpus may still
       embed a long ``<base_slug>`` for which a compact abbreviation (or
       ``Reg_<id>`` stem) is known. Chains whose slug genuinely couldn't be
       shortened (recorded as skips) are exempt. Also *reports* (does not
       fail on) the longest remaining ``@id``s — raw-slug ``Concept_*`` /
       ``Institution_*`` nodes outside this issue's scope stay long.

    Returns ``(ok, list_of_issues)`` where ``ok`` is True iff every *failing*
    check passed. Issues are emitted with file:line context where possible.
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

    # 4. Amendment-shortening prerequisites — rebuild the base_slug → compact
    #    prefix map so we can flag any amendment-family IRI that still embeds a
    #    mappable slug. (The map is derived from the amendments/ chain files,
    #    whose *filenames* the migration does not change.)
    try:
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            _registry = json.load(f)
    except (json.JSONDecodeError, OSError):
        _registry = {}
    amend_prefix_map, _amend_skipped = build_amendment_prefix_map(_registry)
    amend_base_slugs_by_len = sorted(amend_prefix_map.keys(), key=len, reverse=True)

    # 3. Post-apply corpus check.
    files = get_all_scannable_files()
    old_iris = set(rename_map.keys())
    remaining: dict[str, list[str]] = {}
    bad_corpus: dict[str, list[str]] = {}
    long_amendment_iris: dict[str, list[str]] = {}  # iri -> locations
    longest_ids: list[tuple[int, str, str]] = []  # (len, iri, location)

    for fp in files:
        try:
            content = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(fp.relative_to(PROJECT_ROOT))
        # The format/slug/length checks reason about *corpus* IRIs (the data
        # files). They must not scan Python source — an f-string fragment
        # (``estleg:Amendment_``) or a docstring example (``estleg:_Par_1``)
        # would otherwise be flagged as malformed corpus data. The
        # "old IRI lingering" check still spans .py because that's the safety
        # net for ``--rewrite-python``.
        is_data_file = fp.suffix in DEFAULT_REWRITE_SUFFIXES and fp.suffix != ".md"

        file_iris = set(ESTLEG_RE.findall(content))

        # 3a. Old IRIs lingering after apply.
        for iri in old_iris & file_iris:
            remaining.setdefault(iri, []).append(_locate_iri(content, iri, rel))

        if not is_data_file:
            continue

        # 3b. New IRIs that fail the canonical format check. We use the
        #     unicode-aware scanner so Estonian-letter abbreviations (TsÜS,
        #     ÕÕS, …) round-trip cleanly; only genuine garbage trips this.
        for iri in set(ESTLEG_FULL_RE.findall(content)):
            if not NEW_IRI_FORMAT_RE.match(iri):
                bad_corpus.setdefault(iri, []).append(
                    _locate_iri(content, iri, rel)
                )

        # 4a. Amendment-family IRIs that still carry a mappable base_slug.
        # 4b. Track the longest @ids seen (reporting only).
        for iri in file_iris:
            if iri.startswith(("estleg:Amendment_", "estleg:AmendmentChain_",
                               "estleg:AmendmentLink_")):
                if _amendment_base_slug_in_iri(iri, amend_base_slugs_by_len):
                    long_amendment_iris.setdefault(iri, []).append(
                        _locate_iri(content, iri, rel)
                    )
            if len(iri) > ID_LENGTH_REPORT_THRESHOLD:
                longest_ids.append((len(iri), iri, f"{rel}"))

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

    if long_amendment_iris:
        issues.append(
            f"FAIL: {len(long_amendment_iris)} Amendment_/AmendmentChain_/"
            "AmendmentLink_ IRIs still embed a shortenable base slug"
        )
        for iri, locations in sorted(long_amendment_iris.items())[:20]:
            head = locations[0]
            extra = f" (+{len(locations)-1} more)" if len(locations) > 1 else ""
            issues.append(f"  {iri} in {head}{extra}")

    # Reporting only — the >80-char tail (raw-slug Concept_*/Institution_*,
    # long auto-derived law abbreviations) is out of scope for issue #192.
    if longest_ids:
        longest_ids.sort(reverse=True)
        # de-dup by IRI (the same long @id recurs across the combined file etc.)
        seen_iri: set[str] = set()
        uniq_longest: list[tuple[int, str, str]] = []
        for length, iri, loc in longest_ids:
            if iri in seen_iri:
                continue
            seen_iri.add(iri)
            uniq_longest.append((length, iri, loc))
        amend_long = [
            x for x in uniq_longest
            if x[1].startswith(("estleg:Amendment_", "estleg:AmendmentChain_",
                                "estleg:AmendmentLink_"))
        ]
        issues.append(
            "INFO: longest remaining @id is "
            f"{uniq_longest[0][0]} chars ({uniq_longest[0][1]} in "
            f"{uniq_longest[0][2]}); "
            f"{len(uniq_longest)} distinct @ids exceed "
            f"{ID_LENGTH_REPORT_THRESHOLD} chars"
            + (
                f"; longest amendment-family @id now "
                f"{amend_long[0][0]} chars ({amend_long[0][1]})"
                if amend_long else "; no amendment-family @id exceeds the threshold"
            )
        )

    # An ``INFO:`` line does not constitute a failure.
    failing = [i for i in issues if not i.startswith("INFO:")]
    return len(failing) == 0, issues


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

    # Surface any INFO lines (e.g. the longest-remaining-@id report) regardless
    # of pass/fail — they're advisory, not blocking.
    for issue in issues:
        if issue.startswith("INFO:"):
            print(f"  {issue}")

    if passed:
        print("VERIFICATION PASSED: All old IRIs replaced successfully.")
        save_migration_state(
            {
                "last_applied_at": datetime.now(timezone.utc).isoformat(),
                "last_applied_registry_hash": report_registry_hash,
                "last_applied_corpus_hash": report_corpus_hash,
                "total_renames_applied": total_renames_applied,
                "files_changed": total_files_changed,
                "note": (
                    "Amendment_/AmendmentChain_/AmendmentLink_ IRI shortening "
                    "(issue #192): long <base_slug> segments replaced with the "
                    "law's compact abbreviation (data/law_abbreviations.json) "
                    "or the regulation's Reg_<id> stem."
                ),
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
    parser.add_argument(
        "--legacy",
        action="store_true",
        help=(
            "dry-run only: also include the historical law/regulation prefix "
            "remap (LegalProvision_/Cluster_/<prefix>_*). By default dry-run "
            "covers just the Amendment_/AmendmentChain_/AmendmentLink_ "
            "shortening (issue #192) plus the Sanction(s)_<slug>_ cleanup."
        ),
    )
    args = parser.parse_args()

    dispatch = {
        "build-registry": build_registry_cmd,
        "dry-run": lambda: dry_run_cmd(
            families=None if args.legacy else DEFAULT_MIGRATION_FAMILIES
        ),
        "apply": lambda: apply_cmd(
            rewrite_python=args.rewrite_python,
            force_reapply=args.force_reapply,
        ),
    }
    dispatch[args.command]()


if __name__ == "__main__":
    main()
