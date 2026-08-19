#!/usr/bin/env python3
"""
Fetch ALL Estonian laws from Riigi Teataja and generate JSON-LD ontology files.

This script:
1. Queries the Riigi Teataja API for all seadused (laws)
2. Keeps only the latest version of each law
3. Fetches the XML for each
4. Generates a JSON-LD ontology file for each
5. Skips laws that already have output files
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import requests  # noqa: F401  -- tests monkeypatch ``requests.get``

from estleg.estleg_common import (
    CONTEXT,
    act_root_node,
    et_literal,
    sanitize_id,
    save_json,
    sha256_hex,
    slugify,
)
from estleg.law_structure import (  # noqa: F401 — re-exported for tests / generate_missing_parts
    _chapter_id_suffix,
    _division_id_suffix,
    _iter_loiked,
    _loige_body_text,
    _loige_numbers,
    _paragraph_id_suffix,
    _sup_to_unicode,
    _truncate_at_boundary,
    _walk_paragraphs_direct,
    build_subsections,
    chapter_cluster_subject,
    collect_full_text,
    collect_text,
    content_derived_dup_suffix,
    emit_hierarchy_and_provisions,
    provision_summary,
    remint_dupn_id,
    remint_dupn_ids,
)
from estleg.riigiteataja_common import (
    BASE_URL,
    SourceListFetchError,
    ct,
    fetch_acts,
    ln,
    new_page_stats,
)
from estleg.riigiteataja_common import (
    fetch_xml as common_fetch_xml,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
KRR_DIR = REPO_ROOT / "krr_outputs"
DATA_DIR = REPO_ROOT / "data" / "riigiteataja"
DATA_DIR.mkdir(parents=True, exist_ok=True)

NS = "https://w3id.org/estleg/"
DEFAULT_KEHTIV = "2026-05-01"
# #558: sha256 of the last fetched/cached RT XML, keyed by cache_name/slug.
_CONTENT_HASHES: dict[str, str] = {}

# Issue #601: ONE shared minimum-size threshold for the on-disk XML cache,
# applied to BOTH the fresh-download floor and the cache-accept floor.
# Previously the download floor (reject < 200 bytes) was LOWER than the
# cache-accept floor (require > 1000 bytes), so a 200–1000-byte Riigi
# Teataja response got *written* yet was never *read back* — re-fetched on
# every run forever. Unifying the two at one constant means any file we
# persist we will also re-accept (no infinite refetch); content validity is
# enforced separately by ``_is_trustworthy_xml_root`` (root-tag denylist).
MIN_XML_BYTES = 200

# Issue #594/#601: roots that mark a non-act payload (an HTML/error page that
# still happens to parse as XML). Trusting/caching one of these would pin a
# >threshold error page in the cache indefinitely. ``ln()`` strips any
# namespace before the (lower-cased) comparison. Kept as a denylist rather
# than an ``oigusakt`` allowlist so legitimate/synthetic act roots are not
# rejected — real RT acts use ``oigusakt``, but the guard only needs to keep
# obvious error documents out.
_NON_ACT_ROOT_TAGS: frozenset[str] = frozenset({"html", "error", "errors"})
GENERATION_MODES = ("missing-only", "refresh", "force")
MANIFEST_NAME = "generation_manifest_laws.json"
DEFAULT_REGEN_STATE_NAME = ".regen_state.json"
REGEN_STATE_SCHEMA_VERSION = 1
# Argparse sentinel for bare ``--regen-state``; resolved after KRR_DIR is configured.
DEFAULT_REGEN_STATE_SENTINEL = "__default_regen_state__"


def build_law_slug_map(
    all_laws: dict[str, dict],
    *,
    registry: dict | None = None,
    existing_slug_by_title: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return deterministic, unique output slugs for a law title mapping.

    ``slugify(title)`` truncates long treaty titles, so distinct source
    laws can collide on the same 80-character filename stem. When the
    abbreviation registry already assigns that base slug to one title,
    that title keeps the base stem and the remaining colliders receive
    stable numeric suffixes.

    Suffix assignment is "freeze + forward-stable" (#238):

    * ``existing_slug_by_title`` maps a ``title`` to the slug already
      committed for it (loaded from the manifest's ``outputsAll`` block).
      Any title present there keeps its committed slug verbatim — its slug
      is reserved first so it never drifts when sibling titles change or
      reorder across snapshots.
    * Titles not in the freeze map ("new" colliders) are ordered
      deterministically by ``(globaalId, title)`` — the globaalId is the
      stable per-act identity, with the title as a final fallback. The gid
      is compared as a string (opaque RT identifiers like ``"27546"`` and
      ``"201062022004"`` reorder differently under int compare). Each new
      title is assigned the lowest still-unused slug from the sequence
      ``base_slug, base_slug_2, base_slug_3, …`` (skipping any slug a
      frozen title already reserved). A registered title prefers the bare
      ``base_slug`` when it is still free.

    When ``existing_slug_by_title`` is empty/None (a fresh checkout with no
    manifest) every title is "new", giving a pure gid-ordered, snapshot
    order-independent assignment.
    """
    registry = _load_registry() if registry is None else registry
    freeze = existing_slug_by_title or {}
    by_base: dict[str, list[str]] = {}
    for title in all_laws:
        if isinstance(title, str):
            by_base.setdefault(slugify(title), []).append(title)

    slugs: dict[str, str] = {}
    for base_slug, titles in sorted(by_base.items()):
        entry = registry.get(base_slug) if isinstance(registry, dict) else None
        registered_title = (
            entry.get("title")
            if isinstance(entry, dict) and isinstance(entry.get("title"), str)
            else None
        )

        reserved: set[str] = set()
        frozen_titles: list[str] = []
        new_titles: list[str] = []
        for title in titles:
            committed = freeze.get(title)
            if isinstance(committed, str) and committed:
                slugs[title] = committed
                reserved.add(committed)
                frozen_titles.append(title)
            else:
                new_titles.append(title)

        # New colliders are ordered by stable identity (gid, then title).
        # The registry owner is preferred for the bare ``base_slug`` only
        # among the new titles and only when that slug is still free.
        new_titles.sort(
            key=lambda t: (str((all_laws.get(t) or {}).get("gid") or ""), t)
        )
        if registered_title in new_titles and base_slug not in reserved:
            new_titles = [
                registered_title,
                *[t for t in new_titles if t != registered_title],
            ]

        next_index = 0
        for title in new_titles:
            while True:
                candidate = base_slug if next_index == 0 else f"{base_slug}_{next_index + 1}"
                next_index += 1
                if candidate not in reserved:
                    break
            slugs[title] = candidate
            reserved.add(candidate)
    return slugs



PAGE_SCAN_LIMIT = 250


def get_all_laws(
    *,
    kehtiv: str | None = DEFAULT_KEHTIV,
    allow_partial: bool = False,
    page_limit: int = PAGE_SCAN_LIMIT,
) -> tuple[dict[str, dict], dict]:
    """Fetch all law entries from Riigi Teataja API, keeping latest version of each.

    Issue #165 fix 10: hitting the pagination cap (``page_limit``) used
    to silently mark the run ``complete=True``. Now the run is flagged
    incomplete and, unless ``allow_partial`` is set, raises
    ``SourceListFetchError``. The manifest reports ``pagesScanned`` so
    operators can see when the cap matters.
    """
    all_laws: dict[str, dict] = {}
    rows_seen = 0
    page_stats = new_page_stats()
    for law in fetch_acts(
        "seadus",
        kehtiv=kehtiv,
        limiit=None,
        max_pages=page_limit,
        allow_partial=allow_partial,
        stop_on_short_page=False,
        max_retries=0,
        stats=page_stats,
    ):
        rows_seen += 1
        title = law.get("pealkiri", "").strip()
        if not title:
            continue
        gid = str(law.get("globaalID", ""))
        if title not in all_laws or gid > all_laws[title]["gid"]:
            all_laws[title] = {
                "gid": gid,
                "tid": str(law.get("terviktekstID", "")),
                "url": law.get("url", ""),
                "kehtivus": law.get("kehtivus", {}),
                "lyhend": law.get("lyhend", ""),
            }

    cap_hit = bool(page_stats.get("pageLimitHit"))
    pages_scanned = page_stats["pagesFetchedOk"] + page_stats["pagesFailed"]
    completed = (not cap_hit) and page_stats["pagesFailed"] == 0
    if cap_hit and not allow_partial:
        raise SourceListFetchError(
            f"Riigi Teataja pagination cap hit at page {page_limit} "
            f"({rows_seen} rows seen, {len(all_laws)} unique acts). "
            "Re-run with --allow-partial to accept truncation, or "
            "raise the cap."
        )

    return all_laws, {
        "requestedDocument": "seadus",
        "kehtiv": kehtiv,
        "searchRowsSeen": rows_seen,
        "pagesScanned": pages_scanned,
        "pageLimit": page_limit,
        "pageLimitHit": cap_hit,
        "uniqueActs": len(all_laws),
        "complete": completed,
        "partialAllowed": allow_partial,
        "pages": {
            "pagesFetchedOk": page_stats["pagesFetchedOk"],
            "pagesFailed": page_stats["pagesFailed"],
            "pagesRetried": page_stats["pagesRetried"],
        },
    }


def get_existing_files() -> set[str]:
    """Get set of existing output file stems."""
    existing = set()
    for f in KRR_DIR.glob("*_peep.json"):
        existing.add(f.stem.replace("_peep", ""))
    return existing


def has_existing_output(existing: set[str], title: str, slug: str) -> bool:
    if slug in existing:
        return True
    if title in MULTIPART_LAWS:
        return any(ex.startswith(f"{slug}_osa") for ex in existing)
    return False


