#!/usr/bin/env python3
"""Populate ``estleg:Annotation`` nodes from a practitioner-guidance source.

Issue #199 (split from #40): the practitioner-annotation *model* — ``estleg:Annotation``
plus ``estleg:annotates`` / ``estleg:annotationText`` / ``estleg:annotationType`` /
``estleg:annotationSource`` / ``estleg:annotationSourceUrl`` / ``estleg:annotationDate``,
``estleg:AnnotationShape`` (``annotationType`` ``sh:in`` enum: ``guidance`` |
``commentary`` | ``practice_note`` | ``warning`` | ``interpretation``) — was defined in
#40/#200. This script is the *ingestion* half: it emits well-formed ``estleg:Annotation``
nodes linked to corpus entities.

Source decision (made up front, see the issue): **Õiguskantsler seisukohad** — the Chancellor
of Justice's published opinions/positions at https://www.oiguskantsler.ee. We probed the
site: the opinions index at ``/seisukohad-ja-algatused/seisukohad`` is a *server-rendered*
Drupal HTML listing (≈4100 opinions, 20 per page, ~207 pages), each list item carrying a
title, a publication date, a PDF link, and topic/administrative tags. The opinion *body*
lives in the linked PDF (not HTML); a robust full ingestion therefore needs a PDF text
layer (no PDF library is in this project's dependency set today) plus in-body provision-
reference resolution at scale — that is the multi-hour follow-up run. Per the established
"infra + sample, full run is a follow-up" pattern, this script ships:

  * a **curated seed source** (``data/annotations/seed_annotations.json``, the default):
    3–5 well-known Õiguskantsler opinions, each naming the law(s) it concerns by canonical
    title plus a short substantive paraphrase of the interpretive position — offline,
    deterministic, guaranteed well-formed output;
  * a ``--scrape`` mode that fetches the live ``/seisukohad-ja-algatused/seisukohad``
    listing for the ``--limit`` most-recent opinions and extracts law references from each
    opinion *title* (Estonian titles cite laws by genitive name, e.g. "Karistusseadustiku §
    381¹ …" → Karistusseadustik) — the basis the follow-up extends with PDF-body parsing.

Either way the output is identical in shape: for each opinion we resolve every named law to
its real act-level node IRI (the law peep's ``owl:Ontology`` ``@id``, e.g.
``estleg:KOKS_Map_2026`` — like ``generate_harmonisation_links.py::get_law_harmonisation_target_iri``),
and emit **one** ``estleg:Annotation`` per (opinion, resolved law) — ``AnnotationShape``
requires exactly one ``estleg:annotates``, so we go one-annotation-per-(opinion, law) and
dedup the law IRIs within an opinion. A law name that does not resolve to a corpus node is
**skipped** (never a dangling ``annotates``) and recorded in the coverage report's skip
reasons. IRI scheme: ``estleg:Annotation_OK_<opinion-slug>`` for a single-law opinion, or
``estleg:Annotation_OK_<opinion-slug>_<ABBREV>`` when an opinion concerns several laws.

Output placement (mirrors the sanctions / amendments / provision_versions sidecars):
  * the ``estleg:Annotation`` nodes go to ``krr_outputs/annotations/oiguskantsler_seisukohad.jsonld``
    (a ``@graph`` + small ``@context``); ``shacl_validate_all.py``'s ``sidecars`` bucket
    validates the directory against ``estleg:AnnotationShape``;
  * each annotation links *into* the law via ``estleg:annotates`` (a one-directional edge
    to the existing act node). The reciprocal ``estleg:annotatedBy`` back-link onto the
    annotated laws is **deferred** — wiring it today would put a dangling
    ``annotatedBy -> estleg:Annotation_OK_…`` reference into ``combined_ontology.jsonld``
    (the sidecar is not a combined input). See the #199 follow-up.
  * a coverage report goes to ``krr_outputs/reports/kov/extract_annotations_coverage.json``
    (the same place the other pipeline coverage reports live).

Usage::

    python3 scripts/generate_annotations.py --limit 5             # the seed sample
    python3 scripts/generate_annotations.py --seed path/to/seed.json
    python3 scripts/generate_annotations.py --scrape --limit 5    # title-only live scrape
    python3 scripts/generate_annotations.py --probe-pdfs --pdf-probe-sample-size 10
    python3 scripts/generate_annotations.py --scrape --limit 0    # (the unbounded full run is the follow-up)
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote as urllib_parse_unquote

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from estleg_common import (  # noqa: E402
    CONTEXT,
    KNOWN_ABBREVIATIONS,
    KRR_DIR,
    iter_peep_files,
    sanitize_id,
    slugify,
)
from kov_pipeline_coverage import (  # noqa: E402
    CoverageReport,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

ANNOTATIONS_DIR = KRR_DIR / "annotations"
SIDECAR_PATH = ANNOTATIONS_DIR / "oiguskantsler_seisukohad.jsonld"
COVERAGE_PATH = KRR_DIR / "reports" / "kov" / "extract_annotations_coverage.json"
CACHE_DIR = KRR_DIR / ".cache" / "annotations"
PDF_PROBE_REPORT_PATH = KRR_DIR / "reports" / "annotations_pdf_probe.json"

SEED_PATH = REPO_ROOT / "data" / "annotations" / "seed_annotations.json"

ANNOTATION_SOURCE = "Õiguskantsler"
ANNOTATION_TYPE = "interpretation"  # Õiguskantsler opinions are interpretive

SEISUKOHAD_LISTING_URL = "https://www.oiguskantsler.ee/seisukohad-ja-algatused/seisukohad"
FETCH_SLEEP_SECONDS = 0.4

DEFAULT_LIMIT = 5
PDF_PROBE_SAMPLE_SIZE = 10
MIN_PDF_TEXT_CHARS = 500
MIN_PDF_TEXT_VALID_CHAR_RATIO = 0.30
PDF_TEXT_LAYER_ACCEPTANCE_RATIO = 0.80
_PDF_TEXT_CHAR_RE = re.compile(r"[0-9A-Za-zÕÄÖÜõäöüŠŽšž]")

# Act-level @type values that legitimately represent the act node an annotation should
# point at; provision-level nodes intentionally do NOT appear here so the fallback never
# silently picks a section node. (Mirrors generate_harmonisation_links._ACT_LEVEL_TYPES.)
_ACT_LEVEL_TYPES = frozenset({"owl:Ontology", "estleg:Act", "estleg:Map"})

# An ``owl:Ontology`` @id we will accept as a link target: an ``estleg:`` CURIE that is more
# than the bare prefix (some legacy multipart peeps carry ``estleg:`` or the bare namespace
# IRI on the ontology node — those are not addressable, so we reject them).
_BARE_NS = {"estleg:", "https://data.riik.ee/ontology/estleg#"}
_MAP_IRI_RE = re.compile(r"^estleg:[^\s]+_Map(?:_\d{4})?$")


def _looks_like_pdf_url(url: str) -> bool:
    """Return whether ``url`` points directly at a PDF resource."""
    return url.split("?", 1)[0].lower().endswith(".pdf")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Opinion:
    """One Õiguskantsler opinion to annotate from."""

    opinion_id: str          # stable slug / id used in the Annotation IRI
    title: str
    url: str
    date_iso: str | None     # ISO date (YYYY-MM-DD) or None
    law_names: tuple[str, ...]  # law titles the opinion concerns (as written)
    summary: str             # substantive summary / paraphrase of the opinion's position
    tags: tuple[str, ...] = ()  # topic / administrative-area tags (scrape mode only)


@dataclass
class _LawIndex:
    """Resolves a law name to its real act-level node IRI + a short abbreviation.

    Built from every root ``*_peep.json``: each contributes its ``owl:Ontology`` ``@id``
    (the addressable act node) plus its ``dc:source`` (the human title) and slug. Names are
    normalised (lowercased, Estonian diacritics transliterated, parenthetical suffixes
    stripped); genitive variants of the trailing ``…seadus`` / ``…seadustik`` word are
    registered too so a title's "looduskaitseseaduse §…" matches "Looduskaitseseadus".
    """

    by_name: dict[str, str] = field(default_factory=dict)        # normalised name -> IRI
    by_slug: dict[str, str] = field(default_factory=dict)        # peep slug -> IRI
    abbrev_by_iri: dict[str, str] = field(default_factory=dict)  # IRI -> short abbrev
    # Names sorted longest-first, for greedy substring matching against opinion titles.
    _ordered_names: list[str] = field(default_factory=list)

    def resolve(self, name: str) -> str | None:
        return self.by_name.get(_norm_name(name)) or self.by_slug.get(name.strip())

    def abbrev(self, iri: str) -> str:
        return self.abbrev_by_iri.get(iri, "LAW")

    def find_in_title(self, title: str) -> list[str]:
        """Return the act-IRIs whose (genitive-aware) name occurs in ``title``.

        Greedy longest-name-first scan over the normalised title so "kohaliku omavalitsuse
        korralduse seaduse" wins over a stray "seaduse"; deduped, in first-occurrence order.
        """
        hay = _norm_name(title)
        found: list[str] = []
        for name in self._ordered_names:
            if name in hay:
                iri = self.by_name[name]
                if iri not in found:
                    found.append(iri)
        return found


# ---------------------------------------------------------------------------
# Name normalisation + genitive handling
# ---------------------------------------------------------------------------


_DIACRITIC_MAP = {"õ": "o", "ä": "a", "ö": "o", "ü": "u", "š": "s", "ž": "z"}


def _norm_name(value: str) -> str:
    """Lowercase + Estonian diacritic transliteration + parenthetical strip + ws collapse."""
    s = value.lower()
    for src, dst in _DIACRITIC_MAP.items():
        s = s.replace(src, dst)
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s)  # drop a trailing "(Riigi Teataja …)" suffix
    return " ".join(s.split())


def _clean_dc_source(value: object) -> str | None:
    """Pull a plain title string from a ``dc:source`` value (string or list); strip suffix."""
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", value).strip()
    return cleaned or None


def _genitive_variants(norm_name: str) -> list[str]:
    """Return genitive forms of a normalised law name's trailing word, if applicable.

    Estonian opinion titles cite laws in the genitive: "…seaduse §…", "…seadustiku §…",
    "…seadustiku muutmine". The corpus stores nominative titles. We register the obvious
    genitive of the last token so title scanning matches; only the productive ``seadus`` /
    ``seadustik`` / ``seadustikku`` endings (which cover the overwhelming majority of act
    names) are handled — anything else just won't get a genitive alias (the nominative is
    still indexed).
    """
    out: list[str] = []
    if norm_name.endswith("seadustik"):
        out.append(norm_name + "u")        # seadustik -> seadustiku
    elif norm_name.endswith("seadus"):
        out.append(norm_name + "e")        # seadus -> seaduse
    elif norm_name.endswith("seaduste"):
        # already plural-genitive-ish; leave alone
        pass
    return out


# ---------------------------------------------------------------------------
# Building the law index from the corpus
# ---------------------------------------------------------------------------


def _ontology_node_iri(graph: list, *, prefer_map: bool = True) -> str | None:
    """Return the addressable act-node IRI from a peep ``@graph``.

    Preferred path: the ``owl:Ontology`` metadata node's ``@id`` (when ``prefer_map`` and
    several peeps share a title, a ``…_Map`` / ``…_Map_YYYY`` IRI is chosen over a part IRI
    by the caller's first-wins ordering — see :func:`build_law_index`). Restricted fallback:
    ``graph[0]`` only if it is itself an act-level node — never an arbitrary provision node
    (mirrors ``generate_harmonisation_links.get_law_harmonisation_target_iri``).
    """
    for node in graph:
        if not isinstance(node, dict):
            continue
        types = node.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if "owl:Ontology" in types:
            nid = node.get("@id")
            if isinstance(nid, str) and nid not in _BARE_NS and nid.startswith("estleg:"):
                return nid
            return None
    if graph and isinstance(graph[0], dict):
        node = graph[0]
        types = node.get("@type", [])
        if isinstance(types, str):
            types = [types]
        nid = node.get("@id")
        if any(t in _ACT_LEVEL_TYPES for t in types) and isinstance(nid, str) and nid not in _BARE_NS and nid.startswith("estleg:"):
            return nid
    return None


def _abbrev_from_iri(iri: str) -> str:
    """Derive a short alpha-numeric abbreviation from an act-node IRI for the Annotation IRI.

    ``estleg:KOKS_Map_2026`` -> ``KOKS``; ``estleg:KARIST_2_Osa1_1_87`` -> ``KARIST``;
    ``estleg:TsÜS_Osa7_138_169`` -> ``TsUS``. Falls back to ``LAW`` if nothing usable.
    """
    body = iri.removeprefix("estleg:")
    # Take the leading run up to the first separator that introduces a structural suffix
    # (``_Map``, ``_Osa``, ``_Ontology``, ``_v1`` …) or just the first ``_``-token.
    body = re.split(r"_(?:Map|Osa|Ontology|Par|v\d)", body, maxsplit=1)[0]
    body = body.split("_", 1)[0]
    abbrev = sanitize_id(body)  # transliterate diacritics + strip non-alnum/underscore
    abbrev = abbrev.strip("_") or "LAW"
    return abbrev


def build_law_index(krr_dir: Path = KRR_DIR) -> _LawIndex:
    """Scan every root ``*_peep.json`` and build a name/slug -> act-IRI index.

    For a title shared by several peep files (multipart laws), a ``…_Map`` IRI wins; among
    equally-ranked candidates the first by sorted filename wins (deterministic).
    """
    idx = _LawIndex()
    # First pass: collect (slug, ont_iri, dc_source) keeping the best IRI per dc:source.
    candidates: dict[str, tuple[str, int]] = {}  # norm_source -> (iri, rank) ; lower rank = better
    slug_to_iri: dict[str, str] = {}
    for path in sorted(krr_dir.glob("*_peep.json")):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        graph = doc.get("@graph", [])
        iri = _ontology_node_iri(graph)
        if not iri:
            continue
        slug = path.name[: -len("_peep.json")]
        base_slug = re.sub(r"_osa[\d_\-]+$", "", slug, flags=re.IGNORECASE)
        slug_to_iri.setdefault(slug, iri)
        slug_to_iri.setdefault(base_slug, iri)
        # dc:source title
        src = None
        for node in graph:
            if isinstance(node, dict) and "owl:Ontology" in (node.get("@type", []) or []):
                src = _clean_dc_source(node.get("dc:source"))
                break
        if not src:
            continue
        norm = _norm_name(src)
        rank = 0 if _MAP_IRI_RE.match(iri) else 1
        prev = candidates.get(norm)
        if prev is None or rank < prev[1]:
            candidates[norm] = (iri, rank)

    idx.by_slug = dict(slug_to_iri)
    for norm, (iri, _rank) in candidates.items():
        idx.by_name.setdefault(norm, iri)
        for gen in _genitive_variants(norm):
            idx.by_name.setdefault(gen, iri)

    # Per-IRI short abbreviation for the Annotation IRI: prefer a known abbreviation whose
    # full name resolves to this IRI (via the name index just built); else derive one from
    # the IRI body. NB: we deliberately do NOT add bare abbreviations as title-scan keys —
    # a short token like "EKS" would spuriously substring-match inside an unrelated word
    # (e.g. "ülaindEKSiga"); only full law names (long enough to be unambiguous) are scanned.
    full_by_iri: dict[str, str] = {}
    for abbr, full in KNOWN_ABBREVIATIONS.items():
        target = idx.by_name.get(_norm_name(full))
        if target and target not in full_by_iri:
            full_by_iri[target] = abbr
    for iri in set(idx.by_name.values()):
        idx.abbrev_by_iri[iri] = sanitize_id(full_by_iri.get(iri, "")) or _abbrev_from_iri(iri)

    idx._ordered_names = sorted(idx.by_name, key=len, reverse=True)
    return idx


# ---------------------------------------------------------------------------
# Source: curated seed file
# ---------------------------------------------------------------------------


def load_seed_opinions(seed_path: Path = SEED_PATH) -> list[Opinion]:
    """Parse ``data/annotations/seed_annotations.json`` into :class:`Opinion` records."""
    with open(seed_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    opinions: list[Opinion] = []
    for raw in data.get("opinions", []):
        if not isinstance(raw, dict):
            continue
        title = (raw.get("title") or "").strip()
        if not title:
            continue
        oid = (raw.get("id") or slugify(title)).strip() or slugify(title)
        laws = tuple(
            s.strip() for s in (raw.get("laws") or []) if isinstance(s, str) and s.strip()
        )
        date_iso = _coerce_iso_date(raw.get("date"))
        opinions.append(
            Opinion(
                opinion_id=oid,
                title=title,
                url=(raw.get("url") or "").strip(),
                date_iso=date_iso,
                law_names=laws,
                summary=(raw.get("summary") or "").strip(),
            )
        )
    return opinions


def _coerce_iso_date(value: object) -> str | None:
    """Accept ``YYYY-MM-DD`` (strict) or ``DD.MM.YYYY``; return strict ISO or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip()
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", v)
    if m:
        d, mo, y = (int(g) for g in m.groups())
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return None
    try:
        parsed = date.fromisoformat(v)
    except ValueError:
        return None
    return parsed.isoformat() if parsed.isoformat() == v else None


