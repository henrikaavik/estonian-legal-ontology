"""
Shared constants, regex patterns, and helpers for Estonian Legal Ontology scripts.

This module centralises abbreviation mappings, JSON-LD context, and utility
functions used by extract_cross_references.py and
extract_court_provision_links.py so they stay in sync.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Well-known abbreviation -> full law name mappings (union of both scripts)
# ---------------------------------------------------------------------------
KNOWN_ABBREVIATIONS: dict[str, str] = {
    "KarS": "Karistusseadustik",
    "VÕS": "Võlaõigusseadus",
    "TsÜS": "Tsiviilseadustiku üldosa seadus",
    "AÕS": "Asjaõigusseadus",
    "PKS": "Perekonnaseadus",
    "ÄS": "Äriseadustik",
    "HMS": "Haldusmenetluse seadus",
    "TsMS": "Tsiviilkohtumenetluse seadustik",
    "KrMS": "Kriminaalmenetluse seadustik",
    "TMS": "Täitemenetluse seadustik",
    "KOKS": "Kohaliku omavalitsuse korralduse seadus",
    "PS": "Eesti Vabariigi põhiseadus",
    "PankrS": "Pankrotiseadus",
    "MKS": "Maksukorralduse seadus",
    "TLS": "Töölepingu seadus",
    "RLS": "Riigilõivuseadus",
    "TTKS": "Töötervishoiu ja tööohutuse seadus",
    "TKS": "Tolliseadus",
    "TPTS": "Tööturuteenuste ja -toetuste seadus",
    "IKS": "Isikuandmete kaitse seadus",
    "RahaPTS": "Rahapesu ja terrorismi rahastamise tõkestamise seadus",
    "KELS": "Kõrgharidusseadus",
    "PPVS": "Planeerimisseadus",
    "AVVKHS": "Avaliku teenistuse seadus",
    "RHS": "Riigihangete seadus",
    "SHS": "Sotsiaalhoolekande seadus",
    "ELTS": "Elektrituruseadus",
    "EhS": "Ehitusseadus",
    "KVS": "Korruptsioonivastane seadus",
    "MSVS": "Meresõiduvahendite seadus",
    "RSVS": "Relvaseadus",
    "EKS": "Elektroonilise side seadus",
    "KES": "Keskkonnaseadustiku eriosa seadus",
    "LKS": "Looduskaitseseadus",
    "KeÜS": "Keskkonnaseadustiku üldosa seadus",
    "MaaRS": "Maareformi seadus",
    "KOS": "Kinnisasja omandamise kitsendamise seadus",
    "RavS": "Ravimiseadus",
    "AVTS": "Avaliku teabe seadus",
    "KLS": "Kalapüügiseadus",
    "KAVS": "Kaitseväeteenistuse seadus",
    "TTÜKS": "Tööstusomandi kaitse seadus",
    "KMS": "Käibemaksuseadus",
    "TuMS": "Tulumaksuseadus",
    "SMMS": "Sotsiaalmaksuseadus",
    "KindlTS": "Kindlustustegevuse seadus",
    # M7 additions — KOV enabling laws that have peep files in the corpus.
    # Values MUST match the canonical estleg:sourceAct on the act's
    # provisions. Verified by the validation one-liner in Task 1 Step 7
    # of the Layer 2b plan.
    # Note on underscore-suffixed keys: `RHS_HÄDA` and `KELS_LASTEAS`
    # disambiguate from already-registered abbreviations (RHS for
    # Riigihangete seadus, KELS for Kõrgharidusseadus). Future
    # collisions should follow the same `_<HINT>` pattern.
    "PGS": "Põhikooli- ja gümnaasiumiseadus",
    "KELS_LASTEAS": "Koolieelse lasteasutuse seadus",
    "HuviKS": "Huvikooli seadus",
    "RuumS": "Ruumiandmete seadus",
    "ÜVVKS": "Ühisveevärgi ja -kanalisatsiooni seadus",
    "KaugKS": "Kaugkütteseadus",
    "ElamuS": "Elamuseadus",
    "RaamatS": "Rahvaraamatukogu seadus",
    "RHS_HÄDA": "Hädaolukorra seadus",
    "EhSE": "Ehitusseadustik",
    "JäätS": "Jäätmeseadus",
    "TeeS": "Teeseadus",
    "RahvaS": "Rahvahääletuse seadus",
    "RKVS": "Riigikogu valimise seadus",
    "RaamatPS": "Raamatupidamise seadus",
    "ÜTS": "Ühistranspordiseadus",
    # #390 — four enabling laws that were genitive-recognised but
    # resolver-fallthrough because they had no KNOWN_ABBREVIATIONS entry,
    # though their peep files exist. Adding them lets the preamble pass
    # (and Pattern 3 cross-refs) chain genitive -> abbrev -> sourceAct ->
    # prefix -> act_iri. Values are the canonical estleg:sourceAct strings.
    "KNS": "Kohanimeseadus",
    "AluS": "Alusharidusseadus",
    "KOFS": "Kohaliku omavalitsuse üksuse finantsjuhtimise seadus",
    "KOVVS": "Kohaliku omavalitsuse volikogu valimise seadus",
    # #363 — 25 heavily-cited corpus laws that had NO abbreviation, so
    # their genitive citation forms could not resolve through Pattern 3.
    # Each value is the canonical estleg:sourceAct verified against the
    # corpus peep file; the matching genitive is registered in
    # FULLNAME_GENITIVE below. New abbreviations use a non-colliding
    # short form (checked against every existing key).
    "KorrS": "Korrakaitseseadus",
    "RES": "Riigieelarve seadus",
    "VeeS": "Veeseadus",
    "LS": "Liiklusseadus",
    "VPTS": "Väärtpaberituru seadus",
    "VKTS": "Välisriigi kutsekvalifikatsiooni tunnustamise seadus",
    "KeÜSE": "Keskkonnaseadustiku üldosa seadus",
    "MsÜS": "Majandustegevuse seadustiku üldosa seadus",
    "KAS": "Krediidiasutuste seadus",
    "AÕKS": "Atmosfääriõhu kaitse seadus",
    "TOAS": "Tööstusomandi õiguskorralduse aluste seadus",
    "KoPS": "Kogumispensionide seadus",
    "PPVSE": "Politsei ja piirivalve seadus",
    "ÄRS": "Äriregistri seadus",
    "KarRS": "Karistusregistri seadus",
    "RaKS": "Ravikindlustuse seadus",
    "NotS": "Notariaadiseadus",
    "TÜS": "Tulundusühistuseadus",
    "VMS": "Välismaalaste seadus",
    "JAS": "Julgeolekuasutuste seadus",
    "ITDS": "Isikut tõendavate dokumentide seadus",
    "TTKSE": "Tervishoiuteenuste korraldamise seadus",
    "FIS": "Finantsinspektsiooni seadus",
    "LasteKS": "Lastekaitseseadus",
    "MERAS": "Makseasutuste ja e-raha asutuste seadus",
}

# ---------------------------------------------------------------------------
# Genitive / partitive full-name forms -> abbreviation
# ---------------------------------------------------------------------------
FULLNAME_GENITIVE: dict[str, str] = {
    "karistusseadustiku": "KarS",
    "võlaõigusseaduse": "VÕS",
    "tsiviilseadustiku üldosa seaduse": "TsÜS",
    "asjaõigusseaduse": "AÕS",
    "perekonnaseaduse": "PKS",
    "äriseadustiku": "ÄS",
    "haldusmenetluse seaduse": "HMS",
    "tsiviilkohtumenetluse seadustiku": "TsMS",
    "kriminaalmenetluse seadustiku": "KrMS",
    "täitemenetluse seadustiku": "TMS",
    "kohaliku omavalitsuse korralduse seaduse": "KOKS",
    "põhiseaduse": "PS",
    "pankrotiseaduse": "PankrS",
    "maksukorralduse seaduse": "MKS",
    "töölepingu seaduse": "TLS",
    "riigilõivuseaduse": "RLS",
    # M7 — match against parser's lowercased law_ref output. Entries
    # whose abbrev resolves through KNOWN_ABBREVIATIONS to a corpus
    # peep file produce real triples.
    # Four entries (sotsiaalhoolekande seaduse → SHS, avaliku teabe
    # seaduse → AVTS, avaliku teenistuse seaduse → AVVKHS,
    # planeerimisseaduse → PPVS) reuse abbreviations already in
    # KNOWN_ABBREVIATIONS from Layer 2a — only the genitive form is new.
    # #390 — KNS/AluS/KOFS/KOVVS are now registered in
    # KNOWN_ABBREVIATIONS (their peep files exist), so these four
    # genitives resolve end-to-end instead of falling through.
    "kohanimeseaduse": "KNS",
    "põhikooli- ja gümnaasiumiseaduse": "PGS",
    "koolieelse lasteasutuse seaduse": "KELS_LASTEAS",
    "alusharidusseaduse": "AluS",
    "huvikooli seaduse": "HuviKS",
    "ruumiandmete seaduse": "RuumS",
    "kohaliku omavalitsuse üksuse finantsjuhtimise seaduse": "KOFS",
    "ühisveevärgi ja -kanalisatsiooni seaduse": "ÜVVKS",
    "kaugkütteseaduse": "KaugKS",
    "elamuseaduse": "ElamuS",
    "rahvaraamatukogu seaduse": "RaamatS",
    "hädaolukorra seaduse": "RHS_HÄDA",
    "ehitusseadustiku": "EhSE",
    "jäätmeseaduse": "JäätS",
    "teeseaduse": "TeeS",
    "rahvahääletuse seaduse": "RahvaS",
    "riigikogu valimise seaduse": "RKVS",
    "kohaliku omavalitsuse volikogu valimise seaduse": "KOVVS",
    "raamatupidamise seaduse": "RaamatPS",
    "ühistranspordiseaduse": "ÜTS",
    "sotsiaalhoolekande seaduse": "SHS",
    "avaliku teabe seaduse": "AVTS",
    "avaliku teenistuse seaduse": "AVVKHS",
    "planeerimisseaduse": "PPVS",
    # #363 — genitive forms for laws whose abbreviation ALREADY existed
    # in KNOWN_ABBREVIATIONS but lacked a genitive entry, so Pattern 3
    # silently dropped their citations. Each resolves through the
    # existing abbrev to a corpus peep file.
    "tulumaksuseaduse": "TuMS",
    "sotsiaalmaksuseaduse": "SMMS",
    "relvaseaduse": "RSVS",
    "kaitseväeteenistuse seaduse": "KAVS",
    "kindlustustegevuse seaduse": "KindlTS",
    "looduskaitseseaduse": "LKS",
    "ravimiseaduse": "RavS",
    "elektroonilise side seaduse": "EKS",
    "maareformi seaduse": "MaaRS",
    # #363 — genitive forms for the 25 newly-registered abbreviations
    # above. These were the heavily-cited corpus laws that previously
    # had neither an abbreviation nor a genitive entry.
    "korrakaitseseaduse": "KorrS",
    "riigieelarve seaduse": "RES",
    "veeseaduse": "VeeS",
    "liiklusseaduse": "LS",
    "väärtpaberituru seaduse": "VPTS",
    "välisriigi kutsekvalifikatsiooni tunnustamise seaduse": "VKTS",
    "keskkonnaseadustiku üldosa seaduse": "KeÜSE",
    "majandustegevuse seadustiku üldosa seaduse": "MsÜS",
    "krediidiasutuste seaduse": "KAS",
    "atmosfääriõhu kaitse seaduse": "AÕKS",
    "tööstusomandi õiguskorralduse aluste seaduse": "TOAS",
    "kogumispensionide seaduse": "KoPS",
    "politsei ja piirivalve seaduse": "PPVSE",
    "äriregistri seaduse": "ÄRS",
    "karistusregistri seaduse": "KarRS",
    "ravikindlustuse seaduse": "RaKS",
    "notariaadiseaduse": "NotS",
    "tulundusühistuseaduse": "TÜS",
    "välismaalaste seaduse": "VMS",
    "julgeolekuasutuste seaduse": "JAS",
    "isikut tõendavate dokumentide seaduse": "ITDS",
    "tervishoiuteenuste korraldamise seaduse": "TTKSE",
    "finantsinspektsiooni seaduse": "FIS",
    "lastekaitseseaduse": "LasteKS",
    "makseasutuste ja e-raha asutuste seaduse": "MERAS",
}

# ---------------------------------------------------------------------------
# Aggregate registry IRI prefixes — files whose @id values use these
# prefixes are registry/aggregate maps (not classifiable as a single
# act). Centralised here so EuroVoc classification and any future
# pipeline that needs to skip these files share one source of truth.
# ---------------------------------------------------------------------------
AGGREGATE_REGISTRY_PREFIXES: tuple[str, ...] = (
    "estleg:Municipalities_",
    "estleg:Issuers_",
    "estleg:LegalConcepts_",
    "estleg:Citations_",
    "estleg:Similarity_",
)

# Public section 2 / Seadusloome load surface. Consumers should load
# ``combined_ontology.jsonld`` plus these subcorpora and sidecar directories
# when they need to follow enrichment links from the published graph.
PUBLIC_LOAD_SUBDIRS: tuple[str, ...] = (
    "eelnoud",
    "riigikohus",
    "curia",
    "eurlex",
    "concepts",
    "sanctions",
    "amendments",
    "institutions",
    "provision_versions",
    "annotations",
    "harmonisation",
    "regulations",
)

PUBLIC_LOAD_JSONLD_SUFFIXES: tuple[str, ...] = (".json", ".jsonld")

_PUBLIC_NON_DATA_STEM_SUFFIXES: tuple[str, ...] = (
    "_index",
    "_report",
    "_schema",
    "_summary",
)


def is_public_jsonld_data_file(path: Path) -> bool:
    """Return True for JSON-LD data files in the public load surface.

    Sidecar directories contain reports, indexes, and schemas next to actual
    ``@graph`` data. The public loader should include the data files and skip
    those aggregate artifacts so SHACL and graph-closure checks see the same
    consumer-facing graph.
    """
    if path.suffix.lower() not in PUBLIC_LOAD_JSONLD_SUFFIXES:
        return False
    stem = path.stem.lower()
    if stem == "index" or stem.endswith("index"):
        return False
    return not any(stem.endswith(suffix) for suffix in _PUBLIC_NON_DATA_STEM_SUFFIXES)


def iter_public_load_files(
    krr_dir: Path,
    *,
    include_combined: bool = True,
) -> list[Path]:
    """Return sorted JSON-LD inputs for the public section 2 load surface."""
    inputs: set[Path] = set()
    combined = krr_dir / "combined_ontology.jsonld"
    if include_combined and combined.exists():
        inputs.add(combined)

    for subdir_name in PUBLIC_LOAD_SUBDIRS:
        subdir = krr_dir / subdir_name
        if not subdir.is_dir():
            continue
        for suffix in PUBLIC_LOAD_JSONLD_SUFFIXES:
            for path in subdir.rglob(f"*{suffix}"):
                if path.is_file() and is_public_jsonld_data_file(path):
                    inputs.add(path)
    return sorted(inputs)

# ---------------------------------------------------------------------------
# JSON-LD shared context
# ---------------------------------------------------------------------------
NS = "https://data.riik.ee/ontology/estleg#"

CONTEXT: dict[str, str] = {
    "estleg": NS,
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dcterms": "http://purl.org/dc/terms/",
}

# ---------------------------------------------------------------------------
# KOV body-word canonicalization and issuer-name normalization (Layer 2c PR #3)
# ---------------------------------------------------------------------------
# Court summaries cite KOV bodies using genitive forms (Linnavalitsuse,
# Vallavalitsuse); KOV peep estleg:issuer fields use nominative (Linnavalitsus,
# Vallavalitsus). The volikogu forms are identical in both cases. BODY_CANON
# normalizes either side's body word to the nominative form so issuer-string
# compare works after normalize_issuer_name().
BODY_CANON: dict[str, str] = {
    "linnavalitsuse": "linnavalitsus",
    "vallavalitsuse": "vallavalitsus",
    "linnavalitsus":  "linnavalitsus",
    "vallavalitsus":  "vallavalitsus",
    "linnavolikogu":  "linnavolikogu",
    "vallavolikogu":  "vallavolikogu",
}


def normalize_issuer_name(s: str) -> str:
    """Lowercase + Estonian diacritic transliteration + whitespace collapse.

    Same approach as Layer 1 ``titleNormalized``. Used at both index-build
    time (over KOV peep ``estleg:issuer`` strings) and citation-resolve
    time (over reconstructed display name from a regex match).
    """
    s = s.lower()
    for src, dst in (
        ("õ", "o"), ("ä", "a"), ("ö", "o"),
        ("ü", "u"), ("š", "s"), ("ž", "z"),
    ):
        s = s.replace(src, dst)
    return " ".join(s.split())


def jsonld_text(
    value: object, default: str = "", *, prefer_language: str | None = None
) -> str:
    """Return a plain string from common JSON-LD text shapes.

    Generated corpus fields may be plain strings, value objects such as
    ``{"@value": "...", "@language": "et"}``, or lists of those objects.
    Semantic extractors should call this before regex/token processing so
    fresh generator output and enriched normalized output use one contract.
    When ``prefer_language`` is set, language-tagged lists prefer matching
    values but fall back to the default joined text if no match exists.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        text = value.get("@value")
        language = value.get("@language")
        if (
            prefer_language
            and isinstance(language, str)
            and language != prefer_language
        ):
            return default
        return text if isinstance(text, str) else default
    if isinstance(value, list):
        if prefer_language:
            preferred = [
                text
                for item in value
                if (
                    text := jsonld_text(
                        item, default="", prefer_language=prefer_language
                    )
                )
            ]
            if preferred:
                return " ".join(preferred)
        parts = [text for item in value if (text := jsonld_text(item))]
        return " ".join(parts) if parts else default
    return default


