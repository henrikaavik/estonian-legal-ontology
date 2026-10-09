#!/usr/bin/env python3
"""
Extract which institutions are responsible for what from law text.

Scans provision legalText (falling back to summary) for references to Estonian
state institutions and competence-assigning language, then creates
estleg:Institution nodes and links provisions to competent authorities.

Outputs:
  - krr_outputs/institutions/  (per-institution JSON-LD files)
  - krr_outputs/reports/institutional_competence_report.json

Binding rule (#718): an institution is a provision's ``estleg:competentAuthority``
only when, in the same clause, it is the subject of a competence verb
(``Keskkonnaamet annab loa``, ``järelevalvet teostab Keskkonnaamet``), the
adessive holder of a power (``ministril on õigus kehtestada``), or the
genitive agent of a delegated act (``kehtestatakse sotsiaalministri
määrusega``). Every other mention — consultation (``...ga kooskõlastatult``,
``... ettepanekul``/``nõusolekul``/``arvamusel``), descriptive genitives
(``kohaliku omavalitsuse arhiiv``), addressees — is recorded as
``estleg:mentionsInstitution``. The competence type is computed per binding
from that binding's verb phrase, not once for the whole provision.

Dry run: ``--dry-run --dry-run-report PATH`` scans the corpus in memory and
writes only the before/after measurement to PATH (no peep, institution or
report file is touched).
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from estleg.estleg_common import (
    BUILD_EVALUATION_DATE,
    CONTEXT,
    classifier_text,
    iter_peep_files,
    save_json,
)
from estleg.estleg_common import (
    sanitize_id as _shared_sanitize_id,
)
from estleg.extract_cross_references import build_issuer_registry
from estleg.heuristic_overrides import (
    COMPETENCE_PREDICATES,
    OverrideError,
    OverrideStore,
    check_overrides,
    clear_unowned,
    ensure_prov_context,
    finalize_node,
    load_overrides,
    print_check_report,
)
from estleg.extract_sanctions import _find_act_node
from estleg.generate_inverse_references import _ACT_ROOT_TYPES, _iri_values
from estleg.kov_pipeline_coverage import (
    PINNED_RUN_TIMESTAMP,
    CoverageReport,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
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


sanitize_id = partial(
    _shared_sanitize_id,
    max_len=80,
    replace_dash=True,
    collapse_underscores=True,
)


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


def _load_alias_records(path: Path = INSTITUTION_ALIASES_PATH) -> dict[str, dict]:
    """#718: full alias records ``{predecessor_slug: {label, canonical,
    evidence}}`` used to materialise one predecessor node per alias key."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = raw.get("aliases") if isinstance(raw, dict) else None
    if not isinstance(entries, dict):
        return {}
    out: dict[str, dict] = {}
    for key, value in entries.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        norm_key = re.sub(r"_+", "_", key.lower()).strip("_")
        if norm_key in _INSTITUTION_ALIASES:
            out[norm_key] = {
                "label": value.get("label") or key,
                "canonical": _INSTITUTION_ALIASES[norm_key],
                "evidence": value.get("evidence") or "",
            }
    return out


_ALIAS_RECORDS: dict[str, dict] = _load_alias_records()

# #718: every institution a provision mentions without being bound as its
# competent authority (consultation partner, addressee, descriptive
# genitive, predecessor name).
MENTIONS_INSTITUTION = "estleg:mentionsInstitution"

# #718: registrikood / X-tee member code / temporal validity / succession.
INSTITUTION_IDENTITY_PATH = REPO_ROOT / "data" / "institution_identity.json"


