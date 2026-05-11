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
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from estleg_common import save_json

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
EURLEX_DIR = KRR_DIR / "eurlex"
EURLEX_DIR.mkdir(parents=True, exist_ok=True)

NS = "https://data.riik.ee/ontology/estleg#"

SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
PAGE_SIZE = 5000
RATE_DELAY = 1.0  # seconds between SPARQL requests

CONTEXT = {
    "estleg": NS,
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dcterms": "http://purl.org/dc/terms/",
    "eli": "http://data.europa.eu/eli/ontology#",
}

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


def sanitize_celex(celex: str) -> str:
    """Create a safe ID from a CELEX number."""
    return re.sub(r"[^0-9A-Za-z]", "", celex)[:40] or "Unknown"


def sparql_query(query: str) -> list[dict]:
    """Execute a SPARQL query and return bindings.

    We POST the query (``application/x-www-form-urlencoded``) instead of
    GETting it. The Publications Office Virtuoso endpoint answers GET for
    non-trivial queries with ``HTTP 202 Accepted`` and an *empty* body;
    ``raise_for_status()`` does not raise on 2xx, so a GET helper silently
    returns ``[]`` (root cause of #129/#96). POST returns ``200`` +
    ``application/sparql-results+json``. We still guard against a 202 /
    non-JSON body so the retry layer reacts if POST ever misbehaves too.
    """
    resp = requests.post(
        SPARQL_ENDPOINT,
        data={"query": query},
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=120,
    )
    resp.raise_for_status()
    if resp.status_code == 202:
        raise RuntimeError(
            f"SPARQL endpoint returned HTTP 202 (empty body) — "
            f"endpoint unhealthy or rate-limiting: {SPARQL_ENDPOINT}"
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise RuntimeError(
            f"SPARQL endpoint returned a non-JSON body "
            f"(status {resp.status_code}): {exc}"
        ) from exc
    return data.get("results", {}).get("bindings", [])


def sparql_query_with_retry(
    query: str, *, retries: int = 3, backoff: float = 2.0
) -> list[dict]:
    """Execute a SPARQL query with bounded exponential backoff on failure.

    EUR-Lex 5xxs intermittently. Without a retry layer a single transient
    error truncates a multi-page sweep and silently produces partial
    output. We sleep ``backoff * 2**attempt`` between attempts and re-
    raise the underlying exception (wrapped in ``RuntimeError``) on
    terminal failure so the caller can either ``break`` (under
    ``--allow-partial``) or propagate to the run's exit code.
    """
    last_exc: BaseException | None = None
    for attempt in range(retries):
        try:
            return sparql_query(query)
        except Exception as exc:  # noqa: BLE001 — broad to retry network/JSON
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
    raise RuntimeError(
        f"sparql_query failed after {retries} attempts: {last_exc}"
    ) from last_exc


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
    all_items: list[dict] = []
    seen_celex: set[str] = set()
    offset = 0
    partial = False

    while True:
        # ``cdm:directive_date_transposition`` only exists on directive
        # works; including it as an OPTIONAL for the other types is a
        # no-op (it simply never binds). It is the transposition deadline
        # surfaced as ``estleg:transpositionDeadline`` (#96).
        query = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>

SELECT DISTINCT ?work ?celex ?title ?date ?inforce ?eli ?author ?deadline WHERE {{
  ?work a {cdm_class} .
  ?work cdm:resource_legal_id_celex ?celex .
  ?exp cdm:expression_belongs_to_work ?work .
  ?exp cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/EST> .
  ?exp cdm:expression_title ?title .
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

            # Deduplicate: a work can have multiple authors → multiple rows
            if celex in seen_celex:
                # Merge author into existing item
                author_uri = b.get("author", {}).get("value", "")
                if author_uri:
                    author_code = author_uri.split("/")[-1]
                    for item in all_items:
                        if item["celex"] == celex:
                            if author_code not in item["authors"]:
                                item["authors"].append(author_code)
                            break
                # A directive may carry several ``directive_date_transposition``
                # values (corrigenda). Keep the earliest so the node has a
                # single deterministic deadline.
                deadline = b.get("deadline", {}).get("value", "")
                if deadline:
                    for item in all_items:
                        if item["celex"] == celex:
                            cur = item.get("transposition_deadline", "")
                            if not cur or deadline < cur:
                                item["transposition_deadline"] = deadline
                            break
                continue

            seen_celex.add(celex)
            author_uri = b.get("author", {}).get("value", "")
            author_code = author_uri.split("/")[-1] if author_uri else ""

            all_items.append({
                "celex": celex,
                "cellar_uri": b.get("work", {}).get("value", ""),
                "title": b.get("title", {}).get("value", ""),
                "date": b.get("date", {}).get("value", ""),
                "in_force": b.get("inforce", {}).get("value", ""),
                "eli": b.get("eli", {}).get("value", ""),
                "authors": [author_code] if author_code else [],
                "transposition_deadline": b.get("deadline", {}).get("value", ""),
            })

        if len(bindings) < PAGE_SIZE:
            break

        offset += PAGE_SIZE
        time.sleep(RATE_DELAY)

    return all_items, partial


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
            "rdfs:comment": {"@value": "European Legislation Identifier (ELI) URI.", "@language": "en"},
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


def legislation_to_node(item: dict, type_id: str) -> dict:
    """Convert a legislation dict to a JSON-LD node."""
    safe_celex = sanitize_celex(item["celex"])
    node: dict = {
        "@id": f"estleg:EU_{safe_celex}",
        "@type": ["owl:NamedIndividual", "estleg:EULegislation"],
        "rdfs:label": {"@value": item["title"][:500], "@language": "et"},
        "estleg:celexNumber": item["celex"],
        "estleg:euDocumentType": {"@id": f"estleg:EUDocType_{type_id}"},
    }

    # EUR-Lex link (Estonian)
    eurlex_link = f"https://eur-lex.europa.eu/legal-content/ET/TXT/?uri=CELEX:{item['celex']}"
    node["estleg:eurLexLink"] = {"@value": eurlex_link, "@type": "xsd:anyURI"}

    # Canonical source URI (CELEX-based)
    node["dcterms:source"] = {"@id": f"http://publications.europa.eu/resource/celex/{item['celex']}"}

    # owl:sameAs link to EUR-Lex resource URI
    node["owl:sameAs"] = {"@id": f"http://publications.europa.eu/resource/celex/{item['celex']}"}

    # ELI local identifier (CELEX number)
    node["eli:id_local"] = item["celex"]

    # ELI
    if item.get("eli"):
        node["estleg:eliIdentifier"] = {"@value": item["eli"], "@type": "xsd:anyURI"}

    # Date
    if item.get("date"):
        node["estleg:documentDate"] = {"@value": item["date"], "@type": "xsd:date"}

    # Transposition deadline (directives only; many directives — and all
    # regulations/decisions — have none, so emit only when present, #96).
    if item.get("transposition_deadline"):
        node["estleg:transpositionDeadline"] = {
            "@value": item["transposition_deadline"],
            "@type": "xsd:date",
        }

    # In-force status
    if item.get("in_force"):
        in_force_bool = "true" if is_in_force_value(item["in_force"]) else "false"
        node["estleg:inForce"] = {"@value": in_force_bool, "@type": "xsd:boolean"}

    # Institutions
    if item.get("authors"):
        inst_refs = []
        for author_code in item["authors"]:
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
    return parser.parse_args()


def main():
    args = parse_args()
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
    type_counts: dict[str, int] = {}
    partial_types: dict[str, bool] = {}

    for doc_key, doc_info in EU_DOC_TYPES.items():
        print(f"\n--- Fetching {doc_info['label_en']}s ({doc_info['cdm_class']}) ---")
        items, was_partial = fetch_legislation_type(
            doc_info["cdm_class"], allow_partial=args.allow_partial
        )
        print(f"  Total unique: {len(items)}")

        all_legislation[doc_key] = items
        type_counts[doc_key] = len(items)
        partial_types[doc_key] = was_partial

        # Generate per-type file
        graph: list[dict] = [
            {
                "@id": f"estleg:EURlex_{doc_info['type_id']}s_Map_2026",
                "@type": ["owl:Ontology"],
                "rdfs:label": {"@value": f"EL {doc_info['label_et'].lower()} – kõik ({len(items)})", "@language": "et"},
                "dc:description": {"@value": f"Euroopa Liidu {doc_info['label_et'].lower()} eesti keeles EUR-Lexist.", "@language": "et"},
                "dc:source": "EUR-Lex – eur-lex.europa.eu",
            },
        ]

        for item in items:
            graph.append(legislation_to_node(item, doc_info["type_id"]))

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
            "@id": "estleg:EURlex_Combined_Map_2026",
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
            combined_graph.append(legislation_to_node(item, doc_info["type_id"]))
            total += 1

    combined_doc = {"@context": CONTEXT, "@graph": combined_graph}
    combined_path = EURLEX_DIR / "eurlex_combined.jsonld"
    save_json(combined_path, combined_doc)
    print(f"  Saved: {combined_path.name} ({len(combined_graph)} nodes)")

    # Generate index
    print("\n--- Generating index ---")
    in_force_counts: dict[str, int] = {}
    for doc_key, items in all_legislation.items():
        in_force_counts[doc_key] = sum(1 for i in items if is_in_force_value(i.get("in_force")))

    any_partial = any(partial_types.values())
    index = {
        "generated": datetime.now(timezone.utc).date().isoformat(),
        "source": "https://eur-lex.europa.eu",
        "sparql_endpoint": SPARQL_ENDPOINT,
        "total_acts": total,
        "partial": any_partial,
        "by_type": {},
    }

    for doc_key, doc_info in EU_DOC_TYPES.items():
        count = type_counts.get(doc_key, 0)
        in_force = in_force_counts.get(doc_key, 0)
        index["by_type"][doc_info["type_id"]] = {
            "label_et": doc_info["label_et"],
            "label_en": doc_info["label_en"],
            "total": count,
            "in_force": in_force,
            "not_in_force": count - in_force,
            "file": f"eurlex_{doc_key}s_peep.json",
            "partial": partial_types.get(doc_key, False),
        }

    index_path = EURLEX_DIR / "EURLEX_INDEX.json"
    save_json(index_path, index)
    print(f"  Saved: {index_path.name}")

    # Summary
    print("\n" + "=" * 60)
    print(f"Done! Fetched {total} EU legal acts in Estonian.")
    print(f"Files saved to: {EURLEX_DIR.relative_to(REPO_ROOT)}")
    print()
    for doc_key, doc_info in EU_DOC_TYPES.items():
        count = type_counts.get(doc_key, 0)
        in_force = in_force_counts.get(doc_key, 0)
        flag = " [PARTIAL]" if partial_types.get(doc_key) else ""
        print(f"  {doc_info['label_en']:25s}: {count:6d} total ({in_force} in force){flag}")
    print("=" * 60)
    if any_partial:
        # ``--allow-partial`` was set (otherwise the run would have raised
        # before reaching here). Non-zero exit signals downstream
        # pipelines that the index/peep files should be replaced as soon
        # as a clean run is possible.
        sys.exit(2)


if __name__ == "__main__":
    main()
