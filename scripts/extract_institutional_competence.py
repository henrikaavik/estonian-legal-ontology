#!/usr/bin/env python3
"""
Extract which institutions are responsible for what from law text.

Scans estleg:summary text in every provision for references to Estonian
state institutions and competence-assigning language, then creates
estleg:Institution nodes and links provisions to competent authorities.

Outputs:
  - krr_outputs/institutions/  (per-institution JSON-LD files)
  - krr_outputs/institutional_competence_report.json
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from estleg_common import (
    BUILD_EVALUATION_DATE,
    iter_peep_files,
    jsonld_text,
    save_json,
)
from extract_sanctions import _find_act_node
from extract_cross_references import build_issuer_registry
from kov_pipeline_coverage import (
    PINNED_RUN_TIMESTAMP,
    CoverageReport,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
INST_DIR = KRR_DIR / "institutions"
# INSTIT_DIR is the monkeypatch-friendly alias used by tests (and Task 4+).
# main() writes institution files via INSTIT_DIR so tests can redirect to tmp_path.
# Issue #170 Finding 9: mkdir moved from import-time to _ensure_dirs() to
# avoid filesystem side effects on import.
INSTIT_DIR = INST_DIR

# Issue #118: curated alias table mapping historical/predecessor
# institution slugs -> canonical successor slugs (e.g. the 2004
# Maksuamet+Tolliamet -> Maksu- ja Tolliamet merger). Loaded once at
# import; see data/institution_aliases.json for the evidence per entry.
INSTITUTION_ALIASES_PATH = REPO_ROOT / "data" / "institution_aliases.json"


def _ensure_dirs() -> None:
    """Create output directories. Called from main(); avoids import-time
    side effects so tests that monkeypatch INSTIT_DIR don't accidentally
    create the production institutions directory."""
    INSTIT_DIR.mkdir(parents=True, exist_ok=True)


def _id_ref(value: object) -> str | None:
    """Unwrap a JSON-LD {"@id": "..."} object to a plain IRI string.
    Returns the string directly if *value* is already a string, or
    None when *value* is None or lacks an "@id" key.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("@id")
    return None


NS = "https://w3id.org/estleg/"

CONTEXT = {
    "estleg": NS,
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dcterms": "http://purl.org/dc/terms/",
}


# Issue #376: ``save_json`` is imported from ``estleg_common`` (tempfile +
# atomic ``os.replace``) instead of a local non-atomic ``open(filepath, "w")``
# that truncated to 0 bytes before writing — a SIGINT/OOM/disk-full mid-write
# left a zero-byte/partial JSON that ``load_json`` then silently swallowed on
# the next run, permanently dropping enrichment. Re-exported as a module
# global so tests can monkeypatch ``mod.save_json`` and the production call
# sites pick up the override.


def load_json(filepath: Path) -> dict | None:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        print(f"  WARN: cannot load {filepath.name}: {exc}")
        return None


_ESTONIAN_TRANSLITERATION: dict[str, str] = {
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
}
_TRANSLIT_TABLE = str.maketrans(_ESTONIAN_TRANSLITERATION)


def sanitize_id(value: str) -> str:
    s = value.replace(" ", "_").replace("-", "_")
    # Transliterate Estonian diacritics before stripping non-ASCII
    s = s.translate(_TRANSLIT_TABLE)
    s = re.sub(r"[^0-9A-Za-z_]", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:80] or "Unknown"


# Map known abbreviations to canonical full-name suffixes (lowercase).
# Module-level so it can be reused by detect_institutions and tests without
# rebuilding on every call.
_ABBREVIATION_MAP: dict[str, str] = {
    "mta": "maksu_ja_tolliamet",
    "ppa": "politsei_ja_piirivalveamet",
    "ttja": "tarbijakaitse_ja_tehnilise_jarelevalve_amet",
    "harno": "haridus_ja_noorteamet",
}


