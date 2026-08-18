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
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from estleg_common import (
    BUILD_EVALUATION_DATE,
    CONTEXT,
    save_json,
    stamp_combined_dataset_head,
)
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

NS = "https://w3id.org/estleg/"

PAGE_SIZE = 5000
RATE_DELAY = 1.0


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
    # #383: General Court tierce-opposition order (e.g. 62016TT0624,
    # ECLI:EU:T:2019:47, case T-624/16). Same mis-typing family as #343;
    # without this it falls through to ``Other``/``CourtOfJustice``.
    "TT": ("Order", "Kohtumäärus", "Order"),
}

# EFTA Court (CELEX sector ``E``) uses *single-letter* case-law type codes
# (e.g. ``E2014J0018``), unlike the CJEU's two-letter codes. They are mapped
# only when the sector is ``E`` so a stray sector-6 single-letter code can
# never borrow an EFTA classification (#343). Two-letter EFTA works such as
# ``E2024CB0001`` fall back to ``CELEX_TYPE_MAP`` (``CB`` → Order).
CELEX_EFTA_TYPE_MAP = {
    "J": ("Judgment", "Kohtuotsus", "Judgment"),
    "O": ("Order", "Kohtumäärus", "Order"),
    "P": ("Order", "Kohtumäärus", "Order"),
    "A": ("AGOpinion", "Kohtujuristi ettepanek", "Advocate General Opinion"),
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
    "TT": "GeneralCourt",  # #383: General Court tierce-opposition order
    "FJ": "CivilServiceTribunal",
    "FO": "CivilServiceTribunal",
}

# CELEX type-code → case-number court letter (C/T/F). #441
CELEX_CASE_LETTER = {
    "CJ": "C",
    "CO": "C",
    "CV": "C",
    "CC": "C",
    "CA": "C",
    "CN": "C",
    "CP": "C",
    "CB": "C",
    "CD": "C",
    "CS": "C",
    "CT": "C",
    "TJ": "T",
    "TO": "T",
    "TA": "T",
    "TT": "T",
    "TC": "T",
    "FJ": "F",
    "FO": "F",
}

CELLAR_CELEX_PSI = "http://publications.europa.eu/resource/celex/"
CELLAR_ECLI_PSI = "http://publications.europa.eu/resource/ecli/"

CURIA_IDENTITY_FILES = (
    CURIA_DIR / "curia_judgments_peep.json",
    CURIA_DIR / "curia_orders_peep.json",
    CURIA_DIR / "curia_ag_opinions_peep.json",
    CURIA_DIR / "curia_court_opinions_peep.json",
    CURIA_DIR / "curia_other_peep.json",
    CURIA_DIR / "curia_combined.jsonld",
)

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
    # CELEX sector ``8`` is *national* case-law (a member-state court citing
    # EU law, e.g. ``82015EE1202(01)`` = Riigikohus). These are not EU-court
    # decisions at all; the ``fetch_all_case_law`` SPARQL FILTER (#343) keeps
    # only sectors ``6`` and ``E`` so they are no longer imported. The bucket
    # exists so (a) any sector-8 work that ever slips through classifies to an
    # honest non-CJEU sentinel rather than the misleading ``CourtOfJustice``
    # default, and (b) ``generate_schema_nodes`` emits the individual that the
    # one-off ``fix_curia_court_contamination`` remediation (#575) re-points
    # the already-committed sector-8 nodes to.
    "NationalCourt": ("Liikmesriigi kohus", "National Court", "NAT"),
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
    # Extract the type code after the year. CJEU case-law uses a two-letter
    # code (``CJ``, ``TO``, …); the EFTA Court (sector ``E``) uses a single
    # letter (``E2014J0018``). ``[A-Z]{1,2}`` is greedy, so two-letter codes
    # still win where present and only genuinely single-letter EFTA codes
    # fall back to one character (#343).
    match = re.match(r"([6E])\d{4}([A-Z]{1,2})", celex)
    if match:
        sector = match.group(1)
        code = match.group(2)

        if sector == "E":
            court_id = "EFTACourt"
            # Prefer the single-letter EFTA map, then the shared two-letter
            # map (so ``E2024CB0001`` → ``CB`` → Order still resolves).
            type_info = CELEX_EFTA_TYPE_MAP.get(code) or CELEX_TYPE_MAP.get(
                code, ("Other", "Muu", "Other")
            )
            if code not in CELEX_EFTA_TYPE_MAP and code not in CELEX_TYPE_MAP:
                UNKNOWN_CELEX_CODES.add(code)
        else:
            court_id = CELEX_COURT_MAP.get(code, "CourtOfJustice")
            type_info = CELEX_TYPE_MAP.get(code, ("Other", "Muu", "Other"))
            if code not in CELEX_TYPE_MAP:
                UNKNOWN_CELEX_CODES.add(code)

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

    # CELEX sector ``8`` is national case-law (a member-state court, e.g.
    # ``82015EE1202(01)`` = Riigikohus). It is not an EU-court decision, so
    # route it to the honest ``NationalCourt`` sentinel rather than letting
    # it inherit the misleading ``CourtOfJustice`` default below (#575). The
    # ``fetch_all_case_law`` FILTER (#343) already drops sector 8 upstream;
    # this guard only matters if a sector-8 work ever reaches the classifier.
    if re.match(r"8\d{4}", celex):
        return "Other", "Muu", "NationalCourt", "other"

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


