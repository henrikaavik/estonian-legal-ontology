#!/usr/bin/env python3
"""Project committed peeps into a star-schema CSV/Parquet dump (#559, #716).

The pandas/R analytics path should not have to flatten 262 MB of JSON-LD.
This exporter walks ordinary ``*_peep.json`` files (never
``combined_ontology.jsonld``) and writes ten tables:

* ``laws.csv`` — enacted-law act roots
* ``provisions.csv`` — § and lõige rows of laws and regulations (legalText
  truncated to 2000 chars), with temporal columns filled from the provision
  version layer (``provision_versions/``) and the act
* ``citations.csv`` — ``estleg:references`` / ``estleg:referencedBy`` edges
* ``sanctions.csv`` — sanction sidecar nodes with the subject, the act and
  EUR-normalised amounts
* ``court_decisions.csv`` — Riigikohus decision metadata (no full text)
* ``regulations.csv`` — state and KOV regulations (issuer, municipality EHAK,
  temporal status, enabling-provision stamps of #712)
* ``drafts.csv`` — EIS drafts (phase, initiator, amendsLaw, enactedAs)
* ``eu_acts.csv`` — EUR-Lex acts (CELEX, type, in force, deadline, counts)
* ``institutions.csv`` — institution registry (registrikood, X-tee, succession)
* ``competences.csv`` — provision × institution with the competence type (#718)

Column order is a consumer contract: columns are only ever appended.

Two presets select the input:

* ``--sample`` (the default) — a fixed, deterministic, representative subset
  (5 laws + their sanction sidecars, 2 state and 2 KOV issuers, 200 drafts,
  200 EU acts, 2 Riigikohus years, every institution). This is what
  ``krr_outputs/exports/`` commits.
* ``--full`` — every corpus; write it to a directory outside the repo. It is
  never committed.

A further, opt-in table (``--kov-legality``, #712) is the KOV legality view:

* ``kov_legality.csv`` — one row per municipal regulation × enabling provision
  it cites, with the municipality / county (EHAK codes), the issuing body and
  (for abolished municipalities) the pre-merger ``HistoricalMunicipality``, the
  ``ProvisionVersion`` in force at the act's entry into force, and whether that
  enabling provision has been rewritten since (see
  ``derive_kov_enabling_staleness``). It loads the ``provision_versions/``
  surface (~3 s), so it is not part of the star-schema run.

CSV uses the stdlib ``csv`` module. Parquet is written only when ``pyarrow``
imports (and never for ``--sample``); otherwise a one-line stderr note is
printed and CSV still ships. ``pyarrow`` is not a project dependency.

    python3 scripts/serialize_tabular.py --sample          # krr_outputs/exports
    python3 scripts/serialize_tabular.py --full --out /tmp/estleg-tabular
    # per-corpus glob overrides on top of the sample preset
    python3 scripts/serialize_tabular.py --out /tmp/estleg-tabular \\
      --laws-glob '*_peep.json' \\
      --sanctions-glob 'sanctions/sanctions_*.json' \\
      --court-glob 'riigikohus/riigikohus_*_peep.json'

    # KOV legality: committed sample (two issuers of Mulgi vald) / full corpus
    python3 scripts/serialize_tabular.py --kov-legality --out krr_outputs/exports
    python3 scripts/serialize_tabular.py --kov-legality --out /tmp/estleg-tabular \\
      --kov-glob 'regulations/kov/*/*_peep.json'
    # ... or one municipality (EHAK code), e.g. Mulgi vald
    python3 scripts/serialize_tabular.py --kov-legality --out /tmp/estleg-tabular \\
      --kov-glob 'regulations/kov/*/*_peep.json' --kov-municipality 0480
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from estleg.estleg_common import (
    KRR_DIR,
    NS,
    REPO_ROOT,
    act_prefix_from_iri,
    jsonld_text,
)

ESTLEG_PREFIX = "estleg:"
LEGAL_TEXT_MAX = 2000
LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"
#: Separator for multi-valued cells (IRIs never contain it).
LIST_SEP = ";"

SKIP_FILENAMES = frozenset(
    {
        "combined_ontology.jsonld",
        "similarity_index.json",
    }
)

# ── #716 sample preset (committed under krr_outputs/exports/) ────────────────
# Fixed, sorted inputs so two runs are byte-identical. The laws were chosen to
# cover sanctions in fine units and EUR, competences (AKI, PPA, KOV), the KOV
# enabling law and subject (targetGroup / dutyHolder) stamps; the issuers to
# keep provisions.csv under 1 MB (legalText is the bulk of every row).
SAMPLE_LAW_SLUGS: tuple[str, ...] = (
    "abipolitseiniku_seadus",
    "avaliku_teabe_seadus",
    "isikuandmete_kaitse_seadus",
    "kohaliku_omavalitsuse_korralduse_seadus",
    "tootajate_usaldusisiku_seadus",
)
#: State issuers are matched on the root's ``estleg:issuer`` text.
SAMPLE_STATE_ISSUERS: tuple[str, ...] = (
    "Rahvastikuminister",
    "Sotsiaalkaitseminister ning tervise- ja tööminister",
)
#: KOV issuers are directories under ``regulations/kov/`` (one abolished, one current).
SAMPLE_KOV_ISSUERS: tuple[str, ...] = ("abja_vallavolikogu", "kastre_vallavalitsus")
SAMPLE_COURT_YEARS: tuple[int, ...] = (2020, 2024)
SAMPLE_DRAFTS = 200
SAMPLE_EU_ACTS = 200
#: Seed of the hash-ordered draft / EU act draw (stable across Python versions).
SAMPLE_SEED = "estleg-716"

DEFAULT_LAWS_GLOBS: tuple[str, ...] = tuple(f"{slug}_peep.json" for slug in SAMPLE_LAW_SLUGS)
DEFAULT_SANCTIONS_GLOBS: tuple[str, ...] = tuple(
    f"sanctions/sanctions_{slug}.json" for slug in SAMPLE_LAW_SLUGS
)
DEFAULT_COURT_GLOBS: tuple[str, ...] = tuple(
    f"riigikohus/riigikohus_{year}_peep.json" for year in SAMPLE_COURT_YEARS
)
STATE_REGULATION_GLOB = "regulations/riik/*_peep.json"
DEFAULT_REGULATION_GLOBS: tuple[str, ...] = (
    STATE_REGULATION_GLOB,
    *(f"regulations/kov/{issuer}/*_peep.json" for issuer in SAMPLE_KOV_ISSUERS),
)
DEFAULT_DRAFT_GLOBS: tuple[str, ...] = ("eelnoud/eelnoud_*_peep.json",)
DEFAULT_EURLEX_GLOBS: tuple[str, ...] = ("eurlex/eurlex_*_peep.json",)
DEFAULT_INSTITUTION_GLOBS: tuple[str, ...] = ("institutions/institution_*.json",)
VERSION_DIRNAME = "provision_versions"

LAW_COLUMNS: tuple[str, ...] = (
    "iri",
    "slug",
    "title",
    "abbreviation",
    "temporalStatus",
    "kehtiv",
)
PROVISION_COLUMNS: tuple[str, ...] = (
    "iri",
    "act",
    "paragrahv",
    "subsection",
    "legalText",
    "in_force",
    "temporalStatus",
    "valid_from",
    "valid_to",
    # #716 (append-only)
    "current_version",
)
CITATION_COLUMNS: tuple[str, ...] = ("source", "target", "predicate")
SANCTION_COLUMNS: tuple[str, ...] = (
    "iri",
    "provision",
    "type",
    "min",
    "max",
    "unit",
    # #716 (append-only)
    "act",
    "subject",
    "subject_source",
    "subject_person_type",
    "currency",
    "amount_eur_min",
    "amount_eur_max",
    "statutory_default",
    "label",
)
COURT_COLUMNS: tuple[str, ...] = ("iri", "caseNumber", "date", "ecli", "chamber")
REGULATION_COLUMNS: tuple[str, ...] = (
    "iri",
    "rt_id",
    "global_id",
    "title",
    "level",
    "regulation_type",
    "document_type",
    "act_number",
    "issuer",
    "issuer_iri",
    "municipality_ehak",
    "municipality",
    "county_code",
    "county",
    "municipality_status",
    "historical_municipality",
    "kehtiv",
    "temporal_status",
    "entry_into_force",
    "repeal_date",
    "last_amended",
    "publication_date",
    "issued_under",
    "enabling_provisions",
    "enabling_provision_count",
    "enabling_provision_outdated",
    "earliest_superseding_date",
    "provision_count",
    "source_url",
)
DRAFT_COLUMNS: tuple[str, ...] = (
    "iri",
    "eis_number",
    "title",
    "phase",
    "draft_type",
    "initiator",
    "publication_date",
    "change_type",
    "amends_law",
    "enacted_as",
    "affected_laws",
    "eis_link",
)
EU_ACT_COLUMNS: tuple[str, ...] = (
    "iri",
    "celex",
    "title",
    "doc_type",
    "document_date",
    "in_force",
    "institution",
    "eli",
    "transposition_deadline",
    "transposed_by_count",
    "subjects_count",
    "transposed_by",
    "estonia_relevant",
    "source_url",
)
INSTITUTION_COLUMNS: tuple[str, ...] = (
    "iri",
    "label",
    "type",
    "registrikood",
    "xtee_member_code",
    "same_as",
    "predecessor",
    "successor",
    "replaced_by",
    "valid_from",
    "valid_to",
    "competence_count",
)
COMPETENCE_COLUMNS: tuple[str, ...] = (
    "provision",
    "institution",
    "competence_type",
    "competence",
    "competence_area",
    "granted_by",
)

# #712 KOV legality view. Stable consumer contract: column order is part of
# the contract (tests pin it); add new columns at the end only.
KOV_LEGALITY_TABLE = "kov_legality"
DEFAULT_KOV_GLOBS: tuple[str, ...] = (
    "regulations/kov/abja_vallavolikogu/*_peep.json",
    "regulations/kov/mulgi_vallavolikogu/*_peep.json",
)
KOV_LEGALITY_COLUMNS: tuple[str, ...] = (
    "municipality_ehak",
    "municipality",
    "county_code",
    "county",
    "act_iri",
    "title",
    "entry_into_force",
    "temporal_status",
    "issuer",
    "municipality_status",
    "historical_municipality",
    "historical_municipality_name",
    "enabling_provision",
    "citation_status",
    "version_provision",
    "version_in_force",
    "provision_outdated",
    "provision_superseding_date",
    "act_outdated",
    "act_earliest_superseding_date",
)

TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "laws": LAW_COLUMNS,
    "provisions": PROVISION_COLUMNS,
    "citations": CITATION_COLUMNS,
    "sanctions": SANCTION_COLUMNS,
    "court_decisions": COURT_COLUMNS,
    "regulations": REGULATION_COLUMNS,
    "drafts": DRAFT_COLUMNS,
    "eu_acts": EU_ACT_COLUMNS,
    "institutions": INSTITUTION_COLUMNS,
    "competences": COMPETENCE_COLUMNS,
}

#: Internal (unwritten) table of act facts used by the provision temporal join.
ACTS_TABLE = "_acts"

#: Dedupe / sort key per table (first row wins on a duplicate key).
TABLE_KEYS: dict[str, tuple[str, ...]] = {
    "laws": ("iri",),
    "provisions": ("iri",),
    "citations": ("source", "target", "predicate"),
    "sanctions": ("iri",),
    "court_decisions": ("iri",),
    "regulations": ("iri",),
    "drafts": ("iri",),
    "eu_acts": ("iri",),
    "institutions": ("iri",),
    "competences": ("provision", "institution", "competence_type", "competence"),
    ACTS_TABLE: ("iri",),
}

CITATION_PREDICATES: tuple[str, ...] = (
    "estleg:references",
    "estleg:referencedBy",
)

_PROVISION_TYPE_PREFIX = "estleg:LegalProvision"
_SUBSECTION_TYPE = "estleg:Subsection"
_KOV_PROVISION_TYPE = "estleg:KovProvision"
_LAW_TYPE = "estleg:Law"
_ACT_TYPE = "estleg:Act"
_SANCTION_TYPE = "estleg:Sanction"
_COURT_TYPE = "estleg:CourtDecision"
_NATIONAL_REGULATION = "estleg:NationalRegulation"
_MUNICIPAL_REGULATION = "estleg:MunicipalRegulation"
_REGULATION_TYPES = frozenset({_NATIONAL_REGULATION, _MUNICIPAL_REGULATION})
_DRAFT_TYPE = "estleg:DraftLegislation"
_EU_ACT_TYPE = "estleg:EULegislation"
_INSTITUTION_TYPE = "estleg:Institution"
_COMPETENCE_TYPE = "estleg:Competence"

# ── sanction EUR normalisation (mirrors estleg_client/rows.py) ────────────────
#: Euros per trahviühik (fine unit), KarS § 47 lg 1.
EUR_PER_FINE_UNIT = Decimal("4")
#: Fixed EEK/EUR conversion rate (Council Regulation (EU) No 671/2010).
EEK_PER_EUR = Decimal("15.6466")
_NATURAL_UNITS = frozenset({"fine_units", "daily_rates"})
_NATURAL_TYPES = frozenset({"imprisonment", "arrest"})
_LEGAL_TYPES = frozenset({"compulsory_dissolution"})
_LEGAL_MONEY_TYPES = frozenset({"fine", "pecuniary_punishment"})

Tables = dict[str, list[dict[str, str]]]


def compact_iri(value: str) -> str:
    """Collapse a full estleg IRI to the ``estleg:`` prefix form."""
    if value.startswith(NS):
        return ESTLEG_PREFIX + value[len(NS) :]
    return value


def node_types(node: Mapping[str, Any]) -> list[str]:
    raw = node.get("@type") or []
    if isinstance(raw, str):
        raw = [raw]
    return [compact_iri(item) for item in raw if isinstance(item, str)]


def node_id(node: Mapping[str, Any]) -> str:
    raw = node.get("@id")
    return compact_iri(raw) if isinstance(raw, str) else ""


def ref_ids(value: Any) -> list[str]:
    """Return every ``@id`` reachable in a JSON-LD object/list/string value."""
    out: list[str] = []
    if isinstance(value, str):
        out.append(compact_iri(value))
    elif isinstance(value, dict):
        ref = value.get("@id")
        if isinstance(ref, str):
            out.append(compact_iri(ref))
    elif isinstance(value, list):
        for item in value:
            out.extend(ref_ids(item))
    return out


def ref_id(value: Any) -> str:
    ids = ref_ids(value)
    return ids[0] if ids else ""


def join_values(values: Iterable[str]) -> str:
    """``;``-join the non-empty values, dropping duplicates, keeping order."""
    seen: dict[str, None] = {}
    for value in values:
        if value and value not in seen:
            seen[value] = None
    return LIST_SEP.join(seen)


def jsonld_scalar(value: Any) -> str:
    """Plain string for CSV: booleans, numbers, ``@value`` objects, lists."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict) and "@value" in value:
        return jsonld_scalar(value.get("@value"))
    if isinstance(value, list):
        parts = [jsonld_scalar(item) for item in value]
        return " ".join(part for part in parts if part)
    return jsonld_text(value)