def load_institution_identity(path: Path = INSTITUTION_IDENTITY_PATH) -> dict[str, dict]:
    """Return ``{slug: identity record}`` from data/institution_identity.json."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = raw.get("institutions") if isinstance(raw, dict) else None
    return {k: v for k, v in (entries or {}).items() if isinstance(v, dict)}

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
    return _apply_alias(_deinflect_suffix(raw_suffix))


def _deinflect_suffix(raw_suffix: str) -> str:
    """Steps 1–4 of :func:`normalize_iri_suffix` — everything except the
    historical-merge alias rewrite (#718 needs the pre-alias slug to keep
    the predecessor mention)."""
    lower = re.sub(r"_+", "_", raw_suffix.lower()).strip("_")
    if lower in _ABBREVIATION_MAP:
        return _ABBREVIATION_MAP[lower]
    if lower in _INFLECTION_MAP:
        return _INFLECTION_MAP[lower]
    # Estonian case-suffix stripping — collapses inflected forms of *amet,
    # *ministeerium, *inspektsioon, *minister to their nominative stems.
    return _strip_estonian_case(lower)


def normalize_with_predecessor(raw_suffix: str) -> tuple[str, str | None]:
    """Return ``(canonical_slug, predecessor_slug)`` for a raw suffix (#718).

    ``predecessor_slug`` is the de-inflected slug when the alias table
    rewrote it onto a successor (``maanteeamet`` -> ``transpordiamet``),
    else ``None``. The canonical slug is exactly what
    :func:`normalize_iri_suffix` returns.
    """
    pre_alias = _deinflect_suffix(raw_suffix)
    canonical = _apply_alias(pre_alias)
    if canonical != pre_alias and pre_alias in _INSTITUTION_ALIASES:
        return canonical, pre_alias
    return canonical, None


# owl:sameAs aliases: abbreviation IRI → canonical IRI (both lowercase)
# These are emitted as owl:sameAs triples in institution definition files so
# that consumers can resolve either form.
SAMEAS_ALIASES: dict[str, str] = {
    "mta": "maksu_ja_tolliamet",
    "ppa": "politsei_ja_piirivalveamet",
    "ttja": "tarbijakaitse_ja_tehnilise_jarelevalve_amet",
    "harno": "haridus_ja_noorteamet",
}

# Abbreviation display labels for materialized alias individuals (#457).
SAMEAS_ALIAS_LABELS: dict[str, str] = {
    "mta": "MTA",
    "ppa": "PPA",
    "ttja": "TTJA",
    "harno": "Harno",
}

WIKIDATA_INSTITUTIONS_PATH = REPO_ROOT / "data" / "wikidata_institutions.json"
WIKIDATA_ENTITY_PREFIX = "http://www.wikidata.org/entity/"

# Predecessor agency kept as its own node after the 2021 merger (#457).
KESKKONNAINSPEKTSIOON_SLUG = "keskkonnainspektsioon"
KESKKONNAAMET_SLUG = "keskkonnaamet"

# Generic surface tokens that are not named state institutions (#457).
GENERIC_INSTITUTION_SLUGS = frozenset({"vald", "linn", "kohus"})


def load_wikidata_institutions(
    path: Path = WIKIDATA_INSTITUTIONS_PATH, *, include_see_also: bool = False
) -> dict[str, dict]:
    """Slug -> Wikidata record. By default only slugs with an identity
    ``qid``; ``include_see_also`` also keeps slugs that only carry a
    non-identity ``seeAlsoQid`` (#718)."""
    if not path.is_file():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {
        key: value
        for key, value in doc.items()
        if not key.startswith("_") and isinstance(value, dict)
        and (value.get("qid") or (include_see_also and value.get("seeAlsoQid")))
    }


def wikidata_iri_for_slug(slug: str, mapping: dict[str, dict] | None = None) -> str | None:
    payload = mapping if mapping is not None else load_wikidata_institutions()
    entry = payload.get(slug)
    if not isinstance(entry, dict):
        return None
    qid = entry.get("qid")
    if not isinstance(qid, str) or not qid.startswith("Q"):
        return None
    return f"{WIKIDATA_ENTITY_PREFIX}{qid}"


def wikidata_see_also_iri_for_slug(slug: str, mapping: dict[str, dict] | None = None) -> str | None:
    """Non-identity Wikidata link (#718): a concept-level class, or the
    continuous item a predecessor name shares with its successor."""
    payload = mapping if mapping is not None else load_wikidata_institutions(include_see_also=True)
    entry = payload.get(slug)
    qid = entry.get("seeAlsoQid") if isinstance(entry, dict) else None
    if not isinstance(qid, str) or not qid.startswith("Q"):
        return None
    return f"{WIKIDATA_ENTITY_PREFIX}{qid}"


_NAMED_BY_SUFFIX: dict[str, tuple[str, str]] | None = None


def named_institution_by_suffix() -> dict[str, tuple[str, str]]:
    """suffix → (canonical nominative name, type) from NAMED_INSTITUTIONS."""
    global _NAMED_BY_SUFFIX
    if _NAMED_BY_SUFFIX is None:
        out: dict[str, tuple[str, str]] = {}
        for name, raw_suffix, itype in NAMED_INSTITUTIONS:
            if _is_abbreviation_entry(name):
                continue
            out[normalize_iri_suffix(raw_suffix)] = (name, itype)
        _NAMED_BY_SUFFIX = out
    return _NAMED_BY_SUFFIX


def preferred_institution_type(suffix: str, itype: str) -> str:
    """Ministers are not ministries (#457)."""
    if suffix.endswith("minister") and not suffix.endswith("ministeerium"):
        return "minister"
    return itype


def _same_as_ids(node: dict) -> list[str]:
    value = node.get("owl:sameAs")
    if isinstance(value, dict) and isinstance(value.get("@id"), str):
        return [value["@id"]]
    if isinstance(value, list):
        return [
            item["@id"]
            for item in value
            if isinstance(item, dict) and isinstance(item.get("@id"), str)
        ]
    return []


def merge_same_as(node: dict, extra_iris: list[str]) -> None:
    ids = _same_as_ids(node)
    for iri in extra_iris:
        if iri and iri not in ids:
            ids.append(iri)
    if not ids:
        node.pop("owl:sameAs", None)
    elif len(ids) == 1:
        node["owl:sameAs"] = {"@id": ids[0]}
    else:
        node["owl:sameAs"] = [{"@id": iri} for iri in ids]


def _competence_label(name: str, ctype: str) -> str:
    return f"{canonicalize_institution_label(name)} – {ctype}"


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


# #718: genitive (oblique) stems of the named full-name entries whose head
# noun changes in the oblique cases. A named entry now also matches its
# genitive stem plus at most one case ending (``Vabariigi Valitsuse
# määrusega``, ``Riigikohtule``, ``Vabariigi Presidendi ettepanekul``) so the
# clause-level binder can see passive-delegation agents and consultation
# partners, not only nominative subjects. Entries whose oblique forms the
# generic *amet / *inspektsioon patterns already cover need no stem here.
_NAMED_OBLIQUE_STEMS: dict[str, str] = {
    "Vabariigi Valitsus": "Vabariigi Valitsuse",
    "Riigikogu": "Riigikogu",
    "Vabariigi President": "Vabariigi Presidendi",
    "Andmekaitse Inspektsioon": "Andmekaitse Inspektsiooni",
    "Tarbijakaitse ja Tehnilise Järelevalve Amet": "Tarbijakaitse ja Tehnilise Järelevalve Ameti",
    "Riigikohus": "Riigikohtu",
    "ringkonnakohus": "ringkonnakohtu",
    "halduskohus": "halduskohtu",
    "maakohus": "maakohtu",
}

# Case endings that may follow a named entry's oblique stem.
_NAMED_CASE_ENDINGS: str = "sse|ga|le|lt|st|ks|ni|na|ta|l|s|t"


def _compile_named_pattern(name: str) -> re.Pattern[str]:
    """Compile a word-boundary regex for a named institution.

    Abbreviation-only entries (MTA, PPA, ...) compile to a case-sensitive
    pattern; full-name entries compile case-insensitively. Estonian
    diacritics are preserved (UNICODE flag) so ``Järelevalve`` stays
    distinct from ``Jarelevalve``. Full-name entries with an oblique stem
    (#718) also match ``<nominative>t`` (partitive) and
    ``<oblique stem>[<case ending>]``.
    """
    flags = re.UNICODE
    if _is_abbreviation_entry(name):
        return re.compile(rf"\b{re.escape(name)}\b", flags)
    flags |= re.IGNORECASE
    oblique = _NAMED_OBLIQUE_STEMS.get(name)
    if oblique is None:
        return re.compile(rf"\b{re.escape(name)}\b", flags)
    return re.compile(
        rf"\b(?:{re.escape(oblique)}(?:{_NAMED_CASE_ENDINGS})?|{re.escape(name)}t?)\b",
        flags,
    )


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
    r"\b(?:riigi|ringkonna|haldus|maa)koh(?:us|tu)\w*\b",
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
     "minister", "minister"),
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


@dataclass(frozen=True)
class InstitutionMention:
    """One institution mention in a text (#718).

    ``name`` is the display name (canonical for named entries, the surface
    match for generic ones), ``suffix`` the canonical post-alias slug,
    ``predecessor`` the pre-alias slug when the alias table rewrote the
    mention onto a successor. ``start``/``end`` delimit the match;
    ``head_end`` extends past a governing head noun (``kohaliku
    omavalitsuse üksus``) when one follows, and ``nominative`` is the
    surface form the mention's case is measured against.
    """

    name: str
    suffix: str
    itype: str
    start: int
    end: int
    surface: str
    nominative: str
    predecessor: str | None = None
    head_end: int | None = None
    head_surface: str | None = None
    head_nominative: str | None = None


# #718: a ``kohaliku omavalitsuse`` match is usually a genitive modifier of
# a head noun (``kohaliku omavalitsuse üksus kehtestab``). The head carries
# the grammatical case, so the binder measures the case on it instead.
_KOV_HEAD_RE: re.Pattern[str] = re.compile(
    r"\s+(üksus|organ|volikogu|valitsus|asutus)(\w*)\b", re.IGNORECASE | re.UNICODE
)


def iter_institution_mentions(text: str) -> list[InstitutionMention]:
    """Return every institution mention in *text* with its span (#718).

    Order (and therefore first-mention label precedence) is the same as the
    historical :func:`detect_institutions`: named full-name entries first,
    then abbreviation-only entries, then generic patterns. Issue #170
    findings 1, 2 and 4 (word-boundary matching, abbreviations only without
    their full name, generic ``kohus`` suppressed next to a specific court)
    and #259's stoplist apply unchanged.
    """
    mentions: list[InstitutionMention] = []

    # 1. Named institutions.
    full_name_norm_suffixes_present: set[str] = set()
    for pattern, name, raw_suffix, itype in _NAMED_PATTERNS:
        if _is_abbreviation_entry(name):
            continue
        norm_suffix = normalize_iri_suffix(raw_suffix)
        for m in pattern.finditer(text):
            full_name_norm_suffixes_present.add(norm_suffix)
            mentions.append(InstitutionMention(
                name=name, suffix=norm_suffix, itype=itype, start=m.start(),
                end=m.end(), surface=m.group(0), nominative=name,
            ))

    # 1b. Abbreviation-only entries — only when the full name is absent.
    for pattern, name, raw_suffix, itype in _NAMED_PATTERNS:
        if not _is_abbreviation_entry(name):
            continue
        norm_suffix = normalize_iri_suffix(raw_suffix)
        if norm_suffix in full_name_norm_suffixes_present:
            continue
        for m in pattern.finditer(text):
            mentions.append(InstitutionMention(
                name=name, suffix=norm_suffix, itype=itype, start=m.start(),
                end=m.end(), surface=m.group(0), nominative=name,
            ))

    has_specific_court = bool(_SPECIFIC_COURT_PATTERN.search(text))

    # 2. Generic patterns.
    for pat, _default_label, itype in GENERIC_PATTERNS:
        for m in pat.finditer(text):
            group = 1 if m.lastindex else 0
            matched = m.group(group)
            start, end = m.span(group)
            raw_key = sanitize_id(matched)
            norm_key, predecessor = normalize_with_predecessor(raw_key)
            # Issue #259: drop known non-institution slugs (e.g. "mitteamet").
            if not norm_key or norm_key in _INSTITUTION_STOPLIST:
                continue
            if norm_key == "kohus" and has_specific_court:
                continue
            if any(
                o.suffix == norm_key and o.start < end and start < o.end
                for o in mentions
            ):
                continue  # the same institution already matched here
            label = matched
            nominative = canonicalize_institution_label(matched)
            head_end = head_surface = head_nominative = None
            if norm_key == "kohalik_omavalitsus":
                label = "Kohalik omavalitsus"
                nominative = "kohalik omavalitsus"
                head = _KOV_HEAD_RE.match(text, end)
                if head is not None:
                    head_end = head.end()
                    head_surface = head.group(1) + head.group(2)
                    head_nominative = head.group(1)
            elif itype == "local_government_body":
                nominative = _canonical_body_slug(matched) or matched
            mentions.append(InstitutionMention(
                name=label, suffix=norm_key, itype=itype, start=start, end=end,
                surface=matched, nominative=nominative, predecessor=predecessor,
                head_end=head_end, head_surface=head_surface,
                head_nominative=head_nominative,
            ))
    return mentions


def detect_institutions(text: str) -> list[tuple[str, str, str]]:
    """Return list of (canonical_name, normalized_iri_suffix, inst_type)
    found in *text*, one entry per institution (first mention wins).

    Thin wrapper over :func:`iter_institution_mentions` (#718), which
    carries the issue #170 findings 1/2/4 and #259 rules; this function
    keeps the historical de-duplicated tuple contract.
    """
    found: dict[str, tuple[str, str, str]] = {}
    for mention in iter_institution_mentions(text):
        if mention.suffix not in found:
            found[mention.suffix] = (mention.name, mention.suffix, mention.itype)
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


# ---------- #718: clause-level competence binding ----------
#
# An institution is a provision's competentAuthority only when the clause
# makes it the actor of a competence verb. Everything else it is merely
# mentioned in becomes estleg:mentionsInstitution. The rules, in order:
#
#   1. Law titles (``Vabariigi Valitsuse seadus``) are not mentions at all.
#   2. Consultation context right of the mention (``...ga kooskõlastatult``,
#      ``... ettepanekul`` / ``nõusolekul`` / ``arvamuse``) -> mention only.
#   3. Nominative subject: the mention precedes a finite competence verb
#      (or ``võib`` + its da-infinitive) with at most
#      _SUBJECT_MAX_GAP_TOKENS content words between, or follows it with at
#      most _INVERTED_MAX_GAP_TOKENS (Estonian V2 order: ``järelevalvet
#      teostab Keskkonnaamet``). Vowel-final names whose nominative equals
#      their genitive (``Riigikogu``, ``volikogu``) are treated as genitive
#      when a genitive head noun follows (``Riigikogu liige``).
#   4. Adessive holder: ``<X>l on õigus / pädevus / volitus ...``.
#   5. Genitive agent of a delegated act: ``<X> määrusega`` (or
#      ``käskkirjaga`` / ``korraldusega``) within reach of a passive present
#      verb (``kehtestatakse``, ``sätestatakse``, ...) — the passive
#      regulation-making form. Type: regulation for määrus, else the
#      verb-phrase type. ``<X> poolt`` + passive present verb is the agent too.
#   6. Anything else -> mention only (descriptive genitives such as
#      ``kohaliku omavalitsuse arhiiv``, addressees, objects).
#
# A verb phrase whose object is itself a consultation noun (``annab
# arvamuse``, ``teeb ettepaneku``) never binds.

_VOWELS = frozenset("aeiouõäöü")

# Lemma stem -> da-infinitive. Finite forms are <stem>b (3sg), <stem>vad
# (3pl) and ``ei <stem>`` (negation still assigns the power).
_COMPETENCE_VERB_STEMS: dict[str, str] = {
    "anna": "anda",
    "väljasta": "väljastada",
    "teosta": "teostada",
    "kontrolli": "kontrollida",
    "korralda": "korraldada",
    "kehtesta": "kehtestada",
    "otsusta": "otsustada",
    "määra": "määrata",
    "nimeta": "nimetada",
    "kinnita": "kinnitada",
    "lahenda": "lahendada",
    "menetle": "menetleda",
    "peata": "peatada",
    "tühista": "tühistada",
    "keela": "keelata",
    "nõua": "nõuda",
    "kohalda": "kohaldada",
    "rakenda": "rakendada",
    "tunnista": "tunnistada",
    "lõpeta": "lõpetada",
    "keeldu": "keelduda",
    "registreeri": "registreerida",
    "kooskõlasta": "kooskõlastada",
    "vaata": "vaadata",
    "võta": "võtta",
    "tee": "teha",
}
# Verbs that only assign competence together with a particle/object.
_VERB_REQUIRES: dict[str, re.Pattern[str]] = {
    "vaata": re.compile(r"^(?:\s+\S+){0,4}?\s+läbi\b", re.IGNORECASE),
    "võta": re.compile(r"^(?:\s+\S+){0,4}?\s+vastu\b", re.IGNORECASE),
    "tee": re.compile(
        r"^(?:\s+\S+){0,6}?\s+(?:järelevalvet|\w*otsus\w*|ettekirjutus\w*|"
        r"korraldus\w*|kontrolli\w*|määrus\w*)\b",
        re.IGNORECASE,
    ),
}


def _finite_forms(stem: str) -> str:
    # "tee" -> teeb/teevad; others regular.
    return rf"{stem}b|{stem}vad|ei\s+{stem}"


_FINITE_VERB_RE: re.Pattern[str] = re.compile(
    r"\b(?:"
    + "|".join(
        rf"(?P<f_{i}>{_finite_forms(stem)})"
        for i, stem in enumerate(_COMPETENCE_VERB_STEMS)
    )
    + r"|(?P<pad>on\s+pädev(?:ad)?))\b",
    re.IGNORECASE | re.UNICODE,
)
_MODAL_VERB_RE: re.Pattern[str] = re.compile(
    r"\b(?:võib|võivad|ei\s+või)(?:\s+\S+){0,2}?\s+(?P<inf>"
    + "|".join(re.escape(v) for v in _COMPETENCE_VERB_STEMS.values())
    + r")\b",
    re.IGNORECASE | re.UNICODE,
)
_STEM_BY_GROUP: dict[str, str] = {
    f"f_{i}": stem for i, stem in enumerate(_COMPETENCE_VERB_STEMS)
}
_STEM_BY_INFINITIVE: dict[str, str] = {
    inf: stem for stem, inf in _COMPETENCE_VERB_STEMS.items()
}

# Consultation / non-binding nouns. Right of a mention (within one word)
# they make it a consultation partner; as the object of a binding verb they
# make the verb phrase advisory, not competence.
CONSULTATION_BLOCKLIST: tuple[str, ...] = (
    "kooskõlastatult", "kooskõlastades", "kooskõlastusel", "kooskõlastuse",
    "kooskõlastust", "kooskõlastamiseks", "kooskõlastamisel", "arvamus",
    "arvamuse", "arvamust", "arvamusel", "arvamuseta", "ettepanek",
    "ettepanekul", "ettepaneku", "ettepanekut", "ettepanekust", "nõusolek",
    "nõusolekul", "nõusoleku", "nõusolekut", "nõusolekuga", "nõusolekuta",
    "loal", "loata", "koos",
)
_CONSULT_ALT = "|".join(CONSULTATION_BLOCKLIST)
_CONSULT_RIGHT_RE: re.Pattern[str] = re.compile(
    rf"^\W{{0,3}}(?:[\wäöüõšž-]+\s+)?(?:{_CONSULT_ALT})\b", re.IGNORECASE | re.UNICODE
)
_CONSULT_OBJECT_RE: re.Pattern[str] = re.compile(
    rf"^(?:\s+[\wäöüõšž-]+){{0,1}}?\s+(?:{_CONSULT_ALT})\b", re.IGNORECASE | re.UNICODE
)

# Head nouns after a vowel-final name that make the name a genitive
# modifier (``Riigikogu liige``, ``volikogu esimees``, ``Riigikogu otsusega``).
_GENITIVE_HEAD_RE: re.Pattern[str] = re.compile(
    r"^\s+(?:lii[gk]\w*|esim\w*|istung\w*|koosseis\w*|komisjon\w*|fraktsioon\w*|"
    r"otsus\w*|kantselei\w*|juhatus\w*|juhatu\w*|liikme\w*|töökor\w*|"
    r"kodu\w*|valimis\w*|aseesim\w*|ametiisik\w*|eelarve\w*|määrus\w*)\b",
    re.IGNORECASE | re.UNICODE,
)
# ``<genitive name> [up to 4 words] seadus...`` is a law title.
_LAW_TITLE_RE: re.Pattern[str] = re.compile(
    r"^((?:\s+[\wäöüõšž-]+){0,4}?)\s+seadus(?:e|t|es|est|ele|ega|ega|ele|tik\w*)?\b",
    re.IGNORECASE | re.UNICODE,
)
_ADESSIVE_POWER_RE: re.Pattern[str] = re.compile(
    r"^(?:\s+\S+){0,2}?\s+on\s+(?:\S+\s+){0,1}?(?:õigus|pädevus|ainupädevus|volitus|volitused)\b",
    re.IGNORECASE | re.UNICODE,
)
# Only the instrumental "by <X>'s act" forms delegate (``sotsiaalministri
# määrusega``); ``määrusele`` / ``määruse alusel`` merely cite an act.
_DELEGATED_ACT_RE: re.Pattern[str] = re.compile(
    r"^\s+(?P<act>määrusega|käskkirjaga|korraldusega)\b", re.IGNORECASE | re.UNICODE
)
_AGENT_POOLT_RE: re.Pattern[str] = re.compile(r"^\s+poolt\b", re.IGNORECASE)
# ``<X> poolt nimetatud isik`` is a descriptive participle, not an agent.
_POOLT_PARTICIPLE_RE: re.Pattern[str] = re.compile(
    r"^(?:\s+\S+){0,2}?\s+[\wäöüõšž]+(?:tud|dud)\b", re.IGNORECASE | re.UNICODE
)
# Passive present (impersonal) verbs: kehtestatakse, sätestatakse, antakse,
# kinnitatakse, nähakse (ette), määratakse, ... — never the participle
# ``kehtestatud``.
_PASSIVE_PRESENT_RE: re.Pattern[str] = re.compile(
    r"\b[\wäöüõšž]+(?:takse|akse)\b", re.IGNORECASE | re.UNICODE
)
_COORDINATION_RE: re.Pattern[str] = re.compile(r"^\s*(?:,|ja|või|ning)\s*$", re.IGNORECASE)
_PASSIVE_REACH_TOKENS = 6
_SUBJECT_MAX_GAP_TOKENS = 4
_INVERTED_MAX_GAP_TOKENS = 3
_GAP_IGNORED_TOKENS = frozenset({"ja", "ning", "või", "ega", "ka", "samuti"})

_CASE_BY_ENDING: dict[str, str] = {
    "": "genitive", "l": "adessive", "le": "allative", "lt": "ablative",
    "ga": "comitative", "s": "inessive", "st": "elative", "sse": "illative",
    "ks": "translative", "ni": "terminative", "na": "essive", "ta": "abessive",
    "t": "partitive",
}
_GENITIVE_STEMS: tuple[tuple[str, str], ...] = (
    ("kohalik omavalitsus", "kohaliku omavalitsuse"),
    ("ministeerium", "ministeeriumi"), ("minister", "ministri"),
    ("inspektsioon", "inspektsiooni"), ("omavalitsus", "omavalitsuse"),
    ("valitsus", "valitsuse"), ("president", "presidendi"), ("amet", "ameti"),
    ("kohus", "kohtu"), ("üksus", "üksuse"), ("asutus", "asutuse"),
    ("organ", "organi"),
)
_COMPETENCE_TYPE_RANK: dict[str, int] = {
    "licensing": 5, "supervision": 4, "enforcement": 3, "regulation": 2,
    "advisory": 1, "general": 0,
}

# Verb-phrase typing (#718): the #322 ladder plus plural / da-infinitive /
# passive forms of the same verbs, evaluated on ONE binding's verb phrase.
EXTENDED_COMPETENCE_PATTERNS: list[tuple[re.Pattern, str, int]] = [
    *COMPETENCE_PATTERNS,
    (re.compile(r"\b(?:anna(?:b|vad)?|anda|väljasta(?:b|vad|da)?)"
                r"(?:\s+\S+){0,3}?\s+\w*(?:loa|luba|load|lube)\b", re.IGNORECASE), "licensing", 5),
    (re.compile(r"\b(?:teostavad|teostada|teevad|teha)(?:\s+\S+){0,2}?\s+järelevalvet",
                re.IGNORECASE), "supervision", 4),
    (re.compile(r"järelevalvet\s+(?:teostavad|teostada|teevad|teha)\b", re.IGNORECASE),
     "supervision", 4),
    (re.compile(r"\b(?:kontrollivad|kontrollida|korraldavad|korraldada|teostavad|"
                r"teostada|ettekirjutus\w*)\b", re.IGNORECASE), "enforcement", 3),
    (re.compile(r"\b(?:kehtestavad|kehtestada|kehtestatakse)\b", re.IGNORECASE),
     "regulation", 2),
]


def detect_clause_competence_type(text: str) -> str:
    """Most specific competence type in ONE binding's verb phrase (#718).

    Same ladder as :func:`detect_competence_type` (licensing >
    supervision-phrase > enforcement > regulation > supervision-noun >
    general) over :data:`EXTENDED_COMPETENCE_PATTERNS`, which adds the
    plural, da-infinitive and passive forms.
    """
    best_type = "general"
    best_specificity = -1
    for pat, ctype, specificity in EXTENDED_COMPETENCE_PATTERNS:
        if specificity > best_specificity and pat.search(text):
            best_type = ctype
            best_specificity = specificity
    return best_type


def most_specific_competence_type(types: list[str] | set[str]) -> str:
    """Highest-ranked competence type of several bindings (#718)."""
    best = "general"
    for ctype in types:
        if _COMPETENCE_TYPE_RANK.get(ctype, 0) > _COMPETENCE_TYPE_RANK.get(best, 0):
            best = ctype
    return best


_LOIGE_SPLIT_RE: re.Pattern[str] = re.compile(r"\(\d+[¹²³⁴⁵⁶⁷⁸⁹⁰]*\)\s*")
_SENTENCE_SPLIT_RE: re.Pattern[str] = re.compile(
    r"(?<=[a-zäöüõšž\)”\"])[.!?]\s+(?=[A-ZÄÖÜÕŠŽ(„\"])"
)
_POINT_RE: re.Pattern[str] = re.compile(r"(?:^|\s)\d+[¹²³⁴⁵⁶⁷⁸⁹⁰]*\)\s+")


def split_competence_clauses(text: str) -> list[str]:
    """Split provision text into the clauses the binder evaluates (#718).

    Subsections ``(1)``, sentences and ``;``-separated parts are separate
    clauses. An enumeration ``<lead-in>: 1) ...; 2) ...`` yields one clause
    per point, each prefixed with the lead-in, so ``Keskkonnaamet: 1) annab
    loa; 2) teostab järelevalvet`` binds the lead-in subject to both verbs.
    """
    clauses: list[str] = []
    for part in _LOIGE_SPLIT_RE.split(text):
        for sentence in _SENTENCE_SPLIT_RE.split(part):
            sentence = sentence.strip()
            if not sentence:
                continue
            lead, colon, rest = sentence.partition(":")
            points = _POINT_RE.split(rest) if colon else []
            if colon and len(points) > 1:
                lead = lead.strip()
                for item in points:
                    for piece in item.split(";"):
                        piece = piece.strip(" ;.")
                        if piece:
                            clauses.append(f"{lead}: {piece}")
                continue
            clauses.extend(p.strip() for p in sentence.split(";") if p.strip())
    return clauses


def _norm_surface(value: str) -> str:
    return re.sub(r"[\s\-]+", " ", value.lower()).strip()


def _genitive_stem(nominative: str) -> str:
    for root, genitive in _GENITIVE_STEMS:
        if nominative.endswith(root):
            return nominative[: -len(root)] + genitive
    return nominative


def mention_case(surface: str, nominative: str) -> str:
    """Grammatical case of *surface* relative to its *nominative* (#718).

    Returns ``nom``, ``nom_gen`` (vowel-final nominative that is also the
    genitive, e.g. ``Riigikogu``), ``genitive``, one of the oblique case
    names in :data:`_CASE_BY_ENDING`, or ``oblique``.
    """
    s = _norm_surface(surface)
    n = _norm_surface(nominative)
    if s == n:
        return "nom_gen" if n and n[-1] in _VOWELS else "nom"
    if s == n + "t":
        return "partitive"
    g = _norm_surface(_genitive_stem(n))
    if s.startswith(g):
        return _CASE_BY_ENDING.get(s[len(g):], "oblique")
    return "oblique"


@dataclass(frozen=True)
class MentionBinding:
    """Binder verdict for one mention (#718)."""

    mention: InstitutionMention
    role: str          # "competent" | "mention" | "title"
    reason: str        # subject | inverted_subject | adessive_power |
                       # passive_agent | consultation | genitive | ... |
                       # no_competence_verb | law_title
    competence_type: str | None = None


def _content_tokens(text: str) -> list[str]:
    return [
        t for t in re.findall(r"[\wäöüõšž§]+", text.lower())
        if t not in _GAP_IGNORED_TOKENS
    ]


@dataclass(frozen=True)
class _VerbHit:
    start: int
    end: int
    stem: str | None   # None for "on pädev"


def _competence_verbs(clause: str) -> list[_VerbHit]:
    hits: list[_VerbHit] = []
    for m in _FINITE_VERB_RE.finditer(clause):
        if m.group("pad"):
            hits.append(_VerbHit(m.start(), m.end(), None))
            continue
        stem = _STEM_BY_GROUP[m.lastgroup] if m.lastgroup else None
        hits.append(_VerbHit(m.start(), m.end(), stem))
    for m in _MODAL_VERB_RE.finditer(clause):
        hits.append(_VerbHit(m.start(), m.end(), _STEM_BY_INFINITIVE[m.group("inf").lower()]))
    out: list[_VerbHit] = []
    for hit in sorted(hits, key=lambda h: (h.start, -h.end)):
        tail = clause[hit.end:]
        req = _VERB_REQUIRES.get(hit.stem or "")
        if req is not None and not req.match(tail):
            continue
        if _CONSULT_OBJECT_RE.match(tail):
            continue  # "annab arvamuse", "teeb ettepaneku" — advisory, not competence
        out.append(hit)
    return out


def _masked(clause: str, mentions: list[InstitutionMention]) -> str:
    chars = list(clause)
    for m in mentions:
        for i in range(m.start, m.head_end or m.end):
            chars[i] = " "
    return "".join(chars)


def _verb_phrase_type(masked: str, verb: _VerbHit) -> str:
    # Typed on the mention-masked clause so an institution's own name
    # (``Tehnilise Järelevalve Amet``) cannot read as a supervision verb.
    return detect_clause_competence_type(masked[max(0, verb.start - 30): verb.end + 100])


def _bind_mention(
    clause: str,
    mention: InstitutionMention,
    verbs: list[_VerbHit],
    masked: str,
) -> MentionBinding:
    end = mention.head_end or mention.end
    right = clause[end:]
    if mention.head_surface is not None:
        case = mention_case(mention.head_surface, mention.head_nominative or "")
    else:
        case = mention_case(mention.surface, mention.nominative)
    if case in ("genitive", "nom_gen"):
        title = _LAW_TITLE_RE.match(right)
        if title is not None and not _FINITE_VERB_RE.search(title.group(1) or ""):
            return MentionBinding(mention, "title", "law_title")
    if _CONSULT_RIGHT_RE.match(right):
        return MentionBinding(mention, "mention", "consultation")
    if case == "nom_gen" and _GENITIVE_HEAD_RE.match(right):
        case = "genitive"

    if case in ("nom", "nom_gen"):
        best: tuple[int, _VerbHit, str] | None = None
        for verb in verbs:
            if verb.start >= end:
                gap = len(_content_tokens(masked[end:verb.start]))
                if gap <= _SUBJECT_MAX_GAP_TOKENS and (best is None or gap < best[0]):
                    best = (gap, verb, "subject")
            elif verb.end <= mention.start:
                gap = len(_content_tokens(masked[verb.end:mention.start]))
                if gap <= _INVERTED_MAX_GAP_TOKENS and (best is None or gap < best[0]):
                    best = (gap, verb, "inverted_subject")
        if best is not None:
            _gap, verb, reason = best
            return MentionBinding(mention, "competent", reason, _verb_phrase_type(masked, verb))
        return MentionBinding(mention, "mention", "no_competence_verb")

    if case == "adessive":
        power = _ADESSIVE_POWER_RE.match(right)
        if power is not None:
            phrase = masked[end: end + power.end() + 100]
            return MentionBinding(
                mention, "competent", "adessive_power", detect_clause_competence_type(phrase)
            )
        return MentionBinding(mention, "mention", "adessive")

    if case == "genitive":
        act = _DELEGATED_ACT_RE.match(right)
        agent = _AGENT_POOLT_RE.match(right)
        if agent is not None and _POOLT_PARTICIPLE_RE.match(right[agent.end():]):
            agent = None
        anchor = act or agent
        if anchor is not None:
            anchor_end = end + anchor.end()
            window_lo = max(0, end - 80)
            window_hi = min(len(clause), anchor_end + 80)
            for pv in _PASSIVE_PRESENT_RE.finditer(clause, window_lo, window_hi):
                if pv.start() >= anchor_end:
                    gap = len(_content_tokens(masked[anchor_end:pv.start()]))
                elif pv.end() <= mention.start:
                    gap = len(_content_tokens(masked[pv.end():mention.start]))
                else:
                    continue
                if gap > _PASSIVE_REACH_TOKENS:
                    continue
                if act is not None and act.group("act").lower().startswith("määrus"):
                    return MentionBinding(mention, "competent", "passive_agent", "regulation")
                phrase = masked[min(pv.start(), mention.start): max(pv.end(), anchor_end) + 60]
                return MentionBinding(
                    mention, "competent", "passive_agent", detect_clause_competence_type(phrase)
                )
        return MentionBinding(mention, "mention", "genitive")

    return MentionBinding(mention, "mention", case)


def bind_clause(clause: str) -> list[MentionBinding]:
    """Binder verdicts for every institution mention in one clause."""
    mentions = sorted(iter_institution_mentions(clause), key=lambda m: m.start)
    if not mentions:
        return []
    verbs = _competence_verbs(clause)
    # A coordinated clause with its own named subject must be typed separately.
    # Keep coordinated subjects (X and Y inspect) and one subject's coordinated
    # powers (X inspects and grants licences) together.
    for mention in mentions[1:]:
        surface = mention.head_surface or mention.surface
        nominative = mention.head_nominative or mention.nominative
        if mention_case(surface, nominative) not in ("nom", "nom_gen"):
            continue
        separator = re.search(r"(?:,\s*|\b(?:ja|ning|kuid|aga)\s+)$", clause[:mention.start])
        if separator is None:
            continue
        boundary = separator.start()
        if any(v.end <= boundary for v in verbs) and any(
            v.start >= (mention.head_end or mention.end) for v in verbs
        ):
            return bind_clause(clause[:boundary]) + bind_clause(clause[mention.start:])
    masked = _masked(clause, mentions)
    bindings = [_bind_mention(clause, m, verbs, masked) for m in mentions]
    # Coordinated genitive agents (``Politsei- ja Piirivalveameti või
    # Kaitsepolitseiameti poolt``, ``X ja Y määrusega``): a genitive conjunct
    # inherits the passive-agent binding of the conjunct it is joined to.
    for i in range(len(bindings) - 2, -1, -1):
        here, nxt = bindings[i], bindings[i + 1]
        if here.reason != "genitive" or nxt.reason != "passive_agent":
            continue
        between = clause[(here.mention.head_end or here.mention.end):nxt.mention.start]
        if _COORDINATION_RE.match(between):
            bindings[i] = MentionBinding(
                here.mention, "competent", "passive_agent", nxt.competence_type
            )
    return bindings


@dataclass
class InstitutionBinding:
    """Per-institution verdict for a whole provision text (#718)."""

    name: str
    suffix: str
    itype: str
    competent: bool
    competence_type: str | None
    predecessors: tuple[str, ...]
    reasons: tuple[str, ...]
    # False when every mention used a predecessor name (``Maanteeametile``):
    # a non-binding mention then points only at the predecessor node.
    direct: bool = True


def bind_institutions(text: str) -> list[InstitutionBinding]:
    """Clause-level binding over a provision text (#718).

    One entry per institution (canonical slug) in first-mention order. An
    institution is competent when ANY of its mentions binds; its type is the
    most specific of its bindings' verb-phrase types. ``predecessors`` are
    the pre-alias slugs it was mentioned under (``maanteeamet``). Law-title
    mentions are dropped entirely.
    """
    order: list[str] = []
    info: dict[str, dict] = {}
    for clause in split_competence_clauses(text):
        for b in bind_clause(clause):
            m = b.mention
            if b.role == "title":
                continue
            # KOV body words keep their inflected slug in detect_institutions;
            # group them by the canonical body instead (resolved to an Issuer
            # later from the first surface form).
            key = m.nominative if m.itype == "local_government_body" else m.suffix
            entry = info.get(key)
            if entry is None:
                entry = {"name": m.name, "itype": m.itype, "types": [],
                         "predecessors": [], "reasons": [], "direct": False}
                info[key] = entry
                order.append(key)
            if b.role == "competent":
                entry["types"].append(b.competence_type or "general")
            if m.predecessor and m.predecessor not in entry["predecessors"]:
                entry["predecessors"].append(m.predecessor)
            if not m.predecessor:
                entry["direct"] = True
            entry["reasons"].append(b.reason)
    out: list[InstitutionBinding] = []
    for suffix in order:
        e = info[suffix]
        competent = bool(e["types"])
        out.append(InstitutionBinding(
            name=e["name"], suffix=suffix, itype=e["itype"], competent=competent,
            competence_type=most_specific_competence_type(e["types"]) if competent else None,
            predecessors=tuple(e["predecessors"]), reasons=tuple(e["reasons"]),
            direct=e["direct"],
        ))
    return out


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


# Issue #170 Finding 7 / #718: estleg:appliesToProvision used to be sliced
# to the first 50 provisions per Competence node. The slice arrived in the
# 2026-04 bulk fix (befc15816c) without a stated reason; the controlled
# vocabulary describes it as truncation "for size". It dropped 77% of the
# inverse edges (23,498 / 30,360), while the forward provision ->
# competentAuthority edges were never capped. Uncapped, the whole
# institutions/ sidecar stays a few MB, so the list is now complete and
# estleg:appliesToProvisionCount always equals its length. ``None`` means
# "no cap"; tests may pin an int to exercise the count field.
_APPLIES_TO_PROVISION_CAP: int | None = None


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
    # #718: predecessor nodes are mention targets, so their slugs are valid.
    out |= set(_INSTITUTION_ALIASES)
    return out


TRACKED_PROVISIONS: tuple[str, ...] = ("estleg:EHS_Par_26_1", "estleg:ARHIIV_Par_1")


def _target_family(iri: str) -> str:
    if iri.startswith("estleg:Institution_"):
        return "Institution"
    if iri.startswith("estleg:Issuer_"):
        return "Issuer"
    return "other"


@dataclass
class CompetenceMeasurement:
    """Before/after accounting for a dry run (#718).

    "Before" is what the corpus currently ships (``competentAuthority`` +
    one provision-level ``competenceType`` stamped on every authority);
    "after" is the clause-level binder's verdict on the same text.
    """

    before_by_type: Counter = field(default_factory=Counter)
    after_by_type: Counter = field(default_factory=Counter)
    after_provision_types: Counter = field(default_factory=Counter)
    transitions: Counter = field(default_factory=Counter)
    new_bindings_by_type: Counter = field(default_factory=Counter)
    before_by_family: Counter = field(default_factory=Counter)
    after_by_family: Counter = field(default_factory=Counter)
    before_institution_by_type: Counter = field(default_factory=Counter)
    after_institution_by_type: Counter = field(default_factory=Counter)
    mention_edges: int = 0
    provisions_before: int = 0
    provisions_after: int = 0
    tracked: dict = field(default_factory=dict)
    samples: dict = field(default_factory=lambda: defaultdict(list))

    def record(
        self,
        provision_iri: str,
        before: tuple[list[str], str | None],
        after_competent: dict[str, str],
        after_mentioned: list[str],
        text: str,
    ) -> None:
        before_ids, before_type = before
        if not text:
            return  # act roots etc.: act-level edges are rolled up later
        btype = before_type or "general"
        if before_ids:
            self.provisions_before += 1
        if after_competent:
            self.provisions_after += 1
            self.after_provision_types[most_specific_competence_type(
                list(after_competent.values()))] += 1
        for iri in before_ids:
            self.before_by_type[btype] += 1
            self.before_by_family[_target_family(iri)] += 1
            if iri.startswith("estleg:Institution_"):
                self.before_institution_by_type[btype] += 1
            if iri in after_competent:
                state = f"competent:{after_competent[iri]}"
            elif iri in after_mentioned:
                state = "mentionsInstitution"
            else:
                state = "dropped"
            self.transitions[(btype, state)] += 1
            key = f"{btype}->{state}"
            if len(self.samples[key]) < 12:
                self.samples[key].append({
                    "provision": provision_iri, "institution": iri,
                    "text": text[:300],
                })
        for iri, ctype in after_competent.items():
            self.after_by_type[ctype] += 1
            self.after_by_family[_target_family(iri)] += 1
            if iri.startswith("estleg:Institution_"):
                self.after_institution_by_type[ctype] += 1
            if iri not in before_ids:
                self.new_bindings_by_type[ctype] += 1
        self.mention_edges += len(after_mentioned)
        if provision_iri in TRACKED_PROVISIONS:
            self.tracked[provision_iri] = {
                "before": {"competentAuthority": before_ids, "competenceType": before_type},
                "after": {"competentAuthority": after_competent,
                          "mentionsInstitution": after_mentioned},
                "text": text[:500],
            }

    def as_dict(self, state: _PipelineState) -> dict:
        transitions: dict[str, dict[str, int]] = defaultdict(dict)
        for (btype, after_state), n in sorted(self.transitions.items()):
            transitions[btype][after_state] = n
        return {
            "provisions_with_text": state.total_provisions,
            "provisions_with_institution_mentions": state.provisions_with_institutions,
            "bindings_before_total": sum(self.before_by_type.values()),
            "bindings_before_by_type": dict(self.before_by_type.most_common()),
            "bindings_after_total": sum(self.after_by_type.values()),
            "bindings_after_by_type": dict(self.after_by_type.most_common()),
            "bindings_before_by_target": dict(self.before_by_family.most_common()),
            "bindings_after_by_target": dict(self.after_by_family.most_common()),
            "institution_bindings_before_by_type": dict(
                self.before_institution_by_type.most_common()),
            "institution_bindings_after_by_type": dict(
                self.after_institution_by_type.most_common()),
            "provisions_with_authority_before": self.provisions_before,
            "provisions_with_authority_after": self.provisions_after,
            "provision_competence_type_after": dict(self.after_provision_types.most_common()),
            "before_type_to_after_state": transitions,
            "new_bindings_not_in_shipped_data_by_type": dict(
                self.new_bindings_by_type.most_common()),
            "mentions_institution_edges_after": self.mention_edges,
            "binder_reasons": dict(state.binding_reasons.most_common()),
            "unknown_institution_skips": state.unknown_institution_count,
            "unresolved_kov_body_references": state.unresolved_count,
            "tracked_provisions": self.tracked,
            "samples": dict(self.samples),
        }


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
        # #700: human override store (empty unless main()/callers load one),
        # applied overrides, and the institution back-links an overridden
        # competentAuthority contributes. Back-links are recorded after the
        # whole corpus is scanned (record_override_links) so the result does
        # not depend on file order.
        self.overrides: OverrideStore = OverrideStore()
        self.overrides_applied = 0
        self.override_links: list[tuple[str, str, str, str]] = []
        self.override_links_skipped = 0
        # #718: mention-only institutions (IRI -> mention edge count), the
        # binder's per-mention reasons, and the number of mentionsInstitution
        # edges written.
        self.inst_mentions: Counter[str] = Counter()
        self.binding_reasons: Counter[str] = Counter()
        self.mention_edges = 0
        # #718 dry run: never write; collect the before/after measurement.
        self.dry_run = False
        self.measurement: CompetenceMeasurement | None = None
        # Per-institution competence types of the provision processed last.
        self.last_competent: dict[str, str] = {}


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
    named = named_institution_by_suffix().get(iri_suffix)
    if named:
        canon_name, named_type = named
        itype = named_type
    itype = preferred_institution_type(iri_suffix, itype)
    if inst_iri not in state.inst_data:
        state.inst_data[inst_iri] = {
            "name": canon_name,
            "iri_suffix": iri_suffix,
            "type": itype,
        }
    else:
        if named:
            state.inst_data[inst_iri]["name"] = canon_name
        state.inst_data[inst_iri]["type"] = preferred_institution_type(
            iri_suffix, state.inst_data[inst_iri].get("type") or itype
        )
    key = (provision_iri, competence_type, law_name)
    if key in state.inst_provision_keys[inst_iri]:
        return
    state.inst_provision_keys[inst_iri].add(key)
    state.inst_provisions[inst_iri].append(key)


def _resolve_binding_iri(
    binding: InstitutionBinding,
    state: _PipelineState,
    source_municipality: str | None,
    source_issuer: str | None,
    issuer_registry: dict[str, tuple[str, str, str]],
    canonical_suffixes: set[str],
) -> str | None:
    """Map one binder verdict to an Institution_* or Issuer_* IRI, or None.

    KOV body words resolve to the source act's Issuer (Layer 2c PR #2); a
    failure counts toward ``unresolved_count``. Everything else must be a
    canonical institution slug (#170 Finding 8; registry empty = bootstrap).
    """
    if binding.itype == "local_government_body":
        canonical = _canonical_body_slug(binding.name)
        if canonical is None:
            return None
        issuer_iri = _resolve_kov_authority(
            body_slug=canonical,
            source_municipality=source_municipality,
            source_issuer=source_issuer,
            issuer_registry=issuer_registry,
        )
        if issuer_iri is None:
            state.unresolved_count += 1
            return None
        if _is_path3_case(
            source_issuer=source_issuer,
            source_municipality=source_municipality,
            issuer_registry=issuer_registry,
        ):
            state.fallback_hits += 1
        return issuer_iri
    if canonical_suffixes and binding.suffix not in canonical_suffixes:
        state.unknown_institution_count += 1
        return None
    return f"estleg:Institution_{binding.suffix}"


def _process_provision_node(
    node: dict,
    state: _PipelineState,
    source_municipality: str | None,
    source_issuer: str | None,
    issuer_registry: dict[str, tuple[str, str, str]],
    canonical_suffixes: set[str],
    law_name: str,
    forced_competence_type: str | None = None,
) -> bool:
    """Bind institutions in one provision node (#718) and update ``state``.

    Writes ``estleg:competentAuthority`` (institutions the clause-level
    binder makes competent), ``estleg:competenceType`` (the most specific
    type among those bindings; per-institution types live on the Competence
    sidecar nodes) and ``estleg:mentionsInstitution`` (every other mentioned
    institution, plus the predecessor node of an alias-rewritten mention).

    ``forced_competence_type`` (#700) is a human-reviewed competenceType that
    replaces the detected one for every binding of this provision, including
    the institution back-links.

    Returns True iff the node was mutated.
    """
    state.last_competent = {}
    summary = classifier_text(node)
    if not summary:
        return False

    state.total_provisions += 1
    bindings = bind_institutions(summary)
    if not bindings:
        return False

    state.provisions_with_institutions += 1
    provision_iri = node.get("@id", "")

    competent: dict[str, str] = {}  # IRI -> competence type (first-occurrence order)
    mentioned: list[str] = []
    for binding in bindings:
        state.binding_reasons.update(binding.reasons)
        iri = _resolve_binding_iri(
            binding, state, source_municipality, source_issuer,
            issuer_registry, canonical_suffixes,
        )
        if iri is not None:
            if binding.competent:
                ctype = forced_competence_type or binding.competence_type or "general"
                previous = competent.get(iri)
                competent[iri] = (
                    most_specific_competence_type([previous, ctype]) if previous else ctype
                )
            elif binding.direct and iri not in mentioned:
                mentioned.append(iri)
        for predecessor in binding.predecessors:
            if canonical_suffixes and predecessor not in canonical_suffixes:
                continue
            pred_iri = f"estleg:Institution_{predecessor}"
            if pred_iri not in mentioned:
                mentioned.append(pred_iri)

    mentioned = [iri for iri in mentioned if iri not in competent]
    state.last_competent = dict(competent)

    for iri, ctype in competent.items():
        if not iri.startswith("estleg:Institution_"):
            continue
        suffix = iri.removeprefix("estleg:Institution_")
        binding = next(b for b in bindings if b.suffix == suffix)
        _record_provision_for_institution(
            state=state, inst_iri=iri, canon_name=binding.name, iri_suffix=suffix,
            itype=binding.itype, provision_iri=provision_iri,
            competence_type=ctype, law_name=law_name,
        )
    for iri in mentioned:
        state.mention_edges += 1
        if not iri.startswith("estleg:Institution_"):
            continue
        suffix = iri.removeprefix("estleg:Institution_")
        binding = next((b for b in bindings if b.suffix == suffix), None)
        _record_mention_for_institution(state, iri, suffix, binding)

    changed = False
    if competent:
        node["estleg:competentAuthority"] = [{"@id": iri} for iri in competent]
        node["estleg:competenceType"] = (
            forced_competence_type or most_specific_competence_type(list(competent.values()))
        )
        changed = True
    if mentioned:
        node[MENTIONS_INSTITUTION] = [{"@id": iri} for iri in mentioned]
        changed = True
    return changed


def _record_mention_for_institution(
    state: _PipelineState,
    inst_iri: str,
    suffix: str,
    binding: InstitutionBinding | None,
) -> None:
    """Make sure a mention-only institution gets an institution node (#718)
    so every ``estleg:mentionsInstitution`` target resolves."""
    state.inst_mentions[inst_iri] += 1
    if inst_iri in state.inst_data:
        return
    named = named_institution_by_suffix().get(suffix)
    if named is not None:
        name, itype = named
    elif binding is not None and binding.suffix == suffix:
        name, itype = binding.name, binding.itype
    else:
        alias = _ALIAS_RECORDS.get(suffix, {})
        name, itype = alias.get("label") or suffix, "agency"
    state.inst_data[inst_iri] = {
        "name": name,
        "iri_suffix": suffix,
        "type": preferred_institution_type(suffix, itype),
    }


def rollup_act_authorities(doc: dict, overrides: OverrideStore | None = None) -> tuple[int, int]:
    """Stamp each act root with the union of its provisions' authorities.

    Same contract as ``generate_inverse_references.materialize_act_root_aggregates``
    (#508) for ``estleg:competentAuthority``: a provision contributes to the act
    root its ``estleg:partOfAct`` points at, ids are de-duplicated in graph
    order, the shape is a list of ``{"@id"}``, and KOV ``Issuer_*`` bindings
    roll up like ``Institution_*`` ones. Unlike #508 it REPLACES the root's
    value instead of merging, because the competence pass cleared it (and
    #508's merge would keep stale ids forever). ``mentionsInstitution`` never
    rolls up. A root whose competentAuthority a human override owns (#700)
    is left alone.

    The competence pass clears act roots together with provisions, so without
    this the roots stayed empty until #508 re-ran, and
    materialize_combined_inverses (act-like subjects only) emitted no
    estleg:governs (#718 regression). Returns ``(roots changed, roots
    carrying at least one authority afterwards)``.
    """
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return 0, 0
    acts: list[dict] = []
    union: dict[str, list[str]] = defaultdict(list)
    for node in graph:
        if not isinstance(node, dict):
            continue
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if any(t in _ACT_ROOT_TYPES for t in types):
            acts.append(node)
            continue
        act_ids = _iri_values(node.get("estleg:partOfAct"))
        if not act_ids:
            continue
        for iri in _iri_values(node.get("estleg:competentAuthority")):
            if iri not in union[act_ids[0]]:
                union[act_ids[0]].append(iri)
    changed = stamped = 0
    for act in acts:
        act_id = act.get("@id")
        if not isinstance(act_id, str):
            continue
        if overrides is not None and overrides.owns(act_id, "estleg:competentAuthority"):
            stamped += bool(_iri_values(act.get("estleg:competentAuthority")))
            continue
        ids = union.get(act_id, [])
        new_val = [{"@id": iri} for iri in ids]
        if new_val:
            stamped += 1
            if act.get("estleg:competentAuthority") != new_val:
                act["estleg:competentAuthority"] = new_val
                changed += 1
        elif "estleg:competentAuthority" in act:
            del act["estleg:competentAuthority"]
            changed += 1
    return changed, stamped


def run_rollup_only() -> dict[str, int]:
    """#718: stamp act roots from the provision edges already on disk (no NLP)."""
    totals = {"files": 0, "files_written": 0, "act_roots": 0,
              "acts_with_authority_before": 0, "acts_with_authority_after": 0,
              "act_roots_changed": 0}
    for path in iter_peep_files():
        doc = load_json(path)
        if not isinstance(doc, dict):
            continue
        totals["files"] += 1
        for node in doc.get("@graph") or []:
            if not isinstance(node, dict):
                continue
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if any(t in _ACT_ROOT_TYPES for t in types):
                totals["act_roots"] += 1
                if _iri_values(node.get("estleg:competentAuthority")):
                    totals["acts_with_authority_before"] += 1
        changed, stamped = rollup_act_authorities(doc, load_overrides_cached())
        totals["acts_with_authority_after"] += stamped
        totals["act_roots_changed"] += changed
        if changed:
            save_json(path, doc)
            totals["files_written"] += 1
    return totals


_OVERRIDES_CACHE: list[OverrideStore] = []


def load_overrides_cached() -> OverrideStore:
    if not _OVERRIDES_CACHE:
        _OVERRIDES_CACHE.append(load_overrides(None))
    return _OVERRIDES_CACHE[0]


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
        # estleg:Act root — that's a Layer 1
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
                f"but missing estleg:Act root node"
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
        isinstance(n, dict) and (
            ("estleg:competentAuthority" in n)
            or ("estleg:competenceType" in n)
            or (MENTIONS_INSTITUTION in n)
        )
        for n in doc["@graph"]
    )

    # #718 dry run: remember what the corpus ships before it is cleared.
    shipped: dict[str, tuple[list[str], str | None]] = {}
    if state.measurement is not None:
        for n in doc["@graph"]:
            if isinstance(n, dict) and n.get("@id"):
                refs = n.get("estleg:competentAuthority") or []
                if isinstance(refs, dict):
                    refs = [refs]
                ctype = n.get("estleg:competenceType")
                shipped[n["@id"]] = (
                    [r["@id"] for r in refs if isinstance(r, dict) and r.get("@id")],
                    ctype if isinstance(ctype, str) else None,
                )

    # Clear unconditionally — the per-provision loop below
    # re-emits when detection succeeds. #700: except a (node, predicate)
    # owned by a human override, which is never cleared. #718:
    # mentionsInstitution is purely heuristic and always re-derived.
    overrides = state.overrides
    for n in doc["@graph"]:
        if isinstance(n, dict):
            clear_unowned(n, COMPETENCE_PREDICATES, overrides)
            n.pop(MENTIONS_INSTITUTION, None)

    # Store the granting act as an IRI when available; CompetenceShape
    # constrains estleg:grantedBy to IRI values.
    law_name = act_node.get("@id") or filepath.stem.replace("_peep", "")
    modified = False

    for node in doc["@graph"]:
        if not isinstance(node, dict):
            continue
        owned = overrides.for_node(node.get("@id"), COMPETENCE_PREDICATES)
        ctype_rec = owned.get("estleg:competenceType")
        forced_ctype = (
            ctype_rec.value
            if ctype_rec is not None and ctype_rec.action == "set"
            # A removal also vetoes the heuristic type in institution
            # back-links; retain only a general authority relationship.
            else "general" if ctype_rec is not None else None
        )
        # #700: a reviewed competentAuthority replaces detection outright —
        # the heuristic neither writes the node nor records back-links for it.
        if "estleg:competentAuthority" not in owned:
            if _process_provision_node(
                node=node,
                state=state,
                source_municipality=source_municipality,
                source_issuer=source_issuer,
                issuer_registry=issuer_registry,
                canonical_suffixes=canonical_suffixes,
                law_name=law_name,
                forced_competence_type=forced_ctype,
            ):
                modified = True
            if state.measurement is not None and node.get("@id") in shipped:
                after_refs = node.get("estleg:competentAuthority") or []
                after_competent = state.last_competent
                state.measurement.record(
                    provision_iri=node["@id"],
                    before=shipped[node["@id"]],
                    after_competent={
                        r["@id"]: after_competent.get(r["@id"], "general")
                        for r in after_refs
                    },
                    after_mentioned=[
                        r["@id"] for r in node.get(MENTIONS_INSTITUTION) or []
                    ],
                    text=classifier_text(node),
                )
        # Human overrides LAST (+ prov:wasAttributedTo, confidence 1.0).
        if finalize_node(node, overrides, COMPETENCE_PREDICATES):
            modified = True
        if owned:
            state.overrides_applied += len(owned)
            authority_rec = owned.get("estleg:competentAuthority")
            if authority_rec is not None and authority_rec.action == "set":
                ctype = node.get("estleg:competenceType")
                for ref in authority_rec.value:
                    state.override_links.append(
                        (
                            ref["@id"],
                            node.get("@id", ""),
                            ctype if isinstance(ctype, str) else "general",
                            law_name,
                        )
                    )

    if any(
        isinstance(n, dict) and "prov:wasAttributedTo" in n for n in doc["@graph"]
    ):
        if ensure_prov_context(doc):
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

    # #718: re-derive the act-root union the clear above removed (#508 shape).
    if rollup_act_authorities(doc, overrides)[0]:
        modified = True

    # Save when EITHER fresh output OR pre-existing output existed.
    if (modified or had_existing_peep) and not state.dry_run:
        save_json(filepath, doc)