def jsonld_texts(value: object, *, prefer_language: str | None = None) -> list[str]:
    """Return all plain strings from common JSON-LD text shapes."""
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(jsonld_texts(item, prefer_language=prefer_language))
        return out
    text = jsonld_text(value, prefer_language=prefer_language)
    return [text] if text else []


# ---------------------------------------------------------------------------
# Source provenance helper (issue #113)
# ---------------------------------------------------------------------------

def source_provenance(
    rt_url: str | None = None,
    db_name: str | None = None,
    label: str | None = None,
    *,
    language: str | None = "et",
) -> dict:
    """Build the canonical source-provenance field pair for an act node.

    Implements the "Source provenance" contract documented in
    ``docs/SCHEMA_REFERENCE.md``. The contract is:

    - ``dcterms:source`` — reserved for a resolvable IRI object
      ``{"@id": "https://..."}``. Emitted only when ``rt_url`` is a
      non-empty string. Validated by
      ``validate_all.validate_source_provenance``.
    - ``dcterms:title`` — the structured (preferably language-tagged)
      canonical title. Emitted from ``label`` when given; this is the
      field downstream enrichers should prefer for titles.
    - ``dc:source`` — the **legacy human-readable source descriptor**.
      It is intentionally overloaded across document types: an RT/source
      citation string, the act title, or the originating-database name.
      Set to ``label`` if given, else ``db_name``. Stable but legacy —
      not to be removed; new data should also emit ``dcterms:source``
      where an authoritative IRI exists. Validated (typing only) by
      ``validate_all.validate_dc_source``.

    The consumer fallback chain documented in SCHEMA_REFERENCE is:
    for a resolvable URL — ``dcterms:source`` IRI → URL parsed from
    ``dc:source`` (only if it is a URL) → none; for a title —
    ``dcterms:title`` → ``dc:source`` string → ``rdfs:label``.

    Note: most generators currently build these fields inline; this
    helper centralises and documents the shape rather than being wired
    through every generator. Returned dict contains only the keys that
    have a value, so callers can ``ontology_node.update(...)`` it.
    """
    fields: dict = {}
    # dc:source — legacy human-readable descriptor (title preferred, else
    # the originating-database / source label).
    legacy = label if label else db_name
    if legacy:
        fields["dc:source"] = legacy
    # dcterms:title — structured canonical title.
    if label:
        fields["dcterms:title"] = (
            {"@value": label, "@language": language} if language else label
        )
    # dcterms:source — resolvable IRI object only; never a bare string.
    if isinstance(rt_url, str) and rt_url:
        fields["dcterms:source"] = {"@id": rt_url}
    return fields


