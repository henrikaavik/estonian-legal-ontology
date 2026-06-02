#!/usr/bin/env python3
"""
Extract cross-law references from Estonian law XML and JSON-LD files.

This script:
1. Builds a mapping of law abbreviations and full names to ontology provision IRIs
2. Parses XML source files and JSON-LD summary text for Estonian legal citation patterns
3. Resolves citations to existing provision IRIs
4. Adds estleg:references IRI links to provision nodes in each JSON-LD file
5. Generates cross_references_report.json with statistics

Citation patterns detected:
  - KarS § 121            (abbreviation + paragraph)
  - KarS § 121 lg 2       (with subsection)
  - KarS § 121 lg 2 p 3   (with point)
  - VÕS §-de 208-210      (paragraph range)
  - käesoleva seaduse § 12 (self-reference)
  - tsiviilseadustiku üldosa seaduse § 67 (full name reference)
"""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from estleg_common import (
    BUILD_EVALUATION_DATE,
    FULLNAME_GENITIVE,
    KNOWN_ABBREVIATIONS,
    PAR_SUFFIX,
    iter_peep_files,
    jsonld_text,
    save_json,
)
from kov_pipeline_coverage import (
    CoverageReport,
    PINNED_RUN_TIMESTAMP,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
DATA_DIR = REPO_ROOT / "data" / "riigiteataja"

NS = "https://data.riik.ee/ontology/estleg#"

# Full law name -> abbreviation (reverse mapping)
FULLNAME_TO_ABBREV = {v.lower(): k for k, v in KNOWN_ABBREVIATIONS.items()}


def ln(tag: str) -> str:
    """Extract local name from a possibly namespaced XML tag."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def is_provision_node(node: dict) -> bool:
    """Identify provision nodes by their ``_Par_`` @id segment.

    The whole script keys provisions on this convention — every JSON-LD
    provision in the corpus has an @id of shape ``estleg:<prefix>_Par_<n>``.
    Centralising the predicate avoids the historical drift where
    different passes used different heuristics (e.g.
    ``'estleg:paragrahv' not in node`` vs ``'_Par_' in @id``) and could
    disagree on which nodes count as provisions.
    """
    if not isinstance(node, dict):
        return False
    node_id = node.get("@id", "")
    return isinstance(node_id, str) and "_Par_" in node_id


# ----------------------------------------------------------------------
# Act-IRI ↔ prefix derivation (Layer 2b)
#
# The corpus uses three @id shapes for act nodes:
#   estleg:<prefix>_Map_<year>            (most laws)
#   estleg:<prefix>_Osa<N>[_descriptor]   (multi-part laws)
#   estleg:<prefix>                       (rare legacy with no suffix)
#
# A naive ``shortid.split("_")[0]`` is wrong for 34 corpus acts whose
# prefix itself contains an underscore (e.g. KARIST_2_Map_2026 →
# provisions live at estleg:KARIST_2_Par_<n>, NOT estleg:KARIST_Par_<n>).
# ``_prefix_from_act_iri`` strips known suffix patterns so the leading
# prefix is recovered correctly.
# ----------------------------------------------------------------------

_ACT_SUFFIX_PATTERNS = [
    re.compile(r"_Map_\d{4}$"),                            # _Map_2026
    re.compile(r"_Osa\d+(?:_.+)?$"),                       # _Osa1, _Osa5_AsjaoigusteKaitse
    re.compile(r"_(?:Procedure|Substantive)Map_\d{4}$"),   # KrMS variants
]


def _prefix_from_act_iri(act_iri: str) -> str | None:
    """Derive the provision-keying prefix from a full act @id.

    The prefix is the leading segment that all of the act's provisions
    share (e.g. 'KOKS' for 'estleg:KOKS_Map_2026' whose provisions look
    like 'estleg:KOKS_Par_22'; 'KARIST_2' for 'estleg:KARIST_2_Osa1_1_87'
    whose provisions look like 'estleg:KARIST_2_Par_1').

    Used as a fallback when build_provision_index encounters a peep
    that has an owl:Ontology act node but no provisions to ground-truth
    the prefix (a rare edge case for legacy or stub files).
    """
    if not act_iri.startswith("estleg:"):
        return None
    short = act_iri[len("estleg:"):]
    for pat in _ACT_SUFFIX_PATTERNS:
        m = pat.search(short)
        if m:
            return short[: m.start()]
    return short  # legacy: no suffix


def build_provision_index() -> tuple[
    dict[str, dict[str, str]],
    dict[str, list[str]],
    dict[str, Path],
    dict[str, str],   # prefix → act @id
    dict[str, str],   # act @id → prefix (reverse, for resolver use)
]:
    """
    Scan all *_peep.json files to build:
    1. prefix_to_provisions: {prefix: {par_number: full_iri}} e.g. {"Karistusseadustik": {"121": "estleg:KARIST_2_Par_121"}}
    2. source_act_to_prefix: {source_act_name: [prefix, ...]} e.g.
       {"Asjaõigusseadus": ["AOS_Osa1", ..., "AOS_Osa8"]}. A multi-osa
       law shares one ``estleg:sourceAct`` across many osa peep files,
       each with its OWN provision prefix; the value is therefore a
       LIST so every osa's provisions stay reachable. A plain dict here
       was last-writer-wins and silently dropped every osa but the
       alphabetically-last one (#299).
    3. iri_to_file: {iri: filepath} mapping each provision IRI to its containing file
    4. prefix_to_act_iri: {prefix: act_@id} e.g. {"KOKS": "estleg:KOKS_Map_2026"}
    5. act_iri_to_prefix: {act_@id: prefix} (reverse — required by Task 5
       resolver because ``act_iri.split("_")[0]`` breaks for 34 corpus
       acts with multi-segment prefixes like ``KARIST_2_Map_2026``).
    """
    prefix_to_provisions: dict[str, dict[str, str]] = {}
    source_act_to_prefix: dict[str, list[str]] = {}
    iri_to_file: dict[str, Path] = {}
    prefix_to_act_iri: dict[str, str] = {}
    act_iri_to_prefix: dict[str, str] = {}

    # Scan all JSON-LD law files (not riigikohus, not schema/index files)
    for json_file in iter_peep_files():
        if json_file.name.startswith("riigikohus"):
            continue
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        graph = doc.get("@graph", [])
        if not graph:
            continue

        # Track the prefixes actually used in this file. ``file_prefix``
        # (the FIRST provision's prefix) only seeds the sourceAct map;
        # every provision is keyed under its OWN prefix so files that
        # mix prefix families (e.g. erigi_seadus_peep.json: ERIIGI_DI /
        # ERIIGI_ES / ERIIGI_DE) don't route non-first-prefix paragraphs
        # to the wrong-law IRI (#312).
        file_prefix = None
        file_prefixes: list[str] = []
        seen_prefixes: set[str] = set()
        source_act = None
        for node in graph:
            node_id = node.get("@id", "")
            if "_Par_" in node_id and node_id.startswith("estleg:"):
                # Extract prefix: "estleg:KARIST_2_Par_121" -> "KARIST_2"
                local = node_id[len("estleg:"):]
                prefix_part = local.split("_Par_")[0]
                if file_prefix is None:
                    file_prefix = prefix_part
                if prefix_part not in seen_prefixes:
                    seen_prefixes.add(prefix_part)
                    file_prefixes.append(prefix_part)
                # Extract paragraph number
                par_part = local.split("_Par_")[1]
                # Store the provision under ITS OWN prefix (#312).
                if prefix_part not in prefix_to_provisions:
                    prefix_to_provisions[prefix_part] = {}
                prefix_to_provisions[prefix_part][par_part] = node_id
                iri_to_file[node_id] = json_file

                # Get sourceAct — generators may emit it as a plain string
                # or a {"@value": ..., "@language": "et"} object; normalise
                # before using it as a dict key.
                if source_act is None:
                    source_act = jsonld_text(node.get("estleg:sourceAct", ""))

        if source_act and file_prefixes:
            # Accumulate every prefix this sourceAct contributes across
            # all its osa files (#299). De-dup so re-scanning a file (or
            # a file mixing prefixes) doesn't add a prefix twice.
            bucket = source_act_to_prefix.setdefault(source_act, [])
            for prefix_part in file_prefixes:
                if prefix_part not in bucket:
                    bucket.append(prefix_part)

        # Layer 2b: bind act_iri ↔ prefix maps. Reuse file_prefix when
        # available (provisions are the ground truth for prefix
        # grouping), fall back to _prefix_from_act_iri for peeps with
        # an act node but no provisions. Multi-part laws (KARIST_2_Osa1
        # / KARIST_2_Osa2) share the same prefix; setdefault keeps the
        # FIRST act_iri under that prefix and the reverse map keys
        # every act_iri individually.
        for node in graph:
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "owl:Ontology" not in types:
                continue
            act_iri = node.get("@id")
            if not act_iri:
                continue
            prefix = file_prefix or _prefix_from_act_iri(act_iri)
            if not prefix:
                continue
            prefix_to_act_iri.setdefault(prefix, act_iri)
            act_iri_to_prefix[act_iri] = prefix
            break

    # Also scan riigikohus subdirectory files (but those don't have provisions)
    for json_file in sorted((KRR_DIR / "riigikohus").glob("*_peep.json")):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for node in doc.get("@graph", []):
            node_id = node.get("@id", "")
            if "_Par_" in node_id:
                iri_to_file[node_id] = json_file

    return (prefix_to_provisions, source_act_to_prefix, iri_to_file,
            prefix_to_act_iri, act_iri_to_prefix)


def build_abbreviation_to_prefix(
    source_act_to_prefix: dict[str, list[str]],
) -> dict[str, list[str]]:
    """
    Map law abbreviations (KarS, VÕS, etc.) to the prefixes used in
    provision IRIs.

    Returns: {abbreviation: [iri_prefix, ...]} e.g.
    {"KarS": ["KARIST_2"], "AÕS": ["AOS_Osa1", ..., "AOS_Osa8"]}.

    The value is a LIST because a multi-osa law (one ``estleg:sourceAct``
    spread across many osa peep files) contributes several provision
    prefixes; ``resolve_citation`` searches all of them so §§ in every
    osa stay reachable (#299).
    """
    abbrev_to_prefix: dict[str, list[str]] = {}

    for abbrev, full_name in KNOWN_ABBREVIATIONS.items():
        # _iter_prefixes copies/normalises so callers can't mutate the
        # shared sourceAct bucket and a stray string value is tolerated.
        prefixes = _iter_prefixes(source_act_to_prefix.get(full_name))
        if prefixes:
            abbrev_to_prefix[abbrev] = prefixes

    return abbrev_to_prefix


# ----------------------------------------------------------------------
# Canonical lookups (Layer 2b)
#
# These translate the parser's output (genitive law name + (issuer,
# date, act_number) tuples) into real corpus IRIs. The lookups are
# built once per pipeline run and consulted by the Task 5/6 resolver.
# ----------------------------------------------------------------------

_ESTONIAN_TO_ASCII = str.maketrans({
    "õ": "o", "ä": "a", "ö": "o", "ü": "u",
    "Õ": "O", "Ä": "A", "Ö": "O", "Ü": "U",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
})


def _normalize_issuer_label(label: str) -> str:
    """Normalise an issuer label so registry-side ASCII forms
    ('Polva Vallavalitsus') and act-side canonical forms ('Põlva
    Vallavalitsus') hash to the same key.

    Applied SYMMETRICALLY: both lookup-construction and lookup-query
    sides must call this function before keying the dict. The
    transformation strips Estonian diacritics, collapses whitespace,
    and lowercases the result.
    """
    if not label:
        return ""
    s = label.strip().translate(_ESTONIAN_TO_ASCII)
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def _iter_prefixes(value: list[str] | str | None) -> list[str]:
    """Normalise a ``source_act_to_prefix`` value into a list of prefixes.

    ``source_act_to_prefix`` is list-valued since #299 (a multi-osa law
    contributes several prefixes), but a bare string is tolerated for
    older callers / tests that still pass the pre-#299 single-prefix
    shape. ``None`` / empty yields ``[]``.
    """
    if not value:
        return []
    return [value] if isinstance(value, str) else list(value)


def build_abbreviation_mapping_report(
    abbrev_to_prefix: "dict[str, list[str] | str]",
    prefix_to_provisions: "dict[str, dict[str, str]]",
) -> "dict[str, dict[str, object]]":
    """Build the ``abbreviation_mapping`` section of cross_references_report.json.

    ``abbrev_to_prefix`` is list-valued since #299 (a multi-osa law maps to
    several provision prefixes), so every value is normalised through
    ``_iter_prefixes`` and the per-abbrev provision count is summed across all
    of its prefixes. The value is NEVER used as a ``prefix_to_provisions`` key
    directly — doing so raised ``TypeError: unhashable type: 'list'``. Output is
    sorted for deterministic report content.
    """
    return {
        abbrev: {
            "iri_prefixes": sorted(_iter_prefixes(prefixes)),
            "provision_count": sum(
                len(prefix_to_provisions.get(p, {}))
                for p in _iter_prefixes(prefixes)
            ),
        }
        for abbrev, prefixes in sorted(abbrev_to_prefix.items())
    }


# Estonian law-marker words in their genitive form mapped back to the
# nominative form that appears in a corpus title. Only the FINAL
# law-marker word changes case in an act title — the leading
# attributive words ('kaitseliidu', 'avaliku teabe') are already stored
# in their citation form. Used to fold a preamble genitive law_ref
# ('kaitseliidu seaduse') onto the corpus title key ('kaitseliidu
# seadus') for the #390 corpus-title fallback.
_GENITIVE_MARKER_TO_NOMINATIVE: tuple[tuple[str, str], ...] = (
    ("seadustiku", "seadustik"),
    ("seaduse", "seadus"),
    ("koodeksi", "koodeks"),
)


def _genitive_law_ref_to_title(genitive: str) -> str | None:
    """Fold a genitive law name onto its nominative corpus-title form.

    ``'kaitseliidu seaduse'`` → ``'kaitseliidu seadus'``;
    ``'karistusseadustiku'`` → ``'karistusseadustik'``. Returns ``None``
    when the trailing word is not a recognised law marker (so the caller
    skips the fallback rather than emitting a garbage key).
    """
    g = genitive.strip().lower()
    for gen_marker, nom_marker in _GENITIVE_MARKER_TO_NOMINATIVE:
        if g.endswith(gen_marker):
            return g[: -len(gen_marker)] + nom_marker
    return None


def build_law_title_to_iri(
    source_act_to_prefix: dict[str, list[str]],
    prefix_to_act_iri: dict[str, str],
) -> dict[str, str]:
    """Build a {normalized_nominative_law_title → act @id} lookup.

    The corpus stamps every provision's ``estleg:sourceAct`` with the
    law's nominative title (e.g. ``"Kaitseliidu seadus"``), and
    ``build_provision_index`` already aggregates those into
    ``source_act_to_prefix``. Lower-casing each sourceAct yields a
    direct title→act_iri map that resolves ~236 more law genitives than
    the 40-entry ``FULLNAME_GENITIVE`` chain alone (#390).

    The act_iri for a multi-osa law is taken from the FIRST of its
    prefixes that ``prefix_to_act_iri`` knows — issuedUnder is act-level,
    so any one of the law's act IRIs is an acceptable enabling-act target.
    """
    out: dict[str, str] = {}
    for source_act, prefixes in source_act_to_prefix.items():
        act_iri = None
        for prefix in _iter_prefixes(prefixes):
            act_iri = prefix_to_act_iri.get(prefix)
            if act_iri is not None:
                break
        if act_iri is None:
            continue
        out[source_act.strip().lower()] = act_iri
    return out


def build_genitive_to_act_iri(
    source_act_to_prefix: dict[str, list[str]],
    prefix_to_act_iri: dict[str, str],
) -> dict[str, str]:
    """Build a {genitive_law_name (lowercased) → act @id} lookup by
    chaining FULLNAME_GENITIVE → abbrev → source_act_to_prefix →
    prefix_to_act_iri.

    The returned keys are lowercased so the parser's case-insensitive
    output ('Kohaliku omavalitsuse korralduse seaduse' or 'kohaliku
    omavalitsuse korralduse seaduse') resolves uniformly via .lower().

    ``source_act_to_prefix`` values are LISTS of prefixes (multi-osa
    laws contribute several); the act_iri is taken from the first
    prefix ``prefix_to_act_iri`` knows. Entries that fail at any link
    in the chain are silently skipped.
    """
    out: dict[str, str] = {}
    for genitive, abbrev in FULLNAME_GENITIVE.items():
        full_name = KNOWN_ABBREVIATIONS.get(abbrev)
        if full_name is None:
            continue
        prefixes = _iter_prefixes(source_act_to_prefix.get(full_name))
        if not prefixes:
            continue
        act_iri = None
        for prefix in prefixes:
            act_iri = prefix_to_act_iri.get(prefix)
            if act_iri is not None:
                break
        if act_iri is None:
            continue
        out[genitive.lower()] = act_iri
    return out


def _read_adoption_date_from_xml(xml_path: Path) -> str | None:
    """Read <vastuvoetud><aktikuupaev> from a Riigi Teataja XML.
    Returns the ISO-8601 date string or None when absent or unreadable.

    Both XML parse errors AND filesystem errors (missing file, EACCES,
    EIO mid-read) return None so the caller's lookup-build loop can
    skip the entry without crashing the whole pipeline.
    """
    try:
        tree = ET.parse(str(xml_path))
    except (ET.ParseError, OSError):
        return None
    for node in tree.iter():
        # Tag local-name match (ns-aware)
        if node.tag.endswith("vastuvoetud"):
            for child in node:
                if child.tag.endswith("aktikuupaev"):
                    if child.text:
                        return child.text.strip()
    return None


def build_state_regulation_lookup(
    riik_root: Path,
    data_dir: Path,
) -> dict[tuple[str, str, str], str]:
    """Build (normalized_issuer, adoption_date, act_number) → act @id
    for state regulations under riik_root.

    adoption_date is read from each paired XML at
    <vastuvoetud><aktikuupaev>; the corpus has zero estleg:adoptionDate
    fields. Issuer is normalised via _normalize_issuer_label for
    symmetry with the resolver query side (Task 5).
    """
    from estleg_common import build_globalid_xml_lookup, pair_peep_with_xml

    lookup: dict[tuple[str, str, str], str] = {}
    if not riik_root.is_dir():
        return lookup

    xml_lookup = build_globalid_xml_lookup(data_dir)

    for peep in sorted(riik_root.rglob("*_peep.json")):
        try:
            with open(peep, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        # Find the act node — filter to the regulation type set.
        act_node = None
        for node in doc.get("@graph", []):
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if any(t in {"estleg:NationalRegulation",
                          "estleg:GovernmentRegulation",
                          "estleg:MinisterialRegulation"} for t in types):
                act_node = node
                break
        if act_node is None:
            continue

        issuer = act_node.get("estleg:issuer")
        act_num = act_node.get("estleg:actNumber")
        aid = act_node.get("@id")
        if not (issuer and act_num and aid):
            continue

        xml_path = pair_peep_with_xml(peep, xml_lookup, data_dir=data_dir)
        if xml_path is None:
            continue
        adoption_date = _read_adoption_date_from_xml(xml_path)
        if adoption_date is None:
            continue

        # Normalise issuer at construction time so resolver-side
        # queries (which also normalise) hit the same key.
        key = (_normalize_issuer_label(issuer),
               adoption_date,
               str(act_num))
        lookup[key] = aid
    return lookup


def build_kov_act_lookup(
    kov_root: Path,
    data_dir: Path,
    issuers_path: Path,
) -> dict[tuple[str, str, str], str]:
    """Build (normalized_issuer, adoption_date, act_number) → act @id
    for KOV regulations.

    The issuer key is taken from estleg:issuer on the act node and
    passed through _normalize_issuer_label so that registry-side
    ASCII labels ('Polva Vallavalitsus') and act-side canonical
    labels with diacritics ('Põlva Vallavalitsus') hash to the same
    key. issuers_path is consulted only as a sanity validation;
    mismatches are tolerated to absorb Layer 1's slug-derived label
    quirks. adoption_date is read from paired XML.
    """
    from estleg_common import build_globalid_xml_lookup, pair_peep_with_xml

    lookup: dict[tuple[str, str, str], str] = {}
    if not kov_root.is_dir():
        return lookup

    xml_lookup = build_globalid_xml_lookup(data_dir)

    # Load issuer registry for sanity reference (does not gate the
    # build — diacritic mismatches are tolerated).
    valid_issuer_labels: set[str] = set()
    if issuers_path.exists():
        try:
            with open(issuers_path, "r", encoding="utf-8") as fh:
                idoc = json.load(fh)
            for n in idoc.get("@graph", []):
                types = n.get("@type") or []
                if isinstance(types, str):
                    types = [types]
                if "estleg:Issuer" in types:
                    label = jsonld_text(n.get("rdfs:label"))
                    if label:
                        valid_issuer_labels.add(_normalize_issuer_label(label))
        except (json.JSONDecodeError, OSError):
            pass

    for peep in sorted(kov_root.rglob("*_peep.json")):
        if peep.name.startswith("REGULATIONS_KOV_INDEX"):
            continue
        try:
            with open(peep, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        act_node = None
        for node in doc.get("@graph", []):
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "estleg:MunicipalRegulation" in types:
                act_node = node
                break
        if act_node is None:
            continue

        issuer = act_node.get("estleg:issuer")
        act_num = act_node.get("estleg:actNumber")
        aid = act_node.get("@id")
        if not (issuer and act_num and aid):
            continue

        xml_path = pair_peep_with_xml(peep, xml_lookup, data_dir=data_dir)
        if xml_path is None:
            continue
        adoption_date = _read_adoption_date_from_xml(xml_path)
        if adoption_date is None:
            continue

        # Normalise issuer label symmetrically with the resolver-side
        # query (Task 5). When the registry is non-empty, log (don't
        # reject) when the normalised act-issuer isn't in it — this
        # surfaces Layer 1 corpus-build oddities without dropping data.
        norm_issuer = _normalize_issuer_label(issuer)
        if valid_issuer_labels and norm_issuer not in valid_issuer_labels:
            # Tolerate; future Layer 2c may tighten this.
            pass

        lookup[(norm_issuer, adoption_date, str(act_num))] = aid
    return lookup


def build_kov_act_lookup_by_number(
    kov_root: Path,
) -> dict[tuple[str, str], list[str]]:
    """Build (normalized_issuer, act_number) → [act @id, ...] for KOV
    regulations.

    Used for body-text references that don't carry adoption_date.
    Multiple acts under the same issuer can share an act_number across
    historical revisions; the lookup STORES ALL CANDIDATES so the
    resolver (Task 6) can decide whether the match is unique. The
    resolver returns a target only when the candidate list contains
    exactly one entry (after scoping by enactedByMunicipality).

    Issuer labels are passed through _normalize_issuer_label for
    symmetry with the resolver's query side (which does the same).
    """
    lookup: dict[tuple[str, str], list[str]] = {}
    if not kov_root.is_dir():
        return lookup
    for peep in sorted(kov_root.rglob("*_peep.json")):
        if peep.name.startswith("REGULATIONS_KOV_INDEX"):
            continue
        try:
            with open(peep, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        for node in doc.get("@graph", []):
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "estleg:MunicipalRegulation" not in types:
                continue
            issuer = node.get("estleg:issuer")
            act_num = node.get("estleg:actNumber")
            aid = node.get("@id")
            if issuer and act_num and aid:
                key = (_normalize_issuer_label(issuer), str(act_num))
                lookup.setdefault(key, []).append(aid)
            break
    return lookup


def build_issuer_registry(
    issuers_path: Path,
) -> dict[str, tuple[str, str, str]]:
    """Build {issuer @id → (label_normalized, municipality_iri,
    body_type)} from the KOV issuers registry peep file.

    Used by the Task 5/6 resolver to derive municipality scoping from
    an enactedBy reference. Nodes missing any of the three required
    fields are skipped — partial issuer entries can't drive resolver
    scoping reliably.
    """
    out: dict[str, tuple[str, str, str]] = {}
    if not issuers_path.exists():
        return out
    try:
        with open(issuers_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return out
    for node in doc.get("@graph", []):
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "estleg:Issuer" not in types:
            continue
        iid = node.get("@id")
        label = jsonld_text(node.get("rdfs:label"))
        muni = node.get("estleg:currentMunicipality")
        body_type = node.get("estleg:bodyType")
        if isinstance(muni, dict):
            muni = muni.get("@id")
        if isinstance(body_type, dict):
            body_type = body_type.get("@id")
        if not (iid and label and muni and body_type):
            continue
        out[iid] = (_normalize_issuer_label(label), muni, body_type)
    return out


def build_citation_iri(source_act_iri: str, seq: int) -> str:
    """Build a SHACL-valid Citation IRI from the source act @id and a
    1-based sequence number. The 'estleg:' prefix is stripped from the
    source IRI before embedding."""
    shortid = source_act_iri.removeprefix("estleg:")
    return f"estleg:Citation_{shortid}_{seq}"


def build_citation_node(
    iri: str,
    target_iri: str,
    citation_detail: str | None,
    citation_text: str | None,
) -> dict:
    """Build a reified Citation node ready to insert into a peep file's
    @graph. Optional fields are omitted when None."""
    node: dict = {
        "@id": iri,
        "@type": ["owl:NamedIndividual", "estleg:Citation"],
        "estleg:citationTarget": {"@id": target_iri},
    }
    if citation_detail:
        node["estleg:citationDetail"] = citation_detail
    if citation_text:
        node["estleg:citationText"] = citation_text
    return node


def resolve_preamble_citation(
    cit: dict,
    *,
    genitive_to_act_iri: dict[str, str],
    prefix_to_provisions: dict[str, dict[str, str]],
    act_iri_to_prefix: dict[str, str],
    state_reg_lookup: dict[tuple[str, str, str], str],
    kov_act_lookup: dict[tuple[str, str, str], str],
    law_title_to_iri: dict[str, str] | None = None,
) -> tuple[str, str] | None:
    """Resolve a preamble citation to (citation_target_iri, enabling_act_iri).

    citation_target_iri:
        - paragraph-level provision IRI for law citations with §
        - act-level IRI otherwise

    enabling_act_iri:
        - ALWAYS act-level. Layer 2b's issuedUnder semantic is "act
          enacted under THIS act" — the range is Act, not LegalProvision.

    ``law_title_to_iri`` is the optional #390 corpus-title fallback:
    when a genitive law_ref is absent from the 40-entry
    ``genitive_to_act_iri`` chain, the genitive is folded to its
    nominative title form and looked up here (the corpus has ~236 more
    law titles than FULLNAME_GENITIVE covers).

    Returns None when the citation can't be resolved against any lookup.
    """
    form = cit.get("form")

    if form == "law-genitive":
        genitive = cit.get("law_ref", "").lower()
        act_iri = genitive_to_act_iri.get(genitive)
        if act_iri is None and law_title_to_iri:
            # #390 corpus-title fallback: fold the genitive to its
            # nominative title form ('kaitseliidu seaduse' ->
            # 'kaitseliidu seadus') and consult the corpus-title map.
            title = _genitive_law_ref_to_title(genitive)
            if title is not None:
                act_iri = law_title_to_iri.get(title)
        if act_iri is None:
            return None
        # If no paragraph, both target and enabling_act are act-level.
        if not cit.get("paragraphs"):
            return (act_iri, act_iri)
        # Resolve paragraph via prefix_to_provisions. The prefix MUST
        # come from act_iri_to_prefix — naive split on '_' breaks for
        # 34 corpus acts whose prefix itself contains underscores
        # (e.g. estleg:KARIST_2_Osa1_1_87 → prefix is 'KARIST_2',
        # NOT 'KARIST').
        prefix = act_iri_to_prefix.get(act_iri)
        if prefix is None:
            # Act IRI not in the prefix index — fall back to act-level.
            return (act_iri, act_iri)
        par = cit["paragraphs"][0]
        target = prefix_to_provisions.get(prefix, {}).get(par)
        if target is None:
            # Paragraph not in provision index — fall back to act-level
            # for citation_target while keeping enabling_act_iri at act.
            return (act_iri, act_iri)
        return (target, act_iri)

    if form == "state-regulation-date-number":
        # Issuer is normalised symmetrically: parser side calls
        # _normalize_state_issuer to strip the genitive ending; the
        # state_reg_lookup builder (Task 3) calls _normalize_issuer_label
        # on the act-side issuer string. Apply the same here so both
        # sides hash to the same key.
        key = (
            _normalize_issuer_label(cit["issuer"]),
            cit["adoption_date"],
            cit["act_number"],
        )
        act_iri = state_reg_lookup.get(key)
        if act_iri is None:
            return None
        return (act_iri, act_iri)

    if form == "kov-regulation-date-number":
        key = (
            _normalize_issuer_label(cit["issuer"]),
            cit["adoption_date"],
            cit["act_number"],
        )
        act_iri = kov_act_lookup.get(key)
        if act_iri is None:
            return None
        return (act_iri, act_iri)

    return None


# Body-text KOV reference pattern. KOV body text typically cites by
# issuer + act number (no date in body refs, unlike preambles).
# Example: "Tallinna Linnavolikogu määruse nr 15 § 4"
# Or generic: "linnavolikogu määrus nr 5"
_PAT_KOV_BODY_REF = re.compile(
    r"(?:(?P<issuer>(?:[A-ZÕÄÖÜŠŽ][a-zõäöüšž\-]+\s+)*)\s*)?"
    r"(?P<body>(?:[Ll]innavolikogu|[Ll]innavalitsuse?|"
    r"[Vv]allavolikogu|[Vv]allavalitsuse?|"
    r"[Aa]levivolikogu))\s+"
    r"määrus(?:e(?:ga)?)?\s+nr\s*(?P<num>\d+)",
    re.UNICODE,
)


def extract_kov_act_refs_from_text(text: str) -> list[dict]:
    """Parse body text for KOV-act references.

    Returns a list of dicts with:
        issuer    : full issuer label normalised via _normalize_issuer_label,
                    or None (when only generic body word like 'linnavolikogu'
                    is used without a place name)
        body_type : 'volikogu' | 'valitsus'
        act_number: digit string
        text      : matched substring (preserved verbatim from input)
    """
    if not text:
        return []
    out: list[dict] = []
    for m in _PAT_KOV_BODY_REF.finditer(text):
        body_raw = m.group("body")
        body_lower = body_raw.lower()
        body_type = "volikogu" if "volikogu" in body_lower else "valitsus"
        # Body word is captured in genitive form (e.g. "Vallavalitsuse")
        # by the alternation '[Vv]allavalitsuse?'. Strip the trailing
        # 'e' so the label matches the registry's nominative form
        # ('Vallavalitsus') after _normalize_issuer_label collapses case.
        body_canon = body_raw.rstrip("e") if body_lower.endswith("se") else body_raw
        issuer_full = m.group("issuer")
        # Trim leading preamble-license verbs (Vastavalt, Lähtudes, …)
        # but PRESERVE multi-word place names (Järva Jaani, Lääne-Nigula,
        # Saare-Anija, etc.). Reuse _trim_preamble_prefix from Task 2:
        # it strips only known license verbs from _PREAMBLE_LICENSE_PREFIXES,
        # leaving real place tokens intact. A naive "last token" trim
        # incorrectly drops the leading word of 26 multi-word KOV names.
        trimmed_issuer = (
            _trim_preamble_prefix(issuer_full.strip())
            if issuer_full and issuer_full.strip()
            else ""
        )
        if trimmed_issuer:
            raw_label = (trimmed_issuer + " " + body_canon).strip()
            issuer_label = _normalize_issuer_label(raw_label)
        else:
            issuer_label = None
        out.append({
            "issuer": issuer_label,
            "body_type": body_type,
            "act_number": m.group("num"),
            "text": m.group(0).strip(),
        })
    return out


def resolve_kov_internal_act_ref(
    *,
    source_municipality: str | None,
    explicit_issuer: str | None,
    body_type: str | None,
    act_number: str,
    kov_act_lookup_by_number: dict[tuple[str, str], list[str]],
    issuer_registry: dict[str, tuple[str, str, str]],
) -> str | None:
    """Resolve a KOV-internal act reference by act_number, scoped to the
    source act's municipality.

    Args:
        source_municipality: the @id of the source act's
            ``enactedByMunicipality``. None disables scoping.
        explicit_issuer: a pre-normalised issuer label parsed from the
            body-text (e.g. 'tallinna linnavolikogu'). When present,
            the resolver narrows directly to that issuer instead of
            enumerating every issuer in the source municipality. When
            None, the resolver falls back to body-type-scoped enumeration.
        body_type: 'volikogu' | 'valitsus' | None — secondary scope.
        act_number: digit string from the body-text reference.
        kov_act_lookup_by_number: (issuer_label_normalized, act_number)
            → list of candidate act @ids (Task 3 stores all candidates).
        issuer_registry: issuer @id → (label_normalized, municipality @id,
            body_type). The label is the rdfs:label from
            issuers_kov_peep.json passed through _normalize_issuer_label.

    Returns the resolved target @id only when the candidate list
    narrows to exactly one entry. Otherwise None.
    """
    if source_municipality is None:
        return None

    # Path A: explicit issuer string from the body text — the most
    # reliable disambiguator. Verify it belongs to the source
    # municipality (cross-municipality citations should be skipped).
    if explicit_issuer is not None:
        municipality_for_issuer = None
        for _iri, (label, mun_iri, _btype) in issuer_registry.items():
            if label == explicit_issuer:
                municipality_for_issuer = mun_iri
                break
        if municipality_for_issuer != source_municipality:
            return None
        candidates = kov_act_lookup_by_number.get(
            (explicit_issuer, act_number), []
        )
        return candidates[0] if len(candidates) == 1 else None

    # Path B: no explicit issuer — enumerate issuers in the source
    # municipality, narrow by body_type when supplied.
    candidate_labels: list[str] = []
    for _iri, (label, mun_iri, btype) in issuer_registry.items():
        if mun_iri != source_municipality:
            continue
        if body_type is not None and btype != body_type:
            continue
        candidate_labels.append(label)

    matches: list[str] = []
    for label in candidate_labels:
        matches.extend(
            kov_act_lookup_by_number.get((label, act_number), [])
        )
    # Dedupe + uniqueness check — body text isn't reliable enough to
    # pick a winner among ambiguous matches.
    unique = sorted(set(matches))
    return unique[0] if len(unique) == 1 else None


# ----------------------------------------------------------------------
# Superscript-aware paragraph numbers (#254)
# ----------------------------------------------------------------------
#
# Estonian law freely superscripts amended section numbers: ``AÕS § 158²``
# (Unicode glyph) or ``AÕS § 158<sup>2</sup>`` (RT markup). The generator
# (``generate_all_laws._paragraph_id_suffix``) encodes those into distinct
# IRIs — ``§ 158`` → ``_Par_158`` and ``§ 158²`` → ``_Par_158_2``. The
# citation parser must capture the superscript and normalise it into the
# same ``<base>_<m>`` key, or citations silently resolve to the wrong
# (non-superscript) provision and superscript provisions go un-scanned.

# Mirror of generate_all_laws._SUPERSCRIPT_DIGIT_MAP — kept local so this
# module has no import dependency on the generator.
_SUPERSCRIPT_DIGIT_MAP: dict[str, str] = {
    "¹": "1", "²": "2", "³": "3", "⁰": "0", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
}

# A trailing superscript index on a paragraph number: either a run of
# Unicode superscript glyphs or ``<sup>N</sup>`` markup.
_SUP_TOKEN = r"(?:<sup>\s*\d+\s*</sup>|[¹²³⁰⁴-⁹]+)"

# Section-number capture: digits, an OPTIONAL trailing superscript, then an
# OPTIONAL ``-`` range tail. The superscript binds to the first number only
# (ranges across superscripts do not occur in the corpus). Used by every
# ``§``-anchored citation pattern below so all of them resolve superscripts.
_PAR_NUMBER = rf"\d+(?:\s*{_SUP_TOKEN})?(?:\s*[\-–]\s*\d+)?"


def _superscript_suffix_from_token(token: str) -> str:
    """Return the plain-digit index encoded by a superscript ``token``.

    ``token`` is the matched ``_SUP_TOKEN`` text — ``"²"``, ``"²³"`` or
    ``"<sup>2</sup>"``. Returns ``"2"`` / ``"23"`` / ``"2"``, or ``""`` when
    nothing decodes.
    """
    if not token:
        return ""
    m = re.search(r"<sup>\s*(\d+)\s*</sup>", token)
    if m:
        return m.group(1).strip()
    return "".join(_SUPERSCRIPT_DIGIT_MAP.get(ch, "") for ch in token)


def _normalize_par_number(raw: str) -> str:
    """Normalise a single captured section number into an IRI-key shape.

    Strips a trailing superscript glyph/markup and folds it into a
    ``<digits>_<index>`` suffix, matching
    ``generate_all_laws._paragraph_id_suffix`` (``§ 158²`` → ``158_2``).
    A plain number returns its digits unchanged (``"158"`` → ``"158"``).
    Returns ``""`` when no leading digits are present.
    """
    m = re.match(rf"(\d+)\s*({_SUP_TOKEN})?\s*$", raw.strip())
    if not m:
        # No clean single-number match (e.g. leftover punctuation); fall
        # back to bare digits so callers never regress on plain numbers.
        digits = re.sub(r"[^\d]", "", raw)
        return digits
    base = m.group(1)
    sup = _superscript_suffix_from_token(m.group(2) or "")
    return f"{base}_{sup}" if sup else base


# ----------------------------------------------------------------------
# Module-level compiled citation patterns (#386)
#
# KNOWN_ABBREVIATIONS / FULLNAME_GENITIVE are static for the life of the
# process, so the ``sorted() + re.escape() + '|'.join()`` alternation and
# the resulting ``re.compile`` were pure waste when rebuilt on every one
# of the ~142k per-provision ``extract_citations_from_text`` calls (the
# interpolated join string is NOT covered by re's internal compile
# cache). Hoisting them to module-level constants does the work once at
# import. The alternations sort by length descending so a longer
# abbreviation/name wins over a prefix of it.
# ----------------------------------------------------------------------
_ABBREV_ALTERNATION = "|".join(
    re.escape(a) for a in sorted(KNOWN_ABBREVIATIONS.keys(), key=len, reverse=True)
)
_GENITIVE_ALTERNATION = "|".join(
    re.escape(g) for g in sorted(FULLNAME_GENITIVE.keys(), key=len, reverse=True)
)

# Pattern 1: Abbreviation + § + number(s)
#   KarS § 121, KarS §-s 121, KarS § 121 lg 2 p 3, KarS §-de 208-210
_PAT_ABBREV = re.compile(
    rf"({_ABBREV_ALTERNATION})\s*{PAR_SUFFIX}\s*({_PAR_NUMBER})",
    re.UNICODE,
)

# Pattern 2: käesoleva seaduse/seadustiku/koodeksi § N (self-reference).
# The ``koodeksi?`` alternative captures intra-code self-references
# ("käesoleva koodeksi §") in the maritime / criminal-procedure codes
# that the bare ``seadus(?:e|tiku)?`` form silently dropped (#363).
_PAT_SELF = re.compile(
    rf"k[äa]esoleva\s+(?:seadus(?:e|tiku)?|koodeksi?)\s*"
    rf"{PAR_SUFFIX}\s*({_PAR_NUMBER})",
    re.UNICODE | re.IGNORECASE,
)

# Pattern 3: Full law name in genitive form + § + number
#   "tsiviilseadustiku üldosa seaduse § 67". Compiled only when the
#   genitive table is non-empty (it always is in practice).
_PAT_FULLNAME = (
    re.compile(
        rf"({_GENITIVE_ALTERNATION})\s*{PAR_SUFFIX}\s*({_PAR_NUMBER})",
        re.UNICODE | re.IGNORECASE,
    )
    if _GENITIVE_ALTERNATION
    else None
)


def extract_citations_from_text(text: str) -> list[dict]:
    """
    Parse text for Estonian legal citation patterns.

    Returns list of dicts with keys:
      - law_ref: abbreviation or full name reference
      - paragraphs: list of paragraph numbers (strings)
      - is_self_ref: True if "käesoleva seaduse" pattern

    Uses the module-level compiled patterns ``_PAT_ABBREV`` /
    ``_PAT_SELF`` / ``_PAT_FULLNAME`` (#386) rather than rebuilding the
    abbrev/genitive alternations on every call.
    """
    citations: list[dict] = []
    if not text:
        return citations

    # Pattern 1: Abbreviation + § + number(s)
    for m in _PAT_ABBREV.finditer(text):
        abbrev = m.group(1)
        par_range = m.group(2).strip()
        paragraphs = _expand_par_range(par_range)
        citations.append({
            "law_ref": abbrev,
            "paragraphs": paragraphs,
            "is_self_ref": False,
        })

    # Pattern 2: käesoleva seaduse/seadustiku/koodeksi § N (self-reference)
    for m in _PAT_SELF.finditer(text):
        par_range = m.group(1).strip()
        paragraphs = _expand_par_range(par_range)
        citations.append({
            "law_ref": "__SELF__",
            "paragraphs": paragraphs,
            "is_self_ref": True,
        })

    # Pattern 3: Full name in genitive form + § + number
    if _PAT_FULLNAME is not None:
        for m in _PAT_FULLNAME.finditer(text):
            gen_name = m.group(1).lower()
            par_range = m.group(2).strip()
            paragraphs = _expand_par_range(par_range)
            abbrev = FULLNAME_GENITIVE.get(gen_name)
            if abbrev:
                citations.append({
                    "law_ref": abbrev,
                    "paragraphs": paragraphs,
                    "is_self_ref": False,
                })

    return citations


# ----------------------------------------------------------------------
# Preamble citation extraction (Layer 2b)
# ----------------------------------------------------------------------

# Estonian month name (genitive) → 2-digit month number.
# All forms come from the spec text "<day>. <month-genitive> <year>".
_ET_MONTHS = {
    "jaanuari": "01", "veebruari": "02", "märtsi": "03", "aprilli": "04",
    "mai": "05", "juuni": "06", "juuli": "07", "augusti": "08",
    "septembri": "09", "oktoobri": "10", "novembri": "11", "detsembri": "12",
}

# Quote characters seen in the corpus around law names. The parser
# strips these before matching the genitive form.
_QUOTES = '"\'„""«»«»“”„’‘'

# Section symbol or its word form. KOV preambles freely mix § and
# the spelled-out "paragrahv" / "paragrahvi".
_PARA_TOKEN = r"(?:§|paragrahv(?:i)?|paragrahvid?)"

# Lõige (subsection) — multiple case forms in the corpus:
#   lõike (singular genitive) — most common
#   lõikest (singular elative)
#   lõigete (plural genitive) — for "lõigete 1 ja 2"
#   lg (abbreviation) — common in shorter preambles
_LG_TOKEN = r"(?:lõike(?:st|s)?|lõigete|lg)"

# Punkt — multiple forms:
#   punkti (genitive) — "punkti 34"
#   punkt (nominative)
#   p (abbreviation)
_P_TOKEN = r"(?:punkti|punkt|p)"

# Pattern A — law in genitive form + § N (lõike M (punkti K)?)?
#
# The law-name genitive suffix appears in TWO grammatical positions:
#
#   1. ATTACHED to the final word of the name:
#        "alkoholiseaduse § 42"        (alkohol + seaduse)
#        "karistusseadustiku § 121"    (karistus + seadustiku)
#        "põhikooli- ja gümnaasiumiseaduse § 66"
#
#   2. STANDALONE as the final word, after a separate noun phrase:
#        "kohaliku omavalitsuse korralduse seaduse § 22"
#        "rahvaraamatukogu seaduse § 16"
#        "avaliku teabe seaduse § 1"
#        "sotsiaalhoolekande seaduse § 14"
#        "ühisveevärgi ja -kanalisatsiooni seaduse § 5"
#
# A regex requiring the FINAL token to itself end in seaduse|seadustiku
# (the v3+v4 form) misses every shape in case (2) — and case (2)
# covers the bulk of real KOV enabling-act citations. The fix below
# alternates: either (a) attached form `<word><seaduse|seadustiku>`
# or (b) standalone marker `seaduse|seadustiku` after up to 7 leading
# law-name words.
#
# A leading hyphen on a word is allowed so names like
# "ühisveevärgi ja -kanalisatsiooni seaduse" tokenise cleanly.
_LAW_WORD = r"-?[a-zõäöüšžA-ZÕÄÖÜŠŽ][a-zõäöüšžA-ZÕÄÖÜŠŽ\-]*"

_PAT_LAW_PARA = re.compile(
    rf"(?P<law>(?:{_LAW_WORD}\s+){{0,7}}"
    rf"(?:{_LAW_WORD}(?:seaduse|seadustiku)|seaduse|seadustiku))\s*"
    rf"{_PARA_TOKEN}\s*(?P<par>\d+)"
    rf"(?:\s+{_LG_TOKEN}\s+(?P<lg>\d+))?"
    rf"(?:\s+{_P_TOKEN}\s+(?P<p>\d+))?",
    re.UNICODE | re.IGNORECASE,
)

# Chained paragraph reference following a law-genitive match.
# Matches "§ N", "§ N lõike M", "§ N lõike M punkti K" preceded by
# a comma or 'ja'/'ning'/'või' connector. Used to extract chained
# same-law paragraphs after the initial law citation.
#
# Real KOV preambles routinely chain multiple paragraphs under a single
# law (e.g. "kohaliku omavalitsuse korralduse seaduse § 6 lg 3 p 2 ja
# § 22 lg 1 p 34"). _PAT_LAW_PARA captures only the first paragraph;
# this companion pattern is anchored at the position immediately after
# each base match end, advancing through ", §" / " ja §" / " ning §"
# connectors until no contiguous chain remains.
_PAT_PAR_CHAIN = re.compile(
    rf"(?:\s*,\s*|\s+(?:ja|ning|või)\s+)"
    rf"{_PARA_TOKEN}\s*(?P<par>\d+)"
    rf"(?:\s+{_LG_TOKEN}\s+(?P<lg>\d+))?"
    rf"(?:\s+{_P_TOKEN}\s+(?P<p>\d+))?",
    re.UNICODE | re.IGNORECASE,
)

# The greedy 0-7 leading-word group also absorbs preamble-license
# verbs that precede a law citation in the wild ("Määrus kehtestatakse",
# "Kooskõlas", "Lähtudes", etc.). These are NOT part of the law name
# itself. After capture, _trim_preamble_prefix strips them so
# law_ref matches FULLNAME_GENITIVE.
#
# Words that MAY appear inside multiword law names (ja, ning, või,
# adjectives like 'kohaliku', 'avaliku', etc.) are deliberately
# OMITTED from this set. Adding them would break legitimate
# multiword law names.
_PREAMBLE_LICENSE_PREFIXES = {
    # Establishment / license verbs
    "määrus", "määrust", "määruse", "määrusega", "määrustes",
    "kehtestatakse", "kehtestatud", "kehtestab", "kehtestada",
    "antakse", "antud", "annab",
    "vastu", "vastavalt", "võetud",
    # Copula 'on' — sits between 'määrus' and 'kehtestatud' in
    # passive constructions like "Käesolev määrus on kehtestatud …".
    # Not part of any Estonian law name (finite verb), so safe to trim.
    "on",
    # Connector phrases that license a law citation
    "kooskõlas", "lähtudes", "võttes",
    "tulenevalt", "tuginedes", "tuleneb", "tulenevat",
    # Sentence-starts that frame a law citation
    "käesolev", "käesolevaga",
    "see",
    # Adverbial prepositions
    "alusel", "alus",
}


def _trim_preamble_prefix(law: str) -> str:
    """Strip leading preamble-license tokens from a parser match.

    The greedy regex captures up to 7 leading words; this trims off
    common 'Määrus kehtestatakse <lawname>' / 'Kooskõlas <lawname>'
    framings without touching words that belong inside the law name
    itself.

    Trailing punctuation (',', '.', etc.) is stripped before
    membership check so 'Lähtudes,' is recognised as 'lähtudes'.

    A leading connector ``ning`` / ``ja`` is stripped ONLY when the
    remainder still ends in ``seaduse`` / ``seadustiku`` (#390). A
    chained second enabling law surfaces as ``'ning avaliku teenistuse
    seaduse'`` where ``ning`` is a connector, not part of the name —
    but ``ja`` / ``ning`` also appear INSIDE multiword law names
    ('põhikooli- ja gümnaasiumiseaduse', 'ühisveevärgi ja
    -kanalisatsiooni seaduse'), so they are NOT in
    ``_PREAMBLE_LICENSE_PREFIXES``. The end-marker guard distinguishes
    the two: a genuine connector leaves a law name behind, whereas an
    internal ``ja`` is never the FIRST token.
    """
    words = law.split()
    while words and words[0].lower().rstrip(",.;:") in _PREAMBLE_LICENSE_PREFIXES:
        words.pop(0)
    if len(words) >= 2 and words[0].lower().rstrip(",.;:") in {"ja", "ning"}:
        rest = words[1:]
        last = rest[-1].lower().rstrip(",.;:")
        if last.endswith("seaduse") or last.endswith("seadustiku"):
            words = rest
    return " ".join(words)

# Pattern B — state regulation: "Vabariigi Valitsuse <date> määruse(ga) nr N"
# Date can be:
#   - "19. detsembri 2019. a" (named-month genitive form)
#   - "20.12.2007" (numeric DD.MM.YYYY)
# määruse / määrusega — case form varies by sentence position.
_DATE_NAMED = (
    r"(?P<day_n>\d{1,2})\.\s*"
    r"(?P<month>jaanuari|veebruari|märtsi|aprilli|mai|juuni|juuli|"
    r"augusti|septembri|oktoobri|novembri|detsembri)\s+"
    r"(?P<year_n>\d{4})\.?\s*a?\.?"
)
_DATE_NUMERIC = (
    r"(?P<day_d>\d{1,2})\.\s*(?P<month_d>\d{1,2})\.\s*(?P<year_d>\d{4})\.?\s*a?\.?"
)
_DATE_EITHER = rf"(?:{_DATE_NAMED}|{_DATE_NUMERIC})"

_PAT_STATE_REG = re.compile(
    # Issuer alternatives:
    #   1. "Vabariigi Valitsuse" (genitive of Government)
    #   2. minister names ending in "ministri", in three shapes:
    #         "rahandusministri"                  (compound, single token)
    #         "majandus- ja kommunikatsiooniministri" (multiword + hyphen)
    #         "riigihalduse ministri"              (descriptor + standalone token)
    #
    # The standalone "ministri" branch (third shape) is critical — about
    # 1/4 of state regulations in the corpus use the separate-word form
    # ("Riigihalduse minister" issued ~150 acts in the corpus). The
    # alternation `[a-z]+ministri|ministri` covers both attached and
    # standalone usage; the optional leading group consumes any
    # descriptor words preceding a standalone "ministri".
    r"(?P<issuer>Vabariigi\s+Valitsuse|"
    r"(?:[A-ZÕÄÖÜŠŽa-zõäöüšž][\w\-õäöüšžÕÄÖÜŠŽ]*(?:[-\s]+(?:ja\s+)?[a-zõäöüšž][\w\-õäöüšžÕÄÖÜŠŽ]*){0,4}\s+)?"
    r"(?:[a-zõäöüšž]+ministri|ministri))\s+"
    rf"{_DATE_EITHER}\s+määrus(?:ega|es|e|est)\s+nr\s*\.?\s*(?P<num>\d+)",
    re.UNICODE | re.IGNORECASE,
)

# Pattern C — KOV regulation: "<Issuer Display Name> <date> määruse(ga) nr N"
# Issuer ends in volikogu / valitsus(e) — the genitive form is
# "Linnavolikogu" / "Linnavalitsuse" / "Vallavolikogu" / "Vallavalitsuse".
# Issuer name may be compound (Häädemeeste, Lääne-Nigula).
_PAT_KOV_REG = re.compile(
    r"(?P<issuer>[A-ZÕÄÖÜŠŽ][a-zõäöüšž\-]+(?:\s+[A-ZÕÄÖÜŠŽ][a-zõäöüšž\-]+)*\s+"
    r"(?:Linnavolikogu|Linnavalitsuse|Linnavalitsus|"
    r"Vallavolikogu|Vallavalitsuse|Vallavalitsus|"
    r"Alevivolikogu))\s+"
    rf"{_DATE_EITHER}\s+määrus(?:ega|e|est)\s+nr\s*(?P<num>\d+)",
    re.UNICODE,
)


def _build_citation_detail(lg: str | None, p: str | None) -> str | None:
    """Combine lõige (lg) and punkt (p) into 'lg 1' / 'lg 1 p 3'."""
    parts = []
    if lg:
        parts.append(f"lg {lg}")
    if p:
        parts.append(f"p {p}")
    return " ".join(parts) if parts else None


def _normalize_state_issuer(issuer_text: str) -> str:
    """Strip Estonian genitive ending ("...ministri" → "...minister",
    "Vabariigi Valitsuse" → "Vabariigi Valitsus") so the resolver's
    state-regulation lookup key matches the corpus issuer label.

    The corpus stamps state-regulation issuers in nominative case on
    the act node's ``estleg:issuer`` field (e.g. "Rahandusminister",
    "Riigihalduse minister", "Vabariigi Valitsus"). Without normalisation,
    the parser-extracted genitives "rahandusministri" / "riigihalduse
    ministri" / "Vabariigi Valitsuse" would never match any lookup key.

    Estonian: nominative '-minister' takes genitive '-ministri'
    (the final 'er' becomes 'ri'). To round-trip correctly, the
    suffix '-ministri' is replaced with '-minister', not just
    truncated by one char (which would yield '-ministr' — wrong).
    """
    # Collapse internal whitespace (multiword minister regex can pick
    # up stray spaces around hyphens or 'ja').
    s = re.sub(r"\s+", " ", issuer_text.strip())
    if s.endswith("Valitsuse"):
        return s[:-1]  # Valitsuse → Valitsus (single trailing 'e' drop)
    # Reverse the Estonian -er→-ri genitive transform.
    if s.endswith("ministri"):
        return s[:-len("ministri")] + "minister"
    if s.endswith("Ministri"):
        return s[:-len("Ministri")] + "Minister"
    return s


def _strip_quotes(text: str) -> str:
    """Strip surrounding quote characters around a single token. The
    parser pre-processes preamble text to remove these so the law-name
    regex can match `Kohanimeseaduse` whether or not it's quoted."""
    return text.strip(_QUOTES + " ")


def _normalize_date(
    day_n: str | None, month_named: str | None, year_n: str | None,
    day_d: str | None, month_d: str | None, year_d: str | None,
) -> str | None:
    """Build ISO-8601 date from either named-month or numeric-date
    capture groups. Returns None if neither pair is fully populated."""
    if day_n and month_named and year_n:
        m = _ET_MONTHS.get(month_named.lower())
        if m is None:
            return None
        return f"{year_n}-{m}-{int(day_n):02d}"
    if day_d and month_d and year_d:
        return f"{year_d}-{int(month_d):02d}-{int(day_d):02d}"
    return None


def extract_preamble_citations(text: str) -> list[dict]:
    """Parse a preamble for Estonian legal citation forms.

    Returns a list of dicts in textual order. Each dict has:
        form           : "law-genitive" | "state-regulation-date-number"
                         | "kov-regulation-date-number"
        citationText   : original matched substring
        citationDetail : "lg 1" / "lg 1 p 3" / None

    Form-specific keys:
        law-genitive:
            law_ref      : the full genitive law name
            paragraphs   : ["N"] (always one entry — no ranges in
                           preambles in the current corpus)

        state-regulation-date-number / kov-regulation-date-number:
            issuer        : nominative issuer string
            adoption_date : ISO-8601 date string
            act_number    : the digit string after "määruse(ga) nr"
    """
    if not text:
        return []

    # Substitute quote characters with spaces in a SCRATCH copy used
    # only for matching. The original `text` is preserved so that
    # citationText (intended for traceability and SHACL display) is
    # the literal input substring with quotes intact.
    cleaned = re.sub(rf"[{re.escape(_QUOTES)}]", " ", text)

    def _orig_substring(start_in_cleaned: int, end_in_cleaned: int) -> str:
        # Quote substitution is one-char-for-one-char, so offsets in
        # `cleaned` are valid in `text`. Use the original text for
        # citationText to preserve quotes / punctuation.
        return text[start_in_cleaned:end_in_cleaned].strip()

    citations: list[tuple[int, dict]] = []

    for m in _PAT_LAW_PARA.finditer(cleaned):
        # Strip surrounding quotes, then strip leading preamble-license
        # verbs (Määrus kehtestatakse, Kooskõlas, Lähtudes, …) so the
        # law_ref is a clean genitive law name suitable for
        # FULLNAME_GENITIVE lookup. The regex is intentionally
        # permissive on the leading side; this post-step is what
        # actually narrows the capture to the law name itself.
        raw = _strip_quotes(m.group("law")).strip()
        law_ref = _trim_preamble_prefix(raw)
        if not law_ref:
            # Defensive: if trimming consumed everything (the match
            # captured ONLY preamble-license words plus a standalone
            # 'seaduse'), skip the match.
            continue
        cit = {
            "form": "law-genitive",
            "law_ref": law_ref,
            "paragraphs": [m.group("par")],
            "citationDetail": _build_citation_detail(m.group("lg"), m.group("p")),
            "citationText": _orig_substring(m.start(), m.end()),
        }
        citations.append((m.start(), cit))

        # Chain extension: same law_ref, additional paragraphs.
        # The chain regex is anchored to the position immediately after
        # the base match end, advancing through ", §" / " ja §" / " ning §"
        # connectors until no contiguous chain remains. Each chained
        # paragraph carries its OWN lg/p detail — the leading match's
        # detail is NOT propagated forward.
        pos = m.end()
        while True:
            chain_m = _PAT_PAR_CHAIN.match(cleaned, pos)
            if chain_m is None:
                break
            chained_cit = {
                "form": "law-genitive",
                "law_ref": law_ref,
                "paragraphs": [chain_m.group("par")],
                "citationDetail": _build_citation_detail(
                    chain_m.group("lg"), chain_m.group("p")
                ),
                # Use the original-text substring for the FULL match
                # including the connector, so citationText is
                # human-readable.
                "citationText": _orig_substring(chain_m.start(), chain_m.end()),
            }
            citations.append((chain_m.start(), chained_cit))
            pos = chain_m.end()

    for m in _PAT_STATE_REG.finditer(cleaned):
        date = _normalize_date(
            m.group("day_n"), m.group("month"), m.group("year_n"),
            m.group("day_d"), m.group("month_d"), m.group("year_d"),
        )
        if date is None:
            continue
        # Same trim as the law-citation branch — strip leading
        # "Määrus kehtestatakse" / "Lähtudes" / etc. that the greedy
        # leading-words group absorbed when the citation is embedded
        # in a sentence.
        raw_issuer = _trim_preamble_prefix(m.group("issuer").strip())
        if not raw_issuer:
            continue
        cit = {
            "form": "state-regulation-date-number",
            "issuer": _normalize_state_issuer(raw_issuer),
            "adoption_date": date,
            "act_number": m.group("num"),
            "citationDetail": None,
            "citationText": _orig_substring(m.start(), m.end()),
        }
        citations.append((m.start(), cit))

    for m in _PAT_KOV_REG.finditer(cleaned):
        date = _normalize_date(
            m.group("day_n"), m.group("month"), m.group("year_n"),
            m.group("day_d"), m.group("month_d"), m.group("year_d"),
        )
        if date is None:
            continue
        raw_issuer = _trim_preamble_prefix(
            re.sub(r"\s+", " ", m.group("issuer").strip())
        )
        if not raw_issuer:
            continue
        cit = {
            "form": "kov-regulation-date-number",
            "issuer": raw_issuer,
            "adoption_date": date,
            "act_number": m.group("num"),
            "citationDetail": None,
            "citationText": _orig_substring(m.start(), m.end()),
        }
        citations.append((m.start(), cit))

    citations.sort(key=lambda pair: pair[0])
    return [c for _, c in citations]


def _expand_par_range(par_range: str) -> list[str]:
    """Expand '208-210' into ['208', '209', '210'].

    Single numbers normalise to their IRI-key shape, folding a trailing
    superscript into a ``<base>_<m>`` suffix (``'158²'`` → ``'158_2'``,
    ``'22<sup>1</sup>'`` → ``'22_1'``) so the resolver finds the
    superscript provision (#254). A range expands only when BOTH endpoints
    are bare decimal digits; a superscripted range endpoint ('62¹-65') is
    not expandable and yields [] rather than a fabricated key.
    """
    par_range = par_range.replace("–", "-").replace("‑", "-")
    if "-" in par_range:
        parts = par_range.split("-", 1)
        # Only treat as a range when BOTH endpoints are bare decimal digits.
        # NB: use ``str.isdecimal()``, NOT ``str.isdigit()`` — the latter is
        # True for superscript glyphs ('62¹'.isdigit() is True) but int()
        # rejects them (ValueError), so an isdigit() guard lets a superscripted
        # endpoint like '62¹-65' reach int() and crashes the whole run.
        # isdecimal() is True exactly for the chars int() accepts.
        if parts[0].strip().isdecimal() and parts[1].strip().isdecimal():
            start = int(parts[0].strip())
            end = int(parts[1].strip())
            # A reversed ('200-100') or over-wide ('4-59', '100-200') range is
            # malformed → [] rather than falling through to
            # _normalize_par_number, which would strip the hyphen and
            # CONCATENATE the digits ('4-59' -> '459') into a false provision
            # key that can collide with a real § (#341).
            if end < start or end - start > 50:
                return []
            return [str(n) for n in range(start, end + 1)]
        # Hyphen present but the endpoints are not both decimal — e.g. a
        # superscripted range endpoint ('62¹-65'). Cannot expand, and must NOT
        # fall through to _normalize_par_number: it strips the hyphen+superscript
        # and CONCATENATES '62¹-65' -> '6265', a false key. Same anti-fabrication
        # rule as the #341 numeric case above.
        return []
    # Single number (possibly superscripted): fold the trailing superscript
    # into the IRI-key suffix ('158²' -> '158_2', '22<sup>1</sup>' -> '22_1').
    norm = _normalize_par_number(par_range)
    return [norm] if norm else []


def resolve_citation(
    citation: dict,
    self_prefix: str,
    abbrev_to_prefix: dict[str, list[str] | str],
    prefix_to_provisions: dict[str, dict[str, str]],
) -> list[str]:
    """
    Resolve a citation dict to a list of existing provision IRIs.

    Returns list of IRI strings (e.g. ["estleg:KARIST_2_Par_121"]).

    A law abbreviation may map to SEVERAL provision prefixes when the
    law is split across osa files (one ``estleg:sourceAct`` → many osa
    prefixes, #299). Each requested paragraph is searched across ALL of
    the law's prefixes and the FIRST hit wins, so §§ that live in
    different osa (e.g. ``AÕS § 1`` in osa1 and ``AÕS § 200`` in osa5)
    both resolve. ``abbrev_to_prefix`` values may be a list (new
    contract) or a bare string (tolerated for older callers/tests).
    """
    resolved: list[str] = []

    # Determine the candidate IRI prefixes for this law reference. The
    # self-reference prefix is always a single string (the file's own
    # prefix); cross-law abbreviations carry a list of osa prefixes.
    if citation["is_self_ref"]:
        prefixes: list[str] = [self_prefix] if self_prefix else []
    else:
        law_ref = citation["law_ref"]
        mapped = abbrev_to_prefix.get(law_ref)
        if not mapped:
            return []
        prefixes = [mapped] if isinstance(mapped, str) else list(mapped)

    if not prefixes:
        return []

    for par_num in citation["paragraphs"]:
        # Search every candidate prefix; take the first prefix whose
        # provision index carries this paragraph (exact match, then
        # leading-zero-stripped).
        stripped = par_num.lstrip("0") or "0"
        for prefix in prefixes:
            provisions = prefix_to_provisions.get(prefix)
            if not provisions:
                continue
            if par_num in provisions:
                resolved.append(provisions[par_num])
                break
            if stripped in provisions:
                resolved.append(provisions[stripped])
                break

    return resolved


def _xml_paragraph_key(par_el: ET.Element) -> str:
    """Return the IRI-suffix key for a ``paragrahv`` element.

    Mirrors ``generate_all_laws._paragraph_id_suffix`` so the key matches
    the ``_Par_<suffix>`` segment of the provision @id: base digits plus an
    optional superscript index folded into a ``_<m>`` suffix. Reads the
    superscript from the ``paragrahvNr@ylaIndeks`` attribute first (the
    canonical, machine form), then falls back to a Unicode/``<sup>`` glyph
    in ``kuvatavNr``. Returns ``""`` when no paragraph number is present.

    Without this, ``re.sub(r"[^\\d]", "", par_nr)`` collapsed ``§ 158`` and
    ``§ 158²`` to the same key ``158`` (last-write-wins), so the superscript
    provision's body text was lost and ``_load_provision_text`` could not
    find it (#254).
    """
    par_nr = None
    sup = ""
    for child in par_el:
        if ln(child.tag) == "paragrahvNr" and child.text and par_nr is None:
            par_nr = child.text.strip()
            ya = (child.attrib.get("ylaIndeks") or "").strip()
            if ya:
                sup = ya
    if not par_nr:
        return ""
    base = re.sub(r"[^\d]", "", par_nr)
    if not base:
        return ""
    if not sup:
        # No machine ylaIndeks — recover a superscript glyph from kuvatavNr.
        for child in par_el:
            if ln(child.tag) == "kuvatavNr":
                kuv = "".join(child.itertext())
                m = re.search(rf"\d+\s*({_SUP_TOKEN})", kuv)
                if m:
                    sup = _superscript_suffix_from_token(m.group(1))
                break
    return f"{base}_{sup}" if sup else base


def collect_text_from_xml(xml_path: Path) -> dict[str, str]:
    """
    Parse a Riigi Teataja XML and extract text content per paragraph.

    Returns: {paragraph_key: full_text} where ``paragraph_key`` matches the
    ``_Par_<suffix>`` IRI segment (e.g. ``"158"`` or ``"158_2"``). Plain and
    superscript paragraphs therefore stay distinct (#254).
    """
    par_texts: dict[str, str] = {}
    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except (ET.ParseError, OSError):
        return par_texts

    for el in root.iter():
        if ln(el.tag) == "paragrahv":
            key = _xml_paragraph_key(el)
            if not key:
                continue
            # Collect all text within this paragraph
            full_text = " ".join(el.itertext())
            full_text = re.sub(r"\s+", " ", full_text).strip()
            par_texts[key] = full_text

    return par_texts


def _load_provision_text(
    node: dict,
    xml_par_texts: dict[str, str],
) -> str:
    """Compose the scannable text for a provision node.

    Prefers the XML paragraph body keyed by the ``_Par_<suffix>`` segment
    of the @id, then concatenates the JSON-LD ``estleg:summary`` when
    present. When NEITHER carries text, falls back to
    ``estleg:legalText``.

    The legalText fallback is what makes subsection (``_Lg_``) nodes
    scannable: a lõige node's @id suffix (e.g. ``3_Lg_3``) is never an
    XML paragraph key and its ``estleg:summary`` is always empty, so the
    XML+summary composition yields "" and the node would be skipped —
    yet the actual subsection text (often carrying a ``§`` citation)
    lives in ``estleg:legalText`` (#362). Using legalText only as a
    fallback keeps paragraph-level nodes (which carry both summary and
    legalText) from double-counting the same citation.

    The lookup key is the full IRI suffix (``158`` or ``158_2``), which
    matches the key shape produced by ``_xml_paragraph_key`` /
    ``collect_text_from_xml``. This keeps a superscript provision
    (``_Par_158_2``) reading its OWN body instead of colliding with the
    plain provision (``_Par_158``) (#254).
    """
    node_id = node.get("@id", "")
    if not isinstance(node_id, str) or "_Par_" not in node_id:
        return ""
    local = node_id[len("estleg:"):] if node_id.startswith("estleg:") else node_id
    par_num = local.split("_Par_", 1)[1]
    text_to_scan = xml_par_texts.get(par_num, "")
    # estleg:summary may be a plain string or a {"@value": ..., "@language":
    # "et"} object depending on the generator; jsonld_text yields the raw
    # text either way so the string concatenation below never hits a dict.
    summary = jsonld_text(node.get("estleg:summary", ""))
    if summary:
        text_to_scan = (
            text_to_scan + " " + summary if text_to_scan else summary
        )
    # Fallback: subsection (_Lg_) nodes have no XML key and no summary —
    # their citable body is in estleg:legalText (#362).
    if not text_to_scan:
        text_to_scan = jsonld_text(node.get("estleg:legalText", ""))
    return text_to_scan


def _derive_self_prefix(
    graph: list[dict],
    act_iri_to_prefix: dict[str, str] | None,
) -> str | None:
    """Derive the file's own prefix for resolving self-references.

    Strategy (in order):

    1. Look up the act @id from the ``owl:Ontology`` node and consult
       ``act_iri_to_prefix`` (the registry built by
       ``build_provision_index``). This is the authoritative path —
       the registry already handles the 34 corpus acts whose prefix
       contains underscores (e.g. ``KARIST_2_Map_2026``).
    2. Fall back to ``_prefix_from_act_iri`` against the act @id when
       the registry doesn't carry it (rare legacy peep without a
       provision-anchored prefix entry).
    3. Last-resort fallback: scan the first provision @id and split
       on ``_Par_``. This preserves prior behaviour for files whose
       owl:Ontology act node is absent or whose registry mapping is
       missing — but the previous fragile string-split code path is
       no longer the primary derivation.
    """
    # Path 1 + 2: act-IRI driven (registry-first, suffix-stripper second)
    for node in graph:
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "owl:Ontology" not in types:
            continue
        act_iri = node.get("@id")
        if not isinstance(act_iri, str):
            continue
        if act_iri_to_prefix:
            prefix = act_iri_to_prefix.get(act_iri)
            if prefix:
                return prefix
        derived = _prefix_from_act_iri(act_iri)
        if derived:
            return derived
        break

    # Path 3: last-resort provision @id scan. Kept so peeps without an
    # owl:Ontology act node still resolve self-references.
    for node in graph:
        node_id = node.get("@id", "")
        if "_Par_" in node_id and isinstance(node_id, str) \
                and node_id.startswith("estleg:"):
            local = node_id[len("estleg:"):]
            return local.split("_Par_", 1)[0]
    return None


def _run_inlaw_citation_pass(
    graph: list[dict],
    *,
    self_prefix: str,
    abbrev_to_prefix: dict[str, list[str]],
    prefix_to_provisions: dict[str, dict[str, str]],
    xml_par_texts: dict[str, str],
) -> dict:
    """Run the in-law citation pass over ``graph``.

    Mutates provision nodes by attaching ``estleg:references``. Returns
    a stats fragment with ``provisions_scanned``, ``citations_found``,
    ``citations_resolved``, ``provisions_with_refs``, and ``modified``
    (True when at least one provision gained a reference).
    """
    stats = {
        "provisions_scanned": 0,
        "citations_found": 0,
        "citations_resolved": 0,
        "provisions_with_refs": 0,
        "modified": False,
    }
    for node in graph:
        if not is_provision_node(node):
            continue
        stats["provisions_scanned"] += 1

        text_to_scan = _load_provision_text(node, xml_par_texts)
        if not text_to_scan:
            continue

        citations = extract_citations_from_text(text_to_scan)
        if not citations:
            continue

        # Count individual paragraph references (not citation objects)
        # so that total_citations_found >= total_citations_resolved
        # always holds.
        stats["citations_found"] += sum(len(c["paragraphs"]) for c in citations)

        all_refs: list[str] = []
        for cit in citations:
            resolved = resolve_citation(
                cit, self_prefix, abbrev_to_prefix, prefix_to_provisions
            )
            all_refs.extend(resolved)
            stats["citations_resolved"] += len(resolved)

        node_id = node.get("@id", "")
        all_refs = list(dict.fromkeys(r for r in all_refs if r != node_id))

        if not all_refs:
            continue

        node["estleg:references"] = [{"@id": r} for r in all_refs]
        stats["provisions_with_refs"] += 1
        stats["modified"] = True

    return stats


def _run_kov_body_pass(
    graph: list[dict],
    *,
    kov_act_lookup_by_number: dict[tuple[str, str], list[str]],
    issuer_registry: dict[str, tuple[str, str, str]],
) -> dict:
    """Run the KOV body-text reference pass over ``graph``.

    Mutates provision nodes by appending KOV-internal references to
    ``estleg:references``. Tracks which provision @ids were counted
    locally (no document mutation) and returns a stats fragment with
    ``citations_found``, ``citations_resolved``, ``provisions_with_refs``,
    ``counted_ids`` (frozenset of provision @ids that gained a Layer 2b
    ref this pass), and ``modified``.
    """
    stats = {
        "citations_found": 0,
        "citations_resolved": 0,
        "provisions_with_refs": 0,
        "counted_ids": frozenset(),
        "modified": False,
    }

    # Read the source act's municipality (may be None for laws or for
    # state regulations that aren't municipality-bound).
    source_municipality = None
    for node in graph:
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "estleg:MunicipalRegulation" in types:
            ebm = node.get("estleg:enactedByMunicipality")
            if isinstance(ebm, dict):
                source_municipality = ebm.get("@id")
            elif isinstance(ebm, str):
                source_municipality = ebm
            break

    counted_ids: set[str] = set()

    for prov_node in graph:
        if not is_provision_node(prov_node):
            continue
        text_parts: list[str] = []
        # estleg:legalText / estleg:summary may be plain strings or
        # {"@value": ..., "@language": "et"} objects depending on the
        # generator; jsonld_text unwraps both (and yields "" otherwise).
        lt = jsonld_text(prov_node.get("estleg:legalText"))
        if lt:
            text_parts.append(lt)
        sm = jsonld_text(prov_node.get("estleg:summary"))
        if sm:
            text_parts.append(sm)
        text = " ".join(text_parts)
        kov_refs = extract_kov_act_refs_from_text(text)
        if not kov_refs:
            continue

        # Treat every parsed KOV body-text ref as a "found" citation
        # to mirror the in-law branch convention.
        stats["citations_found"] += len(kov_refs)

        new_targets: list[str] = []
        for ref in kov_refs:
            target = resolve_kov_internal_act_ref(
                source_municipality=source_municipality,
                explicit_issuer=ref["issuer"],
                body_type=ref["body_type"],
                act_number=ref["act_number"],
                kov_act_lookup_by_number=kov_act_lookup_by_number,
                issuer_registry=issuer_registry,
            )
            if target is not None:
                new_targets.append(target)

        if not new_targets:
            continue

        # Append to existing references (preserving the in-law refs)
        # and dedupe.
        refs = prov_node.get("estleg:references")
        if refs is None:
            refs = []
            prov_node["estleg:references"] = refs
        elif isinstance(refs, dict):
            refs = [refs]
            prov_node["estleg:references"] = refs

        had_new = False
        for tgt in new_targets:
            if {"@id": tgt} not in refs:
                refs.append({"@id": tgt})
                had_new = True

        if had_new:
            stats["citations_resolved"] += len(new_targets)
            # Only count the provision the FIRST time a Layer 2b ref
            # lands on it (the in-law pass may have already counted it).
            node_id = prov_node.get("@id")
            if isinstance(node_id, str) and node_id not in counted_ids:
                counted_ids.add(node_id)
                stats["provisions_with_refs"] += 1
            stats["modified"] = True

    stats["counted_ids"] = frozenset(counted_ids)
    return stats


def _merge_pass_stats(base: dict, *passes: dict) -> bool:
    """Merge counter fields from one or more pass-stats dicts into
    ``base``. Returns True when ANY pass reported ``modified``.

    ``base`` is mutated in place. Numeric fields are summed; the
    ``modified`` flag from each pass is OR-ed; pass-specific fields
    (e.g. ``counted_ids``) are NOT propagated upward.
    """
    numeric_fields = (
        "provisions_scanned",
        "citations_found",
        "citations_resolved",
        "provisions_with_refs",
    )
    modified = False
    for pass_stats in passes:
        for field in numeric_fields:
            if field in pass_stats:
                base[field] = base.get(field, 0) + pass_stats[field]
        if pass_stats.get("modified"):
            modified = True
    return modified


def process_law_file(
    json_file: Path,
    abbrev_to_prefix: dict[str, list[str]],
    prefix_to_provisions: dict[str, dict[str, str]],
    iri_to_file: dict[str, Path],
    *,
    kov_act_lookup_by_number: dict[tuple[str, str], list[str]] | None = None,
    issuer_registry: dict[str, tuple[str, str, str]] | None = None,
    act_iri_to_prefix: dict[str, str] | None = None,
) -> dict:
    """
    Process a single law JSON-LD file to extract and add cross-references.

    Thin orchestrator: loads the doc, derives context, runs the in-law
    citation pass and the optional KOV body-text pass, merges stats,
    and persists the file when any pass mutated the graph.

    Returns statistics dict.
    """
    stats: dict = {
        "file": json_file.name,
        "provisions_scanned": 0,
        "citations_found": 0,
        "citations_resolved": 0,
        "provisions_with_refs": 0,
    }

    try:
        with open(json_file, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        stats["error"] = str(e)
        return stats

    graph = doc.get("@graph", [])
    if not graph:
        return stats

    self_prefix = _derive_self_prefix(graph, act_iri_to_prefix)
    if not self_prefix:
        return stats

    # Try to find corresponding XML file for richer text. The JSON-LD
    # filename pattern is {slug}_peep.json or {slug}_osa{N}_peep.json.
    slug = json_file.stem.replace("_peep", "")
    xml_slug = re.sub(r"_osa\d+$", "", slug)
    xml_path = DATA_DIR / f"{xml_slug}.xml"
    xml_par_texts: dict[str, str] = {}
    if xml_path.exists():
        xml_par_texts = collect_text_from_xml(xml_path)

    pass_results: list[dict] = []

    inlaw_stats = _run_inlaw_citation_pass(
        graph,
        self_prefix=self_prefix,
        abbrev_to_prefix=abbrev_to_prefix,
        prefix_to_provisions=prefix_to_provisions,
        xml_par_texts=xml_par_texts,
    )
    pass_results.append(inlaw_stats)

    if kov_act_lookup_by_number is not None:
        kov_stats = _run_kov_body_pass(
            graph,
            kov_act_lookup_by_number=kov_act_lookup_by_number,
            issuer_registry=issuer_registry or {},
        )
        pass_results.append(kov_stats)

    modified = _merge_pass_stats(stats, *pass_results)

    if modified:
        save_json(json_file, doc)

    return stats


def clear_existing_references() -> int:
    """
    Remove estleg:references from all provision nodes in *_peep.json files
    so the script is idempotent on re-run.
    """
    cleaned = 0
    for json_file in iter_peep_files():
        if json_file.name.startswith("riigikohus"):
            continue
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        modified = False
        for node in doc.get("@graph", []):
            if "estleg:references" in node:
                del node["estleg:references"]
                modified = True

        if modified:
            save_json(json_file, doc)
            cleaned += 1

    return cleaned


def _process_preamble_for_act(
    peep: Path,
    doc: dict,
    *,
    genitive_to_act_iri: dict[str, str],
    prefix_to_provisions: dict[str, dict[str, str]],
    act_iri_to_prefix: dict[str, str],
    state_reg_lookup: dict,
    kov_act_lookup: dict,
    law_title_to_iri: dict[str, str] | None = None,
) -> dict | None:
    """Run the Layer 2b preamble pass on a single peep file's @graph.

    Performs the per-peep clear-and-regenerate cycle:

      1. Detect existing prior-pass output (issuedUnder /
         implementsCitation / Citation_<shortid>_* nodes) BEFORE clearing,
         so a file whose parser output went from "some" to "none" still
         persists the cleared state to disk on re-run.
      2. Unconditionally clear those triples.
      3. Re-run extract_preamble_citations + resolver.
      4. Re-attach issuedUnder / implementsCitation / Citation nodes
         from the fresh parse.
      5. Save the file when EITHER fresh output exists OR something was
         cleared. The caller's bookkeeping uses the returned `had_output`
         flag (positive output only) for files_with_output counting; the
         `had_existing` flag is an internal save-gating concern.

    Returns None when the peep is malformed for the preamble pass
    (no @graph, no act_node, no preambleText). Returns a stats dict
    otherwise:

        triples_emitted        : count of issuedUnder + implementsCitation
        had_output             : True if fresh parse produced any triples
        had_existing           : True if any prior-pass triples were cleared
        citations_resolved     : count of citations that resolved
        citations_unresolved   : count of citations that did not resolve
        saved                  : True if the file was written to disk
    """
    graph = doc.get("@graph", [])
    if not graph:
        return None

    # Find the act node (owl:Ontology-typed). Skip the file if absent.
    act_node = None
    for n in graph:
        types = n.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "owl:Ontology" in types and n.get("@id"):
            act_node = n
            break
    if act_node is None:
        return None
    source_act_iri = act_node["@id"]

    preamble_text = act_node.get("estleg:preambleText")
    if not preamble_text:
        return None

    # Detect existing prior-pass output BEFORE clearing. Without this
    # flag, the save would be gated solely on positive fresh output —
    # so a parser tightening that drops an act's last citation would
    # leave the now-stale issuedUnder / Citation_* triples on disk.
    citation_prefix = (
        f"estleg:Citation_{source_act_iri.removeprefix('estleg:')}_"
    )
    had_existing = (
        "estleg:issuedUnder" in act_node
        or "estleg:implementsCitation" in act_node
        or any(
            isinstance(n.get("@id"), str) and n["@id"].startswith(citation_prefix)
            for n in graph
        )
    )

    # Idempotency: clear prior pass output before regenerating.
    act_node.pop("estleg:issuedUnder", None)
    act_node.pop("estleg:implementsCitation", None)
    graph[:] = [
        n for n in graph
        if not (isinstance(n.get("@id"), str)
                and n["@id"].startswith(citation_prefix))
    ]

    # Re-find act_node after the comprehension — safe because we only
    # filtered Citation_* nodes.
    for n in graph:
        if n.get("@id") == source_act_iri:
            act_node = n
            break

    citations = extract_preamble_citations(preamble_text)

    issued_under_set: set[str] = set()
    implements_citation_refs: list[dict] = []
    new_citation_nodes: list[dict] = []
    citations_resolved = 0
    citations_unresolved = 0

    for seq, cit in enumerate(citations, start=1):
        resolved = resolve_preamble_citation(
            cit,
            genitive_to_act_iri=genitive_to_act_iri,
            prefix_to_provisions=prefix_to_provisions,
            act_iri_to_prefix=act_iri_to_prefix,
            state_reg_lookup=state_reg_lookup,
            kov_act_lookup=kov_act_lookup,
            law_title_to_iri=law_title_to_iri,
        )
        if resolved is None:
            citations_unresolved += 1
            continue
        citations_resolved += 1
        target_iri, enabling_act_iri = resolved
        # Guard against an act citing ITSELF as its own enabling law
        # (#390): a few pre-1994 regs whose preamble names their own
        # title would otherwise emit issuedUnder -> self. The reified
        # Citation node below is still allowed — a self-citation to a
        # specific provision is meaningful — only the act-level
        # issuedUnder edge is suppressed.
        if enabling_act_iri != source_act_iri:
            issued_under_set.add(enabling_act_iri)

        # Emit a reified Citation node only when the citation has
        # paragraph or detail granularity.
        if cit.get("paragraphs") or cit.get("citationDetail"):
            citation_iri = build_citation_iri(source_act_iri, seq)
            citation_node = build_citation_node(
                iri=citation_iri,
                target_iri=target_iri,
                citation_detail=cit.get("citationDetail"),
                citation_text=cit.get("citationText"),
            )
            new_citation_nodes.append(citation_node)
            implements_citation_refs.append({"@id": citation_iri})

    triples_emitted = 0
    if issued_under_set:
        act_node["estleg:issuedUnder"] = [
            {"@id": iri} for iri in sorted(issued_under_set)
        ]
        triples_emitted += len(issued_under_set)

    if implements_citation_refs:
        act_node["estleg:implementsCitation"] = implements_citation_refs
        graph.extend(new_citation_nodes)
        triples_emitted += len(implements_citation_refs)

    had_output = bool(issued_under_set or implements_citation_refs)

    # Save when EITHER fresh output exists OR something was cleared.
    # The cleared-but-empty case must persist the cleared state to disk
    # so stale triples don't survive a parser tightening.
    saved = False
    if had_output or had_existing:
        save_json(peep, doc)
        saved = True

    return {
        "triples_emitted": triples_emitted,
        "had_output": had_output,
        "had_existing": had_existing,
        "citations_resolved": citations_resolved,
        "citations_unresolved": citations_unresolved,
        "saved": saved,
    }


def main() -> int:
    print("=" * 70)
    print("Estonian Legal Ontology - Extract Cross-Law References")
    print("=" * 70)

    _start = time.perf_counter()
    _files_processed: set[Path] = set()
    _files_processed_kov: set[Path] = set()
    _files_with_output: set[Path] = set()
    _files_with_output_kov: set[Path] = set()
    _triples = 0
    _triples_kov = 0
    _files_skipped = 0
    _skip_reasons: dict[str, int] = {}
    _failures: list[str] = []
    _unresolved = 0

    # Step 1: Clear existing references for idempotent re-run
    print("\n[1/5] Clearing existing estleg:references from law files...")
    cleaned = clear_existing_references()
    print(f"  Cleaned {cleaned} files")

    # Step 2: Build provision index
    print("\n[2/5] Building provision index from JSON-LD files...")
    (prefix_to_provisions, source_act_to_prefix, iri_to_file,
     prefix_to_act_iri, act_iri_to_prefix) = build_provision_index()
    total_provisions = sum(len(v) for v in prefix_to_provisions.values())
    print(f"  Found {len(prefix_to_provisions)} law prefixes with {total_provisions} provisions")
    print(f"  Source act mappings: {len(source_act_to_prefix)}")

    # Layer 2b: canonical lookups for the preamble + body-text passes
    genitive_to_act_iri = build_genitive_to_act_iri(
        source_act_to_prefix=source_act_to_prefix,
        prefix_to_act_iri=prefix_to_act_iri,
    )
    # #390 corpus-title fallback: every law's nominative sourceAct → act
    # IRI, resolving ~236 more law genitives than FULLNAME_GENITIVE alone.
    law_title_to_iri = build_law_title_to_iri(
        source_act_to_prefix=source_act_to_prefix,
        prefix_to_act_iri=prefix_to_act_iri,
    )
    print(f"  law-title fallback: {len(law_title_to_iri)} titles")

    riik_root = KRR_DIR / "regulations" / "riik"
    kov_root = KRR_DIR / "regulations" / "kov"
    issuers_path = KRR_DIR / "issuers_kov_peep.json"
    state_reg_lookup = build_state_regulation_lookup(riik_root, DATA_DIR)
    kov_act_lookup = build_kov_act_lookup(kov_root, DATA_DIR, issuers_path)
    kov_act_lookup_by_number = build_kov_act_lookup_by_number(kov_root)
    issuer_registry = build_issuer_registry(issuers_path)
    print(f"  state-reg lookup: {len(state_reg_lookup)} entries")
    print(f"  KOV act lookup (date-keyed): {len(kov_act_lookup)} entries")
    print(f"  KOV act lookup (number-only): {len(kov_act_lookup_by_number)} keys")

    # Step 3: Build abbreviation mapping
    print("\n[3/5] Building abbreviation-to-prefix mapping...")
    abbrev_to_prefix = build_abbreviation_to_prefix(source_act_to_prefix)
    print(f"  Mapped {len(abbrev_to_prefix)} abbreviations to IRI prefixes")
    # Built once and reused for the cross_references_report.json mapping below
    # so the diagnostic print and the report can never diverge or regress
    # independently on the list-valued #299 contract.
    abbreviation_mapping_report = build_abbreviation_mapping_report(
        abbrev_to_prefix, prefix_to_provisions
    )
    for abbrev, info in abbreviation_mapping_report.items():
        print(f"    {abbrev} -> {info['iri_prefixes']} ({info['provision_count']} provisions)")

    # Step 4: Process each law file
    print("\n[4/5] Processing law files for cross-references...")
    law_files = iter_peep_files()
    # Exclude riigikohus files and non-law files
    law_files = [f for f in law_files if not f.name.startswith("riigikohus")]

    all_stats: list[dict] = []
    total_citations = 0
    total_resolved = 0
    total_with_refs = 0
    files_modified = 0

    for i, json_file in enumerate(law_files, 1):
        stats = process_law_file(
            json_file, abbrev_to_prefix, prefix_to_provisions, iri_to_file,
            kov_act_lookup_by_number=kov_act_lookup_by_number,
            issuer_registry=issuer_registry,
            act_iri_to_prefix=act_iri_to_prefix,
        )
        all_stats.append(stats)

        # Accumulate totals unconditionally (#81: include every file)
        total_citations += stats.get("citations_found", 0)
        total_resolved += stats.get("citations_resolved", 0)
        total_with_refs += stats.get("provisions_with_refs", 0)
        if stats.get("provisions_with_refs", 0) > 0:
            files_modified += 1

        # Coverage accumulators — set-based dedup avoids double-counting
        # files that also produce output in the preamble pass below.
        is_kov = "regulations/kov/" in str(json_file)
        _files_processed.add(json_file)
        if is_kov:
            _files_processed_kov.add(json_file)
        if stats.get("provisions_with_refs", 0) > 0:
            _files_with_output.add(json_file)
            if is_kov:
                _files_with_output_kov.add(json_file)
        delta = stats.get("citations_resolved", 0)
        _triples += delta
        if is_kov:
            _triples_kov += delta
        if stats.get("error"):
            _failures.append(f"{json_file.name}: {stats['error']}")

        if i % 50 == 0 or i == len(law_files):
            print(f"  Processed {i}/{len(law_files)} files "
                  f"(refs found so far: {total_resolved})")

    # Layer 2b: preamble pass — issuedUnder + implementsCitation + Citation
    print("\nLayer 2b: scanning preambles for issuedUnder citations...")
    preamble_files_processed: set[Path] = set()
    preamble_files_with_output: set[Path] = set()
    preamble_files_kov_processed: set[Path] = set()
    preamble_files_kov_with_output: set[Path] = set()
    preamble_triples_emitted = 0
    preamble_triples_emitted_kov = 0
    preamble_citations_resolved = 0
    preamble_citations_unresolved = 0

    for peep in iter_peep_files():
        is_kov = "regulations/kov/" in str(peep)
        try:
            with open(peep, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        result = _process_preamble_for_act(
            peep,
            doc,
            genitive_to_act_iri=genitive_to_act_iri,
            prefix_to_provisions=prefix_to_provisions,
            act_iri_to_prefix=act_iri_to_prefix,
            state_reg_lookup=state_reg_lookup,
            kov_act_lookup=kov_act_lookup,
            law_title_to_iri=law_title_to_iri,
        )
        if result is None:
            # Peep had no @graph, no act_node, or no preambleText —
            # not a candidate for the preamble pass.
            continue

        # Files with a preambleText count as processed even when the
        # parser produced zero citations (matches prior behaviour).
        preamble_files_processed.add(peep)
        if is_kov:
            preamble_files_kov_processed.add(peep)

        preamble_triples_emitted += result["triples_emitted"]
        if is_kov:
            preamble_triples_emitted_kov += result["triples_emitted"]
        preamble_citations_resolved += result["citations_resolved"]
        preamble_citations_unresolved += result["citations_unresolved"]

        # files_with_output only counts files whose fresh parse produced
        # positive output. The cleared-but-empty case (had_existing but
        # not had_output) modifies the file but contributes zero triples
        # to the report — that's the correct semantic.
        if result["had_output"]:
            preamble_files_with_output.add(peep)
            if is_kov:
                preamble_files_kov_with_output.add(peep)

    print(f"  preamble files processed: {len(preamble_files_processed)} "
          f"(KOV: {len(preamble_files_kov_processed)})")
    print(f"  preamble files with output: {len(preamble_files_with_output)} "
          f"(KOV: {len(preamble_files_kov_with_output)})")
    print(f"  preamble triples emitted: {preamble_triples_emitted} "
          f"(KOV: {preamble_triples_emitted_kov})")
    print(f"  preamble citations resolved/unresolved: "
          f"{preamble_citations_resolved}/{preamble_citations_unresolved}")

    # Fold the preamble pass into the run-level coverage accumulators.
    # The set unions deduplicate against the body-text pass above, so a
    # file emitting from both passes counts once in files_with_output.
    _files_processed |= preamble_files_processed
    _files_processed_kov |= preamble_files_kov_processed
    _files_with_output |= preamble_files_with_output
    _files_with_output_kov |= preamble_files_kov_with_output
    _triples += preamble_triples_emitted
    _triples_kov += preamble_triples_emitted_kov
    _unresolved += preamble_citations_unresolved

    # Step 5: Generate report
    print("\n[5/5] Generating cross-references report...")
    total_unresolved = total_citations - total_resolved
    report = {
        "generated": f"{BUILD_EVALUATION_DATE}T00:00:00+00:00",  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "summary": {
            "total_law_files": len(law_files),
            "files_with_references": files_modified,
            "total_citations_found": total_citations,
            "total_citations_resolved": total_resolved,
            "total_citations_unresolved": total_unresolved,
            "total_provisions_with_refs": total_with_refs,
            "abbreviations_mapped": len(abbrev_to_prefix),
            "law_prefixes_indexed": len(prefix_to_provisions),
            "total_provisions_indexed": total_provisions,
        },
        "abbreviation_mapping": abbreviation_mapping_report,
        "per_file_stats": all_stats,
    }

    report_path = KRR_DIR / "cross_references_report.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Law files processed:         {len(law_files)}")
    print(f"  Files with references added:  {files_modified}")
    print(f"  Total citations found:        {total_citations}")
    print(f"  Total citations resolved:     {total_resolved}")
    print(f"  Total citations unresolved:   {total_unresolved}")
    print(f"  Provisions with references:   {total_with_refs}")
    print(f"  Report: {report_path}")
    print("=" * 70)

    # Coverage report (KOV-aware schema, shared across the 6 KOV pipelines)
    all_input_files = list(iter_peep_files())
    _kov_files = [p for p in all_input_files
                  if "regulations/kov/" in str(p)]

    _wall, _rate, _peak_mb = measure_runtime(_start, len(_files_processed))
    out_path = KRR_DIR / "reports" / "kov" / "extract_cross_references_coverage.json"
    write_coverage_report(
        CoverageReport(
            pipeline="extract_cross_references",
            run_timestamp=PINNED_RUN_TIMESTAMP,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
            pipeline_version=resolve_pipeline_version(),
            input_files_total=len(all_input_files),
            input_files_kov=len(_kov_files),
            files_processed=len(_files_processed),
            files_processed_kov=len(_files_processed_kov),
            files_with_output=len(_files_with_output),
            files_with_output_kov=len(_files_with_output_kov),
            files_skipped=_files_skipped,
            skip_reasons=_skip_reasons,
            triples_emitted=_triples,
            triples_emitted_kov=_triples_kov,
            unresolved_references=_unresolved,
            wall_time_seconds=round(_wall, 2),
            items_per_second=round(_rate, 2),
            peak_memory_mb=round(_peak_mb, 1),
            error_count=len(_failures),
            failure_samples=_failures,
        ),
        out_path,
    )
    print(f"\nCoverage report: {out_path}")
    print(f"  input_files_total: {len(all_input_files)} (KOV: {len(_kov_files)})")
    print(f"  files_with_output: {len(_files_with_output)} "
          f"(KOV: {len(_files_with_output_kov)})")
    print(f"  triples_emitted: {_triples} (KOV: {_triples_kov})")

    # Gate check — fails the run when the KOV side produced nothing
    # despite the corpus being present.
    if len(_kov_files) >= 11000 and len(_files_with_output_kov) == 0:
        print("\nGATE FAIL: KOV files were processed but none produced output.")
        return 1
    if _triples_kov == 0 and len(_kov_files) >= 11000:
        print("\nGATE FAIL: zero KOV triples emitted.")
        return 1
    print("\nGATE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