def record_override_links(
    state: _PipelineState, canonical_suffixes: set[str]
) -> None:
    """Add institution back-links for overridden competentAuthority (#700).

    Only ``estleg:Institution_*`` refs get institution files (KOV issuer refs
    never do, matching the heuristic path). A ref is linked when its suffix is
    canonical (or the registry is in bootstrap mode) and its label/type is
    known from this run's detections or the curated named registry; anything
    else is counted in ``override_links_skipped``.
    """
    named_registry = named_institution_by_suffix()
    for inst_iri, provision_iri, ctype, law_name in sorted(state.override_links):
        prefix = "estleg:Institution_"
        if not inst_iri.startswith(prefix):
            continue
        suffix = inst_iri[len(prefix):]
        if canonical_suffixes and suffix not in canonical_suffixes:
            state.override_links_skipped += 1
            continue
        known = state.inst_data.get(inst_iri)
        named = named_registry.get(suffix)
        if known is not None:
            name, itype = known["name"], known["type"]
        elif named is not None:
            name, itype = named
        else:
            state.override_links_skipped += 1
            continue
        _record_provision_for_institution(
            state=state,
            inst_iri=inst_iri,
            canon_name=name,
            iri_suffix=suffix,
            itype=itype,
            provision_iri=provision_iri,
            competence_type=ctype,
            law_name=law_name,
        )


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
    identity = load_institution_identity()
    wd_map = load_wikidata_institutions(include_see_also=True)

    for inst_iri, info in sorted(state.inst_data.items()):
        provisions = state.inst_provisions[inst_iri]
        suffix = info["iri_suffix"]
        if suffix in _ALIAS_RECORDS or suffix in SAMEAS_ALIASES:
            # Predecessor / abbreviation nodes are materialised by
            # write_alias_and_predecessor_files (never competent: an alias
            # mention binds its successor).
            continue
        named = named_institution_by_suffix().get(suffix)
        display_name = preferred_institution_label(
            suffix, info["name"], wd_map, _existing_institution_label(suffix)
        )
        if suffix == KESKKONNAAMET_SLUG:
            display_name = "Keskkonnaamet"
        inst_type = preferred_institution_type(
            suffix, named[1] if named else info["type"]
        )
        inst_node: dict = {
            "@id": inst_iri,
            "@type": ["owl:NamedIndividual", "estleg:Institution"],
            # #577: de-inflect generic-pattern labels to nominative (a no-op on
            # already-canonical named institutions).
            "rdfs:label": display_name,
            "estleg:institutionType": inst_type,
        }

        merge_same_as(inst_node, [
            f"estleg:Institution_{alias}" for alias in canonical_aliases.get(suffix, [])
        ])
        apply_institution_identity(inst_node, suffix, identity, wd_map)

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
            cap = _APPLIES_TO_PROVISION_CAP
            applies_to = [{"@id": p} for p in (prov_iris if cap is None else prov_iris[:cap])]
            total_count = len(prov_iris)
            if cap is not None and total_count > cap:
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
                "rdfs:label": _competence_label(display_name, ctype),
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

    written_slugs.update(write_alias_and_predecessor_files(INSTIT_DIR))
    return written_slugs