# ---------------------------------------------------------------------------
# Run-counters helper (Layer 2c PR #3)
# ---------------------------------------------------------------------------
# Pipelines that read many JSON files and need to record malformed inputs
# under coverage-report fields share this accumulator so failure
# bookkeeping (files_skipped + skip_reasons + error_count + failure_samples)
# stays atomic. _safe_load wraps json.load with the four bumps; callers
# treat None as "skip this file."

# In-memory cap on failures samples. The downstream coverage-report writer
# (kov_pipeline_coverage.write_coverage_report) enforces a final cap of 20
# samples in the JSON output. We over-provision 10× that cap here so a
# single run can collect a diverse pool across reasons before the writer
# truncates — otherwise a single high-frequency failure (e.g. a corrupt
# fixture) could fill the 20-slot budget and crowd out other failure modes.
_FAILURE_SAMPLES_INMEMORY_CAP = 200


@dataclass
class _RunCounters:
    """Mutable accumulator threaded through every read site of a pipeline.

    De-dup set ``seen_error_paths`` ensures the same malformed file
    encountered by multiple walks (cleanup, index-build, processing)
    counts once toward ``file_skips`` rather than once per walk.
    """

    file_skips: int = 0
    error_count: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    seen_error_paths: set[Path] = field(default_factory=set)

    def bump_skip(self, reason: str, sample: str) -> None:
        self.file_skips += 1
        self.error_count += 1
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1
        if len(self.failures) < _FAILURE_SAMPLES_INMEMORY_CAP:
            self.failures.append(sample)

    def bump_citation_skip(self, reason: str, sample: str) -> None:
        """Citation-level skip: bumps skip_reasons + failures only.

        Use this for per-citation routing (ambiguous_key, unknown_issuer,
        etc.) where the underlying file is fine and only one citation
        within it is skipped. Use bump_skip() instead when the entire
        file is unreadable (file_skips/error_count get bumped there).
        """
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1
        if len(self.failures) < _FAILURE_SAMPLES_INMEMORY_CAP:
            self.failures.append(sample)

    def bump_citation_count(self, reason: str) -> None:
        """Counter-only bump (no failure sample) — for known-deferred buckets."""
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1