def _is_trustworthy_xml_root(root: ET.Element | None) -> bool:
    """Return False for a root that marks a non-act payload (#594/#601).

    An HTML/error document can be >``MIN_XML_BYTES`` and still parse as XML;
    caching it would pin an error page in the cache indefinitely. We reject
    such roots by a denylist (``_NON_ACT_ROOT_TAGS``) rather than requiring a
    fixed ``oigusakt`` allowlist, so legitimate act roots — including the
    synthetic ones used in tests — are still trusted.
    """
    return root is not None and ln(root.tag).lower() not in _NON_ACT_ROOT_TAGS


def _read_cached_rt_root(cache_name: str, tid: str | None) -> ET.Element | None:
    """Return a parsed, validated RT act root from local cache, or None.

    Applies the shared ``MIN_XML_BYTES`` floor and ``_is_trustworthy_xml_root``
    (#601) so a too-small RT error stub or a cached HTML error page is treated
    as a cache miss rather than trusted indefinitely. Mirrors ``fetch_xml``'s
    cache lookup: the tid-qualified file first, then the legacy slug-only file
    as a fallback (#165 fix 9) so the first tid-keyed run does not force a full
    corpus refetch.
    """
    candidates: list[Path] = []
    if tid:
        candidates.append(DATA_DIR / f"{cache_name}__tid{sanitize_id(tid)}.xml")
    candidates.append(DATA_DIR / f"{cache_name}.xml")
    for cache_path in candidates:
        if not (cache_path.exists() and cache_path.stat().st_size > MIN_XML_BYTES):
            continue
        try:
            root = ET.parse(str(cache_path)).getroot()
        except ET.ParseError:
            continue
        if _is_trustworthy_xml_root(root):
            _CONTENT_HASHES[cache_name] = sha256_hex(cache_path.read_bytes())
            return root
    return None


def fetch_xml(
    url: str,
    cache_name: str,
    *,
    tid: str | None = None,
) -> ET.Element | None:
    """Fetch law XML via ``riigiteataja_common.fetch_xml`` (#466 / #296 / #389).

    Issue #165 fix 9: when ``tid`` is supplied, embed it in the cache
    filename and fall back to the legacy slug-only file.
    Issue #601: ``validate_root`` rejects HTML/error roots.
    """
    primary = (
        f"{cache_name}__tid{sanitize_id(tid)}" if tid else cache_name
    )
    fallback = cache_name if tid else None

    def _remember(payload: bytes) -> None:
        _CONTENT_HASHES[cache_name] = sha256_hex(payload)

    return common_fetch_xml(
        url,
        primary,
        cache_dir=DATA_DIR,
        fallback_cache_name=fallback,
        min_size=MIN_XML_BYTES,
        validate_root=_is_trustworthy_xml_root,
        on_bytes=_remember,
    )


def _load_registry() -> dict:
    """Load the abbreviation registry from disk (or return empty dict)."""
    registry_path = REPO_ROOT / "data" / "law_abbreviations.json"
    if registry_path.exists():
        with open(registry_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


class PrefixCollisionError(RuntimeError):
    """Raised when a slug claims an abbreviation already taken by another law,
    and the registry does not record either side as a multi-claimant winner.

    This is intentionally fatal: silent re-assignment is what causes the
    order-dependent IRI drift described in #156/#165.
    """


class PrefixAllocator:
    """Encapsulates per-run prefix allocation state.

    Replaces the module-level ``_used_prefixes`` dict so that:
      * partial regenerations cannot reassign abbreviations across runs;
      * tests can construct an allocator with a freshly-loaded registry
        without leaking state between cases;
      * multi-claimant abbreviations MUST appear in
        ``data/law_abbreviations.json`` — if a previously-allocated prefix
        clashes with another law, allocation raises rather than silently
        switching to a slug-based fallback.
    """

    def __init__(self, registry: dict | None = None) -> None:
        # registry: {slug: {"abbrev": str, ...}}
        self._registry: dict = registry if registry is not None else _load_registry()
        self._registry_abbrev_owners: dict[str, str] = {
            entry["abbrev"]: entry["title"]
            for entry in self._registry.values()
            if isinstance(entry, dict)
            and isinstance(entry.get("abbrev"), str)
            and isinstance(entry.get("title"), str)
        }
        # used_prefixes: {prefix: title} — the law that currently owns this prefix
        self._used_prefixes: dict[str, str] = {}

    @property
    def used_prefixes(self) -> dict[str, str]:
        """Read-only view of {prefix -> claimant title}; for tests/diagnostics."""
        return dict(self._used_prefixes)

    def reset(self) -> None:
        self._used_prefixes.clear()

    def allocate(self, abbreviation: str, slug: str, title: str) -> str:
        """Return a stable prefix for the (slug, title) pair.

        Order of resolution:
          1. If the slug (with any ``_osaN`` suffix stripped) is in the
             static registry, use ``registry[base_slug]["abbrev"]``. The
             registry is the single source of truth for multi-claimant
             abbreviations; if the registered prefix is already used by a
             different title, we raise — the registry must be corrected.
          2. Otherwise, derive a candidate from ``abbreviation`` (>3 chars)
             or fall back to the first 40 chars of the slug.
          3. On collision with a *different* title, attempt progressively
             longer slug prefixes; if they all collide, raise — the
             abbreviation MUST be added to the registry instead of silently
             falling back to a numeric suffix.
        """
        registry = self._registry
        base_slug = re.sub(r"_osa\d+$", "", slug)

        # 1. registry path — single source of truth for multi-claimant
        if base_slug in registry:
            candidate = registry[base_slug]["abbrev"]
            owner = self._used_prefixes.get(candidate)
            if owner is not None and owner != title:
                raise PrefixCollisionError(
                    f"Registry-mandated prefix {candidate!r} for slug "
                    f"{base_slug!r} (title {title!r}) is already claimed "
                    f"by {owner!r}. Update data/law_abbreviations.json."
                )
            self._used_prefixes[candidate] = title
            return candidate

        # 2. derive candidate from abbreviation or slug head
        candidate = sanitize_id(abbreviation) if abbreviation else None
        if not candidate or len(candidate) <= 3:
            candidate = sanitize_id(slug[:40])

        owner = self._used_prefixes.get(candidate)
        registry_owner = self._registry_abbrev_owners.get(candidate)
        if registry_owner is not None and registry_owner != title:
            owner = registry_owner
        if owner is None or owner == title:
            self._used_prefixes[candidate] = title
            return candidate

        # 3. collision with a different title — try longer slug prefixes
        for length in (40, 50, 60, 70, 80, len(slug)):
            attempt = sanitize_id(slug[:length])
            attempt_owner = self._used_prefixes.get(attempt)
            registry_attempt_owner = self._registry_abbrev_owners.get(attempt)
            if registry_attempt_owner is not None and registry_attempt_owner != title:
                attempt_owner = registry_attempt_owner
            if attempt_owner is None or attempt_owner == title:
                self._used_prefixes[attempt] = title
                return attempt

        raise PrefixCollisionError(
            f"Could not allocate a unique prefix for slug {slug!r} "
            f"(title {title!r}, abbreviation {abbreviation!r}); "
            f"candidate {candidate!r} is owned by "
            f"{self._used_prefixes.get(candidate)!r}. "
            "Add this law to data/law_abbreviations.json with a stable "
            "abbreviation."
        )


# Module-level default allocator used when ``generate_law_jsonld`` /
# ``generate_multipart_law`` / ``generate_law_stub_jsonld`` are called
# without an explicit allocator argument. main() resets this once per
# run; callers that need an isolated state (tests, library use) should
# instantiate their own ``PrefixAllocator``.
_default_allocator: PrefixAllocator = PrefixAllocator()
# Same dict as the default allocator so ``_used_prefixes.clear()`` in tests
# still resets allocation state (#466: the proxy class is gone).
_used_prefixes: dict[str, str] = _default_allocator._used_prefixes


def _unique_prefix(
    abbreviation: str,
    slug: str,
    title: str,
    *,
    allocator: PrefixAllocator | None = None,
) -> str:
    """Module-level wrapper around ``PrefixAllocator.allocate``."""
    alloc = allocator if allocator is not None else _default_allocator
    return alloc.allocate(abbreviation, slug, title)


def _kehtiv_node(kehtiv: str | None) -> dict | None:
    """Return the ``estleg:kehtiv`` literal value for an ontology node.

    Mirrors ``generate_regulations.build_regulation_jsonld``: the snapshot
    date is stamped as an ``xsd:date`` literal so a downstream staleness
    audit (or ``--missing-only`` re-run) can compare a stored snapshot
    date against the current run cleanly.
    """
    if not kehtiv:
        return None
    return {"@value": kehtiv, "@type": "xsd:date"}


def _stamp_terviktekst_id(node: dict, terviktekst_id: str | None) -> None:
    """Stamp ``estleg:terviktekstId`` so tid-based staleness can fire (#341.1).

    ``existing_law_is_stale`` compares this field to the current RT edition.
    Without the stamp the branch is dead and missing-only never refreshes a
    new terviktekst on an unchanged snapshot date.
    """
    if terviktekst_id:
        node["estleg:terviktekstId"] = str(terviktekst_id)


def _stamp_content_hash(node: dict, slug: str) -> None:
    """Attach ``estleg:contentHash`` from the last fetch/cache of *slug* (#558)."""
    digest = _CONTENT_HASHES.get(slug)
    if digest:
        node["estleg:contentHash"] = digest


def _jsonld_id_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        iri = value.get("@id")
        return [iri] if isinstance(iri, str) else []
    if isinstance(value, list):
        iris: list[str] = []
        for item in value:
            iris.extend(_jsonld_id_values(item))
        return iris
    return []


def _is_cluster_iri(iri: str) -> bool:
    return iri.startswith("estleg:Cluster_")


def _compact_id_refs(iris: list[str]) -> dict | list[dict]:
    refs = [{"@id": iri} for iri in iris]
    if len(refs) == 1:
        return refs[0]
    return refs


def _node_type_tokens(node: dict) -> list[str]:
    raw = node.get("@type", [])
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, str)]
    return []