def _institution_root_node(doc: dict) -> dict | None:
    for node in doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "estleg:Institution" in types and "estleg:Competence" not in types:
            return node
    return None


def preferred_institution_label(
    slug: str,
    surface_name: str,
    wd_map: dict[str, dict] | None = None,
    existing_label: str | None = None,
) -> str:
    """Stable display label for an Institution node (#718).

    The surface form an extraction run meets first depends on corpus order
    (a sentence-initial ``Keskkonnaminister`` vs ``keskkonnaminister``), and
    mention-only institutions made that order visible. Precedence: curated
    ``label`` in data/wikidata_institutions.json, the named-institution
    catalogue, the label already on disk, then the de-inflected surface form.
    """
    wd_map = load_wikidata_institutions(include_see_also=True) if wd_map is None else wd_map
    curated = (wd_map.get(slug) or {}).get("label")
    if isinstance(curated, str) and curated:
        return curated
    named = named_institution_by_suffix().get(slug)
    if named is not None:
        return named[0]
    if isinstance(existing_label, str) and existing_label:
        return existing_label
    return canonicalize_institution_label(surface_name)


def _existing_institution_label(slug: str) -> str | None:
    path = INSTIT_DIR / f"institution_{slug}.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    root = _institution_root_node(doc)
    label = root.get("rdfs:label") if root else None
    return label if isinstance(label, str) else None