def extract_case_numbers(title: str) -> list[str]:
    """Extract *all* case numbers from a title, de-duplicated in first-seen
    order — e.g. ``['C-403/08', 'C-429/08']`` from
    ``'Liidetud kohtuasjad C-403/08, C-429/08'``.

    Joined cases may be separated by ``" ja "`` *or* a comma. The single-value
    :func:`extract_case_number` only captures the first contiguous run, so it
    silently drops every case after a comma separator (#606). This collects
    every ``[CTFPE]-N/N`` token instead, so all joined cases are preserved.
    The prefix class includes ``E`` for EFTA Court cases (e.g. ``E-1/20``).
    """
    # ``dict.fromkeys`` de-duplicates while preserving first-seen order.
    return list(dict.fromkeys(re.findall(r"[CTFPE]-\d+/\d+", title)))


def cellar_celex_iri(celex: str) -> str:
    """CELLAR CELEX PSI for a case-law work."""
    return f"{CELLAR_CELEX_PSI}{celex}"


def cellar_ecli_iri(ecli: str) -> str:
    """CELLAR ECLI PSI; colons (and other reserved chars) are percent-encoded."""
    return f"{CELLAR_ECLI_PSI}{quote(ecli, safe='')}"


def derive_case_number_from_celex(celex: str) -> str | None:
    """Derive a ``C-n/yy`` / ``T-n/yy`` / ``F-n/yy`` case number from CELEX.

    Accepts the case-law shape ``6YYYYXXNNNN`` and the EFTA twin ``EYYYYXXNNNN``.
    ``XX`` is mapped through :data:`CELEX_CASE_LETTER`; ``NNNN`` is the integer
    case number and the year is the last two digits of ``YYYY``. Suffixes such
    as ``(01)`` are ignored. Returns ``None`` when the token is not that shape
    or ``XX`` is unmapped.
    """
    if not isinstance(celex, str) or not celex:
        return None
    match = re.match(r"[6E](\d{4})([A-Z]{2})(\d{4})", celex)
    if not match:
        return None
    year, code, number = match.group(1), match.group(2), match.group(3)
    letter = CELEX_CASE_LETTER.get(code)
    if not letter:
        return None
    return f"{letter}-{int(number)}/{year[2:]}"


def _node_types(node: dict) -> list[str]:
    raw = node.get("@type", [])
    return [raw] if isinstance(raw, str) else list(raw or [])


def _sameas_iris(value: object) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    iris: list[str] = []
    for item in items:
        if isinstance(item, dict):
            iri = item.get("@id")
            if isinstance(iri, str) and iri:
                iris.append(iri)
        elif isinstance(item, str) and item:
            iris.append(item)
    return iris


def _has_eu_case_number(node: dict) -> bool:
    value = node.get("estleg:euCaseNumber")
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        inner = value.get("@value")
        return isinstance(inner, str) and bool(inner.strip())
    if isinstance(value, list):
        return any(
            (isinstance(item, str) and item.strip())
            or (
                isinstance(item, dict)
                and isinstance(item.get("@value"), str)
                and item["@value"].strip()
            )
            for item in value
        )
    return False


def _celex_of(node: dict) -> str:
    value = node.get("estleg:celexNumber")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        inner = value.get("@value")
        return inner if isinstance(inner, str) else ""
    return ""


