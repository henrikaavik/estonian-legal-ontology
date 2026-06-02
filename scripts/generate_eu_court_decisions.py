#!/usr/bin/env python3
"""
Fetch EU court decisions from EUR-Lex via SPARQL and generate JSON-LD ontology files.

Data source: EUR-Lex SPARQL endpoint (https://publications.europa.eu/webapi/rdf/sparql)
Queries case-law, AG opinions, court orders, and court opinions available in Estonian.

Courts covered:
  - Court of Justice (CJ) — Euroopa Kohus
  - General Court (GCEU/CFI) — Üldkohus
  - Civil Service Tribunal (CST) — Avaliku Teenistuse Kohus (abolished 2016)

Generates:
  - krr_outputs/curia/curia_judgments_peep.json           (CJ/GCEU judgments)
  - krr_outputs/curia/curia_orders_peep.json              (court orders)
  - krr_outputs/curia/curia_ag_opinions_peep.json         (AG opinions)
  - krr_outputs/curia/curia_court_opinions_peep.json      (court opinions)
  - krr_outputs/curia/curia_schema.json                   (schema definitions)
  - krr_outputs/curia/curia_combined.jsonld                (all combined)
  - krr_outputs/curia/CURIA_INDEX.json                     (registry)
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from estleg_common import BUILD_EVALUATION_DATE, save_json
from eurlex_common import (
    SPARQL_ENDPOINT,
    sanitize_celex,
    sparql_query,
    sparql_query_with_retry as _sparql_query_with_retry,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
CURIA_DIR = KRR_DIR / "curia"
CURIA_DIR.mkdir(parents=True, exist_ok=True)

NS = "https://data.riik.ee/ontology/estleg#"

PAGE_SIZE = 5000
RATE_DELAY = 1.0

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

# CELEX suffix → decision type classification
# Format: 6YYYYXXNNNN where XX indicates type and court
CELEX_TYPE_MAP = {
    "CJ": ("Judgment", "Kohtuotsus", "Judgment"),
    "TJ": ("Judgment", "Kohtuotsus", "Judgment"),
    "FJ": ("Judgment", "Kohtuotsus", "Judgment"),
    "CO": ("Order", "Kohtumäärus", "Order"),
    "TO": ("Order", "Kohtumäärus", "Order"),
    "FO": ("Order", "Kohtumäärus", "Order"),
    "CC": ("AGOpinion", "Kohtujuristi ettepanek", "Advocate General Opinion"),
    "CA": ("AGOpinion", "Kohtujuristi ettepanek", "Advocate General Opinion"),
    "CN": ("AGOpinion", "Kohtujuristi ettepanek", "Advocate General Opinion"),
    "TA": ("AGOpinion", "Kohtujuristi ettepanek", "Advocate General Opinion"),
    "CV": ("CourtOpinion", "Kohtu arvamus", "Court Opinion"),
    "CP": ("Order", "Kohtumäärus", "Order"),
    "CB": ("Order", "Kohtumäärus", "Order"),
    "CD": ("Order", "Kohtumäärus", "Order"),
    "TC": ("AGOpinion", "Kohtujuristi ettepanek", "Advocate General Opinion"),
    "CS": ("Order", "Kohtumäärus", "Order"),
    "CT": ("Order", "Kohtumäärus", "Order"),
}

# CELEX suffix → court classification
CELEX_COURT_MAP = {
    "CJ": "CourtOfJustice",
    "CO": "CourtOfJustice",
    "CC": "CourtOfJustice",
    "CA": "CourtOfJustice",
    "CN": "CourtOfJustice",
    "CV": "CourtOfJustice",
    "CP": "CourtOfJustice",
    "CB": "CourtOfJustice",
    "CD": "CourtOfJustice",
    "CS": "CourtOfJustice",
    "CT": "CourtOfJustice",
    "TJ": "GeneralCourt",
    "TO": "GeneralCourt",
    "TA": "GeneralCourt",
    "TC": "GeneralCourt",
    "FJ": "CivilServiceTribunal",
    "FO": "CivilServiceTribunal",
}

UNKNOWN_CELEX_CODES: set[str] = set()

# Court info
EU_COURTS = {
    "CourtOfJustice": ("Euroopa Kohus", "Court of Justice", "CJ"),
    "GeneralCourt": ("Üldkohus", "General Court", "GCEU"),
    "CivilServiceTribunal": ("Avaliku Teenistuse Kohus", "Civil Service Tribunal", "CST"),
    # CELEX sector ``E`` covers the EFTA Court (https://www.eftacourt.int).
    # It is *not* a CJEU formation, so it gets its own bucket so consumers
    # can filter EFTA cases out of CJEU statistics. Cases mined from
    # EUR-Lex that carry sector ``E`` (e.g. ``E2024CB0001``) map here.
    "EFTACourt": ("EFTA Kohus", "EFTA Court", "EFTA"),
}

# Known author codes
AUTHOR_CODES = {
    "CJ": "CourtOfJustice",
    "GCEU": "GeneralCourt",
    "CFI": "GeneralCourt",
    "CST": "CivilServiceTribunal",
}


def classify_from_celex(celex: str) -> tuple[str, str, str, str]:
    """
    Classify decision type and court from CELEX number.
    CELEX format for case-law: 6YYYYXXNNNN (CJEU) or EYYYYXXNNNN (EFTA).
    Returns: (decision_type_id, decision_label_et, court_id, category_key)

    Sector ``E`` is the EFTA Court — it shares the case-law type-code
    namespace with the CJEU (CB orders, etc.) but is a separate
    institution. Mapping it to the dedicated ``EFTACourt`` bucket keeps
    EFTA jurisprudence out of CJEU per-court statistics.
    """
    # Extract the two-letter type code after year (positions 5-6 in the CELEX)
    match = re.match(r"([6E])\d{4}([A-Z]{2})", celex)
    if match:
        sector = match.group(1)
        code = match.group(2)
        type_info = CELEX_TYPE_MAP.get(code, ("Other", "Muu", "Other"))
        if code not in CELEX_TYPE_MAP:
            UNKNOWN_CELEX_CODES.add(code)

        if sector == "E":
            court_id = "EFTACourt"
        else:
            court_id = CELEX_COURT_MAP.get(code, "CourtOfJustice")

        # Determine category for file grouping
        if type_info[0] == "Judgment":
            category = "judgments"
        elif type_info[0] == "Order":
            category = "orders"
        elif type_info[0] == "AGOpinion":
            category = "ag_opinions"
        elif type_info[0] == "CourtOpinion":
            category = "court_opinions"
        else:
            category = "other"

        return type_info[0], type_info[1], court_id, category

    return "Other", "Muu", "CourtOfJustice", "other"


def extract_case_number(title: str) -> str:
    """Extract case number from title (e.g., 'C-438/14' from 'Kohtuasi C-438/14').

    The prefix class includes ``E`` for EFTA Court cases (e.g. ``E-1/20``):
    ``classify_from_celex`` maps CELEX sector ``E`` to ``EFTACourt``, so an
    EFTA decision would otherwise classify correctly yet carry an empty
    ``euCaseNumber``.
    """
    match = re.search(r"(?:Kohtuasi\s+|Liidetud kohtuasjad\s+)?((?:[CTFPE]-\d+/\d+)(?:\s+ja\s+[CTFPE]-\d+/\d+)*)", title)
    if match:
        return match.group(1)
    # Try simple pattern
    match = re.search(r"([CTFPE]-\d+/\d+)", title)
    return match.group(1) if match else ""


def clean_title(title: str) -> str:
    """Clean up the EUR-Lex title format (removes # separators)."""
    # EUR-Lex uses # as separator in case-law titles
    parts = [p.strip() for p in title.split("#") if p.strip()]
    return " — ".join(parts) if parts else title


def truncate_label(title: str, max_len: int = 500) -> str:
    """Shorten display labels while keeping the beginning of the case title."""
    if len(title) <= max_len:
        return title
    return title[: max_len - 3].rstrip() + "..."


def sparql_query_with_retry(
    query: str, *, retries: int = 3, backoff: float = 2.0
) -> list[dict]:
    # Forwards to the shared helper; resolves ``sparql_query`` from this
    # module so tests that monkeypatch it on this module still take effect.
    return _sparql_query_with_retry(
        query, query_fn=sparql_query, retries=retries, backoff=backoff
    )


def fetch_all_case_law(*, allow_partial: bool = False) -> tuple[list[dict], bool]:
    """Fetch all EU case-law with Estonian translations via SPARQL.

    Returns ``(items, partial)`` where ``partial`` is ``True`` iff the
    sweep stopped early due to terminal SPARQL failure under
    ``allow_partial``. Without ``allow_partial``, terminal failures
    propagate so the run exits non-zero rather than silently
    truncating the dataset.
    """
    all_items: list[dict] = []
    seen_celex: set[str] = set()
    offset = 0
    partial = False

    while True:
        query = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>

SELECT DISTINCT ?work ?celex ?title ?date ?ecli ?author WHERE {{
  {{
    {{ ?work a cdm:case-law }}
    UNION {{ ?work a cdm:opinion_advocate-general }}
    UNION {{ ?work a cdm:order_cjeu }}
    UNION {{ ?work a cdm:opinion_cjeu }}
  }}
  ?work cdm:resource_legal_id_celex ?celex .
  ?exp cdm:expression_belongs_to_work ?work .
  ?exp cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/EST> .
  ?exp cdm:expression_title ?title .
  OPTIONAL {{ ?work cdm:work_date_document ?date }}
  OPTIONAL {{ ?work cdm:case-law_ecli ?ecli }}
  OPTIONAL {{ ?work cdm:work_created_by_agent ?author }}
}} ORDER BY ?work LIMIT {PAGE_SIZE} OFFSET {offset}
"""
        print(f"  Fetching offset {offset}...")
        try:
            bindings = sparql_query_with_retry(query)
        except Exception as e:
            if allow_partial:
                print(f"  ERROR at offset {offset} (partial run): {e}")
                partial = True
                break
            raise

        if not bindings:
            break

        for b in bindings:
            celex = b.get("celex", {}).get("value", "")
            if not celex:
                continue

            if celex in seen_celex:
                # Merge author
                author_uri = b.get("author", {}).get("value", "")
                if author_uri:
                    author_code = author_uri.split("/")[-1]
                    for item in all_items:
                        if item["celex"] == celex:
                            if author_code not in item["authors"]:
                                item["authors"].append(author_code)
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
                "ecli": b.get("ecli", {}).get("value", ""),
                "authors": [author_code] if author_code else [],
            })

        if len(bindings) < PAGE_SIZE:
            break

        offset += PAGE_SIZE
        time.sleep(RATE_DELAY)

    return all_items, partial


def generate_schema_nodes() -> list[dict]:
    """Generate OWL schema nodes for EU court decisions."""
    nodes: list[dict] = [
        # EUCourtDecision class
        {
            "@id": "estleg:EUCourtDecision",
            "@type": ["owl:Class"],
            "rdfs:label": {"@value": "EL kohtulahend (EU Court Decision)", "@language": "et"},
            "rdfs:comment": {"@value": "Euroopa Liidu kohtu lahend — kohtuotsus, kohtumäärus, kohtujuristi ettepanek või kohtu arvamus.", "@language": "et"},
            "dc:description": {"@value": "A Court of Justice of the European Union decision, including judgments, orders, AG opinions, and court opinions.", "@language": "en"},
        },
        # EUCourtDecisionType class
        {
            "@id": "estleg:EUCourtDecisionType",
            "@type": ["owl:Class"],
            "rdfs:label": {"@value": "EL kohtulahendi liik (EU Court Decision Type)", "@language": "et"},
            "rdfs:comment": {"@value": "Euroopa Liidu kohtulahendi klassifikatsioon.", "@language": "et"},
        },
        # EUCourt class
        {
            "@id": "estleg:EUCourt",
            "@type": ["owl:Class"],
            "rdfs:label": {"@value": "EL kohus (EU Court)", "@language": "et"},
            "rdfs:comment": {"@value": "Euroopa Liidu kohtuinstants.", "@language": "et"},
        },
    ]

    # Decision type individuals
    decision_types = {
        "Judgment": ("Kohtuotsus", "Judgment"),
        "Order": ("Kohtumäärus", "Order"),
        "AGOpinion": ("Kohtujuristi ettepanek", "Advocate General Opinion"),
        "CourtOpinion": ("Kohtu arvamus", "Court Opinion"),
        "Other": ("Muu", "Other"),
    }
    for type_id, (label_et, label_en) in decision_types.items():
        nodes.append({
            "@id": f"estleg:EUDecType_{type_id}",
            "@type": ["owl:NamedIndividual", "estleg:EUCourtDecisionType"],
            "rdfs:label": {"@value": label_et, "@language": "et"},
            "skos:prefLabel": {"@value": label_en, "@language": "en"},
        })

    # Court individuals
    for court_id, (label_et, label_en, code) in EU_COURTS.items():
        nodes.append({
            "@id": f"estleg:EUCourt_{court_id}",
            "@type": ["owl:NamedIndividual", "estleg:EUCourt"],
            "rdfs:label": {"@value": label_et, "@language": "et"},
            "skos:prefLabel": {"@value": label_en, "@language": "en"},
            "estleg:euCourtCode": code,
        })

    # Object properties
    nodes.extend([
        {
            "@id": "estleg:euCourtDecisionType",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "EL lahendi liik", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:EUCourtDecision"},
            "rdfs:range": {"@id": "estleg:EUCourtDecisionType"},
        },
        {
            "@id": "estleg:euCourt",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "EL kohus", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:EUCourtDecision"},
            "rdfs:range": {"@id": "estleg:EUCourt"},
            "rdfs:comment": {"@value": "The EU court that delivered the decision.", "@language": "en"},
        },
    ])

    # Datatype properties
    nodes.extend([
        {
            "@id": "estleg:ecliIdentifier",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "ECLI identifikaator", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:EUCourtDecision"},
            "rdfs:range": {"@id": "xsd:string"},
            "rdfs:comment": {"@value": "European Case Law Identifier (e.g., ECLI:EU:C:2016:758).", "@language": "en"},
        },
        {
            "@id": "estleg:euCaseNumber",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "kohtuasja number", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:EUCourtDecision"},
            "rdfs:range": {"@id": "xsd:string"},
            "rdfs:comment": {"@value": "Case number (e.g., C-438/14).", "@language": "en"},
        },
        {
            "@id": "estleg:curiaLink",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "CURIA link",
            "rdfs:domain": {"@id": "estleg:EUCourtDecision"},
            "rdfs:range": {"@id": "xsd:anyURI"},
            "rdfs:comment": {"@value": "Link to the decision in EUR-Lex (Estonian version).", "@language": "en"},
        },
    ])

    return nodes


def decision_to_node(item: dict) -> dict:
    """Convert a case-law dict to a JSON-LD node."""
    safe_celex = sanitize_celex(item["celex"])
    type_id, type_label, court_id, _ = classify_from_celex(item["celex"])

    cleaned_title = clean_title(item["title"])
    case_number = extract_case_number(item["title"])

    node: dict = {
        "@id": f"estleg:EUCJ_{safe_celex}",
        "@type": ["owl:NamedIndividual", "estleg:EUCourtDecision"],
        "rdfs:label": {"@value": truncate_label(cleaned_title), "@language": "et"},
        "dcterms:title": {"@value": cleaned_title, "@language": "et"},
        "estleg:celexNumber": item["celex"],
        "estleg:euCourtDecisionType": {"@id": f"estleg:EUDecType_{type_id}"},
        "estleg:euCourt": {"@id": f"estleg:EUCourt_{court_id}"},
    }

    # EUR-Lex link (Estonian)
    eurlex_link = f"https://eur-lex.europa.eu/legal-content/ET/TXT/?uri=CELEX:{item['celex']}"
    node["estleg:curiaLink"] = {"@value": eurlex_link, "@type": "xsd:anyURI"}

    # Canonical source URI (CELEX-based)
    node["dcterms:source"] = {"@id": f"http://publications.europa.eu/resource/celex/{item['celex']}"}

    # owl:sameAs link to EUR-Lex resource URI
    node["owl:sameAs"] = {"@id": f"http://publications.europa.eu/resource/celex/{item['celex']}"}

    # ECLI
    if item.get("ecli"):
        node["estleg:ecliIdentifier"] = item["ecli"]

    # Case number
    if case_number:
        node["estleg:euCaseNumber"] = case_number

    # Date — validate before emitting an ``xsd:date`` literal. CELLAR has
    # served malformed/partial dates; an unchecked value breaks downstream
    # SHACL. Mirrors the RK ``decisionDate`` guard in
    # ``generate_court_decisions.py`` (skip + log, keep the rest of the node).
    raw_date = item.get("date")
    if raw_date:
        try:
            datetime.strptime(raw_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            print(
                f"  WARNING: skipping non-YYYY-MM-DD documentDate "
                f"{raw_date!r} for CELEX {item['celex']}",
                file=sys.stderr,
            )
        else:
            node["estleg:documentDate"] = {"@value": raw_date, "@type": "xsd:date"}

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
    print("Fetching EU court decisions from EUR-Lex SPARQL endpoint")
    print(f"Endpoint: {SPARQL_ENDPOINT}")
    print("=" * 60)

    # Generate schema
    print("\n--- Generating schema ---")
    schema_doc = {"@context": CONTEXT, "@graph": generate_schema_nodes()}
    schema_path = CURIA_DIR / "curia_schema.json"
    save_json(schema_path, schema_doc)
    print(f"  Saved: {schema_path.name} ({len(schema_doc['@graph'])} nodes)")

    # Fetch all case-law
    print("\n--- Fetching all EU case-law ---")
    all_items, partial = fetch_all_case_law(allow_partial=args.allow_partial)
    print(f"  Total unique decisions: {len(all_items)}")

    # Classify into categories
    categories: dict[str, list[dict]] = {
        "judgments": [],
        "orders": [],
        "ag_opinions": [],
        "court_opinions": [],
        "other": [],
    }

    for item in all_items:
        _, _, _, category = classify_from_celex(item["celex"])
        categories[category].append(item)

    category_labels = {
        "judgments": ("Kohtuotsused", "Judgments"),
        "orders": ("Kohtumäärused", "Orders"),
        "ag_opinions": ("Kohtujuristi ettepanekud", "AG Opinions"),
        "court_opinions": ("Kohtu arvamused", "Court Opinions"),
        "other": ("Muud lahendid", "Other Decisions"),
    }

    # Generate per-category files
    for cat_key, items in categories.items():
        if not items:
            continue

        label_et, label_en = category_labels[cat_key]
        print(f"\n--- Generating {label_en} file ({len(items)} entries) ---")

        graph: list[dict] = [
            {
                "@id": f"estleg:CURIA_{cat_key.title()}_Map_2026",
                "@type": ["owl:Ontology"],
                "rdfs:label": {"@value": f"EL kohtulahendid – {label_et} ({len(items)})", "@language": "et"},
                "dc:description": {"@value": f"Euroopa Liidu kohtulahendid – {label_et.lower()} eesti keeles.", "@language": "et"},
                "dc:source": "EUR-Lex / CURIA – eur-lex.europa.eu",
            },
        ]

        for item in items:
            graph.append(decision_to_node(item))

        doc = {"@context": CONTEXT, "@graph": graph}
        out_path = CURIA_DIR / f"curia_{cat_key}_peep.json"
        save_json(out_path, doc)
        print(f"  Saved: {out_path.name} ({len(graph)} nodes)")

    # Generate combined file
    print("\n--- Generating combined file ---")
    combined_graph: list[dict] = [
        {
            "@id": "estleg:CURIA_Combined_Map_2026",
            "@type": ["owl:Ontology"],
            "rdfs:label": {"@value": "EL kohtulahendid – kõik (Combined)", "@language": "et"},
            "dc:description": {"@value": "Kõik Euroopa Liidu kohtulahendid eesti keeles EUR-Lexist.", "@language": "et"},
            "dc:source": "EUR-Lex / CURIA – eur-lex.europa.eu",
        },
    ]
    for item in all_items:
        combined_graph.append(decision_to_node(item))

    combined_doc = {"@context": CONTEXT, "@graph": combined_graph}
    combined_path = CURIA_DIR / "curia_combined.jsonld"
    save_json(combined_path, combined_doc)
    print(f"  Saved: {combined_path.name} ({len(combined_graph)} nodes)")

    # Count by court
    court_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for item in all_items:
        type_id, _, court_id, _ = classify_from_celex(item["celex"])
        court_counts[court_id] = court_counts.get(court_id, 0) + 1
        type_counts[type_id] = type_counts.get(type_id, 0) + 1

    # Generate index
    print("\n--- Generating index ---")
    index = {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "source": "https://eur-lex.europa.eu",
        "sparql_endpoint": SPARQL_ENDPOINT,
        "total_decisions": len(all_items),
        "partial": partial,
        "by_type": {k: v for k, v in sorted(type_counts.items(), key=lambda x: -x[1])},
        "by_court": {k: v for k, v in sorted(court_counts.items(), key=lambda x: -x[1])},
        "by_category": {k: len(v) for k, v in categories.items() if v},
        "unknown_celex_type_codes": sorted(UNKNOWN_CELEX_CODES),
    }

    index_path = CURIA_DIR / "CURIA_INDEX.json"
    save_json(index_path, index)
    print(f"  Saved: {index_path.name}")

    # Summary
    print("\n" + "=" * 60)
    print(f"Done! Fetched {len(all_items)} EU court decisions in Estonian.")
    print(f"Files saved to: {CURIA_DIR.relative_to(REPO_ROOT)}")
    print()
    print("By type:")
    for type_id, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {type_id:20s}: {count:6d}")
    print()
    print("By court:")
    for court_id, count in sorted(court_counts.items(), key=lambda x: -x[1]):
        label = EU_COURTS.get(court_id, (court_id, "", ""))[1]
        print(f"  {label:30s}: {count:6d}")
    print("=" * 60)

    # ``UNKNOWN_CELEX_CODES`` is populated as a side-effect of
    # ``classify_from_celex`` for any sector-6/E type code we have not
    # mapped yet. Surface it loudly so reviewers notice when EUR-Lex
    # introduces new codes (e.g. when CST/EFTA case types are added)
    # and we silently bucket them as ``Other``. We deliberately do NOT
    # exit non-zero here: the index already records the offending codes
    # for traceability and emitting a warning keeps the run a no-op for
    # well-known codes.
    if UNKNOWN_CELEX_CODES:
        unknown_sorted = sorted(UNKNOWN_CELEX_CODES)
        print(
            f"WARNING: {len(unknown_sorted)} unknown CELEX type codes "
            f"(bucketed as 'Other'): {unknown_sorted}"
        )

    if partial:
        # ``--allow-partial`` was set (otherwise we would have raised).
        # Non-zero exit signals downstream pipelines to retry as soon as
        # a clean run is possible.
        sys.exit(2)


if __name__ == "__main__":
    main()