def _date_literal(value: str) -> dict:
    return {"@value": value, "@type": "xsd:date"}


def apply_institution_identity(
    node: dict,
    slug: str,
    identity: dict[str, dict] | None = None,
    wd_map: dict[str, dict] | None = None,
) -> None:
    """Stamp #718 identity onto an Institution node (idempotent).

    * ``owl:sameAs`` keeps its non-Wikidata targets and gets exactly the
      slug's identity QID (none for concept-level / predecessor-name slugs);
      ``rdfs:seeAlso`` carries the non-identity ``seeAlsoQid``.
    * ``estleg:registrikood``, ``estleg:xteeMemberCode`` (plain strings),
      ``estleg:validFrom`` / ``estleg:validTo`` (xsd:date) and
      ``estleg:predecessorInstitution`` / ``estleg:successorInstitution``
      (IRIs) mirror data/institution_identity.json; a field absent there is
      removed from the node.
    """
    identity = load_institution_identity() if identity is None else identity
    wd_map = load_wikidata_institutions(include_see_also=True) if wd_map is None else wd_map
    keep = [iri for iri in _same_as_ids(node) if not iri.startswith(WIKIDATA_ENTITY_PREFIX)]
    node.pop("owl:sameAs", None)
    wd = wikidata_iri_for_slug(slug, wd_map)
    merge_same_as(node, keep + ([wd] if wd else []))
    see_also = wikidata_see_also_iri_for_slug(slug, wd_map)
    existing = node.get("rdfs:seeAlso", [])
    existing = existing if isinstance(existing, list) else [existing]
    kept = [ref for ref in existing if not (
        isinstance(ref, dict) and str(ref.get("@id", "")).startswith(WIKIDATA_ENTITY_PREFIX)
    )]
    if see_also:
        kept.append({"@id": see_also})
    if kept:
        node["rdfs:seeAlso"] = kept[0] if len(kept) == 1 else kept
    else:
        node.pop("rdfs:seeAlso", None)
    record = identity.get(slug, {})
    for key in ("registrikood", "xteeMemberCode"):
        value = record.get(key)
        if isinstance(value, str) and value:
            node[f"estleg:{key}"] = value
        else:
            node.pop(f"estleg:{key}", None)
    for key in ("validFrom", "validTo"):
        value = record.get(key)
        if isinstance(value, str) and value:
            node[f"estleg:{key}"] = _date_literal(value)
        else:
            node.pop(f"estleg:{key}", None)
    for key in ("predecessorInstitution", "successorInstitution"):
        slugs = [v for v in record.get(key) or [] if isinstance(v, str) and v]
        if slugs:
            node[f"estleg:{key}"] = [{"@id": f"estleg:Institution_{v}"} for v in slugs]
        else:
            node.pop(f"estleg:{key}", None)