# ---------------------------------------------------------------------------
# Source: live oiguskantsler.ee seisukohad listing (HTML scrape)
# ---------------------------------------------------------------------------


# Each opinion in the listing is a ``<div class="views-row"><div class="list-item doc"> … </div></div>``
# block; inside it: the title (and a PDF or page link) in ``<div class="list-title"> … </div>``,
# then ``<div class="list-body"></div>``, then ``<div class="list-meta"><div class="list-tags">
# <a class="tag" data-tagid="N">Label</a>…</div>DD.MM.YYYY</div>``. The page also contains one
# leading ``<div class="views-row"><article …>`` header row (no ``list-item doc``) — that one is
# naturally skipped because we anchor on ``list-item doc``. We parse with regexes: the listing
# markup is stable Drupal output and no HTML parser is in this project's dependency set.
_OPINION_ROW_RE = re.compile(
    r'<div class="views-row">\s*<div class="list-item doc">(.*?)</div>\s*</div>\s*</div>', re.S
)
_LIST_TITLE_RE = re.compile(r'<div class="list-title">(.*?)</div>\s*<div class="list-body">', re.S)
_LIST_META_RE = re.compile(r'<div class="list-meta">(.*?)</div>\s*$', re.S)
_ANCHOR_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DATE_RE = re.compile(r'(\d{1,2}\.\d{1,2}\.\d{4})')
_TAG_RE = re.compile(r'<a[^>]+class="tag"[^>]*>(.*?)</a>', re.S)


