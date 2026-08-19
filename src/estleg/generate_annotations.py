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
    opinion title plus usable PDF body text when available (Estonian texts cite laws by
    genitive name, e.g. "Karistusseadustiku § 381¹ …" → Karistusseadustik).

Either way the output is identical in shape: for each opinion we resolve every named law
(and, when the text cites ``§``, the matching provision IRIs) and emit **one**
``estleg:Annotation`` per document with one or more ``estleg:annotates`` targets (#459).
A law name that does not resolve to a corpus node is **skipped** (never a dangling
``annotates``) and recorded in the coverage report's skip reasons. IRI scheme:
``estleg:Annotation_OK_<opinion-slug>``.

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
    python3 scripts/generate_annotations.py --scrape --limit 5    # live scrape with PDF body text
    python3 scripts/generate_annotations.py --probe-pdfs --pdf-probe-sample-size 10
    python3 scripts/generate_annotations.py --scrape --limit 0    # full live archive
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import date
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote as urllib_parse_unquote

from estleg.estleg_common import (
    CONTEXT,
    KNOWN_ABBREVIATIONS,
    KRR_DIR,
    PINNED_RUN_TIMESTAMP,
    act_root_node,
    is_domain_individual,
    iter_peep_files,
    sanitize_id,
    slugify,
)
from estleg.kov_pipeline_coverage import (
    CoverageReport,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

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
ANNOTATION_TYPE = "interpretation"  # default when the title does not classify
_SECTION_CITE_RE = re.compile(
    r"(?:§+\s*|paragrahvi?\s+)(\d+)(?:([¹²³⁴⁵⁶⁷⁸⁹⁰]+)|[_^](\d+))?",
    re.IGNORECASE,
)
_SUPERSCRIPT_TO_DIGIT = str.maketrans("¹²³⁴⁵⁶⁷⁸⁹⁰", "1234567890")

SEISUKOHAD_LISTING_URL = "https://www.oiguskantsler.ee/seisukohad-ja-algatused/seisukohad"
FETCH_SLEEP_SECONDS = 0.4

DEFAULT_LIMIT = 5
PDF_PROBE_SAMPLE_SIZE = 10
MIN_PDF_TEXT_CHARS = 500
MIN_PDF_TEXT_VALID_CHAR_RATIO = 0.30
PDF_TEXT_LAYER_ACCEPTANCE_RATIO = 0.80
# Sanity cap only. ``find_in_body`` now does a single cheap leftmost-longest regex pass over
# the WHOLE normalised body, so there is no 50K match-surface truncation hiding beyond-cap
# references (issue #233 — a citation past the old 50K is now found). The cap is kept solely
# to bound peak memory against a pathological multi-MB text layer; legitimate Õiguskantsler
# opinion bodies are far below it, so it never elides a real citation.
PDF_BODY_SCAN_MAX_CHARS = 2_000_000
_PDF_TEXT_CHAR_RE = re.compile(r"[0-9A-Za-zÕÄÖÜõäöüŠŽšž]")

# Õiguskantsler letters open with a standard office-metadata header that must NOT bleed into
# ``estleg:annotationText`` (issue #324). The header is a two-line reference block —
# ``[Lõppvastus/Vastus/…] Teie <date|"kuupäev"> nr <ref> … {Õiguskantsler|Meie} <date> nr
# <ref>`` — optionally preceded by a short recipient/subject block and optionally followed by a
# salutation line (``Lugupeetud/Austatud <name>``). Both tiers are anchored at the document
# head and REQUIRE the ``Teie … nr`` reference pattern, so a genuine opinion body that merely
# contains the word "Teie" later on is never truncated. (Applied to the RAW text-layer string,
# before whitespace collapse, because the match relies on the original line breaks.)
_LETTER_HEADER_PREFIX = (
    r"(?:L[õo]ppvastus\s+|Vastus\s+|Seisukoht\s+|Arvamus\s+|Ettepanek\s+|M[äa]rgukiri\s+)?Teie\b"
)
_LETTER_HEADER_SALUTATION = r"(?:Lugupeetud|Lugupeetav|Austatud|Auv[äa]{1,2}rt)\b[^\n]*"
# Tier A: from the head (optionally past a short recipient/subject block) through the
# salutation line — the common, complete letter header.
_LETTER_HEADER_WITH_SALUTATION_RE = re.compile(
    r"^.{0,400}?" + _LETTER_HEADER_PREFIX + r".*?\bnr\b.*?" + _LETTER_HEADER_SALUTATION + r"[ \t]*\n",
    re.DOTALL | re.IGNORECASE,
)
# Tier B: a salutation-less header — strip just the two reference lines (and any wrapped
# continuation reference-number lines that trail the second one).
_LETTER_HEADER_REFERENCE_RE = re.compile(
    r"^.{0,200}?" + _LETTER_HEADER_PREFIX + r".{0,80}?\bnr\b.*?(?:Õiguskantsler|Meie)\b.*?\bnr\b[^\n]*\n"
    r"(?:[ \t]*[0-9][0-9/\-,\s]*\n)*",
    re.DOTALL | re.IGNORECASE,
)


def _strip_letter_header(text: str) -> str:
    """Drop the standard Õiguskantsler letter header from the start of a raw PDF body.

    Tries the full header (ending at the salutation line) first; if that does not match, falls
    back to stripping the salutation-less reference block. Returns ``text`` unchanged when no
    header is present (e.g. an ``Ettepanek`` that opens straight into substance), so non-letter
    documents and already-clean bodies are never altered.
    """
    stripped = _LETTER_HEADER_WITH_SALUTATION_RE.sub("", text, count=1)
    if stripped == text:
        stripped = _LETTER_HEADER_REFERENCE_RE.sub("", text, count=1)
    return stripped


# Act-level @type values that legitimately represent the act node an annotation should
# point at; provision-level nodes intentionally do NOT appear here so the fallback never
# silently picks a section node. (Mirrors generate_harmonisation_links._ACT_LEVEL_TYPES.)
_ACT_LEVEL_TYPES = frozenset({"estleg:Act", "estleg:Map"})

# An ``owl:Ontology`` @id we will accept as a link target: an ``estleg:`` CURIE that is more
# than the bare prefix (some legacy multipart peeps carry ``estleg:`` or the bare namespace
# IRI on the ontology node — those are not addressable, so we reject them).
_BARE_NS = {"estleg:", "https://w3id.org/estleg/"}
_MAP_IRI_RE = re.compile(r"^estleg:[^\s]+_Map(?:_\d{4})?$")
# A per-osa act node carries its provision range in the IRI tail: ``…_OsaN_<start>_<end>``
# (e.g. ``estleg:volaoigusseadus_Osa10_1005_1067`` -> start 1005). Used to pick the §§1-range
# osa among same-title rank-1 candidates instead of the lexicographic first (issue #379).
_OSA_RANGE_RE = re.compile(r"_Osa\d+_(\d+)_\d+$")


def _osa_range_start(iri: str) -> int | None:
    """Return the provision-range START of an ``…_OsaN_start_end`` osa IRI, else ``None``."""
    m = _OSA_RANGE_RE.search(iri)
    return int(m.group(1)) if m else None


def _looks_like_pdf_url(url: str) -> bool:
    """Return whether ``url`` points directly at a PDF resource."""
    return url.split("?", 1)[0].lower().endswith(".pdf")


def _short_hash(value: str, *, length: int = 8) -> str:
    # Stable disambiguator only; collisions are unlikely at the current archive scale.
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


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
    # One compiled, token-bounded, longest-first alternation over every registered name
    # variant (built lazily on the first title/body scan, then reused across every opinion).
    _body_re: re.Pattern[str] | None = field(default=None, repr=False)

    def resolve(self, name: str) -> str | None:
        return self.by_name.get(_norm_name(name)) or self.by_slug.get(name.strip())

    def abbrev(self, iri: str) -> str:
        return self.abbrev_by_iri.get(iri, "LAW")

    def _ensure_body_re(self) -> re.Pattern[str]:
        """Build (once) the token-bounded leftmost-longest law-name alternation.

        Every key in :attr:`by_name` — the normalised nominative title plus the
        :func:`_genitive_variants` forms registered alongside it — becomes one alternative,
        ordered LONGEST-FIRST so Python ``re``'s leftmost-first alternation yields the longest
        law name at each position (a shorter law name that is a substring of a longer matched
        one is consumed by the longer span, never separately matched). The whole alternation
        is wrapped in word-character lookarounds (:data:`_BODY_WORD`) so a name matches only as
        a standalone token, never embedded in a larger word. The map is already normalised, so
        each matched span maps straight back to its IRI via :attr:`by_name`.
        """
        if self._body_re is None:
            ordered = self._ordered_names or sorted(self.by_name, key=len, reverse=True)
            alternation = "|".join(re.escape(name) for name in ordered)
            pattern = (
                rf"(?<!{_BODY_WORD})(?:{alternation})(?!{_BODY_WORD})"
                if alternation
                else r"(?!x)x"  # matches nothing when the index is empty
            )
            self._body_re = re.compile(pattern)
        return self._body_re

    def _scan_bounded(self, hay: str) -> list[str]:
        """Token-bounded, leftmost-longest scan of ``hay`` -> deduped act-IRIs.

        The shared engine behind :meth:`find_in_title` and :meth:`find_in_body`: one
        :meth:`_ensure_body_re` pass whose longest-first alternation and ``_BODY_WORD``
        lookarounds mean a name is emitted only as a standalone whole token, so a shorter law
        name that is merely a substring of a longer matched name (consumed by the longer span)
        or embedded inside a larger word is never separately matched. IRIs are returned deduped,
        in first-occurrence order.
        """
        found: list[str] = []
        seen: set[str] = set()
        for match in self._ensure_body_re().finditer(hay):
            iri = self.by_name[match.group(0)]
            if iri not in seen:
                seen.add(iri)
                found.append(iri)
        return found

    def find_in_title(self, title: str) -> list[str]:
        """Return the act-IRIs whose (genitive-aware) name occurs in ``title``.

        Title evidence is authoritative, but matched through the SAME token-bounded,
        leftmost-longest scan as :meth:`find_in_body` (see :meth:`_scan_bounded`): the longest
        registered name at each position wins ("kohaliku omavalitsuse korralduse seaduse" over a
        stray "seaduse"), and — crucially — a shorter act title that is only a substring of a
        longer matched one ("asjaõigusseadus" inside "asjaõigusseaduse rakendamise seadus",
        "karistusseadustiku" inside "karistusseadustiku rakendamise seaduse muutmine") is
        consumed by the longer span and never separately emitted. The earlier raw ``in``
        substring scan leaked the shorter base act as a spurious second match (issue #598).
        Deduped, in first-occurrence order.
        """
        return self._scan_bounded(_norm_name(title))

    def find_in_body(self, text: str) -> list[str]:
        """Return act-IRIs whose name occurs in ``text`` as a standalone token.

        A single leftmost-longest, token-bounded regex pass (see :meth:`_scan_bounded` /
        :meth:`_ensure_body_re`) over the FULL normalised body. Estonian legal opinions
        legitimately reference laws by bare name ("vastavalt võlaõigusseadusele", "põhiseadusega
        vastuolus") with no adjacent ``§``/number, so — unlike the earlier citation-cue gating —
        every standalone whole-token match counts; no ``§``/citation cue is required. Because
        matches are non-overlapping and longest-first, a shorter law name that is a substring of
        a longer matched law name at the same position is naturally NOT separately matched (the
        longer span consumes it), which drops the "substring of a longer law title" false
        positive; and the word-boundary lookarounds drop names embedded inside a larger word.
        IRIs are returned deduped, in first-occurrence order, mirroring :meth:`find_in_title`.
        Genitive forms keep working because each genitive variant (e.g. "võlaõigusseaduse") is
        itself a registered token that matches whole, while the nominative ("võlaõigusseadus")
        simply does not match inside it (the trailing "e" trips the trailing-word-char
        lookahead) — which is correct.
        """
        if not text:
            return []
        return self._scan_bounded(_norm_name(text))


# ---------------------------------------------------------------------------
# Name normalisation + genitive handling
# ---------------------------------------------------------------------------


_DIACRITIC_MAP = {"õ": "o", "ä": "a", "ö": "o", "ü": "u", "š": "s", "ž": "z"}

# A "word" character for the token-boundary lookarounds in ``_LawIndex._ensure_body_re``:
# digits plus ASCII and Estonian letters. ``_norm_name`` transliterates the diacritics out of
# the haystack before matching, so in practice only the ASCII subset can ever appear at a
# boundary; the Estonian letters are kept for defensiveness against any un-normalised input.
_BODY_WORD = r"[0-9A-Za-zÀ-ÿõäöüšžÕÄÖÜŠŽ]"


def _norm_name(value: str) -> str:
    """Lowercase + Estonian diacritic transliteration + parenthetical strip + ws collapse."""
    s = value.lower()
    for src, dst in _DIACRITIC_MAP.items():
        s = s.replace(src, dst)
    s = re.sub(r"\s*\([^)]*\)\s*", " ", s)  # drop every "(Riigi Teataja …)" parenthetical
    return " ".join(s.split())


def _clean_dc_source(value: object) -> str | None:
    """Pull a plain title string from a ``dc:source`` value (string or list); strip suffix."""
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, str):
        return None
    cleaned = " ".join(re.sub(r"\s*\([^)]*\)\s*", " ", value).split())
    return cleaned or None


def _split_compound_title(value: str) -> list[str]:
    """Split an "A + B" compound title into the individual law names it joins.

    A few corpus ``dc:source`` titles join two distinct law names with a spaced " + "
    separator (e.g. "Tulumaksuseadus + Käibemaksuseadus"); each side names its own act
    and must be indexed separately so a lookup for either half resolves. Splits ONLY on
    a "+" surrounded by whitespace, so a name carrying a bare "+" is left intact, and
    returns the (stripped, non-empty) parts — a single-element list holding the value
    unchanged when there is no spaced "+", so single-name titles are unaffected.
    """
    parts = [p.strip() for p in re.split(r"\s+\+\s+", value)]
    return [p for p in parts if p]


# Productive Estonian singular oblique-case endings attached to a ``…seadus`` / ``…seadustik``
# genitive stem, in the order they appear in legal prose. The genitive stem is the nominative
# plus the genitive marker (``seadus`` -> ``seaduse``, ``seadustik`` -> ``seadustiku``); each
# of these endings then attaches to that stem. Registering every form as its own standalone
# token lets the word-bounded body scan keep genuine bare-name references in any common case —
# "vastavalt võlaõigusseaduse**le**" (allative), "põhiseadusega vastuolus" (comitative),
# "võlaõigusseadus**est** tulenevalt" (elative) — without ever matching a name embedded in a
# larger word. (Mirrors the case-ending handling in extract_institutional_competence.py.)
_SEADUS_CASE_ENDINGS: tuple[str, ...] = (
    "",     # genitive itself (the bare stem: seaduse / seadustiku)
    "sse",  # illative   (seadusesse / seadustikusse)
    "s",    # inessive   (seaduses)
    "st",   # elative    (seadusest)
    "le",   # allative   (seadusele)
    "l",    # adessive   (seadusel)
    "lt",   # ablative   (seaduselt)
    "ks",   # translative(seaduseks)
    "ni",   # terminative(seaduseni)
    "na",   # essive     (seadusena)
    "ta",   # abessive   (seaduseta)
    "ga",   # comitative (seadusega)
)


def _genitive_variants(norm_name: str) -> list[str]:
    """Return inflected (genitive-stem + case-ending) forms of a normalised law name.

    Estonian opinions cite laws by an inflected trailing word — most often the genitive
    ("…seaduse §…", "…seadustiku muutmine"), but also the allative ("…seadusele"), comitative
    ("…seadusega"), elative ("…seadusest") and the other oblique cases. The corpus stores the
    nominative title, so we register the genitive *stem* of the productive ``…seadus`` /
    ``…seadustik`` endings plus every common singular case suffix (:data:`_SEADUS_CASE_ENDINGS`)
    as standalone tokens. Any other ending gets no alias (only the nominative is indexed). The
    partitive ("…seadust") is deliberately NOT registered: it is a prefix of "…seadustik" and
    would create cross-law ambiguity, and the word-bounded body scan already keeps the common
    genitive/allative/comitative prose forms.
    """
    if norm_name.endswith("seadustik"):
        stem = norm_name + "u"             # seadustik -> seadustiku
    elif norm_name.endswith("seadus"):
        stem = norm_name + "e"             # seadus -> seaduse
    else:
        # e.g. an already-plural "…seaduste" name: no productive singular stem to inflect.
        return []
    return [stem + ending for ending in _SEADUS_CASE_ENDINGS]


# ---------------------------------------------------------------------------
# Estonian abbreviation tokens (used by the annotationText sentence-trimmer)
# ---------------------------------------------------------------------------

# Curated from Estonian legal citation/document text; expand when live annotation runs
# surface another common token that precedes non-sentence ordinal periods. Consumed by the
# ``annotationText`` sentence-boundary trimmer (``_is_false_estonian_abbreviation_boundary``)
# so an ordinal/abbreviation period ("§ 47 lg 1." , "nr 12.") is not mistaken for a real
# sentence end.
_ESTONIAN_ABBREVIATION_TOKENS = frozenset(
    {"art", "jt", "lg", "lk", "nn", "nr", "nt", "p", "ptk", "vt"}
)
_ESTONIAN_ORDINAL_PREFIX_TOKENS = _ESTONIAN_ABBREVIATION_TOKENS | frozenset({"osa"})


# ---------------------------------------------------------------------------
# Building the law index from the corpus
# ---------------------------------------------------------------------------


def _ontology_node_iri(graph: list, *, prefer_map: bool = True) -> str | None:
    """Return the addressable act-node IRI from a peep ``@graph``.

    Preferred path: the act-root node's ``@id`` (`estleg:Act` / regulation
    class, #435). Restricted fallback: ``graph[0]`` only if it is itself
    an act-level node — never an arbitrary provision node.
    """
    root = act_root_node({"@graph": graph})
    if root is not None:
        nid = root.get("@id")
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

    For a title shared by several peep files (multipart laws), a ``…_Map`` IRI wins. Among
    equally-ranked rank-1 (non-``_Map_``) per-osa candidates — laws like KarS/VÕS/AOS/TsÜS that
    have no whole-law ``_Map_`` peep — the osa with the LOWEST provision-range start wins, so
    the §§1-range node is chosen rather than the lexicographic first (``_Osa10_`` sorts before
    ``_Osa1_``; issue #379). Candidates with no parseable osa range, or an equal start, fall back
    to the first by sorted filename (deterministic). NB: an osa node is still a part, not the
    whole act — adding ``_Map_`` peeps for these laws (and a SHACL ``AnnotationShape`` constraint
    that ``annotates`` targets aren't ``estleg:Part``) is the proper fix, tracked separately.
    """
    idx = _LawIndex()
    # First pass: collect (slug, ont_iri, dc_source) keeping the best IRI per dc:source.
    # Priority key per norm_source: (rank, osa_range_start) — lower is better. ``rank`` is 0 for
    # a ``_Map_`` IRI, 1 otherwise; ``osa_range_start`` is the ``…_OsaN_<start>_…`` start (only
    # meaningful for rank-1 osa nodes), with ``inf`` when absent so a ranged osa beats an
    # unranged candidate and the lowest-§ osa wins among ranged ones.
    candidates: dict[str, tuple[str, tuple[int, float]]] = {}  # norm_source -> (iri, priority)
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
            if isinstance(node, dict) and is_domain_individual(node):
                src = _clean_dc_source(node.get("dc:source"))
                break
        if not src:
            continue
        rank = 0 if _MAP_IRI_RE.match(iri) else 1
        range_start = _osa_range_start(iri)
        priority = (rank, float(range_start) if range_start is not None else float("inf"))
        # A few titles join two law names with " + " (e.g. "Tulumaksuseadus +
        # Käibemaksuseadus"); register each half so a lookup for either resolves (#606).
        for norm in _split_compound_title(_norm_name(src)):
            prev = candidates.get(norm)
            if prev is None or priority < prev[1]:
                candidates[norm] = (iri, priority)

    idx.by_slug = dict(slug_to_iri)
    for norm, (iri, _priority) in candidates.items():
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
        known_abbrev = full_by_iri.get(iri)
        abbrev = sanitize_id(known_abbrev) if known_abbrev else ""
        if not abbrev or abbrev == "Unknown":
            abbrev = _abbrev_from_iri(iri)
        idx.abbrev_by_iri[iri] = abbrev

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
    r'<div class="views-row">\s*<div class="list-item doc">(.*?)</div>\s*</div>\s*</div>', re.DOTALL
)
_LIST_TITLE_RE = re.compile(r'<div class="list-title">(.*?)</div>\s*<div class="list-body">', re.DOTALL)
_LIST_META_RE = re.compile(r'<div class="list-meta">(.*?)</div>\s*$', re.DOTALL)
_ANCHOR_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_DATE_RE = re.compile(r'(\d{1,2}\.\d{1,2}\.\d{4})')
_TAG_RE = re.compile(r'<a[^>]+class="tag"[^>]*>(.*?)</a>', re.DOTALL)


def parse_listing_html(html_text: str) -> list[Opinion]:
    """Parse one ``/seisukohad-ja-algatused/seisukohad`` page into :class:`Opinion` records.

    Law references are extracted after optional PDF-body enrichment by the caller (it has the
    law index); here we just shape (title, url, date, tags). Rows with no usable title are skipped.
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
        meta_after_tags = re.sub(r'<div class="list-tags">.*?</div>', " ", meta_block, flags=re.DOTALL)
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
                summary="",    # body is filled later from the PDF text layer when usable
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
    import urllib.error
    import urllib.request

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
    except (urllib.error.URLError, OSError, ValueError) as exc:
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
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"    fetch error for {url}: {exc}")
        return None
    if cache_file is not None and len(body) > 200:
        try:
            cache_file.write_bytes(body)
        except OSError:
            pass
    return body


def disambiguate_duplicate_opinion_ids(opinions: list[Opinion]) -> list[Opinion]:
    """Append a stable source hash when the live archive reuses a title/PDF slug.

    Deterministic and input-order independent: the per-opinion source hash is derived from
    content (url, or date/title) with no list position, and residual ``_2``/``_3`` ordinals
    for truly identical url/date/title rows are assigned by a STABLE sort of the colliding
    group, not by arrival order. Equivalent inputs in any order yield identical IRIs.
    """
    counts: dict[str, int] = {}
    for op in opinions:
        counts[op.opinion_id] = counts.get(op.opinion_id, 0) + 1
    if not any(count > 1 for count in counts.values()):
        return opinions

    # First pass: assign each colliding opinion a content-derived (position-independent) id,
    # bucketing any residual collisions so we can break ties by a stable sorted rank below.
    interim_ids: list[str] = []
    collisions: dict[str, list[int]] = {}
    for pos, op in enumerate(opinions):
        if counts[op.opinion_id] > 1:
            source_key = op.url or f"{op.date_iso or ''}|{op.title}"
            interim_id = f"{op.opinion_id}_{_short_hash(source_key)}"
        else:
            interim_id = op.opinion_id
        interim_ids.append(interim_id)
        collisions.setdefault(interim_id, []).append(pos)

    # Second pass: for any interim id shared by >1 row (identical url, or identical
    # date+title), sort the colliding rows by a stable composite key and append the ordinal
    # by sorted rank so the same set of inputs always maps the same row to the same suffix.
    final_ids: list[str] = list(interim_ids)
    for interim_id, positions in collisions.items():
        if len(positions) <= 1:
            continue
        ordered = sorted(
            positions,
            key=lambda p: (
                opinions[p].url,
                opinions[p].date_iso or "",
                opinions[p].title,
                _short_hash(
                    f"{opinions[p].url}|{opinions[p].date_iso or ''}|"
                    f"{opinions[p].title}|{opinions[p].summary}"
                ),
            ),
        )
        for rank, pos in enumerate(ordered):
            final_ids[pos] = interim_id if rank == 0 else f"{interim_id}_{rank + 1}"

    return [
        op if final_ids[pos] == op.opinion_id else replace(op, opinion_id=final_ids[pos])
        for pos, op in enumerate(opinions)
    ]


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


def _normalise_pdf_text(text: str) -> str:
    """Collapse PDF text-layer whitespace and return the FULL normalised body.

    ``find_in_body`` now does one cheap leftmost-longest regex pass over whatever this returns,
    so the whole document is scanned and a law cited anywhere in it — including past the old
    50K match-surface limit (issue #233) — is found. No prefix-slice / citation-window
    truncation is performed; only the :data:`PDF_BODY_SCAN_MAX_CHARS` *sanity* cap is applied,
    to bound peak memory against a pathological multi-MB text layer. Real Õiguskantsler bodies
    are far below it, so it never elides a genuine citation. (The unrelated ~2000-char
    ``annotationText`` display truncation lives in :func:`_truncate_to_sentence`.)

    The standard Õiguskantsler letter header (office metadata: ``Teie … nr … {Õiguskantsler|
    Meie} … nr … [Lugupeetud …]``) is stripped first, on the raw text, so neither the body scan
    nor ``annotationText`` opens with boilerplate (issue #324). Citations live in the body, so
    dropping the header never loses a law reference.
    """
    normalised = re.sub(r"\s+", " ", _strip_letter_header(text)).strip()
    return normalised[:PDF_BODY_SCAN_MAX_CHARS]


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


def attach_pdf_text_layers(
    opinions: list[Opinion],
    *,
    fetcher=_fetch_bytes,
    extractor=extract_pdf_text_layer,
    cache_dir: Path | None = CACHE_DIR,
    sleep: float = FETCH_SLEEP_SECONDS,
) -> tuple[list[Opinion], dict]:
    """Attach usable PDF text-layer body text to scraped opinions.

    Returns the enriched opinions and lightweight extraction stats for operator output.
    The original opinion is preserved when the URL is not a PDF, the fetch fails, or the
    text layer fails quality checks.
    """
    enriched: list[Opinion] = []
    stats = {
        "pdf_urls": 0,
        "usable_text_layers": 0,
        "fetch_failed": 0,
        "extraction_unavailable": 0,
        "unusable_or_scanned": 0,
        "non_pdf_urls": 0,
    }
    for op in opinions:
        if not op.url or not _looks_like_pdf_url(op.url):
            stats["non_pdf_urls"] += 1
            enriched.append(op)
            continue
        stats["pdf_urls"] += 1
        pdf_bytes = fetcher(op.url, cache_dir=cache_dir, sleep=sleep)
        if not pdf_bytes:
            stats["fetch_failed"] += 1
            enriched.append(op)
            continue
        text, error = extractor(pdf_bytes)
        if error:
            stats["extraction_unavailable"] += 1
            enriched.append(op)
            continue
        usable, _length, _ratio = pdf_text_is_usable(text)
        if not usable:
            stats["unusable_or_scanned"] += 1
            enriched.append(op)
            continue
        stats["usable_text_layers"] += 1
        enriched.append(replace(op, summary=_normalise_pdf_text(text or "")))
    return enriched, stats


def scrape_recent_opinions(
    limit: int,
    *,
    listing_url: str = SEISUKOHAD_LISTING_URL,
    cache_dir: Path | None = CACHE_DIR,
    sleep: float = FETCH_SLEEP_SECONDS,
) -> list[Opinion]:
    """Fetch live seisukohad listing opinions.

    Walks ``?page=0, 1, …`` until ``limit`` opinions are collected (or pages run out). Each
    page yields ≈20 opinions. ``limit <= 0`` means unbounded archive mode, guarded by the
    hard page cap below.
    """
    collected: list[Opinion] = []
    page = 0
    target = None if limit <= 0 else limit
    while (target is None or len(collected) < target) and page < 400:  # ≈8000 opinions max
        url = listing_url if page == 0 else f"{listing_url}?page={page}"
        body = _fetch_url(url, cache_dir=cache_dir, sleep=sleep)
        if not body:
            break
        page_opinions = parse_listing_html(body)
        if not page_opinions:
            break
        collected.extend(page_opinions)
        page += 1
    selected = collected if target is None else collected[:target]
    return disambiguate_duplicate_opinion_ids(selected)


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


_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?]\s+")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _is_false_estonian_abbreviation_boundary(head: str, boundary: int) -> bool:
    """Return True for period-space matches that are abbreviation punctuation."""
    if head[boundary] != ".":
        return False

    before_tokens = _TOKEN_RE.findall(head[:boundary])
    if not before_tokens:
        return False
    before = before_tokens[-1].lower()
    if before in _ESTONIAN_ABBREVIATION_TOKENS:
        return True

    after_match = _TOKEN_RE.search(head[boundary + 1 :])
    after = after_match.group(0) if after_match else ""
    if (
        before.isdigit()
        and len(before_tokens) >= 2
        and before_tokens[-2].lower() in _ESTONIAN_ORDINAL_PREFIX_TOKENS
    ):
        return after[:1].islower()
    return False


def _late_sentence_boundary(head: str, min_index: int) -> int | None:
    for match in reversed(list(_SENTENCE_BOUNDARY_RE.finditer(head))):
        boundary = match.start()
        if boundary <= min_index:
            break
        if not _is_false_estonian_abbreviation_boundary(head, boundary):
            return boundary
    return None


def _truncate_to_sentence(text: str, max_chars: int) -> str:
    """Trim long annotation text at a sentence boundary where practical."""
    if len(text) <= max_chars:
        return text.rstrip()
    head = text[:max_chars]
    last_boundary = _late_sentence_boundary(head, int(max_chars * 0.7))
    if last_boundary is not None:
        return head[: last_boundary + 1].rstrip()
    last_space = head.rfind(" ")
    return (head[:last_space] if last_space > 0 else head).rstrip() + "…"


def classify_annotation_type(title: str, text: str = "") -> str:
    """Pick an AnnotationShape enum value from the document title (#459)."""
    hay = f"{title}\n{text}".casefold()
    if any(token in hay for token in ("hoiatus", "hoiatab")):
        return "warning"
    if any(token in hay for token in ("soovitus", "soovitab", "ettepanek")):
        return "guidance"
    if "kommentaar" in hay:
        return "commentary"
    if any(token in hay for token in ("juhend", "praktika", "selgitus")):
        return "practice_note"
    if "arvamus" in hay:
        return "commentary"
    return "interpretation"


def extract_section_numbers(text: str) -> list[str]:
    """Return cited section numbers (``12``, ``381_1``) in first-seen order."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _SECTION_CITE_RE.finditer(text or ""):
        base = match.group(1)
        extra = match.group(3) or ""
        if match.group(2):
            extra = match.group(2).translate(_SUPERSCRIPT_TO_DIGIT)
        key = f"{base}_{extra}" if extra else base
        if key not in seen:
            seen.add(key)
            found.append(key)
    return found


def act_iri_prefix(iri: str) -> str:
    if not iri.startswith("estleg:"):
        return ""
    return re.sub(r"_Map(?:_\d{4})?$", "", iri)


def provision_iris_for_acts(
    act_iris: list[str], text: str, provision_ids: set[str] | None
) -> list[str]:
    """Prefer cited ``_Par_`` nodes when they exist in ``provision_ids``."""
    if not provision_ids:
        return list(act_iris)
    sections = extract_section_numbers(text)
    if not sections:
        return list(act_iris)
    out: list[str] = []
    seen: set[str] = set()
    for act in act_iris:
        prefix = act_iri_prefix(act)
        matched = False
        if prefix:
            for section in sections:
                candidate = f"{prefix}_Par_{section}"
                if candidate in provision_ids and candidate not in seen:
                    out.append(candidate)
                    seen.add(candidate)
                    matched = True
        if not matched and act not in seen:
            out.append(act)
            seen.add(act)
    return out


def _annotates_value(iris: list[str]) -> dict | list[dict]:
    refs = [{"@id": iri} for iri in iris]
    if len(refs) == 1:
        return refs[0]
    return refs


def annotates_iris(node: dict) -> list[str]:
    value = node.get("estleg:annotates")
    if isinstance(value, dict) and isinstance(value.get("@id"), str):
        return [value["@id"]]
    if isinstance(value, list):
        return [
            item["@id"]
            for item in value
            if isinstance(item, dict) and isinstance(item.get("@id"), str)
        ]
    return []


def _annotation_text(opinion: Opinion) -> str:
    """Build a substantive ``annotationText`` for the opinion.

    Title + a paragraph of summary when we have one (seed source or scraped PDF text layer);
    otherwise, scraped topic tags become a short note. Never a 50 KB dump — at most
    ~2 000 chars.
    """
    parts = [opinion.title.strip()]
    if opinion.summary.strip():
        parts.append(opinion.summary.strip())
    elif opinion.tags:
        parts.append("Õiguskantsleri seisukoht. Teemad: " + ", ".join(opinion.tags) + ".")
    text = "\n\n".join(p for p in parts if p)
    return _truncate_to_sentence(text, 2000)


def build_annotations_for_opinion(
    opinion: Opinion,
    law_index: _LawIndex,
    *,
    provision_ids: set[str] | None = None,
) -> _OpinionResult:
    """Resolve an opinion's law(s) and build one ``estleg:Annotation`` (#459).

    A law name that does not resolve to a corpus act node is recorded (``unresolved_names``)
    and produces no annotation — never a dangling ``estleg:annotates``. When an opinion's
    explicit ``law_names`` is empty (the scrape source), law references are extracted from
    the *title* instead. Cited ``§`` numbers are remapped onto provision IRIs when those
    nodes exist in ``provision_ids``.
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
        # 2) extract from the title (authoritative) unioned with body evidence (scrape
        # source). Both accept a standalone whole-token law-name match: a leftmost-longest,
        # word-bounded scan (see find_in_body) keeps genuine bare-name references — Estonian
        # opinions cite laws by bare name ("vastavalt võlaõigusseadusele") with no adjacent
        # § — while dropping substring-of-a-longer-title and embedded-in-a-word false hits.
        for iri in law_index.find_in_title(opinion.title):
            if iri not in iris:
                iris.append(iri)
        for iri in law_index.find_in_body(opinion.summary):
            if iri not in iris:
                iris.append(iri)
        if not iris:
            result.unresolved_names.append(
                f"<no law name resolvable from title/body: {opinion.title!r}>"
            )

    result.resolved_iris = list(iris)
    if not iris:
        return result

    text = _annotation_text(opinion)
    targets = provision_iris_for_acts(iris, f"{opinion.title}\n{text}", provision_ids)
    node = {
        "@id": f"estleg:Annotation_OK_{sanitize_id(opinion.opinion_id)}",
        "@type": ["owl:NamedIndividual", "estleg:Annotation"],
        "estleg:annotates": _annotates_value(targets),
        "estleg:annotationText": text,
        "estleg:annotationType": classify_annotation_type(opinion.title, text),
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


def collect_provision_ids(krr_dir: Path = KRR_DIR) -> set[str]:
    """Collect ``estleg:*_Par_*`` @ids from root law peeps."""
    ids: set[str] = set()
    for path in sorted(krr_dir.glob("*_peep.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for node in doc.get("@graph") or []:
            if not isinstance(node, dict):
                continue
            nid = node.get("@id")
            if isinstance(nid, str) and "_Par_" in nid:
                ids.add(nid)
    return ids


def remint_annotation_sidecar(
    *,
    in_path: Path = SIDECAR_PATH,
    out_path: Path | None = None,
    provision_ids: set[str] | None = None,
) -> dict[str, int]:
    """Collapse one-document-per-N-acts nodes and drop duplicated bodies (#459)."""
    dest = out_path or in_path
    doc = json.loads(in_path.read_text(encoding="utf-8"))
    graph = doc.get("@graph") or []
    header = next(
        (node for node in graph if isinstance(node, dict) and "owl:Ontology" in (
            node.get("@type") if isinstance(node.get("@type"), list) else [node.get("@type")]
        )),
        None,
    )
    groups: dict[str, list[dict]] = {}
    for node in graph:
        if not isinstance(node, dict):
            continue
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "estleg:Annotation" not in types:
            continue
        url = node.get("estleg:annotationSourceUrl")
        if isinstance(url, dict):
            url = url.get("@value")
        key = url if isinstance(url, str) and url else str(node.get("@id"))
        groups.setdefault(key, []).append(node)

    collapsed: list[dict] = []
    for key, nodes in groups.items():
        first = nodes[0]
        ids = [str(node.get("@id") or "") for node in nodes]
        common = ids[0]
        if len(ids) > 1:
            prefix = ""
            for chars in zip(*ids):
                if len(set(chars)) != 1:
                    break
                prefix += chars[0]
            common = prefix.rstrip("_") or ids[0]
        targets: list[str] = []
        seen: set[str] = set()
        for node in nodes:
            for iri in annotates_iris(node):
                if iri not in seen:
                    targets.append(iri)
                    seen.add(iri)
        text = first.get("estleg:annotationText") or ""
        if isinstance(text, dict):
            text = text.get("@value") or ""
        label = first.get("rdfs:label")
        title = ""
        if isinstance(label, dict):
            title = str(label.get("@value") or "")
        elif isinstance(label, str):
            title = label
        title = title.removeprefix("Õiguskantsleri seisukoht: ").strip()
        refined = provision_iris_for_acts(targets, f"{title}\n{text}", provision_ids)
        node = {
            "@id": common,
            "@type": ["owl:NamedIndividual", "estleg:Annotation"],
            "estleg:annotates": _annotates_value(refined),
            "estleg:annotationText": text,
            "estleg:annotationType": classify_annotation_type(title, text),
            "estleg:annotationSource": first.get("estleg:annotationSource") or ANNOTATION_SOURCE,
        }
        if first.get("estleg:annotationSourceUrl"):
            node["estleg:annotationSourceUrl"] = first["estleg:annotationSourceUrl"]
        if first.get("estleg:annotationDate"):
            node["estleg:annotationDate"] = first["estleg:annotationDate"]
        if first.get("rdfs:label"):
            node["rdfs:label"] = first["rdfs:label"]
        collapsed.append(node)

    collapsed.sort(key=lambda item: str(item.get("@id") or ""))
    if header is not None:
        header["rdfs:label"] = {
            "@value": (
                f"Õiguskantsleri seisukohad — praktiku annotatsioonid "
                f"({len(collapsed)} annotatsiooni)"
            ),
            "@language": "et",
        }
        new_graph = [header, *collapsed]
    else:
        new_graph = collapsed
    write_sidecar(new_graph[1:] if header is not None else new_graph, out_path=dest)
    return {
        "before": sum(
            1
            for node in graph
            if isinstance(node, dict)
            and "estleg:Annotation" in (
                node.get("@type") if isinstance(node.get("@type"), list) else [node.get("@type")]
            )
        ),
        "after": len(collapsed),
        "groups": len(groups),
    }


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
    pdf_stats: dict | None = None,
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
    # Record PDF-body extraction unavailability as a blocking-visible skip reason (#282):
    # when pdfminer is missing every PDF opinion silently degrades to a title-only sidecar,
    # so the count of opinions whose body could not be extracted belongs in skip_reasons even
    # though the opinion may still have produced a title-resolved annotation.
    if pdf_stats and pdf_stats.get("extraction_unavailable"):
        skip_reasons["pdf_text_extraction_unavailable"] = pdf_stats["extraction_unavailable"]
    wall, rate, peak_mb = measure_runtime(start_perf, len(results))
    report = CoverageReport(
        pipeline="extract_annotations",
        run_timestamp=PINNED_RUN_TIMESTAMP,
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
    if scrape:
        return scrape_recent_opinions(limit, cache_dir=cache_dir, sleep=sleep), "oiguskantsler.ee scrape"
    if limit <= 0:
        return [], f"seed file {_rel(seed_path)}"
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
    use_pdf_body: bool = False,
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

    pdf_stats: dict | None = None
    if scrape and use_pdf_body:
        opinions, pdf_stats = attach_pdf_text_layers(opinions, cache_dir=cache_dir, sleep=sleep)
        print(
            "PDF body extraction: "
            f"{pdf_stats['usable_text_layers']}/{pdf_stats['pdf_urls']} usable text layer(s); "
            f"{pdf_stats['fetch_failed']} fetch failed, "
            f"{pdf_stats['extraction_unavailable']} unavailable, "
            f"{pdf_stats['unusable_or_scanned']} unusable/scanned, "
            f"{pdf_stats['non_pdf_urls']} non-PDF URL(s)"
        )
        # #282: pdfminer entirely absent — every PDF opinion's body failed to extract, so a
        # full run would silently regenerate a thinner title-only sidecar (body citations
        # lost) and still exit 0. Refuse: leave any existing sidecar untouched, record the
        # gap in the coverage report's skip reasons, and exit non-zero — unless the operator
        # explicitly opts into the degraded output with --allow-partial.
        if (
            pdf_stats["pdf_urls"] > 0
            and pdf_stats["extraction_unavailable"] == pdf_stats["pdf_urls"]
            and not allow_partial
        ):
            print(
                "\nERROR: PDF text-layer extraction is unavailable for every PDF opinion "
                f"({pdf_stats['extraction_unavailable']}/{pdf_stats['pdf_urls']}). pdfminer.six "
                'is likely not installed (run pip install -e ".[pdf]", e.g. via .venv/bin/python). '
                "Refusing to overwrite the sidecar with a degraded title-only body; "
                "re-run with --allow-partial to accept the title-only output.",
                file=sys.stderr,
            )
            _write_coverage(
                [],
                start_perf=start_perf,
                source_label=source_label,
                failures=[
                    "pdf_text_extraction_unavailable: pdfminer.six not installed; "
                    f"{pdf_stats['extraction_unavailable']}/{pdf_stats['pdf_urls']} PDF opinion(s) "
                    "could not be body-extracted (re-run with --allow-partial to accept title-only)"
                ],
                pdf_stats=pdf_stats,
                path=coverage_path,
            )
            return 1

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
        results, start_perf=start_perf, source_label=source_label, failures=failures,
        pdf_stats=pdf_stats, path=coverage_path,
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
             "For --scrape, 0 = full archive. For seed mode, 0 = none "
             "(writes a zeroed coverage report and no sidecar).",
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
             "for the --limit most-recent opinions, or the full archive with --limit 0.",
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
        help="Accept degraded output: continue past opinions that raise an error during "
             "annotation synthesis (failures are recorded in the coverage report), and — under "
             "--scrape with PDF body extraction — proceed with a title-only sidecar even when "
             "pdfminer.six is entirely unavailable (every PDF body failed to extract). Without "
             "this flag a fully-unavailable pdfminer aborts the run (exit 1) and leaves the "
             "existing sidecar untouched, so a body-less corpus is never silently committed.",
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
    parser.add_argument(
        "--no-pdf-body",
        dest="use_pdf_body",
        action="store_false",
        default=True,
        help="Skip PDF body-text extraction during --scrape (resolve laws from titles only). "
             "Body extraction is ON by default under --scrape; this turns it off. Ignored "
             "without --scrape (a title-only seed source has no PDF body to extract).",
    )
    parser.add_argument(
        "--remint-sidecar",
        action="store_true",
        help="Collapse the committed annotations sidecar to one node per document (#459).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args([] if argv is None else argv)
    if args.remint_sidecar:
        provision_ids = collect_provision_ids()
        stats = remint_annotation_sidecar(provision_ids=provision_ids)
        print(stats)
        return 0
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
        use_pdf_body=args.use_pdf_body,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