def _load_institution_aliases(path: Path = INSTITUTION_ALIASES_PATH) -> dict[str, str]:
    """Issue #118: load the curated historical-merge alias table.

    Returns a flat ``{historical_slug: canonical_slug}`` map. Missing or
    malformed file -> empty map (the extractor still runs; the noun-stem
    normaliser alone is the previous behaviour). Both keys and values are
    lowercased and underscore-collapsed so a lookup matches whatever
    ``normalize_iri_suffix`` produces.
    """
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    entries = raw.get("aliases")
    if not isinstance(entries, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in entries.items():
        if not isinstance(key, str):
            continue
        canonical: str | None = None
        if isinstance(value, str):
            canonical = value
        elif isinstance(value, dict):
            cand = value.get("canonical")
            if isinstance(cand, str):
                canonical = cand
        if not canonical:
            continue
        norm_key = re.sub(r"_+", "_", key.lower()).strip("_")
        norm_val = re.sub(r"_+", "_", canonical.lower()).strip("_")
        if norm_key and norm_val and norm_key != norm_val:
            out[norm_key] = norm_val
    return out


# Loaded once at import. Tests that need a different table can
# monkeypatch this directly or re-run ``_load_institution_aliases``.
_INSTITUTION_ALIASES: dict[str, str] = _load_institution_aliases()

# Map known Estonian inflected forms to nominative (lowercase).
# Issue #170 Finding 10: the old map enumerated double/triple-underscore
# variants (e.g. politsei__ja_piirivalveamet); these are unreachable
# because normalize_iri_suffix() collapses runs of underscores via
# re.sub(r"_+", "_") BEFORE the lookup. They've been deleted; the typo
# variant and the kohalik_omavalitsus inflections are the only entries
# that survive.
_INFLECTION_MAP: dict[str, str] = {
    "kohaliku_omavalitsus": "kohalik_omavalitsus",
    "kohaliku_omavalitsuse": "kohalik_omavalitsus",
    "kohalik_omavalitsuse": "kohalik_omavalitsus",
    # Typo variant: missing 'e' in Andmekaitse
    "andmekaitsinspektsioon": "andmekaitseinspektsioon",
}

# Issue #170 Finding 5 + Issue #118: Estonian noun-stem normaliser.
# Estonian nominal inflection layers a case suffix onto the GENITIVE stem,
# which itself adds "i" for consonant-ending forms — so e.g.:
#   nominative:   maksuamet
#   genitive:     maksuameti
#   partitive:    maksuametit
#   illative:     maksuametisse  (genitive + "sse")
#   inessive:     maksuametis    (genitive + "s")
#   elative:      maksuametist   (genitive + "st")
#   allative:     maksuametile   (genitive + "le")
#   adessive:     maksuametil    (genitive + "l")
#   ablative:     maksuametilt   (genitive + "lt")
#   translative:  maksuametiks   (genitive + "ks")
#   terminative:  maksuametini   (genitive + "ni")
#   essive:       maksuametina   (genitive + "na")
#   abessive:     maksuametita   (genitive + "ta")
#   comitative:   maksuametiga   (genitive + "ga")
# Plural forms layer "te"/"de" before the case ending (maksuametitele,
# maksuametitega, ...) — rarer in legal text but they do occur.
#
# To collapse all of these to nominative, we strip the case suffix first
# and then drop a trailing "i" if the result still doesn't end in a known
# institutional root. The list is sorted strictly longest-first so the
# stripper can't carve a longer case into a wrong stem (e.g. "...isse"
# must be tried before "...se"); the root-match guard in
# _strip_estonian_case() makes this defensive in any case, but ordering
# keeps the first matching strip the correct one.
_CASE_SUFFIXES_LONGEST_FIRST: tuple[str, ...] = (
    # Plural-stem composites (te-/de- + case ending) — 4–5 chars.
    "tesse", "tega", "tele", "telt", "test", "teks", "teni", "tena", "teta",
    "tide", "tes",
    # Singular case endings attached to the genitive stem.
    "esse",                             # illative after "-e-" stems
    "sse",                              # illative ("...isse")
    "iks",                              # rare "-iks" composite
    "elt", "est", "ele", "ile",         # rare/composite variants
    "lt", "le", "st", "ks", "ga", "se", "ni", "na", "ta", "ts", "tt", "de",
    "te",
    # Genitive marker / single-letter case endings — tried last.
    "i", "l", "s", "t", "e",
)

_INSTITUTION_ROOTS: tuple[str, ...] = (
    "amet", "ministeerium", "inspektsioon", "minister",
    # Issue #118: also recognise *kogu (riigikogu / volikogu) and
    # *valitsus (vabariigivalitsus / linnavalitsus / vallavalitsus) so a
    # de-inflected stem ending in one of these is treated as already
    # nominative. (KOV body words are additionally canonicalised by
    # _canonical_body_slug(); this just guards normalize_iri_suffix().)
    "kogu", "valitsus",
)


# Issue #321: ``minister`` is a stem-changing noun — its genitive elides the
# stressed ``e`` and appends ``i`` (nominative ``minister`` → genitive
# ``ministri``), so every oblique form layers a case ending on the genitive
# stem ``ministri`` (``ministril``/``ministrile``/``ministrit``/...). The
# generic ``<root> + i`` genitive rule in _strip_estonian_case can't reach
# this because ``ministri`` is NOT ``minister`` + ``i``. This pattern matches
# a trailing ``ministri`` genitive stem plus an optional Estonian case suffix
# (the partitive ``ministrit`` is handled explicitly) so all forms collapse
# to the nominative ``...minister`` slug instead of spawning inflated siblings
# (``Institution_sotsiaalministril`` etc.) — the same de-inflection guarantee
# Issue #170 Finding 5 gives ``*amet``.
_MINISTER_CASE_ALT: str = "|".join(
    re.escape(s)
    for s in sorted(set(_CASE_SUFFIXES_LONGEST_FIRST), key=len, reverse=True)
    if s != "i"  # the bare genitive marker is already part of "ministri"
)
_MINISTER_GENITIVE_STEM_RE: re.Pattern[str] = re.compile(
    rf"^(.*minist)ri(?:t|{_MINISTER_CASE_ALT})?$"
)


def _collapse_minister_stem(stem: str) -> str | None:
    """Issue #321: collapse a ``*ministri`` genitive-stem form (with an
    optional trailing case suffix, or the partitive ``*ministrit``) to its
    nominative ``*minister`` slug. Returns None when ``stem`` is not a
    minister oblique form so the caller can fall through to the generic
    case-suffix stripper."""
    m = _MINISTER_GENITIVE_STEM_RE.match(stem)
    if m is None:
        return None
    return m.group(1) + "er"


def _strip_estonian_case(stem: str) -> str:
    """Strip a single Estonian case suffix from the trailing component of
    ``stem`` and return the bare nominative stem.

    The stripper iterates: try every suffix; if the resulting stem ends
    in a known institutional root (or in genitive + root, e.g.
    ``maksuameti`` → strip ``i`` → ``maksuamet``), return the stripped
    form. Otherwise return ``stem`` unchanged so we don't accidentally
    truncate a non-institution word that happens to share a Finno-Ugric
    case ending.
    """
    if not stem:
        return stem

    def _ends_in_root(s: str) -> bool:
        return any(s.endswith(b) for b in _INSTITUTION_ROOTS)

    # Already nominative — nothing to do.
    if _ends_in_root(stem):
        return stem

    # Issue #321: handle the stem-changing ``minister`` genitive (``ministri``
    # + optional case ending / partitive ``ministrit``) before the generic
    # loop, which can't reach it via the ``<root> + i`` rule.
    collapsed = _collapse_minister_stem(stem)
    if collapsed is not None:
        return collapsed

    for suffix in _CASE_SUFFIXES_LONGEST_FIRST:
        if not stem.endswith(suffix):
            continue
        candidate = stem[: -len(suffix)]
        if not candidate:
            continue
        if _ends_in_root(candidate):
            return candidate
        # Genitive form: stem ends in <root>+"i". Drop trailing "i" to
        # check for a root match (e.g. "maksuameti" → "maksuamet").
        if candidate.endswith("i") and _ends_in_root(candidate[:-1]):
            return candidate[:-1]
    return stem


def canonicalize_institution_label(raw: str) -> str:
    """De-inflect an institution display label to nominative form (#577).

    Generic-pattern matches stored the raw inflected match as ``rdfs:label``
    (e.g. ``Finantsinspektsioonilt``, ``teadusministrile``,
    ``Politsei- ja Piirivalveametile``). In Estonian the case ending attaches to
    the final head noun, so we de-inflect each space/hyphen component via
    ``_strip_estonian_case`` (which is a no-op on a form that is already
    nominative, so named institutions like ``Finantsinspektsioon`` are
    preserved), then restore the component's leading capitalisation. The
    connector ``ja``/``ning`` and already-nominative tokens are left as-is.
    """
    if not raw:
        return raw

    def _fix_component(token: str) -> str:
        if not token or token.lower() in ("ja", "ning", "või", "ja/või"):
            return token
        stripped = _strip_estonian_case(token.lower())
        if stripped == token.lower():
            return token  # already nominative — keep original casing
        return stripped[:1].upper() + stripped[1:] if token[:1].isupper() else stripped

    # Split on spaces, then on hyphens within each space-token, preserving both
    # separators (``Politsei-`` keeps its trailing hyphen).
    out_tokens = []
    for sp in raw.split(" "):
        out_tokens.append("-".join(_fix_component(h) for h in sp.split("-")))
    return " ".join(out_tokens)


def _apply_alias(slug: str) -> str:
    """Issue #118: resolve a normalised slug through the historical-merge
    alias table, following the chain (capped) in case an alias points at
    a slug that is itself an alias key (defensive — the curated table
    avoids this, but a future edit shouldn't loop)."""
    seen: set[str] = set()
    current = slug
    while current in _INSTITUTION_ALIASES and current not in seen:
        seen.add(current)
        current = _INSTITUTION_ALIASES[current]
    return current


def normalize_iri_suffix(raw_suffix: str) -> str:
    """Normalize an IRI suffix to lowercase convention (matching institution
    definition files) and map known abbreviations/inflections to canonical forms.

    Applies, in order:
      1. Lowercase + underscore-collapse (existing).
      2. Abbreviation lookup (MTA, PPA, ...).
      3. Explicit inflection-map lookup (typo variants, KOV omavalitsus).
      4. Estonian case-suffix stripping for ``*amet`` / ``*ministeerium`` /
         ``*inspektsioon`` / ``*minister`` stems (Finding 5 — fixes inflated
         siblings like ``Institution_maksuametile``).
      5. Issue #118: curated historical-merge alias table — applied LAST
         (after de-inflection) so a de-inflected predecessor name like
         ``maksuamet`` is collapsed onto its successor ``maksu_ja_tolliamet``.
    """
    lower = re.sub(r"_+", "_", raw_suffix.lower()).strip("_")

    # Check abbreviation map first
    if lower in _ABBREVIATION_MAP:
        return _apply_alias(_ABBREVIATION_MAP[lower])

    # Check inflection map
    if lower in _INFLECTION_MAP:
        return _apply_alias(_INFLECTION_MAP[lower])

    # Estonian case-suffix stripping — collapses inflected forms of *amet,
    # *ministeerium, *inspektsioon, *minister to their nominative stems.
    stripped = _strip_estonian_case(lower)
    if stripped != lower:
        return _apply_alias(stripped)

    # Default: lowercase the entire suffix, then resolve aliases.
    return _apply_alias(lower)


# owl:sameAs aliases: abbreviation IRI → canonical IRI (both lowercase)
# These are emitted as owl:sameAs triples in institution definition files so
# that consumers can resolve either form.
SAMEAS_ALIASES: dict[str, str] = {
    "mta": "maksu_ja_tolliamet",
    "ppa": "politsei_ja_piirivalveamet",
    "ttja": "tarbijakaitse_ja_tehnilise_jarelevalve_amet",
    "harno": "haridus_ja_noorteamet",
}


_CANONICAL_BODY_SLUGS = frozenset({
    "linnavolikogu", "vallavolikogu", "alevivolikogu",
    "linnavalitsus", "vallavalitsus",
})


# Estonian case suffixes that can trail a KOV body word, sorted strictly
# longest-first. The genitive stem of a *valitsus word adds "e"
# (vallavalitsuse), so its oblique cases are "...valitsuse" + ending —
# hence the "e"-prefixed composites (-ele, -elt, -eks, -eni, -ena, ...).
# *volikogu words take a bare genitive (vallavolikogu), so their oblique
# cases attach the plain ending. The stripper below only commits a strip
# when the remainder still ends in "volikogu"/"valitsus", so a spurious
# match on a non-body word is a no-op. Issue #118 widened this from the
# original set to cover translative (-ks), terminative (-ni), essive
# (-na), abessive (-ta) and the plural-stem composites (-tele, -tega, ...),
# matching _CASE_SUFFIXES_LONGEST_FIRST.
_BODY_CASE_SUFFIXES_LONGEST_FIRST: tuple[str, ...] = (
    # Plural-stem composites.
    "tesse", "tega", "tele", "telt", "test", "teks", "teni", "tena", "teta",
    "tide", "tes",
    # "e"-stem (genitive-of-valitsus) composites.
    "esse", "ele", "elt", "est", "eks", "eni", "ena", "eta", "ega", "el",
    "es",
    # Bare endings (genitive-of-volikogu) + the illative "sse".
    "sse",
    "le", "lt", "st", "ks", "ni", "na", "ta", "ga", "se", "de", "te",
    "e", "l", "s", "t",
)


def _canonical_body_slug(matched: str) -> str | None:
    """Strip Estonian case suffixes from a KOV body-word match
    and return the canonical slug (linnavolikogu, vallavolikogu,
    alevivolikogu, linnavalitsus, vallavalitsus) or None if the
    match doesn't fit one of the five reachable stems.
    """
    s = matched.lower()
    for suffix in _BODY_CASE_SUFFIXES_LONGEST_FIRST:
        if s.endswith(suffix) and len(s) > len(suffix) + 4:
            stripped = s[: -len(suffix)]
            if stripped.endswith(("volikogu", "valitsus")):
                s = stripped
                break
    if s in _CANONICAL_BODY_SLUGS:
        return s
    return None


# ---------- institution catalogue ----------

# Named institutions: (canonical name, IRI suffix, institution type)
# IRI suffixes are stored in raw form here; normalize_iri_suffix() is applied
# when building the actual IRI.
NAMED_INSTITUTIONS: list[tuple[str, str, str]] = [
    # Government / Parliament / President
    ("Vabariigi Valitsus", "vabariigivalitsus", "government"),
    ("Riigikogu", "riigikogu", "parliament"),
    ("Vabariigi President", "vabariigipresident", "head_of_state"),
    # Specific agencies (order matters: longer names first)
    # Abbreviations map to canonical full-name suffixes via normalize_iri_suffix()
    ("Andmekaitse Inspektsioon", "andmekaitseinspektsioon", "agency"),
    ("Tarbijakaitse ja Tehnilise Järelevalve Amet", "ttja", "agency"),
    ("Maksu- ja Tolliamet", "maksu_ja_tolliamet", "agency"),
    ("Politsei- ja Piirivalveamet", "politsei_ja_piirivalveamet", "agency"),
    ("Keskkonnaamet", "keskkonnaamet", "agency"),
    ("Terviseamet", "terviseamet", "agency"),
    ("Haridus- ja Noorteamet", "haridus_ja_noorteamet", "agency"),
    # Abbreviation-only matches (text may say "MTA" or "PPA" without full name)
    ("MTA", "mta", "agency"),
    ("PPA", "ppa", "agency"),
    # Courts
    ("Riigikohus", "riigikohus", "court"),
    ("ringkonnakohus", "ringkonnakohus", "court"),
    ("halduskohus", "halduskohus", "court"),
    ("maakohus", "maakohus", "court"),
]

# Issue #170 Findings 1 + 2: pre-compile word-boundary regexes for each
# named institution and cache them at module load.
#
# Why we don't reuse the simple substring matcher: ``"riigikogu" in
# text.lower()`` will match inside unrelated words that happen to contain
# the substring (e.g. abbreviations like "MTA" appear inside random ASCII
# triples). Running through compiled ``\bMTA\b`` patterns with
# re.IGNORECASE | re.UNICODE eliminates the leak.
#
# Abbreviations (MTA / PPA) are checked case-SENSITIVELY against the
# original text and only against \bABBR\b — that's how the legal text
# distinguishes the abbreviation from a stray uppercase substring.
# Additionally, an abbreviation entry only registers when the canonical
# full-name entry has NOT already been matched in the same provision —
# enforced inside detect_institutions() via the
# full_name_norm_suffixes_present sentinel set.


def _is_abbreviation_entry(name: str) -> bool:
    """Return True iff ``name`` is an abbreviation-only entry (e.g. ``MTA``,
    ``PPA``). Such entries match the original-case text against
    ``\\bNAME\\b`` (no IGNORECASE) and only register when the canonical
    full-name entry hasn't already been matched in the same provision."""
    return name.isupper() and " " not in name


def _compile_named_pattern(name: str) -> re.Pattern[str]:
    """Compile a word-boundary regex for a named institution.

    Abbreviation-only entries (MTA, PPA, ...) compile to a case-sensitive
    pattern; full-name entries compile case-insensitively. Estonian
    diacritics are preserved (UNICODE flag) so ``Järelevalve`` stays
    distinct from ``Jarelevalve``.
    """
    flags = re.UNICODE
    if not _is_abbreviation_entry(name):
        flags |= re.IGNORECASE
    return re.compile(rf"\b{re.escape(name)}\b", flags)


_NAMED_PATTERNS: list[tuple[re.Pattern[str], str, str, str]] = [
    (_compile_named_pattern(name), name, raw_suffix, itype)
    for name, raw_suffix, itype in NAMED_INSTITUTIONS
]

# Issue #170 Finding 4: regex used to suppress the generic ``kohus`` token
# when a specific court has already been mentioned in the SAME text — the
# previous logic relied on the order of ``found`` accumulator state, which
# is order-dependent and fragile. This pattern is checked directly against
# the input text instead.
_SPECIFIC_COURT_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:riigikohus|ringkonnakohus|halduskohus|maakohus)\w*\b",
    re.IGNORECASE | re.UNICODE,
)