def parse_listing_html(html_text: str) -> list[Opinion]:
    """Parse one ``/seisukohad-ja-algatused/seisukohad`` page into :class:`Opinion` records.

    Law references are extracted from each opinion's *title* by the caller (it has the law
    index); here we just shape (title, url, date, tags). Rows with no usable title are skipped.
    """
    out: list[Opinion] = []
    for row in _OPINION_ROW_RE.findall(html_text):
        mt = _LIST_TITLE_RE.search(row)
        title_block = mt.group(1) if mt else row
        ma = _ANCHOR_RE.search(title_block)
        if ma:
            href = html.unescape(ma.group(1).strip())
            title = _strip_tags(html.unescape(ma.group(2)))
        else:
            href = ""
            title = _strip_tags(html.unescape(title_block))
        if not title:
            continue
        # The date and tags live in the trailing <div class="list-meta">…</div>; the date is
        # the plain "DD.MM.YYYY" text after the </div> that closes <div class="list-tags">.
        meta_block = ""
        mm = _LIST_META_RE.search(row)
        if mm:
            meta_block = mm.group(1)
        elif "list-meta" in row:
            meta_block = row[row.find("list-meta"):]
        # strip the inner <div class="list-tags">…</div> so a date can't be confused with a tag id
        meta_after_tags = re.sub(r'<div class="list-tags">.*?</div>', " ", meta_block, flags=re.S)
        md = _DATE_RE.search(_strip_tags(meta_after_tags))
        date_iso = _coerce_iso_date(md.group(1)) if md else None
        tags = tuple(html.unescape(_strip_tags(t)) for t in _TAG_RE.findall(meta_block) if _strip_tags(t))
        # Stable opinion id: prefer the page-path / PDF-filename slug (URL-decoded);
        # fall back to a slug of the title.
        slug_src = href
        if "/" in slug_src:
            slug_src = slug_src.rstrip("/").rsplit("/", 1)[-1]
        slug_src = re.sub(r"\.pdf$", "", urllib_parse_unquote(slug_src), flags=re.IGNORECASE)
        oid = slugify(html.unescape(slug_src)) or slugify(title)
        url = href
        if url.startswith("/"):
            url = "https://www.oiguskantsler.ee" + url
        out.append(
            Opinion(
                opinion_id=oid,
                title=title,
                url=url,
                date_iso=date_iso,
                law_names=(),  # filled by the caller via the law index (title scan)
                summary="",    # body is in the PDF; the follow-up adds PDF text extraction
                tags=tags,
            )
        )
    return out