def retarget_chapter_cluster_identity(node: dict) -> bool:
    """Move Chapter→Cluster ``owl:sameAs`` onto ``dcterms:subject`` (#436)."""
    if not isinstance(node, dict):
        return False
    if "estleg:Chapter" not in _node_type_tokens(node):
        return False
    if "owl:sameAs" not in node:
        return False

    same_as_iris = _jsonld_id_values(node.get("owl:sameAs"))
    cluster_ids = [iri for iri in same_as_iris if _is_cluster_iri(iri)]
    remaining = [iri for iri in same_as_iris if not _is_cluster_iri(iri)]
    if not cluster_ids:
        return False

    subject_ids = _jsonld_id_values(node.get("dcterms:subject"))
    seen = set(subject_ids)
    for cid in cluster_ids:
        if cid not in seen:
            subject_ids.append(cid)
            seen.add(cid)

    subject_val = (
        chapter_cluster_subject(subject_ids[0])
        if len(subject_ids) == 1
        else _compact_id_refs(subject_ids)
    )
    remaining_val = _compact_id_refs(remaining) if remaining else None

    rebuilt: dict = {}
    placed = False
    for key, value in node.items():
        if key == "owl:sameAs":
            rebuilt["dcterms:subject"] = subject_val
            if remaining_val is not None:
                rebuilt["owl:sameAs"] = remaining_val
            placed = True
            continue
        if key == "dcterms:subject":
            continue
        rebuilt[key] = value

    if not placed:
        after: dict = {}
        inserted = False
        for key, value in rebuilt.items():
            after[key] = value
            if key == "estleg:partOfAct":
                after["dcterms:subject"] = subject_val
                if remaining_val is not None:
                    after["owl:sameAs"] = remaining_val
                inserted = True
        if not inserted:
            after["dcterms:subject"] = subject_val
            if remaining_val is not None:
                after["owl:sameAs"] = remaining_val
        rebuilt = after

    node.clear()
    node.update(rebuilt)
    return True


def generate_law_jsonld(
    title: str,
    slug: str,
    root: ET.Element,
    abbreviation: str = "",
    rt_url: str = "",
    *,
    kehtiv: str | None = None,
    terviktekst_id: str | None = None,
    allocator: PrefixAllocator | None = None,
) -> dict:
    """Generate JSON-LD for a single law.

    ``kehtiv`` (when supplied) is stamped onto the ``owl:Ontology`` node
    as ``estleg:kehtiv`` (xsd:date), mirroring
    ``generate_regulations.build_regulation_jsonld`` so that
    ``missing-only`` re-runs can refresh stale snapshots.
    ``terviktekst_id`` is stamped as ``estleg:terviktekstId`` whenever
    ``kehtiv`` is set (#341.1).
    """
    prefix = _unique_prefix(abbreviation, slug, title, allocator=allocator)

    # Collect all paragrahv elements
    paragrahvid = [el for el in root.iter() if ln(el.tag) == "paragrahv"]

    # Get paragraph range
    par_numbers = []
    for p in paragrahvid:
        nr = ct(p, "paragrahvNr")
        if nr:
            try:
                par_numbers.append(int(re.sub(r"[^\d]", "", nr)))
            except ValueError:
                pass

    par_min = min(par_numbers) if par_numbers else "?"
    par_max = max(par_numbers) if par_numbers else "?"

    ontology_id = f"estleg:{prefix}_Map_2026"

    # Construct Riigi Teataja source URL
    rt_source_url = ""
    if rt_url:
        rt_source_url = BASE_URL + rt_url if rt_url.startswith("/") else rt_url

    ontology_node: dict = {
        "@id": ontology_id,
        "@type": ["estleg:Act", "estleg:Law", "schema:Legislation"],
        "rdfs:label": et_literal(f"{title} teemakaardistus"),
        "dc:source": title,
        "dcterms:title": title,
        "estleg:contentStatus": "structuredBody",
    }
    if rt_source_url:
        ontology_node["dcterms:source"] = {"@id": rt_source_url}
    kehtiv_value = _kehtiv_node(kehtiv)
    if kehtiv_value is not None:
        ontology_node["estleg:kehtiv"] = kehtiv_value
        _stamp_terviktekst_id(ontology_node, terviktekst_id)
    _stamp_content_hash(ontology_node, slug)

    graph: list[dict] = [ontology_node]
    _treaty_patterns = ("konventsiooni", "lepingu", "protokolli")
    fallback_label = (
        "Lepingu sätted"
        if any(pat in slug.lower() for pat in _treaty_patterns)
        else title
    )
    emit_hierarchy_and_provisions(
        graph=graph,
        chapter_root=root,
        paragrahvid=paragrahvid,
        prefix=prefix,
        ontology_id=ontology_id,
        class_id="estleg:LegalProvision",
        title=title,
        slug=slug,
        par_min=par_min,
        par_max=par_max,
        par_numbers=par_numbers,
        scheme_id=f"estleg:{prefix}_TopicScheme",
        descend_osa=True,
        fallback_label=fallback_label,
        provision_iri_prefix=f"estleg:{prefix}_Par_",
        structural_ns=prefix,
        subsection_builder=build_subsections,
    )
    return {"@context": CONTEXT, "@graph": graph}




def generate_law_stub_jsonld(
    title: str,
    slug: str,
    root: ET.Element,
    abbreviation: str = "",
    rt_url: str = "",
    *,
    content_status: str = "noStructuredBody",
    kehtiv: str | None = None,
    terviktekst_id: str | None = None,
    allocator: PrefixAllocator | None = None,
) -> dict:
    """Generate an act-level representation for laws without paragraph nodes.

    ``kehtiv`` (when supplied) is stamped as ``estleg:kehtiv`` (xsd:date)
    so missing-only re-runs can refresh stale stubs the same way they
    refresh structured law files.
    ``terviktekst_id`` is stamped as ``estleg:terviktekstId`` whenever
    ``kehtiv`` is set (#341.1).
    """
    prefix = _unique_prefix(abbreviation, slug, title, allocator=allocator)
    # Issue #165 fix 5: rt_url may be None (or empty) for laws missing a
    # Riigi Teataja link, so guard against ``None.startswith``.
    rt_source_url = ""
    if rt_url:
        rt_source_url = BASE_URL + rt_url if rt_url.startswith("/") else rt_url
    ontology_node: dict = {
        "@id": f"estleg:{prefix}_Map_2026",
        "@type": ["estleg:Act", "estleg:Law", "schema:Legislation"],
        "rdfs:label": et_literal(f"{title} teemakaardistus"),
        "dc:source": title,
        "dcterms:title": title,
        "estleg:contentStatus": content_status,
        "estleg:contentStatusReason": (
            "No structured paragraph nodes were found in the source XML; "
            "the act is modeled at act level to preserve coverage."
        ),
    }
    if rt_source_url:
        ontology_node["dcterms:source"] = {"@id": rt_source_url}
    kehtiv_value = _kehtiv_node(kehtiv)
    if kehtiv_value is not None:
        ontology_node["estleg:kehtiv"] = kehtiv_value
        _stamp_terviktekst_id(ontology_node, terviktekst_id)
    _stamp_content_hash(ontology_node, slug)
    return {"@context": CONTEXT, "@graph": [ontology_node]}


