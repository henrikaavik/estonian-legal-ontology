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

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
DATA_DIR = REPO_ROOT / "data" / "riigiteataja"
DATA_DIR.mkdir(parents=True, exist_ok=True)

SEARCH_URL = "https://www.riigiteataja.ee/api/oigusakt_otsing/1/otsi"
BASE_URL = "https://www.riigiteataja.ee"
NS = "https://data.riik.ee/ontology/estleg#"
DEFAULT_KEHTIV = "2026-05-01"
GENERATION_MODES = ("missing-only", "refresh", "force")
MANIFEST_NAME = "generation_manifest_laws.json"

CONTEXT = {
    "estleg": NS,
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
}


def ln(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def ct(el: ET.Element, name: str) -> str | None:
    for c in el:
        if ln(c.tag) == name and c.text:
            return c.text.strip()
    return None


def slugify(text: str) -> str:
    """Convert Estonian text to a filename-safe slug."""
    # Transliterate Estonian chars
    replacements = {
        "ä": "a", "ö": "o", "ü": "u", "õ": "o",
        "Ä": "A", "Ö": "O", "Ü": "U", "Õ": "O",
        "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    return text[:80]


_ESTONIAN_TRANSLITERATION: dict[str, str] = {
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
}
_TRANSLIT_TABLE = str.maketrans(_ESTONIAN_TRANSLITERATION)


def sanitize_id(value: str) -> str:
    s = value.replace(" ", "_")
    # Transliterate Estonian diacritics before stripping non-ASCII
    s = s.translate(_TRANSLIT_TABLE)
    s = re.sub(r"[^0-9A-Za-z_]", "", s)
    return s or "Unknown"


def collect_text(el: ET.Element, max_len: int = 500) -> str:
    parts: list[str] = []
    for child in el.iter():
        tag = ln(child.tag)
        if tag in ("loige", "lauseOsa", "lause", "tavatekst"):
            txt = "".join(child.itertext()).strip()
            txt = re.sub(r"\s+", " ", txt)
            if txt and len(txt) > 3:
                parts.append(txt)
        if len(" ".join(parts)) >= max_len:
            break
    joined = " ".join(parts)
    return joined[:max_len] if joined else ""


# Mapping of Unicode superscript digits to plain digits.
_SUPERSCRIPT_DIGIT_MAP: dict[str, str] = {
    "¹": "1",  # superscript 1
    "²": "2",  # superscript 2
    "³": "3",  # superscript 3
    "⁰": "0",  # superscript 0
    "⁴": "4",  # superscript 4
    "⁵": "5",  # superscript 5
    "⁶": "6",  # superscript 6
    "⁷": "7",  # superscript 7
    "⁸": "8",  # superscript 8
    "⁹": "9",  # superscript 9
}


def _superscript_index(par_el: ET.Element) -> str:
    """Extract the superscript index for a paragrahv element.

    Riigi Teataja XML records superscripted paragraph numbers in two
    forms:
      * ``ylaIndeks="N"`` attribute on the ``paragrahvNr`` element
        (canonical, machine-friendly);
      * the ``kuvatavNr`` CDATA, either as a Unicode superscript glyph
        or as ``<sup>N</sup>`` markup.

    Returns the index as a plain digit string ("1", "2", ...) or ``""``
    when no superscript is present.
    """
    for c in par_el:
        if ln(c.tag) == "paragrahvNr":
            ya = c.attrib.get("ylaIndeks")
            if ya and ya.strip():
                return ya.strip()
            break

    kuv = None
    for c in par_el:
        if ln(c.tag) == "kuvatavNr":
            kuv = "".join(c.itertext()) or ""
            break
    if not kuv:
        return ""

    m = re.search(
        r"\d+\s*(?:<sup>\s*(\d+)\s*</sup>|([¹²³⁰⁴-⁹]+))",
        kuv,
    )
    if not m:
        return ""
    if m.group(1):
        return m.group(1).strip()
    if m.group(2):
        return "".join(_SUPERSCRIPT_DIGIT_MAP.get(ch, "") for ch in m.group(2))
    return ""


def _paragraph_id_suffix(par_el: ET.Element) -> str:
    """Return the canonical IRI suffix for a paragrahv element.

    Combines ``paragrahvNr`` with any superscript index so that
    duplicate paragraph numbers (``§ 22`` and ``§ 22¹``) get distinct,
    order-independent IRIs (``Par_22`` vs ``Par_22_1``). This replaces
    the previous insertion-order disambiguation (#156, #165 fix 2).
    """
    par_nr = ct(par_el, "paragrahvNr") or ""
    sup = _superscript_index(par_el)
    base = sanitize_id(par_nr) if par_nr else "Unknown"
    if sup:
        return f"{base}_{sanitize_id(sup)}"
    return base


def _walk_paragraphs_direct(
    container: ET.Element,
    *,
    block_tags: tuple[str, ...] = ("peatykk", "jagu", "osa"),
) -> list[ET.Element]:
    """Return paragrahv elements that belong DIRECTLY to ``container``.

    Recursively descends through wrapper elements but stops as soon as
    it encounters a deeper structural block — paragraphs inside those
    nested blocks belong to the inner block, not to ``container``.
    Replaces the ``ch.iter()`` walk that mis-attributed paragraphs in
    osa → peatykk → jagu nesting (#166).
    """
    out: list[ET.Element] = []
    for child in container:
        tag = ln(child.tag)
        if tag == "paragrahv":
            out.append(child)
        elif tag in block_tags:
            continue
        else:
            out.extend(_walk_paragraphs_direct(child, block_tags=block_tags))
    return out


def _loige_body_text(loige_el: ET.Element) -> str:
    """Return the joined text of one ``loige`` subtree.

    Concatenates the ``lauseOsa``/``lause``/``tavatekst`` fragments below
    the lõige, normalising whitespace and dropping fragments of length
    <= 3 (the same filter ``collect_full_text`` applies). The lõige's own
    ``(N)`` marker is *not* prepended here — callers add it when they want
    the marker (the provision-level ``estleg:legalText`` does;
    ``estleg:legalText`` on a Subsection node also does, mirroring the
    provision-level form).
    """
    body_parts: list[str] = []
    for grandchild in loige_el.iter():
        tag = ln(grandchild.tag)
        if tag in ("lauseOsa", "lause", "tavatekst"):
            txt = "".join(grandchild.itertext()).strip()
            txt = re.sub(r"\s+", " ", txt)
            if txt and len(txt) > 3:
                body_parts.append(txt)
    return " ".join(body_parts).strip()


def _superscript_from_text(value: str) -> str:
    """Extract a trailing superscript index from a number-bearing string.

    Mirrors the ``kuvatavNr`` branch of ``_superscript_index`` but works
    on a bare string (a ``loige`` ``kuvatavNr`` like ``(2)`` or ``(2¹)``).
    Machine ``loigeNr`` superscripts are carried in the ``ylaIndeks``
    attribute, so this fallback only matters for the display form.
    Returns the index as a plain digit string ("1", "2", ...) or ``""``.
    """
    if not value:
        return ""
    m = re.search(
        r"\d+\s*(?:<sup>\s*(\d+)\s*</sup>|([¹²³⁰⁴-⁹]+))",
        value,
    )
    if not m:
        return ""
    if m.group(1):
        return m.group(1).strip()
    if m.group(2):
        return "".join(_SUPERSCRIPT_DIGIT_MAP.get(ch, "") for ch in m.group(2))
    return ""


def _loige_numbers(loige_el: ET.Element) -> tuple[str, str]:
    """Return ``(display_number, id_suffix)`` for one ``loige`` element.

    The *display number* is the human-facing lõige label — preferring the
    ``kuvatavNr`` CDATA (stripped of its parentheses; e.g. ``(2¹)`` →
    ``2¹``) and falling back to ``loigeNr``. The *id suffix* is the
    canonical, order-independent IRI fragment: the base ``loigeNr`` digits
    plus any superscript index (``ylaIndeks`` on the ``loigeNr`` element,
    or a Unicode/``<sup>`` superscript glyph in ``kuvatavNr``), joined
    with an underscore — ``§ 14 lg 2¹`` → ``2_1``. This mirrors
    ``_paragraph_id_suffix`` for paragrahv elements.
    """
    loige_nr = ""
    ya = ""
    for sub in loige_el:
        if ln(sub.tag) == "loigeNr":
            if sub.text:
                loige_nr = sub.text.strip()
            ya = (sub.attrib.get("ylaIndeks") or "").strip()
            break

    kuv_raw = ""
    for sub in loige_el:
        if ln(sub.tag) == "kuvatavNr":
            kuv_raw = "".join(sub.itertext()).strip()
            break
    kuv_inner = re.sub(r"[()]", "", kuv_raw).strip() if kuv_raw else ""

    # Display: kuvatavNr (without parens) wins, else loigeNr, else "".
    display = kuv_inner or loige_nr

    # ID suffix base + superscript.
    base = loige_nr
    if not base:
        # No machine loigeNr — fall back to the digits in the display form.
        m = re.match(r"\s*(\d+)", kuv_inner)
        base = m.group(1) if m else kuv_inner
    sup = ya
    if not sup and kuv_raw:
        sup = _superscript_from_text(kuv_raw)
    # sanitize_id keeps [0-9A-Za-z_] only, so a base like "2" stays "2"
    # and any stray punctuation is dropped before it becomes an IRI fragment.
    base_clean = sanitize_id(base) if base else "Unknown"
    if sup:
        return display, f"{base_clean}_{sanitize_id(sup)}"
    return display, base_clean


def _iter_loiked(el: ET.Element) -> list[ET.Element]:
    """Return the direct ``loige`` children of a paragrahv element, in
    document order."""
    return [child for child in el if ln(child.tag) == "loige"]


def collect_full_text(el: ET.Element) -> str:
    """Return the complete text of a provision, preserving lõige boundaries.

    Each ``loige`` is prefixed with its own marker (``(N) ...``) drawn
    from ``loigeNr`` (or ``kuvatavNr`` when ``loigeNr`` is missing).
    When no lõige structure exists, fall back to concatenating the raw
    text fragments (``lauseOsa``/``lause``/``tavatekst``) directly so
    single-paragraph laws still emit useful text.
    """
    lõige_blocks: list[str] = []
    for child in _iter_loiked(el):
        loige_nr, _suffix = _loige_numbers(child)
        body = _loige_body_text(child)
        if not body:
            continue
        if loige_nr:
            lõige_blocks.append(f"({loige_nr}) {body}")
        else:
            lõige_blocks.append(body)

    if lõige_blocks:
        return " ".join(lõige_blocks)

    parts: list[str] = []
    for child in el.iter():
        tag = ln(child.tag)
        if tag in ("lauseOsa", "lause", "tavatekst"):
            txt = "".join(child.itertext()).strip()
            txt = re.sub(r"\s+", " ", txt)
            if txt and len(txt) > 3:
                parts.append(txt)
    return " ".join(parts)


def build_subsections(
    par_el: ET.Element,
    provision_id: str,
    *,
    abbrev_prefix: str,
    par_suffix: str,
    paragraph_display: str,
    osa_nr: str | None = None,
    seen_ids: set[str] | None = None,
) -> list[dict]:
    """Build ``estleg:Subsection`` nodes for one paragrahv's ``loige`` children.

    Each lõige becomes one ``estleg:Subsection`` named individual:
      * ``@id``: ``estleg:<PREFIX>[_Osa<N>]_Par_<parSuffix>_Lg_<loigeSuffix>``
        (superscript ``§ 14 lg 2¹`` → ``..._Par_14_Lg_2_1``);
      * ``@type``: ``["estleg:Subsection", "owl:NamedIndividual"]``;
      * ``estleg:subsectionNumber``: the display lõige number as a string;
      * ``estleg:legalText``: the lõige's own text, prefixed with its
        ``(N)`` marker (mirroring the provision-level ``estleg:legalText``);
      * ``estleg:parentProvision``: ``{"@id": provision_id}``;
      * ``rdfs:label``: e.g. ``"§ 14 lg 2"``.

    Subsections whose lõige carries no usable text are skipped (an empty
    lõige is a structural artefact, not a citable unit, and SHACL requires
    ``estleg:legalText``). Returns the nodes in document order. ``seen_ids``,
    when supplied, accumulates the emitted Subsection IRIs and is checked
    for collisions (a duplicate raises ``ValueError`` — the same fail-fast
    contract the paragraph IRIs use).
    """
    if seen_ids is None:
        seen_ids = set()
    # Paragraph display strings from kuvatavNr carry a trailing period and
    # whitespace ("§ 14. "); strip it so the Subsection label reads
    # "§ 14 lg 2" rather than "§ 14.  lg 2".
    par_label_base = None
    if paragraph_display:
        cleaned = re.sub(r"\s+", " ", paragraph_display).strip().rstrip(".").strip()
        par_label_base = cleaned or None
    subsection_nodes: list[dict] = []
    for loige_el in _iter_loiked(par_el):
        display_nr, suffix = _loige_numbers(loige_el)
        body = _loige_body_text(loige_el)
        if not body:
            # Empty lõige (e.g. a repealed-marker placeholder) — no
            # citable text, so no Subsection node.
            continue
        osa_segment = f"_Osa{osa_nr}" if osa_nr else ""
        sub_id = f"estleg:{abbrev_prefix}{osa_segment}_Par_{par_suffix}_Lg_{suffix}"
        if sub_id in seen_ids:
            raise ValueError(
                f"Duplicate subsection IRI {sub_id!r} produced for prefix "
                f"{abbrev_prefix!r}; check loigeNr / ylaIndeks / kuvatavNr "
                f"in the source XML."
            )
        seen_ids.add(sub_id)

        legal_text = f"({display_nr}) {body}" if display_nr else body

        # estleg:legalText / estleg:subsectionNumber are emitted as plain
        # (xsd:string) literals, matching estleg:SubsectionShape's
        # sh:datatype xsd:string and the existing estleg:legalText data in
        # the corpus (the generate_missing_parts.py outputs).
        node: dict = {
            "@id": sub_id,
            "@type": ["estleg:Subsection", "owl:NamedIndividual"],
            "estleg:subsectionNumber": display_nr or suffix,
            "estleg:legalText": legal_text,
            "estleg:parentProvision": {"@id": provision_id},
        }
        if par_label_base and display_nr:
            node["rdfs:label"] = f"{par_label_base} lg {display_nr}"
        subsection_nodes.append(node)
    return subsection_nodes


class SourceListFetchError(RuntimeError):
    """Raised when the law source list cannot be fetched completely."""


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
    page = 1
    rows_seen = 0
    completed = False
    pages_scanned = 0
    cap_hit = False
    # Per-page success/fail/retry counters surfaced in the manifest so a
    # consumer can see which pages were fetched vs. failed. ``get_all_laws``
    # has no retry-with-backoff loop, so ``pagesRetried`` stays 0 here; the
    # key is kept for parity with the regulation/common counters.
    page_stats = {"pagesFetchedOk": 0, "pagesFailed": 0, "pagesRetried": 0}
    while True:
        params: dict[str, str | int] = {
            "leht": page,
            "dokument": "seadus",
            "tekst": "terviktekst",
        }
        if kehtiv:
            params["kehtiv"] = kehtiv
            params["kehtivKehtetus"] = "false"
            params["mitteJoustunud"] = "false"
        try:
            resp = requests.get(
                SEARCH_URL,
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            page_stats["pagesFailed"] += 1
            message = f"API error on page {page}: {e}"
            print(f"  {message}")
            if allow_partial:
                break
            raise SourceListFetchError(message) from e

        page_stats["pagesFetchedOk"] += 1
        aktid = data.get("aktid", [])
        pages_scanned = page
        if not aktid:
            completed = True
            break

        for law in aktid:
            rows_seen += 1
            title = law.get("pealkiri", "").strip()
            if not title:
                continue
            gid = str(law.get("globaalID", ""))
            # Keep the entry with the largest globalID (most recent)
            if title not in all_laws or gid > all_laws[title]["gid"]:
                all_laws[title] = {
                    "gid": gid,
                    "tid": str(law.get("terviktekstID", "")),
                    "url": law.get("url", ""),
                    "kehtivus": law.get("kehtivus", {}),
                    "lyhend": law.get("lyhend", ""),
                }
        page += 1
        if page > page_limit:
            cap_hit = True
            completed = False
            break

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
        "pages": page_stats,
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


def fetch_xml(
    url: str,
    cache_name: str,
    *,
    tid: str | None = None,
) -> ET.Element | None:
    """Fetch law XML, using cache if available.

    Issue #165 fix 9: when ``tid`` is supplied, embed it in the cache
    filename. That way, when Riigi Teataja publishes a new tervikteksti
    edition for the same slug, the next run misses the cache and
    re-fetches automatically. Legacy slug-only cache files are still
    consulted as a fallback so the first upgrade does not trigger a
    full corpus refetch.
    """
    primary_path = (
        DATA_DIR / f"{cache_name}__tid{sanitize_id(tid)}.xml"
        if tid
        else DATA_DIR / f"{cache_name}.xml"
    )
    legacy_path = DATA_DIR / f"{cache_name}.xml"

    for cache_path in (primary_path, legacy_path):
        if cache_path.exists() and cache_path.stat().st_size > 1000:
            try:
                return ET.parse(str(cache_path)).getroot()
            except ET.ParseError:
                pass

    full_url = BASE_URL + url if url.startswith("/") else url
    try:
        resp = requests.get(full_url, timeout=60)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        xml_text = resp.text

        if len(xml_text) < 200:
            return None

        primary_path.write_text(xml_text, encoding="utf-8")
        return ET.fromstring(xml_text)
    except Exception as e:
        print(f"    Fetch error: {e}")
        return None


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
        if owner is None or owner == title:
            self._used_prefixes[candidate] = title
            return candidate

        # 3. collision with a different title — try longer slug prefixes
        for length in (40, 50, 60, 70, 80, len(slug)):
            attempt = sanitize_id(slug[:length])
            attempt_owner = self._used_prefixes.get(attempt)
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


# Backwards-compatible shim. Existing tests reach in via
# ``generate_all_laws._used_prefixes.clear()`` to reset state between
# cases. The real state now lives on ``_default_allocator``; the
# module-level dict mirrors it so legacy ``.clear()`` calls keep
# working without breaking unrelated test files.
class _UsedPrefixesProxy(dict):
    """Mutable dict-view of the default allocator's used_prefixes."""

    def __init__(self) -> None:
        super().__init__()

    def __contains__(self, key: object) -> bool:
        return key in _default_allocator._used_prefixes

    def __getitem__(self, key):
        return _default_allocator._used_prefixes[key]

    def __iter__(self):
        return iter(_default_allocator._used_prefixes)

    def __len__(self) -> int:
        return len(_default_allocator._used_prefixes)

    def get(self, key, default=None):
        return _default_allocator._used_prefixes.get(key, default)

    def items(self):
        return _default_allocator._used_prefixes.items()

    def keys(self):
        return _default_allocator._used_prefixes.keys()

    def values(self):
        return _default_allocator._used_prefixes.values()

    def __setitem__(self, key, value) -> None:
        _default_allocator._used_prefixes[key] = value

    def clear(self) -> None:
        _default_allocator.reset()

    def pop(self, key, *args):
        return _default_allocator._used_prefixes.pop(key, *args)


_used_prefixes: _UsedPrefixesProxy = _UsedPrefixesProxy()


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


def generate_law_jsonld(
    title: str,
    slug: str,
    root: ET.Element,
    abbreviation: str = "",
    rt_url: str = "",
    *,
    kehtiv: str | None = None,
    allocator: PrefixAllocator | None = None,
) -> dict:
    """Generate JSON-LD for a single law.

    ``kehtiv`` (when supplied) is stamped onto the ``owl:Ontology`` node
    as ``estleg:kehtiv`` (xsd:date), mirroring
    ``generate_regulations.build_regulation_jsonld`` so that
    ``missing-only`` re-runs can refresh stale snapshots.
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

    # Check if law has osa (parts) structure
    osad = []
    for el in root.iter():
        if ln(el.tag) == "osa":
            nr = ct(el, "osaNr")
            osa_title = ct(el, "osaPealkiri")
            if nr:
                osad.append({"nr": nr, "title": osa_title or ""})

    ontology_id = f"estleg:{prefix}_Map_2026"
    class_id = f"estleg:LegalProvision_{slug}"

    # Construct Riigi Teataja source URL
    rt_source_url = ""
    if rt_url:
        rt_source_url = BASE_URL + rt_url if rt_url.startswith("/") else rt_url

    ontology_node: dict = {
        "@id": ontology_id,
        "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
        "rdfs:label": f"{title} teemakaardistus",
        "dc:source": title,
        "dcterms:title": title,
        "estleg:contentStatus": "structuredBody",
    }
    if rt_source_url:
        ontology_node["dcterms:source"] = {"@id": rt_source_url}
        ontology_node["owl:sameAs"] = {"@id": rt_source_url}
    kehtiv_value = _kehtiv_node(kehtiv)
    if kehtiv_value is not None:
        ontology_node["estleg:kehtiv"] = kehtiv_value

    graph: list[dict] = [
        ontology_node,
        {
            "@id": class_id,
            "@type": ["owl:Class"],
            "rdfs:label": "Õigusnorm (paragrahv)",
            "rdfs:subClassOf": {"@id": "estleg:LegalProvision"},
        },
    ]

    # Issue #89: Build hierarchy — Chapter and Division nodes
    # Maps paragrahv number -> containing chapter/division IRI for isPartOf links
    par_to_container: dict[int, str] = {}
    clusters = []
    # Issue #166: walk peatykks at any depth, but mapping each paragrahv
    # only to its IMMEDIATE enclosing peatykk/jagu. ``root.iter()`` on
    # its own — combined with ``ch.iter()`` for paragrahvs below — would
    # double-count any paragrahv that lives in a nested block.
    def _peatykks(parent: ET.Element) -> list[ET.Element]:
        out: list[ET.Element] = []
        for c in parent:
            tag = ln(c.tag)
            if tag == "peatykk":
                out.append(c)
            else:
                out.extend(_peatykks(c))
        return out

    for ch in _peatykks(root):
        if ln(ch.tag) == "peatykk":
            ch_nr = ct(ch, "peatykkNr") or ""
            ch_title = ct(ch, "peatykkPealkiri") or ""
            if ch_title:
                # Issue #166: only paragrahvs that DIRECTLY belong to
                # this peatykk (or one of its jagu children).
                ch_pars = _walk_paragraphs_direct(ch)
                ch_par_nrs = []
                for p in ch_pars:
                    nr = ct(p, "paragrahvNr")
                    if nr:
                        try:
                            ch_par_nrs.append(int(re.sub(r"[^\d]", "", nr)))
                        except ValueError:
                            pass

                cluster_id = f"estleg:Cluster_{prefix}_{sanitize_id(ch_nr or ch_title[:20])}"
                chapter_id = f"estleg:Chapter_{prefix}_{sanitize_id(ch_nr or ch_title[:20])}"
                par_range = f"§{min(ch_par_nrs)}–{max(ch_par_nrs)}" if ch_par_nrs else ""
                clusters.append({
                    "id": cluster_id,
                    "label": f"{par_range} {ch_title}".strip(),
                    "par_nrs": set(ch_par_nrs),
                })

                # Existing TopicCluster node (kept for backward compatibility)
                graph.append({
                    "@id": cluster_id,
                    "@type": ["owl:NamedIndividual", "estleg:TopicCluster", "skos:Concept"],
                    "rdfs:label": f"{par_range} {ch_title}".strip(),
                    "skos:prefLabel": f"{par_range} {ch_title}".strip(),
                    "skos:inScheme": {"@id": f"estleg:{prefix}_TopicScheme"},
                })

                # Issue #89: Chapter hierarchy node
                chapter_node: dict = {
                    "@id": chapter_id,
                    "@type": ["owl:NamedIndividual", "estleg:Chapter"],
                    "rdfs:label": f"{ch_nr}. peatükk – {ch_title}".strip(" –"),
                    "estleg:chapterNumber": ch_nr,
                    "owl:sameAs": {"@id": cluster_id},
                }

                # Issue #89: Division nodes inside this chapter
                division_ids = []
                jagu_els = [c for c in ch if ln(c.tag) == "jagu"]
                for jagu_el in jagu_els:
                    j_nr = ct(jagu_el, "jaguNr") or ""
                    j_title = ct(jagu_el, "jaguPealkiri") or ""
                    if j_title or j_nr:
                        div_id = f"estleg:Division_{prefix}_{sanitize_id(ch_nr)}_{sanitize_id(j_nr or j_title[:20])}"
                        division_ids.append(div_id)
                        graph.append({
                            "@id": div_id,
                            "@type": ["owl:NamedIndividual", "estleg:Division"],
                            "rdfs:label": f"{j_nr}. jagu – {j_title}".strip(" –"),
                            "estleg:isPartOf": {"@id": chapter_id},
                        })
                        # Issue #166: only paragraphs that DIRECTLY belong
                        # to this jagu — not those nested in deeper blocks.
                        for jp in _walk_paragraphs_direct(jagu_el):
                            jp_nr = ct(jp, "paragrahvNr")
                            if jp_nr:
                                try:
                                    par_to_container[int(re.sub(r"[^\d]", "", jp_nr))] = div_id
                                except ValueError:
                                    pass

                if division_ids:
                    chapter_node["estleg:hasPart"] = [{"@id": d} for d in division_ids]

                graph.append(chapter_node)

                # Map paragrahvs directly under chapter (not in any jagu) to the chapter
                for p_num in ch_par_nrs:
                    if p_num not in par_to_container:
                        par_to_container[p_num] = chapter_id

    # Issue #87: Create fallback cluster for laws without any peatükk chapters
    if not clusters and paragrahvid:
        _treaty_patterns = ("konventsiooni", "lepingu", "protokolli")
        slug_lower = slug.lower()
        if any(pat in slug_lower for pat in _treaty_patterns):
            fallback_label = "Lepingu sätted"
        else:
            fallback_label = title
        fallback_cluster_id = f"estleg:Cluster_{prefix}_default"
        all_par_nrs = set(par_numbers)
        par_range = f"§{par_min}–{par_max}" if par_numbers else ""
        clusters.append({
            "id": fallback_cluster_id,
            "label": f"{par_range} {fallback_label}".strip(),
            "par_nrs": all_par_nrs,
        })
        graph.append({
            "@id": fallback_cluster_id,
            "@type": ["owl:NamedIndividual", "estleg:TopicCluster", "skos:Concept"],
            "rdfs:label": f"{par_range} {fallback_label}".strip(),
            "skos:prefLabel": f"{par_range} {fallback_label}".strip(),
            "skos:inScheme": {"@id": f"estleg:{prefix}_TopicScheme"},
        })

    # Add provision counts to each cluster
    for cl in clusters:
        count = len(cl["par_nrs"])
        for node in graph:
            if node.get("@id") == cl["id"]:
                node["estleg:provisionCount"] = count
                break

    # Add per-law ConceptScheme node with hasTopConcept references
    if clusters:
        scheme_node = {
            "@id": f"estleg:{prefix}_TopicScheme",
            "@type": ["skos:ConceptScheme"],
            "rdfs:label": f"{title} teemaskeem",
            "skos:hasTopConcept": [{"@id": cl["id"]} for cl in clusters],
        }
        graph.insert(2, scheme_node)

    # Add paragraph nodes
    seen_ids: set[str] = set()
    # Issue #132: every estleg:Subsection IRI emitted in this file —
    # a collision (loigeNr/ylaIndeks/kuvatavNr clash) fails fast.
    seen_subsection_ids: set[str] = set()
    for p in paragrahvid:
        p_nr = ct(p, "paragrahvNr") or "?"
        p_title = ct(p, "paragrahvPealkiri") or ""
        p_display = ct(p, "kuvatavNr") or f"§ {p_nr}"
        text = collect_text(p)
        full_text = collect_full_text(p)

        # Issue #156/#165 fix 2: build the IRI suffix from paragrahvNr
        # plus any superscript index (ylaIndeks="N" or §X¹). This is
        # order-independent so partial regenerations no longer drift
        # IRIs based on insertion order.
        par_suffix = _paragraph_id_suffix(p)
        p_id = f"estleg:{prefix}_Par_{par_suffix}"

        if p_id in seen_ids:
            raise ValueError(
                f"Duplicate paragraph IRI {p_id!r} produced for prefix "
                f"{prefix!r}; check ylaIndeks or kuvatavNr in the source XML."
            )
        seen_ids.add(p_id)

        # Find cluster
        try:
            p_num = int(re.sub(r"[^\d]", "", p_nr))
        except ValueError:
            p_num = 0

        cluster_ref = None
        for cl in clusters:
            if p_num in cl["par_nrs"]:
                cluster_ref = cl["id"]
                break

        # Issue #92: Build label — prefer title, fall back to text excerpt
        if p_title:
            label = f"{p_display} {p_title}"
        elif text:
            excerpt = text[:80].rstrip()
            if len(text) > 80:
                excerpt = excerpt + "..."
            label = f"{p_display} [{excerpt}]"
        else:
            label = p_display

        node: dict = {
            "@id": p_id,
            "@type": ["owl:NamedIndividual", class_id],
            "estleg:paragrahv": p_display,
            "rdfs:label": label,
            "estleg:sourceAct": title,
        }

        if text:
            node["estleg:summary"] = text

        # Issue #88: Add full legal text without truncation
        if full_text:
            node["estleg:legalText"] = full_text

        if cluster_ref:
            node["estleg:requestedCluster"] = {"@id": cluster_ref}

        # Issue #89: Link provision to containing chapter or division
        container_ref = par_to_container.get(p_num)
        if container_ref:
            node["estleg:isPartOf"] = {"@id": container_ref}

        # Issue #132: emit estleg:Subsection nodes for each lõige. The
        # provision keeps its full concatenated estleg:legalText (the sum
        # of the subsections) for backward compatibility; the per-lõige
        # text additionally lives on Subsection nodes — the level
        # citations actually reference ("TsÜS § 14 lg 2").
        subsection_nodes = build_subsections(
            p,
            p_id,
            abbrev_prefix=prefix,
            par_suffix=par_suffix,
            paragraph_display=p_display,
            seen_ids=seen_subsection_ids,
        )
        if subsection_nodes:
            node["estleg:hasSubsection"] = [
                {"@id": s["@id"]} for s in subsection_nodes
            ]

        graph.append(node)
        graph.extend(subsection_nodes)

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
    allocator: PrefixAllocator | None = None,
) -> dict:
    """Generate an act-level representation for laws without paragraph nodes.

    ``kehtiv`` (when supplied) is stamped as ``estleg:kehtiv`` (xsd:date)
    so missing-only re-runs can refresh stale stubs the same way they
    refresh structured law files.
    """
    prefix = _unique_prefix(abbreviation, slug, title, allocator=allocator)
    # Issue #165 fix 5: rt_url may be None (or empty) for laws missing a
    # Riigi Teataja link, so guard against ``None.startswith``.
    rt_source_url = ""
    if rt_url:
        rt_source_url = BASE_URL + rt_url if rt_url.startswith("/") else rt_url
    ontology_node: dict = {
        "@id": f"estleg:{prefix}_Map_2026",
        "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
        "rdfs:label": f"{title} teemakaardistus",
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
        ontology_node["owl:sameAs"] = {"@id": rt_source_url}
    kehtiv_value = _kehtiv_node(kehtiv)
    if kehtiv_value is not None:
        ontology_node["estleg:kehtiv"] = kehtiv_value
    return {"@context": CONTEXT, "@graph": [ontology_node]}


def generate_multipart_law(
    title: str,
    slug: str,
    root: ET.Element,
    abbreviation: str = "",
    rt_url: str = "",
    *,
    kehtiv: str | None = None,
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

        ontology_id = f"estleg:{prefix}_Osa{osa_nr}_{par_min}_{par_max}"
        class_id = f"estleg:LegalProvision_{slug}_osa{osa_nr}"

        # Issue #89: Mark file-level ontology node with estleg:Part type
        osa_ontology_node: dict = {
            "@id": ontology_id,
            "@type": ["owl:Ontology", "estleg:Act", "estleg:Law", "estleg:Part"],
            "rdfs:label": f"{title} Osa {osa_nr} ({osa_title}) §{par_min}–{par_max} kaardistus",
            "dc:source": title,
            "dcterms:title": title,
            "estleg:contentStatus": "structuredBody",
        }
        if rt_source_url:
            osa_ontology_node["dcterms:source"] = {"@id": rt_source_url}
            osa_ontology_node["owl:sameAs"] = {"@id": rt_source_url}
        if kehtiv_value is not None:
            osa_ontology_node["estleg:kehtiv"] = kehtiv_value

        graph: list[dict] = [
            osa_ontology_node,
            {
                "@id": class_id,
                "@type": ["owl:Class"],
                "rdfs:label": "Õigusnorm (paragrahv)",
                "rdfs:subClassOf": {"@id": "estleg:LegalProvision"},
            },
        ]

        # Issue #89: Build hierarchy — Chapter and Division nodes within this osa
        par_to_container: dict[int, str] = {}
        clusters = []
        part_concept_id = f"estleg:Cluster_{prefix}_{osa_nr}_Part"
        scheme_id = f"estleg:{prefix}_Osa{osa_nr}_TopicScheme"
        # Issue #166: walk peatykk children directly so paragrahvs that
        # belong to a deeper nested chapter inside the same osa do not
        # double-count up to the outer chapter.
        def _osa_peatykks(parent: ET.Element) -> list[ET.Element]:
            out: list[ET.Element] = []
            for c in parent:
                tag = ln(c.tag)
                if tag == "peatykk":
                    out.append(c)
                elif tag == "osa":
                    continue
                else:
                    out.extend(_osa_peatykks(c))
            return out

        for ch in _osa_peatykks(osa_el):
            if ln(ch.tag) == "peatykk":
                ch_nr = ct(ch, "peatykkNr") or ""
                ch_title = ct(ch, "peatykkPealkiri") or ""
                if ch_title:
                    # Issue #166: only paragrahvs that are immediate
                    # descendants of THIS chapter (or its jagu children).
                    ch_pars = _walk_paragraphs_direct(ch)
                    ch_par_nrs = set()
                    for p in ch_pars:
                        nr = ct(p, "paragrahvNr")
                        if nr:
                            try:
                                ch_par_nrs.add(int(re.sub(r"[^\d]", "", nr)))
                            except ValueError:
                                pass
                    cluster_id = f"estleg:Cluster_{prefix}_{osa_nr}_{sanitize_id(ch_nr or ch_title[:20])}"
                    chapter_id = f"estleg:Chapter_{prefix}_{osa_nr}_{sanitize_id(ch_nr or ch_title[:20])}"
                    par_range = f"§{min(ch_par_nrs)}–{max(ch_par_nrs)}" if ch_par_nrs else ""
                    clusters.append({"id": cluster_id, "label": f"{par_range} {ch_title}".strip(), "par_nrs": ch_par_nrs})

                    # Existing TopicCluster node (kept for backward compatibility)
                    graph.append({
                        "@id": cluster_id,
                        "@type": ["owl:NamedIndividual", "estleg:TopicCluster", "skos:Concept"],
                        "rdfs:label": f"{par_range} {ch_title}".strip(),
                        "skos:prefLabel": f"{par_range} {ch_title}".strip(),
                        "skos:inScheme": {"@id": scheme_id},
                        "skos:broader": {"@id": part_concept_id},
                    })

                    # Issue #89: Chapter hierarchy node
                    chapter_node: dict = {
                        "@id": chapter_id,
                        "@type": ["owl:NamedIndividual", "estleg:Chapter"],
                        "rdfs:label": f"{ch_nr}. peatükk – {ch_title}".strip(" –"),
                        "estleg:chapterNumber": ch_nr,
                        "owl:sameAs": {"@id": cluster_id},
                    }

                    # Issue #89: Division nodes inside this chapter
                    division_ids = []
                    jagu_els = [c for c in ch if ln(c.tag) == "jagu"]
                    for jagu_el in jagu_els:
                        j_nr = ct(jagu_el, "jaguNr") or ""
                        j_title = ct(jagu_el, "jaguPealkiri") or ""
                        if j_title or j_nr:
                            div_id = f"estleg:Division_{prefix}_{osa_nr}_{sanitize_id(ch_nr)}_{sanitize_id(j_nr or j_title[:20])}"
                            division_ids.append(div_id)
                            graph.append({
                                "@id": div_id,
                                "@type": ["owl:NamedIndividual", "estleg:Division"],
                                "rdfs:label": f"{j_nr}. jagu – {j_title}".strip(" –"),
                                "estleg:isPartOf": {"@id": chapter_id},
                            })
                            # Issue #166: only paragraphs that DIRECTLY
                            # belong to this jagu, not those inside any
                            # nested peatykk/jagu/osa blocks.
                            for jp in _walk_paragraphs_direct(jagu_el):
                                jp_nr = ct(jp, "paragrahvNr")
                                if jp_nr:
                                    try:
                                        par_to_container[int(re.sub(r"[^\d]", "", jp_nr))] = div_id
                                    except ValueError:
                                        pass

                    if division_ids:
                        chapter_node["estleg:hasPart"] = [{"@id": d} for d in division_ids]

                    graph.append(chapter_node)

                    # Map paragrahvs directly under chapter (not in any jagu) to the chapter
                    for p_num in ch_par_nrs:
                        if p_num not in par_to_container:
                            par_to_container[p_num] = chapter_id

        # Issue #87: Fallback cluster for osa without peatükk chapters
        if not clusters and paragrahvid:
            fallback_label = f"{title} Osa {osa_nr}"
            if osa_title:
                fallback_label = f"{title} Osa {osa_nr} ({osa_title})"
            fallback_cluster_id = f"estleg:Cluster_{prefix}_{osa_nr}_default"
            all_par_nrs = set(par_numbers)
            osa_par_range = f"§{par_min}–{par_max}" if par_numbers else ""
            clusters.append({
                "id": fallback_cluster_id,
                "label": f"{osa_par_range} {fallback_label}".strip(),
                "par_nrs": all_par_nrs,
            })
            graph.append({
                "@id": fallback_cluster_id,
                "@type": ["owl:NamedIndividual", "estleg:TopicCluster", "skos:Concept"],
                "rdfs:label": f"{osa_par_range} {fallback_label}".strip(),
                "skos:prefLabel": f"{osa_par_range} {fallback_label}".strip(),
                "skos:inScheme": {"@id": scheme_id},
            })

        # Add provision counts to each cluster
        for cl in clusters:
            count = len(cl["par_nrs"])
            for node in graph:
                if node.get("@id") == cl["id"]:
                    node["estleg:provisionCount"] = count
                    break

        # Add per-osa ConceptScheme and part-level concept nodes
        if clusters:
            part_label = f"Osa {osa_nr}"
            if osa_title:
                part_label = f"Osa {osa_nr} ({osa_title})"

            # Determine top concepts: part-level concept if we have chapter clusters,
            # otherwise the fallback cluster is the direct top concept
            has_chapter_clusters = any(
                node.get("skos:broader") for node in graph
                if isinstance(node.get("skos:broader"), dict)
            )

            if has_chapter_clusters:
                # Insert part-level grouping concept
                part_concept_node = {
                    "@id": part_concept_id,
                    "@type": ["owl:NamedIndividual", "skos:Concept"],
                    "rdfs:label": part_label,
                    "skos:prefLabel": part_label,
                    "skos:inScheme": {"@id": scheme_id},
                    "skos:narrower": [{"@id": cl["id"]} for cl in clusters],
                }
                graph.insert(2, part_concept_node)
                top_concepts = [{"@id": part_concept_id}]
            else:
                # Fallback cluster is the direct top concept (no part-level needed)
                top_concepts = [{"@id": cl["id"]} for cl in clusters]

            scheme_node = {
                "@id": scheme_id,
                "@type": ["skos:ConceptScheme"],
                "rdfs:label": f"{title} {part_label} teemaskeem",
                "skos:hasTopConcept": top_concepts,
            }
            graph.insert(2, scheme_node)

        seen_ids: set[str] = set()
        # Issue #132: Subsection IRIs emitted within this osa file.
        seen_subsection_ids: set[str] = set()
        for p in paragrahvid:
            p_nr = ct(p, "paragrahvNr") or "?"
            p_title = ct(p, "paragrahvPealkiri") or ""
            p_display = ct(p, "kuvatavNr") or f"§ {p_nr}"
            text = collect_text(p)
            full_text = collect_full_text(p)
            # Issue #156/#165 fix 2: superscript-aware paragraph IRI suffix.
            par_suffix = _paragraph_id_suffix(p)
            p_id = f"estleg:{prefix}_Osa{osa_nr}_Par_{par_suffix}"
            if p_id in seen_ids:
                raise ValueError(
                    f"Duplicate paragraph IRI {p_id!r} produced for prefix "
                    f"{prefix!r} osa {osa_nr!r}; check ylaIndeks or kuvatavNr "
                    f"in the source XML."
                )
            seen_ids.add(p_id)

            try:
                p_num = int(re.sub(r"[^\d]", "", p_nr))
            except ValueError:
                p_num = 0

            cluster_ref = None
            for cl in clusters:
                if p_num in cl["par_nrs"]:
                    cluster_ref = cl["id"]
                    break

            # Issue #92: Build label — prefer title, fall back to text excerpt
            if p_title:
                label = f"{p_display} {p_title}"
            elif text:
                excerpt = text[:80].rstrip()
                if len(text) > 80:
                    excerpt = excerpt + "..."
                label = f"{p_display} [{excerpt}]"
            else:
                label = p_display

            node: dict = {
                "@id": p_id,
                "@type": ["owl:NamedIndividual", class_id],
                "estleg:paragrahv": p_display,
                "rdfs:label": label,
                "estleg:sourceAct": title,
            }
            if text:
                node["estleg:summary"] = text
            # Issue #88: Add full legal text without truncation
            if full_text:
                node["estleg:legalText"] = full_text
            if cluster_ref:
                node["estleg:requestedCluster"] = {"@id": cluster_ref}
            # Issue #89: Link provision to containing chapter or division
            container_ref = par_to_container.get(p_num)
            if container_ref:
                node["estleg:isPartOf"] = {"@id": container_ref}
            # Issue #132: per-lõige estleg:Subsection nodes (osa-scoped IRIs).
            subsection_nodes = build_subsections(
                p,
                p_id,
                abbrev_prefix=prefix,
                par_suffix=par_suffix,
                paragraph_display=p_display,
                osa_nr=str(osa_nr),
                seen_ids=seen_subsection_ids,
            )
            if subsection_nodes:
                node["estleg:hasSubsection"] = [
                    {"@id": s["@id"]} for s in subsection_nodes
                ]
            graph.append(node)
            graph.extend(subsection_nodes)

        filename = f"{slug}_osa{osa_nr}_peep.json"
        results.append((filename, {"@context": CONTEXT, "@graph": graph}))

    return results


def save_json(filepath: Path, doc: dict):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _ontology_node(doc: dict) -> dict | None:
    """Return the first ``owl:Ontology`` node in a doc's ``@graph``."""
    for node in doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "owl:Ontology" in types:
            return node
    return None


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


def existing_doc_matches(path: Path, doc: dict) -> bool:
    """Return True iff ``path`` already contains exactly ``doc``."""
    existing = _load_existing_doc(path)
    if existing is None:
        return False
    return existing == doc


def merge_existing_enrichments(new_doc: dict, existing_path: Path) -> dict:
    """Additive-merge: copy enrichment fields from an existing law file onto
    matching nodes in ``new_doc``. Generator-emitted fields always win — only
    keys MISSING from the new node are pulled across from the existing one.

    The generator owns structural fields (``@type``, ``rdfs:label``,
    ``estleg:paragrahv``, ``estleg:summary``, ``estleg:legalText``,
    ``estleg:sourceAct``, ``estleg:contentStatus``, ``estleg:kehtiv``,
    ``estleg:hasSubsection`` …). Everything else on an act or provision node
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


def _law_file_base_slug(name: str) -> str:
    """Return the law slug for a peep filename.

    Strips the trailing ``_peep.json`` and any ``_osaN`` part suffix so a
    multipart law's per-osa files all resolve to the parent slug:
      * ``advokatuuriseadus_peep.json`` -> ``advokatuuriseadus``
      * ``volaigusseadus_osa2_peep.json`` -> ``volaigusseadus``
    """
    stem = name[: -len("_peep.json")] if name.endswith("_peep.json") else name
    return re.sub(r"_osa\d+$", "", stem)


def source_removed_law_files(
    krr_dir: Path,
    current_titles: set[str],
    *,
    multipart_titles: set[str] | None = None,
) -> list[str]:
    """List root ``*_peep.json`` files whose law is no longer in the source.

    Identity is slug-based (``slugify(title)``) rather than ``dc:source``
    text, so the check is robust to historical drift in the ``dc:source``
    literal format. A file is reported when the slug derived from its
    filename (with any ``_osaN`` suffix stripped) does not match the
    slug of any title in ``current_titles`` (the titles the live search
    returned this run). Reported, NOT deleted — mirrors
    ``generate_regulations.source_removed_files``.
    """
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


def main():
    args = parse_args()
    mode = "refresh" if args.refresh else "force" if args.force else "missing-only"

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
        all_laws = dict(list(sorted(all_laws.items()))[:args.limit])
        source_manifest["limited"] = True
        source_manifest["limit"] = args.limit
    print(f"  Found {len(all_laws)} unique law titles")

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
    for title, info in sorted(all_laws.items()):
        slug = slugify(title)
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
    })
    generated = 0
    failed = 0
    skipped = 0  # stub acts (no structured body)
    # Per-act outcome ledger keyed by title: (status, reason). Folded into
    # the manifest's outputsAll/outputsGenerated entries (#119).
    act_status: dict[str, tuple[str, str | None]] = {}

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
            continue

        # Count paragraphs
        par_count = sum(1 for el in root.iter() if ln(el.tag) == "paragrahv")
        if par_count == 0:
            doc = generate_law_stub_jsonld(
                title, slug, root, abbreviation, rt_url=url, kehtiv=args.kehtiv
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
            skipped += 1
            print(f"    Saved stub: {filename} (no structured body, {status})")
            act_status[title] = ("stub", "no structured paragraph nodes in source XML")
            continue

        # Check if multi-part
        osa_count = sum(1 for el in root.iter() if ln(el.tag) == "osa")

        if title in MULTIPART_LAWS and osa_count > 1:
            # Generate separate files per osa
            results = generate_multipart_law(
                title, slug, root, abbreviation, rt_url=url, kehtiv=args.kehtiv
            )
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
                run_counts[status] += 1
                if status != "existingSkipped":
                    generated += 1
                print(f"    Saved: {filename} ({len(doc['@graph'])} nodes, {status})")
            act_status[title] = ("full", None)
        else:
            # Single file
            doc = generate_law_jsonld(
                title, slug, root, abbreviation, rt_url=url, kehtiv=args.kehtiv
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

        # Rate limit - be polite to Riigi Teataja
        time.sleep(0.3)

    # Source-removed detection (#108): existing peep files whose law title
    # no longer appears in the current source list. Reported, not deleted.
    source_removed = source_removed_law_files(
        KRR_DIR, set(all_laws), multipart_titles=MULTIPART_LAWS
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
        outputs_all.append(_manifest_entry(title, info, status=status, reason=reason))
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
        "generated": datetime.now(timezone.utc).isoformat(),
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
            "refreshedStale": run_counts["refreshedStale"],
            "unchanged": run_counts["unchanged"],
            "refreshed": run_counts["refreshed"],
            "forceRewritten": run_counts["forceRewritten"],
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
