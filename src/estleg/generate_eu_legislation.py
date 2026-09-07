#!/usr/bin/env python3
"""
Fetch EU legislation from EUR-Lex via SPARQL and generate JSON-LD ontology files.

Data source: EUR-Lex SPARQL endpoint (https://publications.europa.eu/webapi/rdf/sparql)
Queries regulations, directives, and decisions available in Estonian.

Generates:
  - krr_outputs/eurlex/eurlex_regulations_peep.json     (EU regulations)
  - krr_outputs/eurlex/eurlex_directives_peep.json      (EU directives)
  - krr_outputs/eurlex/eurlex_decisions_peep.json       (EU decisions)
  - krr_outputs/eurlex/eurlex_schema.json               (schema definitions)
  - krr_outputs/eurlex/eurlex_combined.jsonld            (all combined)
  - krr_outputs/eurlex/EURLEX_INDEX.json                 (registry)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from estleg.estleg_common import (
    BUILD_EVALUATION_DATE,
    CONTEXT,
    mint_act_iri,
    save_json,
    stamp_combined_dataset_head,
    title_langstrings,
)
from estleg.eurlex_common import (
    SPARQL_ENDPOINT,
    sanitize_celex,
    sparql_query,
)
from estleg.eurlex_common import (
    sparql_query_with_retry as _sparql_query_with_retry,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
KRR_DIR = REPO_ROOT / "krr_outputs"
EURLEX_DIR = KRR_DIR / "eurlex"
EURLEX_DIR.mkdir(parents=True, exist_ok=True)

NS = "https://w3id.org/estleg/"

PAGE_SIZE = 5000
RATE_DELAY = 1.0  # seconds between SPARQL requests


# EU document types to query
EU_DOC_TYPES = {
    "regulation": {
        "cdm_class": "cdm:regulation",
        "type_id": "Regulation",
        "label_et": "EL määrus",
        "label_en": "EU Regulation",
        "description": "Euroopa Liidu määrus – otsekohaldatav õigusakt kõigis liikmesriikides.",
    },
    "directive": {
        "cdm_class": "cdm:directive",
        "type_id": "Directive",
        "label_et": "EL direktiiv",
        "label_en": "EU Directive",
        "description": "Euroopa Liidu direktiiv – liikmesriikide poolt ülevõetav õigusakt.",
    },
    "decision": {
        "cdm_class": "cdm:decision",
        "type_id": "Decision",
        "label_et": "EL otsus",
        "label_en": "EU Decision",
        "description": "Euroopa Liidu otsus – konkreetsele adressaadile suunatud siduv akt.",
    },
}

# CELEX sector/document-type override table (#394).
#
# The ``cdm:regulation`` query returns a small tail of records whose CELEX
# is NOT a sector-3 type-R regulation: sector-4 type-X (UN-ECE vehicle
# type-approval rules, e.g. ``42024X0211``) and sector-5 type-AP (European
# Parliament legislative positions, e.g. ``52025AP0047``). Typing these as
# ``EUDocType_Regulation`` is semantically wrong, so we route them to a
# distinct document-type individual keyed on ``(sector, type_letters)``.
#
# A CELEX is ``S YYYY T NNNN`` (sector digit, 4-digit year, 1–2 type
# letters, running number). Only the regulations query is reclassified —
# directives (all sector-3 L) and decisions (all sector-3 D) are clean.
EU_DOC_TYPE_OVERRIDES: dict[tuple[str, str], dict] = {
    ("4", "X"): {
        "type_id": "InternationalAgreement",
        "label_et": "rahvusvaheline kokkulepe",
        "label_en": "International Agreement",
        "description": (
            "Rahvusvaheline kokkulepe (CELEX-i sektor 4) — nt ÜRO Euroopa "
            "Majanduskomisjoni (UN-ECE) sõidukite tüübikinnituse eeskirjad."
        ),
    },
    ("5", "AP"): {
        "type_id": "ParliamentPosition",
        "label_et": "Euroopa Parlamendi seisukoht",
        "label_en": "European Parliament Position",
        "description": (
            "Euroopa Parlamendi seadusandlik seisukoht (CELEX-i sektor 5, "
            "tüüp AP) — ei ole jõustunud määrus."
        ),
    },
}

_CELEX_RE = re.compile(r"^(?P<sector>\d)\d{4}(?P<type>[A-Z]+)\d+")


def classify_eu_doc_type(celex: str, nominal_type_id: str) -> str | None:
    """Resolve the effective ``EUDocType`` id for a CELEX from the regulations query.

    Returns the document-type id (``"Regulation"``, ``"InternationalAgreement"``,
    ``"ParliamentPosition"``, …) to use for ``estleg:euDocumentType``, or
    ``None`` when the record is a non-Regulation CELEX with no override mapping
    (caller should drop it rather than mistype it as a Regulation).

    Only the regulations query (``nominal_type_id == "Regulation"``) is
    reclassified; directives/decisions pass through unchanged because the
    upstream ``cdm:directive``/``cdm:decision`` queries are sector-3-clean
    (#394). A regulations record is only typed ``Regulation`` when its CELEX
    actually matches a sector-3 type-R pattern (``^3\\d{4}R``).
    """
    if nominal_type_id != "Regulation":
        return nominal_type_id

    m = _CELEX_RE.match(celex or "")
    if m is not None and m.group("sector") == "3" and m.group("type") == "R":
        return "Regulation"

    # Non-sector-3-R record routed through the regulations query: map it to a
    # distinct type if known, else signal the caller to drop it.
    if m is not None:
        override = EU_DOC_TYPE_OVERRIDES.get((m.group("sector"), m.group("type")))
        if override is not None:
            return override["type_id"]

    return None


def _node_types(node: dict) -> list[str]:
    raw = node.get("@type", [])
    return [raw] if isinstance(raw, str) else list(raw)


def retarget_non_regulation_types(graph: list) -> int:
    """Rewrite ``euDocumentType`` on legislation nodes using CELEX sector/type.

    Sector-4 type-X → InternationalAgreement; sector-5 type-AP →
    ParliamentPosition. Real ``^3\\d{4}R`` regulations are left alone.
    Returns the number of nodes whose type changed (#394).
    """
    changed = 0
    for node in graph:
        if not isinstance(node, dict):
            continue
        if "estleg:EULegislation" not in _node_types(node):
            continue
        celex = node.get("estleg:celexNumber")
        if not isinstance(celex, str) or not celex:
            continue
        effective = classify_eu_doc_type(celex, "Regulation")
        if not effective or effective == "Regulation":
            continue
        new_id = f"estleg:EUDocType_{effective}"
        current = node.get("estleg:euDocumentType")
        cur_id = current.get("@id") if isinstance(current, dict) else current
        if cur_id != new_id:
            node["estleg:euDocumentType"] = {"@id": new_id}
            changed += 1
    return changed


def ensure_override_type_individuals(graph: list) -> int:
    """Append missing EUDocType individuals for the #394 override types."""
    have = {
        n.get("@id")
        for n in graph
        if isinstance(n, dict) and isinstance(n.get("@id"), str)
    }
    added = 0
    by_id = {n["@id"]: n for n in generate_schema_nodes() if "@id" in n}
    for override in EU_DOC_TYPE_OVERRIDES.values():
        iri = f"estleg:EUDocType_{override['type_id']}"
        if iri in have:
            continue
        node = by_id.get(iri)
        if node is not None:
            graph.append(node)
            added += 1
    return added


# EU institution mapping (corporate-body authority code → labels)
EU_INSTITUTIONS = {
    "COM": ("EuropeanCommission", "Euroopa Komisjon", "European Commission"),
    "CONSIL": ("CouncilOfEU", "Euroopa Liidu Nõukogu", "Council of the European Union"),
    "EP": ("EuropeanParliament", "Euroopa Parlament", "European Parliament"),
    "ECB": ("EuropeanCentralBank", "Euroopa Keskpank", "European Central Bank"),
    "SANTE": ("DG_SANTE", "Tervise ja toiduohutuse peadirektoraat", "DG Health and Food Safety"),
    "MARE": ("DG_MARE", "Merenduse ja kalanduse peadirektoraat", "DG Maritime Affairs and Fisheries"),
    "GROW": ("DG_GROW", "Siseturu peadirektoraat", "DG Internal Market"),
    "COMP": ("DG_COMP", "Konkurentsi peadirektoraat", "DG Competition"),
    "AGRI": ("DG_AGRI", "Põllumajanduse peadirektoraat", "DG Agriculture"),
    "ENV": ("DG_ENV", "Keskkonna peadirektoraat", "DG Environment"),
    "ENER": ("DG_ENER", "Energia peadirektoraat", "DG Energy"),
}


def sparql_query_with_retry(
    query: str, *, retries: int = 3, backoff: float = 2.0
) -> list[dict]:
    # Forwards to the shared helper, but resolves ``sparql_query`` from
    # this module so tests that monkeypatch it on this module still take
    # effect.
    return _sparql_query_with_retry(
        query, query_fn=sparql_query, retries=retries, backoff=backoff
    )


def fetch_legislation_type(
    cdm_class: str, *, allow_partial: bool = False
) -> tuple[list[dict], bool]:
    """
    Fetch all legislation of a given type with Estonian translations.
    Uses OFFSET/LIMIT pagination. Deduplicates by CELEX.

    Returns ``(items, partial)`` where ``partial`` is ``True`` iff the
    sweep stopped early due to terminal SPARQL failure under
    ``allow_partial``. Without ``allow_partial``, terminal failures
    propagate as ``RuntimeError`` so the run exits non-zero rather than
    silently truncating the dataset.
    """
    by_celex: dict[str, dict] = {}
    offset = 0
    partial = False

    while True:
        # ``cdm:directive_date_transposition`` only exists on directive
        # works; including it as an OPTIONAL for the other types is a
        # no-op (it simply never binds). It is the transposition deadline
        # surfaced as ``estleg:transpositionDeadline`` (#96).
        query = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>

SELECT DISTINCT ?work ?celex ?title ?title_en ?date ?inforce ?eli ?author ?deadline WHERE {{
  ?work a {cdm_class} .
  ?work cdm:resource_legal_id_celex ?celex .
  ?exp cdm:expression_belongs_to_work ?work .
  ?exp cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/EST> .
  ?exp cdm:expression_title ?title .
  OPTIONAL {{
    ?exp_en cdm:expression_belongs_to_work ?work .
    ?exp_en cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/ENG> .
    ?exp_en cdm:expression_title ?title_en .
  }}
  OPTIONAL {{ ?work cdm:work_date_document ?date }}
  OPTIONAL {{ ?work cdm:resource_legal_in-force ?inforce }}
  OPTIONAL {{ ?work cdm:resource_legal_eli ?eli }}
  OPTIONAL {{ ?work cdm:work_created_by_agent ?author }}
  OPTIONAL {{ ?work cdm:directive_date_transposition ?deadline }}
}} ORDER BY ?work LIMIT {PAGE_SIZE} OFFSET {offset}
"""
        print(f"    Fetching offset {offset}...")
        try:
            bindings = sparql_query_with_retry(query)
        except Exception as e:
            if allow_partial:
                print(f"    ERROR at offset {offset} (partial run): {e}")
                partial = True
                break
            raise

        if not bindings:
            break

        for b in bindings:
            celex = b.get("celex", {}).get("value", "")
            if not celex:
                continue

            # Deduplicate: a work can have multiple authors → multiple rows.
            # ``by_celex`` maps CELEX -> its item dict so both merges below are
            # O(1) lookups (the prior list scan was O(n^2) over the growing
            # corpus). Dict insertion order mirrors the old append order, so the
            # returned list is byte-identical.
            if celex in by_celex:
                item = by_celex[celex]
                # Merge author into existing item
                author_uri = b.get("author", {}).get("value", "")
                if author_uri:
                    author_code = author_uri.split("/")[-1]
                    if author_code not in item["authors"]:
                        item["authors"].append(author_code)
                # A directive may carry several ``directive_date_transposition``
                # values (corrigenda). Keep the earliest so the node has a
                # single deterministic deadline.
                deadline = b.get("deadline", {}).get("value", "")
                if deadline:
                    cur = item.get("transposition_deadline", "")
                    if not cur or deadline < cur:
                        item["transposition_deadline"] = deadline
                title_en = b.get("title_en", {}).get("value", "")
                if title_en and not item.get("title_en"):
                    item["title_en"] = title_en
                continue

            author_uri = b.get("author", {}).get("value", "")
            author_code = author_uri.split("/")[-1] if author_uri else ""

            by_celex[celex] = {
                "celex": celex,
                "cellar_uri": b.get("work", {}).get("value", ""),
                "title": b.get("title", {}).get("value", ""),
                "title_en": b.get("title_en", {}).get("value", ""),
                "date": b.get("date", {}).get("value", ""),
                "in_force": b.get("inforce", {}).get("value", ""),
                "eli": b.get("eli", {}).get("value", ""),
                "authors": [author_code] if author_code else [],
                "transposition_deadline": b.get("deadline", {}).get("value", ""),
            }

        if len(bindings) < PAGE_SIZE:
            break

        offset += PAGE_SIZE
        time.sleep(RATE_DELAY)

    return list(by_celex.values()), partial


def generate_schema_nodes() -> list[dict]:
    """Generate OWL schema nodes for EU legislation."""
    nodes: list[dict] = [
        # EULegislation class
        {
            "@id": "estleg:EULegislation",
            "@type": ["owl:Class"],
            "rdfs:label": {"@value": "EL õigusakt (EU Legislation)", "@language": "et"},
            "rdfs:comment": {"@value": "Euroopa Liidu õigusakt — määrus, direktiiv või otsus.", "@language": "et"},
            "dc:description": {"@value": "A European Union legal act — regulation, directive, or decision.", "@language": "en"},
        },
        # EUDocumentType class
        {
            "@id": "estleg:EUDocumentType",
            "@type": ["owl:Class"],
            "rdfs:label": {"@value": "EL õigusakti liik (EU Document Type)", "@language": "et"},
            "rdfs:comment": {"@value": "Euroopa Liidu õigusakti klassifikatsioon.", "@language": "et"},
        },
        # EUInstitution class
        {
            "@id": "estleg:EUInstitution",
            "@type": ["owl:Class"],
            "rdfs:label": {"@value": "EL institutsioon (EU Institution)", "@language": "et"},
            "rdfs:comment": {"@value": "Euroopa Liidu institutsioon või organ, mis on õigusakti autor.", "@language": "et"},
        },
    ]

    # Document type individuals
    for doc_key, doc_info in EU_DOC_TYPES.items():
        nodes.append({
            "@id": f"estleg:EUDocType_{doc_info['type_id']}",
            "@type": ["owl:NamedIndividual", "estleg:EUDocumentType"],
            "rdfs:label": {"@value": doc_info["label_et"], "@language": "et"},
            "skos:prefLabel": {"@value": doc_info["label_en"], "@language": "en"},
            "rdfs:comment": {"@value": doc_info["description"], "@language": "et"},
        })

    # Override document-type individuals (#394): distinct types for the
    # sector-4/sector-5 records that arrive through the regulations query but
    # are NOT sector-3 type-R regulations. Declared here so the
    # ``estleg:euDocumentType`` references emitted by ``legislation_to_node``
    # resolve to a defined individual. Sorted by type_id for byte-stable output.
    for override in sorted(
        EU_DOC_TYPE_OVERRIDES.values(), key=lambda o: o["type_id"]
    ):
        nodes.append({
            "@id": f"estleg:EUDocType_{override['type_id']}",
            "@type": ["owl:NamedIndividual", "estleg:EUDocumentType"],
            "rdfs:label": {"@value": override["label_et"], "@language": "et"},
            "skos:prefLabel": {"@value": override["label_en"], "@language": "en"},
            "rdfs:comment": {"@value": override["description"], "@language": "et"},
        })

    # Institution individuals
    for code, (inst_id, label_et, label_en) in EU_INSTITUTIONS.items():
        nodes.append({
            "@id": f"estleg:EUInst_{inst_id}",
            "@type": ["owl:NamedIndividual", "estleg:EUInstitution"],
            "rdfs:label": {"@value": label_et, "@language": "et"},
            "skos:prefLabel": {"@value": label_en, "@language": "en"},
            "estleg:euInstitutionCode": code,
        })

    # Object properties
    nodes.extend([
        {
            "@id": "estleg:euDocumentType",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "EL õigusakti liik", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:EULegislation"},
            "rdfs:range": {"@id": "estleg:EUDocumentType"},
        },
        {
            "@id": "estleg:euInstitution",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "EL institutsioon", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:EULegislation"},
            "rdfs:range": {"@id": "estleg:EUInstitution"},
            "rdfs:comment": {"@value": "The EU institution that authored or adopted the legal act.", "@language": "en"},
        },
    ])

    # Datatype properties
    nodes.extend([
        {
            "@id": "estleg:celexNumber",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "CELEX number",
            "rdfs:domain": {"@id": "estleg:EULegislation"},
            "rdfs:range": {"@id": "xsd:string"},
            "rdfs:comment": {"@value": "CELEX identifier for the EU legal act (e.g., 32016R0679).", "@language": "en"},
        },
        {
            "@id": "estleg:eliIdentifier",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "ELI identifikaator", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:EULegislation"},
            "rdfs:range": {"@id": "xsd:anyURI"},
            # #610: kept as an xsd:anyURI literal for string consumers; the SAME
            # canonical URI is ALSO emitted as an ``owl:sameAs`` object-link on
            # each act (see ``legislation_to_node``) so follow-your-nose
            # dereference into the EU ELI register actually works.
            "rdfs:comment": {"@value": "European Legislation Identifier (ELI) URI. Also emitted as owl:sameAs for dereference.", "@language": "en"},
        },
        {
            "@id": "estleg:eurLexLink",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "EUR-Lex link",
            "rdfs:domain": {"@id": "estleg:EULegislation"},
            "rdfs:range": {"@id": "xsd:anyURI"},
            "rdfs:comment": {"@value": "Link to the legal act in EUR-Lex (Estonian version).", "@language": "en"},
        },
        {
            "@id": "estleg:documentDate",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "dokumendi kuupäev", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:EULegislation"},
            "rdfs:range": {"@id": "xsd:date"},
            "rdfs:comment": {"@value": "Date of the legal act.", "@language": "en"},
        },
        # NOTE: ``estleg:transpositionDeadline`` (#96) is a corpus-wide
        # reusable property declared in ``controlled_vocabulary.jsonld`` — not
        # here — so it is defined exactly once (re-declaring it in this
        # subcorpus schema would trip ``validate_all.validate_id_uniqueness``).
        {
            "@id": "estleg:inForce",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "jõus", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:EULegislation"},
            "rdfs:range": {"@id": "xsd:boolean"},
            "rdfs:comment": {"@value": "Whether the legal act is currently in force.", "@language": "en"},
        },
    ])

    return nodes


def is_in_force_value(value: object) -> bool:
    """Best-effort coercion of EUR-Lex ``in_force`` payloads to a Python bool.

    EUR-Lex returns this field as ``"1"``/``"0"``, ``"true"``/``"false"``,
    or occasionally upper-case (``"TRUE"``) — and the field can be entirely
    absent, in which case our SPARQL extractor leaves an empty string or
    ``None`` on the item dict. The previous implementation called
    ``str(None)`` and treated ``"none"`` as truthy-on-equality (which it
    isn't), masking a bug class where missing values silently became
    ``False`` only because ``"none" not in {"1", "true"}``. Make the
    semantics explicit so callers can rely on them.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "y"}


def _is_valid_iso_date(value: object) -> bool:
    """Return ``True`` iff ``value`` is a strict ``YYYY-MM-DD`` date string.

    Guards ``estleg:documentDate``/``estleg:transpositionDeadline`` against
    the malformed/partial dates CELLAR occasionally serves; an unchecked
    value would emit a syntactically invalid ``xsd:date`` literal and break
    downstream SHACL. Mirrors the court-decision generator's ``decisionDate``
    guard (#394). Also rejects years outside 1900..2100 so CELLAR's
    1001-01-01 / 1002-02-02 null sentinels are omitted rather than typed
    as xsd:date (#352).
    """
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return 1900 <= parsed.year <= 2100


# #607(b): the ELI canonical URI -> its *natural id* segment. An ELI URI is
# ``http(s)://data.europa.eu/eli/<natural-id>/oj`` (the ``/oj`` OJ-version suffix
# is optional), where the natural id is e.g. ``reg/2008/15`` or
# ``reg_impl/2013/1247``. ``eli:id_local`` is meant to hold this natural id, NOT
# the CELEX (which lives in ``estleg:celexNumber``). Returns ``None`` for a URI
# that is not on the ELI host so callers can fall back rather than mislabel.
_ELI_URI_RE = re.compile(
    r"^https?://data\.europa\.eu/eli/(?P<natural>.+?)(?:/oj)?$"
)


def eli_natural_id(eli_uri: str) -> str | None:
    """Extract the ELI natural id (``reg/2008/15``) from an ELI canonical URI.

    Returns ``None`` when ``eli_uri`` is empty or not an ``data.europa.eu/eli``
    URI, so the caller can keep its existing fallback (CELEX) instead of writing
    a malformed local id.
    """
    if not eli_uri:
        return None
    match = _ELI_URI_RE.match(eli_uri.strip())
    if not match:
        return None
    natural = match.group("natural").strip("/")
    return natural or None


def legislation_to_node(item: dict, type_id: str) -> dict | None:
    """Convert a legislation dict to a JSON-LD node.

    Returns ``None`` when the record must be dropped — specifically, a
    non-sector-3-R CELEX that arrived through the regulations query with no
    override mapping (#394). Callers skip ``None`` rather than emitting a
    mistyped Regulation node.
    """
    # #394: resolve the effective document type from the CELEX sector/type
    # rather than blindly trusting the query's nominal ``type_id``. The
    # regulations query returns sector-4 (UN-ECE type-X) and sector-5 (EP
    # type-AP) records that must not be typed ``EUDocType_Regulation``.
    effective_type_id = classify_eu_doc_type(item["celex"], type_id)
    if effective_type_id is None:
        print(
            f"  WARNING: dropping non-Regulation CELEX {item['celex']!r} "
            f"returned by the {type_id} query (no override mapping)",
            file=sys.stderr,
        )
        return None

    safe_celex = sanitize_celex(item["celex"])
    title_et = item["title"][:500]
    title_en = (item.get("title_en") or "")[:500]
    title_lit = {"@value": title_et, "@language": "et"}
    node: dict = {
        "@id": f"estleg:EU_{safe_celex}",
        "@type": ["owl:NamedIndividual", "estleg:EULegislation"],
        "rdfs:label": title_lit,
        "dcterms:title": title_langstrings(title_et, title_en or None),
        "estleg:celexNumber": item["celex"],
        "estleg:euDocumentType": {"@id": f"estleg:EUDocType_{effective_type_id}"},
    }

    # EUR-Lex link (Estonian)
    eurlex_link = f"https://eur-lex.europa.eu/legal-content/ET/TXT/?uri=CELEX:{item['celex']}"
    node["estleg:eurLexLink"] = {"@value": eurlex_link, "@type": "xsd:anyURI"}

    # Canonical source URI (CELEX-based)
    node["dcterms:source"] = {"@id": f"http://publications.europa.eu/resource/celex/{item['celex']}"}

    # owl:sameAs to the canonical external resources. The CELEX CELLAR URI is
    # always available; the ELI canonical URI is appended below when the act has
    # an ELI (#610) so ``?act owl:sameAs ?eurlexResource`` dereferences both the
    # CELLAR and ELI register entries instead of only CELLAR. Built as a list so
    # multiple external identities coexist without one clobbering the other.
    same_as: list[dict] = [
        {"@id": f"http://publications.europa.eu/resource/celex/{item['celex']}"}
    ]

    # ELI
    eli_uri = item.get("eli")
    if eli_uri:
        node["estleg:eliIdentifier"] = {"@value": eli_uri, "@type": "xsd:anyURI"}
        # #610: the ELI canonical URI is a real, resolvable Linked-Data resource
        # — emit it as an owl:sameAs object-link too (not only the dead anyURI
        # literal) so the act is actually joined to the EU ELI register.
        same_as.append({"@id": eli_uri})
        # #607(b): the ELI *natural id* (``reg/2008/15``) is the value
        # ``eli:id_local`` is meant to carry — NOT the CELEX, which lives in
        # ``estleg:celexNumber``. Derive it from the ELI URI.
        natural = eli_natural_id(eli_uri)
        node["eli:id_local"] = natural if natural is not None else item["celex"]
    else:
        # No ELI URI to derive from. Keep CELEX as the local id (#607(b) "at
        # minimum stop mislabelling" — there is no ELI natural id available).
        node["eli:id_local"] = item["celex"]

    node["owl:sameAs"] = same_as if len(same_as) > 1 else same_as[0]

    # Date — validate before emitting an ``xsd:date`` literal. CELLAR has
    # served malformed/partial dates; an unchecked value breaks downstream
    # SHACL. Mirrors the ``documentDate`` guard in
    # ``generate_eu_court_decisions.py`` (skip + log, keep the rest of the
    # node).
    raw_date = item.get("date")
    if raw_date:
        if _is_valid_iso_date(raw_date):
            node["estleg:documentDate"] = {"@value": raw_date, "@type": "xsd:date"}
        else:
            print(
                f"  WARNING: skipping non-YYYY-MM-DD documentDate "
                f"{raw_date!r} for CELEX {item['celex']}",
                file=sys.stderr,
            )

    # Transposition deadline (directives ONLY). The SPARQL
    # ``cdm:directive_date_transposition`` predicate only binds on directive
    # works, but we guard on the document type rather than trusting that
    # CELLAR modelling assumption: a stray deadline on a regulation/decision
    # node is semantically wrong, so a non-directive never gets one (#288).
    # Many directives also have none, so emit only when present (#96). The
    # value is validated the same way as ``documentDate`` (skip + log).
    if effective_type_id == "Directive" and item.get("transposition_deadline"):
        raw_deadline = item["transposition_deadline"]
        if _is_valid_iso_date(raw_deadline):
            node["estleg:transpositionDeadline"] = {
                "@value": raw_deadline,
                "@type": "xsd:date",
            }
        else:
            print(
                f"  WARNING: skipping non-YYYY-MM-DD transpositionDeadline "
                f"{raw_deadline!r} for CELEX {item['celex']}",
                file=sys.stderr,
            )

    # In-force status
    if item.get("in_force"):
        in_force_bool = "true" if is_in_force_value(item["in_force"]) else "false"
        node["estleg:inForce"] = {"@value": in_force_bool, "@type": "xsd:boolean"}

    if item.get("estonia_relevant"):
        node["estleg:estoniaRelevant"] = dict(ESTONIA_RELEVANT_TRUE)

    # Institutions
    if item.get("authors"):
        inst_refs = []
        # Authors accumulate in SPARQL endpoint row order, which can differ
        # between runs; sort (de-duplicated) so multi-author ``euInstitution``
        # output is byte-stable across regenerations (#288).
        for author_code in sorted(set(item["authors"])):
            if author_code in EU_INSTITUTIONS:
                inst_id = EU_INSTITUTIONS[author_code][0]
                inst_refs.append({"@id": f"estleg:EUInst_{inst_id}"})
            else:
                inst_refs.append({"@id": f"estleg:EUInst_{sanitize_celex(author_code)}"})

        if len(inst_refs) == 1:
            node["estleg:euInstitution"] = inst_refs[0]
        elif inst_refs:
            node["estleg:euInstitution"] = inst_refs

    return node


def _node_doc_type_id(node: dict) -> str | None:
    """Extract the effective ``EUDocType`` id suffix from an emitted node.

    Returns e.g. ``"Regulation"`` / ``"InternationalAgreement"`` from a node's
    ``estleg:euDocumentType`` ``{"@id": "estleg:EUDocType_<Type>"}`` reference,
    or ``None`` if the property is missing/malformed.
    """
    ref = node.get("estleg:euDocumentType")
    if not isinstance(ref, dict):
        return None
    doc_id = ref.get("@id")
    prefix = "estleg:EUDocType_"
    if isinstance(doc_id, str) and doc_id.startswith(prefix):
        return doc_id[len(prefix):]
    return None


def _node_in_force(node: dict) -> bool:
    """Return whether an emitted node's ``estleg:inForce`` literal is ``"true"``.

    Nodes only carry ``estleg:inForce`` when the upstream ``in_force`` payload
    was truthy (see ``legislation_to_node``); an absent property therefore
    counts as not-in-force, mirroring the in-force test on the raw row.
    """
    ref = node.get("estleg:inForce")
    if not isinstance(ref, dict):
        return False
    return str(ref.get("@value")).strip().casefold() == "true"


def count_emitted_by_doc_type(nodes: Iterable[dict]) -> dict[str, dict[str, int]]:
    """Tally EMITTED legislation nodes by their effective ``euDocumentType``.

    The ``EURLEX_INDEX.json`` ``by_type`` counts MUST be derived from what the
    graph actually contains, not from the raw fetched-row ``type_counts``:
    a regulations-query row can be DROPPED (``legislation_to_node`` returns
    ``None``, so no node is emitted) or RETYPED (e.g. a sector-4 record →
    ``InternationalAgreement``). Counting raw rows under the query's nominal
    type would advertise more/mistyped legislation than is emitted (#398).

    Only legislation individuals are counted — nodes carrying an
    ``estleg:euDocumentType`` ref. The ``owl:Ontology`` header node and any
    other metadata nodes are skipped. Returns
    ``{type_id: {"total", "in_force", "not_in_force"}}`` keyed by the effective
    document-type id suffix (``"Regulation"``, ``"InternationalAgreement"``, …).
    """
    counts: dict[str, dict[str, int]] = {}
    for node in nodes:
        type_id = _node_doc_type_id(node)
        if type_id is None:
            continue
        bucket = counts.setdefault(
            type_id, {"total": 0, "in_force": 0, "not_in_force": 0}
        )
        bucket["total"] += 1
        if _node_in_force(node):
            bucket["in_force"] += 1
        else:
            bucket["not_in_force"] += 1
    return counts


ESTONIA_RELEVANT_TRUE = {"@value": "true", "@type": "xsd:boolean"}


def collect_estonia_relevant_celex(krr_dir: Path | None = None) -> set[str]:
    """CELEX numbers Estonia has matched as transposing instruments (#527).

    Seeded from the committed transposition report and, as a belt-and-braces
    pass, from ``estleg:transposedBy`` already on EUR-Lex directive nodes.
    """
    root = krr_dir or KRR_DIR
    celex: set[str] = set()
    report = root / "reports" / "transposition_mapping.json"
    if report.exists():
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        for mapping in data.get("mappings") or []:
            if not isinstance(mapping, dict):
                continue
            value = mapping.get("directive_celex")
            if isinstance(value, str) and value:
                celex.add(value)
    directives = root / "eurlex" / "eurlex_directives_peep.json"
    if directives.exists():
        try:
            doc = json.loads(directives.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            doc = {}
        for node in doc.get("@graph") or []:
            if not isinstance(node, dict):
                continue
            if not node.get("estleg:transposedBy"):
                continue
            value = node.get("estleg:celexNumber")
            if isinstance(value, str) and value:
                celex.add(value)
    return celex


def stamp_estonia_relevant(node: dict, relevant: bool) -> bool:
    """Set or clear ``estleg:estoniaRelevant``. Returns True if the node changed.

    Positive hits are stamped ``true``. Non-relevant nodes omit the property
    so the 33k-act historical pile is not rewritten with ``false``.
    """
    if relevant:
        if node.get("estleg:estoniaRelevant") == ESTONIA_RELEVANT_TRUE:
            return False
        node["estleg:estoniaRelevant"] = dict(ESTONIA_RELEVANT_TRUE)
        return True
    if "estleg:estoniaRelevant" in node:
        del node["estleg:estoniaRelevant"]
        return True
    return False


def _node_estonia_relevant(node: dict) -> bool:
    ref = node.get("estleg:estoniaRelevant")
    if isinstance(ref, dict):
        return str(ref.get("@value")).strip().casefold() == "true"
    return ref is True


def build_eurlex_lens(
    nodes: Iterable[dict], relevant_celex: set[str]
) -> dict[str, int | str]:
    """Consumer-surface counts: in-force vs historical, plus Estonia relevance."""
    in_force = 0
    not_in_force = 0
    estonia_relevant = 0
    both = 0
    for node in nodes:
        if _node_doc_type_id(node) is None:
            continue
        force = _node_in_force(node)
        if force:
            in_force += 1
        else:
            not_in_force += 1
        celex = node.get("estleg:celexNumber")
        relevant = (
            _node_estonia_relevant(node)
            or (isinstance(celex, str) and celex in relevant_celex)
        )
        if relevant:
            estonia_relevant += 1
            if force:
                both += 1
    return {
        "in_force": in_force,
        "not_in_force": not_in_force,
        "estonia_relevant": estonia_relevant,
        "in_force_and_estonia_relevant": both,
        "source": "transposition_mapping.json + estleg:transposedBy",
        "note": (
            "Full CELLAR acquis is retained. This lens is the in-force / "
            "Estonia-pertinent consumer cut (#527)."
        ),
    }


def apply_estonia_relevance_lens(
    *,
    krr_dir: Path | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Stamp ``estoniaRelevant`` on EUR-Lex peeps and restamp INDEX ``lens``."""
    root = krr_dir or KRR_DIR
    eurlex_dir = root / "eurlex"
    relevant = collect_estonia_relevant_celex(root)
    stats = {"relevant_celex": len(relevant), "nodes_changed": 0, "peeps": 0}
    all_nodes: list[dict] = []
    for path in sorted(eurlex_dir.glob("*_peep.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        changed = False
        for node in doc.get("@graph") or []:
            if not isinstance(node, dict):
                continue
            if _node_doc_type_id(node) is None:
                continue
            all_nodes.append(node)
            celex = node.get("estleg:celexNumber")
            hit = isinstance(celex, str) and celex in relevant
            if stamp_estonia_relevant(node, hit):
                changed = True
                stats["nodes_changed"] += 1
        if changed:
            stats["peeps"] += 1
            if not dry_run:
                save_json(path, doc)
    index_path = eurlex_dir / "EURLEX_INDEX.json"
    if index_path.exists() and not dry_run:
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            index = {}
        if isinstance(index, dict):
            index["lens"] = build_eurlex_lens(all_nodes, relevant)
            save_json(index_path, index)
    return stats


def rebuild_eurlex_combined_from_peeps(eurlex_dir: Path = EURLEX_DIR) -> dict:
    """Rebuild ``eurlex_combined.jsonld`` from the current peeps (#417).

    The SPARQL generator writes combined from fetched rows *before*
    transposition enrichment. Consumers that load only combined then miss
    every ``transposedBy`` / ``transpositionDeadline`` edge. This pass
    concatenates instance nodes from ``eurlex_*_peep.json`` so combined
    stays a complete view of the peeps.
    """
    header = {
        "@id": mint_act_iri("EURlex_Combined"),
        "@type": ["owl:Ontology"],
        "rdfs:label": {
            "@value": "EL õigusaktid – kõik liigid (Combined)",
            "@language": "et",
        },
        "dc:description": {
            "@value": "Kõik Euroopa Liidu õigusaktid eesti keeles EUR-Lexist.",
            "@language": "et",
        },
        "dc:source": "EUR-Lex – eur-lex.europa.eu",
        "owl:imports": {"@id": "estleg:EURlex_Schema_2026"},
    }
    graph: list[dict] = [header]
    seen = {header["@id"]}
    for path in sorted(eurlex_dir.glob("*_peep.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for node in doc.get("@graph", []):
            if not isinstance(node, dict):
                continue
            nid = node.get("@id")
            if not isinstance(nid, str) or nid in seen:
                continue
            types = node.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if "owl:Ontology" in types:
                continue
            seen.add(nid)
            graph.append(node)
    combined_doc = {"@context": CONTEXT, "@graph": graph}
    stamp_combined_dataset_head(
        combined_doc,
        label="Estonian Legal Ontology — EUR-Lex combined",
    )
    dest = eurlex_dir / "eurlex_combined.jsonld"
    save_json(dest, combined_doc)
    return {
        "path": dest,
        "nodes": len(graph),
        "files": len(list(eurlex_dir.glob("*_peep.json"))),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "Continue and write partial output (with ``partial: true`` in "
            "the index) if a SPARQL pagination request fails after retries."
        ),
    )
    parser.add_argument(
        "--rebuild-combined-from-peeps",
        action="store_true",
        help=(
            "Skip SPARQL fetch; rebuild eurlex_combined.jsonld from current "
            "peeps so transposition edges are not dropped (#417)."
        ),
    )
    parser.add_argument(
        "--retarget-non-regulation",
        action="store_true",
        help=(
            "Offline #394 pass: retarget sector-4/5 CELEX in the committed "
            "regulations peep + combined + schema (no SPARQL)."
        ),
    )
    parser.add_argument(
        "--apply-lens",
        action="store_true",
        help=(
            "Offline #527 pass: stamp estleg:estoniaRelevant from the "
            "transposition mapping and write EURLEX_INDEX.json lens counts."
        ),
    )
    return parser.parse_args()


def retarget_non_regulation_corpus(krr_dir: Path | None = None) -> dict[str, int]:
    """Apply #394 CELEX-type retargeting to committed eurlex artifacts."""
    root = krr_dir or (REPO_ROOT / "krr_outputs")
    stats: dict[str, int] = {}
    files = (
        root / "eurlex" / "eurlex_regulations_peep.json",
        root / "eurlex" / "eurlex_combined.jsonld",
        root / "eurlex" / "eurlex_schema.json",
    )
    for path in files:
        if not path.exists():
            stats[path.name] = -1
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        graph = doc.get("@graph", [])
        changed = retarget_non_regulation_types(graph)
        added = ensure_override_type_individuals(graph)
        if changed or added:
            save_json(path, doc)
        stats[path.name] = changed
    return stats


def main():
    args = parse_args()
    if args.retarget_non_regulation:
        stats = retarget_non_regulation_corpus()
        print("Retargeted non-Regulation CELEX types (#394):")
        for name, n in stats.items():
            print(f"  {name}: {n}")
        return
    if args.apply_lens:
        stats = apply_estonia_relevance_lens()
        print(
            f"Applied Estonia-relevance lens (#527): "
            f"{stats['relevant_celex']} CELEX, "
            f"{stats['nodes_changed']} nodes, "
            f"{stats['peeps']} peep file(s)"
        )
        return
    if args.rebuild_combined_from_peeps:
        stats = rebuild_eurlex_combined_from_peeps()
        print(
            f"Rebuilt {stats['path']} from peeps: "
            f"{stats['nodes']} nodes, {stats['files']} peep files"
        )
        return
    print("=" * 60)
    print("Fetching EU legislation from EUR-Lex SPARQL endpoint")
    print(f"Endpoint: {SPARQL_ENDPOINT}")
    print("=" * 60)

    # Generate schema
    print("\n--- Generating schema ---")
    schema_graph: list[dict] = [
        {
            "@id": "estleg:EURlex_Schema_2026",
            "@type": ["owl:Ontology"],
            "rdfs:label": {"@value": "EUR-Lex skeem (EU Legislation schema)", "@language": "et"},
            "dc:description": {
                "@value": (
                    "Schema-only ontology for EU legislation: classes, "
                    "object/datatype properties, and named individuals. "
                    "Imported by the combined dataset to avoid duplicate "
                    "schema declarations across per-type files."
                ),
                "@language": "en",
            },
        },
    ]
    schema_graph.extend(generate_schema_nodes())
    schema_doc = {"@context": CONTEXT, "@graph": schema_graph}
    schema_path = EURLEX_DIR / "eurlex_schema.json"
    save_json(schema_path, schema_doc)
    print(f"  Saved: {schema_path.name} ({len(schema_doc['@graph'])} nodes)")

    all_legislation: dict[str, list[dict]] = {}
    partial_types: dict[str, bool] = {}
    relevant_celex = collect_estonia_relevant_celex()

    for doc_key, doc_info in EU_DOC_TYPES.items():
        print(f"\n--- Fetching {doc_info['label_en']}s ({doc_info['cdm_class']}) ---")
        items, was_partial = fetch_legislation_type(
            doc_info["cdm_class"], allow_partial=args.allow_partial
        )
        print(f"  Total unique: {len(items)} fetched (index counts the emitted subset)")

        for item in items:
            if item.get("celex") in relevant_celex:
                item["estonia_relevant"] = True
        all_legislation[doc_key] = items
        partial_types[doc_key] = was_partial

        # Generate per-type file
        graph: list[dict] = [
            {
                "@id": mint_act_iri(f"EURlex_{doc_info['type_id']}s"),
                "@type": ["owl:Ontology"],
                "rdfs:label": {"@value": f"EL {doc_info['label_et'].lower()} – kõik ({len(items)})", "@language": "et"},
                "dc:description": {"@value": f"Euroopa Liidu {doc_info['label_et'].lower()} eesti keeles EUR-Lexist.", "@language": "et"},
                "dc:source": "EUR-Lex – eur-lex.europa.eu",
            },
        ]

        for item in items:
            node = legislation_to_node(item, doc_info["type_id"])
            if node is not None:  # #394: skip dropped non-Regulation CELEX
                graph.append(node)

        doc = {"@context": CONTEXT, "@graph": graph}
        out_path = EURLEX_DIR / f"eurlex_{doc_key}s_peep.json"
        save_json(out_path, doc)
        print(f"  Saved: {out_path.name} ({len(graph)} nodes)")

        time.sleep(RATE_DELAY)

    # Generate combined file
    print("\n--- Generating combined file ---")
    # NOTE: The combined file imports the canonical schema from
    # ``eurlex_schema.json`` via ``owl:imports`` rather than embedding the
    # schema nodes inline. Keeping schema in a single file avoids
    # duplicate ``owl:Class``/``owl:NamedIndividual`` declarations that
    # break downstream reasoning when both files are loaded together.
    combined_graph: list[dict] = [
        {
            "@id": mint_act_iri("EURlex_Combined"),
            "@type": ["owl:Ontology"],
            "rdfs:label": {"@value": "EL õigusaktid – kõik liigid (Combined)", "@language": "et"},
            "dc:description": {"@value": "Kõik Euroopa Liidu õigusaktid eesti keeles EUR-Lexist.", "@language": "et"},
            "dc:source": "EUR-Lex – eur-lex.europa.eu",
            "owl:imports": {"@id": "estleg:EURlex_Schema_2026"},
        },
    ]

    total = 0
    for doc_key, doc_info in EU_DOC_TYPES.items():
        for item in all_legislation.get(doc_key, []):
            node = legislation_to_node(item, doc_info["type_id"])
            if node is None:  # #394: skip dropped non-Regulation CELEX
                continue
            combined_graph.append(node)
            total += 1

    combined_doc = {"@context": CONTEXT, "@graph": combined_graph}
    stamp_combined_dataset_head(
        combined_doc,
        label="Estonian Legal Ontology — EUR-Lex combined",
    )
    combined_path = EURLEX_DIR / "eurlex_combined.jsonld"
    save_json(combined_path, combined_doc)
    print(f"  Saved: {combined_path.name} ({len(combined_graph)} nodes)")

    # Generate index
    print("\n--- Generating index ---")
    # #398: derive by_type/total/in_force from the EMITTED nodes' effective
    # ``estleg:euDocumentType`` + ``estleg:inForce``, NOT from raw fetched-row
    # ``type_counts``. ``combined_graph`` is exactly the emitted legislation
    # nodes (plus the owl:Ontology header, which carries no euDocumentType and
    # is skipped by the tally). A dropped (None) row contributes nothing; a
    # retyped row (e.g. sector-4 → InternationalAgreement) counts under its
    # effective type, never the regulations query's nominal ``Regulation``.
    emitted_counts = count_emitted_by_doc_type(combined_graph)

    any_partial = any(partial_types.values())
    index = {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "source": "https://eur-lex.europa.eu",
        "sparql_endpoint": SPARQL_ENDPOINT,
        "total_acts": total,
        "partial": any_partial,
        "by_type": {},
    }

    # The regulations query is the one that yields retyped (override) records,
    # so override-typed nodes physically live in the regulations peep file and
    # inherit its partial flag.
    reg_doc_key = next(
        (k for k, v in EU_DOC_TYPES.items() if v["type_id"] == "Regulation"), None
    )
    reg_file = f"eurlex_{reg_doc_key}s_peep.json" if reg_doc_key else None
    reg_partial = partial_types.get(reg_doc_key, False) if reg_doc_key else False

    # Canonical types first, in their declared order — always present (even at
    # zero) so the index shape is stable across regenerations.
    canonical_type_ids: set[str] = set()
    for doc_key, doc_info in EU_DOC_TYPES.items():
        type_id = doc_info["type_id"]
        canonical_type_ids.add(type_id)
        c = emitted_counts.get(type_id, {"total": 0, "in_force": 0, "not_in_force": 0})
        index["by_type"][type_id] = {
            "label_et": doc_info["label_et"],
            "label_en": doc_info["label_en"],
            "total": c["total"],
            "in_force": c["in_force"],
            "not_in_force": c["not_in_force"],
            "file": f"eurlex_{doc_key}s_peep.json",
            "partial": partial_types.get(doc_key, False),
        }

    # Override types (e.g. InternationalAgreement / ParliamentPosition): emitted
    # only when such nodes actually survive. Counting them under their effective
    # type — rather than folding them into Regulation — keeps the index honest
    # and ensures sum(by_type totals) == total_acts (#398). Sorted for stable
    # output.
    override_labels = {
        o["type_id"]: o for o in EU_DOC_TYPE_OVERRIDES.values()
    }
    for type_id in sorted(set(emitted_counts) - canonical_type_ids):
        c = emitted_counts[type_id]
        meta = override_labels.get(type_id, {})
        index["by_type"][type_id] = {
            "label_et": meta.get("label_et", type_id),
            "label_en": meta.get("label_en", type_id),
            "total": c["total"],
            "in_force": c["in_force"],
            "not_in_force": c["not_in_force"],
            "file": reg_file,
            "partial": reg_partial,
        }

    index["lens"] = build_eurlex_lens(combined_graph, relevant_celex)

    index_path = EURLEX_DIR / "EURLEX_INDEX.json"
    save_json(index_path, index)
    print(f"  Saved: {index_path.name}")

    # Summary — report the same EMITTED-node counts the index advertises (#398),
    # so the console total and per-type breakdown agree with EURLEX_INDEX.json
    # rather than the raw fetched-row tallies.
    print("\n" + "=" * 60)
    print(f"Done! Emitted {total} EU legal acts in Estonian.")
    print(f"Files saved to: {EURLEX_DIR.relative_to(REPO_ROOT)}")
    print()
    for entry in index["by_type"].values():
        flag = " [PARTIAL]" if entry["partial"] else ""
        print(
            f"  {entry['label_en']:25s}: {entry['total']:6d} total "
            f"({entry['in_force']} in force){flag}"
        )
    print("=" * 60)
    if any_partial:
        # ``--allow-partial`` was set (otherwise the run would have raised
        # before reaching here). Non-zero exit signals downstream
        # pipelines that the index/peep files should be replaced as soon
        # as a clean run is possible.
        sys.exit(2)


if __name__ == "__main__":
    main()