def generate_multipart_law(
    title: str,
    slug: str,
    root: ET.Element,
    abbreviation: str = "",
    rt_url: str = "",
    *,
    kehtiv: str | None = None,
    terviktekst_id: str | None = None,
    allocator: PrefixAllocator | None = None,
) -> list[tuple[str, dict]]:
    """Generate separate JSON-LD files for each osa (part) of a multi-part law.

    ``kehtiv`` (when supplied) is stamped as ``estleg:kehtiv`` (xsd:date)
    on every per-osa ``owl:Ontology`` node.
    """
    prefix = _unique_prefix(abbreviation, slug, title, allocator=allocator)
    results = []
    kehtiv_value = _kehtiv_node(kehtiv)

    # Construct Riigi Teataja source URL
    rt_source_url = ""
    if rt_url:
        rt_source_url = BASE_URL + rt_url if rt_url.startswith("/") else rt_url

    for osa_el in root.iter():
        if ln(osa_el.tag) != "osa":
            continue

        osa_nr = ct(osa_el, "osaNr")
        osa_title = ct(osa_el, "osaPealkiri") or ""
        if not osa_nr:
            continue

        paragrahvid = [el for el in osa_el.iter() if ln(el.tag) == "paragrahv"]
        if not paragrahvid:
            continue

        par_numbers = []
        for p in paragrahvid:
            nr = ct(p, "paragrahvNr")
            if nr:
                try:
                    par_numbers.append(int(re.sub(r"[^\d]", "", nr)))
                except ValueError:
                    pass

        par_min = min(par_numbers) if par_numbers else "?"
        par_max = max(par_numbers) if par_numbers else "?"

        # Issue #309: the per-osa act @id must be snapshot-stable. Encoding the
        # §-range (``_{par_min}_{par_max}``) made the @id shift whenever RT
        # inserts/removes a paragraph, silently dropping every enrichment keyed
        # on the old IRI (merge_existing_enrichments). The §-range lives only in
        # ``rdfs:label`` now; the @id is keyed on the stable osa number alone.
        ontology_id = f"estleg:{prefix}_Osa{sanitize_id(osa_nr)}"

        # Issue #89: Mark file-level ontology node with estleg:Part type
        osa_ontology_node: dict = {
            "@id": ontology_id,
            "@type": ["estleg:Act", "estleg:Law", "estleg:Part", "schema:Legislation"],
            "rdfs:label": et_literal(
                f"{title} Osa {osa_nr} ({osa_title}) §{par_min}–{par_max} kaardistus"
            ),
            "dc:source": title,
            "dcterms:title": title,
            "estleg:contentStatus": "structuredBody",
        }
        if rt_source_url:
            osa_ontology_node["dcterms:source"] = {"@id": rt_source_url}
        if kehtiv_value is not None:
            osa_ontology_node["estleg:kehtiv"] = kehtiv_value
            _stamp_terviktekst_id(osa_ontology_node, terviktekst_id)
        _stamp_content_hash(osa_ontology_node, slug)

        graph: list[dict] = [osa_ontology_node]
        fallback_label = f"{title} Osa {osa_nr}"
        if osa_title:
            fallback_label = f"{title} Osa {osa_nr} ({osa_title})"
        part_label = f"Osa {osa_nr}"
        if osa_title:
            part_label = f"Osa {osa_nr} ({osa_title})"
        emit_hierarchy_and_provisions(
            graph=graph,
            chapter_root=osa_el,
            paragrahvid=paragrahvid,
            prefix=prefix,
            ontology_id=ontology_id,
            class_id="estleg:LegalProvision",
            title=title,
            slug=slug,
            par_min=par_min,
            par_max=par_max,
            par_numbers=par_numbers,
            scheme_id=f"estleg:{prefix}_Osa{osa_nr}_TopicScheme",
            descend_osa=False,
            osa_nr=str(osa_nr),
            part_concept_id=f"estleg:Cluster_{prefix}_{osa_nr}_Part",
            cluster_has_broader=True,
            fallback_label=fallback_label,
            provision_iri_prefix=f"estleg:{prefix}_Osa{osa_nr}_Par_",
            structural_ns=f"{prefix}_{osa_nr}",
            part_label=part_label,
            subsection_builder=build_subsections,
        )
        filename = f"{slug}_osa{osa_nr}_peep.json"
        results.append((filename, {"@context": CONTEXT, "@graph": graph}))

    return results




def _ontology_node(doc: dict) -> dict | None:
    """Return the act-root node in a doc's ``@graph`` (#435)."""
    return act_root_node(doc)


def _stored_literal(value: object) -> str | None:
    """Unwrap a JSON-LD literal (``{"@value": ...}``) to a plain string."""
    if value is None:
        return None
    if isinstance(value, dict):
        inner = value.get("@value")
        return None if inner is None else str(inner)
    return str(value)


def existing_law_is_stale(
    existing_doc: dict,
    current_kehtiv: str | None,
    current_tid: str | None = None,
) -> bool:
    """Return True when an already-written law file is stale w.r.t. this run.

    Analogous to ``generate_regulations.existing_is_stale``. A file is
    stale when, on its first ``owl:Ontology`` node:
      * the stored ``estleg:kehtiv`` snapshot date is missing or differs
        from the current run's ``current_kehtiv``, OR
      * a ``current_tid`` was supplied and the stored
        ``estleg:terviktekstId`` differs from it.

    Returns False when the document is unreadable / has no ontology node
    (the caller already treats those as "needs write"), or when neither
    comparison could be made because no expected values were supplied.
    """
    if not isinstance(existing_doc, dict):
        return False
    ontology = _ontology_node(existing_doc)
    if ontology is None:
        return False

    if current_tid:
        stored_tid = _stored_literal(ontology.get("estleg:terviktekstId"))
        if stored_tid and stored_tid != str(current_tid):
            return True

    if current_kehtiv:
        stored_kehtiv = _stored_literal(ontology.get("estleg:kehtiv"))
        if stored_kehtiv is None or stored_kehtiv != str(current_kehtiv):
            return True

    return False