def _strip_tags(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def _fetch_url(url: str, *, timeout: int = 30, cache_dir: Path | None = CACHE_DIR, sleep: float = FETCH_SLEEP_SECONDS) -> str | None:
    """GET ``url`` (with a small on-disk cache under ``krr_outputs/.cache/annotations/``).

    The cache is gitignored (the ``krr_outputs/.cache/`` rule) so repeat scrapes are fast.
    Returns the decoded body, or ``None`` on any error.
    """
    import urllib.request
    import urllib.error

    cache_file: Path | None = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / (sanitize_id(re.sub(r"^https?://", "", url)) + ".html")
        if cache_file.exists() and cache_file.stat().st_size > 200:
            try:
                return cache_file.read_text(encoding="utf-8")
            except OSError:
                pass
    req = urllib.request.Request(
        url, headers={"User-Agent": "law-ontology research crawler (contact henrik.aavik@gmail.com)"}
    )
    try:
        if sleep:
            time.sleep(sleep)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:  # noqa: BLE001 — log + skip
        print(f"    fetch error for {url}: {exc}")
        return None
    if cache_file is not None and len(body) > 200:
        try:
            cache_file.write_text(body, encoding="utf-8")
        except OSError:
            pass
    return body


def _fetch_bytes(
    url: str,
    *,
    timeout: int = 60,
    cache_dir: Path | None = CACHE_DIR,
    sleep: float = FETCH_SLEEP_SECONDS,
) -> bytes | None:
    """GET binary content with the same annotations cache policy as HTML pages."""
    import urllib.error
    import urllib.request

    cache_file: Path | None = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".pdf" if url.lower().split("?", 1)[0].endswith(".pdf") else ".bin"
        cache_file = cache_dir / (sanitize_id(re.sub(r"^https?://", "", url)) + suffix)
        if cache_file.exists() and cache_file.stat().st_size > 200:
            try:
                return cache_file.read_bytes()
            except OSError:
                pass
    req = urllib.request.Request(
        url, headers={"User-Agent": "law-ontology research crawler (contact henrik.aavik@gmail.com)"}
    )
    try:
        if sleep:
            time.sleep(sleep)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:  # noqa: BLE001
        print(f"    fetch error for {url}: {exc}")
        return None
    if cache_file is not None and len(body) > 200:
        try:
            cache_file.write_bytes(body)
        except OSError:
            pass
    return body


def _text_quality_ratio(text: str) -> float:
    visible = re.sub(r"\s+", "", text or "")
    if not visible:
        return 0.0
    valid = sum(1 for ch in visible if _PDF_TEXT_CHAR_RE.fullmatch(ch))
    return valid / len(visible)


def pdf_text_is_usable(text: str | None) -> tuple[bool, int, float]:
    """Return whether extracted PDF text is long and readable enough."""
    text = text or ""
    length = len(text.strip())
    ratio = _text_quality_ratio(text)
    return (
        length >= MIN_PDF_TEXT_CHARS and ratio >= MIN_PDF_TEXT_VALID_CHAR_RATIO,
        length,
        ratio,
    )


def extract_pdf_text_layer(pdf_bytes: bytes) -> tuple[str | None, str | None]:
    """Extract text from PDF bytes with an optional pdfminer installation."""
    try:
        from pdfminer.high_level import extract_text  # type: ignore[import-not-found]
    except ImportError:
        return None, 'pdfminer.six not installed; run pip install -e ".[pdf]"'
    try:
        text = extract_text(BytesIO(pdf_bytes)) or ""
    except Exception as exc:  # noqa: BLE001
        return None, f"pdfminer extraction failed: {exc}"
    return text, None


def probe_pdf_text_layers(
    opinions: list[Opinion],
    *,
    sample_size: int = PDF_PROBE_SAMPLE_SIZE,
    fetcher=_fetch_bytes,
    extractor=extract_pdf_text_layer,
    cache_dir: Path | None = CACHE_DIR,
    sleep: float = FETCH_SLEEP_SECONDS,
) -> dict:
    """Probe whether sampled Õiguskantsler PDFs have extractable text layers."""
    rows: list[dict] = []
    skipped_non_pdf = 0
    for op in opinions:
        if len(rows) >= sample_size:
            break
        if not op.url:
            continue
        if not _looks_like_pdf_url(op.url):
            skipped_non_pdf += 1
            continue
        row = {
            "opinion_id": op.opinion_id,
            "title": op.title,
            "url": op.url,
            "status": "pending",
            "text_length": 0,
            "valid_char_ratio": 0.0,
        }
        pdf_bytes = fetcher(op.url, cache_dir=cache_dir, sleep=sleep)
        if not pdf_bytes:
            row["status"] = "fetch_failed"
            rows.append(row)
            continue
        text, error = extractor(pdf_bytes)
        if error:
            row["status"] = "extraction_unavailable"
            row["warning"] = error
            rows.append(row)
            continue
        usable, length, ratio = pdf_text_is_usable(text)
        row["text_length"] = length
        row["valid_char_ratio"] = round(ratio, 3)
        row["status"] = "usable_text_layer" if usable else "unusable_or_scanned"
        rows.append(row)

    usable_count = sum(1 for r in rows if r["status"] == "usable_text_layer")
    sampled = len(rows)
    usable_ratio = usable_count / sampled if sampled else 0.0
    recommendation = (
        "pdf_text_layer_ok"
        if sampled and usable_ratio >= PDF_TEXT_LAYER_ACCEPTANCE_RATIO
        else "evaluate_ocr_before_full_ingestion"
    )
    return {
        "sample_size_requested": sample_size,
        "sampled": sampled,
        "non_pdf_urls_skipped": skipped_non_pdf,
        "usable_text_layer_count": usable_count,
        "usable_text_layer_ratio": round(usable_ratio, 3),
        "acceptance_ratio": PDF_TEXT_LAYER_ACCEPTANCE_RATIO,
        "recommendation": recommendation,
        "rows": rows,
    }


def write_pdf_probe_report(report: dict, path: Path = PDF_PROBE_REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    tmp.replace(path)
    return path


def scrape_recent_opinions(
    limit: int,
    *,
    listing_url: str = SEISUKOHAD_LISTING_URL,
    cache_dir: Path | None = CACHE_DIR,
    sleep: float = FETCH_SLEEP_SECONDS,
) -> list[Opinion]:
    """Fetch the ``limit`` most-recent opinions from the live seisukohad listing.

    Walks ``?page=0, 1, …`` until ``limit`` opinions are collected (or pages run out). Each
    page yields ≈20 opinions. ``limit <= 0`` returns ``[]`` without hitting the network.
    """
    if limit <= 0:
        return []
    collected: list[Opinion] = []
    page = 0
    while len(collected) < limit and page < 400:  # 400-page hard stop ⇒ ≈8000 opinions
        url = listing_url if page == 0 else f"{listing_url}?page={page}"
        body = _fetch_url(url, cache_dir=cache_dir, sleep=sleep)
        if not body:
            break
        page_opinions = parse_listing_html(body)
        if not page_opinions:
            break
        collected.extend(page_opinions)
        page += 1
    return collected[:limit]


# ---------------------------------------------------------------------------
# Annotation synthesis
# ---------------------------------------------------------------------------


@dataclass
class _OpinionResult:
    opinion_id: str
    title: str
    law_names: tuple[str, ...]
    resolved_iris: list[str] = field(default_factory=list)
    unresolved_names: list[str] = field(default_factory=list)
    annotations: list[dict] = field(default_factory=list)


def _xsd_date(value: str) -> dict:
    return {"@value": value, "@type": "xsd:date"}


def _xsd_anyuri(value: str) -> dict:
    return {"@value": value, "@type": "xsd:anyURI"}


def _annotation_text(opinion: Opinion) -> str:
    """Build a substantive ``annotationText`` for the opinion.

    Title + a paragraph of summary when we have one (the seed source); for the scrape
    source we have title + topic tags only (the PDF body is the follow-up), so we surface
    those as a short note. Never a 50 KB dump — at most ~2 000 chars.
    """
    parts = [opinion.title.strip()]
    if opinion.summary.strip():
        parts.append(opinion.summary.strip())
    elif opinion.tags:
        parts.append("Õiguskantsleri seisukoht. Teemad: " + ", ".join(opinion.tags) + ".")
    text = "\n\n".join(p for p in parts if p)
    return text[:2000].rstrip()


def build_annotations_for_opinion(opinion: Opinion, law_index: _LawIndex) -> _OpinionResult:
    """Resolve an opinion's law(s) and build one ``estleg:Annotation`` per resolved law.

    A law name that does not resolve to a corpus act node is recorded (``unresolved_names``)
    and produces no annotation — never a dangling ``estleg:annotates``. When an opinion's
    explicit ``law_names`` is empty (the scrape source), law references are extracted from
    the *title* instead. Within an opinion the resolved IRIs are deduped (one annotation per
    distinct annotated law).
    """
    result = _OpinionResult(opinion_id=opinion.opinion_id, title=opinion.title, law_names=opinion.law_names)

    # 1) explicit law names (seed source)
    iris: list[str] = []
    if opinion.law_names:
        for name in opinion.law_names:
            iri = law_index.resolve(name)
            if iri:
                if iri not in iris:
                    iris.append(iri)
            else:
                result.unresolved_names.append(name)
    else:
        # 2) extract from the title (scrape source)
        for iri in law_index.find_in_title(opinion.title):
            if iri not in iris:
                iris.append(iri)
        if not iris:
            result.unresolved_names.append(f"<no law name resolvable from title: {opinion.title!r}>")

    result.resolved_iris = list(iris)
    if not iris:
        return result

    text = _annotation_text(opinion)
    multi = len(iris) > 1
    for iri in iris:
        suffix = f"_{law_index.abbrev(iri)}" if multi else ""
        ann_iri = f"estleg:Annotation_OK_{sanitize_id(opinion.opinion_id)}{suffix}"
        node: dict = {
            "@id": ann_iri,
            "@type": ["owl:NamedIndividual", "estleg:Annotation"],
            "estleg:annotates": {"@id": iri},
            "estleg:annotationText": text,
            "estleg:annotationType": ANNOTATION_TYPE,
            "estleg:annotationSource": ANNOTATION_SOURCE,
            "rdfs:label": {"@value": f"Õiguskantsleri seisukoht: {opinion.title}", "@language": "et"},
        }
        if opinion.url:
            node["estleg:annotationSourceUrl"] = _xsd_anyuri(opinion.url)
        if opinion.date_iso:
            node["estleg:annotationDate"] = _xsd_date(opinion.date_iso)
        result.annotations.append(node)
    return result


# ---------------------------------------------------------------------------
# Sidecar writing
# ---------------------------------------------------------------------------


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def write_sidecar(annotations: list[dict], *, out_path: Path = SIDECAR_PATH) -> Path:
    """Write the annotations sidecar (``@context`` + ``owl:Ontology`` header + nodes).

    Mirrors the sanctions / amendments / provision_versions sidecars: a minimal
    ``owl:Ontology`` header node (no undefined ``estleg:total*`` count predicate — the
    per-run totals live in the coverage report) followed by the ``estleg:Annotation`` nodes.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    map_node = {
        "@id": "estleg:Annotations_Oiguskantsler_Map",
        "@type": ["owl:Ontology"],
        "rdfs:label": {
            "@value": f"Õiguskantsleri seisukohad — praktiku annotatsioonid ({len(annotations)} annotatsiooni)",
            "@language": "et",
        },
        "dc:source": ANNOTATION_SOURCE,
    }
    doc = {"@context": dict(CONTEXT), "@graph": [map_node, *annotations]}
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    tmp.replace(out_path)
    return out_path


def remove_sidecar(out_path: Path = SIDECAR_PATH) -> None:
    """Delete a previously-written sidecar (used when a run produces zero annotations)."""
    try:
        out_path.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Coverage report
# ---------------------------------------------------------------------------


def _write_coverage(
    results: list[_OpinionResult],
    *,
    start_perf: float,
    source_label: str,
    failures: list[str],
    path: Path = COVERAGE_PATH,
) -> None:
    all_input_files = list(iter_peep_files())
    kov_files = [p for p in all_input_files if "regulations/kov/" in str(p)]
    total_annotations = sum(len(r.annotations) for r in results)
    opinions_with_output = sum(1 for r in results if r.annotations)
    opinions_skipped = sum(1 for r in results if not r.annotations)
    annotated_laws = len({iri for r in results for iri in r.resolved_iris if r.annotations})
    unresolved_total = sum(len(r.unresolved_names) for r in results)
    # Skip-reason histogram + a short sample of unresolved source items.
    skip_reasons: dict[str, int] = {}
    unresolved_samples: list[str] = []
    for r in results:
        if not r.annotations:
            if r.unresolved_names:
                skip_reasons["law_name_not_resolved_to_corpus_node"] = (
                    skip_reasons.get("law_name_not_resolved_to_corpus_node", 0) + 1
                )
            else:
                skip_reasons["no_law_named_in_opinion"] = skip_reasons.get("no_law_named_in_opinion", 0) + 1
        for name in r.unresolved_names:
            if len(unresolved_samples) < 20:
                unresolved_samples.append(f"{r.opinion_id}: {name}")
    wall, rate, peak_mb = measure_runtime(start_perf, len(results))
    report = CoverageReport(
        pipeline="extract_annotations",
        run_timestamp=datetime.now(timezone.utc).isoformat(),
        pipeline_version=resolve_pipeline_version(),
        input_files_total=len(all_input_files),
        input_files_kov=len(kov_files),
        files_processed=len(results),          # opinions processed
        files_processed_kov=0,                  # opinions are not corpus files; no KOV subset
        files_with_output=opinions_with_output,
        files_with_output_kov=0,
        files_skipped=opinions_skipped,
        skip_reasons=skip_reasons,
        triples_emitted=total_annotations,      # counts estleg:Annotation nodes emitted
        triples_emitted_kov=0,
        unresolved_references=unresolved_total,  # law names that did not resolve to a corpus node
        wall_time_seconds=round(wall, 2),
        items_per_second=round(rate, 2),
        peak_memory_mb=round(peak_mb, 1),
        error_count=len(failures),
        failure_samples=(failures + unresolved_samples)[:20],
    )
    write_coverage_report(report, path)
    print(
        f"\nCoverage report -> {_rel(path)}  "
        f"(source={source_label}; opinions={len(results)}, annotations={total_annotations}, "
        f"annotated laws={annotated_laws}, unresolved law names={unresolved_total})"
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def collect_opinions(
    *,
    scrape: bool,
    limit: int,
    seed_path: Path,
    cache_dir: Path | None = CACHE_DIR,
    sleep: float = FETCH_SLEEP_SECONDS,
) -> tuple[list[Opinion], str]:
    """Return (opinions, source_label). ``limit <= 0`` ⇒ no opinions (and no network)."""
    if limit <= 0:
        return [], ("oiguskantsler.ee scrape" if scrape else f"seed file {_rel(seed_path)}")
    if scrape:
        return scrape_recent_opinions(limit, cache_dir=cache_dir, sleep=sleep), "oiguskantsler.ee scrape"
    opinions = load_seed_opinions(seed_path)[:limit]
    return opinions, f"seed file {_rel(seed_path)}"


def run(
    *,
    scrape: bool,
    limit: int,
    seed_path: Path = SEED_PATH,
    allow_partial: bool = False,
    krr_dir: Path | None = None,
    out_path: Path | None = None,
    coverage_path: Path | None = None,
    cache_dir: Path | None = CACHE_DIR,
    sleep: float = FETCH_SLEEP_SECONDS,
    start_perf: float | None = None,
) -> int:
    """Generate the annotations sidecar + coverage report; return a process exit code."""
    start_perf = time.perf_counter() if start_perf is None else start_perf
    krr_dir = KRR_DIR if krr_dir is None else krr_dir
    out_path = SIDECAR_PATH if out_path is None else out_path
    coverage_path = COVERAGE_PATH if coverage_path is None else coverage_path

    opinions, source_label = collect_opinions(
        scrape=scrape, limit=limit, seed_path=seed_path, cache_dir=cache_dir, sleep=sleep
    )
    if not opinions:
        print(f"No opinions to process (--limit {limit}). Source: {source_label}. Writing a zeroed coverage report.")
        remove_sidecar(out_path)
        _write_coverage([], start_perf=start_perf, source_label=source_label, failures=[], path=coverage_path)
        return 0

    print("=" * 70)
    print(f"Õiguskantsler annotation ingestion — {len(opinions)} opinion(s), source: {source_label}")
    for op in opinions:
        print(f"  [{op.date_iso or '????-??-??'}] {op.title[:80]}")
    print("=" * 70)

    law_index = build_law_index(krr_dir)
    print(f"Law index: {len(law_index.by_name)} name keys -> {len(set(law_index.by_name.values()))} act nodes")

    results: list[_OpinionResult] = []
    failures: list[str] = []
    for op in opinions:
        try:
            res = build_annotations_for_opinion(op, law_index)
        except Exception as exc:  # noqa: BLE001 — record + keep going
            failures.append(f"{op.opinion_id}: {exc}")
            if not allow_partial:
                # Mirror the other generators: sample runs are forgiving by default; the
                # flag exists for parity, and a hard failure here would just abort a run we
                # can otherwise complete with a recorded error.
                pass
            continue
        results.append(res)
        if res.annotations:
            print(f"  {op.opinion_id}: {len(res.annotations)} annotation(s) -> {', '.join(res.resolved_iris)}")
        else:
            why = "; ".join(res.unresolved_names) or "no law named"
            print(f"  {op.opinion_id}: SKIPPED (no resolvable law — {why})")

    annotations = [node for res in results for node in res.annotations]
    if annotations:
        written = write_sidecar(annotations, out_path=out_path)
        print(f"\nWrote {len(annotations)} estleg:Annotation node(s) -> {_rel(written)}")
    else:
        remove_sidecar(out_path)
        print("\nNo annotations produced (no opinion's law resolved to a corpus node) — no sidecar written.")

    _write_coverage(
        results, start_perf=start_perf, source_label=source_label, failures=failures, path=coverage_path
    )

    # Summary
    annotated_laws = sorted({iri for res in results for iri in res.resolved_iris if res.annotations})
    opinions_no_law = sum(1 for res in results if not res.annotations)
    print("\n" + "=" * 70)
    print(
        f"Done. {len(annotations)} estleg:Annotation node(s) across {len(annotated_laws)} annotated law(s) "
        f"from {len(results)} opinion(s); {opinions_no_law} opinion(s) had no resolvable law."
    )
    for res in results:
        flag = "" if res.annotations else "  (no resolvable law)"
        print(f"  {res.opinion_id:55s} annotations={len(res.annotations):2d}{flag}")
    print("=" * 70)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        metavar="N",
        help=f"Process at most N opinions from the active source (default {DEFAULT_LIMIT}). "
             "0 = none (writes a zeroed coverage report and no sidecar).",
    )
    parser.add_argument(
        "--seed",
        type=Path,
        default=SEED_PATH,
        metavar="PATH",
        help=f"Curated seed file of Õiguskantsler opinions (default {_rel(SEED_PATH)}). "
             "Ignored when --scrape is given.",
    )
    parser.add_argument(
        "--scrape",
        action="store_true",
        help="Instead of the seed file, scrape the live oiguskantsler.ee seisukohad listing "
             "for the --limit most-recent opinions (law refs extracted from titles only; the "
             "full-archive run with PDF-body parsing is the #199 follow-up).",
    )
    parser.add_argument(
        "--probe-pdfs",
        action="store_true",
        help="Fetch a sample of live Õiguskantsler opinion PDFs, test text-layer extraction "
             "with optional pdfminer.six, write an annotations_pdf_probe report, and exit.",
    )
    parser.add_argument(
        "--pdf-probe-sample-size",
        type=int,
        default=PDF_PROBE_SAMPLE_SIZE,
        metavar="N",
        help=f"With --probe-pdfs, sample N opinion URLs (default {PDF_PROBE_SAMPLE_SIZE}).",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Continue past opinions that raise an error during annotation synthesis "
             "(failures are recorded in the coverage report). Accepted for parity with the "
             "other generators; sample runs are forgiving by default.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help=f"Bypass the on-disk page cache ({_rel(CACHE_DIR)}). Only relevant with --scrape.",
    )
    parser.add_argument(
        "--no-sleep",
        action="store_true",
        help="Disable the polite inter-fetch delay (use only against a warm cache). --scrape only.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cache_dir = None if args.no_cache else CACHE_DIR
    sleep = 0.0 if args.no_sleep else FETCH_SLEEP_SECONDS
    if args.probe_pdfs:
        limit = max(args.limit, args.pdf_probe_sample_size)
        opinions = scrape_recent_opinions(limit, cache_dir=cache_dir, sleep=sleep)
        report = probe_pdf_text_layers(
            opinions,
            sample_size=args.pdf_probe_sample_size,
            cache_dir=cache_dir,
            sleep=sleep,
        )
        path = write_pdf_probe_report(report)
        print(
            f"PDF probe -> {_rel(path)}; usable text layers "
            f"{report['usable_text_layer_count']}/{report['sampled']} "
            f"({report['usable_text_layer_ratio']:.0%}); "
            f"recommendation={report['recommendation']}"
        )
        return 0
    return run(
        scrape=args.scrape,
        limit=args.limit,
        seed_path=args.seed,
        allow_partial=args.allow_partial,
        cache_dir=cache_dir,
        sleep=sleep,
    )


if __name__ == "__main__":
    raise SystemExit(main())
