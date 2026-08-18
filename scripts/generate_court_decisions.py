#!/usr/bin/env python3
"""
Fetch Supreme Court (Riigikohus) decisions from RIK and generate JSON-LD ontology files.

Data source: https://rikos.rik.ee (Supreme Court decisions search)
API: GET https://rikos.rik.ee/?aasta=YYYY&pageSize=100&lk=N

Generates:
  - krr_outputs/riigikohus/riigikohus_YYYY_peep.json  (per-year files)
  - krr_outputs/riigikohus/riigikohus_schema.json      (schema definitions)
  - krr_outputs/riigikohus/RIIGIKOHUS_INDEX.json        (registry)

Full decision text (issue #135)
-------------------------------
By default this script ingests only the search-result table (date,
case number, decision type, a long abstract used as ``estleg:summary``,
and the RIK object id). Each decision additionally has a detail page at
``https://rikos.rik.ee/?asjaNr=<case_nr>`` that serves the *full*
decision text (``RIIGIKOHUS``, the operative part, ``ASJAOLUD``,
reasoning, etc.) — for both the modern ``WordSection1`` layout and the
older bare-``<P>`` layout.

Run with ``--fetch-full-text`` to enrich the existing
``riigikohus_*_peep.json`` files in place: for the first
``--full-text-limit`` decisions (deterministic order: year descending,
then file order; ``0`` means *all* — a multi-hour ~12k-page scrape) it
fetches the detail page and adds a plain-string ``estleg:legalText``
literal to that decision node. Fetches are cached on disk under
``krr_outputs/.cache/court_decisions/<safe_case_nr>_<digest>.txt`` so reruns
are near-instant and the full run is resumable. Decisions not fetched in a
given run keep their existing shape — ``estleg:legalText`` is optional.
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_mod
import json
import logging
import math
import re
import sys
import time
import urllib.parse
from datetime import datetime
from functools import partial
from pathlib import Path

import requests  # noqa: F401  -- tests monkeypatch ``requests.get``

from estleg_common import (
    CONTEXT,
    BUILD_EVALUATION_DATE,
    FULLNAME_GENITIVE,
    KNOWN_ABBREVIATIONS,
    allowed_get,
    save_json,
    sanitize_id as _shared_sanitize_id,
)

try:  # Optional dep — falls back to regex parser when unavailable.
    from bs4 import BeautifulSoup  # type: ignore[import-not-found]

    _BS4_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when bs4 missing
    BeautifulSoup = None  # type: ignore[assignment]
    _BS4_AVAILABLE = False

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
RK_DIR = KRR_DIR / "riigikohus"
RK_DIR.mkdir(parents=True, exist_ok=True)

NS = "https://w3id.org/estleg/"

SEARCH_URL = "https://rikos.rik.ee/"
PAGE_SIZE = 100
RATE_DELAY = 0.3  # seconds between requests

# --- Full decision-text fetch (issue #135) ---------------------------------
# Each decision's full text lives on a detail page reachable as
# ``SEARCH_URL?asjaNr=<case_nr>``. The page is plain HTML; the modern
# layout wraps the text in ``<div class=WordSection1>`` while older
# decisions emit bare ``<P>`` paragraphs straight under ``<body>``. We
# extract every layout the same way: take the ``<body>``, drop scripts /
# styles, strip the remaining tags, unescape entities, and collapse
# whitespace (the ``print_nupp`` and ``custom-data-container`` wrapper
# divs carry no text, so they vanish naturally).
DETAIL_RATE_DELAY = RATE_DELAY  # seconds between detail-page fetches
DETAIL_TIMEOUT = 30  # seconds per detail-page request
DETAIL_RETRIES = 3  # attempts for a transient (5xx / network) failure
DETAIL_BACKOFF = 2.0  # base seconds for exponential backoff between retries
# Polite, identifiable UA — the bare ``python-requests`` default is the
# kind of string sites rate-limit first.
DETAIL_USER_AGENT = (
    "law-ontology/1.0 (+https://github.com/henrikaavik/law-ontology; "
    "Supreme Court decision-text ingestion, issue #135)"
)
# On-disk cache for fetched detail pages — one ``.txt`` per case number.
# Mirrors the per-CELEX cache pattern in ``generate_harmonisation_links.py``;
# the directory is gitignored and lives outside any path the corpus
# validators scan (they only look at ``riigikohus/*_peep.json`` and
# ``**/*.json{,ld}``, never ``.cache/`` or ``.txt``).
COURT_TEXT_CACHE_DIR = KRR_DIR / ".cache" / "court_decisions"
COURT_TEXT_CACHE_TTL_DAYS = 365  # decisions are immutable; refresh yearly
_CACHE_DIGEST_SUFFIX_RE = re.compile(r"_[0-9a-f]{10}$")
# Below this many characters the "decision text" is almost certainly the
# search form or an error stub rather than a real decision body — treat
# as a failed fetch and do not cache it.
MIN_DECISION_TEXT_LEN = 40
# Guard against successful HTTP responses whose body is not usable
# Estonian text (for example mojibake or binary-ish payloads decoded as
# HTML). Ratio is measured over non-whitespace characters after tag
# stripping; real decisions are well above this floor.
MIN_DECISION_TEXT_VALID_CHAR_RATIO = 0.30
# Default cap on how many decisions to fetch full text for in one
# ``--fetch-full-text`` run. ``0`` (or ``--full-text-limit 0``) means
# *all* ~12k decisions — the multi-hour run; the cache makes it resumable.
DEFAULT_FULL_TEXT_LIMIT = 50


# Case type classification based on case number prefix
CASE_TYPE_MAP = {
    "1": ("Criminal", "Kriminaalasi", "Criminal Case"),
    "2": ("Civil", "Tsiviilasi", "Civil Case"),
    "3": ("Administrative", "Haldusasi", "Administrative Case"),
    "4": ("Misdemeanor", "Väärteoasi", "Misdemeanor Case"),
    "5": ("ConstitutionalReview", "Põhiseaduslikkuse järelevalve", "Constitutional Review"),
    "Other": ("Other", "Muu", "Other Case"),
}

# Decision type mapping
DECISION_TYPES = {
    "Kohtuotsus": ("Judgment", "Kohtuotsus"),
    "Kohtumäärus": ("Ruling", "Kohtumäärus"),
    "Kohtu resolutsioon": ("Resolution", "Kohtu resolutsioon"),
    "Määrus": ("OrderRuling", "Määrus"),
}


# Court-decision IDs: slashes/dashes become ``_``, leftover non-ASCII
# becomes ``_``, cap at 80 (#449).
sanitize_id = partial(
    _shared_sanitize_id,
    max_len=80,
    replace_dash=True,
    replace_slash=True,
    canonicalize_ranges=False,
    unknown_char="_",
)


# ``\s`` does not match the byte-order mark (U+FEFF); strip it
# explicitly before matching so legacy fetched feeds with a stray
# BOM don't fall through to the ``Other`` bucket.
_CASE_PREFIX_RE = re.compile(r"^[\s﻿]*(\d)")

# Old-format Riigikohus case numbers (1996–2017) look like
# ``3-<chamber>-<...>-<yy>``: the leading ``3`` is the *court* (the
# Supreme Court), NOT the case type. The case type is encoded in the
# SECOND segment (the chamber digit): 1=Criminal, 2=Civil,
# 3=Administrative, 4=ConstitutionalReview. The single-digit second
# segment (``3-2-…``) is what distinguishes this legacy layout from the
# modern one introduced in 2018 (``3-21-…``, where ``21`` is the filing
# year, not a chamber). We therefore require the chamber digit to be
# followed by ``-`` so a two-digit modern year (``3-21-…``) does NOT
# match and falls through to the first-digit path below (issue #342).
_OLD_FORMAT_CASE_RE = re.compile(r"^3-(\d)-")
# The chamber digit maps to a *different* scale than the modern first
# digit: chambers run 1=Criminal, 2=Civil, 3=Administrative,
# 4=ConstitutionalReview (there is no chamber 5, and chamber 4 is
# constitutional review, whereas the modern first-digit ``4`` is a
# misdemeanor). Values here are CASE_TYPE_MAP keys, so the resolved type
# id/labels stay in sync with the schema individuals.
_OLD_FORMAT_CHAMBER_TO_MAP_KEY: dict[str, str] = {
    "1": "1",  # Criminal
    "2": "2",  # Civil
    "3": "3",  # Administrative
    "4": "5",  # ConstitutionalReview
}

# Legacy Roman-numeral case numbers (1993–1996) look like
# ``III-<chamber>/<sub>-<seq>/<yy>`` (e.g. ``III-2/3-9/93``): the leading
# ``III`` is the Supreme Court, and the FIRST segment after it is the
# deciding-chamber digit. Only chambers ``1`` (Criminal) and ``2`` (Civil)
# are read from the number here because they are unambiguous; chambers
# ``3``/``4`` mix administrative, civil and constitutional matters in the
# legacy numbering, so they are left to fall through to ``Other`` and are
# recovered from the decision text by ``rederive_riigikohus_case_types.py``
# (#579). At generation time the chamber token in the text is not yet
# available (``estleg:legalText`` is added by a later enrichment pass), so
# the case number is the only signal we can use without a network fetch.
_ROMAN_LEGACY_CASE_RE = re.compile(r"^III-(\d)/")
_ROMAN_LEGACY_CHAMBER_TO_MAP_KEY: dict[str, str] = {
    "1": "1",  # Criminal
    "2": "2",  # Civil
}


def classify_case(case_nr: str) -> tuple[str, str, str]:
    """Classify case type from a Riigikohus case number.

    Two numbering layouts coexist:

    * **Old format** (1996–2017) ``3-<chamber>-<...>-<yy>``: the leading
      ``3`` is the Supreme Court itself, so the case type lives in the
      SECOND segment (the chamber digit) — ``3-1-*``=Criminal,
      ``3-2-*``=Civil, ``3-3-*``=Administrative,
      ``3-4-*``=ConstitutionalReview. Reading the first digit here would
      mis-stamp every legacy decision as ``Administrative`` (issue #342).
    * **New format** (2018+) e.g. ``1-17-…`` / ``3-21-…``: the case type
      is the FIRST digit (1=criminal, 2=civil, …) and the rest is the
      filing year and sequence.
    * **Legacy Roman format** (1993–1996) ``III-<chamber>/<sub>-<seq>/<yy>``:
      the leading ``III`` is the Supreme Court and the first segment after
      it is the chamber. Only the unambiguous chambers ``1`` (Criminal) and
      ``2`` (Civil) are resolved here; ``3``/``4`` fall through to ``Other``
      and are recovered from the decision text post-hoc (#579).

    Real-world inputs sometimes carry leading whitespace, BOM, or other
    invisible characters, so the first-digit path skips leading
    whitespace and picks the FIRST decimal digit. Inputs with no leading
    digit (or empty inputs) are logged at WARNING and classified as
    ``Other`` rather than mis-mapped silently.
    """
    if not case_nr:
        logger.warning("classify_case: empty case_nr")
        return CASE_TYPE_MAP["Other"]
    # Legacy Roman ``III-<chamber>/…`` numbers carry the case type in the
    # first segment after ``III``; resolve the unambiguous chambers before
    # the first-digit path (which would read the leading Roman ``I`` as a
    # non-digit and fall through to Other).
    roman_match = _ROMAN_LEGACY_CASE_RE.match(case_nr)
    if roman_match is not None:
        map_key = _ROMAN_LEGACY_CHAMBER_TO_MAP_KEY.get(roman_match.group(1))
        if map_key is not None:
            return CASE_TYPE_MAP[map_key]
        # Chambers 3/4 are ambiguous in the legacy numbering — leave them as
        # Other for the text-based re-derivation rather than guessing.
        return CASE_TYPE_MAP["Other"]
    # Old-format ``3-<chamber>-…`` numbers carry the case type in the
    # chamber digit, not the leading court digit — resolve those first.
    old_match = _OLD_FORMAT_CASE_RE.match(case_nr)
    if old_match is not None:
        map_key = _OLD_FORMAT_CHAMBER_TO_MAP_KEY.get(old_match.group(1))
        if map_key is not None:
            return CASE_TYPE_MAP[map_key]
        # An unrecognised chamber digit (e.g. ``3-9-…``) is a data
        # anomaly; fall through to the first-digit path rather than
        # silently dropping it.
    match = _CASE_PREFIX_RE.match(case_nr)
    if match is None:
        logger.warning(
            "classify_case: no leading digit in case_nr=%r — classifying as Other",
            case_nr,
        )
        return CASE_TYPE_MAP["Other"]
    return CASE_TYPE_MAP.get(match.group(1), CASE_TYPE_MAP["Other"])


# Modern-format filing year: ``<type>-<yy>-<seq>…`` (e.g. ``3-21-2176/52``
# → ``21`` → 2021). Anchored at the start so the two-digit token can ONLY
# be the second segment — the unanchored ``\d-(\d{2})-`` previously also
# matched the *third* segment of old-format numbers (``3-2-2-15-96`` →
# ``15`` → bogus year 2015). Old-format ``3-<chamber>-…`` numbers have a
# single-digit second segment and therefore do not match here; they fall
# back to the decision's validated ``date`` field (issue #341).
_MODERN_YEAR_RE = re.compile(r"^\d-(\d{2})-")


def _decision_year_key(dec: dict) -> str:
    """Derive the calendar-year bucket for a decision dict.

    Prefers the modern case-number layout (``<type>-<yy>-…``), where the
    two-digit second segment is the filing year and maps to ``20yy``.
    When the case number is old-format (single-digit chamber segment) or
    otherwise unparseable, falls back to the validated ``date`` field
    (``DD.MM.YYYY``) and uses its 4-digit calendar year. Returns
    ``"unknown"`` only when neither source yields a year — never a blind
    ``2000+yy`` guess that lands pre-2001 decisions in future buckets.
    """
    case_nr = dec.get("case_nr", "") or ""
    match = _MODERN_YEAR_RE.match(case_nr)
    if match:
        # Two-digit modern year: ``yy`` → ``20yy`` (the modern numbering
        # scheme started in 2018, so there is no 1900s ambiguity).
        return str(2000 + int(match.group(1)))
    date_str = (dec.get("date") or "").strip()
    if date_str:
        try:
            parsed = datetime.strptime(date_str, "%d.%m.%Y")
        except ValueError:
            pass
        else:
            return str(parsed.year)
    return "unknown"


# Known law abbreviations — sourced from the resolver's authoritative
# ``KNOWN_ABBREVIATIONS`` table (#606) instead of a hand-maintained local
# copy that had drifted out of sync (it was missing the EhS/EhSE split, so
# the court extractor under-detected law references). Anchored matches must
# lowercase before deduplication so "KarS" / "kars" / "KARS" collapse into
# the canonical "KarS" form rather than producing duplicate entries.
_KNOWN_LAW_ABBREVS: tuple[str, ...] = tuple(KNOWN_ABBREVIATIONS.keys())
_LAW_ABBREV_LOOKUP: dict[str, str] = {
    abbrev.casefold(): abbrev for abbrev in _KNOWN_LAW_ABBREVS
}

# Word-boundary anchored: prevents ``\w+seaduse`` from absorbing trailing
# characters into a longer match (greediness). Each abbreviation is
# ``re.escape``-d and the alternation is ordered LONGEST-FIRST so a longer
# abbreviation wins over a prefix of it ("EhSE" before "EhS"), mirroring the
# proven citation pattern in ``extract_cross_references.py``.
_KNOWN_LAW_PATTERN = re.compile(
    r"\b("
    + "|".join(re.escape(a) for a in sorted(_KNOWN_LAW_ABBREVS, key=len, reverse=True))
    + r")\b\s*§\s*[\d\-]+",
    re.IGNORECASE,
)
_GENITIVE_LAW_PATTERN = re.compile(
    r"\b(\w+seadus(?:e|tiku?)?)\b\s*§\s*[\d\-]+\s*(?:lg\s*[\d]+)?",
    re.IGNORECASE,
)


def detect_referenced_laws(summary: str) -> list[str]:
    """Extract law references (KarS §, VÕS §, etc.) from decision summary.

    Returns canonicalised law identifiers in first-occurrence order.
    Known abbreviations resolve through ``_LAW_ABBREV_LOOKUP`` so
    case variants collapse (e.g. ``Kars`` and ``KarS`` both become
    ``KarS``). Genitive long-form references (``liiklusseaduse``,
    ``perekonnaseaduse``) resolve through ``FULLNAME_GENITIVE`` to the
    canonical abbreviation the IRI resolver uses (``liiklusseaduse`` →
    ``LS``); a genitive with no table entry would only become a dead
    token that joins to no ``interpretsLaw`` IRI, so it is dropped
    rather than emitted verbatim (#596).
    """
    if not summary:
        return []
    refs: list[str] = []
    seen: set[str] = set()

    for match in _KNOWN_LAW_PATTERN.finditer(summary):
        raw = match.group(1)
        canonical = _LAW_ABBREV_LOOKUP.get(raw.casefold(), raw)
        if canonical not in seen:
            seen.add(canonical)
            refs.append(canonical)

    for match in _GENITIVE_LAW_PATTERN.finditer(summary):
        # Resolve the genitive to its canonical abbreviation; drop forms
        # FULLNAME_GENITIVE can't map (they never resolve to a law IRI).
        canonical = FULLNAME_GENITIVE.get(match.group(1).lower())
        if canonical and canonical not in seen:
            seen.add(canonical)
            refs.append(canonical)

    return refs


# Number of <td> columns we expect on every search-result row. The
# Riigikohus search result table follows: date | case_nr (with link) |
# description | object_id. Any row deviating from this shape is a
# parser-mismatch signal and is skipped with a logger warning.
_EXPECTED_COLUMN_COUNT = 4


def _extract_row_fields(tds_text: list[str], link: str = "") -> dict | None:
    """Build a decision dict from already-extracted text columns.

    Shared between the BeautifulSoup and regex parsers so behaviour
    stays identical regardless of code path. Returns ``None`` for
    rows missing required fields.
    """
    if len(tds_text) < _EXPECTED_COLUMN_COUNT:
        return None
    date_str = tds_text[0].strip()
    case_nr = tds_text[1].strip()
    raw_desc = re.sub(r"\s+", " ", tds_text[2].strip())
    raw_desc = html_mod.unescape(raw_desc)
    obj_id = tds_text[3].strip()

    if not case_nr or not date_str:
        return None

    parts = raw_desc.split("|", 1)
    decision_type = parts[0].strip() if len(parts) > 1 else ""
    summary = parts[1].strip() if len(parts) > 1 else raw_desc

    return {
        "date": date_str,
        "case_nr": case_nr,
        "decision_type": decision_type,
        "summary": summary,
        "object_id": obj_id,
        "link": link,
    }


def _parse_with_bs4(html_text: str) -> list[dict]:
    """Preferred path: BeautifulSoup-based row extraction.

    Targets rows in any ``<table>`` and validates each row has
    exactly ``_EXPECTED_COLUMN_COUNT`` ``<td>`` cells before
    accepting. Rows with unexpected column counts are logged at
    WARNING for visibility into parser regressions.
    """
    decisions: list[dict] = []
    soup = BeautifulSoup(html_text, "html.parser")
    # Prefer the dedicated search-result table when present; fall
    # back to scanning all tables otherwise.
    tables = soup.select("table.search-result") or soup.find_all("table")
    for table in tables:
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if not tds:
                continue
            if len(tds) != _EXPECTED_COLUMN_COUNT:
                logger.warning(
                    "parse_html_table: skipping row with %d columns (expected %d)",
                    len(tds),
                    _EXPECTED_COLUMN_COUNT,
                )
                continue
            tds_text = [td.get_text(" ", strip=True) for td in tds]
            link = ""
            anchor = tds[1].find("a", href=True)
            if anchor:
                link = anchor["href"]
                if link and not link.startswith("http"):
                    link = f"https://rikos.rik.ee{link}"
            row = _extract_row_fields(tds_text, link=link)
            if row is not None:
                decisions.append(row)
    return decisions


def _iter_tag_inner(html_text: str, tag: str) -> list[str]:
    """Return inner HTML of each ``<tag ...>...</tag>`` via a linear scan (#356)."""
    open_pat = f"<{tag}"
    close_pat = f"</{tag}>"
    lower = html_text.lower()
    open_l = open_pat.lower()
    close_l = close_pat.lower()
    out: list[str] = []
    pos = 0
    while True:
        start = lower.find(open_l, pos)
        if start < 0:
            break
        # Require a tag boundary so ``<tr`` does not match ``<trace``.
        after = start + len(open_l)
        if after < len(html_text) and html_text[after] not in " \t\r\n/>":
            pos = after
            continue
        gt = html_text.find(">", after)
        if gt < 0:
            break
        end = lower.find(close_l, gt + 1)
        if end < 0:
            break
        out.append(html_text[gt + 1 : end])
        pos = end + len(close_l)
    return out


def _parse_with_regex(html_text: str) -> list[dict]:
    """Fallback path used when BeautifulSoup is unavailable.

    The regex approach is fragile against nested markup; we add a
    column-count assertion and warning logging to surface parser
    drift instead of silently miscounting rows.
    """
    decisions: list[dict] = []
    # Linear scan (#356): the previous greedy ``<tr[^>]*>(.*?)</tr>``
    # fallback is O(n²) on unclosed ``<tr>`` (ReDoS). Walk the string
    # once instead.
    trs = _iter_tag_inner(html_text, "tr")
    for tr in trs:
        tds = _iter_tag_inner(tr, "td")
        if not tds:
            continue
        if len(tds) != _EXPECTED_COLUMN_COUNT:
            logger.warning(
                "parse_html_table: skipping row with %d columns (expected %d)",
                len(tds),
                _EXPECTED_COLUMN_COUNT,
            )
            continue

        # Strip tags, then collapse whitespace exactly like the bs4 path's
        # ``get_text(" ", strip=True)`` so inline tags inside a cell (e.g.
        # ``3<wbr>-1-1``) yield identical ``case_nr``/``object_id`` — and
        # therefore identical ``@id``/``caseNumber`` — across both parsers.
        tds_text = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", td)).strip() for td in tds]
        link = ""
        # Accept both double- and single-quoted hrefs; the bs4 path extracts
        # either, so the regex fallback must too or single-quoted links drop.
        link_match = re.search(r'href=["\']([^"\']+)["\']', tds[1])
        if link_match:
            link = link_match.group(1)
            if link and not link.startswith("http"):
                link = f"https://rikos.rik.ee{link}"

        row = _extract_row_fields(tds_text, link=link)
        if row is not None:
            decisions.append(row)
    return decisions


def parse_html_table(html_text: str) -> list[dict]:
    """Parse decision rows from the Riigikohus search-result HTML.

    Prefers BeautifulSoup with strict column-count validation; falls
    back to a regex-based parser when ``bs4`` is unavailable. Both
    parsers reject rows whose column count differs from
    ``_EXPECTED_COLUMN_COUNT`` (4) so summaries cannot silently
    absorb misaligned cell content.
    """
    if _BS4_AVAILABLE:
        return _parse_with_bs4(html_text)
    return _parse_with_regex(html_text)


def fetch_year(year: int) -> list[dict]:
    """Fetch all decisions for a given year."""
    # First request to get total count
    resp = allowed_get(
        SEARCH_URL,
        params={"aasta": year, "pageSize": PAGE_SIZE},
        timeout=30,
    )
    resp.raise_for_status()

    total_match = re.search(r"Tulemusi leiti kokku:\s*(\d+)", resp.text)
    total = int(total_match.group(1)) if total_match else 0

    if total == 0:
        return []

    total_pages = math.ceil(total / PAGE_SIZE)
    print(f"  Year {year}: {total} decisions, {total_pages} pages")

    all_decisions = parse_html_table(resp.text)

    for page in range(2, total_pages + 1):
        time.sleep(RATE_DELAY)
        resp = allowed_get(
            SEARCH_URL,
            params={"aasta": year, "pageSize": PAGE_SIZE, "lk": page},
            timeout=30,
        )
        resp.raise_for_status()
        page_decisions = parse_html_table(resp.text)
        all_decisions.extend(page_decisions)
        if page % 5 == 0:
            print(f"    Page {page}/{total_pages} ({len(all_decisions)} so far)")

    return all_decisions


def generate_schema_nodes() -> list[dict]:
    """Generate OWL schema nodes for CourtDecision."""
    nodes: list[dict] = [
        # CourtDecision class
        {
            "@id": "estleg:CourtDecision",
            "@type": ["owl:Class"],
            "rdfs:label": {"@value": "Kohtulahend (Court Decision)", "@language": "et"},
            "rdfs:comment": {"@value": "Riigikohtu lahend – kohtuotsus, kohtumäärus või resolutsioon.", "@language": "et"},
            "dc:description": {"@value": "A Supreme Court (Riigikohus) decision, including judgments, rulings, and resolutions.", "@language": "en"},
        },
        # CaseType class
        {
            "@id": "estleg:CaseType",
            "@type": ["owl:Class"],
            "rdfs:label": {"@value": "Kohtuasja liik (Case Type)", "@language": "et"},
            "rdfs:comment": {"@value": "Kohtuasja klassifikatsioon: kriminaalasi, tsiviilasi, haldusasi, väärteoasi, põhiseaduslikkuse järelevalve.", "@language": "et"},
        },
        # DecisionType class
        {
            "@id": "estleg:DecisionType",
            "@type": ["owl:Class"],
            "rdfs:label": {"@value": "Lahendi liik (Decision Type)", "@language": "et"},
            "rdfs:comment": {"@value": "Lahendi tüüp: kohtuotsus, kohtumäärus, resolutsioon.", "@language": "et"},
        },
    ]

    # Case type individuals
    for code, (type_id, label_et, label_en) in CASE_TYPE_MAP.items():
        nodes.append({
            "@id": f"estleg:CaseType_{type_id}",
            "@type": ["owl:NamedIndividual", "estleg:CaseType"],
            "rdfs:label": {"@value": label_et, "@language": "et"},
            "skos:prefLabel": {"@value": label_en, "@language": "en"},
            "estleg:caseTypeCode": code,
        })

    # Decision type individuals. Skip Resolution (#344): unused in the
    # corpus. OrderRuling is retained as deprecated for compatibility.
    for label_et, (type_id, _) in DECISION_TYPES.items():
        if type_id == "Resolution":
            continue
        node = {
            "@id": f"estleg:DecisionType_{type_id}",
            "@type": ["owl:NamedIndividual", "estleg:DecisionType"],
            "rdfs:label": {"@value": label_et, "@language": "et"},
            "skos:prefLabel": {"@value": type_id, "@language": "en"},
        }
        if type_id == "OrderRuling":
            node["owl:deprecated"] = True
            node["rdfs:comment"] = {
                "@value": (
                    "Deprecated (issue #344): declared but never used in "
                    "the corpus — kohtumäärus decisions use "
                    "estleg:DecisionType_Ruling instead. Retained for "
                    "backward compatibility; do not emit new references."
                ),
                "@language": "en",
            }
        nodes.append(node)

    # Object properties
    nodes.extend([
        {
            "@id": "estleg:caseType",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "kohtuasja liik", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {"@id": "estleg:CaseType"},
        },
        {
            "@id": "estleg:decisionType",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "lahendi liik", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {"@id": "estleg:DecisionType"},
        },
        {
            "@id": "estleg:interpretsLaw",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "tõlgendab seadust", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {
                "@type": "owl:Class",
                "owl:unionOf": {
                    "@list": [
                        {"@id": "estleg:LegalProvision"},
                        {"@id": "estleg:Act"},
                    ],
                },
            },
            "rdfs:comment": {
                "@value": "Links a court decision to the legal provision or whole act it interprets or applies.",
                "@language": "en",
            },
        },
    ])

    # Datatype properties
    nodes.extend([
        {
            "@id": "estleg:caseNumber",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "kohtuasja number", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {"@id": "xsd:string"},
        },
        {
            "@id": "estleg:decisionDate",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "lahendi kuupäev", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {"@id": "xsd:date"},
        },
        {
            "@id": "estleg:rikObjectId",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "RIK objekti ID",
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {"@id": "xsd:string"},
        },
        {
            "@id": "estleg:rikosUrl",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "RIKOS URL",
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {"@id": "xsd:anyURI"},
            "rdfs:comment": {
                "@value": (
                    "Direct rikos.rik.ee document URL derived from "
                    "rikObjectId (https://rikos.rik.ee/Lahend/Index?id={id})."
                ),
                "@language": "en",
            },
        },
        {
            "@id": "estleg:chamber",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "kolleegium", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {"@id": "xsd:string"},
            "rdfs:comment": {
                "@value": (
                    "Deciding chamber extracted from the legalText header "
                    "(e.g. Halduskolleegium, Kriminaalkolleegium, Erikogu)."
                ),
                "@language": "en",
            },
        },
        {
            "@id": "estleg:decisionLink",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "lahendi link", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {"@id": "xsd:anyURI"},
        },
        {
            "@id": "estleg:ecliIdentifier",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "ECLI",
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {"@id": "xsd:string"},
            "rdfs:comment": {
                "@value": (
                    "European Case Law Identifier (ECLI:EE:RK:YYYY:ordinal). "
                    "Assigned from H2 2016; derived from case number + decision year."
                ),
                "@language": "en",
            },
        },
        {
            "@id": "estleg:referencedLaw",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "viidatud seadus", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {"@id": "xsd:string"},
            "rdfs:comment": {"@value": "Name or abbreviation of law referenced in the decision.", "@language": "en"},
        },
    ])

    return nodes


# ECLI assigned to published Estonian Supreme Court case law from H2 2016
# (e-justice.europa.eu EE page): ECLI:EE:RK:YYYY:<case with - and / as dots>.
_ECLI_ASSIGNED_FROM = datetime(2016, 7, 1)


def mint_riigikohus_ecli(
    case_nr: str, decision_date: str | datetime | None
) -> str | None:
    """Mint ``ECLI:EE:RK:YYYY:<ordinal>`` when the decision is in scope.

    Returns None when the case number is empty or the decision predates
    the H2-2016 ECLI assignment. ``decision_date`` may be ``YYYY-MM-DD``
    or ``DD.MM.YYYY``.
    """
    case_nr = (case_nr or "").strip()
    if not case_nr:
        return None
    parsed: datetime | None = None
    if isinstance(decision_date, datetime):
        parsed = decision_date
    elif isinstance(decision_date, str) and decision_date.strip():
        raw = decision_date.strip()
        for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
    if parsed is None or parsed < _ECLI_ASSIGNED_FROM:
        return None
    ordinal = re.sub(r"[-/]", ".", case_nr)
    ordinal = re.sub(r"[^A-Za-z0-9.]", "", ordinal)
    if not ordinal:
        return None
    return f"ECLI:EE:RK:{parsed.year}:{ordinal}"


# Direct rikos.rik.ee document URL (#442). Search-result ``decisionLink``
# stays on riigikohus.ee; this is the object-id document page.
_RIKOS_DOCUMENT_URL = "https://rikos.rik.ee/Lahend/Index?id={oid}"
# Chamber names sit in the document head; later body text cites other
# chambers / "üldkogule" and must not be read as the deciding bench.
_CHAMBER_HEADER_CHARS = 400
# Most-specific first so constitutional review is not shadowed. ``koll?e+gium``
# tolerates OCR ``kollegium`` / ``koleegium`` / extra ``e`` seen in legacy text.
_CHAMBER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "Põhiseaduslikkuse järelevalve kolleegium",
        re.compile(
            r"p[õo]hiseaduslikkuse\s+j[äa]relevalve\s+koll?e+gium",
            re.IGNORECASE,
        ),
    ),
    (
        "Kriminaalkolleegium",
        re.compile(r"kriminaal(?:kohtu)?koll?e+gium", re.IGNORECASE),
    ),
    (
        "Tsiviilkolleegium",
        re.compile(r"tsiviil(?:kohtu)?koll?e+gium", re.IGNORECASE),
    ),
    (
        "Halduskolleegium",
        re.compile(r"haldus(?:kohtu)?koll?e+gium", re.IGNORECASE),
    ),
    (
        "Erikogu",
        re.compile(r"\berikogu\b", re.IGNORECASE),
    ),
    (
        "Üldkogu",
        re.compile(r"\b[üu]ldkogu\b", re.IGNORECASE),
    ),
]


def rikos_url_from_object_id(oid: str) -> str:
    """Return the direct rikos.rik.ee document URL for a RIK object id."""
    return _RIKOS_DOCUMENT_URL.format(oid=(oid or "").strip())


def extract_chamber(legal_text: str) -> str | None:
    """Return the deciding-chamber label from a legalText header, or None.

    Only the document head is searched so body cross-references (another
    chamber, ``üldkogule`` in a resolutsioon) cannot win. When several
    chamber tokens appear in the header, the leftmost match is kept —
    that is the deciding bench on regular RK title pages.
    """
    if not legal_text or not isinstance(legal_text, str):
        return None
    header = legal_text[:_CHAMBER_HEADER_CHARS]
    hits: list[tuple[int, str]] = []
    for label, rx in _CHAMBER_PATTERNS:
        match = rx.search(header)
        if match:
            hits.append((match.start(), label))
    if not hits:
        return None
    hits.sort(key=lambda item: item[0])
    return hits[0][1]


def _unwrap_literal(value: object) -> str:
    """Plain string from a JSON-LD literal (string or ``{@value}``)."""
    if isinstance(value, dict):
        inner = value.get("@value")
        return inner if isinstance(inner, str) else ""
    return value if isinstance(value, str) else ""


def _typed_any_uri(url: str) -> dict:
    return {"@value": url, "@type": "xsd:anyURI"}


def _build_node_id(dec: dict) -> str | None:
    """Build a deterministic ``@id`` for a decision.

    The IRI is built from ``(case_nr, object_id)`` so two decisions
    sharing the same ``case_nr`` always produce distinct, stable
    IRIs across runs (no run-order-dependent flipping). When
    ``object_id`` is missing we log a WARNING and skip the
    decision because there is no other reliable disambiguator.
    """
    case_nr = (dec.get("case_nr") or "").strip()
    if not case_nr:
        logger.warning("decision_to_node: skipping decision with empty case_nr: %r", dec)
        return None
    case_id = sanitize_id(case_nr)
    obj_id = (dec.get("object_id") or "").strip()
    if not obj_id:
        logger.warning(
            "decision_to_node: skipping decision %s — missing object_id required for stable IRI",
            case_nr,
        )
        return None
    return f"estleg:RK_{case_id}_{sanitize_id(obj_id)}"


def _decision_content_key(dec: dict) -> tuple[str, str, str]:
    """Content fingerprint used to detect true-duplicate decision rows.

    The rikos API sometimes returns the *same* real document as several
    rows that differ only in ``object_id`` (issue #392). Those rows share
    the same case number, decision date, and decision type, so the
    ``(case_nr, date, decision_type)`` triple identifies the underlying
    document independently of the per-row ``object_id`` that goes into the
    ``@id``. All three components are stripped so incidental whitespace
    does not split a genuine duplicate into two keys.
    """
    return (
        (dec.get("case_nr") or "").strip(),
        (dec.get("date") or "").strip(),
        (dec.get("decision_type") or "").strip(),
    )


def court_decision_content_key_from_node(
    node: dict,
) -> tuple[str, str, str] | None:
    """Same fingerprint as ``_decision_content_key``, read from a JSON-LD node."""
    types = node.get("@type") or []
    if isinstance(types, str):
        types = [types]
    if "estleg:CourtDecision" not in types:
        return None
    case = node.get("estleg:caseNumber") or ""
    if not isinstance(case, str):
        case = ""
    date = node.get("estleg:decisionDate")
    if isinstance(date, dict):
        date = date.get("@value") or ""
    if not isinstance(date, str):
        date = ""
    dtype = node.get("estleg:decisionType")
    if isinstance(dtype, dict):
        dtype = dtype.get("@id") or ""
    if not isinstance(dtype, str):
        dtype = ""
    return (case.strip(), date.strip(), dtype.strip())


def choose_canonical_decision(nodes: list[dict]) -> dict:
    """Keep the shortest @id (no extra rikObjectId suffix), then lexical min."""
    return min(nodes, key=lambda n: (len(str(n.get("@id") or "")), str(n.get("@id") or "")))


def dedupe_court_decision_graph(
    graph: list,
) -> tuple[list, dict[str, str]]:
    """Drop true-duplicate CourtDecision nodes (#392).

    Returns ``(new_graph, remap)`` where ``remap`` maps each dropped
    ``@id`` onto the kept canonical ``@id``.
    """
    groups: dict[tuple[str, str, str], list[dict]] = {}
    passthrough: list[dict] = []
    for node in graph:
        if not isinstance(node, dict):
            passthrough.append(node)
            continue
        key = court_decision_content_key_from_node(node)
        if key is None:
            passthrough.append(node)
            continue
        groups.setdefault(key, []).append(node)

    remap: dict[str, str] = {}
    kept: list[dict] = []
    for nodes in groups.values():
        if len(nodes) == 1:
            kept.append(nodes[0])
            continue
        winner = choose_canonical_decision(nodes)
        kept.append(winner)
        win_id = winner.get("@id")
        if not isinstance(win_id, str):
            continue
        for node in nodes:
            nid = node.get("@id")
            if isinstance(nid, str) and nid != win_id:
                remap[nid] = win_id
    # Preserve original graph order: passthrough + kept, first-seen.
    kept_ids = {n.get("@id") for n in kept}
    new_graph = []
    for node in graph:
        if not isinstance(node, dict):
            new_graph.append(node)
            continue
        nid = node.get("@id")
        if court_decision_content_key_from_node(node) is None:
            new_graph.append(node)
            continue
        if nid in kept_ids:
            new_graph.append(node)
            kept_ids.discard(nid)
    return new_graph, remap


def rewrite_id_refs(value: object, remap: dict[str, str]) -> bool:
    """Rewrite ``@id`` refs in place. Dedupes list-of-IRI-objects. Returns changed."""
    changed = False
    if isinstance(value, dict):
        iri = value.get("@id")
        if isinstance(iri, str) and iri in remap:
            value["@id"] = remap[iri]
            changed = True
        for item in value.values():
            if rewrite_id_refs(item, remap):
                changed = True
    elif isinstance(value, list):
        for item in value:
            if rewrite_id_refs(item, remap):
                changed = True
        # Collapse duplicate {"@id": X} siblings after remap.
        seen: set[str] = set()
        new_list: list = []
        list_changed = False
        for item in value:
            if isinstance(item, dict):
                iri = item.get("@id")
                if isinstance(iri, str) and len(item) == 1:
                    if iri in seen:
                        list_changed = True
                        continue
                    seen.add(iri)
            new_list.append(item)
        if list_changed:
            value[:] = new_list
            changed = True
    return changed


def decision_to_node(
    dec: dict,
    year: int,
    seen_ids: set[str],
    seen_keys: set[tuple[str, str, str]] | None = None,
) -> dict | None:
    """Convert a decision dict to a JSON-LD node.

    Returns ``None`` for decisions that cannot be assigned a stable
    IRI (missing ``case_nr`` or ``object_id``). Callers MUST handle
    ``None`` and skip the row.

    When ``seen_keys`` is supplied, rows that repeat an already-emitted
    ``(case_nr, date, decision_type)`` triple are treated as true
    duplicates of the same underlying document (issue #392): the dup is
    logged at WARNING and skipped so the same real decision is not
    emitted as two+ nodes with different ``rikObjectId``. Passing
    ``None`` (the default) disables content-level dedup and keeps the
    legacy "one node per distinct ``@id``" behaviour.
    """
    node_id = _build_node_id(dec)
    if node_id is None:
        return None
    if node_id in seen_ids:
        # Same (case_nr, object_id) twice in one feed — extremely
        # rare. Keep the first; surface the dup at WARNING.
        logger.warning(
            "decision_to_node: duplicate node id %s — keeping first occurrence",
            node_id,
        )
        return None
    if seen_keys is not None:
        content_key = _decision_content_key(dec)
        if content_key in seen_keys:
            # Distinct object_id, identical real document — the rikos API
            # returned the same decision twice. Keep the first; skip this
            # duplicate so interpretsLaw links cannot land on a phantom
            # second node non-deterministically (issue #392).
            logger.warning(
                "decision_to_node: duplicate decision %r (case_nr/date/type "
                "match, distinct object_id %r) — skipping duplicate node %s",
                content_key,
                (dec.get("object_id") or "").strip(),
                node_id,
            )
            return None
        seen_keys.add(content_key)
    seen_ids.add(node_id)

    type_id, _type_et, _type_en = classify_case(dec["case_nr"])

    node: dict = {
        "@id": node_id,
        "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
        "rdfs:label": {"@value": f"RK {dec['case_nr']}", "@language": "et"},
        "estleg:caseNumber": dec["case_nr"],
        "estleg:caseType": {"@id": f"estleg:CaseType_{type_id}"},
    }

    # Decision type
    if dec.get("decision_type"):
        dt_info = DECISION_TYPES.get(dec["decision_type"])
        if dt_info:
            node["estleg:decisionType"] = {"@id": f"estleg:DecisionType_{dt_info[0]}"}

    # Date
    parsed_date: datetime | None = None
    if dec.get("date"):
        try:
            parsed_date = datetime.strptime(dec["date"], "%d.%m.%Y")
            node["estleg:decisionDate"] = {
                "@value": parsed_date.strftime("%Y-%m-%d"),
                "@type": "xsd:date",
            }
        except ValueError:
            parsed_date = None

    ecli = mint_riigikohus_ecli(dec["case_nr"], parsed_date)
    if ecli:
        node["estleg:ecliIdentifier"] = ecli

    # Summary
    if dec.get("summary"):
        node["estleg:summary"] = {"@value": dec["summary"][:800], "@language": "et"}

    # Link — URL-encoded query string so case_nr containing '#'/'/'/spaces
    # never silently breaks the resulting URL. ``urlencode`` plus
    # ``quote(safe='')`` is belt-and-suspenders here: ``urlencode``
    # alone already escapes '#', '/', and spaces in values.
    if dec.get("link"):
        encoded_query = urllib.parse.urlencode(
            {"asjaNr": dec["case_nr"]}, quote_via=urllib.parse.quote
        )
        riigikohus_link = (
            f"https://www.riigikohus.ee/et/lahendid/?{encoded_query}"
        )
        node["estleg:decisionLink"] = {"@value": riigikohus_link, "@type": "xsd:anyURI"}
        node["dcterms:source"] = {"@id": dec["link"]}

    # RIK object ID + direct rikos document URL (#442).
    if dec.get("object_id"):
        node["estleg:rikObjectId"] = dec["object_id"]
        node["estleg:rikosUrl"] = _typed_any_uri(
            rikos_url_from_object_id(dec["object_id"])
        )

    legal_text = dec.get("legal_text") or dec.get("legalText") or ""
    if isinstance(legal_text, dict):
        legal_text = legal_text.get("@value") or ""
    if isinstance(legal_text, str):
        chamber = extract_chamber(legal_text)
        if chamber:
            node["estleg:chamber"] = chamber

    # Referenced laws
    refs = detect_referenced_laws(dec.get("summary") or "")
    if refs:
        node["estleg:referencedLaw"] = refs

    return node


def backfill_ecli_on_node(node: dict) -> bool:
    """Stamp ``estleg:ecliIdentifier`` on a committed CourtDecision node."""
    types = node.get("@type") or []
    if isinstance(types, str):
        types = [types]
    if "estleg:CourtDecision" not in types:
        return False
    if node.get("estleg:ecliIdentifier"):
        return False
    case_nr = node.get("estleg:caseNumber")
    if not isinstance(case_nr, str):
        return False
    date_val = node.get("estleg:decisionDate")
    if isinstance(date_val, dict):
        date_val = date_val.get("@value")
    ecli = mint_riigikohus_ecli(
        case_nr, date_val if isinstance(date_val, str) else None
    )
    if not ecli:
        return False
    node["estleg:ecliIdentifier"] = ecli
    return True


def backfill_rk_identity(node: dict) -> bool:
    """Stamp ``estleg:rikosUrl`` + ``estleg:chamber`` on a CourtDecision.

    Idempotent: already-correct values are left untouched. Chamber is
    filled only when the node already carries ``estleg:legalText``.
    """
    types = node.get("@type") or []
    if isinstance(types, str):
        types = [types]
    if "estleg:CourtDecision" not in types:
        return False
    changed = False

    oid = _unwrap_literal(node.get("estleg:rikObjectId"))
    if oid:
        url = rikos_url_from_object_id(oid)
        existing = node.get("estleg:rikosUrl")
        existing_url = _unwrap_literal(existing)
        if existing_url != url:
            node["estleg:rikosUrl"] = _typed_any_uri(url)
            changed = True

    if not _unwrap_literal(node.get("estleg:chamber")):
        chamber = extract_chamber(_unwrap_literal(node.get("estleg:legalText")))
        if chamber:
            node["estleg:chamber"] = chamber
            changed = True
    return changed


def backfill_rk_identity_files(rk_dir: Path | None = None) -> dict[str, int]:
    """One-shot rewrite of ``riigikohus_*_peep.json`` via ``save_json``."""
    target = rk_dir if rk_dir is not None else RK_DIR
    stats = {"files": 0, "files_changed": 0, "nodes_changed": 0, "nodes": 0}
    for path in sorted(target.glob("riigikohus_*_peep.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("backfill_rk_identity: skip %s: %s", path.name, exc)
            continue
        graph = doc.get("@graph")
        if not isinstance(graph, list):
            continue
        stats["files"] += 1
        file_changed = False
        for node in graph:
            if not isinstance(node, dict):
                continue
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "estleg:CourtDecision" not in types:
                continue
            stats["nodes"] += 1
            if backfill_rk_identity(node):
                stats["nodes_changed"] += 1
                file_changed = True
        if file_changed:
            save_json(path, doc)
            stats["files_changed"] += 1
    return stats


# ---------------------------------------------------------------------------
# Full decision-text fetch (issue #135)
# ---------------------------------------------------------------------------

# Detail pages carry ``<meta name="description" content="LahendiDokument">``
# and a ``data-faili-objekt-id`` attribute; the search form / "not found"
# fallback page carries neither (it has ``id="lahenditeOtsing"`` instead).
# We use these markers to reject a non-decision response before extracting.
_DECISION_DOC_MARKERS = (
    'content="LahendiDokument"',
    "data-faili-objekt-id",
)

_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)
_BODY_RE = re.compile(r"<body\b[^>]*>(.*?)</body>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_INLINE_WS_RE = re.compile(r"[ \t\r\f\v ]+")
_MULTI_WS_RE = re.compile(r"\s+")
_VISIBLE_WS_RE = re.compile(r"\s+")
_ESTONIAN_TEXT_CHAR_RE = re.compile(r"[0-9A-Za-zÕÄÖÜõäöüŠŽšž]")
_MOJIBAKE_MARKER_RE = re.compile(r"(?:Ã.|Â.|â€|�)")


def _looks_like_decision_document(html_text: str) -> bool:
    """Return ``True`` iff ``html_text`` is a RIK decision detail page.

    A missing / bogus ``asjaNr`` makes ``rikos.rik.ee`` serve the search
    form instead of a 404; without this guard we would extract the form
    markup as if it were decision text.
    """
    return any(marker in html_text for marker in _DECISION_DOC_MARKERS)


def decision_text_quality_ratio(text: str) -> float:
    """Return the share of visible chars that look like Estonian text."""
    visible = _VISIBLE_WS_RE.sub("", text)
    if not visible:
        return 0.0
    valid = sum(1 for ch in visible if _ESTONIAN_TEXT_CHAR_RE.fullmatch(ch))
    return valid / len(visible)


def extract_decision_text(html_text: str) -> str | None:
    """Strip a RIK decision detail page down to plain text.

    Handles both the modern ``<div class=WordSection1>`` layout and the
    older bare-``<P>`` layout by simply taking the whole ``<body>``,
    dropping ``<script>``/``<style>`` blocks, removing the remaining
    tags, unescaping HTML entities, and normalising whitespace. Returns
    ``None`` for a response that is not a decision document or whose
    extracted text is implausibly short (``< MIN_DECISION_TEXT_LEN``).
    """
    if not html_text or not _looks_like_decision_document(html_text):
        return None
    body_match = _BODY_RE.search(html_text)
    body = body_match.group(1) if body_match else html_text
    body = _SCRIPT_STYLE_RE.sub(" ", body)
    plain = _TAG_RE.sub(" ", body)
    plain = html_mod.unescape(plain)
    # Collapse runs of inline whitespace (incl. NBSP) first so the final
    # normalisation does not have to also fold those, then collapse the
    # remainder (newlines, repeated spaces) into single spaces.
    plain = _INLINE_WS_RE.sub(" ", plain)
    plain = _MULTI_WS_RE.sub(" ", plain).strip()
    if len(plain) < MIN_DECISION_TEXT_LEN:
        return None
    if _MOJIBAKE_MARKER_RE.search(plain):
        return None
    if decision_text_quality_ratio(plain) < MIN_DECISION_TEXT_VALID_CHAR_RATIO:
        return None
    return plain


def _rel_to_repo(path: Path) -> str:
    """``path`` relative to the repo root, or the absolute path if outside it.

    The cache dir is normally under the repo, but tests point it at a
    ``tmp_path`` outside the tree — ``Path.relative_to`` raises there.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _detail_url(case_nr: str) -> str:
    """Build the RIK detail-page URL for ``case_nr`` (query-string encoded)."""
    encoded_query = urllib.parse.urlencode(
        {"asjaNr": case_nr}, quote_via=urllib.parse.quote
    )
    return f"{SEARCH_URL}?{encoded_query}"


def _court_text_cache_path(case_nr: str) -> Path:
    """On-disk cache path for ``case_nr``'s fetched full text."""
    digest = hashlib.sha1(case_nr.encode("utf-8")).hexdigest()[:10]
    return COURT_TEXT_CACHE_DIR / f"{sanitize_id(case_nr)}_{digest}.txt"


def _migrate_legacy_cache(cache_dir: Path) -> int:
    """Delete pre-digest court full-text cache entries from ``cache_dir``."""
    if not cache_dir.exists():
        return 0
    cleaned = 0
    for path in cache_dir.glob("*.txt"):
        if _CACHE_DIGEST_SUFFIX_RE.search(path.stem):
            continue
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("court cache cleanup: could not remove %s: %s", path, exc)
        else:
            cleaned += 1
    return cleaned


def _court_text_cache_is_fresh(
    path: Path, ttl_days: int = COURT_TEXT_CACHE_TTL_DAYS
) -> bool:
    """Return ``True`` iff ``path`` exists and is younger than ``ttl_days``."""
    if not path.exists():
        return False
    age_seconds = time.time() - path.stat().st_mtime
    return age_seconds < ttl_days * 86400


def _http_get_decision_page(url: str) -> str:
    """GET a RIK detail page, retrying transient failures with backoff."""
    last_exc: BaseException | None = None
    for attempt in range(DETAIL_RETRIES):
        try:
            resp = allowed_get(
                url,
                headers={"User-Agent": DETAIL_USER_AGENT},
                timeout=DETAIL_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.text
        except Exception as exc:  # noqa: BLE001 — broad to retry network/HTTP blips
            last_exc = exc
            if attempt < DETAIL_RETRIES - 1:
                time.sleep(DETAIL_BACKOFF * (2 ** attempt))
    raise RuntimeError(
        f"detail-page fetch failed after {DETAIL_RETRIES} attempts: {last_exc}"
    ) from last_exc


def fetch_decision_text(
    case_nr: str, *, use_cache: bool = True, session_fetched: list[bool] | None = None
) -> str | None:
    """Fetch the full text of a Supreme Court decision by case number.

    Looks the text up first in the on-disk cache
    (``COURT_TEXT_CACHE_DIR``); a fresh cache entry short-circuits both
    the HTTP request and the courtesy ``time.sleep`` that follows it, so
    reruns within the TTL never touch the network. On a cache miss it
    GETs ``SEARCH_URL?asjaNr=<case_nr>`` (with a polite User-Agent and
    bounded retry-on-transient-error), extracts the plain text, sleeps
    ``DETAIL_RATE_DELAY`` to be nice to the endpoint, and writes the
    result to the cache. Returns ``None`` when the case number is empty,
    the response is not a decision document, or the extracted text is
    implausibly short — callers treat ``None`` as "no full text for this
    decision in this run" and leave the node's existing shape untouched.

    ``session_fetched`` (when supplied) is a one-element list the caller
    uses as an out-param: it is set to ``[True]`` iff this call issued a
    network request (i.e. it was *not* a cache hit), so the caller can
    skip its own inter-fetch sleep on cache hits.
    """
    if session_fetched is not None:
        session_fetched[:] = [False]
    case_nr = (case_nr or "").strip()
    if not case_nr:
        return None

    cache_path = _court_text_cache_path(case_nr)
    if use_cache and _court_text_cache_is_fresh(cache_path):
        try:
            text = cache_path.read_text(encoding="utf-8")
        except OSError:
            text = ""  # corrupt/unreadable cache entry — fall through to refetch
        else:
            return text or None

    if session_fetched is not None:
        session_fetched[:] = [True]
    try:
        html_text = _http_get_decision_page(_detail_url(case_nr))
    except Exception as exc:  # noqa: BLE001 — terminal failure for this decision
        logger.warning("fetch_decision_text: %s — %s", case_nr, exc)
        time.sleep(DETAIL_RATE_DELAY)
        return None

    text = extract_decision_text(html_text)
    time.sleep(DETAIL_RATE_DELAY)

    if text is None:
        logger.warning(
            "fetch_decision_text: %s — response is not a decision document "
            "(or text too short); skipping",
            case_nr,
        )
        return None

    if use_cache:
        try:
            COURT_TEXT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            # Cache write is best-effort — a failed write must not abort the run.
            logger.warning("fetch_decision_text: cache write failed for %s: %s", case_nr, exc)

    return text


def _iter_decision_nodes_in_year_order() -> list[tuple[Path, dict, list[dict]]]:
    """Yield ``(peep_path, graph, decision_nodes)`` for every year file.

    Ordered by year descending (newest first) so a small
    ``--full-text-limit`` enriches the most recent — and most useful —
    decisions first. Within a year file, node order is preserved.
    """
    def _year_key(p: Path) -> int:
        m = re.search(r"riigikohus_(\d{4})_peep\.json$", p.name)
        return int(m.group(1)) if m else -1

    result: list[tuple[Path, dict, list[dict]]] = []
    for peep_path in sorted(
        RK_DIR.glob("riigikohus_*_peep.json"), key=_year_key, reverse=True
    ):
        try:
            with open(peep_path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("enrich_full_text: skipping unreadable %s: %s", peep_path.name, exc)
            continue
        graph = doc.get("@graph", [])
        if not isinstance(graph, list):
            continue
        decision_nodes = [
            node
            for node in graph
            if isinstance(node, dict) and "estleg:CourtDecision" in (node.get("@type") or [])
        ]
        result.append((peep_path, doc, decision_nodes))
    return result


def enrich_full_text(limit: int = DEFAULT_FULL_TEXT_LIMIT, *, use_cache: bool = True) -> dict:
    """Add ``estleg:legalText`` to existing decision nodes (issue #135).

    Operates on the already-generated ``riigikohus_*_peep.json`` files
    (it does NOT re-scrape the search table). For the first ``limit``
    decision nodes — in year-descending, then file order; ``limit <= 0``
    means all of them — it calls :func:`fetch_decision_text` on the
    node's ``estleg:caseNumber`` and, when text comes back, sets a plain
    ``xsd:string`` ``estleg:legalText`` literal on the node. Nodes
    already carrying ``estleg:legalText`` are skipped (idempotent /
    resumable). Returns a small stats dict that the caller folds into
    ``RIIGIKOHUS_INDEX.json``.
    """
    year_files = _iter_decision_nodes_in_year_order()
    total_decisions = sum(len(nodes) for _, _, nodes in year_files)
    unbounded = limit is None or limit <= 0
    target = total_decisions if unbounded else min(limit, total_decisions)

    print("\n--- Fetching full decision text (issue #135) ---")
    print(f"  Decisions in corpus: {total_decisions}")
    print(
        "  Fetching full text for: "
        + ("all decisions (this is the multi-hour run)" if unbounded else f"up to {target}")
    )
    if use_cache:
        cleaned = _migrate_legacy_cache(COURT_TEXT_CACHE_DIR)
        if cleaned:
            print(f"cleaned {cleaned} legacy court cache entries")
    print(f"  Cache: {_rel_to_repo(COURT_TEXT_CACHE_DIR)} "
          f"({'enabled' if use_cache else 'BYPASSED'})")

    attempted = 0
    succeeded = 0
    failed = 0
    cache_hits = 0
    already_had = 0
    files_modified = 0
    session_fetched: list[bool] = [False]

    for peep_path, doc, decision_nodes in year_files:
        modified = False
        for node in decision_nodes:
            if not unbounded and attempted >= target:
                break
            if "estleg:legalText" in node:
                already_had += 1
                continue
            case_nr = (node.get("estleg:caseNumber") or "").strip()
            if not case_nr:
                continue

            attempted += 1
            text = fetch_decision_text(
                case_nr, use_cache=use_cache, session_fetched=session_fetched
            )
            # ``session_fetched`` is set to ``[False]`` by fetch_decision_text
            # iff the call was served from cache (no network request).
            if session_fetched and session_fetched[0] is False:
                cache_hits += 1

            if text:
                # Plain Python str → JSON-LD plain string literal, which
                # expands to ``xsd:string`` (matching the SHACL shape).
                # NEVER language-tag this — the SHACL ``sh:datatype
                # xsd:string`` on ``estleg:legalText`` would reject a
                # ``rdf:langString``.
                node["estleg:legalText"] = text
                backfill_rk_identity(node)
                succeeded += 1
                modified = True
            else:
                failed += 1

            if attempted % 25 == 0:
                print(
                    f"    {attempted}/{target} attempted "
                    f"({succeeded} ok, {failed} no-text, {cache_hits} from cache)"
                )

        if modified:
            save_json(peep_path, doc)
            files_modified += 1
        if not unbounded and attempted >= target:
            break

    remaining = total_decisions - already_had - succeeded
    print(
        f"  Done: attempted={attempted}, with full text now={already_had + succeeded} "
        f"(new={succeeded}, pre-existing={already_had}), failed={failed}, "
        f"cache hits={cache_hits}, files rewritten={files_modified}, "
        f"still without full text={remaining}"
    )
    return {
        "fetch_attempted": attempted,
        "fetch_succeeded": succeeded,
        "fetch_failed": failed,
        "cache_hits": cache_hits,
        "with_full_text_total": already_had + succeeded,
        "without_full_text_total": remaining,
        "total_decisions": total_decisions,
    }


def _update_index_full_text_stats(stats: dict) -> None:
    """Merge ``enrich_full_text`` stats into ``RIIGIKOHUS_INDEX.json``."""
    index_path = RK_DIR / "RIIGIKOHUS_INDEX.json"
    if not index_path.exists():
        logger.warning("enrich_full_text: %s not found — skipping index update", index_path.name)
        return
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("enrich_full_text: cannot update %s: %s", index_path.name, exc)
        return
    index["full_text"] = {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "source": f"{SEARCH_URL}?asjaNr=<case_nr>",
        "property": "estleg:legalText",
        "decisions_with_full_text": stats.get("with_full_text_total", 0),
        "decisions_without_full_text": stats.get("without_full_text_total", 0),
        "last_run_attempted": stats.get("fetch_attempted", 0),
        "last_run_succeeded": stats.get("fetch_succeeded", 0),
        "last_run_failed": stats.get("fetch_failed", 0),
        "last_run_cache_hits": stats.get("cache_hits", 0),
    }
    save_json(index_path, index)
    print(f"  Updated {index_path.name} with full-text coverage stats")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--fetch-full-text",
        action="store_true",
        help=(
            "Enrich the existing riigikohus_*_peep.json files with full "
            "decision text (estleg:legalText) from each decision's RIK "
            "detail page, instead of re-scraping the search table. "
            "Fetches are cached under "
            f"{_rel_to_repo(COURT_TEXT_CACHE_DIR)}."
        ),
    )
    parser.add_argument(
        "--full-text-limit",
        type=int,
        default=DEFAULT_FULL_TEXT_LIMIT,
        metavar="N",
        help=(
            "With --fetch-full-text: fetch full text for at most N "
            f"decisions in this run (default: {DEFAULT_FULL_TEXT_LIMIT}; "
            "0 = all ~12k decisions, i.e. the multi-hour run — resumable "
            "thanks to the on-disk cache)."
        ),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help=(
            "With --fetch-full-text: bypass the on-disk detail-page cache "
            f"({_rel_to_repo(COURT_TEXT_CACHE_DIR)}) and force "
            "fresh HTTP fetches."
        ),
    )
    return parser.parse_args(argv)


def run_full_text_enrichment(args: argparse.Namespace) -> None:
    """Entry point for ``--fetch-full-text`` mode (issue #135)."""
    print("=" * 60)
    print("Enriching Supreme Court decisions with full decision text (#135)")
    print("=" * 60)
    decision_files = list(RK_DIR.glob("riigikohus_*_peep.json"))
    if not decision_files:
        print(
            f"ERROR: no riigikohus_*_peep.json files under {_rel_to_repo(RK_DIR)}. "
            "Run this script without --fetch-full-text first to scrape the corpus."
        )
        sys.exit(1)
    stats = enrich_full_text(limit=args.full_text_limit, use_cache=not args.no_cache)
    _update_index_full_text_stats(stats)
    print("\n" + "=" * 60)
    print("Full decision-text enrichment complete!")
    print(f"  Decisions with estleg:legalText: {stats['with_full_text_total']}/{stats['total_decisions']}")
    print(f"  New this run:                    {stats['fetch_succeeded']}")
    print(f"  Failed fetches this run:         {stats['fetch_failed']}")
    print(f"  Cache hits this run:             {stats['cache_hits']}")
    if stats["without_full_text_total"]:
        print(
            f"  Still without full text:         {stats['without_full_text_total']} "
            "— re-run with --fetch-full-text --full-text-limit 0 for the full sweep"
        )
    print("=" * 60)


def main(argv: list[str] | None = None):
    args = parse_args(argv)
    if args.fetch_full_text:
        run_full_text_enrichment(args)
        return

    start_year = 1993
    end_year = 2026

    print("=" * 60)
    print("Fetching Supreme Court decisions from RIK")
    print(f"Years: {start_year}–{end_year}")
    print("=" * 60)

    # Generate schema
    print("\n--- Generating schema ---")
    schema_doc = {"@context": CONTEXT, "@graph": generate_schema_nodes()}
    schema_path = RK_DIR / "riigikohus_schema.json"
    save_json(schema_path, schema_doc)
    print(f"  Saved: {schema_path.name} ({len(schema_doc['@graph'])} nodes)")

    all_decisions: list[dict] = []
    year_stats: dict[int, int] = {}

    for year in range(end_year, start_year - 1, -1):
        print(f"\n--- Year {year} ---")
        try:
            decisions = fetch_year(year)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        if not decisions:
            print("  No decisions found")
            continue

        # Generate per-year file
        graph: list[dict] = [
            {
                "@id": f"estleg:Riigikohus_{year}_Map",
                "@type": ["owl:Ontology"],
                "rdfs:label": {"@value": f"Riigikohtu lahendid {year}", "@language": "et"},
                "dc:description": {"@value": f"Riigikohtu lahendid aastast {year} ({len(decisions)} lahendit)", "@language": "et"},
                "dc:source": "Riigikohus – rikos.rik.ee",
            },
        ]

        seen_ids: set[str] = set()
        # Content-level dedup within the year file: drops rikos rows that
        # repeat the same (case_nr, date, decision_type) under a different
        # object_id, i.e. the same real document returned twice (#392).
        seen_keys: set[tuple[str, str, str]] = set()
        skipped_in_year = 0
        # Track only the decisions that actually produced a node. Rows that
        # decision_to_node drops as duplicates (seen_ids / seen_keys) or as
        # unbuildable (no stable IRI) must NOT feed the index counts, or the
        # index would advertise more decisions than the emitted graph holds
        # (#398). The index total + case_type_counts + per-year breakdown are
        # tallied from this emitted set below, matching the de-duplicated peep
        # graph (the same invariant rederive_court_case_types enforces).
        emitted_in_year: list[dict] = []
        for dec in decisions:
            node = decision_to_node(dec, year, seen_ids, seen_keys)
            if node is None:
                skipped_in_year += 1
                continue
            graph.append(node)
            emitted_in_year.append(dec)
        if skipped_in_year:
            print(f"  Skipped {skipped_in_year} unsalvageable decisions for {year}")

        doc = {"@context": CONTEXT, "@graph": graph}
        out_path = RK_DIR / f"riigikohus_{year}_peep.json"
        save_json(out_path, doc)
        print(f"  Saved: {out_path.name} ({len(graph)} nodes)")

        # Count emitted decisions (post-dedup), not the raw rikos rows (#398).
        year_stats[year] = len(emitted_in_year)
        all_decisions.extend(emitted_in_year)
        time.sleep(RATE_DELAY)

    # Generate index
    print("\n--- Generating index ---")
    index = {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "source": "https://rikos.rik.ee",
        "total_decisions": len(all_decisions),
        "years": {str(y): c for y, c in sorted(year_stats.items(), reverse=True)},
        "case_type_counts": {},
    }

    # Count by case type — overall AND per-year breakdown so we can
    # validate the prefix distribution looks sensible (e.g. roughly
    # 30% Civil + 30% Criminal + 30% Administrative; an Other-heavy
    # year likely indicates a parser regression).
    type_counts: dict[str, int] = {}
    per_year_type_counts: dict[str, dict[str, int]] = {}
    for dec in all_decisions:
        type_id, _, _ = classify_case(dec["case_nr"])
        type_counts[type_id] = type_counts.get(type_id, 0) + 1
        year_key = _decision_year_key(dec)
        per_year_type_counts.setdefault(year_key, {})
        per_year_type_counts[year_key][type_id] = (
            per_year_type_counts[year_key].get(type_id, 0) + 1
        )
    index["case_type_counts"] = type_counts
    index["case_type_counts_per_year"] = per_year_type_counts

    index_path = RK_DIR / "RIIGIKOHUS_INDEX.json"
    save_json(index_path, index)
    print(f"  Saved: {index_path.name}")

    # Summary
    print("\n" + "=" * 60)
    print(f"Done! Fetched {len(all_decisions)} Supreme Court decisions.")
    print(f"Years covered: {start_year}–{end_year}")
    print(f"Files saved to: {_rel_to_repo(RK_DIR)}")
    print()
    for type_id, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {type_id}: {count}")
    print("=" * 60)


if __name__ == "__main__":
    main()