def _safe_load(path: Path, counters: _RunCounters) -> dict | None:
    """Load JSON from ``path``; return ``None`` and bump counters on failure.

    Same path counted only once across the whole run via
    ``counters.seen_error_paths``.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError) as exc:
        if path not in counters.seen_error_paths:
            counters.seen_error_paths.add(path)
            counters.bump_skip(
                "json_decode_error",
                f"json_decode_error | {path.name} | "
                f"{type(exc).__name__}: {str(exc)[:80]}",
            )
        return None


# ---------------------------------------------------------------------------
# Paragraph citation regex fragment
#
# Handles all Estonian grammatical cases for the § symbol:
#   §, §§, §-de, §-des, §-d, §-s, §-st, §-le, §-i, §-ga
# Also accepts non-breaking hyphen (\u2011).
# ---------------------------------------------------------------------------
PAR_SUFFIX = r"§§?(?:[\-\u2011](?:de(?:s)?|d|s|st|le|i|ga))?"


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"


# ---------------------------------------------------------------------------
# Declared build / evaluation date (#295).
#
# Git-tracked report and index artifacts must be byte-stable across reruns over
# the same corpus: embedding a wall-clock ``datetime.now()`` in a ``generated``
# field re-diffs the artifact on every run (timestamp-only churn). Generators
# therefore stamp this pinned date instead of the wall clock. It is the
# committed-corpus baseline and can be overridden per-release via the
# ``ESTLEG_BUILD_EVALUATION_DATE`` environment variable (YYYY-MM-DD); it MUST
# stay in sync with the date the corpus was last regenerated. The orchestrator
# (``run_all_integration``) declares the same value (overridable via the same
# ``ESTLEG_BUILD_EVALUATION_DATE`` env var) and passes it to the temporal step
# via ``--evaluation-date``.
BUILD_EVALUATION_DATE: str = os.environ.get(
    "ESTLEG_BUILD_EVALUATION_DATE", "2026-06-01"
)


# ---------------------------------------------------------------------------
# Operational state files — the SINGLE SOURCE OF TRUTH (#240).
#
# These are pipeline bookkeeping artifacts written under ``krr_outputs/``
# that are NOT corpus data and must never be counted by any file-counter
# or fed to the validators. Both ``validate_all`` and ``fix_all_issues``
# import these three names (and re-export ``OPERATIONAL_STATE_FILES`` as a
# module-level alias for backward compatibility); a literal copy must
# never be reintroduced in either module. ``is_operational_state_file``
# is the classifier and ``iter_krr_jsonld_files`` is the ONE shared
# enumerator every counter routes through — this is the anti-drift
# mechanism that keeps local and CI file counts identical.
#
# The corpus count this exclusion yields is pinned by ``metadata.jsonld``
# ``estleg:totalFiles`` / ``estleg:fileCount`` (currently 23114), which
# ``validate_metadata_catalog`` enforces — treat that file as the source of
# truth rather than this prose. Any change here that moves that number means
# the classifier was broadened or narrowed incorrectly.
# ---------------------------------------------------------------------------
OPERATIONAL_STATE_FILES: frozenset[str] = frozenset(
    {
        ".regen_state.json",
        "generation_manifest_laws.json",
        "latest_pipeline_manifest.json",
    }
)

# Operational directories whose generated ``*.json`` state manifests should
# also be excluded even when they don't match a basename in
# ``OPERATIONAL_STATE_FILES``. Kept deliberately conservative (a single
# known integration-report directory) so the pinned ``metadata.jsonld``
# count (currently 23114) is unchanged: the only ``*.json`` currently living under
# ``reports/integration`` is ``latest_pipeline_manifest.json``, which is
# already excluded by basename. The pattern guard is forward-looking — it
# stops a *new* generated state manifest dropped into that directory from
# silently inflating counts (the PR #232 drift bug).
_OPERATIONAL_STATE_DIR_SUFFIXES: tuple[tuple[str, ...], ...] = (
    ("reports", "integration"),
)


def is_operational_state_file(path: Path) -> bool:
    """Return True if ``path`` is a pipeline state file, not corpus data.

    Two ways a path qualifies:

    1. **Basename match** — ``path.name`` is one of
       ``OPERATIONAL_STATE_FILES``.
    2. **Generated-state-manifest pattern** — a ``*.json`` whose parent
       directory ends with a known operational subpath (currently only
       ``reports/integration``). This conservative guard keeps the corpus
       count stable today while preventing future generated manifests in
       that directory from drifting the count.
    """
    if path.name in OPERATIONAL_STATE_FILES:
        return True
    if path.suffix.lower() == ".json":
        parent_parts = path.parent.parts
        for suffix in _OPERATIONAL_STATE_DIR_SUFFIXES:
            if len(parent_parts) >= len(suffix) and parent_parts[-len(suffix):] == suffix:
                return True
    return False


def iter_krr_jsonld_files(krr_dir: Path) -> Iterator[Path]:
    """Yield every corpus JSON/JSON-LD file under ``krr_dir``.

    This is the ONE shared enumerator that every file-counter must go
    through (the anti-drift mechanism for #240). It recursively globs
    ``**/*.json`` and ``**/*.jsonld`` and yields only files that are not
    operational state files (per ``is_operational_state_file``). Results
    are de-duplicated and sorted so callers get a stable order.
    """
    seen: set[Path] = set()
    for pattern in ("**/*.json", "**/*.jsonld"):
        for path in krr_dir.glob(pattern):
            if path in seen:
                continue
            seen.add(path)
    for path in sorted(seen):
        if not is_operational_state_file(path):
            yield path


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def save_json(filepath: Path, doc: dict | list) -> None:
    """Write JSON-LD document to file with consistent formatting.

    Uses a tempfile + atomic ``os.replace`` so partial writes never appear
    at ``filepath``. If serialization fails (non-serialisable payload, OS
    error mid-write), the tempfile is unlinked best-effort before the
    original exception propagates so failed runs don't litter ``.tmp``
    droppings in the target directory.
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=filepath.parent,
        prefix=f".{filepath.name}.",
        suffix=".tmp",
        delete=False,
    )
    tmp_name = tmp.name
    try:
        with tmp as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_name, filepath)
    except BaseException:
        # Best-effort cleanup of the tempfile so failed writes never
        # leave ``.<name>.<rand>.tmp`` droppings in the target directory.
        # Suppress FileNotFoundError so we don't mask the original error
        # if the tempfile was already removed (e.g. by os.replace).
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def iter_peep_files(
    *,
    include_laws: bool = True,
    include_regulations: bool = True,
    include_kov: bool = True,
) -> list[Path]:
    """Return every ``*_peep.json`` ontology file managed by the pipeline.

    Pipeline-wide file iterator that includes top-level laws, state-level
    regulations under ``regulations/riik/``, and KOV regulations under
    ``regulations/kov/``. The Layer 2a default flip (2026-05-03) made KOV
    inclusion the new baseline; pipelines that aren't yet KOV-ready opt
    out explicitly via ``include_kov=False`` (Layer 2b/2c/3 deferrals,
    or KOV-irrelevant pipelines like harmonisation links).
    """
    files: list[Path] = []
    if include_laws:
        files.extend(KRR_DIR.glob("*_peep.json"))
    if include_regulations:
        riik_dir = KRR_DIR / "regulations" / "riik"
        if riik_dir.exists():
            files.extend(riik_dir.glob("*_peep.json"))
        if include_kov:
            kov_dir = KRR_DIR / "regulations" / "kov"
            if kov_dir.exists():
                files.extend(kov_dir.glob("**/*_peep.json"))
    # Sort by absolute posix path (casefold) — using relative_to(KRR_DIR)
    # would raise ValueError for any path outside KRR_DIR, which is a
    # footgun for future callers, symlinks, or test scaffolding.
    return sorted(files, key=lambda p: p.as_posix().casefold())


