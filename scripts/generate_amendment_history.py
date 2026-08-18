#!/usr/bin/env python3
"""
Build amendment chain relationships between Estonian laws and their amending acts.

This script:
1. Loads all existing law JSON-LD files from krr_outputs/*_peep.json
2. Loads draft legislation from krr_outputs/eelnoud/ for amendment relationships
3. Parses cached law XML files for amendment references (muutmismarge blocks)
4. Adds estleg:amendedBy / estleg:amends / estleg:amendmentDate to JSON-LD for
   *effected* (RT-derived) amendments
5. Emits draft-derived nodes as estleg:ProposedAmendment (NOT AmendmentEvent) —
   never-enacted bills are pending proposals, not effected legal changes (#423)
6. Creates krr_outputs/amendments/ directory with amendment chain data
7. Generates amendment_history_report.json

Effected vs. proposed (#423)
----------------------------
Two distinct node kinds land in each chain document:

* ``estleg:AmendmentEvent`` — derived from Riigi Teataja ``<muutmismarge>``
  blocks (and inline RT references). These are *effected* changes: the act
  text was actually modified. They carry ``estleg:amends`` (inverse of the
  act-root ``estleg:amendedBy``), ``estleg:amendmentDate`` / ``entryIntoForce``,
  and the chronologically-latest one per act is flagged
  ``estleg:isCurrentAmendment`` (computed over effected events ONLY).

* ``estleg:ProposedAmendment`` — derived from draft amendment bills in
  ``krr_outputs/eelnoud/`` whose ``legislativePhase`` is Review/Submission/
  PublicConsultation (none enacted). They MUST NOT be typed AmendmentEvent and
  MUST NOT assert effected semantics. They carry ``estleg:proposesToAmend``
  (proposal-scoped, NOT ``estleg:amends``), ``estleg:publicationDate`` (the
  draft's EIS publication date — NOT ``estleg:amendmentDate``, which is reserved
  for adoption), and ``estleg:amendingDraft`` → the Draft_* node. They NEVER
  carry ``estleg:isCurrentAmendment``. On the act root they are linked via
  ``estleg:hasProposedAmendment`` (inverse of ``estleg:proposesToAmend``) — NOT
  ``estleg:amendedBy`` (effected-only) and NOT ``estleg:affectedBy`` (act-root →
  Draft; owned by extract_draft_impact.py — writing it here would clobber that
  pipeline's clear-then-rewrite).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from functools import partial
from pathlib import Path

from estleg_common import (
    CONTEXT,
    PINNED_RUN_TIMESTAMP,
    build_globalid_xml_lookup,
    iter_peep_files,
    jsonld_text,
    pair_peep_with_xml,
    save_json,
    sanitize_id as _shared_sanitize_id,
)
from kov_pipeline_coverage import (
    CoverageReport,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
DATA_DIR = REPO_ROOT / "data" / "riigiteataja"
AMENDMENTS_DIR = KRR_DIR / "amendments"
EELNOUD_DIR = KRR_DIR / "eelnoud"
# Compact law-abbreviation registry (issue #83 / #192). Maps a law slug to
# ``{"abbrev": ..., ...}``; used so the Amendment_/AmendmentChain_/
# AmendmentLink_ IRIs minted below carry the compact abbreviation rather than
# the long ``<base_slug>`` segment.
LAW_ABBREVIATIONS_PATH = REPO_ROOT / "data" / "law_abbreviations.json"


def _draft_publication_sort_key(draft: dict) -> str:
    """Sort key for draft-derived proposed amendments (#404).

    Only a string publication_date is comparable; lists/objects/None
    sort last via a high sentinel so a future JSON-LD shape cannot
    TypeError the chain builder.
    """
    value = draft.get("publication_date")
    return value if isinstance(value, str) else "9999-99-99"

# Pinned, deterministic ``run_timestamp`` for the git-tracked coverage report
# (issue #295). The previous ``datetime.now(timezone.utc)`` value re-diffed the
# tracked coverage file on every run (timestamp-only churn). ``CoverageReport``
# requires a valid UTC ISO-8601 timestamp, so we pin a fixed sentinel rather
# than emitting a live wall clock. The epoch makes "this is not a real run
# clock" unmistakable; genuine run time lives in ``wall_time_seconds``.
NS = "https://w3id.org/estleg/"

# Structural tails stripped from an ``estleg:amends`` target to recover the
# compact stem the amendment IRIs should adopt for laws/regulations absent from
# the abbreviation registry (e.g. ``estleg:Reg_1052132_Map_2026`` → ``Reg_1052132``).
_AMENDS_STEM_STRIP_RE = re.compile(
    r"_(?:Map_\d+|Map|Osa\d+(?:_.*)?|Par_?\d+(?:_.*)?|Chapter\d+(?:_.*)?"
    r"|Division\d+(?:_.*)?|TopicScheme\d+(?:_.*)?)$"
)
_COMPACT_PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")



def ln(tag: str) -> str:
    """Strip XML namespace prefix."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def ct(el: ET.Element, name: str) -> str | None:
    """Get child element text by local tag name."""
    for c in el:
        if ln(c.tag) == name and c.text:
            return c.text.strip()
    return None


sanitize_id = partial(_shared_sanitize_id, max_len=80, replace_dash=True)


# Multipart-law parts share a slug stem and differ only by a trailing
# ``_osaN`` (e.g. ``vololigusseadus_osa1`` … ``_osa9``). They are parts of ONE
# law and the chain builder already groups them by this base. Used by the
# title→slug ambiguity check (#595) so genuine multipart siblings are NOT
# mistaken for distinct co-named acts.
_OSA_SUFFIX_RE = re.compile(r"_osa\d+$")


def base_slug_of(slug: str) -> str:
    """Strip a trailing ``_osaN`` multipart suffix to the law's base slug."""
    return _OSA_SUFFIX_RE.sub("", slug)


def _osa_order_key(slug: str) -> tuple[int, str]:
    """Natural-numeric sort key for a multipart ``_osaN`` slug (issue #606).

    Ordering members of a base_slug group by this key makes ``members[0]``
    the lowest-numbered part (``osa1``) deterministically, instead of relying
    on lexical string order — where ``_osa10``/``_osa11`` sort BEFORE
    ``_osa2`` — or filesystem glob order. A single-part law (no ``_osaN``
    suffix) yields ``(0, slug)`` and is unaffected. ``slug`` is a stable
    secondary key for parts that somehow share an osa number.
    """
    m = _OSA_SUFFIX_RE.search(slug)
    if m:
        return (int(m.group(0).removeprefix("_osa")), slug)
    return (0, slug)


def slugify(text: str) -> str:
    """Convert Estonian text to a filename-safe slug."""
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


# Plausible-year window for an amendment date (issue #587). Riigi Teataja
# source XML occasionally carries typo years far outside any real legislative
# timeline (verified: ``<aktikuupaev>2918-10-17</aktikuupaev>``, a 3006-03-02,
# and 2026-12-19-style near-future dates). Such a corrupt year sorts LAST under
# ``_amend_sort_key`` and wrongly wins ``estleg:isCurrentAmendment``. The lower
# bound predates the modern Riigi Teataja (the earliest digitised acts are
# 1990s); the upper bound is the build year plus a small slack for genuinely
# pre-published future entry-into-force / signing dates. A date whose year is
# outside ``[_MIN_PLAUSIBLE_YEAR, current_year + _MAX_FUTURE_YEAR_SLACK]`` is
# treated as unparseable (``parse_date`` returns ``None``) so it is dropped from
# the chain rather than allowed to corrupt ordering / the current-amendment flag.
_MIN_PLAUSIBLE_YEAR = 1990
_MAX_FUTURE_YEAR_SLACK = 2


def _max_plausible_year() -> int:
    """Upper bound for a plausible amendment year (build year + slack).

    Computed from the wall clock so the gate tracks the real calendar rather
    than a frozen constant; the slack tolerates a date that was pre-published
    slightly ahead of the current year (e.g. a January build seeing a December
    entry-into-force of the same cycle).
    """
    from datetime import date
    return date.today().year + _MAX_FUTURE_YEAR_SLACK


def _year_is_plausible(year: int) -> bool:
    """True when ``year`` falls inside the plausible amendment-year window."""
    return _MIN_PLAUSIBLE_YEAR <= year <= _max_plausible_year()


def parse_date(value: str) -> str | None:
    """Parse a date from XML, stripping timezone offsets, with a sanity gate.

    Returns ``YYYY-MM-DD`` for a well-formed, plausibly-dated value, else
    ``None``. Beyond format parsing this REJECTS implausible years (issue
    #587): a corrupt/typo year (e.g. ``2918`` or ``3006``) or one more than a
    couple of years in the future is dropped to ``None`` so it can neither sort
    last in the chain nor win ``estleg:isCurrentAmendment``.
    """
    if not value:
        return None
    from datetime import datetime
    value = value.strip()
    value = re.sub(r"\+\d{2}:\d{2}", "", value)
    value = re.sub(r"-\d{2}:\d{2}$", "", value)
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if not _year_is_plausible(parsed.year):
            return None
        return parsed.strftime("%Y-%m-%d")
    return None


def make_xsd_date(iso_date: str) -> dict:
    """Create an xsd:date typed value for JSON-LD."""
    return {"@value": iso_date, "@type": "xsd:date"}


# A Riigi Teataja globalId is an all-digit token (e.g. ``178370`` or the longer
# ``13244294`` / ``106122014001`` forms). Used to recognise an ``akt_viide`` as
# a citable RT identifier so ``estleg:amendingAct`` can carry a stable
# riigiteataja.ee reference rather than an opaque string.
_RT_GLOBALID_RE = re.compile(r"^\d+$")


def _amending_act_value(amend: dict) -> str | None:
    """Best offline identifier of the act that made an amendment (issue #587).

    Resolution order (most → least citable):
      1. ``rt_reference`` — the amending act's Riigi Teataja publication marker
         (``"RT I, 2002, 57, 356"``), the canonical human-readable citation.
      2. ``akt_viide`` as a riigiteataja.ee act URL when it is a bare RT
         globalId (all digits) — a stable, dereferenceable identifier.
      3. ``akt_viide`` verbatim otherwise (non-numeric reference text).

    Returns ``None`` when neither field is present, so the caller emits
    ``estleg:amendingAct`` only when there is a real, source-derived value —
    nothing is fabricated. All inputs come from the offline ``<muutmismarge>``
    parse; no network access is involved.
    """
    rt_reference = amend.get("rt_reference")
    if rt_reference:
        return rt_reference
    akt_viide = amend.get("akt_viide")
    if not akt_viide:
        return None
    akt_viide = akt_viide.strip()
    if not akt_viide:
        return None
    if _RT_GLOBALID_RE.match(akt_viide):
        return f"https://www.riigiteataja.ee/akt/{akt_viide}"
    return akt_viide


def load_law_files(*, failures: list[str] | None = None) -> dict[str, dict]:
    """
    Load all law JSON-LD files.
    Returns dict keyed by slug (stem without _peep).
    Records JSON / IO errors to the optional `failures` sink instead of
    swallowing them silently.
    """
    laws = {}
    for f in iter_peep_files():
        slug = f.stem.replace("_peep", "")
        try:
            with open(f, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            laws[slug] = {
                "path": f,
                "doc": doc,
                "title": extract_title(doc),
            }
        except (json.JSONDecodeError, OSError) as exc:
            if failures is not None:
                failures.append(
                    f"load_law_files | {f.name} | "
                    f"{type(exc).__name__}: {str(exc)[:80]}"
                )
    return laws


def _amendment_chain_path(base_slug: str) -> Path:
    return AMENDMENTS_DIR / f"amendments_{base_slug}.json"


def _remove_amendment_chain_file(base_slug: str) -> int:
    path = _amendment_chain_path(base_slug)
    if not path.exists():
        return 0
    path.unlink()
    return 1


def _remove_obsolete_amendment_chain_files(
    current_base_slugs: set[str],
    *,
    safe_to_delete: bool = True,
) -> int:
    if not safe_to_delete:
        return 0
    removed = 0
    prefix = "amendments_"
    for path in sorted(AMENDMENTS_DIR.glob(f"{prefix}*.json")):
        base_slug = path.stem[len(prefix):]
        if base_slug not in current_base_slugs:
            path.unlink()
            removed += 1
    return removed


def extract_title(doc: dict) -> str:
    """Extract the law title from the ontology node's dc:source."""
    for node in doc.get("@graph", []):
        types = node.get("@type", [])
        if "owl:Ontology" in types:
            return node.get("dc:source", node.get("rdfs:label", ""))
    return ""


def build_title_to_slug_map(laws: dict[str, dict]) -> dict[str, list[str]]:
    """Build a mapping from a lookup key to the LIST of slugs sharing it.

    Each title contributes under two keys — its lowercased form and its
    :func:`slugify` form — and the value is the *list* of every slug that
    resolves to that key (issue #595).

    The previous ``mapping[key] = slug`` was last-writer-wins: 512+ true
    cross-document title collisions silently collapsed to a single survivor
    (``"kohanime määramine"`` alone is shared by 181 distinct KOV acts), so
    ``match_law_name_to_slug`` returned an arbitrary act and every co-named act
    became unreachable — a draft's ``estleg:proposesToAmend`` bound to the wrong
    act. Keeping the full list lets the matcher DETECT ambiguity (>1 slug under
    a key) and refuse to guess rather than pick one. Slugs are appended in the
    iteration order of ``laws`` and de-duplicated per key so the structure is
    deterministic and a single slug never appears twice under one key.
    """
    mapping: dict[str, list[str]] = {}

    def _add(key: str, slug: str) -> None:
        bucket = mapping.setdefault(key, [])
        if slug not in bucket:
            bucket.append(slug)

    for slug, info in laws.items():
        title = info["title"]
        if title:
            _add(title.lower(), slug)
            # Also map partial name (without "seadus" suffix form variations)
            # e.g., "Alkoholiseadus" -> alkoholiseadus
            _add(slugify(title), slug)
    return mapping


def extract_amendments_from_xml(
    xml_path: Path,
    *,
    failures: list[str] | None = None,
) -> list[dict]:
    """
    Extract amendment references (muutmismarge blocks) from a law XML file.
    Each muutmismarge contains info about an amending act.
    """
    amendments: list[dict] = []
    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except (ET.ParseError, OSError) as exc:
        if failures is not None:
            failures.append(
                f"extract_amendments_from_xml | {xml_path.name} | "
                f"{type(exc).__name__}: {str(exc)[:80]}"
            )
        return amendments

    for el in root.iter():
        if ln(el.tag) != "muutmismarge":
            continue

        amendment: dict = {
            "date": None,
            "rt_reference": None,
            "entry_into_force": None,
            "akt_viide": None,
        }

        # aktikuupaev — date of the amending act
        val = ct(el, "aktikuupaev")
        if val:
            amendment["date"] = parse_date(val)

        # joustumine — when the amendment entered into force
        val = ct(el, "joustumine")
        if val:
            amendment["entry_into_force"] = parse_date(val)

        # Build RT reference from avaldamismarge child
        avaldamismarge = None
        for child in el:
            if ln(child.tag) == "avaldamismarge":
                avaldamismarge = child
                break

        if avaldamismarge is not None:
            rt_osa = ct(avaldamismarge, "RTosa") or ""
            rt_aasta = ct(avaldamismarge, "RTaasta") or ""
            rt_nr = ct(avaldamismarge, "RTnr") or ""
            rt_artikkel = ct(avaldamismarge, "RTartikkel") or ""
            akt_viide = ct(avaldamismarge, "aktViide")

            # Only mint an rt_reference when the components are enough to be
            # unique (issue #263). A reference needs the publication YEAR plus
            # at least one of the number/article; otherwise we produce a
            # degenerate stub like ``"RT I, , , 13"`` that is neither a valid
            # RT citation nor a usable dedup key. In that case leave it None
            # and let the (date, entry_into_force, akt_viide) tuple key in.
            if rt_aasta and (rt_nr or rt_artikkel):
                amendment["rt_reference"] = (
                    f"{rt_osa}, {rt_aasta}, {rt_nr}, {rt_artikkel}".strip(", ")
                )
            if akt_viide:
                amendment["akt_viide"] = akt_viide

        if amendment["date"] or amendment["rt_reference"]:
            amendments.append(amendment)

    return _dedupe_amendments(amendments, failures=failures)


# A merged record whose entry-into-force precedes its adoption date by more
# than this many days is physically impossible (a law cannot take effect ~a
# year before it was adopted). It is the tell-tale of a cross-marker blend
# where ``entry_into_force`` was backfilled from a *different* amending act
# than the one that supplied ``date`` (issue #353). 90 days tolerates the
# rare legitimate retro-active clause while catching the 285–356-day inversions
# the dedup merge produced.
_MAX_EIF_BEFORE_DATE_DAYS = 90


def _days_between(earlier: str | None, later: str | None) -> int | None:
    """Return ``(later - earlier)`` in whole days, or ``None`` if unparseable.

    Both arguments are ``YYYY-MM-DD`` strings (the shape ``parse_date`` emits).
    A negative result means ``later`` is chronologically BEFORE ``earlier``.
    """
    if not earlier or not later:
        return None
    from datetime import datetime
    try:
        d_earlier = datetime.strptime(earlier, "%Y-%m-%d")
        d_later = datetime.strptime(later, "%Y-%m-%d")
    except ValueError:
        return None
    return (d_later - d_earlier).days


def _amend_dedup_key(amend: dict) -> tuple:
    """Return the identity key used to collapse duplicate amendment records.

    RT repeats the SAME amending act across many ``<muutmismarge>`` markers
    (issue #263). The canonical identity is the RT publication reference; when
    that is absent (degenerate/empty avaldamismarge) we fall back to the
    ``(date, entry_into_force, akt_viide)`` tuple so two markers describing the
    same act still collapse instead of minting spurious ``_2``/``_3`` nodes.
    """
    rt = amend.get("rt_reference")
    if rt:
        return ("rt", rt)
    return (
        "tuple",
        amend.get("date") or "",
        amend.get("entry_into_force") or "",
        amend.get("akt_viide") or "",
    )


def _amend_completeness(amend: dict) -> int:
    """Score how complete an amendment record is.

    Used when merging duplicates so the most-complete record wins (issue
    #263 — the duplicate marker often carries less data, e.g. ``date: None``).
    """
    return sum(
        1
        for key in ("date", "rt_reference", "entry_into_force", "akt_viide")
        if amend.get(key)
    )


def _sanitize_merged_amendment(
    amend: dict,
    *,
    failures: list[str] | None = None,
) -> dict:
    """Drop a physically-impossible ``entry_into_force`` from a merged record.

    A merge can backfill ``entry_into_force`` from a different marker than the
    one that supplied ``date`` (issue #353). When the result has
    ``entry_into_force`` more than ``_MAX_EIF_BEFORE_DATE_DAYS`` days BEFORE
    ``date`` the pair is impossible (a law in force ~a year before adoption),
    so we null the conflicting ``entry_into_force`` and log a warning rather
    than emit the bad pair. ``date`` is kept because the adoption date is the
    more trustworthy anchor (an act always exists before it takes effect).
    """
    delta = _days_between(amend.get("date"), amend.get("entry_into_force"))
    if delta is not None and delta < -_MAX_EIF_BEFORE_DATE_DAYS:
        bad_eif = amend.get("entry_into_force")
        amend["entry_into_force"] = None
        if failures is not None:
            failures.append(
                "dedupe_amendments | impossible_eif_before_date | "
                f"date={amend.get('date')} eif={bad_eif} "
                f"({-delta}d before adoption) | rt={amend.get('rt_reference')} "
                f"akt_viide={amend.get('akt_viide')} | nulled entry_into_force"
            )
    return amend


def _would_invert(date: str | None, eif: str | None) -> bool:
    """True when ``eif`` precedes ``date`` by more than the allowed window.

    Guards the cross-marker backfill (issue #353): we must not adopt a
    ``date``/``entry_into_force`` value from another marker if the resulting
    pair would be physically impossible (a law in force long before adoption).
    """
    delta = _days_between(date, eif)
    return delta is not None and delta < -_MAX_EIF_BEFORE_DATE_DAYS


def _dedupe_amendments(
    amendments: list[dict],
    *,
    failures: list[str] | None = None,
) -> list[dict]:
    """Collapse duplicate amendment records emitted from one XML.

    Duplicates (same RT reference, or — when no reference — same
    ``(date, entry_into_force, akt_viide)`` tuple) are merged into a single
    record. The most-complete record wins outright; remaining records then
    backfill any field the winner is still missing. Insertion order of the
    first occurrence is preserved so chain IDs stay stable across reruns.

    Two date-coherence guards prevent the impossible-pair bug from issue #353:

    * The ``date``/``entry_into_force`` backfill is REFUSED when adopting the
      loser's value would make ``entry_into_force`` precede ``date`` by more
      than ``_MAX_EIF_BEFORE_DATE_DAYS`` days — i.e. when two genuinely-distinct
      markers (one carrying only an adoption date, the other only an
      effective date) would otherwise blend into one impossible event.
    * As a backstop, EVERY surviving record (merged or not) is run through
      ``_sanitize_merged_amendment`` so a surviving inversion is nulled and
      logged rather than emitted — a single un-merged marker can carry an
      impossible ``entry_into_force``/``date`` pair on its own (issue #587).

    The dedup key itself is unchanged (issue #263): identical repeated markers
    still collapse — only the cross-marker *date blend* is constrained.
    """
    merged: dict[tuple, dict] = {}
    order: list[tuple] = []
    for amend in amendments:
        key = _amend_dedup_key(amend)
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(amend)
            order.append(key)
            continue
        # Pick the more-complete record as the base, then backfill.
        if _amend_completeness(amend) > _amend_completeness(existing):
            winner, loser = dict(amend), existing
        else:
            winner, loser = existing, amend
        for field_name in ("date", "rt_reference", "entry_into_force", "akt_viide"):
            if winner.get(field_name) or not loser.get(field_name):
                continue
            candidate = loser[field_name]
            # Date-coherence guard: don't pull in a date/eif from the loser
            # if it would make the merged pair an impossible inversion.
            if field_name == "entry_into_force" and _would_invert(
                winner.get("date"), candidate
            ):
                continue
            if field_name == "date" and _would_invert(
                candidate, winner.get("entry_into_force")
            ):
                continue
            winner[field_name] = candidate
        merged[key] = winner
    # Sanitise EVERY surviving record, not only those that absorbed a duplicate
    # (issue #587). A single un-merged ``<muutmismarge>`` can itself carry an
    # ``entry_into_force`` that precedes its ``date`` by more than the allowed
    # window (verified: 38 such records on the corpus, the worst 356 days early
    # — physically impossible). The #353 fix only ran the guard on merged
    # records; running it on all of them nulls a stray impossible
    # ``entry_into_force`` regardless of how the record was produced.
    return [
        _sanitize_merged_amendment(merged[k], failures=failures) for k in order
    ]


def extract_rt_references_from_text(
    xml_path: Path,
    *,
    failures: list[str] | None = None,
) -> list[str]:
    """
    Scan law text for inline RT publication references like "RT I, 28.06.2023, 10".
    These often indicate amendment acts referenced in the body text.
    """
    references: list[str] = []
    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except (ET.ParseError, OSError) as exc:
        if failures is not None:
            failures.append(
                f"extract_rt_references_from_text | {xml_path.name} | "
                f"{type(exc).__name__}: {str(exc)[:80]}"
            )
        return references

    full_text = " ".join(root.itertext())
    # Pattern: RT I, YYYY, NR, ART or RT I, DD.MM.YYYY, NR
    # Roman numeral group is anchored with a word boundary so we accept
    # I/II/III but not stray "IIII" runs. Article portion is allowed to
    # include hyphenated lisa numbers ("RT III, 2009, 14, 89-90").
    pattern = (
        r"RT\s+(?:I{1,3})\b\s*,\s*"
        r"(?:\d{2}\.\d{2}\.)?\d{4}\s*,\s*"
        r"\d+(?:\s*[-,]\s*\d+)*"
    )
    matches = re.findall(pattern, full_text)
    # Deduplicate while preserving order
    seen = set()
    for m in matches:
        normalized = re.sub(r"\s+", " ", m.strip())
        if normalized not in seen:
            seen.add(normalized)
            references.append(normalized)
    return references


def load_draft_amendments(*, failures: list[str] | None = None) -> list[dict]:
    """
    Load draft legislation that are amendment bills from eelnoud directory.
    Returns list of dicts with draft_id, affected_law_name, etc.
    """
    drafts: list[dict] = []

    # Try the combined file first
    combined_path = EELNOUD_DIR / "eelnoud_combined.jsonld"
    if not combined_path.exists():
        return drafts

    try:
        with open(combined_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        if failures is not None:
            failures.append(
                f"load_draft_amendments | {combined_path.name} | "
                f"{type(exc).__name__}: {str(exc)[:80]}"
            )
        return drafts

    for node in doc.get("@graph", []):
        types = node.get("@type", [])
        if "estleg:DraftLegislation" not in types:
            continue

        # Check if it is an amendment bill
        draft_type = node.get("estleg:draftType", {})
        if isinstance(draft_type, dict):
            type_id = draft_type.get("@id", "")
        else:
            type_id = str(draft_type)

        if type_id != "estleg:DraftType_AmendmentBill":
            continue

        affected = node.get("estleg:affectedLawName")
        if not affected:
            continue

        # Normalize to list
        if isinstance(affected, str):
            affected = [affected]

        draft_id = node.get("@id", "")
        # ``rdfs:label`` is a language-tagged value-object since PR #297
        # (``{"@value": title, "@language": "et"}``). Unwrap to the plain
        # title (issue #385) so the AmendmentLink label embeds the title
        # text, not a stringified Python dict.
        label = jsonld_text(node.get("rdfs:label", ""))
        pub_date = node.get("estleg:publicationDate")
        if isinstance(pub_date, dict):
            pub_date = pub_date.get("@value")

        for law_name in affected:
            drafts.append({
                "draft_id": draft_id,
                "draft_label": label,
                "affected_law_name": law_name,
                "publication_date": pub_date,
            })

    return drafts


def _normalize_title(text: str) -> str:
    """Normalise a title for exact comparison (lower, collapse ws)."""
    return re.sub(r"\s+", " ", text.lower()).strip()


def _title_tokens(text: str) -> set[str]:
    """Tokenise a title for Jaccard similarity (alnum tokens, lowered)."""
    return {t for t in re.findall(r"[\wäöüõšž]+", text.lower()) if t}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersect = a & b
    union = a | b
    return len(intersect) / len(union) if union else 0.0


def _resolve_map_key(
    title_map: dict[str, list[str]],
    key: str,
    law_name: str,
    stage: str,
    *,
    failures: list[str] | None = None,
) -> str | None:
    """Resolve a single ``title_map`` key to a unique slug, or refuse.

    Returns a slug when the key resolves to a single act, else ``None``.

    "A single act" is judged by BASE slug (issue #595): multipart-law parts
    (``…_osa1`` … ``…_osaN``) all carry the law's shared title and reduce to
    one ``base_slug_of`` — they are one law, not a collision, so we return the
    first part (the chain builder re-expands the group). The match is AMBIGUOUS
    only when the key maps to >1 *distinct base slug* — genuinely different
    co-named documents (e.g. the 181 acts titled ``"kohanime määramine"``). The
    old last-writer-wins map hid those behind a single survivor; we now surface
    the collision to ``failures`` and return ``None`` rather than binding a
    draft to an arbitrary co-named act. A key absent from the map returns
    ``None`` so the caller falls through to the next strategy.
    """
    slugs = title_map.get(key)
    if not slugs:
        return None
    distinct_bases = {base_slug_of(slug) for slug in slugs}
    if len(distinct_bases) == 1:
        # One law (single act, or the parts of one multipart law).
        return slugs[0]
    if failures is not None:
        failures.append(
            f"match_law_name_to_slug | ambiguous | stage={stage} | "
            f"input={law_name!r} | key={key!r} | "
            f"distinct_bases={sorted(distinct_bases)[:5]}"
        )
    return None


def match_law_name_to_slug(
    law_name: str,
    title_map: dict[str, list[str]],
    laws: dict[str, dict],
    *,
    failures: list[str] | None = None,
) -> str | None:
    """Match a law name (from a draft's affectedLawName) to an existing law slug.

    Strategy (strict-first). Every ``title_map`` lookup now resolves through
    :func:`_resolve_map_key`, which returns a slug ONLY when the key maps to a
    single act; a key shared by several co-named acts is AMBIGUOUS and yields a
    failure + ``None`` instead of an arbitrary survivor (issue #595):
      1. Exact normalised title match.
      2. Slugified exact match.
      3. Direct slug match.
      4. Suffix-normalised match ("X seaduse" -> "X seadus").
      5. Token-level Jaccard ≥ 0.9 — exactly one candidate wins.
         Multiple candidates above the threshold are AMBIGUOUS — bail
         out and surface to the failures sink rather than picking one.

    An ambiguous hit at stages 1–4 returns ``None`` immediately (we do NOT
    fall through to a fuzzier stage, which would only pick a different
    arbitrary act): the draft genuinely cannot be bound to one act offline.
    """
    norm_name = _normalize_title(law_name)
    if not norm_name:
        return None

    # 1. Exact normalised title
    if norm_name in title_map:
        return _resolve_map_key(
            title_map, norm_name, law_name, "exact_title", failures=failures
        )

    # 2. Slugified exact match
    slug_name = slugify(law_name)
    if slug_name and slug_name in title_map:
        return _resolve_map_key(
            title_map, slug_name, law_name, "slugified", failures=failures
        )

    # 3. Direct slug match against laws keyed by slug
    if slug_name and slug_name in laws:
        return slug_name

    # 4. Suffix normalisation: "X seaduse" -> "X seadus"
    normalized = re.sub(r"seaduse$", "seadus", norm_name)
    normalized = re.sub(r"seadustiku$", "seadustik", normalized)
    if normalized in title_map:
        return _resolve_map_key(
            title_map, normalized, law_name, "suffix_title", failures=failures
        )
    slug_norm = slugify(normalized)
    if slug_norm and slug_norm in title_map:
        return _resolve_map_key(
            title_map, slug_norm, law_name, "suffix_slug", failures=failures
        )
    if slug_norm and slug_norm in laws:
        return slug_norm

    # 5. Token Jaccard fallback — strict, NO substring fallthrough.
    name_tokens = _title_tokens(law_name)
    if not name_tokens:
        return None
    candidates: list[tuple[float, str, str]] = []
    for title_lower, slugs in title_map.items():
        score = _jaccard(name_tokens, _title_tokens(title_lower))
        if score >= 0.9:
            for slug in slugs:
                candidates.append((score, title_lower, slug))

    if not candidates:
        return None

    # Distinct slugs above threshold => ambiguous match.
    distinct_slugs = {slug for _, _, slug in candidates}
    if len(distinct_slugs) > 1:
        if failures is not None:
            top = sorted(candidates, key=lambda x: -x[0])[:3]
            failures.append(
                "match_law_name_to_slug | ambiguous | stage=jaccard | "
                f"input={law_name!r} | candidates="
                f"{[(s, slug) for s, _, slug in top]}"
            )
        return None

    # Exactly one slug at >= 0.9 wins.
    return candidates[0][2]


# Act-root properties this script OWNS and rewrites every run. Both are
# cleared in Step 0 so a rerun removes stale entries (the #384 "never
# regenerated" class of bug) — including stale draft-derived entries that the
# pre-#423 code wrote into ``estleg:amendedBy``. ``estleg:hasProposedAmendment``
# (inverse of ``estleg:proposesToAmend``) is the act-root back-link to
# ProposedAmendment nodes (#423); clearing it keeps proposals out of
# ``amendedBy`` AND drops proposals whose draft has since been enacted/withdrawn.
#
# NOTE: ``estleg:affectedBy`` is deliberately NOT cleared here. That act-root
# property (act → Draft, "Pending drafts affecting this provision") is owned by
# extract_draft_impact.py, which runs its own clear-then-rewrite. Touching it
# from this script would clobber that pipeline depending on run order.
_OWNED_ACT_ROOT_PROPS = ("estleg:amendedBy", "estleg:hasProposedAmendment")


def clear_amended_by_from_file(
    filepath: Path,
    *,
    failures: list[str] | None = None,
) -> bool:
    """
    Remove this script's owned act-root amendment links (estleg:amendedBy and
    estleg:hasProposedAmendment) from all nodes in a law JSON-LD file.
    Returns True if the file was modified.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        if failures is not None:
            failures.append(
                f"clear_amended_by_from_file | {filepath.name} | "
                f"{type(exc).__name__}: {str(exc)[:80]}"
            )
        return False

    modified = False
    for node in data.get("@graph", []):
        for prop in _OWNED_ACT_ROOT_PROPS:
            if prop in node:
                del node[prop]
                modified = True

    if modified:
        save_json(filepath, data)
    return modified


def load_law_abbreviations(
    path: Path | None = None,
    *,
    failures: list[str] | None = None,
) -> dict[str, dict]:
    """Load the compact law-abbreviation registry (slug -> entry).

    Returns ``{}`` (with a recorded failure) if the file is absent or
    unreadable — callers fall back to the slug so generation never crashes.
    """
    path = path if path is not None else LAW_ABBREVIATIONS_PATH
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        if failures is not None:
            failures.append(
                f"load_law_abbreviations | {path.name} | "
                f"{type(exc).__name__}: {str(exc)[:80]}"
            )
        return {}


def _stem_from_amends_target(amends_iri: str) -> str | None:
    """Recover a compact prefix from an ``estleg:amends`` target IRI.

    ``estleg:Reg_1052132_Map_2026`` → ``Reg_1052132``; ``estleg:AVRS_Map_2026``
    → ``AVRS``. Returns ``None`` when nothing compact-looking can be recovered.
    """
    if not amends_iri or not amends_iri.startswith("estleg:"):
        return None
    local = amends_iri[len("estleg:"):]
    prev = None
    while prev != local:
        prev = local
        local = _AMENDS_STEM_STRIP_RE.sub("", local)
    if not local or not _COMPACT_PREFIX_RE.match(local):
        return None
    return local


def short_prefix_for_base_slug(
    base_slug: str,
    canonical_ontology_id: str,
    abbreviations: dict[str, dict],
) -> str:
    """Return the compact IRI prefix the amendment IRIs should use.

    Resolution order (issue #192):
      1. ``data/law_abbreviations.json`` — the law's registered ``abbrev``.
      2. The compact stem recovered from the act's canonical ontology IRI
         (``estleg:Reg_<id>_Map_2026`` → ``Reg_<id>``) — covers regulations
         and the handful of slug-spelling mismatches absent from the registry.
      3. ``sanitize_id(base_slug)`` as a last resort so generation never fails
         on a slug with no registry entry and no usable ontology IRI.
    """
    entry = abbreviations.get(base_slug)
    if isinstance(entry, dict) and entry.get("abbrev"):
        return entry["abbrev"]
    stem = _stem_from_amends_target(canonical_ontology_id)
    if stem and len(stem) < len(base_slug):
        return stem
    return sanitize_id(base_slug)


def _stable_amend_suffix(amend: dict) -> str:
    """Compute a stable IRI suffix for an XML amendment record.

    Hashes the amendment's canonical identity (issue #263): the
    ``rt_reference`` when present (the unique RT publication ref), else the
    ``(date, entry_into_force, akt_viide)`` tuple. This mirrors
    ``_amend_dedup_key`` so two records that dedup to the same identity hash
    identically (idempotent reruns) while two genuinely-distinct records hash
    differently — leaving the ``_N`` disambiguator a true last resort rather
    than the over-counting source it was when only ``rt_reference`` was hashed.
    """
    payload = amend.get("rt_reference") or "|".join(
        [
            amend.get("date") or "",
            amend.get("entry_into_force") or "",
            amend.get("akt_viide") or "",
        ]
    )
    digest = hashlib.md5(payload.encode("utf-8")).hexdigest()
    return digest[:10]


# Act-level type markers. ``estleg:amendedBy`` is an act-level property: it
# may only be stamped onto a node typed as an act (covers laws, state
# regulations, KOV regulations). Mirrors ``extract_temporal_data.ACT_TYPE_MARKERS``
# / ``find_act_node`` (issue #128, #289) so this script stops the old
# ``graph[0]`` fallback that could stamp the link onto a ``LegalConcept``.
_ACT_TYPE_MARKERS = {"estleg:Act"}


def find_act_node(graph: list[dict]) -> dict | None:
    """Return the act node ``estleg:amendedBy`` belongs on, or None.

    Preference order (mirrors ``extract_temporal_data.find_act_node``):
    the ``owl:Ontology`` act-metadata node when it is also typed
    ``estleg:Act`` (the historical write site), then any ``estleg:Act``-typed
    node. Returns ``None`` when neither exists — the caller must then SKIP
    enrichment for that file rather than falling back to ``graph[0]`` (some
    peeps, e.g. ``tsiviilseadustik_osa6/osa7``, have ``graph[0]`` =
    ``estleg:LegalConcept``).
    """
    for node in graph:
        if not isinstance(node, dict):
            continue
        types = set(node.get("@type") or [])
        if "owl:Ontology" in types and types & _ACT_TYPE_MARKERS:
            return node
    for node in graph:
        if not isinstance(node, dict):
            continue
        if set(node.get("@type") or []) & _ACT_TYPE_MARKERS:
            return node
    return None


def _amend_sort_key(amend: dict) -> str:
    """Return a YYYY-MM-DD-shaped string for chronological sorting.

    Prefer ``entry_into_force`` (when the amendment took legal effect),
    fall back to the act's signing date.

    A record with NO usable date (e.g. an implausible year already nulled by
    :func:`parse_date`, issue #587) sorts to the FRONT via a low sentinel —
    NOT the end. The chronologically-last entry is flagged
    ``estleg:isCurrentAmendment``; pushing the dateless record last (the old
    ``"9999-99-99"`` behaviour) let a corrupt/undated amendment win "current".
    Sorting it first means only a real, plausibly-dated amendment can be the
    in-force tip.
    """
    eif = amend.get("entry_into_force") or ""
    sig = amend.get("date") or ""
    primary = eif or sig
    return primary or "0000-00-00"


def _has_valid_amend_date(amend: dict) -> bool:
    """True when an amendment carries at least one usable (plausible) date.

    ``parse_date`` already nulls implausible years (#587), so this is simply
    "does the record still have an ``entry_into_force`` or ``date``". Used to
    decide whether an amendment is eligible to be flagged the *current* one —
    a dateless record never is.
    """
    return bool(amend.get("entry_into_force") or amend.get("date"))


def main() -> int:
    AMENDMENTS_DIR.mkdir(parents=True, exist_ok=True)
    EELNOUD_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("Estonian Legal Ontology - Generate Amendment History")
    print("=" * 70)

    _start = time.perf_counter()

    # Coverage counters — initialised early so all downstream steps
    # can update them. Amendment-history is a hybrid pipeline: per-act
    # estleg:amendedBy writes (in-place) PLUS aggregate report files.
    _files_processed = 0
    _files_processed_kov = 0
    _files_with_output = 0
    _files_with_output_kov = 0
    # Coverage triples are split into the two surfaces this script
    # writes so the totals don't lie:
    #   - amendedBy_link_total: estleg:amendedBy refs stamped on each
    #     act's ontology node (one per chain entry).
    #   - amendment_event_triples: properties (@type, amends,
    #     amendmentDate, entryIntoForce, rtReference, label, etc.)
    #     emitted on each Amendment / AmendmentLink node.
    _amendedBy_link_total = 0
    _amendment_event_triples = 0
    _amendedBy_link_total_kov = 0
    _amendment_event_triples_kov = 0
    _fallback_hits = 0
    _unresolved = 0
    _unsafe_cleanup_failures: list[str] = []
    _soft_failures: list[str] = []
    _skip_reasons: dict[str, int] = {}

    # Step 0: Clear existing amendedBy references
    print("\n[0/5] Clearing existing estleg:amendedBy from all law files...")
    cleared_count = 0
    for peep_file in iter_peep_files():
        if clear_amended_by_from_file(peep_file, failures=_soft_failures):
            cleared_count += 1
    print(f"  Cleared estleg:amendedBy from {cleared_count} files")

    # Step 1: Load all law files
    print("\n[1/5] Loading law JSON-LD files...")
    laws = load_law_files(failures=_unsafe_cleanup_failures)
    print(f"  Loaded {len(laws)} law files")

    title_map = build_title_to_slug_map(laws)
    print(f"  Built title-to-slug mapping with {len(title_map)} entries")

    # Compact-abbreviation registry (issue #192) — used so the amendment IRIs
    # carry the law's compact prefix instead of the long <base_slug> segment.
    law_abbreviations = load_law_abbreviations(failures=_soft_failures)
    print(f"  Loaded {len(law_abbreviations)} law abbreviations")

    # Step 2: Extract amendments from XML files (paired per-peep via
    # globalId, with a slug fallback for legacy laws that have no
    # estleg:globalId on their act node).
    print("\n[2/5] Extracting amendment references from XML files...")
    xml_lookup = build_globalid_xml_lookup(DATA_DIR)
    print(f"  Indexed {len(xml_lookup)} XML files by globalId")

    amendments_by_slug: dict[str, list[dict]] = {}
    rt_refs_by_slug: dict[str, list[str]] = {}
    total_amendments = 0
    paired_count = 0
    unpaired_count = 0
    # Track which slugs were paired+extracted (regardless of whether
    # the XML had any muutmismarge blocks) — feeds _files_processed
    # in the coverage report.
    paired_slugs: set[str] = set()
    # Set of canonical XML paths that came via the globalId lookup —
    # used to detect when the slug fallback fired.
    _lookup_paths = {str(p) for p in xml_lookup.values()}

    for slug, info in laws.items():
        # info["path"] is the peep file path; pair via globalId, with
        # slug fallback for laws that don't carry estleg:globalId.
        xml_path = pair_peep_with_xml(
            info["path"], xml_lookup, data_dir=DATA_DIR
        )
        if xml_path is None:
            unpaired_count += 1
            continue
        paired_count += 1
        paired_slugs.add(slug)
        if str(xml_path) not in _lookup_paths:
            _fallback_hits += 1
        amendments = extract_amendments_from_xml(
            xml_path, failures=_unsafe_cleanup_failures
        )
        if amendments:
            amendments_by_slug[slug] = amendments
            total_amendments += len(amendments)
        rt_refs = extract_rt_references_from_text(xml_path, failures=_soft_failures)
        if rt_refs:
            rt_refs_by_slug[slug] = rt_refs

    _unresolved = unpaired_count

    print(f"  Paired XML for {paired_count} of {len(laws)} laws "
          f"({unpaired_count} unpaired — see coverage report).")
    print(f"  Found {total_amendments} amendment references across {len(amendments_by_slug)} laws")
    print(f"  Found RT references in {len(rt_refs_by_slug)} laws")

    # Step 3: Load draft amendments
    print("\n[3/5] Loading draft legislation amendment relationships...")
    draft_amendments = load_draft_amendments(failures=_unsafe_cleanup_failures)
    print(f"  Found {len(draft_amendments)} amendment bill entries")

    # Match drafts to existing laws.
    #
    # Drafts often list the affected law multiple times (e.g. once in
    # genitive form "X seaduse" and once with the full title "X seaduse
    # muutmise ja sellega seonduvalt teiste seaduste muutmise seadus").
    # Both forms can resolve to the same target slug, which would
    # produce duplicate AmendmentLink @ids when chain_entries are built.
    # Dedupe by draft_id within each target slug to avoid that.
    draft_matches: dict[str, list[dict]] = {}  # target law slug -> list of amending drafts
    seen_per_target: dict[str, set[str]] = {}
    matched_drafts = 0
    unmatched_drafts = 0

    for da in draft_amendments:
        target_slug = match_law_name_to_slug(
            da["affected_law_name"], title_map, laws, failures=_soft_failures
        )
        if target_slug:
            seen = seen_per_target.setdefault(target_slug, set())
            if da["draft_id"] in seen:
                # Duplicate (same draft → same target via a second
                # affected_law_name spelling). Skip the duplicate.
                continue
            seen.add(da["draft_id"])
            draft_matches.setdefault(target_slug, []).append(da)
            matched_drafts += 1
        else:
            unmatched_drafts += 1

    print(f"  Matched {matched_drafts} drafts to existing laws")
    print(f"  Unmatched: {unmatched_drafts} (law not in ontology)")

    # Step 4: Build amendment chain data and enrich JSON-LD files.
    #
    # Multipart laws (e.g. võlaõigusseadus_osa1..osa9) all share the
    # same canonical XML; they amend "Võlaõigusseadus" as a whole.
    # Group by base_slug (= slug with trailing ``_osaN`` stripped) so
    # one chain document covers every part — separate per-part files
    # would race-write the same path and lose data.
    print("\n[4/5] Building amendment chains and enriching JSON-LD files...")
    enriched = 0
    amendment_chains: list[dict] = []
    stale_chain_files_removed = 0

    # Coverage: every paired peep counts as processed regardless of
    # whether it ends up grouped under a base_slug.
    for slug, info in sorted(laws.items()):
        is_kov = "regulations/kov" in str(info["path"])
        if slug in paired_slugs:
            _files_processed += 1
            if is_kov:
                _files_processed_kov += 1

    # Group paired laws by base_slug (drop trailing _osaN suffix).
    groups: dict[str, list[tuple[str, dict]]] = {}
    for slug, info in laws.items():
        base_slug = base_slug_of(slug)
        groups.setdefault(base_slug, []).append((slug, info))

    stale_chain_files_removed += _remove_obsolete_amendment_chain_files(
        set(groups),
        safe_to_delete=not _unsafe_cleanup_failures,
    )

    for base_slug in sorted(groups):
        # Order parts by natural osa number so members[0] is deterministically
        # the lowest-numbered part (osa1), not whatever lexical/glob order put
        # first — osa10/osa11 must NOT win the canonical slot (issue #606).
        members = sorted(groups[base_slug], key=lambda m: _osa_order_key(m[0]))

        # Pull XML amendments / drafts for the group. All parts of a
        # multipart law share the same canonical XML so any member's
        # entry is canonical; pick the first non-empty one.
        xml_amendments: list[dict] = []
        for slug, _info in members:
            cand = amendments_by_slug.get(slug)
            if cand:
                xml_amendments = cand
                break
        if not xml_amendments:
            xml_amendments = amendments_by_slug.get(base_slug, [])

        drafts: list[dict] = []
        seen_draft_ids: set[str] = set()
        for slug, _info in members:
            for da in draft_matches.get(slug, []):
                if da["draft_id"] in seen_draft_ids:
                    continue
                seen_draft_ids.add(da["draft_id"])
                drafts.append(da)
        for da in draft_matches.get(base_slug, []):
            if da["draft_id"] in seen_draft_ids:
                continue
            seen_draft_ids.add(da["draft_id"])
            drafts.append(da)

        if not xml_amendments and not drafts:
            has_paired_member = False
            for slug, _info in members:
                if slug in paired_slugs:
                    has_paired_member = True
                    _skip_reasons["no_amendments_found"] = (
                        _skip_reasons.get("no_amendments_found", 0) + 1
                    )
            if has_paired_member and not _unsafe_cleanup_failures:
                stale_chain_files_removed += _remove_amendment_chain_file(base_slug)
            continue

        # Sort xml_amendments chronologically (entry_into_force first,
        # falling back to signing date) so chain index numbers track
        # legal-effect order, not arbitrary XML document order.
        xml_amendments_sorted = sorted(xml_amendments, key=_amend_sort_key)

        # Build chain entries ONCE for the whole base_slug.
        # Each Amendment node gets a stable IRI suffix derived from
        # its rt_reference so regenerations don't reshuffle IDs.
        chain_entries: list[dict] = []
        # Use the first member's ontology IRI as the canonical "amends"
        # target for the chain document. (Each part still gets its own
        # estleg:amendedBy — see the per-member loop below.)
        canonical_ontology_id = ""
        canonical_title = members[0][1]["title"]
        # Collect EVERY part's ontology IRI (issue #327). A multipart law's
        # AmendmentEvents must declare ``estleg:amends`` against ALL parts, not
        # just the first osa — otherwise a bare-SPARQL forward query
        # "laws amended by act X" returns only osa1 and silently drops
        # osa2..N. Order-preserving + de-duplicated so the list is stable and
        # carries no repeats when two parts share an ontology node.
        member_ontology_ids: list[str] = []
        for _slug, _info in members:
            for node in _info["doc"].get("@graph", []):
                if "owl:Ontology" in (node.get("@type") or []):
                    onto_id = node.get("@id", "")
                    if onto_id and onto_id not in member_ontology_ids:
                        member_ontology_ids.append(onto_id)
                    if not canonical_ontology_id:
                        canonical_ontology_id = onto_id
                        if canonical_title is None or not canonical_title:
                            canonical_title = (
                                node.get("dc:source")
                                or node.get("rdfs:label")
                                or ""
                            )
                    break

        # ``estleg:amends`` payload: a single ``{"@id": ...}`` for a
        # single-part law, or a LIST of ``{"@id": ...}`` (one per part) for a
        # multipart law so each AmendmentEvent points at every amended part.
        def _amends_value() -> dict | list[dict] | None:
            if not member_ontology_ids:
                return None
            if len(member_ontology_ids) == 1:
                return {"@id": member_ontology_ids[0]}
            return [{"@id": oid} for oid in member_ontology_ids]

        # Compact prefix for the Amendment_/AmendmentChain_/AmendmentLink_
        # IRIs minted below — the law's registered abbreviation, the
        # regulation's Reg_<id> stem, or (last resort) the sanitised slug.
        amend_prefix = short_prefix_for_base_slug(
            base_slug, canonical_ontology_id, law_abbreviations
        )

        seen_amend_ids: set[str] = set()
        # Index of the last chain entry whose source amendment carries a valid
        # (plausibly-dated) date — the only entry eligible for the
        # ``isCurrentAmendment`` flag (issue #587). Tracked alongside the
        # lock-step build so a dateless record (e.g. one whose corrupt year was
        # nulled by ``parse_date``) can never be flagged current.
        last_valid_dated_idx: int | None = None
        for amend in xml_amendments_sorted:
            suffix = _stable_amend_suffix(amend)
            amend_id = f"estleg:Amendment_{amend_prefix}_{suffix}"
            # Collisions on hash prefix are extremely unlikely but
            # defended against here so re-runs remain idempotent.
            disambig = 1
            base_id = amend_id
            while amend_id in seen_amend_ids:
                disambig += 1
                amend_id = f"{base_id}_{disambig}"
            seen_amend_ids.add(amend_id)

            amend_node: dict = {
                "@id": amend_id,
                "@type": ["owl:NamedIndividual", "estleg:AmendmentEvent"],
            }
            amends_value = _amends_value()
            if amends_value is not None:
                amend_node["estleg:amends"] = amends_value

            if amend.get("date"):
                amend_node["estleg:amendmentDate"] = make_xsd_date(amend["date"])
            if amend.get("entry_into_force"):
                amend_node["estleg:entryIntoForce"] = make_xsd_date(amend["entry_into_force"])
            # ``estleg:amendingAct`` — WHICH act made this change (issue #587).
            # Derived OFFLINE from the source ``<muutmismarge>``: ``akt_viide``
            # is the amending act's Riigi Teataja globalId (e.g. ``178370``) and
            # ``rt_reference`` is its publication marker (``RT I, 2002, 57, 356``).
            # Both were previously parsed only to seed the dedup hash and then
            # discarded, so every same-day distinct amendment rendered as an
            # identical ``"Muudatus YYYY-MM-DD"`` node with no way to say which
            # act amended the law. We surface the best available offline
            # identifier as a literal on the event (NOT an ``@id`` to a corpus
            # node: amending acts are overwhelmingly absent from the corpus — 0
            # of a 987-sample ``akt_viide`` matched a corpus act globalId — so an
            # ``@id`` would dangle). No data is fabricated: emitted only when an
            # identifier is actually present.
            amending_act = _amending_act_value(amend)
            if amending_act is not None:
                amend_node["estleg:amendingAct"] = amending_act
            if amend.get("rt_reference"):
                amend_node["estleg:rtReference"] = amend["rt_reference"]
                amend_node["rdfs:label"] = f"Muudatus: {amend['rt_reference']}"
            else:
                amend_node["rdfs:label"] = f"Muudatus {amend.get('date', 'unknown')}"
            if _has_valid_amend_date(amend):
                last_valid_dated_idx = len(chain_entries)
            chain_entries.append(amend_node)

        # Mark the LATEST *effected, validly-dated* amendment as the current one
        # (issues #389, #587). ``chain_entries`` holds ONLY the RT-derived
        # AmendmentEvent nodes (proposals are built separately below and are
        # never flagged — #423). ``xml_amendments_sorted`` is ascending by
        # legal-effect date, so the last *plausibly-dated* node is the in-force
        # tip. We flag ``last_valid_dated_idx`` rather than ``chain_entries[-1]``
        # so a record whose corrupt/future year was nulled by ``parse_date``
        # (and which therefore sorts to the front with no date) can never win
        # "current". When NO entry has a valid date, the flag is omitted
        # entirely rather than guessed. SHACL note for the shapes team: this
        # property warrants ``sh:maxCount 1`` per amended act on
        # AmendmentEventShape (see report).
        if last_valid_dated_idx is not None:
            chain_entries[last_valid_dated_idx]["estleg:isCurrentAmendment"] = {
                "@value": "true",
                "@type": "xsd:boolean",
            }

        # Build PROPOSED-amendment nodes from draft bills (issue #423). The
        # referenced drafts are in Review/Submission/PublicConsultation phase —
        # none enacted — so they are NOT effected legal changes and must NOT be
        # typed ``estleg:AmendmentEvent``. They become ``estleg:ProposedAmendment``
        # and carry proposal-scoped predicates only:
        #   * ``estleg:proposesToAmend`` (NOT ``estleg:amends`` — that asserts an
        #     effected change and is the inverse of the act-root ``amendedBy``).
        #   * ``estleg:publicationDate`` (the draft's EIS publication date — NOT
        #     ``estleg:amendmentDate``, which is reserved for adoption dates).
        #   * ``estleg:amendingDraft`` → the Draft_* node (kept).
        # They NEVER carry ``estleg:isCurrentAmendment``.
        #
        # IRIs stay ``AmendmentLink_{sanitize_id(draft_id)}_{amend_prefix}`` for
        # @id stability across the retype (existing consumers / git diffs key on
        # the suffix, not the class). IRIs are id-based and position-independent,
        # so sorting by publication_date fixes chain order (issue #387: 119
        # chains were non-monotonic) WITHOUT disturbing any @id. Undated drafts
        # sort last via a high sentinel.
        drafts_sorted = sorted(drafts, key=_draft_publication_sort_key)
        proposed_nodes: list[dict] = []
        for da in drafts_sorted:
            draft_id = da["draft_id"]
            proposed_node: dict = {
                "@id": (
                    f"estleg:AmendmentLink_"
                    f"{sanitize_id(draft_id.replace('estleg:', ''))}_"
                    f"{amend_prefix}"
                ),
                "@type": ["owl:NamedIndividual", "estleg:ProposedAmendment"],
                "rdfs:label": f"Eelnõu muudatus: {da['draft_label']}",
                "estleg:amendingDraft": {"@id": draft_id},
            }
            amends_value = _amends_value()
            if amends_value is not None:
                # proposesToAmend mirrors the multipart ``amends`` payload shape
                # (single object or list of objects) but with proposal scope.
                proposed_node["estleg:proposesToAmend"] = amends_value
            if da.get("publication_date"):
                proposed_node["estleg:publicationDate"] = make_xsd_date(
                    da["publication_date"]
                )
            proposed_nodes.append(proposed_node)

        # Stamp the act-root amendment links on every member's ontology node so
        # each part's peep file references the shared chain. EFFECTED events go
        # to ``estleg:amendedBy``; PROPOSALS go to ``estleg:hasProposedAmendment``
        # (inverse of ``estleg:proposesToAmend``) — never ``amendedBy`` (#423).
        # Both were cleared in Step 0, so reruns drop stale entries.
        amended_by_refs = [{"@id": e["@id"]} for e in chain_entries]
        proposed_refs = [{"@id": p["@id"]} for p in proposed_nodes]

        members_enriched_for_kov = 0
        members_enriched = 0
        for slug, info in members:
            doc = info["doc"]
            graph = doc.get("@graph", [])
            # ``estleg:amendedBy`` / ``estleg:hasProposedAmendment`` are
            # act-level — locate the act node the same way extract_temporal_data
            # does (issue #289). SKIP (with a counter) when there is no act node
            # rather than stamping onto ``graph[0]``, which could be a
            # ``LegalConcept`` (the #128 bug).
            ontology_node = find_act_node(graph)
            if ontology_node is None:
                _skip_reasons["no_act_node"] = (
                    _skip_reasons.get("no_act_node", 0) + 1
                )
                continue

            ctx = doc.get("@context", {})
            if isinstance(ctx, dict) and "dcterms" not in ctx:
                ctx["dcterms"] = "http://purl.org/dc/terms/"
                doc["@context"] = ctx

            # Effected events → amendedBy; proposals → hasProposedAmendment.
            # Both keys were cleared in Step 0; only write the ones that have
            # refs so a draft-only act gets hasProposedAmendment without an
            # empty amendedBy array, and an RT-only act stays as it was (#423).
            if amended_by_refs:
                ontology_node["estleg:amendedBy"] = amended_by_refs
            if proposed_refs:
                ontology_node["estleg:hasProposedAmendment"] = proposed_refs
            save_json(info["path"], doc)
            enriched += 1
            members_enriched += 1

            is_member_kov = "regulations/kov" in str(info["path"])
            n_links = len(amended_by_refs) + len(proposed_refs)
            _amendedBy_link_total += n_links
            _files_with_output += 1
            if is_member_kov:
                _amendedBy_link_total_kov += n_links
                _files_with_output_kov += 1
                members_enriched_for_kov += 1

        # Every node that lands in the chain document — effected AmendmentEvent
        # nodes AND proposed-amendment nodes (#423). ``estleg:totalAmendments``
        # historically counted the whole chain graph, so keep both kinds in the
        # count to preserve the on-disk artifact's meaning (drafts were already
        # included pre-#423; only their @type/predicates change).
        chain_nodes = chain_entries + proposed_nodes

        # Amendment event triples: count properties on each chain node.
        if chain_nodes:
            event_triples = sum(
                # @id + each property key (excluding @id itself counted once via key list)
                len(node) for node in chain_nodes
            )
            _amendment_event_triples += event_triples
            if members_enriched_for_kov > 0:
                # Attribute event triples proportionally to KOV — for a
                # purely KOV chain, all event triples count toward KOV.
                if members_enriched > 0:
                    kov_share = int(
                        round(event_triples * members_enriched_for_kov / members_enriched)
                    )
                else:
                    kov_share = 0
                _amendment_event_triples_kov += kov_share

        # Save ONE chain doc per base_slug. The chain @id uses the compact
        # prefix; the *filename* keeps the human-readable base_slug (it's a
        # stable on-disk artifact key, not part of the IRI namespace). A
        # draft-only act (chain_entries empty, proposed_nodes non-empty) still
        # gets a chain file (#423).
        if chain_nodes:
            chain_doc = {
                "@context": CONTEXT,
                "@graph": [
                    {
                        "@id": f"estleg:AmendmentChain_{amend_prefix}",
                        "@type": ["owl:Ontology"],
                        "rdfs:label": f"Muudatuste ahel: {canonical_title}",
                        "dc:source": canonical_title,
                        "estleg:totalAmendments": {
                            "@value": str(len(chain_nodes)),
                            "@type": "xsd:integer",
                        },
                    },
                    *chain_nodes,
                ],
            }
            chain_path = AMENDMENTS_DIR / f"amendments_{base_slug}.json"
            save_json(chain_path, chain_doc)
            amendment_chains.append({
                "law": canonical_title,
                "base_slug": base_slug,
                "parts": [s for s, _ in members],
                "xml_amendments": len(xml_amendments_sorted),
                "draft_amendments": len(drafts),
                "total": len(chain_nodes),
                "file": chain_path.name,
            })

    print(f"  Enriched {enriched} law files with amendment references")
    print(f"  Created {len(amendment_chains)} amendment chain files")
    if stale_chain_files_removed:
        print(f"  Removed {stale_chain_files_removed} stale amendment chain files")

    # Step 5: Generate report
    print("\n[5/5] Generating amendment_history_report.json...")

    # Find most amended laws
    amendment_chains_sorted = sorted(amendment_chains, key=lambda x: x["total"], reverse=True)

    # Multipart laws are now already aggregated into one chain doc per
    # base_slug, so this block just maps law title -> stats. Kept for
    # backwards-compatible report shape.
    deduped_most_amended: dict[str, dict] = {}
    for c in amendment_chains_sorted:
        canon_name = c["law"]
        if canon_name in deduped_most_amended:
            entry = deduped_most_amended[canon_name]
            entry["total_amendments"] += c["total"]
            entry["xml_amendments"] += c["xml_amendments"]
            entry["draft_amendments"] += c["draft_amendments"]
            entry["_part_count"] += 1
        else:
            deduped_most_amended[canon_name] = {
                "law": canon_name,
                "total_amendments": c["total"],
                "xml_amendments": c["xml_amendments"],
                "draft_amendments": c["draft_amendments"],
                "_part_count": 1,
            }
    most_amended_list = sorted(
        deduped_most_amended.values(), key=lambda x: x["total_amendments"], reverse=True
    )
    for entry in most_amended_list:
        del entry["_part_count"]

    total_triples = _amendedBy_link_total + _amendment_event_triples
    total_triples_kov = _amendedBy_link_total_kov + _amendment_event_triples_kov
    _failures = _unsafe_cleanup_failures + _soft_failures

    # NOTE (issue #295): no wall-clock ``generated`` field. This report is
    # git-tracked and is NOT in OPERATIONAL_STATE_FILES, so embedding
    # ``date.today()`` made it re-diff on every run regardless of data
    # changes (timestamp-only churn, banned by AGENTS.md). The data below is
    # fully determined by the corpus, so the report is byte-stable across
    # reruns of the same inputs.
    report = {
        "summary": {
            "total_laws_analyzed": len(laws),
            "laws_with_amendments": len(amendment_chains),
            "total_amendment_references": total_amendments,
            "draft_amendments_matched": matched_drafts,
            "draft_amendments_unmatched": unmatched_drafts,
            "laws_enriched": enriched,
            # Split coverage triples — see comment near the counter
            # initialisations for why both surfaces are exposed.
            "amendedBy_link_total": _amendedBy_link_total,
            "amendment_event_triples": _amendment_event_triples,
            "triples_total": total_triples,
            "stale_chain_files_removed": stale_chain_files_removed,
            "failures": len(_failures),
        },
        "most_amended_laws": most_amended_list[:30],
        "amendment_chains": amendment_chains_sorted,
    }

    report_path = KRR_DIR / "reports" / "amendment_history_report.json"
    save_json(report_path, report)

    # Surface failures aggressively — silent swallowing was the root
    # cause of issue #173's bare-except ladder.
    if _failures:
        print(
            f"\n[P1] generate_amendment_history: {len(_failures)} failure(s) "
            "logged during run — see coverage report for samples.",
            file=sys.stderr,
        )

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Laws analyzed:            {len(laws)}")
    print(f"  Laws with amendments:     {len(amendment_chains)}")
    print(f"  Total amendment refs:     {total_amendments}")
    print(f"  Draft amendments matched: {matched_drafts}")
    print(f"  Law files enriched:       {enriched}")
    print(f"  Amendment chain files:    {len(amendment_chains)}")
    print(f"  amendedBy link triples:   {_amendedBy_link_total}")
    print(f"  Amendment event triples:  {_amendment_event_triples}")
    if most_amended_list:
        print("\n  Top 5 most amended laws:")
        for c in most_amended_list[:5]:
            print(f"    {c['law']}: {c['total_amendments']} amendments")
    print(f"\n  Report: {report_path.relative_to(REPO_ROOT)}")
    print(f"  Chains: {AMENDMENTS_DIR.relative_to(REPO_ROOT)}/")
    print("=" * 70)

    # ---------- coverage report ----------
    # Hybrid pipeline: per-act in-place amendedBy writes PLUS aggregate
    # report files. Counters track per-peep activity (paired +
    # extracted) and per-peep output (amendedBy triples written).
    all_peep_files = iter_peep_files()
    kov_files = [f for f in all_peep_files if "regulations/kov" in str(f)]
    _wall, _rate, _peak_mb = measure_runtime(_start, _files_processed)
    write_coverage_report(
        CoverageReport(
            pipeline="generate_amendment_history",
            run_timestamp=PINNED_RUN_TIMESTAMP,
            pipeline_version=resolve_pipeline_version(),
            input_files_total=len(all_peep_files),
            input_files_kov=len(kov_files),
            files_processed=_files_processed,
            files_processed_kov=_files_processed_kov,
            files_with_output=_files_with_output,
            files_with_output_kov=_files_with_output_kov,
            files_skipped=sum(_skip_reasons.values()),
            skip_reasons=_skip_reasons,
            triples_emitted=total_triples,
            triples_emitted_kov=total_triples_kov,
            fallback_hits=_fallback_hits,
            unresolved_references=_unresolved,
            wall_time_seconds=round(_wall, 2),
            items_per_second=round(_rate, 2),
            peak_memory_mb=round(_peak_mb, 1),
            error_count=len(_failures),
            failure_samples=_failures,
        ),
        REPO_ROOT / "krr_outputs" / "reports" / "kov"
        / "generate_amendment_history_coverage.json",
    )
    return 0


if __name__ == "__main__":
    main()