def _load_existing_doc(path: Path) -> dict | None:
    """Read a JSON document off disk, returning None on any error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# Issue #594: structural-body tags. A genuine RT act with no ``paragrahv``
# but ALSO no ``loige``/``peatykk``/``jagu`` is legitimately body-less (a
# stub is correct). One whose root carries any of these block tags but
# yields ``par_count == 0`` is a degraded/foreign parse (e.g. a future RT
# schema rename of the paragraph tag, or a partial fetch) — stubbing it
# would launder a transient defect into a sticky body-less artifact.
_STRUCTURAL_BODY_TAGS: frozenset[str] = frozenset(
    {"paragrahv", "loige", "peatykk", "jagu", "jaotis"}
)


def xml_root_has_structure(root: ET.Element | None) -> bool:
    """Return True when ``root`` contains any structural-body element.

    Used by the stub guard (#594): a recognized RT act that has at least
    one ``loige``/``peatykk``/``jagu``/``jaotis`` (or ``paragrahv``) but
    parsed to zero paragraphs is structurally degraded, not body-less.
    """
    if root is None:
        return False
    return any(ln(el.tag) in _STRUCTURAL_BODY_TAGS for el in root.iter())


def existing_peep_is_structured(path: Path) -> bool:
    """Return True when an on-disk peep already carries provision content.

    A structured law peep contains at least one provision *instance*: a node
    typed ``estleg:LegalProvision`` or a per-law subclass thereof
    (``estleg:LegalProvision_<slug>``), an ``estleg:Subsection``, or any node
    bearing provision text (``estleg:legalText`` / ``estleg:paragrahv``). A
    body-less stub has only the act ``owl:Ontology`` node (with
    ``estleg:contentStatus == noStructuredBody``) and so returns False. Used
    by the stub guard (#594) to refuse overwriting a previously-structured
    act with a body-less stub when the fresh parse degrades to zero
    paragraphs.
    """
    doc = _load_existing_doc(path)
    if not isinstance(doc, dict):
        return False
    for node in doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        if "estleg:legalText" in node or "estleg:paragrahv" in node:
            return True
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        for t in types:
            if t == "estleg:Subsection" or (
                isinstance(t, str) and t.startswith("estleg:LegalProvision")
            ):
                return True
    return False


def existing_doc_matches(path: Path, doc: dict) -> bool:
    """Return True iff ``path`` already contains exactly ``doc``."""
    existing = _load_existing_doc(path)
    if existing is None:
        return False
    return existing == doc


_MERGE_BLOCKED_FIELDS = frozenset({
    # Generator-owned structural link. Keeping a stale value here can point
    # provisions at clusters that no longer exist after RT structure changes.
    "estleg:requestedCluster",
})


def _should_preserve_textless_summary(new_node: dict, existing_node: dict) -> bool:
    """Return True when an existing curated summary should survive regen."""
    if "estleg:summary" not in existing_node or "estleg:summary" not in new_node:
        return False
    types = new_node.get("@type", [])
    if isinstance(types, str):
        types = [types]
    if "estleg:LegalProvision" not in types:
        return False
    return "estleg:legalText" not in new_node


def merge_existing_enrichments(new_doc: dict, existing_path: Path) -> dict:
    """Additive-merge: copy enrichment fields from an existing law file onto
    matching nodes in ``new_doc``. Generator-emitted fields always win — only
    keys MISSING from the new node are pulled across from the existing one.

    The generator owns structural fields (``@type``, ``rdfs:label``,
    ``estleg:paragrahv``, ``estleg:summary``, ``estleg:legalText``,
    ``estleg:sourceAct``, ``estleg:contentStatus``, ``estleg:kehtiv``,
    ``estleg:hasSubsection``, ``estleg:requestedCluster`` …). Everything else on an act or provision node
    — EuroVoc ``dcterms:subject`` (#123/#126), ``estleg:transposesDirective``
    (#129/#96), ``estleg:harmonisedWith`` (#197), ``estleg:affectedBy``
    (drafts), ``estleg:normativeType``, ``estleg:hasVersion`` (#198),
    ``estleg:hasOpinion`` (#199), sanctions/court-link side-channel fields —
    is layered on by separate enrichment scripts and would otherwise be
    wiped on every regen. Acceptance test: re-running ``generate_all_laws``
    twice in a row over an enriched corpus is a no-op (#205, drift #3).
    """
    if not existing_path.exists():
        return new_doc
    existing = _load_existing_doc(existing_path)
    if not isinstance(existing, dict):
        return new_doc
    existing_by_id = {
        n.get("@id"): n
        for n in existing.get("@graph", [])
        if isinstance(n, dict) and n.get("@id")
    }
    for new_node in new_doc.get("@graph", []):
        if not isinstance(new_node, dict):
            continue
        node_id = new_node.get("@id")
        if not node_id or node_id not in existing_by_id:
            continue
        existing_node = existing_by_id[node_id]
        for key, value in existing_node.items():
            if key.startswith("@"):
                continue
            if key == "estleg:summary" and _should_preserve_textless_summary(
                new_node, existing_node
            ):
                new_node[key] = value
                continue
            if key in _MERGE_BLOCKED_FIELDS:
                continue
            if key not in new_node:
                new_node[key] = value
    return new_doc


def write_law_output(
    out_path: Path,
    doc: dict,
    *,
    mode: str,
    expected_kehtiv: str | None = None,
    expected_tid: str | None = None,
    preserve_enrichments: bool = True,
) -> str:
    """Write one law artifact and return a run-stat status key.

    Mirrors ``generate_regulations.write_regulation_output``:

      * ``missing-only`` — skip when the on-disk file is present and not
        stale (``existingSkipped``); otherwise refresh it in place
        (``refreshedStale`` when a stale file was overwritten,
        ``newlyGenerated`` when there was nothing there before).
      * ``refresh`` — rewrite when the generated JSON-LD changed
        (``refreshed``), leave it alone otherwise (``unchanged``).
      * ``force`` — always rewrite (``forceRewritten``, or
        ``newlyGenerated`` when the file did not exist).

    When ``preserve_enrichments`` is true (default), enrichment fields on
    matching @id nodes in an existing file are merged onto ``doc`` before
    writing. See ``merge_existing_enrichments``.
    """
    if mode not in GENERATION_MODES:
        raise ValueError(f"Unsupported generation mode: {mode}")

    existed = out_path.exists()
    if existed and preserve_enrichments:
        doc = merge_existing_enrichments(doc, out_path)
    if existed and mode == "missing-only":
        existing = _load_existing_doc(out_path)
        if existing is not None and not existing_law_is_stale(
            existing, expected_kehtiv, expected_tid
        ):
            return "existingSkipped"
        save_json(out_path, doc)
        return "refreshedStale"
    if existed and mode == "refresh" and existing_doc_matches(out_path, doc):
        return "unchanged"

    save_json(out_path, doc)
    if not existed:
        return "newlyGenerated"
    if mode == "force":
        return "forceRewritten"
    return "refreshed"


def remove_obsolete_multipart_outputs(
    krr_dir: Path, slug: str, expected_filenames: set[str]
) -> list[Path]:
    """Delete stale ``<slug>_osa*_peep.json`` files no longer generated."""
    removed: list[Path] = []
    part_name_re = re.compile(rf"{re.escape(slug)}_osa\d+_peep\.json\Z")
    for path in sorted(krr_dir.glob(f"{slug}_osa*_peep.json")):
        if not part_name_re.fullmatch(path.name):
            continue
        if path.name in expected_filenames:
            continue
        path.unlink()
        removed.append(path)
    return removed


def _cached_xml_root(cache_name: str, tid: str | None = None) -> ET.Element | None:
    """Return a parsed law XML root from the local cache, or None.

    Like ``fetch_xml``'s cache lookup but NEVER falls back to the network —
    used by the post-generation reconciliation so a cleanup pass cannot
    trigger HTTP. Shares ``_read_cached_rt_root`` so the same ``MIN_XML_BYTES``
    floor, ``_is_trustworthy_xml_root`` denylist, and tid-then-legacy fallback
    apply (#601).
    """
    return _read_cached_rt_root(cache_name, tid)


def _expected_osa_filenames(slug: str, root: ET.Element) -> set[str]:
    """The ``<slug>_osaN_peep.json`` files ``generate_multipart_law`` would emit.

    Mirrors that function's osa selection exactly (an ``osa`` element with a
    non-empty ``osaNr`` and at least one ``paragrahv``), so the reconciliation
    pass can delete fresh-but-orphaned parts that are no longer in the law's
    current structure, not only snapshot-stale ones.
    """
    names: set[str] = set()
    for osa_el in root.iter():
        if ln(osa_el.tag) != "osa":
            continue
        osa_nr = ct(osa_el, "osaNr")
        if not osa_nr:
            continue
        if not any(ln(el.tag) == "paragrahv" for el in osa_el.iter()):
            continue
        names.add(f"{slug}_osa{osa_nr}_peep.json")
    return names


def _law_file_base_slug(name: str) -> str:
    """Return the law slug for a peep filename.

    Strips the trailing ``_peep.json`` and any ``_osaN`` part suffix so a
    multipart law's per-osa files all resolve to the parent slug:
      * ``advokatuuriseadus_peep.json`` -> ``advokatuuriseadus``
      * ``volaigusseadus_osa2_peep.json`` -> ``volaigusseadus``
    """
    stem = name.removesuffix("_peep.json")
    return re.sub(r"_osa\d+$", "", stem)


def source_removed_law_files(
    krr_dir: Path,
    current_titles: set[str],
    *,
    current_slugs: set[str] | None = None,
    multipart_titles: set[str] | None = None,
) -> list[str]:
    """List root ``*_peep.json`` files whose law is no longer in the source.

    Identity is slug-based rather than ``dc:source`` text, so the check
    is robust to historical drift in the ``dc:source`` literal format.
    A file is reported when the slug derived from its filename (with any
    ``_osaN`` suffix stripped) does not match the slug of any title in
    ``current_titles`` (the titles the live search returned this run).
    Reported, NOT deleted — mirrors
    ``generate_regulations.source_removed_files``.
    """
    if current_slugs is None:
        current_slugs = {slugify(t) for t in current_titles}
    # A multipart law also "owns" its per-osa slug shape; nothing extra to
    # add to the set because ``_law_file_base_slug`` already collapses
    # ``_osaN`` back to the parent slug.
    removed: list[str] = []
    for path in sorted(krr_dir.glob("*_peep.json")):
        base_slug = _law_file_base_slug(path.name)
        if base_slug and base_slug not in current_slugs:
            removed.append(path.name)
    return removed


# ---------------------------------------------------------------------------
# Manifest replay (#108)
# ---------------------------------------------------------------------------


def load_law_list_from_manifest(manifest_path: Path) -> dict[str, dict]:
    """Read a previously-emitted manifest and reconstruct an ``all_laws``-like dict.

    The returned shape matches what ``get_all_laws`` produces
    (``{title: {"gid", "tid", "url", "kehtivus", "lyhend"}}``) so the rest
    of ``main()`` can run unchanged. Only the act *list* is replayed —
    the per-act XML is still fetched live (keyed on the recorded
    ``terviktekstId`` so a snapshot edition that has since changed misses
    the cache). The ``outputsAll`` block is preferred; ``outputs`` /
    ``outputsGenerated`` are accepted as fallbacks for older manifests.

    Raises ``ValueError`` when the manifest is missing, unreadable, or
    carries no usable act list.
    """
    if not manifest_path.exists():
        raise ValueError(f"Manifest not found: {manifest_path}")
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Cannot read manifest {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"Manifest {manifest_path} is not a JSON object")

    entries = None
    for key in ("outputsAll", "outputsGenerated", "outputs"):
        candidate = manifest.get(key)
        if isinstance(candidate, list) and candidate:
            entries = candidate
            break
    if not entries:
        raise ValueError(
            f"Manifest {manifest_path} has no outputsAll/outputsGenerated/outputs list"
        )

    all_laws: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = entry.get("title")
        if not isinstance(title, str) or not title:
            continue
        url = entry.get("sourceUrl") or ""
        if isinstance(url, str) and url.startswith(BASE_URL):
            url = url[len(BASE_URL):]
        all_laws[title] = {
            "gid": "" if entry.get("globaalId") is None else str(entry.get("globaalId")),
            "tid": "" if entry.get("terviktekstId") is None else str(entry.get("terviktekstId")),
            "url": url,
            "kehtivus": {},
            "lyhend": entry.get("lyhend", ""),
        }
    if not all_laws:
        raise ValueError(
            f"Manifest {manifest_path} produced an empty law list (no usable entries)"
        )
    return all_laws


def load_committed_slug_map(manifest_path: Path) -> dict[str, str]:
    """Return ``{title: slug}`` from a manifest's ``outputsAll`` block.

    Feeds the freeze map for ``build_law_slug_map`` (#238) so already-committed
    collision suffixes never drift across snapshots. Missing/unreadable/empty
    manifests yield an empty map (never raises) — a fresh checkout then gets a
    pure gid-ordered, forward-stable assignment.
    """
    if not manifest_path.exists():
        return {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(manifest, dict):
        return {}
    entries = manifest.get("outputsAll")
    if not isinstance(entries, list):
        return {}
    mapping: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = entry.get("title")
        slug = entry.get("slug")
        if isinstance(title, str) and title and isinstance(slug, str) and slug:
            mapping[title] = slug
    return mapping


# Laws that should be split by osa (large multi-part laws)
MULTIPART_LAWS = {
    "Võlaõigusseadus",
    "Tsiviilseadustiku üldosa seadus",
    "Asjaõigusseadus",
    "Karistusseadustik",
    "Tsiviilkohtumenetluse seadustik",
    "Kriminaalmenetluse seadustik",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--missing-only", action="store_true", help="Only create outputs that do not exist (refreshing stale snapshots). Default.")
    mode.add_argument("--refresh", action="store_true", help="Refresh expected outputs for every source act.")
    mode.add_argument("--force", action="store_true", help="Force regeneration of every source act output.")
    parser.add_argument("--kehtiv", default=DEFAULT_KEHTIV, help="Snapshot date YYYY-MM-DD (default: %(default)s).")
    parser.add_argument(
        "--from-manifest",
        default=None,
        metavar="PATH",
        help=(
            "Replay the law list from a previously-emitted manifest "
            "(its outputsAll block) instead of querying the live search. "
            "Per-act XML is still fetched (keyed on the recorded terviktekstId)."
        ),
    )
    parser.add_argument("--allow-partial", action="store_true", help="Allow source-list fetch failures and mark the run partial.")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N law titles.")
    parser.add_argument(
        "--regen-state",
        nargs="?",
        const=DEFAULT_REGEN_STATE_SENTINEL,
        default=None,
        metavar="PATH",
        help=(
            "Persist per-law refresh progress to PATH. When PATH already "
            "exists, laws listed under completed are skipped so a long run "
            "can resume after interruption."
        ),
    )
    parser.add_argument(
        "--reset-regen-state",
        action="store_true",
        help=(
            "Discard an existing --regen-state file before running. Requires "
            "--regen-state so accidental state deletion is explicit."
        ),
    )
    return parser.parse_args()


def _manifest_entry(
    title: str,
    info: dict,
    *,
    slug_override: str | None = None,
    status: str = "full",
    reason: str | None = None,
) -> dict:
    """Build a per-act manifest entry from an ``all_laws`` row.

    Issue #119: ``status`` records whether the act was modeled with a
    structured body (``full``), as an act-level stub (``stub``), or could
    not be fetched/parsed (``failed``); ``skipped`` means the act was not
    selected for (re)generation this run (e.g. ``missing-only`` and the
    file is already up to date). ``reason`` carries a short human note for
    non-``full`` statuses.
    """
    slug = slug_override if slug_override is not None else slugify(title)
    url = info.get("url") or ""
    source_url = (
        BASE_URL + url if isinstance(url, str) and url.startswith("/") else url
    )
    entry: dict = {
        "title": title,
        "slug": slug,
        "terviktekstId": info.get("tid"),
        "globaalId": info.get("gid"),
        "sourceUrl": source_url,
        "status": status,
    }
    if reason:
        entry["reason"] = reason
    return entry


def _regen_state_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    if value == DEFAULT_REGEN_STATE_SENTINEL:
        return KRR_DIR / DEFAULT_REGEN_STATE_NAME
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def load_regen_state(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {
            "schemaVersion": REGEN_STATE_SCHEMA_VERSION,
            "completed": {},
            "failed": {},
        }
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"WARNING: regen state {path} unreadable; starting fresh: {exc}",
            file=sys.stderr,
        )
        return {
            "schemaVersion": REGEN_STATE_SCHEMA_VERSION,
            "completed": {},
            "failed": {},
        }
    if not isinstance(state, dict):
        return {
            "schemaVersion": REGEN_STATE_SCHEMA_VERSION,
            "completed": {},
            "failed": {},
        }
    state.setdefault("schemaVersion", REGEN_STATE_SCHEMA_VERSION)
    if not isinstance(state.get("completed"), dict):
        state["completed"] = {}
    if not isinstance(state.get("failed"), dict):
        state["failed"] = {}
    return state


def _current_laws_by_slug(
    all_laws: dict[str, dict],
    *,
    slug_by_title: dict[str, str] | None = None,
) -> dict[str, tuple[str, dict]]:
    if slug_by_title is None:
        slug_by_title = build_law_slug_map(all_laws)
    return {
        slug_by_title.get(title, slugify(title)): (title, info)
        for title, info in all_laws.items()
        if isinstance(title, str)
    }


def completed_regen_slugs(
    state: dict,
    all_laws: dict[str, dict],
    *,
    kehtiv: str,
    slug_by_title: dict[str, str] | None = None,
) -> set[str]:
    """Return compatible completed slugs without mutating state.

    This read-only helper is kept for diagnostics and tests; production resume
    state cleanup uses ``prune_completed_regen_state`` so stale entries are
    recorded before the run proceeds.
    """
    completed = state.get("completed")
    if not isinstance(completed, dict):
        return set()
    if state.get("schemaVersion") != REGEN_STATE_SCHEMA_VERSION:
        return set()

    current_by_slug = _current_laws_by_slug(all_laws, slug_by_title=slug_by_title)
    valid: set[str] = set()
    for slug, entry in list(completed.items()):
        if not isinstance(slug, str) or not isinstance(entry, dict):
            continue
        current = current_by_slug.get(slug)
        if current is None:
            continue
        title, info = current
        entry_title = entry.get("title")
        if isinstance(entry_title, str) and entry_title != title:
            continue
        expected_tid = info.get("tid")
        if entry.get("kehtiv") != kehtiv:
            continue
        if str(entry.get("terviktekstId") or "") != str(expected_tid or ""):
            continue
        valid.add(slug)
    return valid


def _normalize_dropped_completed_record(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    entries = value.get("entries")
    if not isinstance(entries, dict):
        return None
    normalized_entries = {
        key: str(reason)
        for key, reason in entries.items()
        if isinstance(key, str)
    }
    if not normalized_entries:
        return None
    at = value.get("at")
    if not isinstance(at, str) or not at:
        at = datetime.now(UTC).isoformat()
    return {
        "at": at,
        "entries": normalized_entries,
    }


def _append_dropped_completed(state: dict, entries: dict[str, str]) -> None:
    if not entries:
        return
    record = {
        "at": datetime.now(UTC).isoformat(),
        "entries": entries,
    }
    history = state.get("droppedCompleted")
    if isinstance(history, list):
        history.append(record)
    elif isinstance(history, dict):
        legacy = _normalize_dropped_completed_record(history)
        state["droppedCompleted"] = ([legacy] if legacy else []) + [record]
    else:
        state["droppedCompleted"] = [record]


def prune_completed_regen_state(
    state: dict,
    all_laws: dict[str, dict],
    *,
    kehtiv: str,
    slug_by_title: dict[str, str] | None = None,
) -> set[str]:
    """Drop stale completed entries from regen state and return valid slugs."""
    completed = state.get("completed")
    if not isinstance(completed, dict):
        return set()
    if state.get("schemaVersion") != REGEN_STATE_SCHEMA_VERSION:
        stale = {
            slug: "schemaVersion changed"
            for slug in completed
            if isinstance(slug, str)
        }
        completed.clear()
        failed = state.get("failed")
        if isinstance(failed, dict):
            failed.clear()
        state["schemaVersion"] = REGEN_STATE_SCHEMA_VERSION
        _append_dropped_completed(state, stale)
        return set()

    current_by_slug = _current_laws_by_slug(all_laws, slug_by_title=slug_by_title)
    valid: set[str] = set()
    stale: dict[str, str] = {}
    for slug, entry in list(completed.items()):
        if not isinstance(slug, str) or not isinstance(entry, dict):
            if isinstance(slug, str):
                stale[slug] = "invalid completed entry"
            completed.pop(slug, None)
            continue
        current = current_by_slug.get(slug)
        if current is None:
            stale[slug] = "not in current source list"
            completed.pop(slug, None)
            continue
        title, info = current
        entry_title = entry.get("title")
        if isinstance(entry_title, str) and entry_title != title:
            stale[slug] = "title changed"
            completed.pop(slug, None)
            continue
        expected_tid = info.get("tid")
        if entry.get("kehtiv") != kehtiv:
            stale[slug] = "kehtiv changed"
            completed.pop(slug, None)
            continue
        if str(entry.get("terviktekstId") or "") != str(expected_tid or ""):
            stale[slug] = "terviktekstId changed"
            completed.pop(slug, None)
            continue
        valid.add(slug)
    if stale:
        _append_dropped_completed(state, stale)
    return valid


def _relative_output(path: Path) -> str:
    try:
        return str(path.relative_to(KRR_DIR))
    except ValueError:
        return str(path)


def _subsection_count(doc: dict) -> int:
    return sum(
        1
        for node in doc.get("@graph", [])
        if isinstance(node, dict)
        and "estleg:Subsection" in (node.get("@type") or [])
    )


def save_regen_state(path: Path | None, state: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    state["schemaVersion"] = REGEN_STATE_SCHEMA_VERSION
    state["updatedAt"] = datetime.now(UTC).isoformat()
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


def record_regen_state(
    path: Path | None,
    state: dict,
    *,
    title: str,
    slug: str,
    info: dict,
    status: str,
    output_paths: list[Path] | None = None,
    subsection_count: int = 0,
    kehtiv: str | None = None,
    reason: str | None = None,
    warnings: list[str] | None = None,
) -> None:
    if path is None:
        return
    entry = {
        "title": title,
        "slug": slug,
        "globaalId": info.get("gid"),
        "terviktekstId": info.get("tid"),
        "kehtiv": kehtiv,
        "sourceUrl": info.get("url"),
        "status": status,
        "outputPaths": [_relative_output(p) for p in (output_paths or [])],
        "subsectionCount": subsection_count,
        "warnings": warnings or [],
        "completedAt": datetime.now(UTC).isoformat(),
    }
    if reason:
        entry["reason"] = reason
    if status in {"full", "stub"}:
        state.setdefault("completed", {})[slug] = entry
        state.setdefault("failed", {}).pop(slug, None)
    else:
        state.setdefault("failed", {})[slug] = entry
    save_regen_state(path, state)


def main():
    args = parse_args()
    mode = "refresh" if args.refresh else "force" if args.force else "missing-only"
    regen_state_path = _regen_state_path(getattr(args, "regen_state", None))
    if getattr(args, "reset_regen_state", False) and regen_state_path is None:
        print("FATAL: --reset-regen-state requires --regen-state", file=sys.stderr)
        sys.exit(2)
    if getattr(args, "reset_regen_state", False) and regen_state_path is not None:
        try:
            regen_state_path.unlink()
            print(f"Reset regen state: {regen_state_path}")
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"FATAL: cannot reset regen state: {exc}", file=sys.stderr)
            sys.exit(2)
    regen_state = load_regen_state(regen_state_path)
    if regen_state_path is not None:
        regen_state.setdefault("startedAt", datetime.now(UTC).isoformat())
        regen_state["mode"] = mode
        regen_state["kehtiv"] = args.kehtiv
        save_regen_state(regen_state_path, regen_state)

    print("=" * 70)
    print("Estonian Legal Ontology - Generate ALL Laws from Riigi Teataja")
    print("=" * 70)

    # Issue #165 fix 1 / fix 7: reset the default allocator so a stale
    # in-memory state from a previous run (in long-lived test
    # processes, REPL sessions, etc.) cannot bias prefix selection.
    _default_allocator.reset()

    # Step 1: Get the law list — either replayed from a manifest (#108)
    # or queried live from Riigi Teataja.
    from_manifest = getattr(args, "from_manifest", None)
    if from_manifest:
        print(f"\n[1/4] Replaying law list from manifest: {from_manifest}")
        try:
            all_laws = load_law_list_from_manifest(Path(from_manifest))
        except ValueError as exc:
            print(f"  FATAL: {exc}")
            sys.exit(2)
        source_manifest = {
            "requestedDocument": "seadus",
            "kehtiv": args.kehtiv,
            "replayedFromManifest": str(from_manifest),
            "uniqueActs": len(all_laws),
            # A replay regenerates only from the recorded act list; the
            # live source list is *not* re-queried, so we cannot vouch
            # for its completeness beyond what the manifest captured.
            "complete": True,
            "partialAllowed": args.allow_partial,
        }
    else:
        print("\n[1/4] Fetching law list from Riigi Teataja API...")
        try:
            all_laws, source_manifest = get_all_laws(kehtiv=args.kehtiv, allow_partial=args.allow_partial)
        except SourceListFetchError as exc:
            print(f"  FATAL: {exc}")
            sys.exit(2)
    if args.limit is not None:
        all_laws = dict(sorted(all_laws.items())[:args.limit])
        source_manifest["limited"] = True
        source_manifest["limit"] = args.limit
    print(f"  Found {len(all_laws)} unique law titles")
    # Freeze already-committed collision suffixes so a snapshot refresh never
    # drifts an existing output filename / IRI (#238). New colliders fall back
    # to a deterministic gid-ordered assignment inside build_law_slug_map.
    committed_slug_by_title = load_committed_slug_map(KRR_DIR / MANIFEST_NAME)
    slug_by_title = build_law_slug_map(
        all_laws, existing_slug_by_title=committed_slug_by_title
    )
    completed_slugs = prune_completed_regen_state(
        regen_state, all_laws, kehtiv=args.kehtiv, slug_by_title=slug_by_title
    )
    if regen_state_path is not None:
        save_regen_state(regen_state_path, regen_state)

    # Step 2: Check existing files
    print("\n[2/4] Checking existing files...")
    existing = get_existing_files()
    print(f"  Found {len(existing)} existing output files")

    # Step 3: Determine which laws to (re)generate. In missing-only mode
    # an act is still selected when its on-disk snapshot is stale
    # (estleg:kehtiv drift) so a stale snapshot gets refreshed quietly —
    # this covers both the single-file shape (``<slug>_peep.json``) and
    # multipart laws (any ``<slug>_osa*_peep.json`` whose stored snapshot
    # is out of date).
    def _existing_paths(slug: str, title: str) -> list[Path]:
        out: list[Path] = []
        single = KRR_DIR / f"{slug}_peep.json"
        if single.exists():
            out.append(single)
        if title in MULTIPART_LAWS:
            out.extend(sorted(KRR_DIR.glob(f"{slug}_osa*_peep.json")))
        return out

    to_generate: dict[str, dict] = {}
    already_mapped = 0
    resumed_completed_titles: set[str] = set()
    for title, info in sorted(all_laws.items()):
        slug = slug_by_title[title]
        if slug in completed_slugs:
            resumed_completed_titles.add(title)
            continue
        if mode == "missing-only" and has_existing_output(existing, title, slug):
            stale = False
            for path in _existing_paths(slug, title):
                existing_doc = _load_existing_doc(path)
                if existing_doc is not None and existing_law_is_stale(
                    existing_doc, args.kehtiv, info.get("tid")
                ):
                    stale = True
                    break
            if not stale:
                already_mapped += 1
                continue
        to_generate[title] = {**info, "slug": slug}

    print(f"  Already mapped: {already_mapped}")
    print(f"  To generate: {len(to_generate)}")

    # Step 4: Generate each law
    print(f"\n[3/4] Generating {len(to_generate)} laws...")
    run_counts = Counter({
        "newlyGenerated": 0,
        "existingSkipped": 0,
        "refreshedStale": 0,
        "unchanged": 0,
        "refreshed": 0,
        "forceRewritten": 0,
        "obsoleteMultipartRemoved": 0,
    })
    # Slugs whose osa parts were (re)generated via the multipart branch this
    # run; their obsolete-osa cleanup already ran in-loop with the
    # authoritative expected set, so the post-generation reconciliation pass
    # must not re-sweep them (#237/#246).
    multipart_generated_slugs: set[str] = set()
    generated = 0
    failed = 0
    skipped = 0  # stub acts (no structured body)
    # Per-act outcome ledger keyed by title: (status, reason). Folded into
    # the manifest's outputsAll/outputsGenerated entries (#119).
    act_status: dict[str, tuple[str, str | None]] = {
        title: ("skipped", "completed in regen state")
        for title in resumed_completed_titles
    }

    for i, (title, info) in enumerate(sorted(to_generate.items()), 1):
        slug = info["slug"]
        url = info["url"]
        abbreviation = info.get("lyhend", "")

        print(f"\n  [{i}/{len(to_generate)}] {title}")
        print(f"    slug: {slug}, url: {url}")

        # Fetch XML — pass tid so cache invalidates when RT publishes a
        # newer terviktekst edition (#165 fix 9).
        root = fetch_xml(url, slug, tid=info.get("tid"))
        if root is None:
            print("    SKIP: Could not fetch XML")
            failed += 1
            act_status[title] = ("failed", "XML fetch/parse failed")
            record_regen_state(
                regen_state_path,
                regen_state,
                title=title,
                slug=slug,
                info=info,
                status="failed",
                kehtiv=args.kehtiv,
                reason="XML fetch/parse failed",
            )
            continue

        # Count paragraphs
        par_count = sum(1 for el in root.iter() if ln(el.tag) == "paragrahv")
        if par_count == 0:
            filename = f"{slug}_peep.json"
            out_path = KRR_DIR / filename
            # Issue #594: structural-staleness guard. A zero-paragraph parse
            # is only a *genuine* body-less act when the recognized RT root
            # carries no structural-body tag at all AND no previously-built
            # structured peep exists. If the root DOES carry block tags
            # (``loige``/``peatykk``/``jagu``/``jaotis`` — a future RT
            # paragraph-tag rename or a partial fetch) or a structured peep
            # already exists, refuse to overwrite it with a sticky body-less
            # stub: record ``failed`` so ``missing-only`` retries instead of
            # laundering the degradation into a permanent stub that
            # ``existing_law_is_stale`` (kehtiv/tid only) never re-checks.
            degraded_structure = xml_root_has_structure(root)
            had_structure = existing_peep_is_structured(out_path)
            if degraded_structure or had_structure:
                why = (
                    "root carries structural-body tags but zero paragraphs"
                    if degraded_structure
                    else "existing peep had structured provisions"
                )
                reason = (
                    f"degraded/empty parse refused (no stub written): {why}"
                )
                print(f"    SKIP (degraded): {reason}")
                failed += 1
                act_status[title] = ("failed", reason)
                record_regen_state(
                    regen_state_path,
                    regen_state,
                    title=title,
                    slug=slug,
                    info=info,
                    status="failed",
                    kehtiv=args.kehtiv,
                    reason=reason,
                )
                continue
            doc = generate_law_stub_jsonld(
                title, slug, root, abbreviation, rt_url=url,
                kehtiv=args.kehtiv, terviktekst_id=info.get("tid"),
            )
            status = write_law_output(
                out_path,
                doc,
                mode=mode,
                expected_kehtiv=args.kehtiv,
                expected_tid=info.get("tid"),
            )
            run_counts[status] += 1
            if status != "existingSkipped":
                generated += 1
            skipped += 1
            print(f"    Saved stub: {filename} (no structured body, {status})")
            act_status[title] = ("stub", "no structured paragraph nodes in source XML")
            record_regen_state(
                regen_state_path,
                regen_state,
                title=title,
                slug=slug,
                info=info,
                status="stub",
                output_paths=[out_path],
                kehtiv=args.kehtiv,
                reason="no structured paragraph nodes in source XML",
            )
            continue

        # Check if multi-part
        osa_count = sum(1 for el in root.iter() if ln(el.tag) == "osa")

        if title in MULTIPART_LAWS and osa_count > 1:
            # Generate separate files per osa
            output_paths: list[Path] = []
            subsection_count = 0
            try:
                results = generate_multipart_law(
                    title, slug, root, abbreviation, rt_url=url,
                    kehtiv=args.kehtiv, terviktekst_id=info.get("tid"),
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                reason = f"multipart generation failed: {exc}"
                act_status[title] = ("failed", reason)
                record_regen_state(
                    regen_state_path,
                    regen_state,
                    title=title,
                    slug=slug,
                    info=info,
                    status="failed",
                    output_paths=output_paths,
                    subsection_count=subsection_count,
                    kehtiv=args.kehtiv,
                    reason=reason,
                )
                print(f"    FAIL: {reason}")
                continue
            try:
                for filename, doc in results:
                    out_path = KRR_DIR / filename
                    # Issue #165 fix 3 + #108: respect the run mode and refresh
                    # stale osa snapshots in missing-only (the multipart branch
                    # used to hard-code ``if not exists``).
                    status = write_law_output(
                        out_path,
                        doc,
                        mode=mode,
                        expected_kehtiv=args.kehtiv,
                        expected_tid=info.get("tid"),
                    )
                    output_paths.append(out_path)
                    subsection_count += _subsection_count(doc)
                    run_counts[status] += 1
                    if status != "existingSkipped":
                        generated += 1
                    print(
                        f"    Saved: {filename} ({len(doc['@graph'])} nodes, {status})"
                    )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                reason = f"multipart output write failed: {exc}"
                act_status[title] = ("failed", reason)
                record_regen_state(
                    regen_state_path,
                    regen_state,
                    title=title,
                    slug=slug,
                    info=info,
                    status="failed",
                    output_paths=output_paths,
                    subsection_count=subsection_count,
                    kehtiv=args.kehtiv,
                    reason=reason,
                )
                print(f"    FAIL: {reason}")
                continue
            warnings = []
            try:
                obsolete_paths = remove_obsolete_multipart_outputs(
                    KRR_DIR, slug, {filename for filename, _doc in results}
                )
                run_counts["obsoleteMultipartRemoved"] += len(obsolete_paths)
                for obsolete_path in obsolete_paths:
                    print(f"    Removed obsolete part: {obsolete_path.name}")
            except Exception as exc:  # noqa: BLE001
                warning = f"obsolete multipart cleanup failed: {exc}"
                warnings.append(warning)
                print(f"    WARN: {warning}")
            multipart_generated_slugs.add(slug)
            if subsection_count == 0 and any(
                ln(el.tag) == "loige" for el in root.iter()
            ):
                warnings.append("source XML has loige elements but emitted no Subsection nodes")
            act_status[title] = ("full", None)
            record_regen_state(
                regen_state_path,
                regen_state,
                title=title,
                slug=slug,
                info=info,
                status="full",
                output_paths=output_paths,
                subsection_count=subsection_count,
                kehtiv=args.kehtiv,
                warnings=warnings,
            )
        else:
            # Single file
            doc = generate_law_jsonld(
                title, slug, root, abbreviation, rt_url=url,
                kehtiv=args.kehtiv, terviktekst_id=info.get("tid"),
            )
            filename = f"{slug}_peep.json"
            out_path = KRR_DIR / filename
            status = write_law_output(
                out_path,
                doc,
                mode=mode,
                expected_kehtiv=args.kehtiv,
                expected_tid=info.get("tid"),
            )
            run_counts[status] += 1
            if status != "existingSkipped":
                generated += 1
            node_count = len(doc["@graph"])
            print(f"    Saved: {filename} ({node_count} nodes, {par_count} paragraphs, {status})")
            act_status[title] = ("full", None)
            subsection_count = _subsection_count(doc)
            warnings = []
            if subsection_count == 0 and any(
                ln(el.tag) == "loige" for el in root.iter()
            ):
                warnings.append("source XML has loige elements but emitted no Subsection nodes")
            record_regen_state(
                regen_state_path,
                regen_state,
                title=title,
                slug=slug,
                info=info,
                status="full",
                output_paths=[out_path],
                subsection_count=subsection_count,
                kehtiv=args.kehtiv,
                warnings=warnings,
            )

        # Rate limit - be polite to Riigi Teataja
        time.sleep(0.3)

    # Stale multipart reconciliation (#237, #246): the in-loop osa cleanup
    # only runs in the multipart branch. Laws skipped from ``to_generate``
    # (fresh in missing-only mode) AND laws regenerated via the single-file /
    # stub branch (e.g. a formerly-multipart law that is no longer multipart)
    # would otherwise keep stale or structurally-orphaned ``<slug>_osaN_peep.json``
    # parts. Sweep every source law NOT handled by the multipart branch here,
    # AFTER generation.
    # Files already unlinked inside the main loop are gone from disk, so this
    # pass cannot double-count them.
    for title, info in sorted(all_laws.items()):
        slug = slug_by_title[title]
        if slug in multipart_generated_slugs:
            # Multipart parts (re)generated this run already had their
            # obsolete-osa cleanup with the authoritative expected set.
            continue
        try:
            if title in MULTIPART_LAWS:
                # Skipped multipart law: reconcile against the osa set its
                # cached XML would currently produce, so a fresh-but-orphaned
                # part (current kehtiv/tid yet no longer in the structure) is
                # removed too, not only snapshot-stale parts (#246). Fall back
                # to a stale-only sweep when the XML is not cached — never fetch
                # from the network during cleanup.
                cached_root = _cached_xml_root(slug, info.get("tid"))
                if cached_root is not None:
                    obsolete_paths = remove_obsolete_multipart_outputs(
                        KRR_DIR, slug, _expected_osa_filenames(slug, cached_root)
                    )
                    run_counts["obsoleteMultipartRemoved"] += len(obsolete_paths)
                    for obsolete_path in obsolete_paths:
                        print(f"    Removed orphan multipart part: {obsolete_path.name}")
                else:
                    for part_path in sorted(KRR_DIR.glob(f"{slug}_osa*_peep.json")):
                        existing_doc = _load_existing_doc(part_path)
                        if existing_doc is not None and existing_law_is_stale(
                            existing_doc, args.kehtiv, info.get("tid")
                        ):
                            part_path.unlink()
                            run_counts["obsoleteMultipartRemoved"] += 1
                            print(f"    Removed stale multipart part: {part_path.name}")
            else:
                # Law is no longer multipart: any ``_osaN`` part is orphaned.
                obsolete_paths = remove_obsolete_multipart_outputs(
                    KRR_DIR, slug, set()
                )
                run_counts["obsoleteMultipartRemoved"] += len(obsolete_paths)
                for obsolete_path in obsolete_paths:
                    print(f"    Removed orphan multipart part: {obsolete_path.name}")
        except Exception as exc:  # noqa: BLE001
            print(f"    WARN: multipart reconciliation failed for {slug}: {exc}")

    # Source-removed detection (#108): existing peep files whose law title
    # no longer appears in the current source list. Reported, not deleted.
    source_removed = source_removed_law_files(
        KRR_DIR,
        set(all_laws),
        current_slugs=set(slug_by_title.values()),
        multipart_titles=MULTIPART_LAWS,
    )

    # Step 4: Summary
    print("\n" + "=" * 70)
    print("[4/4] SUMMARY")
    print("=" * 70)
    print(f"  Total laws in Riigi Teataja: {len(all_laws)}")
    print(f"  Already mapped (up to date): {already_mapped}")
    print(f"  Newly generated/written:     {generated}")
    print(f"  Unchanged (refresh):         {run_counts['unchanged']}")
    print(f"  Refreshed stale (missing-only): {run_counts['refreshedStale']}")
    print(f"  Refreshed (refresh):         {run_counts['refreshed']}")
    print(f"  Force rewritten:             {run_counts['forceRewritten']}")
    print(f"  Obsolete multipart files removed: {run_counts['obsoleteMultipartRemoved']}")
    print(f"  Stub acts (no paragraphs):   {skipped}")
    print(f"  Failed (fetch errors):       {failed}")
    print(f"  Source-removed peep files:   {len(source_removed)}")
    print(f"  Total files now:             {len(list(KRR_DIR.glob('*_peep.json')))}")

    # Issue #165 fix 8: ``outputs`` keeps the existing meaning (delta
    # for this run). ``outputsAll`` is the mode-invariant projection of
    # every act. Issue #119: each entry now carries ``status`` (and an
    # optional ``reason``).
    def _status_for(title: str) -> tuple[str, str | None]:
        if title in act_status:
            return act_status[title]
        if title in to_generate:
            # Selected but never reached (shouldn't happen) — be honest.
            return ("failed", "not processed")
        # Not selected this run: in missing-only that means an existing
        # up-to-date file, in refresh/force it can't happen.
        return ("skipped", "existing file up to date" if mode == "missing-only" else None)

    outputs_all = []
    for title, info in sorted(all_laws.items()):
        status, reason = _status_for(title)
        outputs_all.append(
            _manifest_entry(
                title,
                info,
                slug_override=slug_by_title.get(title),
                status=status,
                reason=reason,
            )
        )
    outputs_generated = []
    for title, info in sorted(to_generate.items()):
        status, reason = _status_for(title)
        outputs_generated.append(
            _manifest_entry(
                title, info, slug_override=info.get("slug"), status=status, reason=reason
            )
        )

    status_counts = Counter(entry["status"] for entry in outputs_all)

    manifest = {
        "generated": datetime.now(UTC).isoformat(),
        "mode": mode,
        "source": source_manifest,
        "counts": {
            "sourceActs": len(all_laws),
            "alreadyMapped": already_mapped,
            "selectedForGeneration": len(to_generate),
            "generatedFiles": generated,
            "stubbedActs": skipped,
            "failedFetches": failed,
            "fullActs": status_counts.get("full", 0),
            "stubActs": status_counts.get("stub", 0),
            "failedActs": status_counts.get("failed", 0),
            "skippedActs": status_counts.get("skipped", 0),
        },
        # Unchanged/refreshed split mirroring generate_regulations' run block.
        "run": {
            "generationMode": mode,
            "newlyGenerated": run_counts["newlyGenerated"],
            "existingSkipped": run_counts["existingSkipped"] + already_mapped,
            "resumedCompleted": len(resumed_completed_titles),
            "refreshedStale": run_counts["refreshedStale"],
            "unchanged": run_counts["unchanged"],
            "refreshed": run_counts["refreshed"],
            "forceRewritten": run_counts["forceRewritten"],
            "obsoleteMultipartRemoved": run_counts["obsoleteMultipartRemoved"],
            "failedFetches": failed,
            "partialAllowed": args.allow_partial,
            "sourceRemovedFromSnapshotCount": len(source_removed),
            "sourceRemovedFromSnapshot": source_removed,
        },
        # Backwards-compat alias: old downstreams keep working.
        "outputs": outputs_generated,
        # New keys (#165 fix 8 / #119):
        "outputsGenerated": outputs_generated,
        "outputsAll": outputs_all,
    }
    save_json(KRR_DIR / MANIFEST_NAME, manifest)


if __name__ == "__main__":
    main()