# Issue #259: an institutional root (amet / inspektsioon) is followed
# either by nothing (nominative), by the partitive "...it", or by the
# genitive marker "i" plus at most one Estonian case suffix. Derivational
# endings that turn the root into a DIFFERENT lexeme — "ametnik" (an
# official, a person), "ametlik"/"ametlikult" (an adjective/adverb),
# "ametkond" (a collective) — are NOT case endings and must not match.
#
# We build the case-suffix alternation from _CASE_SUFFIXES_LONGEST_FIRST so
# it stays in lock-step with the de-inflection performed by
# normalize_iri_suffix(); the single-letter "i" genitive marker is handled
# by the surrounding regex (`i(?:<suffix>)?`) rather than the alternation.
_ROOT_CASE_ALT: str = "|".join(
    re.escape(s)
    for s in sorted(set(_CASE_SUFFIXES_LONGEST_FIRST), key=len, reverse=True)
    if s != "i"  # the bare genitive "i" is the prefix of the oblique forms
)


def _root_inflection_group(root: str) -> str:
    """Return a regex fragment matching ``root`` in nominative, partitive
    (``root`` + ``it``) or any oblique case (genitive ``root`` + ``i`` +
    optional case suffix). The trailing ``\\b`` enforced by the caller
    rejects derivational endings (``ametnik``/``ametlik``/``ametkond``)
    because the character after ``amet`` is then a consonant that is
    neither ``i`` nor a word boundary."""
    return rf"{root}(?:i(?:{_ROOT_CASE_ALT})?|it)?"


# Issue #259: normalized slugs that the generic *amet pattern can still
# legitimately match (valid case form of an ``amet`` stem) but that are
# never real institutions. ``mitteamet`` ("non-/un-office", from the
# negating prefix ``mitte-``) is the canonical offender — it produced a
# committed ``institution_mitteamet.json`` from corpus genitive forms like
# ``mitteameti`` / ``mitteametile``. Matched against the NORMALIZED slug.
_INSTITUTION_STOPLIST: frozenset[str] = frozenset({
    "mitteamet",
})


