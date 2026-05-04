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
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from estleg_common import (
    CONTEXT,
    FULLNAME_GENITIVE,
    KNOWN_ABBREVIATIONS,
    PAR_SUFFIX,
    iter_peep_files,
    save_json,
    sanitize_id,
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
    dict[str, str],
    dict[str, Path],
    dict[str, str],   # prefix → act @id
    dict[str, str],   # act @id → prefix (reverse, for resolver use)
]:
    """
    Scan all *_peep.json files to build:
    1. prefix_to_provisions: {prefix: {par_number: full_iri}} e.g. {"Karistusseadustik": {"121": "estleg:KARIST_2_Par_121"}}
    2. source_act_to_prefix: {source_act_name: prefix} e.g. {"Karistusseadustik": "Karistusseadustik"}
    3. iri_to_file: {iri: filepath} mapping each provision IRI to its containing file
    4. prefix_to_act_iri: {prefix: act_@id} e.g. {"KOKS": "estleg:KOKS_Map_2026"}
    5. act_iri_to_prefix: {act_@id: prefix} (reverse — required by Task 5
       resolver because ``act_iri.split("_")[0]`` breaks for 34 corpus
       acts with multi-segment prefixes like ``KARIST_2_Map_2026``).
    """
    prefix_to_provisions: dict[str, dict[str, str]] = {}
    source_act_to_prefix: dict[str, str] = {}
    iri_to_file: dict[str, Path] = {}
    prefix_to_act_iri: dict[str, str] = {}
    act_iri_to_prefix: dict[str, str] = {}

    # Scan all JSON-LD law files (not riigikohus, not schema/index files)
    for json_file in iter_peep_files(include_kov=False):  # DEFERRED to Layer 2b
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

        # Detect the prefix used in this file by scanning provision nodes
        file_prefix = None
        source_act = None
        for node in graph:
            node_id = node.get("@id", "")
            if "_Par_" in node_id and node_id.startswith("estleg:"):
                # Extract prefix: "estleg:KARIST_2_Par_121" -> "Karistusseadustik"
                local = node_id[len("estleg:"):]
                prefix_part = local.split("_Par_")[0]
                if file_prefix is None:
                    file_prefix = prefix_part
                # Extract paragraph number
                par_part = local.split("_Par_")[1]
                # Store the provision
                if file_prefix not in prefix_to_provisions:
                    prefix_to_provisions[file_prefix] = {}
                prefix_to_provisions[file_prefix][par_part] = node_id
                iri_to_file[node_id] = json_file

                # Get sourceAct
                if source_act is None:
                    source_act = node.get("estleg:sourceAct", "")

        if file_prefix and source_act:
            source_act_to_prefix[source_act] = file_prefix

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
    source_act_to_prefix: dict[str, str],
) -> dict[str, str]:
    """
    Map law abbreviations (KarS, VÕS, etc.) to the prefix used in provision IRIs.

    Returns: {abbreviation: iri_prefix} e.g. {"KarS": "Karistusseadustik"}
    """
    abbrev_to_prefix: dict[str, str] = {}

    for abbrev, full_name in KNOWN_ABBREVIATIONS.items():
        if full_name in source_act_to_prefix:
            abbrev_to_prefix[abbrev] = source_act_to_prefix[full_name]

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


