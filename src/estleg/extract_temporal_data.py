#!/usr/bin/env python3
"""
Extract temporal validity data from cached Riigi Teataja XML files
and enrich existing law JSON-LD files with temporal properties.

This script:
1. Scans data/riigiteataja/*.xml for temporal metadata
2. Extracts entry-into-force dates, repeal dates, amendment dates, etc.
3. Adds estleg:entryIntoForce, estleg:repealDate, etc. to law JSON-LD files
4. Falls back to INDEX.json metadata when XML fields are missing
5. Generates temporal_data_report.json with statistics
"""

from __future__ import annotations

import argparse
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

from estleg.estleg_common import (
    BUILD_EVALUATION_DATE,
    build_globalid_xml_lookup,
    is_domain_individual,
    iter_peep_files,
    pair_peep_with_xml,
    save_json,
)
from estleg.kov_pipeline_coverage import (
    CoverageReport,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)

# Re-export for backwards-compat: existing tests and callers import
# these helpers from extract_temporal_data. Layer 2b can drop the
# re-export once those callers are updated.
__all__ = [
    "build_globalid_xml_lookup",
    "pair_peep_with_xml",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
KRR_DIR = REPO_ROOT / "krr_outputs"
DATA_DIR = REPO_ROOT / "data" / "riigiteataja"

NS = "https://w3id.org/estleg/"

# #602: named plausibility band for a parsed year (was a magic ``1900``/
# ``2100`` literal repeated in both date-parse paths). Years outside this
# inclusive range are source digit-transpositions (2918→2018) or EUR-Lex
# null-sentinels (1001-01-01) and are dropped rather than emitted as a
# poisoned xsd:date (#352).
MIN_PLAUSIBLE_YEAR = 1900
MAX_PLAUSIBLE_YEAR = 2100



def ln(tag: str) -> str:
    """Strip XML namespace prefix."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def ct(el: ET.Element, name: str) -> str | None:
    """Get child element text by local tag name."""
    for c in el:
        if ln(c.tag) == name and c.text:
            return c.text.strip()
    return None


def find_element_recursive(root: ET.Element, name: str) -> ET.Element | None:
    """Find first element with given local tag name anywhere in tree."""
    for el in root.iter():
        if ln(el.tag) == name:
            return el
    return None


def find_all_elements(root: ET.Element, name: str) -> list[ET.Element]:
    """Find all elements with given local tag name."""
    return [el for el in root.iter() if ln(el.tag) == name]


def parse_date(value: str) -> str | None:
    """
    Parse various date formats from Riigi Teataja XML into ISO date string.
    Handles: YYYY-MM-DD, YYYY-MM-DD+TZ, YYYY-MM-DD-TZ, DD.MM.YYYY, etc.
    Also handles malformed dates like "2011+02:00-01-01" where timezone offset
    is embedded in the middle of the date string.

    Strategy: try ``datetime.fromisoformat`` first on the raw value (Python 3.11+
    handles trailing timezone offsets including negative offsets). Only fall
    back to regex stripping when fromisoformat fails — this avoids the prior
    behaviour of greedily eating dashes that were date separators.
    """
    if not value:
        return None
    value = value.strip()

    # Step 1: try fromisoformat directly. This handles trailing offsets
    # like "+02:00" and "-02:00" natively (Python 3.11+) without
    # accidentally consuming date separators.
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        pass
    else:
        # Plausibility guard (#352): reject well-formed but logically
        # impossible years — source digit-transpositions (2918→2018,
        # 3006→2006, 0218→2018) and EUR-Lex null-sentinels (1001-01-01,
        # 1002-02-02). These pass fromisoformat but break every
        # as-of-date / ordering query, so we drop them rather than emit
        # a poisoned xsd:date.
        if parsed.year < MIN_PLAUSIBLE_YEAR or parsed.year > MAX_PLAUSIBLE_YEAR:
            return None
        return parsed.strftime("%Y-%m-%d")

    # Step 2: regex fallback for legacy/malformed inputs. Strip embedded
    # offsets first ("2011+02:00-01-01" → "2011-01-01"), then trailing
    # negative offsets at end-of-string only (so we don't eat date
    # separators in well-formed dates).
    cleaned = re.sub(r'\+\d{2}:\d{2}', '', value)
    cleaned = re.sub(r'-\d{2}:\d{2}$', '', cleaned)

    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        # Plausibility guard (#352): same year-range bound as the
        # fromisoformat path above — reject implausible years here too so
        # the fallback can't reintroduce a sentinel/typo date.
        if parsed.year < MIN_PLAUSIBLE_YEAR or parsed.year > MAX_PLAUSIBLE_YEAR:
            return None
        return parsed.strftime("%Y-%m-%d")
    return None


def parse_rt_year(value: str) -> str | None:
    """Extract a strict four-digit RT publication year."""
    if not value:
        return None
    match = re.match(r"^\s*(\d{4})(?:[+-]\d{2}:\d{2})?\s*$", value)
    if not match:
        return None
    return match.group(1)


def _coerce_iso_date(value: str) -> date:
    """Parse an ISO ``YYYY-MM-DD`` string into a ``date`` object.

    Defensive helper: ``parse_date`` is the only producer of these
    strings and it always normalises to ``YYYY-MM-DD``. The assertion
    catches caller bugs that would otherwise silently degrade ordering
    via string comparison.
    """
    assert isinstance(value, str), (
        f"_coerce_iso_date expected str, got {type(value).__name__}: {value!r}"
    )
    return date.fromisoformat(value)


def extract_temporal_from_xml(xml_path: Path) -> dict:
    """
    Extract all temporal metadata from a Riigi Teataja XML file.
    Returns dict with parsed date fields.
    """
    result: dict[str, str | None] = {
        "entry_into_force": None,
        "valid_from": None,
        "valid_until": None,
        "invalidation_date": None,
        "last_amendment_date": None,
        "publication_date": None,
        "publication_year": None,
        "adoption_date": None,
    }

    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except (ET.ParseError, OSError):
        return result

    # Look in <metaandmed> section
    metaandmed = find_element_recursive(root, "metaandmed")

    # Look in <kehtivus> block within metaandmed
    kehtivus = find_element_recursive(root, "kehtivus")
    if kehtivus is not None:
        # kehtivuseAlgus / kehtivAlates — effective from
        for tag in ("kehtivuseAlgus", "kehtivAlates"):
            val = ct(kehtivus, tag)
            if val:
                result["valid_from"] = parse_date(val)
                break
        # kehtivuseLopp / kehtivKuni — valid until
        for tag in ("kehtivuseLopp", "kehtivKuni"):
            val = ct(kehtivus, tag)
            if val:
                result["valid_until"] = parse_date(val)
                break

    # Look in <vastuvoetud> block for adoption and entry into force
    vastuvoetud = find_element_recursive(root, "vastuvoetud")
    if vastuvoetud is not None:
        # aktikuupaev — adoption date
        val = ct(vastuvoetud, "aktikuupaev")
        if val:
            result["adoption_date"] = parse_date(val)
        # joustumine — entry into force
        val = ct(vastuvoetud, "joustumine")
        if val:
            result["entry_into_force"] = parse_date(val)

    # Direct children of root or metaandmed for other fields
    search_root = metaandmed if metaandmed is not None else root

    # joustumisKuupaev — explicit entry into force date
    for el in search_root.iter():
        tag = ln(el.tag)
        if tag == "joustumisKuupaev" and el.text:
            parsed = parse_date(el.text.strip())
            if parsed:
                result["entry_into_force"] = parsed
                break

    # kehtetuKuupaev — date of invalidation
    for el in search_root.iter():
        tag = ln(el.tag)
        if tag == "kehtetuKuupaev" and el.text:
            parsed = parse_date(el.text.strip())
            if parsed:
                result["invalidation_date"] = parsed
                break

    # avaldamineKuupaev or avaldamismarge — publication date.
    # #571: the RT tag is "avaldamineKuupaev" (with an "ne"); the old
    # "avaldamiseKuupaev" matched NO RT XML, so this loop always fell through to
    # the year-only fallback and ~100% of records got a fabricated date.
    for el in root.iter():
        tag = ln(el.tag)
        if tag == "avaldamineKuupaev" and el.text:
            parsed = parse_date(el.text.strip())
            if parsed:
                result["publication_date"] = parsed
                break

    # #571: when only the RT YEAR is known (no avaldamineKuupaev), do NOT
    # fabricate a "{year}-01-01" publicationDate — a precise-looking xsd:date the
    # source never stated. Record the year at year precision (publication_year →
    # estleg:publicationYear, xsd:gYear) so consumers get the year without a
    # spurious day/month.
    if not result["publication_date"]:
        avaldamismarge = find_element_recursive(root, "avaldamismarge")
        if avaldamismarge is not None:
            rt_aasta = ct(avaldamismarge, "RTaasta")
            if rt_aasta:
                year = parse_rt_year(rt_aasta)
                if year:
                    result["publication_year"] = year

    # Last amendment date: find the latest muutmismarge.
    # Compare as ``date`` objects rather than as ISO strings — string
    # comparison silently mis-orders any non-ISO format that slips
    # through (DD.MM.YYYY etc.).
    muutmismarked = find_all_elements(root, "muutmismarge")
    latest_amendment: date | None = None
    for mm in muutmismarked:
        aktikp = ct(mm, "aktikuupaev")
        if aktikp:
            parsed_iso = parse_date(aktikp)
            if parsed_iso:
                parsed_date = _coerce_iso_date(parsed_iso)
                if latest_amendment is None or parsed_date > latest_amendment:
                    latest_amendment = parsed_date
    if latest_amendment:
        result["last_amendment_date"] = latest_amendment.isoformat()

    # muutmisKuupaev — explicit last amendment date field. Iterate ALL
    # occurrences (the XML may carry multiple historical amendment
    # markers) and keep the latest. Previously the loop bailed out
    # after the first hit which under-reported the latest amendment.
    current_latest = (
        _coerce_iso_date(result["last_amendment_date"])
        if result["last_amendment_date"]
        else None
    )
    for el in root.iter():
        tag = ln(el.tag)
        if tag == "muutmisKuupaev" and el.text:
            parsed_iso = parse_date(el.text.strip())
            if parsed_iso:
                parsed_date = _coerce_iso_date(parsed_iso)
                if current_latest is None or parsed_date > current_latest:
                    current_latest = parsed_date
    if current_latest is not None:
        result["last_amendment_date"] = current_latest.isoformat()

    # Use valid_from as entry_into_force fallback
    if not result["entry_into_force"] and result["valid_from"]:
        result["entry_into_force"] = result["valid_from"]

    return result


def validate_evaluation_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("evaluation date must be YYYY-MM-DD") from exc
    return value


def determine_temporal_status(temporal: dict, evaluation_date: str | None = None) -> str:
    """
    Determine the temporal status of a law.
    Returns one of: "inForce", "repealed", "notYetEffective", "unknown".

    "unknown" is returned when the temporal dict is empty or has no
    actionable date fields — previously this case fell through to
    "inForce" which mass-misclassified repealed/not-yet-effective laws
    that lacked XML metadata. ``date`` objects (not ISO strings) drive
    all comparisons to avoid lexicographic-ordering surprises.

    Cross-field sanity guard (#287): a repeal/invalidation/valid_until
    date that precedes the entry_into_force date is contradictory
    source data (a law cannot be repealed before it ever takes force).
    Rather than silently classifying such a law ``repealed`` we return
    ``unknown`` so the data-quality signal is preserved.
    """
    today_iso = evaluation_date or BUILD_EVALUATION_DATE
    today = _coerce_iso_date(today_iso)

    # Distinguish "no data at all" from "in force": when none of the
    # actionable date fields have a value, callers cannot conclude the
    # law is in force, so we emit ``unknown``.
    actionable_keys = (
        "invalidation_date",
        "valid_until",
        "entry_into_force",
        "valid_from",
    )
    if not any(temporal.get(k) for k in actionable_keys):
        return "unknown"

    # Cross-field sanity guard (#287): if any repeal/invalidation/
    # valid_until date is strictly before entry_into_force, the source
    # dates contradict each other. Classify ``unknown`` instead of
    # ``repealed`` (a law repealed before it ever took force is a data
    # error, not a genuinely repealed law).
    if temporal.get("entry_into_force"):
        entry = _coerce_iso_date(temporal["entry_into_force"])
        for end_key in ("invalidation_date", "valid_until"):
            if temporal.get(end_key):
                if _coerce_iso_date(temporal[end_key]) < entry:
                    return "unknown"

    # If there is an invalidation date or valid_until in the past, it is repealed
    if temporal.get("invalidation_date"):
        if _coerce_iso_date(temporal["invalidation_date"]) <= today:
            return "repealed"
    if temporal.get("valid_until"):
        if _coerce_iso_date(temporal["valid_until"]) <= today:
            return "repealed"

    # If entry_into_force is in the future, not yet effective
    if temporal.get("entry_into_force"):
        if _coerce_iso_date(temporal["entry_into_force"]) > today:
            return "notYetEffective"

    return "inForce"


def make_xsd_date(iso_date: str) -> dict:
    """Create an xsd:date typed value for JSON-LD."""
    return {"@value": iso_date, "@type": "xsd:date"}


TEMPORAL_KEYS_TO_CLEAR = [
    "estleg:entryIntoForce",
    "estleg:repealDate",
    "estleg:lastAmendmentDate",
    "estleg:publicationDate",
    "estleg:publicationYear",  # #571: clear on re-derive so a record that gains an
    # exact publicationDate (or loses publication metadata) can't keep a stale year.
    "estleg:temporalStatus",
    "estleg:adoptionDate",
]

# Act-level type markers. Temporal validity is an *act-level* property
# in this ontology (#128) — temporal triples are only ever written onto
# a node whose ``@type`` includes ``estleg:Act`` (covers laws, state
# regulations, and KOV regulations). Some non-law catalogues also carry
# ``owl:Ontology`` nodes, so owl:Ontology alone is not a safe target. If
# no act node can be located we skip enrichment for that file rather than
# falling back to ``graph[0]``.
ACT_TYPE_MARKERS = {"estleg:Act"}


def _node_types(node: dict) -> set[str]:
    types = node.get("@type", [])
    if isinstance(types, str):
        return {types}
    if isinstance(types, list):
        return {t for t in types if isinstance(t, str)}
    return set()


def is_act_level_node(node: dict) -> bool:
    """Return True if this node is a legitimate target for temporal props.

    True when the node is an ``estleg:Act`` (any subclass, since the
    KOV layer-1 enrichment stamps ``estleg:Act`` onto every act node).
    """
    types = _node_types(node)
    return bool(types & ACT_TYPE_MARKERS)


def find_act_node(graph: list[dict]) -> dict | None:
    """Return the act node a peep file's temporal triples belong on.

    Preference order: the act-root individual (#435), then any
    ``estleg:Act``-typed node. Returns ``None`` when neither exists — the
    caller must then skip enrichment for that file.
    """
    for node in graph:
        if is_domain_individual(node):
            return node
    for node in graph:
        if _node_types(node) & ACT_TYPE_MARKERS:
            return node
    return None


def clear_temporal_keys(graph: list[dict]) -> bool:
    """Strip every temporal property from every node in ``graph``.

    Returns True if anything was removed. Run during the enrichment
    pass so a fresh run re-derives temporal triples from scratch *and*
    so stale temporal props that an earlier ``graph[0]`` fallback wrote
    onto non-Act nodes (e.g. ``estleg:LegalConcept`` in
    ``tsiviilseadustik_osa6/osa7``) are removed. The act node is
    re-populated below; non-Act nodes are simply left clean.
    """
    removed = False
    for node in graph:
        if not isinstance(node, dict):
            continue
        for key in TEMPORAL_KEYS_TO_CLEAR:
            if key in node:
                del node[key]
                removed = True
    return removed


# INDEX.json date keys we know how to map into our internal temporal
# dict. Both top-level and ``kehtivus``-block forms are recognised.
_INDEX_DATE_KEY_MAP: dict[str, str] = {
    "joustumine": "entry_into_force",
    "joustumiseKuupaev": "entry_into_force",
    "kehtivAlates": "valid_from",
    "kehtivuseAlgus": "valid_from",
    "kehtivKuni": "valid_until",
    "kehtivuseLopp": "valid_until",
    "kehtetuKuupaev": "invalidation_date",
    "kehtetuksTunnistamiseKuupaev": "invalidation_date",
    "muutmisKuupaev": "last_amendment_date",
    "viimaneMuutmine": "last_amendment_date",
    "avaldamineKuupaev": "publication_date",  # #571: real RT tag has "ne", not "se"
    "vastuvotmiseKuupaev": "adoption_date",
    "aktikuupaev": "adoption_date",
}


def _index_to_temporal(idx_data: dict) -> dict:
    """Map INDEX.json fields (top-level or ``kehtivus`` block) onto
    the internal temporal dict shape.

    Skips fields we cannot parse and returns an all-None scaffold when
    no actionable dates are present — callers must check before
    feeding it to ``determine_temporal_status``.
    """
    result: dict[str, str | None] = {
        "entry_into_force": None,
        "valid_from": None,
        "valid_until": None,
        "invalidation_date": None,
        "last_amendment_date": None,
        "publication_date": None,
        "adoption_date": None,
    }

    def _ingest(source: dict) -> None:
        for src_key, dst_key in _INDEX_DATE_KEY_MAP.items():
            raw = source.get(src_key)
            if not raw or result.get(dst_key):
                continue
            parsed = parse_date(str(raw))
            if parsed:
                result[dst_key] = parsed

    _ingest(idx_data)
    kehtivus = idx_data.get("kehtivus")
    if isinstance(kehtivus, dict):
        _ingest(kehtivus)
    return result


def load_index_metadata() -> dict[str, dict]:
    """
    Load INDEX.json for fallback metadata.
    Returns dict keyed by law slug with any available kehtivus data.
    """
    index_path = KRR_DIR / "INDEX.json"
    if not index_path.exists():
        return {}
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    result = {}
    for law in data.get("laws", []):
        name = law.get("name", "")
        if name:
            result[name] = law
    return result


def main(evaluation_date: str | None = None):
    evaluation_date = evaluation_date or BUILD_EVALUATION_DATE

    print("=" * 70)
    print("Estonian Legal Ontology - Extract Temporal Validity Data")
    print("=" * 70)
    print(f"Evaluation date: {evaluation_date}")

    _start = time.perf_counter()

    # Step 1: Build the globalId → XML path index (recursive — covers
    # laws at the root, state regs under maarus/, and KOV under
    # maarus_kov/).
    print("\n[1/4] Indexing XML files by globalId...")
    xml_lookup = build_globalid_xml_lookup(DATA_DIR)
    print(f"  Indexed {len(xml_lookup)} XML files")

    # Step 2: Per-peep XML pairing + temporal extraction. We pair each
    # peep file individually rather than pre-extracting all XML to a
    # dict, so the slug fallback for laws-without-globalId fires per
    # file. Result is still keyed by peep filename stem (without the
    # _peep suffix) so step 4's enrichment logic doesn't change.
    #
    # Multi-part laws (asjaoigusseadus_osa1, _osa2, ...) all pair to
    # the same base XML file, so we cache temporal extraction by XML
    # path to avoid re-parsing AND to keep the unique-XML count honest
    # for the report (the existing report fields total_xml_files /
    # xml_with_temporal_data are XML-oriented, not peep-oriented).
    print("\n[2/4] Extracting temporal metadata per peep file...")
    temporal_by_slug: dict[str, dict] = {}
    temporal_by_xml: dict[Path, dict] = {}
    unique_xml_paths: set[Path] = set()
    extracted_xml = 0  # unique XML files that yielded ≥1 date
    parse_errors = 0

    # Coverage counters (initialised here so step 2's _unresolved
    # counter is visible to the per-peep loop below).
    _files_processed = 0
    _files_processed_kov = 0
    _files_with_output = 0
    _files_with_output_kov = 0
    _triples = 0
    _triples_kov = 0
    _fallback_hits = 0
    _unresolved = 0
    _failures: list[str] = []
    _skip_reasons: dict[str, int] = {}

    all_peep_files = iter_peep_files()
    _kov_files = [f for f in all_peep_files if "regulations/kov" in str(f)]

    # Precompute set of canonical-pairing paths for O(1) fallback
    # detection. Mirrors the pattern in generate_amendment_history —
    # avoids a per-peep O(N) scan over xml_lookup.values().
    _lookup_paths = {str(p) for p in xml_lookup.values()}

    for peep in all_peep_files:
        xml_path = pair_peep_with_xml(peep, xml_lookup, data_dir=DATA_DIR)
        if xml_path is None:
            _unresolved += 1
            continue
        slug = peep.stem.replace("_peep", "")

        # Track slug-fallback hits (laws-without-globalId path):
        # detected when the XML returned is not in the globalId
        # lookup's values.
        if str(xml_path) not in _lookup_paths:
            _fallback_hits += 1

        # Reuse cached extraction if this XML was already parsed
        # (multi-part laws share their base XML).
        if xml_path in temporal_by_xml:
            temporal_by_slug[slug] = temporal_by_xml[xml_path]
            continue

        try:
            temporal = extract_temporal_from_xml(xml_path)
            unique_xml_paths.add(xml_path)
            has_data = any(v is not None for v in temporal.values())
            if has_data:
                extracted_xml += 1
            temporal_by_xml[xml_path] = temporal
            temporal_by_slug[slug] = temporal
        except Exception as e:
            parse_errors += 1
            _failures.append(f"{xml_path.name}: {e!r}")
            print(f"  ERROR parsing {xml_path.name}: {e}")

    print(f"  Extracted temporal data from {extracted_xml} unique XML files "
          f"(across {len(temporal_by_slug)} peep files; {parse_errors} errors)")

    # Step 3: Load INDEX.json for fallback data
    print("\n[3/4] Loading INDEX.json for fallback metadata...")
    index_meta = load_index_metadata()
    print(f"  Loaded metadata for {len(index_meta)} laws from INDEX.json")

    # Step 4: Enrich law JSON-LD files (single pass — clear stale
    # keys + write fresh values + save once per file).
    print("\n[4/4] Enriching law JSON-LD files with temporal properties...")
    law_files = iter_peep_files()
    print(f"  Found {len(law_files)} law JSON-LD files")

    enriched = 0
    skipped = 0
    # Files where no act / owl:Ontology node could be located. We refuse
    # to fall back to graph[0] (#128) — those files are skipped and the
    # counter surfaces them in the report so a regression is visible.
    no_act_node = 0
    status_counts = {"inForce": 0, "repealed": 0, "notYetEffective": 0, "unknown": 0}
    report_entries: list[dict] = []

    # Merged single-pass (clear + enrich): the prior implementation
    # opened/saved each file twice — once to clear stale temporal keys
    # and again to re-enrich. Now we load the doc once, mutate
    # in-memory using a ``dirty`` flag, and save at most once at the
    # end of each file's iteration.
    for law_file in law_files:
        is_kov = "regulations/kov" in str(law_file)
        # Derive the slug: remove _peep.json, also handle _osa variants
        stem = law_file.stem.replace("_peep", "")

        # Try to find matching XML slug. With the new flow,
        # temporal_by_slug is keyed by peep.stem.replace("_peep", "")
        # — exactly what `stem` computes here. The base_slug fallback
        # remains for safety against unexpected key shapes.
        xml_slug = stem
        # Remove osa suffix for lookup
        base_slug = re.sub(r"_osa\d+$", "", stem)

        temporal = temporal_by_slug.get(xml_slug) or temporal_by_slug.get(base_slug)

        # INDEX.json fallback. Previously, an empty `idx_data` caused
        # us to fabricate an all-None temporal dict, which then drove
        # ``determine_temporal_status`` into the "inForce" default —
        # mass-misclassifying repealed/not-yet-effective laws.
        # Now we map idx_data.kehtivus block fields (when present)
        # into the temporal dict; if no real dates can be derived,
        # we leave temporal as-is so downstream classification yields
        # ``unknown`` rather than ``inForce``.
        if temporal is None or not any(v for v in temporal.values()):
            idx_data = index_meta.get(stem) or index_meta.get(base_slug)
            if idx_data:
                merged = _index_to_temporal(idx_data)
                if temporal is None:
                    temporal = merged
                else:
                    # Fill in any blanks the XML left empty.
                    for k, v in merged.items():
                        if v and not temporal.get(k):
                            temporal[k] = v

        # Load JSON-LD and clear stale temporal keys in a single pass.
        try:
            with open(law_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ERROR reading {law_file.name}: {e}")
            skipped += 1
            continue

        graph = doc.get("@graph", [])
        if not graph:
            skipped += 1
            continue

        # Ensure context has dcterms (in-memory mutation, marked dirty
        # only if it was actually missing).
        dirty = False
        ctx = doc.get("@context", {})
        if isinstance(ctx, dict) and "dcterms" not in ctx:
            ctx["dcterms"] = "http://purl.org/dc/terms/"
            doc["@context"] = ctx
            dirty = True

        # Clear stale temporal keys from EVERY node — this re-derives a
        # clean slate for the act node and, crucially, scrubs temporal
        # props that an old graph[0] fallback wrote onto non-Act nodes
        # (e.g. estleg:LegalConcept in tsiviilseadustik_osa6/osa7).
        if clear_temporal_keys(graph):
            dirty = True

        # Locate the act-level node. No graph[0] fallback (#128): if the
        # file carries neither an owl:Ontology node nor an estleg:Act
        # node we skip enrichment entirely and bump a counter so the
        # regression is visible in the report.
        ontology_node = find_act_node(graph)
        if ontology_node is None:
            if dirty:
                save_json(law_file, doc)
            no_act_node += 1
            skipped += 1
            status_counts["unknown"] += 1
            _skip_reasons["no_act_node"] = (
                _skip_reasons.get("no_act_node", 0) + 1
            )
            report_entries.append({
                "file": law_file.name,
                "slug": stem,
                "status": "no_act_node",
                "temporal": {},
            })
            continue

        # If we have no temporal data at all, persist any clears, log
        # a skip, and continue.
        if temporal is None:
            if dirty:
                save_json(law_file, doc)
            skipped += 1
            status_counts["unknown"] += 1
            _skip_reasons["no_xml_data"] = (
                _skip_reasons.get("no_xml_data", 0) + 1
            )
            report_entries.append({
                "file": law_file.name,
                "slug": stem,
                "status": "no_xml_data",
                "temporal": {},
            })
            continue

        _files_processed += 1
        if is_kov:
            _files_processed_kov += 1

        # Determine temporal status
        status = determine_temporal_status(temporal, evaluation_date=evaluation_date)
        status_counts[status] += 1

        # Add temporal properties to the ontology node
        # Count date triples written for this file (entryIntoForce,
        # repealDate, lastAmendmentDate, publicationDate) plus the
        # always-written temporalStatus.
        _file_triples = 0

        if temporal.get("entry_into_force"):
            ontology_node["estleg:entryIntoForce"] = make_xsd_date(temporal["entry_into_force"])
            dirty = True
            _file_triples += 1

        # repealDate semantics (#287): emit whenever a repeal/
        # invalidation/valid_until date is present, regardless of
        # whether it is in the past or scheduled for the future — a
        # scheduled future repeal is informative. The two date sources
        # are now treated symmetrically (previously ``valid_until`` only
        # wrote ``repealDate`` when ``status == "repealed"`` while
        # ``invalidation_date`` wrote it unconditionally, so the field's
        # meaning differed by source). ``invalidation_date`` takes
        # precedence when both are present (it is the more specific
        # field), mirroring the prior precedence.
        repeal_date = temporal.get("invalidation_date") or temporal.get("valid_until")
        if repeal_date:
            ontology_node["estleg:repealDate"] = make_xsd_date(repeal_date)
            dirty = True
            _file_triples += 1

        if temporal.get("last_amendment_date"):
            ontology_node["estleg:lastAmendmentDate"] = make_xsd_date(temporal["last_amendment_date"])
            dirty = True
            _file_triples += 1

        if temporal.get("publication_date"):
            ontology_node["estleg:publicationDate"] = make_xsd_date(temporal["publication_date"])
            dirty = True
            _file_triples += 1
        elif temporal.get("publication_year"):
            # #571: year precision only (no avaldamineKuupaev) — emit
            # estleg:publicationYear (xsd:gYear) instead of a fabricated date.
            ontology_node["estleg:publicationYear"] = {
                "@value": str(temporal["publication_year"]),
                "@type": "xsd:gYear",
            }
            dirty = True
            _file_triples += 1

        ontology_node["estleg:temporalStatus"] = status
        dirty = True
        _file_triples += 1  # temporalStatus is always written

        if dirty:
            save_json(law_file, doc)
            enriched += 1
            _triples += _file_triples
            if is_kov:
                _triples_kov += _file_triples
            if _file_triples > 0:
                _files_with_output += 1
                if is_kov:
                    _files_with_output_kov += 1

        report_entries.append({
            "file": law_file.name,
            "slug": stem,
            "status": status,
            "temporal": {k: v for k, v in temporal.items() if v is not None},
        })

    # Generate report
    print("\n  Generating temporal_data_report.json...")
    # Determinism (#295): the report body must NOT embed a wall-clock
    # ``generated`` timestamp (``date.today()``) — that re-diffs the
    # git-tracked report on every run regardless of whether the
    # underlying data changed (timestamp-only churn). The declared
    # ``evaluationDate`` below already records the date this run was
    # evaluated against and is deterministic when the caller pins it.
    report = {
        "evaluationDate": evaluation_date,
        "summary": {
            "total_xml_files": len(unique_xml_paths),
            "xml_with_temporal_data": extracted_xml,
            "total_peep_files_paired": len(temporal_by_slug),
            "xml_parse_errors": parse_errors,
            "total_law_files": len(law_files),
            "enriched": enriched,
            "skipped_no_data": skipped,
            "skipped_no_act_node": no_act_node,
            "status_counts": status_counts,
        },
        "laws": report_entries,
    }
    report_path = KRR_DIR / "reports" / "temporal_data_report.json"
    save_json(report_path, report)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Unique XML files paired: {len(unique_xml_paths)}")
    print(f"  With temporal data:    {extracted_xml}")
    print(f"  Peep files paired:     {len(temporal_by_slug)}")
    print(f"  Law files enriched:    {enriched}")
    print(f"  Skipped (no data):     {skipped}")
    print(f"  Skipped (no act node): {no_act_node}")
    print("  Status breakdown:")
    for status, count in status_counts.items():
        print(f"    {status}: {count}")
    print(f"\n  Report: {report_path.relative_to(REPO_ROOT)}")
    print("=" * 70)

    # ---------- coverage report ----------
    _wall, _rate, _peak_mb = measure_runtime(_start, _files_processed)
    # Determinism (#295): pin the coverage report's ``run_timestamp``
    # to the declared evaluation date (at midnight UTC) instead of the
    # wall-clock ``datetime.now()`` so the timestamp field stops
    # contributing wall-clock churn. ``validate_run_timestamp`` requires
    # a UTC ISO-8601 string, which this satisfies.
    _run_timestamp = f"{evaluation_date}T00:00:00+00:00"
    write_coverage_report(
        CoverageReport(
            pipeline="extract_temporal_data",
            run_timestamp=_run_timestamp,
            pipeline_version=resolve_pipeline_version(),
            input_files_total=len(all_peep_files),
            input_files_kov=len(_kov_files),
            files_processed=_files_processed,
            files_processed_kov=_files_processed_kov,
            files_with_output=_files_with_output,
            files_with_output_kov=_files_with_output_kov,
            files_skipped=skipped,
            skip_reasons=_skip_reasons,
            triples_emitted=_triples,
            triples_emitted_kov=_triples_kov,
            fallback_hits=_fallback_hits,
            unresolved_references=_unresolved,
            wall_time_seconds=round(_wall, 2),
            items_per_second=round(_rate, 2),
            peak_memory_mb=round(_peak_mb, 1),
            error_count=len(_failures),
            failure_samples=_failures,
        ),
        REPO_ROOT / "krr_outputs" / "reports" / "kov"
        / "extract_temporal_data_coverage.json",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evaluation-date",
        type=validate_evaluation_date,
        default=None,
        help=(
            "Date used for temporalStatus evaluation (YYYY-MM-DD). "
            f"Defaults to the pinned BUILD_EVALUATION_DATE ({BUILD_EVALUATION_DATE})."
        ),
    )
    args = parser.parse_args()
    main(evaluation_date=args.evaluation_date)