# Generic patterns: regex → (label template, IRI template, inst type)
# Issue #170 Finding 3: ministry/agency/inspektsioon patterns now run with
# re.IGNORECASE so sentence-internal forms like "siseministeeriumi" or
# "rahandusministeeriumile" match. The IRI suffix is then lowercased and
# de-inflected by normalize_iri_suffix().
GENERIC_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # Ministries: "Xministeerium" or "Xminister"
    (re.compile(r"\b([a-zäöüõšž]+(?:\s*-\s*ja\s+[a-zäöüõšž]+)*ministeerium\w*)\b",
                re.IGNORECASE | re.UNICODE),
     "ministry", "ministry"),
    # Issue #321: also match the genitive stem ``ministri`` so inflected
    # (oblique) forms — the most common in competence clauses
    # (``sotsiaalministril on õigus kehtestada``, ``haridusministri
    # käskkiri``) — are detected, not just the nominative ``minister``.
    # normalize_iri_suffix() (via _collapse_minister_stem) de-inflects the
    # match back to the nominative ``*minister`` slug.
    (re.compile(r"\b([a-zäöüõšž]+(?:minister|ministri)\w*)\b",
                re.IGNORECASE | re.UNICODE),
     "ministry", "ministry"),
    # Agencies: "Xamet", "Xinspektsioon". Issue #259: the trailing token
    # is constrained to a case-suffix alternation (see _root_inflection_group)
    # so derivational forms like "politseiametnik" (a person) and
    # "mitteametlikult" (an adverb) no longer match as agencies.
    (re.compile(
        r"\b([a-zäöüõšž]+(?:\s*-\s*ja\s+[a-zäöüõšž]+)*"
        + _root_inflection_group("amet") + r")\b",
        re.IGNORECASE | re.UNICODE),
     "agency", "agency"),
    (re.compile(
        r"\b([a-zäöüõšž]+" + _root_inflection_group("inspektsioon") + r")\b",
        re.IGNORECASE | re.UNICODE),
     "agency", "agency"),
    # Courts (generic)
    (re.compile(r"\b(kohus)\b", re.IGNORECASE), "court", "court"),
    # Local government
    (re.compile(r"\bkohalik(?:u)?\s+omavalitsus(?:e)?\b", re.IGNORECASE),
     "local_government", "local_government"),
    (re.compile(r"\b(?:linna|valla|alevi)volikogu(?:sse|lt|le|st|ga|l|s)?\b", re.IGNORECASE),
     "local_government_council", "local_government_body"),
    (re.compile(r"\b(?:linna|valla)valitsus(?:e(?:sse|le|lt|ga|st|s|l)?|se|t)?\b", re.IGNORECASE),
     "local_government_executive", "local_government_body"),
    (re.compile(r"\b(vald|linn)\b", re.IGNORECASE),
     "local_government", "local_government"),
]

# ---------- competence-assigning patterns ----------

# Issue #322 (+ #258, #373): a provision can match SEVERAL competence
# patterns at once (e.g. it both "annab loa" AND "teostab järelevalvet").
# The old logic returned the FIRST list match, so the more specific type was
# silently discarded — a licensing+supervision provision collapsed to
# `supervision` only because the supervision phrase sat above the licensing
# verb. detect_competence_type now collects EVERY match and returns the
# highest-SPECIFICITY type instead of depending on list order.
#
# Specificity ladder (highest wins; #322 mandate: licensing > enforcement >
# regulation > supervision > general):
#   5  licensing            — an explicit grant verb ("annab loa",
#                             "annab tegevusloa", ...). Outranks everything,
#                             including the verb-adjacent supervision phrase,
#                             so "annab loa ja teostab järelevalvet" is
#                             licensing (the #322 headline case).
#   4  supervision (phrase) — a verb-ADJACENT supervision phrase
#                             ("teostab järelevalvet" / "teeb järelevalvet").
#                             A genuine supervision assignment, so it must
#                             beat the bare enforcement verb "teostab" that
#                             the same phrase also contains (otherwise
#                             "teostab järelevalvet" would mis-type as
#                             enforcement).
#   3  enforcement          — "kontrollib" / "korraldab" / bare "teostab".
#   2  regulation           — "kehtestab".
#   1  supervision (noun)    — the bare ubiquitous noun "järelevalve"; the
#                             weakest signal, only decisive when nothing
#                             operative matched.
#   0  general              — "on pädev"; also the no-match default.
#
# Issue #373: "annab tegevusloa" / "väljastab tegevusloa" are added as
# licensing verbs. The bare \bloa\b / \bluba\b patterns can't match inside
# the compound "tegevusloa"/"tegevusluba" (no internal word boundary), so 6
# licensing-grant provisions previously fell through to the default
# `general`.
COMPETENCE_PATTERNS: list[tuple[re.Pattern, str, int]] = [
    # Licensing grant verbs — highest specificity.
    (re.compile(r"\bannab\s+loa\b", re.IGNORECASE), "licensing", 5),
    (re.compile(r"\bväljastab\s+luba\b", re.IGNORECASE), "licensing", 5),
    # Issue #373: compound "tegevusluba" forms (the \bloa\b/\bluba\b word
    # boundary fails inside the compound).
    (re.compile(r"\bannab\s+tegevusloa\b", re.IGNORECASE), "licensing", 5),
    (re.compile(r"\bväljastab\s+tegevusloa\b", re.IGNORECASE), "licensing", 5),
    # Verb-adjacent supervision phrases — genuine supervision assignment,
    # ranked above the bare enforcement verb "teostab" they also contain.
    (re.compile(r"järelevalvet\s+teostab", re.IGNORECASE), "supervision", 4),
    (re.compile(r"teostab\s+järelevalvet", re.IGNORECASE), "supervision", 4),
    (re.compile(r"teeb\s+järelevalvet", re.IGNORECASE), "supervision", 4),
    # Explicit enforcement / regulation verbs.
    (re.compile(r"\bkontrollib\b", re.IGNORECASE), "enforcement", 3),
    (re.compile(r"\bkorraldab\b", re.IGNORECASE), "enforcement", 3),
    (re.compile(r"\bteostab\b", re.IGNORECASE), "enforcement", 3),
    (re.compile(r"\bkehtestab\b", re.IGNORECASE), "regulation", 2),
    # Bare "järelevalve" noun — weakest signal.
    (re.compile(r"järelevalve", re.IGNORECASE), "supervision", 1),
    # "on pädev" — non-specific competence assertion; lowest, tied with the
    # no-match default.
    (re.compile(r"\bon\s+pädev\b", re.IGNORECASE), "general", 0),
]


def detect_institutions(text: str) -> list[tuple[str, str, str]]:
    """Return list of (canonical_name, normalized_iri_suffix, inst_type)
    found in *text*. Named institutions are checked first; generic
    patterns fill in the rest. All IRI suffixes are normalized to
    lowercase convention via normalize_iri_suffix().

    Issue #170 findings addressed:
      Finding 1. Substring matcher leaks — replaced ``substring in
         text.lower()`` with compiled ``\\b...\\b`` regexes
         (re.IGNORECASE | re.UNICODE for full-name entries,
         case-sensitive for abbreviation-only entries).
      Finding 2. Abbreviation entries (MTA / PPA) are now matched
         against the original-case text, and only register when the
         canonical full-name entry has NOT already matched in the same
         provision (full names take precedence so we don't emit a
         duplicate Institution_mta alongside
         Institution_maksu_ja_tolliamet).
      Finding 4. Generic ``kohus`` is suppressed when ANY of riigikohus
         / ringkonnakohus / halduskohus / maakohus appears in the text.
         The check is now a regex against the input text, not based on
         ``found`` accumulator state.
    """
    found: dict[str, tuple[str, str, str]] = {}

    # 1. Named institutions (first match for a given norm_suffix wins,
    #    so full-name entries should precede abbreviation-only entries
    #    in NAMED_INSTITUTIONS to keep the better canonical label).
    full_name_norm_suffixes_present: set[str] = set()
    for pattern, name, raw_suffix, itype in _NAMED_PATTERNS:
        if _is_abbreviation_entry(name):
            # Defer abbrev entries until after we know which full names
            # matched in this same text (Finding 2).
            continue
        if pattern.search(text):
            norm_suffix = normalize_iri_suffix(raw_suffix)
            full_name_norm_suffixes_present.add(norm_suffix)
            if norm_suffix not in found:
                found[norm_suffix] = (name, norm_suffix, itype)

    # 1b. Abbreviation-only entries — register only when the canonical
    #     full-name suffix is NOT already present in this provision.
    for pattern, name, raw_suffix, itype in _NAMED_PATTERNS:
        if not _is_abbreviation_entry(name):
            continue
        norm_suffix = normalize_iri_suffix(raw_suffix)
        if norm_suffix in full_name_norm_suffixes_present:
            continue
        if norm_suffix in found:
            continue
        if pattern.search(text):
            found[norm_suffix] = (name, norm_suffix, itype)

    # Issue #170 Finding 4: pre-compute "specific court mentioned" once
    # instead of relying on the ordering of NAMED_INSTITUTIONS or the
    # state of ``found``. Using the compiled pattern means the
    # suppression decision is robust no matter what generic patterns
    # fire.
    has_specific_court = bool(_SPECIFIC_COURT_PATTERN.search(text))

    # 2. Generic patterns (only if not already captured by a named entry)
    for pat, default_label, itype in GENERIC_PATTERNS:
        for m in pat.finditer(text):
            matched = m.group(1) if m.lastindex else m.group(0)
            raw_key = sanitize_id(matched)
            norm_key = normalize_iri_suffix(raw_key)
            # Issue #259: drop known non-institution slugs (e.g.
            # "mitteamet") that survive the regex as a valid case form.
            if norm_key in _INSTITUTION_STOPLIST:
                continue
            if norm_key and norm_key not in found:
                # Skip if this is just the generic "kohus" and a specific
                # court appears anywhere in the text (riigikohus,
                # ringkonnakohus, halduskohus, maakohus).
                if norm_key == "kohus" and has_specific_court:
                    continue
                # Use canonical label for local government (avoid inflected forms)
                if norm_key == "kohalik_omavalitsus":
                    matched = "Kohalik omavalitsus"
                found[norm_key] = (matched, norm_key, itype)

    return list(found.values())