def write_alias_and_predecessor_files(instit_dir: Path) -> set[str]:
    """Materialize abbreviation alias nodes and one predecessor node per
    historical alias key (#457 Keskkonnainspektsioon, generalised by #718).

    A predecessor node keeps ``dcterms:isReplacedBy`` (the #457 contract)
    and carries the #718 identity fields (``estleg:successorInstitution``,
    ``estleg:validTo``, Wikidata, registrikood where known). It receives
    ``estleg:mentionsInstitution`` edges from provisions that name the
    predecessor; competence still binds the canonical successor.
    """
    identity = load_institution_identity()
    wd_map = load_wikidata_institutions(include_see_also=True)
    written: set[str] = set()
    for alias, canonical in SAMEAS_ALIASES.items():
        node = {
            "@id": f"estleg:Institution_{alias}",
            "@type": ["owl:NamedIndividual", "estleg:Institution"],
            "rdfs:label": SAMEAS_ALIAS_LABELS.get(alias, alias.upper()),
            "estleg:institutionType": "agency",
            "owl:sameAs": {"@id": f"estleg:Institution_{canonical}"},
        }
        wd = wikidata_iri_for_slug(canonical, wd_map)
        if wd:
            merge_same_as(node, [wd])
        save_json(
            instit_dir / f"institution_{alias}.json",
            {"@context": CONTEXT, "@graph": [node]},
        )
        written.add(alias)

    for slug, record in sorted(_ALIAS_RECORDS.items()):
        successor = record["canonical"]
        if slug == KESKKONNAINSPEKTSIOON_SLUG:
            comment = (
                "Historical Environmental Inspectorate; merged into Keskkonnaamet "
                "on 2021-01-01 (#457)."
            )
        else:
            comment = (
                f"Historical predecessor of estleg:Institution_{successor} "
                f"(#718). {record['evidence']}".strip()
            )
        pred = {
            "@id": f"estleg:Institution_{slug}",
            "@type": ["owl:NamedIndividual", "estleg:Institution"],
            "rdfs:label": record["label"],
            "estleg:institutionType": "agency",
            "dcterms:isReplacedBy": {"@id": f"estleg:Institution_{successor}"},
            "rdfs:comment": comment,
        }
        apply_institution_identity(pred, slug, identity, wd_map)
        save_json(
            instit_dir / f"institution_{slug}.json",
            {"@context": CONTEXT, "@graph": [pred]},
        )
        written.add(slug)
    return written