def build_globalid_xml_lookup(rt_root: Path) -> dict[str, Path]:
    """Recursively scan rt_root for *.xml files and build a
    globalId → Path lookup keyed by the root element's globaalID
    attribute. Files without a recognisable globaalID are skipped.

    Walks rt_root and all subdirectories so KOV XML under
    ``maarus_kov/`` and state regulation XML under ``maarus/`` are
    paired alongside law XML at the root.
    """
    lookup: dict[str, Path] = {}
    if not rt_root.is_dir():
        return lookup
    for xml_path in sorted(rt_root.rglob("*.xml"), key=lambda p: str(p.relative_to(rt_root)).casefold()):
        try:
            tree = ET.parse(str(xml_path))
        except ET.ParseError:
            continue
        root = tree.getroot()
        # Primary: root element's globaalID attribute (most RT XML).
        gid = root.get("globaalID")
        if gid is None:
            # Fallback: scan top-level <metaandmed> children for a
            # globaalID leaf element.
            #
            # NOTE: ElementTree's truthiness on Element is False when
            # the element has no children, so `child.find(a) or
            # child.find(b)` would silently discard a leaf hit. Use
            # explicit `is None` checks throughout.
            for child in root:
                if not child.tag.endswith("metaandmed"):
                    continue
                gid_el = child.find("globaalID")
                if gid_el is None:
                    gid_el = child.find("{*}globaalID")
                if gid_el is not None and gid_el.text:
                    gid = gid_el.text.strip()
                    break
        if gid:
            gid = str(gid)
            if gid in lookup:
                print(
                    "WARN: duplicate globalId "
                    f"{gid} in {lookup[gid].relative_to(rt_root)} and {xml_path.relative_to(rt_root)}; "
                    "keeping first"
                )
                continue
            lookup[gid] = xml_path
    return lookup