def build_genitive_to_act_iri(
    source_act_to_prefix: dict[str, str],
    prefix_to_act_iri: dict[str, str],
) -> dict[str, str]:
    """Build a {genitive_law_name (lowercased) → act @id} lookup by
    chaining FULLNAME_GENITIVE → abbrev → source_act_to_prefix →
    prefix_to_act_iri.

    The returned keys are lowercased so the parser's case-insensitive
    output ('Kohaliku omavalitsuse korralduse seaduse' or 'kohaliku
    omavalitsuse korralduse seaduse') resolves uniformly via .lower().

    Entries that fail at any link in the chain (genitive → abbrev →
    full_name → prefix → act_iri) are silently skipped — Layer 2b
    accepts that some genitive forms recognise laws without a corpus
    peep file.
    """
    out: dict[str, str] = {}
    for genitive, abbrev in FULLNAME_GENITIVE.items():
        full_name = KNOWN_ABBREVIATIONS.get(abbrev)
        if full_name is None:
            continue
        prefix = source_act_to_prefix.get(full_name)
        if prefix is None:
            continue
        act_iri = prefix_to_act_iri.get(prefix)
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
                    label = n.get("rdfs:label")
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
        label = node.get("rdfs:label")
        muni = node.get("estleg:enactedByMunicipality")
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
) -> tuple[str, str] | None:
    """Resolve a preamble citation to (citation_target_iri, enabling_act_iri).

    citation_target_iri:
        - paragraph-level provision IRI for law citations with §
        - act-level IRI otherwise

    enabling_act_iri:
        - ALWAYS act-level. Layer 2b's issuedUnder semantic is "act
          enacted under THIS act" — the range is Act, not LegalProvision.

    Returns None when the citation can't be resolved against any lookup.
    """
    form = cit.get("form")

    if form == "law-genitive":
        genitive = cit.get("law_ref", "").lower()
        act_iri = genitive_to_act_iri.get(genitive)
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
        if issuer_full and issuer_full.strip():
            # Issuer prefix may absorb leading capitalised non-place
            # words (e.g. sentence-initial "Vastavalt", "Lähtudes",
            # "Käesolevaga"). Place names in Estonian KOV issuer labels
            # are typically the single token immediately preceding the
            # body word (hyphenated compounds like "Lääne-Nigula" remain
            # one token). Take only that trailing token to avoid
            # contaminating the lookup key with adverbs.
            issuer_tokens = issuer_full.strip().split()
            place_name = issuer_tokens[-1] if issuer_tokens else ""
            raw_label = (place_name + " " + body_canon).strip()
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