def detect_competence_type(text: str) -> str:
    """Return the most specific competence type found in *text*.

    Issue #322: a provision may match several patterns at once (e.g. a
    licensing grant that also mentions supervision). We collect EVERY
    matching pattern and return the type with the highest specificity
    (licensing > supervision-phrase > enforcement > regulation >
    supervision-noun > general) instead of returning the first list match,
    which silently discarded the more specific type.
    """
    best_type = "general"
    best_specificity = -1
    for pat, ctype, specificity in COMPETENCE_PATTERNS:
        if specificity > best_specificity and pat.search(text):
            best_type = ctype
            best_specificity = specificity
    return best_type


def _fold_area_text(value: str) -> str:
    """Lowercase and ASCII-fold enough Estonian text for area keywords."""
    folded = value.lower()
    for src, dst in (
        ("õ", "o"), ("ä", "a"), ("ö", "o"), ("ü", "u"),
        ("š", "s"), ("ž", "z"),
    ):
        folded = folded.replace(src, dst)
    return folded


COMPETENCE_AREA_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("data_protection", ("andmekaitse", "isikuand", "iks")),
    ("tax", ("maksu", "tolli", "mks", "kms", "tums", "smms", "aktsiis")),
    ("environment", ("keskkonna", "looduskaitse", "jaat", "mets", "veesead", "keus", "lks")),
    ("health", ("tervise", "ravimi", "haige", "patsient", "meditsiin")),
    ("transport", ("transpordi", "maantee", "raudtee", "lennu", "laeva", "liiklus", "uts")),
    ("education", ("haridus", "kool", "oppe", "noorte", "pgs", "kels_lasteas")),
    ("labour", ("tooinspektsioon", "tootaja", "toolep", "tootus", "tls", "ttks")),
    ("internal_security", ("politsei", "piirivalve", "paaste", "kapo", "kaitsepolitsei", "siseministeerium")),
    ("justice", ("kohus", "prokur", "justiits", "kohtutaitur", "notar", "kriminaal", "tsiviil")),
    ("consumer_protection", ("tarbijakaitse", "tarbija", "ttja")),
    ("finance", ("finants", "pank", "kindlustus", "rahandus", "eelarve")),
    ("agriculture", ("pollu", "veterinaar", "toidu", "kala")),
    ("culture", ("kultuur", "muinsus", "raamatukogu")),
    ("social_protection", ("sotsiaal", "sotsiaalkindlustus", "pension", "toetus")),
    ("local_government", ("kohalik_omavalitsus", "vallavalitsus", "linnavalitsus", "volikogu")),
    ("general_government", ("vabariigivalitsus", "riigikogu", "ministeerium", "minister", "juhtministeerium", "president")),
)


def infer_competence_area(
    institution_name: str,
    iri_suffix: str,
    competence_type: str,
    source_act_refs: list[str],
) -> str:
    """Infer a coarse area from institution identity and source-act hints."""
    haystack = _fold_area_text(
        " ".join([institution_name, iri_suffix, competence_type, *source_act_refs])
    )
    for area, keywords in COMPETENCE_AREA_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return area
    return "general"


def _select_granted_by(source_act_refs: list[str]) -> str | None:
    """Choose one granting act IRI for a competence node.

    The sidecar groups provisions by institution and competence type, so very
    broad competences can span many unrelated acts. estleg:grantedBy is
    documented as "the majority source act", so we only emit it for a STRICT
    plurality and abstain otherwise (Issue #274):

      * A tie for the top spot (``len(top_refs) > 1``) has no single majority
        act, so we return None rather than fabricating a deterministic-but-
        arbitrary winner from sort order.
      * An all-singleton spread (``top_count == 1`` with more than one act)
        likewise has no plurality — return None regardless of how many acts
        there are (the old guard only abstained when ``len(counts) > 3``,
        so e.g. a 1-1 tie or three singletons leaked an arbitrary winner).

    The sole singleton (``top_count == 1`` and ``len(counts) == 1``) is a
    genuine unanimous act and is returned.
    """
    counts = Counter(
        ref for ref in source_act_refs
        if isinstance(ref, str) and ref.startswith("estleg:")
    )
    if not counts:
        return None
    top_count = max(counts.values())
    top_refs = sorted(ref for ref, count in counts.items() if count == top_count)
    # No strict plurality: the top is tied between two or more acts.
    if len(top_refs) > 1:
        return None
    # No plurality at all: every act appears exactly once (and there's more
    # than one of them).
    if top_count == 1 and len(counts) > 1:
        return None
    return top_refs[0]


def _swap_issuer_body_suffix(
    source_issuer: str, target_body_slug: str
) -> str | None:
    """Given an Issuer @id like 'estleg:Issuer_abja_vallavolikogu'
    and a target body slug like 'vallavalitsus', return the paired
    Issuer @id 'estleg:Issuer_abja_vallavalitsus'.

    Returns None when:
    - The source IRI doesn't match the expected
      `Issuer_<place>_<body>` shape.
    - The source body and target body belong to DIFFERENT
      municipal families (linna* / valla* / alevi* are mutually
      exclusive). A linnavalitsus source must NOT pair with
      vallavolikogu target — same historical-conflation problem
      Path 1's suffix check rejects.
    """
    if not source_issuer.startswith("estleg:Issuer_"):
        return None
    suffix = source_issuer[len("estleg:Issuer_"):]
    source_body = None
    place = None
    for body in ("vallavolikogu", "vallavalitsus", "linnavolikogu",
                  "linnavalitsus", "alevivolikogu"):
        if suffix.endswith("_" + body):
            source_body = body
            place = suffix[: -len("_" + body)]
            break
    if source_body is None or place is None:
        return None

    family_prefixes = ("linna", "valla", "alevi")
    source_family = next(
        (p for p in family_prefixes if source_body.startswith(p)), None)
    target_family = next(
        (p for p in family_prefixes if target_body_slug.startswith(p)), None)
    if source_family is None or target_family is None:
        return None
    if source_family != target_family:
        return None

    return f"estleg:Issuer_{place}_{target_body_slug}"