def _ecli_of(node: dict) -> str:
    value = node.get("estleg:ecliIdentifier")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        inner = value.get("@value")
        return inner if isinstance(inner, str) else ""
    return ""


def retarget_curia_identity(node: dict) -> bool:
    """Idempotent rewrite of one ``EUCourtDecision`` node's #441 identity.

    Ensures ``owl:sameAs`` is a list of the CELLAR CELEX PSI plus the encoded
    ECLI PSI (when ``ecliIdentifier`` is present), renames ``estleg:curiaLink``
    to ``estleg:eurLexLink``, and fills a missing ``euCaseNumber`` from CELEX.
    Returns ``True`` iff the node changed. Non-decision nodes are left alone.
    """
    if not isinstance(node, dict) or "estleg:EUCourtDecision" not in _node_types(node):
        return False

    changed = False
    celex = _celex_of(node)
    ecli = _ecli_of(node)

    wanted: list[str] = []
    if celex:
        wanted.append(cellar_celex_iri(celex))
    if ecli:
        wanted.append(cellar_ecli_iri(ecli))
    if wanted:
        existing = _sameas_iris(node.get("owl:sameAs"))
        extras = [iri for iri in existing if iri not in wanted]
        new_sameas = [{"@id": iri} for iri in wanted + extras]
        if node.get("owl:sameAs") != new_sameas:
            node["owl:sameAs"] = new_sameas
            changed = True

    if "estleg:curiaLink" in node:
        renamed: dict = {}
        already = node.get("estleg:eurLexLink")
        for key, value in node.items():
            if key == "estleg:curiaLink":
                if already is None:
                    renamed["estleg:eurLexLink"] = value
                continue
            renamed[key] = value
        node.clear()
        node.update(renamed)
        changed = True

    if not _has_eu_case_number(node) and celex:
        derived = derive_case_number_from_celex(celex)
        if derived:
            node["estleg:euCaseNumber"] = derived
            changed = True

    return changed


def rewrite_curia_identity_files(
    paths: tuple[Path, ...] | None = None,
) -> dict[str, int]:
    """Apply :func:`retarget_curia_identity` to the committed CURIA artifacts."""
    counts: dict[str, int] = {}
    for path in paths or CURIA_IDENTITY_FILES:
        if not path.is_file():
            counts[path.name] = 0
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        graph = doc.get("@graph")
        changed = 0
        if isinstance(graph, list):
            for node in graph:
                if isinstance(node, dict) and retarget_curia_identity(node):
                    changed += 1
        if changed:
            save_json(path, doc)
        counts[path.name] = changed
    return counts


def clean_title(title: str) -> str:
    """Clean up the EUR-Lex title format (removes # separators).

    #355: EUR-Lex/curia titles embed non-breaking spaces (U+00A0), e.g.
    ``M.\\u00a0Wathelet``. Left un-normalized they break SPARQL ``str()``
    equality and full-text indexes (``M.\\u00a0Wathelet`` != ``M. Wathelet``),
    so collapse them to a regular space before any other cleanup.
    """
    title = title.replace(" ", " ")
    # EUR-Lex uses # as separator in case-law titles
    parts = [p.strip() for p in title.split("#") if p.strip()]
    return " — ".join(parts) if parts else title


def truncate_label(title: str, max_len: int = 500) -> str:
    """Shorten display labels while keeping the beginning of the case title.

    #355: also normalize non-breaking spaces (U+00A0) to a regular space so
    a label is never emitted with an NBSP even when truncated or when this
    helper is invoked directly on a not-yet-``clean_title``-d string.
    """
    title = title.replace(" ", " ")
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
  # #343: the broad ``cdm:case-law`` branch also matches CELEX **sector 8**
  # works — national-court decisions citing EU law (Riigikohus, Tallinna
  # Halduskohus, …). They are not EU-court decisions, so restrict to the
  # CJEU (sector ``6``) and EFTA Court (sector ``E``) namespaces; otherwise
  # ``classify_from_celex`` defaults them to ``EUCourt_CourtOfJustice`` and
  # contaminates the CJEU statistics / ``EUCourtDecision`` class.
  FILTER(STRSTARTS(?celex, "6") || STRSTARTS(?celex, "E"))
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
            # #442: no rdfs:domain — the same property is used on
            # estleg:CourtDecision (Riigikohus). A domain of EUCourtDecision
            # would entail every RK decision is an EU court decision.
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
            "@id": "estleg:eurLexLink",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "EUR-Lex link",
            "rdfs:domain": {"@id": "estleg:EUCourtDecision"},
            "rdfs:range": {"@id": "xsd:anyURI"},
            "rdfs:comment": {
                "@value": "EUR-Lex ET TXT URL for the decision (https://eur-lex.europa.eu/legal-content/ET/TXT/?uri=CELEX:<celex>).",
                "@language": "en",
            },
        },
    ])

    return nodes