def extract_citations_from_text(text: str) -> list[dict]:
    """
    Parse text for Estonian legal citation patterns.

    Returns list of dicts with keys:
      - law_ref: abbreviation or full name reference
      - paragraphs: list of paragraph numbers (strings)
      - is_self_ref: True if "käesoleva seaduse" pattern
    """
    citations: list[dict] = []
    if not text:
        return citations

    # Known abbreviation list for regex alternation
    abbrevs = "|".join(re.escape(a) for a in sorted(KNOWN_ABBREVIATIONS.keys(), key=len, reverse=True))

    # Pattern 1: Abbreviation + § + number(s)
    # KarS § 121, KarS §-s 121, KarS § 121 lg 2 p 3
    # Also handles: KarS §-de 208-210, KarS §§ 1-10
    pat_abbrev = re.compile(
        rf"({abbrevs})\s*{PAR_SUFFIX}\s*(\d+(?:\s*[\-–]\s*\d+)?)",
        re.UNICODE,
    )

    for m in pat_abbrev.finditer(text):
        abbrev = m.group(1)
        par_range = m.group(2).strip()
        paragraphs = _expand_par_range(par_range)
        citations.append({
            "law_ref": abbrev,
            "paragraphs": paragraphs,
            "is_self_ref": False,
        })

    # Pattern 2: käesoleva seaduse § N (self-reference)
    pat_self = re.compile(
        rf"k[äa]esoleva\s+seadus(?:e|tiku)?\s*{PAR_SUFFIX}\s*(\d+(?:\s*[\-–]\s*\d+)?)",
        re.UNICODE | re.IGNORECASE,
    )
    for m in pat_self.finditer(text):
        par_range = m.group(1).strip()
        paragraphs = _expand_par_range(par_range)
        citations.append({
            "law_ref": "__SELF__",
            "paragraphs": paragraphs,
            "is_self_ref": True,
        })

    # Pattern 3: Full name in genitive form + § + number
    # "tsiviilseadustiku üldosa seaduse § 67"
    genitive_names = "|".join(
        re.escape(g) for g in sorted(FULLNAME_GENITIVE.keys(), key=len, reverse=True)
    )
    if genitive_names:
        pat_fullname = re.compile(
            rf"({genitive_names})\s*{PAR_SUFFIX}\s*(\d+(?:\s*[\-–]\s*\d+)?)",
            re.UNICODE | re.IGNORECASE,
        )
        for m in pat_fullname.finditer(text):
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
    """
    words = law.split()
    while words and words[0].lower().rstrip(",.;:") in _PREAMBLE_LICENSE_PREFIXES:
        words.pop(0)
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
    """Expand '208-210' into ['208', '209', '210']. Single numbers return as-is."""
    par_range = par_range.replace("–", "-").replace("‑", "-")
    if "-" in par_range:
        parts = par_range.split("-", 1)
        try:
            start = int(parts[0].strip())
            end = int(parts[1].strip())
            if end - start <= 50:  # sanity limit
                return [str(n) for n in range(start, end + 1)]
        except ValueError:
            pass
    # Single number or failed range
    clean = re.sub(r"[^\d]", "", par_range)
    return [clean] if clean else []


def resolve_citation(
    citation: dict,
    self_prefix: str,
    abbrev_to_prefix: dict[str, str],
    prefix_to_provisions: dict[str, dict[str, str]],
) -> list[str]:
    """
    Resolve a citation dict to a list of existing provision IRIs.

    Returns list of IRI strings (e.g. ["estleg:KARIST_2_Par_121"]).
    """
    resolved: list[str] = []

    # Determine the IRI prefix for this law reference
    if citation["is_self_ref"]:
        prefix = self_prefix
    else:
        law_ref = citation["law_ref"]
        prefix = abbrev_to_prefix.get(law_ref)
        if not prefix:
            return []

    provisions = prefix_to_provisions.get(prefix, {})
    if not provisions:
        return []

    for par_num in citation["paragraphs"]:
        # Try exact match first
        if par_num in provisions:
            resolved.append(provisions[par_num])
        else:
            # Try stripping leading zeros
            stripped = par_num.lstrip("0") or "0"
            if stripped in provisions:
                resolved.append(provisions[stripped])

    return resolved


def collect_text_from_xml(xml_path: Path) -> dict[str, str]:
    """
    Parse a Riigi Teataja XML and extract text content per paragraph.

    Returns: {paragraph_number: full_text}
    """
    par_texts: dict[str, str] = {}
    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except (ET.ParseError, OSError):
        return par_texts

    for el in root.iter():
        if ln(el.tag) == "paragrahv":
            # Find paragraph number
            par_nr = None
            for child in el:
                if ln(child.tag) == "paragrahvNr" and child.text:
                    par_nr = child.text.strip()
                    break
            if not par_nr:
                continue
            # Collect all text within this paragraph
            full_text = " ".join(el.itertext())
            full_text = re.sub(r"\s+", " ", full_text).strip()
            par_nr_clean = re.sub(r"[^\d]", "", par_nr)
            if par_nr_clean:
                par_texts[par_nr_clean] = full_text

    return par_texts


def process_law_file(
    json_file: Path,
    abbrev_to_prefix: dict[str, str],
    prefix_to_provisions: dict[str, dict[str, str]],
    source_act_to_prefix: dict[str, str],
) -> dict:
    """
    Process a single law JSON-LD file to extract and add cross-references.

    Returns statistics dict.
    """
    stats = {
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

    # Determine this file's prefix and source act for self-references
    self_prefix = None
    self_source_act = None
    for node in graph:
        node_id = node.get("@id", "")
        if "_Par_" in node_id and node_id.startswith("estleg:"):
            local = node_id[len("estleg:"):]
            self_prefix = local.split("_Par_")[0]
            self_source_act = node.get("estleg:sourceAct", "")
            break

    if not self_prefix:
        return stats

    # Try to find corresponding XML file for richer text
    # The JSON-LD filename pattern is {slug}_peep.json or {slug}_osa{N}_peep.json
    slug = json_file.stem.replace("_peep", "")
    # Remove _osa{N} suffix for XML lookup
    xml_slug = re.sub(r"_osa\d+$", "", slug)
    xml_path = DATA_DIR / f"{xml_slug}.xml"
    xml_par_texts: dict[str, str] = {}
    if xml_path.exists():
        xml_par_texts = collect_text_from_xml(xml_path)

    modified = False
    for node in graph:
        node_id = node.get("@id", "")
        if "_Par_" not in node_id:
            continue

        stats["provisions_scanned"] += 1

        # Get text to scan: prefer XML text, fall back to JSON-LD summary
        local = node_id[len("estleg:"):]
        par_num = local.split("_Par_")[1]
        text_to_scan = xml_par_texts.get(par_num, "")
        summary = node.get("estleg:summary", "")
        if summary:
            text_to_scan = text_to_scan + " " + summary if text_to_scan else summary

        if not text_to_scan:
            continue

        # Extract and resolve citations
        citations = extract_citations_from_text(text_to_scan)
        if not citations:
            continue

        # Count individual paragraph references (not citation objects) so
        # that total_citations_found >= total_citations_resolved always holds.
        stats["citations_found"] += sum(len(c["paragraphs"]) for c in citations)

        all_refs: list[str] = []
        for cit in citations:
            resolved = resolve_citation(
                cit, self_prefix, abbrev_to_prefix, prefix_to_provisions
            )
            all_refs.extend(resolved)
            stats["citations_resolved"] += len(resolved)

        # Deduplicate and remove self-links
        all_refs = list(dict.fromkeys(r for r in all_refs if r != node_id))

        if not all_refs:
            continue

        # Add estleg:references (using IRI references, always as list)
        ref_iris = [{"@id": r} for r in all_refs]
        node["estleg:references"] = ref_iris

        stats["provisions_with_refs"] += 1
        modified = True

    if modified:
        save_json(json_file, doc)

    return stats


def clear_existing_references() -> int:
    """
    Remove estleg:references from all provision nodes in *_peep.json files
    so the script is idempotent on re-run.
    """
    cleaned = 0
    for json_file in iter_peep_files(include_kov=False):  # DEFERRED to Layer 2b
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


def main() -> None:
    print("=" * 70)
    print("Estonian Legal Ontology - Extract Cross-Law References")
    print("=" * 70)

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

    # Step 3: Build abbreviation mapping
    print("\n[3/5] Building abbreviation-to-prefix mapping...")
    abbrev_to_prefix = build_abbreviation_to_prefix(source_act_to_prefix)
    print(f"  Mapped {len(abbrev_to_prefix)} abbreviations to IRI prefixes")
    for abbrev, prefix in sorted(abbrev_to_prefix.items()):
        provisions = prefix_to_provisions.get(prefix, {})
        print(f"    {abbrev} -> {prefix} ({len(provisions)} provisions)")

    # Step 4: Process each law file
    print("\n[4/5] Processing law files for cross-references...")
    law_files = iter_peep_files(include_kov=False)  # DEFERRED to Layer 2b
    # Exclude riigikohus files and non-law files
    law_files = [f for f in law_files if not f.name.startswith("riigikohus")]

    all_stats: list[dict] = []
    total_citations = 0
    total_resolved = 0
    total_with_refs = 0
    files_modified = 0

    for i, json_file in enumerate(law_files, 1):
        stats = process_law_file(
            json_file, abbrev_to_prefix, prefix_to_provisions, source_act_to_prefix
        )
        all_stats.append(stats)

        # Accumulate totals unconditionally (#81: include every file)
        total_citations += stats.get("citations_found", 0)
        total_resolved += stats.get("citations_resolved", 0)
        total_with_refs += stats.get("provisions_with_refs", 0)
        if stats.get("provisions_with_refs", 0) > 0:
            files_modified += 1

        if i % 50 == 0 or i == len(law_files):
            print(f"  Processed {i}/{len(law_files)} files "
                  f"(refs found so far: {total_resolved})")

    # Step 5: Generate report
    print("\n[5/5] Generating cross-references report...")
    total_unresolved = total_citations - total_resolved
    report = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
        "abbreviation_mapping": {
            abbrev: {
                "iri_prefix": prefix,
                "provision_count": len(prefix_to_provisions.get(prefix, {})),
            }
            for abbrev, prefix in sorted(abbrev_to_prefix.items())
        },
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


if __name__ == "__main__":
    main()