def cleanup_institution_overlay(instit_dir: Path = INSTIT_DIR) -> dict[str, int]:
    """Remint existing institution files without rescanning peeps (#457)."""
    wd_map = load_wikidata_institutions(include_see_also=True)
    identity = load_institution_identity()
    named = named_institution_by_suffix()
    reminted = 0
    for path in sorted(instit_dir.glob("institution_*.json")):
        slug = path.stem.removeprefix("institution_")
        if slug in SAMEAS_ALIASES or slug in _ALIAS_RECORDS:
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        graph = doc.get("@graph")
        if not isinstance(graph, list):
            continue
        changed = False
        root = _institution_root_node(doc)
        root_label = root.get("rdfs:label") if root else None
        if isinstance(root_label, str):
            root_label = (
                "Keskkonnaamet" if slug == KESKKONNAAMET_SLUG
                else preferred_institution_label(slug, root_label, wd_map, root_label)
            )
        for node in graph:
            if not isinstance(node, dict):
                continue
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            label = node.get("rdfs:label")
            if not isinstance(label, str):
                continue
            if "estleg:Competence" in types:
                if " – " in label:
                    head, tail = label.rsplit(" – ", 1)
                    # #718: the head follows the institution's stable label.
                    new_label = _competence_label(
                        root_label if isinstance(root_label, str) else head, tail
                    )
                    if new_label != label:
                        node["rdfs:label"] = new_label
                        changed = True
                continue
            if "estleg:Institution" not in types:
                continue
            preferred = named.get(slug)
            new_label = root_label if node is root and isinstance(root_label, str) else label
            if new_label != label:
                node["rdfs:label"] = new_label
                changed = True
            new_type = preferred_institution_type(
                slug, preferred[1] if preferred else str(node.get("estleg:institutionType") or "")
            )
            if new_type and node.get("estleg:institutionType") != new_type:
                node["estleg:institutionType"] = new_type
                changed = True
            extras = [
                f"estleg:Institution_{alias}"
                for alias, canonical in SAMEAS_ALIASES.items()
                if canonical == slug
            ]
            before = json.dumps(node, sort_keys=True, ensure_ascii=False)
            merge_same_as(node, extras)
            apply_institution_identity(node, slug, identity, wd_map)
            if json.dumps(node, sort_keys=True, ensure_ascii=False) != before:
                changed = True
        if changed:
            save_json(path, doc)
            reminted += 1
    extras = write_alias_and_predecessor_files(instit_dir)
    return {"reminted": reminted, "extra_files": len(extras)}