def _resolve_kov_authority(
    body_slug: str,
    source_municipality: str | None,
    source_issuer: str | None,
    issuer_registry: dict[str, tuple[str, str, str]],
) -> str | None:
    """Resolve a KOV body-word detection to an Issuer @id.

    Three priority paths (in order):
      1. Same body type AND slug suffix match source_issuer →
         return source_issuer.
      2. Opposite body type → derive paired issuer slug; return
         it if registered AND in same municipality.
      3. Path 3 fallback: registry-wide unique match in
         source_municipality. Reached ONLY when source_issuer is
         missing, unknown to the registry, or has municipality
         inconsistent with the act's stated enactedByMunicipality.

    When source_issuer is known and consistent (Path 1+2
    authoritative), Path 3 is NOT consulted — abstain instead.

    Args:
        body_slug: Canonical body slug from _canonical_body_slug().
            Must be one of: linnavolikogu, vallavolikogu, alevivolikogu,
            linnavalitsus, vallavalitsus. Inflected forms are not
            accepted (body_type_map returns None → resolver abstains).
        source_municipality: Plain-string IRI of estleg:enactedByMunicipality,
            unwrapped from JSON-LD {"@id": "..."} form via _id_ref().
            None when the source is a Law or state regulation.
        source_issuer: Plain-string IRI of estleg:enactedBy, unwrapped via
            _id_ref(). None when absent or non-KOV.
        issuer_registry: issuer @id → (label, municipality_iri, body_type)
            from build_issuer_registry().
    """
    if source_municipality is None:
        return None

    body_type_map = {
        "linnavolikogu": "volikogu",
        "vallavolikogu": "volikogu",
        "alevivolikogu": "volikogu",
        "linnavalitsus": "valitsus",
        "vallavalitsus": "valitsus",
    }
    target_body_type = body_type_map.get(body_slug)
    if target_body_type is None:
        return None

    source_issuer_authoritative = False
    if source_issuer is not None:
        source_entry = issuer_registry.get(source_issuer)
        if source_entry is not None:
            _label, source_mun, source_body_type = source_entry
            if source_mun == source_municipality:
                source_issuer_authoritative = True

                if (source_body_type == target_body_type
                        and source_issuer.endswith("_" + body_slug)):
                    return source_issuer

                if source_body_type != target_body_type:
                    paired_iri = _swap_issuer_body_suffix(source_issuer, body_slug)
                    if paired_iri is not None and paired_iri in issuer_registry:
                        paired_entry = issuer_registry.get(paired_iri)
                        if (paired_entry is not None
                                and paired_entry[1] == source_municipality):
                            return paired_iri
                    return None

                return None

    if source_issuer_authoritative:
        return None
    suffix = "_" + body_slug
    matches = [
        issuer_iri
        for issuer_iri, (_label, mun_iri, btype) in issuer_registry.items()
        if mun_iri == source_municipality
        and btype == target_body_type
        and issuer_iri.endswith(suffix)
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _is_path3_case(
    source_issuer: str | None,
    source_municipality: str | None,
    issuer_registry: dict[str, tuple[str, str, str]],
) -> bool:
    """Return True iff the resolver's Path 1+2 are not
    authoritative for this case (i.e. Path 3 is what would run).
    Mirrors the resolver's source_issuer_authoritative check;
    used by main() to count fallback_hits."""
    if source_municipality is None:
        return False
    if source_issuer is None:
        return True
    entry = issuer_registry.get(source_issuer)
    if entry is None:
        return True
    _label, source_mun, _btype = entry
    return source_mun != source_municipality


# Issue #170 Finding 7: truncation cap for estleg:appliesToProvision.
# Major institutions (Vabariigi Valitsus, Riigikogu, ...) easily exceed
# this; main() now emits the FULL list AND records a count plus a warning
# whenever truncation would have lost provisions. Tests that need to
# verify behaviour around this threshold can pin _APPLIES_TO_PROVISION_CAP
# directly.
_APPLIES_TO_PROVISION_CAP = 50


def _load_canonical_institutions(directory: Path) -> set[str]:
    """Issue #170 Finding 8: load the registry of canonical institution
    suffixes by inspecting the existing per-institution JSON files.

    The registry feeds detect-time validation: any institution whose
    normalized iri_suffix isn't in the registry is dropped (or routed to
    ``_unverified`` if a future PR opts to surface them). When the
    directory is empty (first-run bootstrap, or test run with no fixture
    files), an empty set is returned and validation is skipped — we don't
    want to refuse to emit institutions on a freshly-cloned tree.

    Issue #118: slugs are returned underscore-collapsed so they compare
    equal to whatever ``normalize_iri_suffix`` produces. Without this, a
    stale double-underscore filename on disk (``institution_maksu__ja_
    tolliamet.json``, the pre-#118 naming) would not match the collapsed
    ``maksu_ja_tolliamet`` suffix the normaliser emits — so every
    predecessor-name reference (``Maksuamet`` → ``maksu_ja_tolliamet``
    via the alias table) would be dropped as ``unknown_institution`` and
    the alias target would never get a file. Collapsing here lets the
    re-run create the canonical-named file and prune the stale one.
    """
    suffixes: set[str] = set()
    if directory.exists():
        for path in directory.glob("institution_*.json"):
            slug = path.stem.removeprefix("institution_")
            if slug:
                suffixes.add(re.sub(r"_+", "_", slug).strip("_"))
    # Issue #118: when the directory holds at least one institution file we
    # also seed the curated canonical slugs — the named-institution
    # catalogue and every alias `canonical` target — so the registry
    # check accepts them even on a re-run where a stale double-underscore
    # file for one of them was just pruned (the file gets re-created with
    # the canonical name this pass). When the directory is empty we leave
    # the set empty so a freshly-cloned tree still bootstraps everything.
    if suffixes:
        suffixes |= _curated_canonical_suffixes()
    return suffixes


def _curated_canonical_suffixes() -> set[str]:
    """Issue #118: the always-valid canonical institution slugs — every
    named-institution IRI suffix (after normalisation) plus every alias
    `canonical` target. Used to seed the registry so curated agencies are
    never dropped as ``unknown_institution`` even when their on-disk file
    is briefly absent (e.g. a stale double-underscore name being pruned)."""
    out: set[str] = set()
    for _name, raw_suffix, _itype in NAMED_INSTITUTIONS:
        out.add(normalize_iri_suffix(raw_suffix))
    out |= set(_INSTITUTION_ALIASES.values())
    return out


class _PipelineState:
    """Bundle of mutable counters/state shared by the per-file processing
    helpers. Pulled out of main() so process_law_file / write_*
    helpers (Issue #170 Finding 11 — main was 370+ lines) can update one
    shared object instead of returning a tuple of bookkeeping deltas."""

    def __init__(self) -> None:
        self.inst_data: dict[str, dict] = {}
        # institution IRI → list of (provision IRI, competence_type, law_name)
        self.inst_provisions: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        # institution IRI → set of (provision_iri, competence_type, law_name)
        # tuples seen so far. Finding 6: dedup at append-time across ALL
        # provisions (and across the whole run), not just within one provision.
        self.inst_provision_keys: dict[str, set[tuple[str, str, str]]] = defaultdict(set)

        self.files_processed: set[Path] = set()
        self.files_processed_kov: set[Path] = set()
        self.files_with_output: set[Path] = set()
        self.files_with_output_kov: set[Path] = set()
        self.triples = 0
        self.triples_kov = 0
        self.files_skipped = 0
        self.skip_reasons: dict[str, int] = {}
        self.failures: list[str] = []

        self.total_provisions = 0
        self.provisions_with_institutions = 0
        self.unresolved_count = 0
        self.fallback_hits = 0
        self.per_peep_errors = 0
        # Finding 8: count of detected suffixes that didn't match the
        # canonical registry; surfaced in the coverage report.
        self.unknown_institution_count = 0
        # Finding 7: institutions whose appliesToProvision list would have
        # been truncated. Logged once per (institution, ctype) pair.
        self.truncated_institution_competences: set[tuple[str, str]] = set()


def _record_provision_for_institution(
    state: _PipelineState,
    inst_iri: str,
    canon_name: str,
    iri_suffix: str,
    itype: str,
    provision_iri: str,
    competence_type: str,
    law_name: str,
) -> None:
    """Update ``state.inst_data`` and ``state.inst_provisions`` with a new
    (provision, competence-type, law) tuple, deduping at append-time.

    Issue #170 Finding 6: the per-institution dedup is now keyed by the
    full triple, so the same provision IRI never lands in
    ``inst_provisions[inst_iri]`` twice — even if main() is called
    repeatedly via the same module instance, or if the same provision
    triggers the same institution detection across multiple summaries.
    """
    if inst_iri not in state.inst_data:
        state.inst_data[inst_iri] = {
            "name": canon_name,
            "iri_suffix": iri_suffix,
            "type": itype,
        }
    key = (provision_iri, competence_type, law_name)
    if key in state.inst_provision_keys[inst_iri]:
        return
    state.inst_provision_keys[inst_iri].add(key)
    state.inst_provisions[inst_iri].append(key)


def _process_provision_node(
    node: dict,
    state: _PipelineState,
    source_municipality: str | None,
    source_issuer: str | None,
    issuer_registry: dict[str, tuple[str, str, str]],
    canonical_suffixes: set[str],
    law_name: str,
) -> bool:
    """Detect institutions in one provision node, mutate the node with
    competentAuthority/competenceType triples, and update ``state``.

    Returns True iff the node was mutated.
    """
    summary = jsonld_text(node.get("estleg:summary", ""))
    if not summary:
        return False

    state.total_provisions += 1
    institutions = detect_institutions(summary)
    if not institutions:
        return False

    state.provisions_with_institutions += 1
    competence_type = detect_competence_type(summary)
    provision_iri = node.get("@id", "")

    authority_refs: list[dict] = []
    for canon_name, iri_suffix, itype in institutions:
        if itype == "local_government_body":
            canonical = _canonical_body_slug(canon_name)
            if canonical is None:
                continue
            issuer_iri = _resolve_kov_authority(
                body_slug=canonical,
                source_municipality=source_municipality,
                source_issuer=source_issuer,
                issuer_registry=issuer_registry,
            )
            if issuer_iri is None:
                state.unresolved_count += 1
                continue
            if _is_path3_case(
                source_issuer=source_issuer,
                source_municipality=source_municipality,
                issuer_registry=issuer_registry,
            ):
                state.fallback_hits += 1
            authority_refs.append({"@id": issuer_iri})
            continue

        # Issue #170 Finding 8: validate against the canonical
        # 126-institution registry. When the registry is empty
        # (bootstrap mode), accept every detection so a clean tree can
        # populate the registry from scratch.
        if canonical_suffixes and iri_suffix not in canonical_suffixes:
            state.unknown_institution_count += 1
            continue

        # Existing Institution_* path for everything else
        inst_iri = f"estleg:Institution_{iri_suffix}"
        _record_provision_for_institution(
            state=state,
            inst_iri=inst_iri,
            canon_name=canon_name,
            iri_suffix=iri_suffix,
            itype=itype,
            provision_iri=provision_iri,
            competence_type=competence_type,
            law_name=law_name,
        )
        authority_refs.append({"@id": inst_iri})

    # Dedupe authority_refs by @id (preserve first-occurrence order).
    # Required because a single provision can mention the same body
    # multiple times (different inflections), and each match would
    # otherwise emit a separate competentAuthority ref.
    seen: set[str] = set()
    deduped: list[dict] = []
    for ref in authority_refs:
        iri = ref.get("@id") if isinstance(ref, dict) else None
        if iri is None or iri in seen:
            continue
        seen.add(iri)
        deduped.append(ref)
    authority_refs = deduped

    if authority_refs:
        node["estleg:competentAuthority"] = authority_refs
        node["estleg:competenceType"] = competence_type
        return True
    return False


def process_law_file(
    filepath: Path,
    state: _PipelineState,
    issuer_registry: dict[str, tuple[str, str, str]],
    canonical_suffixes: set[str],
) -> None:
    """Load a peep file, detect institutions in every provision, write
    competentAuthority/competenceType triples, and persist the result.

    Updates ``state`` for coverage-report bookkeeping (Issue #170 Finding
    11 refactor — main() was 370+ lines).
    """
    doc = load_json(filepath)
    if doc is None or "@graph" not in doc:
        state.per_peep_errors += 1
        state.failures.append(
            f"{filepath.name}: JSON load failed or missing @graph"
        )
        return

    act_node = _find_act_node(doc)
    if act_node is None:
        # Aggregate-registry peeps (issuers_kov_peep.json,
        # municipalities_peep.json, etc.) have no provisions —
        # silently skip (no counter, no failure log).
        # Malformed act peeps DO have provisions but no
        # estleg:Act + owl:Ontology root — that's a Layer 1
        # data bug worth surfacing.
        has_provisions = any(
            "estleg:paragrahv" in n
            for n in doc.get("@graph", [])
        )
        if has_provisions:
            state.files_skipped += 1
            state.skip_reasons["missing_act_node"] = (
                state.skip_reasons.get("missing_act_node", 0) + 1
            )
            state.failures.append(
                f"{filepath.name}: malformed peep — has provisions "
                f"but missing estleg:Act + owl:Ontology root node"
            )
        return

    source_municipality = _id_ref(act_node.get("estleg:enactedByMunicipality"))
    source_issuer = _id_ref(act_node.get("estleg:enactedBy"))

    is_kov = "regulations/kov/" in str(filepath)
    state.files_processed.add(filepath)
    if is_kov:
        state.files_processed_kov.add(filepath)

    # Detect prior peep-side output BEFORE clearing.
    had_existing_peep = any(
        ("estleg:competentAuthority" in n)
        or ("estleg:competenceType" in n)
        for n in doc["@graph"]
    )

    # Clear unconditionally — the per-provision loop below
    # re-emits when detection succeeds.
    for n in doc["@graph"]:
        n.pop("estleg:competentAuthority", None)
        n.pop("estleg:competenceType", None)

    # Store the granting act as an IRI when available; CompetenceShape
    # constrains estleg:grantedBy to IRI values.
    law_name = act_node.get("@id") or filepath.stem.replace("_peep", "")
    modified = False

    for node in doc["@graph"]:
        if _process_provision_node(
            node=node,
            state=state,
            source_municipality=source_municipality,
            source_issuer=source_issuer,
            issuer_registry=issuer_registry,
            canonical_suffixes=canonical_suffixes,
            law_name=law_name,
        ):
            modified = True

    # Note: triples counts COMPETENT-AUTHORITY REFERENCES
    # (elements of competentAuthority lists across all provisions),
    # not raw RDF triples — matches the analytical question
    # "how many provision→authority bindings did we produce?"
    if modified:
        state.files_with_output.add(filepath)
        if is_kov:
            state.files_with_output_kov.add(filepath)
        n_refs = sum(
            len(node.get("estleg:competentAuthority", []))
            if isinstance(node.get("estleg:competentAuthority"), list)
            else (1 if "estleg:competentAuthority" in node else 0)
            for node in doc.get("@graph", [])
        )
        state.triples += n_refs
        if is_kov:
            state.triples_kov += n_refs

    # Save when EITHER fresh output OR pre-existing output existed.
    if modified or had_existing_peep:
        save_json(filepath, doc)


def write_institution_files(state: _PipelineState) -> set[str]:
    """Emit one JSON-LD file per institution under INSTIT_DIR. Returns
    the set of slugs written (used for stale-file pruning).

    Issue #170 Finding 7: emit ALL provisions per competence type and
    add estleg:appliesToProvisionCount alongside the (capped) list.
    When the list would be truncated, log a single warning per
    (institution, competence_type) pair so the truncation is visible
    in the run output.
    """
    print(f"\n[2/4] Generating institution files ({len(state.inst_data)} institutions)...")

    # Build reverse map: canonical suffix → list of abbreviation aliases
    canonical_aliases: dict[str, list[str]] = defaultdict(list)
    for alias, canonical in SAMEAS_ALIASES.items():
        canonical_aliases[canonical].append(alias)

    written_slugs: set[str] = set()

    for inst_iri, info in sorted(state.inst_data.items()):
        provisions = state.inst_provisions[inst_iri]
        inst_node: dict = {
            "@id": inst_iri,
            "@type": ["owl:NamedIndividual", "estleg:Institution"],
            # #577: de-inflect generic-pattern labels to nominative (a no-op on
            # already-canonical named institutions).
            "rdfs:label": canonicalize_institution_label(info["name"]),
            "estleg:institutionType": info["type"],
        }

        # Add owl:sameAs links for abbreviation aliases of this institution
        suffix = info["iri_suffix"]
        if suffix in canonical_aliases:
            same_as = [
                {"@id": f"estleg:Institution_{a}"}
                for a in canonical_aliases[suffix]
            ]
            if len(same_as) == 1:
                inst_node["owl:sameAs"] = same_as[0]
            else:
                inst_node["owl:sameAs"] = same_as

        graph: list[dict] = [inst_node]

        # Group provisions by competence type, preserving the source act ref
        # used to derive estleg:grantedBy for the reified Competence node.
        by_competence: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for prov_iri, ctype, source_act_ref in provisions:
            by_competence[ctype].append((prov_iri, source_act_ref))

        competence_iris: list[str] = []
        for ctype, entries in sorted(by_competence.items()):
            prov_iris = [prov_iri for prov_iri, _source_act_ref in entries]
            source_act_refs = [source_act_ref for _prov_iri, source_act_ref in entries]
            applies_to = [{"@id": p} for p in prov_iris[:_APPLIES_TO_PROVISION_CAP]]
            total_count = len(prov_iris)
            if total_count > _APPLIES_TO_PROVISION_CAP:
                key = (inst_iri, ctype)
                if key not in state.truncated_institution_competences:
                    state.truncated_institution_competences.add(key)
                    print(
                        f"  WARN: truncating {info['name']} / {ctype} "
                        f"appliesToProvision from {total_count} → "
                        f"{_APPLIES_TO_PROVISION_CAP}; full count surfaced "
                        f"as estleg:appliesToProvisionCount"
                    )
            competence_iri = f"{inst_iri}_competence_{ctype}"
            competence_node = {
                "@id": competence_iri,
                "@type": ["owl:NamedIndividual", "estleg:Competence"],
                "rdfs:label": f"{info['name']} – {ctype}",
                "estleg:competenceType": ctype,
                "estleg:institution": {"@id": inst_iri},
                "estleg:appliesToProvision": applies_to,
                "estleg:appliesToProvisionCount": {
                    "@value": str(total_count),
                    "@type": "xsd:integer",
                },
                "estleg:competenceArea": infer_competence_area(
                    institution_name=info["name"],
                    iri_suffix=info["iri_suffix"],
                    competence_type=ctype,
                    source_act_refs=source_act_refs,
                ),
            }
            granted_by = _select_granted_by(source_act_refs)
            if granted_by:
                competence_node["estleg:grantedBy"] = {"@id": granted_by}
            graph.append(competence_node)
            competence_iris.append(competence_iri)

        if competence_iris:
            inst_node["estleg:hasCompetence"] = [
                {"@id": iri} for iri in competence_iris
            ]

        doc = {"@context": CONTEXT, "@graph": graph}
        filename = f"institution_{info['iri_suffix']}.json"
        save_json(INSTIT_DIR / filename, doc)
        written_slugs.add(info["iri_suffix"])

    return written_slugs


def write_report(state: _PipelineState, total_law_files: int) -> Path:
    """Emit the human-readable competence report."""
    print("\n[3/4] Generating report...")

    # Competence-type breakdown
    competence_counts: dict[str, int] = defaultdict(int)
    for provisions in state.inst_provisions.values():
        for _, ctype, _ in provisions:
            competence_counts[ctype] += 1

    # Laws per institution
    inst_law_counts: dict[str, int] = {}
    for inst_iri, provisions in state.inst_provisions.items():
        laws = {law for _, _, law in provisions}
        inst_law_counts[state.inst_data[inst_iri]["name"]] = len(laws)

    report = {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "summary": {
            "total_law_files": total_law_files,
            "total_provisions_with_text": state.total_provisions,
            "provisions_with_institutions": state.provisions_with_institutions,
            "unique_institutions": len(state.inst_data),
        },
        "by_competence_type": dict(sorted(competence_counts.items(), key=lambda x: -x[1])),
        "institutions_by_provision_count": {
            state.inst_data[k]["name"]: len(v)
            for k, v in sorted(state.inst_provisions.items(), key=lambda x: -len(x[1]))
        },
        "institutions_by_law_count": dict(
            sorted(inst_law_counts.items(), key=lambda x: -x[1])
        ),
        "institution_types": {
            info["type"]: info["name"]
            for info in sorted(state.inst_data.values(), key=lambda x: x["type"])
        },
    }

    report_path = KRR_DIR / "institutional_competence_report.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")
    return report_path