def pair_peep_with_xml(
    peep_path: Path,
    lookup: dict[str, Path],
    *,
    data_dir: Path | None = None,
    counters: _RunCounters | None = None,
) -> Path | None:
    """Pair a peep file to its XML.

    Two paths in priority order:

    1. **globalId-based** (the new path, KOV + state regs): the
       generators stamp ``estleg:globalId`` on every act node, and KOV
       XML filenames embed the same id (``reg_<globalId>.xml``). This
       is the canonical pairing for everything generated post-Phase-1.

    2. **Slug-based fallback** (legacy laws): the 615 indexed laws
       were generated before the ``estleg:globalId`` field existed,
       so their act nodes carry ``estleg:Law``/``owl:Ontology`` types
       but no globalId. For these, fall back to matching the peep
       file's stem (sans ``_peep``) against
       ``<data_dir>/<stem>.xml``. ``data_dir`` is required for the
       fallback; if not supplied, only the globalId path runs.

    Returns the XML path or None if neither path resolves.
    """
    try:
        with open(peep_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (ValueError, OSError) as exc:
        if counters is not None:
            if peep_path not in counters.seen_error_paths:
                counters.seen_error_paths.add(peep_path)
                counters.bump_skip(
                    "json_decode_error",
                    f"json_decode_error | {peep_path.name} | "
                    f"{type(exc).__name__}: {str(exc)[:80]}",
                )
        else:
            # No counters provided — still surface the corruption signal so
            # callers that haven't been wired into the run-counter framework
            # don't silently conflate "corrupt peep" with "no XML found".
            print(
                f"pair_peep_with_xml: corrupt peep {peep_path}: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        return None
    gid = None
    for node in doc.get("@graph", []):
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if not any(t.endswith("Regulation") or t == "estleg:Law"
                   or t == "owl:Ontology" for t in types):
            continue
        gid = node.get("estleg:globalId")
        if gid:
            break
    # Path 1: globalId
    if gid:
        xml = lookup.get(str(gid))
        if xml is not None:
            return xml
    # Path 2: slug fallback (laws without globalId)
    if data_dir is not None:
        slug = peep_path.stem.replace("_peep", "")
        xml = data_dir / f"{slug}.xml"
        if xml.exists():
            return xml
        # Stripped-_osa fallback for multi-part laws
        base_slug = re.sub(r"_osa\d+$", "", slug)
        xml = data_dir / f"{base_slug}.xml"
        if xml.exists():
            return xml
    return None


_ESTONIAN_TRANSLITERATION: dict[str, str] = {
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
}
_TRANSLIT_TABLE = str.maketrans(_ESTONIAN_TRANSLITERATION)


def sanitize_id(value: str) -> str:
    """Create a safe ID component from a string."""
    s = value.replace(" ", "_")
    # Transliterate Estonian diacritics before stripping non-ASCII
    s = s.translate(_TRANSLIT_TABLE)
    s = re.sub(r"[^0-9A-Za-z_]", "", s)
    return s or "Unknown"


def slugify(text: str, max_len: int = 80) -> str:
    """Convert Estonian text to a filename-safe slug.

    Lowercases, transliterates Estonian diacritics, replaces non-alnum
    runs with underscores, and truncates to ``max_len`` characters.
    Centralised here so callers (laws, regulations, downstream scripts)
    share one definition.

    The truncation can land on a ``_`` (when the boundary character at
    ``max_len`` was an underscore), so a final ``rstrip('_')`` runs AFTER
    the cut. Without it, a title slugifying to exactly ``max_len`` chars
    ending in ``_`` would produce double-underscore IRIs once a suffix
    like ``_Map_2026`` / ``_Par_N`` is appended (#346).
    """
    text = text.translate(_TRANSLIT_TABLE)
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")[:max_len].rstrip("_")