def decision_to_node(item: dict) -> dict:
    """Convert a case-law dict to a JSON-LD node."""
    safe_celex = sanitize_celex(item["celex"])
    type_id, type_label, court_id, _ = classify_from_celex(item["celex"])

    cleaned_title = clean_title(item["title"])
    case_numbers = extract_case_numbers(item["title"])

    node: dict = {
        "@id": f"estleg:EUCJ_{safe_celex}",
        "@type": ["owl:NamedIndividual", "estleg:EUCourtDecision"],
        "rdfs:label": {"@value": truncate_label(cleaned_title), "@language": "et"},
        "dcterms:title": {"@value": cleaned_title, "@language": "et"},
        "estleg:celexNumber": item["celex"],
        "estleg:euCourtDecisionType": {"@id": f"estleg:EUDecType_{type_id}"},
        "estleg:euCourt": {"@id": f"estleg:EUCourt_{court_id}"},
    }

    # EUR-Lex ET TXT link (shared property name with the legislation corpus)
    eurlex_link = f"https://eur-lex.europa.eu/legal-content/ET/TXT/?uri=CELEX:{item['celex']}"
    node["estleg:eurLexLink"] = {"@value": eurlex_link, "@type": "xsd:anyURI"}

    node["dcterms:source"] = {"@id": cellar_celex_iri(item["celex"])}

    # CELLAR CELEX PSI always; ECLI PSI when the work has an identifier (#441).
    same_as: list[dict] = [{"@id": cellar_celex_iri(item["celex"])}]
    if item.get("ecli"):
        node["estleg:ecliIdentifier"] = item["ecli"]
        same_as.append({"@id": cellar_ecli_iri(item["ecli"])})
    node["owl:sameAs"] = same_as

    # Case number(s). Joined cases (#606) may carry several, so emit them all;
    # a single case stays a one-element list (JSON-LD-equivalent to the prior
    # scalar). NOTE (deferred): the SHACL ``EUCourtDecisionShape`` still pins
    # ``estleg:euCaseNumber`` to ``sh:maxCount 1``; a multi-valued list needs a
    # follow-up maxCount relaxation before the corpus is regenerated (no regen
    # happens here, so nothing breaks today). When the title has no C/T/F/P/E
    # token, derive one from the CELEX so court opinions are not left blank.
    if case_numbers:
        node["estleg:euCaseNumber"] = case_numbers
    else:
        derived = derive_case_number_from_celex(item["celex"])
        if derived:
            node["estleg:euCaseNumber"] = [derived]

    # Date — validate before emitting an ``xsd:date`` literal. CELLAR has
    # served malformed/partial dates; an unchecked value breaks downstream
    # SHACL. Mirrors the RK ``decisionDate`` guard in
    # ``generate_court_decisions.py`` (skip + log, keep the rest of the node).
    raw_date = item.get("date")
    if raw_date:
        try:
            parsed_date = datetime.strptime(raw_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            print(
                f"  WARNING: skipping non-YYYY-MM-DD documentDate "
                f"{raw_date!r} for CELEX {item['celex']}",
                file=sys.stderr,
            )
        else:
            # Range guard (#606): a date can be FORMAT-valid yet implausible —
            # CELLAR has served years like ``0044``. EU/EEC/ECSC case-law
            # starts ~1952, so only accept years in [1950, current_year + 1].
            max_year = datetime.now().year + 1
            if 1950 <= parsed_date.year <= max_year:
                node["estleg:documentDate"] = {"@value": raw_date, "@type": "xsd:date"}
            else:
                print(
                    f"  WARNING: skipping out-of-range documentDate "
                    f"{raw_date!r} (year {parsed_date.year}, allowed "
                    f"[1950, {max_year}]) for CELEX {item['celex']}",
                    file=sys.stderr,
                )

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
    stamp_combined_dataset_head(
        combined_doc,
        label="Estonian Legal Ontology — CURIA combined",
    )
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