def write_coverage(state: _PipelineState, start_time: float) -> tuple[Path, list[Path]]:
    """Emit the KOV coverage report and return (report path, kov_files).

    Issue #170 Finding 8: ``unknown_institution_count`` is added to
    ``skip_reasons`` so it's visible in the JSON output without changing
    the CoverageReport schema.
    """
    # Second scan to get the full input universe for input_files_total /
    # input_files_kov. Cannot reuse law_files here: aggregate-registry
    # peeps were silently skipped above but still count toward the input
    # universe.
    all_input_files = list(iter_peep_files())
    kov_files = [p for p in all_input_files
                 if "regulations/kov/" in str(p)]

    skip_reasons = dict(state.skip_reasons)
    if state.unknown_institution_count:
        skip_reasons["unknown_institution"] = state.unknown_institution_count

    wall, rate, peak_mb = measure_runtime(start_time, len(state.files_processed))
    out_path = (KRR_DIR / "reports" / "kov"
                / "extract_institutional_competence_coverage.json")
    write_coverage_report(
        CoverageReport(
            pipeline="extract_institutional_competence",
            run_timestamp=PINNED_RUN_TIMESTAMP,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
            pipeline_version=resolve_pipeline_version(),
            input_files_total=len(all_input_files),
            input_files_kov=len(kov_files),
            files_processed=len(state.files_processed),
            files_processed_kov=len(state.files_processed_kov),
            files_with_output=len(state.files_with_output),
            files_with_output_kov=len(state.files_with_output_kov),
            files_skipped=state.files_skipped,
            skip_reasons=skip_reasons,
            triples_emitted=state.triples,
            triples_emitted_kov=state.triples_kov,
            unresolved_references=state.unresolved_count,
            fallback_hits=state.fallback_hits,
            wall_time_seconds=round(wall, 2),
            items_per_second=round(rate, 2),
            peak_memory_mb=round(peak_mb, 1),
            error_count=len(state.failures),
            failure_samples=state.failures,
        ),
        out_path,
    )
    print(f"\nCoverage report: {out_path}")
    print(f"  input_files_total: {len(all_input_files)} (KOV: {len(kov_files)})")
    print(f"  files_with_output: {len(state.files_with_output)} (KOV: {len(state.files_with_output_kov)})")
    print(f"  triples_emitted (authority references): {state.triples} (KOV: {state.triples_kov})")
    print(f"  unresolved_references: {state.unresolved_count}; fallback_hits: {state.fallback_hits}")
    if state.unknown_institution_count:
        print(f"  unknown_institution_count: {state.unknown_institution_count}")
    return out_path, kov_files