def write_report(state: _PipelineState, total_law_files: int) -> Path:
    """Emit the human-readable competence report."""
    print("\n[3/4] Generating report...")

    # Competence-type breakdown
    competence_counts: dict[str, int] = defaultdict(int)
    for provisions in state.inst_provisions.values():
        for _, ctype, _ in provisions:
            competence_counts[ctype] += 1

    # Laws per institution (#718: mention-only institutions have no
    # competence provisions and are listed separately below).
    competent_provisions = {k: v for k, v in state.inst_provisions.items() if v}
    inst_law_counts: dict[str, int] = {}
    for inst_iri, provisions in competent_provisions.items():
        laws = {law for _, _, law in provisions}
        inst_law_counts[state.inst_data[inst_iri]["name"]] = len(laws)

    report = {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "summary": {
            "total_law_files": total_law_files,
            "total_provisions_with_text": state.total_provisions,
            "provisions_with_institutions": state.provisions_with_institutions,
            "unique_institutions": len(state.inst_data),
            "institutions_with_competence": len(competent_provisions),
        },
        "by_competence_type": dict(sorted(competence_counts.items(), key=lambda x: -x[1])),
        # #718: clause-level binding — mentions that are not competence.
        "mentions_institution_edges": state.mention_edges,
        "mention_only_institutions": sorted(
            state.inst_data[iri]["name"]
            for iri in state.inst_mentions
            if iri in state.inst_data and not state.inst_provisions.get(iri)
        ),
        "binder_reasons": dict(state.binding_reasons.most_common()),
        "institutions_by_provision_count": {
            state.inst_data[k]["name"]: len(v)
            for k, v in sorted(competent_provisions.items(), key=lambda x: -len(x[1]))
        },
        "institutions_by_law_count": dict(
            sorted(inst_law_counts.items(), key=lambda x: -x[1])
        ),
        "institution_types": {
            info["type"]: info["name"]
            for info in sorted(state.inst_data.values(), key=lambda x: x["type"])
        },
    }

    report_path = KRR_DIR / "reports" / "institutional_competence_report.json"
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


def run_dry_run(overrides: OverrideStore, report_path: Path | None) -> int:
    """#718: measure the clause-level binder against the shipped corpus.

    Reads every peep, runs the same per-file processing as a real pass with
    ``state.dry_run`` set (no peep, institution, report or coverage file is
    written) and prints / optionally saves a :class:`CompetenceMeasurement`.
    """
    start_time = time.perf_counter()
    law_files = iter_peep_files()
    issuer_registry = build_issuer_registry(KRR_DIR / "issuers_kov_peep.json")
    canonical_suffixes = _load_canonical_institutions(INSTIT_DIR)
    state = _PipelineState()
    state.overrides = overrides
    state.dry_run = True
    state.measurement = CompetenceMeasurement()
    for idx, filepath in enumerate(law_files, 1):
        process_law_file(
            filepath=filepath,
            state=state,
            issuer_registry=issuer_registry,
            canonical_suffixes=canonical_suffixes,
        )
        if idx % 1000 == 0 or idx == len(law_files):
            print(f"  [dry-run {idx}/{len(law_files)}]")
    result = state.measurement.as_dict(state)
    result["files"] = len(law_files)
    result["institutions_with_competence"] = sum(1 for v in state.inst_provisions.values() if v)
    result["mention_only_institutions"] = sorted(
        iri for iri in state.inst_mentions if not state.inst_provisions.get(iri)
    )
    result["wall_time_seconds"] = round(time.perf_counter() - start_time, 1)
    keys = ("bindings_before_total", "bindings_before_by_type", "bindings_after_total",
            "bindings_after_by_type", "mentions_institution_edges_after",
            "before_type_to_after_state", "wall_time_seconds")
    print(json.dumps({k: result[k] for k in keys}, ensure_ascii=False, indent=2))
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=list) + "\n",
            encoding="utf-8",
        )
        print(f"  dry-run report: {report_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cleanup-only",
        action="store_true",
        help="Remint existing institution overlay files without rescanning peeps (#457).",
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        default=None,
        help="human override store (#700; default data/heuristic_overrides.jsonl)",
    )
    parser.add_argument(
        "--rollup-only",
        action="store_true",
        help="#718: stamp act roots with the union of the provision-level "
        "competentAuthority already on disk (no extraction, peeps only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="#718: run the clause-level binder over the corpus in memory and "
        "report before/after binding counts; writes nothing under krr_outputs/",
    )
    parser.add_argument(
        "--dry-run-report",
        type=Path,
        default=None,
        help="with --dry-run: write the before/after measurement JSON here",
    )
    parser.add_argument(
        "--check-overrides",
        action="store_true",
        help="dry run: report how many competence overrides would apply and "
        "which are stale (node not found); writes nothing",
    )
    args = parser.parse_args([] if argv is None else argv)
    if args.cleanup_only:
        print(cleanup_institution_overlay())
        return 0
    if args.rollup_only:
        print(json.dumps(run_rollup_only(), indent=2))
        return 0
    try:
        overrides = load_overrides(args.overrides)
    except OverrideError as exc:
        print(f"ERROR: {exc}")
        return 1
    if args.check_overrides:
        report = check_overrides(overrides, iter_peep_files(), COMPETENCE_PREDICATES)
        print_check_report(report, "extract_institutional_competence")
        return 0

    if args.dry_run:
        return run_dry_run(overrides, args.dry_run_report)

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
    state.overrides = overrides
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

    record_override_links(state, canonical_suffixes)
    if state.overrides_applied or state.override_links_skipped:
        print(
            f"  Human overrides applied: {state.overrides_applied} "
            f"(institution back-links skipped: {state.override_links_skipped})"
        )
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
    import sys

    raise SystemExit(main(sys.argv[1:]))