def link_value(value: Any) -> str:
    """A URL cell from an ``@id`` reference or an ``xsd:anyURI`` literal."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict) and "@id" in value:
        return ref_id(value)
    return jsonld_scalar(value)


def local_after(iri: str, marker: str) -> str:
    """``estleg:Phase_Review`` → ``Review`` for ``marker="Phase_"``."""
    if not iri:
        return ""
    local = iri.rsplit(":", 1)[-1].rsplit("/", 1)[-1]
    return local.split(marker, 1)[1] if marker in local else local


def truncate_legal_text(text: str, max_len: int = LEGAL_TEXT_MAX) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len]


def derive_abbrev(act_id: str) -> str:
    """Derive a law abbreviation from an act-root ``@id``."""
    return act_prefix_from_iri(compact_iri(act_id)) or compact_iri(act_id).removeprefix(
        ESTLEG_PREFIX
    )


def file_slug(path: Path) -> str:
    name = path.name
    if name.endswith("_peep.json"):
        return name[: -len("_peep.json")]
    if name.startswith("sanctions_") and name.endswith(".json"):
        return name[len("sanctions_") : -len(".json")]
    return path.stem


def is_law_node(node: Mapping[str, Any]) -> bool:
    return _LAW_TYPE in node_types(node)


def is_provision_node(node: Mapping[str, Any]) -> bool:
    for item in node_types(node):
        if item.startswith(_PROVISION_TYPE_PREFIX):
            return True
        if item in {_SUBSECTION_TYPE, _KOV_PROVISION_TYPE}:
            return True
    return False


def is_subsection_node(node: Mapping[str, Any]) -> bool:
    return _SUBSECTION_TYPE in node_types(node)


def is_sanction_node(node: Mapping[str, Any]) -> bool:
    return _SANCTION_TYPE in node_types(node)


def is_court_decision_node(node: Mapping[str, Any]) -> bool:
    return _COURT_TYPE in node_types(node)


def is_regulation_node(node: Mapping[str, Any]) -> bool:
    return not _REGULATION_TYPES.isdisjoint(node_types(node))


def iter_nodes(graph: object) -> list[dict[str, Any]]:
    """Accept a JSON-LD document, an ``@graph`` list, or a single node."""
    if isinstance(graph, dict):
        raw = graph.get("@graph")
        if isinstance(raw, list):
            return [node for node in raw if isinstance(node, dict)]
        if "@id" in graph or "@type" in graph:
            return [graph]
        return []
    if isinstance(graph, Sequence) and not isinstance(graph, (str, bytes)):
        return [node for node in graph if isinstance(node, dict)]
    return []


def _title(node: Mapping[str, Any]) -> str:
    return (
        jsonld_text(node.get("dcterms:title"), prefer_language="et")
        or jsonld_text(node.get("dc:source"))
        or jsonld_text(node.get("rdfs:label"), prefer_language="et")
    )


def law_row(
    node: Mapping[str, Any],
    *,
    slug: str = "",
    abbreviation: str = "",
) -> dict[str, str]:
    iri = node_id(node)
    return {
        "iri": iri,
        "slug": slug,
        "title": _title(node),
        "abbreviation": abbreviation or derive_abbrev(iri),
        "temporalStatus": jsonld_text(node.get("estleg:temporalStatus")),
        "kehtiv": jsonld_scalar(node.get("estleg:kehtiv")),
    }


def act_fact_row(node: Mapping[str, Any]) -> dict[str, str]:
    """Internal act facts the provision temporal join falls back to."""
    return {
        "iri": node_id(node),
        "temporalStatus": jsonld_text(node.get("estleg:temporalStatus")),
        "entryIntoForce": jsonld_scalar(node.get("estleg:entryIntoForce")),
        "repealDate": jsonld_scalar(node.get("estleg:repealDate")),
        "kehtiv": jsonld_scalar(node.get("estleg:kehtiv")),
        "level": (
            "kov" if _MUNICIPAL_REGULATION in node_types(node)
            else "state" if _NATIONAL_REGULATION in node_types(node)
            else ""
        ),
    }


def provision_row(
    node: Mapping[str, Any],
    *,
    act: str = "",
    paragrahv: str = "",
    in_force: str = "",
) -> dict[str, str]:
    text = jsonld_text(node.get("estleg:legalText")) or jsonld_text(
        node.get("estleg:summary")
    )
    subsection = (
        jsonld_text(node.get("estleg:subsectionNumber"))
        if is_subsection_node(node)
        else ""
    )
    return {
        "iri": node_id(node),
        "act": compact_iri(act) if act else ref_id(node.get("estleg:partOfAct")),
        "paragrahv": paragrahv or jsonld_text(node.get("estleg:paragrahv")),
        "subsection": subsection,
        "legalText": truncate_legal_text(text),
        "in_force": in_force or jsonld_scalar(node.get("estleg:inForce")),
        "temporalStatus": jsonld_text(node.get("estleg:temporalStatus")),
        "valid_from": jsonld_scalar(
            node.get("estleg:validFrom") or node.get("estleg:versionValidFrom")
        ),
        "valid_to": jsonld_scalar(
            node.get("estleg:validTo") or node.get("estleg:versionValidTo")
        ),
        "current_version": ref_id(node.get("estleg:currentVersion")),
    }


def citation_rows(node: Mapping[str, Any]) -> list[dict[str, str]]:
    source = node_id(node)
    if not source:
        return []
    rows: list[dict[str, str]] = []
    for predicate in CITATION_PREDICATES:
        for target in ref_ids(node.get(predicate)):
            if not target:
                continue
            rows.append(
                {
                    "source": source,
                    "target": target,
                    "predicate": predicate,
                }
            )
    return rows


def _decimal(value: Any) -> Decimal | None:
    text = jsonld_scalar(value).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def format_decimal(value: Decimal | None) -> str:
    """``Decimal('1200')`` → ``1200``; ``Decimal('319.56')`` → ``319.56``."""
    if value is None:
        return ""
    text = format(value.normalize(), "f")
    return text


def to_eur(amount: Decimal | None, unit: str, currency: str) -> Decimal | None:
    """EUR value of a penalty amount, or ``None`` when it is not money.

    ``monetary`` in EUR (or without a currency) is taken as is, in EEK it is
    divided by 15.6466 and rounded to cents; ``fine_units`` are × 4 EUR
    (KarS § 47 lg 1). Custodial units (``days`` / ``years``), ``daily_rates``
    (income-dependent) and ``percent_of_turnover`` have no EUR value.
    """
    if amount is None or not unit:
        return None
    if unit == "fine_units":
        return amount * EUR_PER_FINE_UNIT
    if unit == "monetary":
        code = (currency or "EUR").upper()
        if code == "EUR":
            return amount
        if code == "EEK":
            return (amount / EEK_PER_EUR).quantize(Decimal("0.01"))
    return None


def sanction_person_type(sanction_type: str, unit: str) -> str:
    """Natural / legal person inferred from the penalty kind (KarS §§ 44-48)."""
    if unit in _NATURAL_UNITS or sanction_type in _NATURAL_TYPES:
        return "natural_person"
    if sanction_type in _LEGAL_TYPES:
        return "legal_person"
    if sanction_type in _LEGAL_MONEY_TYPES and unit in {"monetary", "percent_of_turnover"}:
        return "legal_person"
    return ""


def sanction_row(node: Mapping[str, Any]) -> dict[str, str]:
    max_unit = jsonld_text(node.get("estleg:maxPenaltyUnit"))
    min_unit = jsonld_text(node.get("estleg:minPenaltyUnit"))
    unit = min_unit or max_unit
    currency = jsonld_text(node.get("estleg:maxPenaltyCurrency")) or jsonld_text(
        node.get("estleg:minPenaltyCurrency")
    )
    sanction_type = jsonld_text(node.get("estleg:sanctionType"))
    max_eur = to_eur(_decimal(node.get("estleg:maxPenaltyAmount")), max_unit, currency)
    min_eur = to_eur(
        _decimal(node.get("estleg:minPenaltyAmount")), min_unit or max_unit, currency
    )
    explicit_subject = jsonld_text(node.get("estleg:sanctionSubject"))
    return {
        "iri": node_id(node),
        "provision": ref_id(node.get("estleg:applicableProvision")),
        "type": sanction_type,
        "min": jsonld_scalar(node.get("estleg:minPenaltyAmount")),
        "max": jsonld_scalar(node.get("estleg:maxPenaltyAmount")),
        "unit": unit,
        "act": "",
        "subject": explicit_subject,
        "subject_source": "sanctionSubject" if explicit_subject else "",
        "subject_person_type": sanction_person_type(sanction_type, max_unit or min_unit),
        "currency": currency,
        "amount_eur_min": format_decimal(min_eur),
        "amount_eur_max": format_decimal(max_eur),
        "statutory_default": jsonld_scalar(node.get("estleg:isStatutoryDefault")),
        "label": jsonld_text(node.get("rdfs:label"), prefer_language="et"),
    }


def court_decision_row(node: Mapping[str, Any]) -> dict[str, str]:
    return {
        "iri": node_id(node),
        "caseNumber": jsonld_text(node.get("estleg:caseNumber")),
        "date": jsonld_scalar(node.get("estleg:decisionDate")),
        "ecli": jsonld_text(node.get("estleg:ecliIdentifier")),
        "chamber": jsonld_text(node.get("estleg:chamber")),
    }


def _ehak_from_municipality_iri(iri: str) -> str:
    prefix = "estleg:Municipality_EHAK_"
    return iri[len(prefix):] if iri.startswith(prefix) else ""


def regulation_row(
    node: Mapping[str, Any],
    by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, str]:
    """A state or KOV regulation root; ``by_id`` resolves its citation nodes."""
    types = node_types(node)
    lookup = by_id or {}
    enabling: list[str] = []
    for citation in ref_ids(node.get("estleg:implementsCitation")):
        target = ref_id((lookup.get(citation) or {}).get("estleg:citationTarget"))
        if target:
            enabling.append(target)
    enabling_cell = join_values(enabling)
    is_kov = _MUNICIPAL_REGULATION in types
    specific = sorted(
        item.removeprefix(ESTLEG_PREFIX)
        for item in types
        if item.startswith(ESTLEG_PREFIX)
        and item not in _REGULATION_TYPES
        and item != _ACT_TYPE
    )
    return {
        "iri": node_id(node),
        "rt_id": jsonld_text(node.get("estleg:terviktekstId")),
        "global_id": jsonld_text(node.get("estleg:globalId")),
        "title": jsonld_text(node.get("dc:source")) or jsonld_text(node.get("rdfs:label")),
        "level": "kov" if is_kov else "state",
        "regulation_type": LIST_SEP.join(specific),
        "document_type": jsonld_text(node.get("estleg:documentType")),
        "act_number": jsonld_text(node.get("estleg:actNumber")),
        "issuer": jsonld_text(node.get("estleg:issuer")),
        "issuer_iri": ref_id(node.get("estleg:enactedBy")),
        "municipality_ehak": _ehak_from_municipality_iri(
            ref_id(node.get("estleg:enactedByMunicipality"))
        ),
        "municipality": "",
        "county_code": "",
        "county": "",
        "municipality_status": jsonld_text(node.get("estleg:municipalityStatus")),
        "historical_municipality": ref_id(node.get("estleg:enactedByHistoricalMunicipality")),
        "kehtiv": jsonld_scalar(node.get("estleg:kehtiv")),
        "temporal_status": jsonld_text(node.get("estleg:temporalStatus")),
        "entry_into_force": jsonld_scalar(node.get("estleg:entryIntoForce")),
        "repeal_date": jsonld_scalar(node.get("estleg:repealDate")),
        "last_amended": jsonld_scalar(node.get("estleg:lastAmendmentDate")),
        "publication_date": jsonld_scalar(node.get("estleg:publicationDate")),
        "issued_under": join_values(ref_ids(node.get("estleg:issuedUnder"))),
        "enabling_provisions": enabling_cell,
        "enabling_provision_count": str(len(enabling_cell.split(LIST_SEP))) if enabling_cell else "0",
        "enabling_provision_outdated": jsonld_scalar(node.get("estleg:enablingProvisionOutdated")),
        "earliest_superseding_date": jsonld_scalar(node.get("estleg:earliestSupersedingDate")),
        "provision_count": "",
        "source_url": link_value(node.get("dcterms:source")),
    }


def draft_row(node: Mapping[str, Any]) -> dict[str, str]:
    affected = node.get("estleg:affectedLawName")
    affected_list = affected if isinstance(affected, list) else [affected]
    return {
        "iri": node_id(node),
        "eis_number": jsonld_text(node.get("estleg:eisNumber")),
        "title": jsonld_text(node.get("rdfs:label"), prefer_language="et"),
        "phase": local_after(ref_id(node.get("estleg:legislativePhase")), "Phase_"),
        "draft_type": local_after(ref_id(node.get("estleg:draftType")), "DraftType_"),
        "initiator": jsonld_text(node.get("estleg:initiator")),
        "publication_date": jsonld_scalar(node.get("estleg:publicationDate")),
        "change_type": jsonld_text(node.get("estleg:changeType")),
        "amends_law": join_values(ref_ids(node.get("estleg:amendsLaw"))),
        "enacted_as": join_values(ref_ids(node.get("estleg:enactedAs"))),
        "affected_laws": join_values(
            jsonld_text(item) for item in affected_list if item is not None
        ),
        "eis_link": link_value(node.get("estleg:eisLink")),
    }


def eu_act_row(node: Mapping[str, Any]) -> dict[str, str]:
    transposed = join_values(ref_ids(node.get("estleg:transposedBy")))
    subjects = join_values(ref_ids(node.get("dcterms:subject")))
    return {
        "iri": node_id(node),
        "celex": jsonld_text(node.get("estleg:celexNumber")),
        "title": _title(node),
        "doc_type": local_after(ref_id(node.get("estleg:euDocumentType")), "EUDocType_"),
        "document_date": jsonld_scalar(node.get("estleg:documentDate")),
        "in_force": jsonld_scalar(node.get("estleg:inForce")),
        "institution": local_after(ref_id(node.get("estleg:euInstitution")), "EUInst_"),
        "eli": link_value(node.get("estleg:eliIdentifier")),
        "transposition_deadline": jsonld_scalar(node.get("estleg:transpositionDeadline")),
        "transposed_by_count": str(len(transposed.split(LIST_SEP))) if transposed else "0",
        "subjects_count": str(len(subjects.split(LIST_SEP))) if subjects else "0",
        "transposed_by": transposed,
        "estonia_relevant": jsonld_scalar(node.get("estleg:estoniaRelevant")),
        "source_url": link_value(node.get("estleg:eurLexLink"))
        or link_value(node.get("dcterms:source")),
    }


def institution_row(node: Mapping[str, Any]) -> dict[str, str]:
    competences = ref_ids(node.get("estleg:hasCompetence"))
    return {
        "iri": node_id(node),
        "label": jsonld_text(node.get("rdfs:label"), prefer_language="et"),
        "type": jsonld_text(node.get("estleg:institutionType")),
        "registrikood": jsonld_scalar(node.get("estleg:registrikood")),
        "xtee_member_code": jsonld_text(node.get("estleg:xteeMemberCode")),
        "same_as": join_values(ref_ids(node.get("owl:sameAs"))),
        "predecessor": join_values(ref_ids(node.get("estleg:predecessorInstitution"))),
        "successor": join_values(ref_ids(node.get("estleg:successorInstitution"))),
        "replaced_by": join_values(ref_ids(node.get("dcterms:isReplacedBy"))),
        "valid_from": jsonld_scalar(node.get("estleg:validFrom")),
        "valid_to": jsonld_scalar(node.get("estleg:validTo")),
        "competence_count": str(len(dict.fromkeys(competences))),
    }


def competence_rows(node: Mapping[str, Any]) -> list[dict[str, str]]:
    """One row per provision an ``estleg:Competence`` binding applies to (#718)."""
    competence = node_id(node)
    base = {
        "institution": ref_id(node.get("estleg:institution")),
        "competence_type": jsonld_text(node.get("estleg:competenceType")),
        "competence": competence,
        "competence_area": jsonld_text(node.get("estleg:competenceArea")),
        "granted_by": join_values(ref_ids(node.get("estleg:grantedBy"))),
    }
    return [
        {"provision": provision, **base}
        for provision in dict.fromkeys(ref_ids(node.get("estleg:appliesToProvision")))
        if provision
    ]


def _in_force_from_status(status: str) -> str:
    if status == "inForce":
        return "true"
    if status in {"repealed", "notYetEffective"}:
        return "false"
    return ""


def _lookup_chain(
    node: Mapping[str, Any],
    by_id: Mapping[str, Mapping[str, Any]],
    field: str,
) -> str:
    """Walk ``parentProvision`` until ``field`` (or ``partOfAct``) resolves."""
    seen: set[str] = set()
    current: Mapping[str, Any] | None = dict(node)
    while current is not None:
        iri = node_id(current)
        if iri:
            if iri in seen:
                break
            seen.add(iri)
        if field == "estleg:partOfAct":
            found = ref_id(current.get("estleg:partOfAct"))
        else:
            found = jsonld_text(current.get(field))
        if found:
            return found
        parent = ref_id(current.get("estleg:parentProvision"))
        current = by_id.get(parent) if parent else None
    return ""


def empty_tables() -> Tables:
    return {name: [] for name in TABLE_KEYS}


def project_graph(
    graph: object,
    *,
    slug: str = "",
    abbreviation: str = "",
) -> Tables:
    """Project a JSON-LD graph into the star-schema row lists.

    Provision rows carry three internal ``_``-prefixed fields (own
    ``inForce``, ``dutyHolder``/``targetGroup`` subjects, parent provision)
    that the cross-file joins in :func:`finalize_tables` read; the CSV
    writer only emits the declared columns.
    """
    nodes = iter_nodes(graph)
    by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        iri = node_id(node)
        if iri:
            by_id[iri] = node

    act_status: dict[str, str] = {}
    for node in nodes:
        if _ACT_TYPE in node_types(node) or is_law_node(node):
            act_status[node_id(node)] = jsonld_text(node.get("estleg:temporalStatus"))

    tables = empty_tables()
    provision_counts: dict[str, int] = {}
    for node in nodes:
        iri = node_id(node)
        if not iri:
            continue
        types = node_types(node)
        if _LAW_TYPE in types:
            tables["laws"].append(law_row(node, slug=slug, abbreviation=abbreviation))
        if _ACT_TYPE in types or _LAW_TYPE in types:
            tables[ACTS_TABLE].append(act_fact_row(node))
        if is_regulation_node(node):
            tables["regulations"].append(regulation_row(node, by_id))
        if is_provision_node(node):
            act = _lookup_chain(node, by_id, "estleg:partOfAct")
            paragrahv = _lookup_chain(node, by_id, "estleg:paragrahv")
            own_force = jsonld_scalar(node.get("estleg:inForce"))
            own_status = jsonld_text(node.get("estleg:temporalStatus"))
            in_force = own_force or _in_force_from_status(own_status)
            if not in_force:
                in_force = _in_force_from_status(act_status.get(act, ""))
            row = provision_row(node, act=act, paragrahv=paragrahv, in_force=in_force)
            row["_own_in_force"] = own_force or _in_force_from_status(own_status)
            row["_duty_holder"] = join_values(ref_ids(node.get("estleg:dutyHolder")))
            row["_target_group"] = join_values(ref_ids(node.get("estleg:targetGroup")))
            row["_parent"] = ref_id(node.get("estleg:parentProvision"))
            tables["provisions"].append(row)
            if not is_subsection_node(node) and act:
                provision_counts[act] = provision_counts.get(act, 0) + 1
        if _SANCTION_TYPE in types:
            tables["sanctions"].append(sanction_row(node))
        if _COURT_TYPE in types:
            tables["court_decisions"].append(court_decision_row(node))
        if _DRAFT_TYPE in types:
            tables["drafts"].append(draft_row(node))
        if _EU_ACT_TYPE in types:
            tables["eu_acts"].append(eu_act_row(node))
        if _INSTITUTION_TYPE in types:
            tables["institutions"].append(institution_row(node))
        if _COMPETENCE_TYPE in types:
            tables["competences"].extend(competence_rows(node))
        tables["citations"].extend(citation_rows(node))

    for row in tables["regulations"]:
        row["provision_count"] = str(provision_counts.get(row["iri"], 0))
    return _sort_tables(tables)


def _row_key(name: str, row: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(row.get(column, "") for column in TABLE_KEYS.get(name, ("iri",)))


def _key_complete(name: str, key: tuple[str, ...]) -> bool:
    if name == "competences":
        return bool(key[0] and key[1])
    if name == "citations":
        return bool(key[0] and key[1])
    return bool(key[0])


class TableAccumulator:
    """Merge per-file projections incrementally (first row wins on a key)."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[tuple[str, ...], dict[str, str]]] = {
            name: {} for name in TABLE_KEYS
        }

    def add(self, chunk: Mapping[str, Iterable[Mapping[str, str]]]) -> None:
        for name, rows in chunk.items():
            bucket = self._rows.setdefault(name, {})
            for row in rows:
                key = _row_key(name, row)
                if _key_complete(name, key) and key not in bucket:
                    bucket[key] = dict(row)

    def tables(self) -> Tables:
        return _sort_tables({name: list(rows.values()) for name, rows in self._rows.items()})


def _sort_tables(tables: Mapping[str, Iterable[Mapping[str, str]]]) -> Tables:
    out: Tables = {}
    for name in TABLE_KEYS:
        rows = [dict(row) for row in tables.get(name, [])]
        rows.sort(key=lambda row, _name=name: _row_key(_name, row))
        if name == "citations":
            # Drop duplicate citation triples while preserving sort order.
            seen: set[tuple[str, ...]] = set()
            unique: list[dict[str, str]] = []
            for row in rows:
                key = _row_key(name, row)
                if key in seen:
                    continue
                seen.add(key)
                unique.append(row)
            rows = unique
        out[name] = rows
    return out


def merge_tables(chunks: Iterable[Mapping[str, Iterable[Mapping[str, str]]]]) -> Tables:
    acc = TableAccumulator()
    for chunk in chunks:
        acc.add(chunk)
    return acc.tables()


# ── cross-file joins ─────────────────────────────────────────────────────────


def load_version_layer(paths: Iterable[Path]) -> Any:
    """Index the ``ProvisionVersion`` chains of the given version files.

    Returns a ``derive_kov_enabling_staleness.VersionLayer`` whose chains are
    ``(validFrom, validTo or "", versionIRI)`` rows sorted by ``validFrom``.
    """
    from estleg.derive_kov_enabling_staleness import VersionLayer

    layer = VersionLayer()
    for path in paths:
        doc = _load_jsonld(path)
        for node in iter_nodes(doc):
            if "estleg:ProvisionVersion" not in node_types(node):
                continue
            target = ref_id(node.get("estleg:versionOf"))
            valid_from = jsonld_scalar(node.get("estleg:versionValidFrom"))
            version = node_id(node)
            if not target or not valid_from or not version:
                continue
            valid_to = jsonld_scalar(node.get("estleg:versionValidTo"))
            layer.chains.setdefault(target, []).append((valid_from, valid_to, version))
    for rows in layer.chains.values():
        rows.sort()
    return layer


def apply_provision_temporal(
    provisions: Sequence[dict[str, str]],
    acts: Mapping[str, Mapping[str, str]],
    versions: Any = None,
    snapshot_by_level: Mapping[str, str] | None = None,
) -> None:
    """Fill ``temporalStatus`` / ``valid_from`` / ``valid_to`` / ``current_version``.

    Precedence per column: the provision's own stamp, then the redaction of
    its § in force on the act's ``kehtiv`` snapshot date (version layer), then
    the act (``temporalStatus`` / ``entryIntoForce`` / ``repealDate``).
    A redaction that ended before the snapshot makes the row ``repealed``; a
    § whose first redaction starts after it is ``notYetEffective``.
    """
    from estleg.derive_kov_enabling_staleness import version_key

    levels = snapshot_by_level or {}
    for row in provisions:
        act = acts.get(row.get("act", ""), {})
        snapshot = act.get("kehtiv") or levels.get(act.get("level", ""), "")
        chain: list[tuple[str, str, str]] = []
        if versions is not None:
            key = version_key(row["iri"], versions)
            chain = versions.chains.get(key, []) if key else []
        current: tuple[str, str, str] | None = None
        for entry in chain:
            if snapshot and entry[0] > snapshot:
                break
            current = entry
        if not row.get("current_version") and current:
            row["current_version"] = current[2]
        if not row.get("valid_from"):
            row["valid_from"] = (current[0] if current else "") or act.get("entryIntoForce", "")
        if not row.get("valid_to"):
            row["valid_to"] = (current[1] if current else "") or act.get("repealDate", "")
        if not row.get("temporalStatus"):
            status = ""
            if current and current[1] and snapshot and current[1] < snapshot:
                status = "repealed"
            elif chain and current is None and snapshot:
                status = "notYetEffective"
            row["temporalStatus"] = status or act.get("temporalStatus", "")
        if not row.get("_own_in_force"):
            derived = _in_force_from_status(row["temporalStatus"])
            if derived:
                row["in_force"] = derived


def apply_sanction_subjects(
    sanctions: Sequence[dict[str, str]],
    provisions: Sequence[Mapping[str, str]],
) -> None:
    """Fill ``act`` / ``subject`` / ``subject_source`` from the provision.

    Subject precedence: an explicit ``estleg:sanctionSubject``; then, for
    ``estleg:dutyHolder`` before ``estleg:targetGroup``: the provision's own
    stamp, the union of its lõiked's stamps, its parent § 's stamp; else the
    act IRI (``subject_source=act``).
    """
    by_iri = {row["iri"]: row for row in provisions}
    children: dict[str, list[Mapping[str, str]]] = {}
    for row in provisions:
        parent = row.get("_parent", "")
        if parent:
            children.setdefault(parent, []).append(row)
    for row in sanctions:
        provision = by_iri.get(row.get("provision", ""))
        if provision is not None and not row.get("act"):
            row["act"] = provision.get("act", "")
        if row.get("subject"):
            continue
        subject, source = "", ""
        if provision is not None:
            kids = sorted(children.get(provision["iri"], []), key=lambda kid: kid["iri"])
            parent = by_iri.get(provision.get("_parent", ""), {})
            for field, label in (("_duty_holder", "dutyHolder"), ("_target_group", "targetGroup")):
                subject = provision.get(field, "") or join_values(
                    value for kid in kids for value in kid.get(field, "").split(LIST_SEP)
                ) or parent.get(field, "")
                if subject:
                    source = label
                    break
        if not subject and row.get("act"):
            subject, source = row["act"], "act"
        row["subject"], row["subject_source"] = subject, source


def apply_regulation_context(
    regulations: Sequence[dict[str, str]],
    context: Any = None,
    snapshot_by_level: Mapping[str, str] | None = None,
) -> None:
    """Municipality / county names and EHAK codes, and the RT ``kehtiv`` date."""
    levels = snapshot_by_level or {}
    for row in regulations:
        if not row.get("kehtiv"):
            row["kehtiv"] = levels.get(row.get("level", ""), "")
        ehak = row.get("municipality_ehak", "")
        if not ehak or context is None:
            continue
        mun = context.municipalities.get(ehak, {})
        county = mun.get("county", "")
        row["municipality"] = mun.get("name", "")
        row["county"] = county
        row["county_code"] = context.county_codes.get(county, "")


def _sample_rank(seed: str, iri: str) -> str:
    return hashlib.sha256(f"{seed}:{iri}".encode()).hexdigest()


def sample_rows(
    rows: Sequence[dict[str, str]],
    limit: int | None,
    *,
    linked: Any,
    seed: str = SAMPLE_SEED,
) -> list[dict[str, str]]:
    """Deterministic draw of ``limit`` rows, half of them ``linked`` ones.

    Rows are ranked by ``sha256(seed:iri)`` (stable across Python versions and
    platforms), so the draw does not depend on file or dict order. Half the
    quota goes to rows that carry links into the rest of the corpus
    (``linked(row)``) so the sample exercises the join columns.
    """
    if limit is None or len(rows) <= limit:
        return list(rows)
    ranked = sorted(rows, key=lambda row: _sample_rank(seed, row["iri"]))
    with_links = [row for row in ranked if linked(row)]
    without = [row for row in ranked if not linked(row)]
    take = min(len(with_links), limit // 2)
    chosen = with_links[:take] + without[: limit - take]
    if len(chosen) < limit:
        chosen += with_links[take : take + limit - len(chosen)]
    return sorted(chosen, key=lambda row: row["iri"])


def _draft_linked(row: Mapping[str, str]) -> bool:
    return bool(row.get("amends_law") or row.get("enacted_as"))


def _eu_act_linked(row: Mapping[str, str]) -> bool:
    return row.get("transposed_by_count", "0") not in {"", "0"}


def finalize_tables(
    tables: Tables,
    *,
    versions: Any = None,
    kov_context: Any = None,
    snapshot_by_level: Mapping[str, str] | None = None,
    draft_limit: int | None = None,
    eu_act_limit: int | None = None,
    restrict_competences: bool = False,
) -> Tables:
    """Run the cross-file joins and the sample draws in place; return ``tables``."""
    acts = {row["iri"]: row for row in tables.get(ACTS_TABLE, [])}
    apply_provision_temporal(tables["provisions"], acts, versions, snapshot_by_level)
    apply_sanction_subjects(tables["sanctions"], tables["provisions"])
    apply_regulation_context(tables["regulations"], kov_context, snapshot_by_level)
    tables["drafts"] = sample_rows(tables["drafts"], draft_limit, linked=_draft_linked)
    tables["eu_acts"] = sample_rows(tables["eu_acts"], eu_act_limit, linked=_eu_act_linked)
    if restrict_competences:
        known = {row["iri"] for row in tables["provisions"]}
        tables["competences"] = [
            row for row in tables["competences"] if row["provision"] in known
        ]
    return tables


# ── input selection ──────────────────────────────────────────────────────────


def is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8") as handle:
            return handle.readline().startswith(LFS_POINTER_PREFIX)
    except (OSError, UnicodeDecodeError):
        return False


def load_abbrev_registry(path: Path | None = None) -> dict[str, str]:
    registry_path = path or (REPO_ROOT / "data" / "law_abbreviations.json")
    try:
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for slug, entry in raw.items():
        if isinstance(entry, dict):
            abbrev = entry.get("abbrev")
            if isinstance(abbrev, str) and abbrev:
                out[str(slug)] = abbrev
    return out


def expand_pattern(root: Path, pattern: str) -> list[Path]:
    """Resolve a glob or literal path relative to ``root`` (then cwd)."""
    if not pattern:
        return []
    candidate = Path(pattern)
    if candidate.is_file():
        return [candidate]
    rooted = root / pattern
    if rooted.is_file():
        return [rooted]
    matches = [path for path in root.glob(pattern) if path.is_file()]
    if matches:
        return sorted(matches)
    cwd_matches = [path for path in Path().glob(pattern) if path.is_file()]
    return sorted(cwd_matches)


def resolve_globs(root: Path, patterns: Sequence[str]) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for path in expand_pattern(root, pattern):
            if path.name in SKIP_FILENAMES:
                continue
            if path.suffix.lower() not in {".json", ".jsonld"}:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(path)
    return found


def resolve_input_paths(
    krr_dir: Path,
    *,
    laws_globs: Sequence[str] | None = None,
    sanctions_globs: Sequence[str] | None = None,
    court_globs: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[Path]:
    """Law files first (honouring ``--limit``), then sidecars, then courts."""
    law_paths = resolve_globs(krr_dir, laws_globs or DEFAULT_LAWS_GLOBS)
    if limit is not None:
        law_paths = law_paths[: max(limit, 0)]
    sidecar_paths = resolve_globs(
        krr_dir, sanctions_globs or DEFAULT_SANCTIONS_GLOBS
    )
    court_paths = resolve_globs(krr_dir, court_globs or DEFAULT_COURT_GLOBS)
    ordered: list[Path] = []
    seen: set[Path] = set()
    for path in (*law_paths, *sidecar_paths, *court_paths):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(path)
    return ordered


@dataclass(frozen=True)
class Selection:
    """Which corpus files feed which tables, and how they are sampled.

    The defaults are the committed ``--sample`` preset; :data:`FULL_SELECTION`
    is ``--full``. ``state_issuers`` filters ``regulations/riik`` files on the
    root's ``estleg:issuer`` (``None`` keeps all). ``version_globs=None``
    loads ``provision_versions/<slug>.jsonld`` for each law / regulation file
    actually read.
    """

    laws_globs: tuple[str, ...] = DEFAULT_LAWS_GLOBS
    sanctions_globs: tuple[str, ...] = DEFAULT_SANCTIONS_GLOBS
    court_globs: tuple[str, ...] = DEFAULT_COURT_GLOBS
    regulation_globs: tuple[str, ...] = DEFAULT_REGULATION_GLOBS
    state_issuers: tuple[str, ...] | None = SAMPLE_STATE_ISSUERS
    draft_globs: tuple[str, ...] = DEFAULT_DRAFT_GLOBS
    eurlex_globs: tuple[str, ...] = DEFAULT_EURLEX_GLOBS
    institution_globs: tuple[str, ...] = DEFAULT_INSTITUTION_GLOBS
    version_globs: tuple[str, ...] | None = None
    draft_limit: int | None = SAMPLE_DRAFTS
    eu_act_limit: int | None = SAMPLE_EU_ACTS
    restrict_competences: bool = True
    limit: int | None = None


SAMPLE_SELECTION = Selection()
FULL_SELECTION = Selection(
    laws_globs=("*_peep.json",),
    sanctions_globs=("sanctions/sanctions_*.json",),
    court_globs=("riigikohus/riigikohus_*_peep.json",),
    regulation_globs=(STATE_REGULATION_GLOB, "regulations/kov/*/*_peep.json"),
    state_issuers=None,
    version_globs=(f"{VERSION_DIRNAME}/*.jsonld",),
    draft_limit=None,
    eu_act_limit=None,
    restrict_competences=False,
)


def _issuer_matches(path: Path, issuers: Sequence[str]) -> bool:
    """Cheap byte prefilter on ``"estleg:issuer": "<name>"`` then a real parse."""
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    needles = set()
    for issuer in issuers:
        for ascii_only in (True, False):
            needles.add(json.dumps(issuer, ensure_ascii=ascii_only).encode("utf-8"))
    if not any(needle in raw for needle in needles):
        return False
    try:
        doc = json.loads(raw)
    except ValueError:
        return False
    wanted = set(issuers)
    return any(
        is_regulation_node(node) and jsonld_text(node.get("estleg:issuer")) in wanted
        for node in iter_nodes(doc)
    )


def _is_state_regulation_path(root: Path, path: Path) -> bool:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return rel.parts[:2] == ("regulations", "riik")


@dataclass(frozen=True)
class InputPlan:
    law_paths: tuple[Path, ...]
    other_paths: tuple[Path, ...]
    version_paths: tuple[Path, ...]


def plan_inputs(root: Path, selection: Selection) -> InputPlan:
    """Resolve a :class:`Selection` to the files the export reads."""
    law_paths = [
        path for path in resolve_globs(root, selection.laws_globs)
        if not path.name.startswith(("INDEX", "REGULATIONS_"))
    ]
    if selection.limit is not None:
        law_paths = law_paths[: max(selection.limit, 0)]
    regulation_paths: list[Path] = []
    for path in resolve_globs(root, selection.regulation_globs):
        if path.name.startswith("REGULATIONS_"):
            continue
        if selection.state_issuers is not None and _is_state_regulation_path(root, path):
            if not _issuer_matches(path, selection.state_issuers):
                continue
        regulation_paths.append(path)
    others = [
        *resolve_globs(root, selection.sanctions_globs),
        *resolve_globs(root, selection.court_globs),
        *regulation_paths,
        *resolve_globs(root, selection.draft_globs),
        *resolve_globs(root, selection.eurlex_globs),
        *resolve_globs(root, selection.institution_globs),
    ]
    law_set = {path.resolve() for path in law_paths}
    seen: set[Path] = set()
    other_paths: list[Path] = []
    for path in others:
        resolved = path.resolve()
        if resolved in law_set or resolved in seen:
            continue
        seen.add(resolved)
        other_paths.append(path)
    if selection.version_globs is not None:
        version_paths = resolve_globs(root, selection.version_globs)
    else:
        version_dir = root / VERSION_DIRNAME
        version_paths = [
            candidate
            for path in (*law_paths, *regulation_paths)
            if (candidate := version_dir / f"{file_slug(path)}.jsonld").is_file()
        ]
    return InputPlan(tuple(law_paths), tuple(other_paths), tuple(version_paths))


def load_snapshot_dates(root: Path) -> dict[str, str]:
    """RT ``kehtiv`` snapshot date of the state / KOV regulation corpora."""
    out: dict[str, str] = {}
    for level, rel in (
        ("state", "regulations/riik/REGULATIONS_RIIK_INDEX.json"),
        ("kov", "regulations/kov/REGULATIONS_KOV_INDEX.json"),
    ):
        doc = _load_jsonld(root / rel) if (root / rel).is_file() else None
        if isinstance(doc, dict) and isinstance(doc.get("kehtiv"), str):
            out[level] = doc["kehtiv"]
    return out


def _load_jsonld(path: Path, *, required: bool = False) -> dict[str, Any] | list[Any] | None:
    if is_lfs_pointer(path):
        if required:
            raise ValueError(f"Unmaterialized JSON-LD input: {path}")
        print(f"WARN: skip LFS pointer {path}", file=sys.stderr)
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            doc = json.load(handle)
        if required and not isinstance(doc if isinstance(doc, list) else (
            doc.get("@graph") if isinstance(doc, dict) else None
        ), list):
            raise ValueError("expected a JSON-LD graph list")
        return doc
    except (OSError, ValueError) as exc:
        if required:
            raise ValueError(f"Cannot load required JSON-LD input {path}: {exc}") from exc
        print(f"WARN: skip {path}: {exc}", file=sys.stderr)
        return None


def project_files(
    paths: Sequence[Path],
    *,
    registry: Mapping[str, str] | None = None,
    accumulator: TableAccumulator | None = None,
) -> Tables:
    abbrev_map = dict(registry or {})
    acc = accumulator if accumulator is not None else TableAccumulator()
    for path in paths:
        doc = _load_jsonld(path)
        if doc is None:
            continue
        slug = file_slug(path)
        acc.add(project_graph(doc, slug=slug, abbreviation=abbrev_map.get(slug, "")))
    return acc.tables()


def build_tables(
    root: Path,
    selection: Selection = SAMPLE_SELECTION,
    *,
    registry: Mapping[str, str] | None = None,
    kov_context: Any = None,
) -> Tables:
    """Read the selected files and return the finalized tables."""
    plan = plan_inputs(root, selection)
    acc = TableAccumulator()
    project_files(plan.law_paths, registry=registry, accumulator=acc)
    project_files(plan.other_paths, registry=registry, accumulator=acc)
    tables = acc.tables()
    versions = load_version_layer(plan.version_paths)
    if kov_context is None and any(row.get("municipality_ehak") for row in tables["regulations"]):
        kov_context = load_kov_context()
    return finalize_tables(
        tables,
        versions=versions,
        kov_context=kov_context,
        snapshot_by_level=load_snapshot_dates(root),
        draft_limit=selection.draft_limit,
        eu_act_limit=selection.eu_act_limit,
        restrict_competences=selection.restrict_competences,
    )


# ── writers ──────────────────────────────────────────────────────────────────


def _write_csv(
    path: Path,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, str]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(columns),
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _write_parquet(
    path: Path,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, str]],
) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    data = {column: [row.get(column, "") for row in rows] for column in columns}
    table = pa.table(data, schema=pa.schema([(column, pa.string()) for column in columns]))
    pq.write_table(table, path)


def write_tables(
    out_dir: Path | str,
    tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    *,
    graph: object | None = None,
    slug: str = "",
    abbreviation: str = "",
    write_parquet: bool | None = None,
) -> dict[str, int]:
    """Write the star-schema tables as CSV (and Parquet if available).

    ``tables`` is the dict returned by :func:`project_graph` /
    :func:`build_tables`. Alternatively pass ``graph=`` a tiny JSON-LD
    document / ``@graph`` list and the projector (plus the in-graph joins)
    runs first. Returns ``{table_name: row_count}``.
    """
    if tables is None and graph is None:
        raise TypeError("write_tables() requires tables or graph=")
    projected: Tables
    if graph is not None:
        projected = project_graph(graph, slug=slug, abbreviation=abbreviation)
        if tables is not None:
            projected = merge_tables([projected, tables])
        projected = finalize_tables(projected)
    else:
        assert tables is not None
        projected = _sort_tables(tables)

    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)

    parquet_ok = False
    if write_parquet is not False:
        try:
            import pyarrow
            import pyarrow.parquet  # noqa: F401
        except ImportError:
            print("parquet skipped: pyarrow is not installed", file=sys.stderr)
        else:
            parquet_ok = True

    counts: dict[str, int] = {}
    for name, columns in TABLE_COLUMNS.items():
        rows = projected.get(name, [])
        _write_csv(dest / f"{name}.csv", columns, rows)
        if parquet_ok:
            _write_parquet(dest / f"{name}.parquet", columns, rows)
        counts[name] = len(rows)
    return counts


def serialize(
    *,
    krr_dir: Path | None = None,
    out_dir: Path | None = None,
    laws_globs: Sequence[str] | None = None,
    sanctions_globs: Sequence[str] | None = None,
    court_globs: Sequence[str] | None = None,
    limit: int | None = None,
    write_parquet: bool | None = None,
    abbrev_registry: Path | Mapping[str, str] | None = None,
    selection: Selection | None = None,
    regulation_globs: Sequence[str] | None = None,
    draft_globs: Sequence[str] | None = None,
    eurlex_globs: Sequence[str] | None = None,
    institution_globs: Sequence[str] | None = None,
    kov_context: Any = None,
) -> dict[str, int]:
    """Load the selected peeps and write star-schema tables under ``out_dir``.

    ``selection`` defaults to the ``--sample`` preset; every ``*_globs``
    argument overrides that corpus only.
    """
    root = Path(krr_dir) if krr_dir is not None else KRR_DIR
    dest = Path(out_dir) if out_dir is not None else (root / "exports")
    if isinstance(abbrev_registry, Mapping):
        registry = dict(abbrev_registry)
    else:
        registry = load_abbrev_registry(
            Path(abbrev_registry) if abbrev_registry is not None else None
        )
    chosen = selection or SAMPLE_SELECTION
    overrides: dict[str, Any] = {}
    for field, value in (
        ("laws_globs", laws_globs),
        ("sanctions_globs", sanctions_globs),
        ("court_globs", court_globs),
        ("regulation_globs", regulation_globs),
        ("draft_globs", draft_globs),
        ("eurlex_globs", eurlex_globs),
        ("institution_globs", institution_globs),
    ):
        if value:
            overrides[field] = tuple(value)
    if regulation_globs:
        overrides["state_issuers"] = None
    if limit is not None:
        overrides["limit"] = limit
    if overrides:
        chosen = dataclasses.replace(chosen, **overrides)
    tables = build_tables(root, chosen, registry=registry, kov_context=kov_context)
    return write_tables(dest, tables, write_parquet=write_parquet)


# ── #712 KOV legality view ───────────────────────────────────────────────────


@dataclass(frozen=True)
class KovContext:
    """Registry lookups the KOV legality rows join against."""

    municipalities: Mapping[str, Mapping[str, str]]   # EHAK → {name, county}
    county_codes: Mapping[str, str]                   # county label → EHAK code
    historical_names: Mapping[str, str]               # HistoricalMunicipality IRI → formerName


def load_kov_context(ehak_dir: Path | None = None) -> KovContext:
    """Load municipalities, county codes and historical names from ``data/ehak``."""
    from estleg.enrich_kov_layer1 import load_county_codes
    from estleg.kov_registry import load_municipalities

    root = Path(ehak_dir) if ehak_dir is not None else REPO_ROOT / "data" / "ehak"
    municipalities = load_municipalities(root / "municipalities.json")
    historical_names: dict[str, str] = {}
    doc = _load_jsonld(root / "historical_municipalities.jsonld", required=True)
    for node in iter_nodes(doc):
        name = jsonld_text(node.get("estleg:formerName"))
        if name and "estleg:HistoricalMunicipality" in node_types(node):
            historical_names[node_id(node)] = name
    return KovContext(
        municipalities={code: dict(mun) for code, mun in municipalities.items()},
        county_codes=load_county_codes(root / "counties.json"),
        historical_names=historical_names,
    )


def kov_legality_rows(
    doc: object,
    *,
    versions: Any,
    context: KovContext,
) -> list[dict[str, str]]:
    """Project the KOV roots of one peep into legality rows.

    ``versions`` is a ``derive_kov_enabling_staleness.VersionLayer``. The
    evaluation is recomputed here (not read back from the peep stamps) so the
    table also carries the per-citation version IRI the peeps do not store;
    ``act_outdated`` therefore equals the peep's
    ``estleg:enablingProvisionOutdated`` whenever that stamp is current.
    """
    from estleg.derive_kov_enabling_staleness import evaluate_root, is_kov_root

    nodes = iter_nodes(doc)
    by_id = {node["@id"]: node for node in nodes if isinstance(node.get("@id"), str)}
    rows: list[dict[str, str]] = []
    for root in nodes:
        if not is_kov_root(root):
            continue
        ehak = _ehak_from_municipality_iri(ref_id(root.get("estleg:enactedByMunicipality")))
        mun = context.municipalities.get(ehak, {})
        county = mun.get("county", "")
        historical = ref_id(root.get("estleg:enactedByHistoricalMunicipality"))
        evaluation = evaluate_root(root, by_id, versions)
        act_outdated = ""
        act_earliest = ""
        if evaluation.resolved:
            act_outdated = "true" if evaluation.outdated else "false"
            act_earliest = evaluation.earliest_superseding_date or ""
        base = {
            "municipality_ehak": ehak,
            "municipality": mun.get("name", ""),
            "county_code": context.county_codes.get(county, ""),
            "county": county,
            "act_iri": node_id(root),
            "title": jsonld_text(root.get("dc:source")) or jsonld_text(root.get("rdfs:label")),
            "entry_into_force": jsonld_scalar(root.get("estleg:entryIntoForce")),
            "temporal_status": jsonld_text(root.get("estleg:temporalStatus")),
            "issuer": ref_id(root.get("estleg:enactedBy")),
            "municipality_status": jsonld_text(root.get("estleg:municipalityStatus")),
            "historical_municipality": historical,
            "historical_municipality_name": context.historical_names.get(historical, ""),
            "act_outdated": act_outdated,
            "act_earliest_superseding_date": act_earliest,
        }
        if not evaluation.provisions:
            rows.append(
                {**base, "citation_status": "no_citation" if evaluation.as_of else "no_entry_into_force"}
            )
            continue
        for prov in evaluation.provisions:
            resolved = prov.status == "resolved"
            rows.append(
                {
                    **base,
                    "enabling_provision": prov.target,
                    "citation_status": prov.status,
                    "version_provision": prov.provision,
                    "version_in_force": prov.version_in_force,
                    "provision_outdated": (
                        ("true" if prov.outdated else "false") if resolved else ""
                    ),
                    "provision_superseding_date": prov.superseding_date,
                }
            )
    return rows


def serialize_kov_legality(
    *,
    krr_dir: Path | None = None,
    out_dir: Path | None = None,
    kov_globs: Sequence[str] | None = None,
    municipalities: Sequence[str] | None = None,
    versions: Any = None,
    context: KovContext | None = None,
) -> int:
    """Write ``kov_legality.csv`` under ``out_dir``; return the row count.

    ``municipalities`` filters to the given EHAK codes (current successor
    codes, i.e. ``estleg:enactedByMunicipality``).
    """
    from estleg.derive_kov_enabling_staleness import build_version_layer

    root = Path(krr_dir) if krr_dir is not None else KRR_DIR
    dest = Path(out_dir) if out_dir is not None else (root / "exports")
    if versions is None:
        versions = build_version_layer(root / "provision_versions")
    if context is None:
        context = load_kov_context()
    wanted = set(municipalities or ())
    rows: list[dict[str, str]] = []
    paths = []
    for pattern in kov_globs or DEFAULT_KOV_GLOBS:
        matches = resolve_globs(root, [pattern])
        if not matches:
            raise ValueError(f"No KOV inputs match {pattern!r} under {root}")
        paths.extend(matches)
    for path in sorted(set(paths)):
        if path.name.startswith("REGULATIONS_KOV_INDEX"):
            continue
        doc = _load_jsonld(path, required=True)
        for row in kov_legality_rows(doc, versions=versions, context=context):
            if not wanted or row["municipality_ehak"] in wanted:
                rows.append(row)
    rows.sort(
        key=lambda r: (r["municipality_ehak"], r["act_iri"], r.get("enabling_provision", ""))
    )
    dest.mkdir(parents=True, exist_ok=True)
    _write_csv(dest / f"{KOV_LEGALITY_TABLE}.csv", KOV_LEGALITY_COLUMNS, rows)
    return len(rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    preset = parser.add_mutually_exclusive_group()
    preset.add_argument(
        "--sample",
        action="store_true",
        help="the committed, deterministic sample (default; CSV only, no Parquet)",
    )
    preset.add_argument(
        "--full",
        action="store_true",
        help="every corpus; requires --out outside krr_outputs/exports (never committed)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output directory (default: krr_outputs/exports; required with --full)",
    )
    parser.add_argument(
        "--krr-dir",
        type=Path,
        default=KRR_DIR,
        help="corpus root used to resolve globs (default: krr_outputs)",
    )
    parser.add_argument(
        "--laws-glob",
        action="append",
        dest="laws_globs",
        metavar="GLOB",
        help=(
            "law peep glob relative to --krr-dir (repeatable). "
            "Default: the sample's five laws"
        ),
    )
    parser.add_argument(
        "--sanctions-glob",
        action="append",
        dest="sanctions_globs",
        metavar="GLOB",
        help="sanctions sidecar glob relative to --krr-dir (repeatable)",
    )
    parser.add_argument(
        "--court-glob",
        action="append",
        dest="court_globs",
        metavar="GLOB",
        help="court peep glob relative to --krr-dir (repeatable)",
    )
    parser.add_argument(
        "--regulations-glob",
        action="append",
        dest="regulation_globs",
        metavar="GLOB",
        help="state / KOV regulation peep glob (repeatable; disables the state-issuer filter)",
    )
    parser.add_argument(
        "--drafts-glob",
        action="append",
        dest="draft_globs",
        metavar="GLOB",
        help="EIS draft peep glob (repeatable)",
    )
    parser.add_argument(
        "--eurlex-glob",
        action="append",
        dest="eurlex_globs",
        metavar="GLOB",
        help="EUR-Lex peep glob (repeatable)",
    )
    parser.add_argument(
        "--institutions-glob",
        action="append",
        dest="institution_globs",
        metavar="GLOB",
        help="institution registry file glob (repeatable)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="process at most N law peeps (after glob sort)",
    )
    parser.add_argument(
        "--no-parquet",
        action="store_true",
        help="do not attempt a Parquet write even if pyarrow is installed",
    )
    parser.add_argument(
        "--abbrev-registry",
        type=Path,
        default=REPO_ROOT / "data" / "law_abbreviations.json",
        help="law abbreviation registry (slug → abbrev)",
    )
    parser.add_argument(
        "--kov-legality",
        action="store_true",
        help="write only the KOV legality table (kov_legality.csv, #712) "
             "instead of the star-schema tables",
    )
    parser.add_argument(
        "--kov-glob",
        action="append",
        dest="kov_globs",
        metavar="GLOB",
        help=(
            "KOV peep glob relative to --krr-dir (repeatable). Default: the "
            "committed sample (" + ", ".join(DEFAULT_KOV_GLOBS) + ")"
        ),
    )
    parser.add_argument(
        "--kov-municipality",
        action="append",
        dest="kov_municipalities",
        metavar="EHAK",
        help="restrict the KOV legality table to this current-municipality "
             "EHAK code (repeatable)",
    )
    return parser.parse_args(argv)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out = args.out if args.out is not None else args.krr_dir / "exports"
    if args.kov_legality:
        n = serialize_kov_legality(
            krr_dir=args.krr_dir,
            out_dir=out,
            kov_globs=args.kov_globs,
            municipalities=args.kov_municipalities,
        )
        print(f"kov legality export: {n} rows")
        print(f"  wrote {out / (KOV_LEGALITY_TABLE + '.csv')}")
        return 0
    if args.full:
        if args.out is None or _is_within(args.out, KRR_DIR / "exports"):
            print(
                "error: --full needs --out outside krr_outputs/exports "
                "(the full export is never committed)",
                file=sys.stderr,
            )
            return 2
        selection = FULL_SELECTION
    else:
        selection = SAMPLE_SELECTION
    write_parquet: bool | None = None
    if args.no_parquet or not args.full:
        write_parquet = False
    started = time.monotonic()
    counts = serialize(
        krr_dir=args.krr_dir,
        out_dir=out,
        laws_globs=args.laws_globs,
        sanctions_globs=args.sanctions_globs,
        court_globs=args.court_globs,
        regulation_globs=args.regulation_globs,
        draft_globs=args.draft_globs,
        eurlex_globs=args.eurlex_globs,
        institution_globs=args.institution_globs,
        limit=args.limit,
        write_parquet=write_parquet,
        abbrev_registry=args.abbrev_registry,
        selection=selection,
    )
    elapsed = time.monotonic() - started
    parts = ", ".join(f"{counts[name]} {name}" for name in TABLE_COLUMNS)
    print(f"tabular export ({'full' if args.full else 'sample'}): {parts}")
    print(f"  wrote {out} in {elapsed:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