def main() -> int:
    print("=" * 70)
    print("Estonian Legal Ontology - Institutional Competence Extraction")
    print("=" * 70)

    # Issue #170 Finding 9: create the institutions directory only when
    # main() actually runs — keeps imports side-effect-free.
    _ensure_dirs()

    law_files = iter_peep_files()
    print(f"\n  Found {len(law_files)} law files to process")

    # Layer 2c PR #2: build issuer registry once at startup.
    issuer_registry = build_issuer_registry(
        KRR_DIR / "issuers_kov_peep.json"
    )

    # Issue #170 Finding 8: load the canonical 126-institution registry
    # from disk. Empty-set return means we're bootstrapping (no
    # validation).
    canonical_suffixes = _load_canonical_institutions(INSTIT_DIR)
    if canonical_suffixes:
        print(f"  Loaded {len(canonical_suffixes)} canonical institution suffixes")
    else:
        print("  No canonical institutions found — bootstrap mode "
              "(validation disabled)")

    print("\n[1/4] Processing law files (per-file idempotent)...")

    state = _PipelineState()
    start_time = time.perf_counter()

    pre_existing_institution_files: list[Path] = []
    if INSTIT_DIR.exists():
        pre_existing_institution_files = list(INSTIT_DIR.glob("institution_*.json"))

    for idx, filepath in enumerate(law_files, 1):
        process_law_file(
            filepath=filepath,
            state=state,
            issuer_registry=issuer_registry,
            canonical_suffixes=canonical_suffixes,
        )
        if idx % 100 == 0 or idx == len(law_files):
            print(f"  [{idx}/{len(law_files)}] processed – {len(state.inst_data)} institutions found")

    written_slugs = write_institution_files(state)
    write_report(state, total_law_files=len(law_files))

    # ---------- summary ----------
    print("\n[4/4] SUMMARY")
    print("=" * 70)
    print(f"  Total provisions analysed: {state.total_provisions}")
    print(f"  With institution refs:     {state.provisions_with_institutions}")
    print(f"  Unique institutions:       {len(state.inst_data)}")
    print(f"  Institution files:         {len(state.inst_data)}")
    print()
    print("  Top institutions by provision count:")
    top = sorted(state.inst_provisions.items(), key=lambda x: -len(x[1]))[:10]
    for inst_iri, provs in top:
        print(f"    {state.inst_data[inst_iri]['name']:45s}  {len(provs)} provisions")
    print("=" * 70)

    # Layer 2c PR #2: stale-file pruning. Only delete when the run
    # was clean (no per-peep load errors); otherwise we might delete
    # a file whose owning peep failed to load.
    if state.per_peep_errors == 0:
        for path in pre_existing_institution_files:
            slug = path.stem.removeprefix("institution_")
            if slug not in written_slugs:
                try:
                    path.unlink()
                except OSError as exc:
                    print(f"  WARN: could not delete stale file {path.name}: {exc}")
    else:
        print(f"[skip stale-deletion] {state.per_peep_errors} per-peep "
              f"errors during this run; preserving "
              f"{len(pre_existing_institution_files)} pre-existing "
              f"institution files.")

    _, kov_files = write_coverage(state, start_time)

    # Gate check (matches Layer 2c PR #1 convention; corpus KOV count ~11,059).
    if len(kov_files) >= 11000 and len(state.files_with_output_kov) == 0:
        print("\nGATE FAIL: KOV files were processed but none produced output.")
        return 1
    if state.triples_kov == 0 and len(kov_files) >= 11000:
        print("\nGATE FAIL: zero KOV triples emitted.")
        return 1
    print("\nGATE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
