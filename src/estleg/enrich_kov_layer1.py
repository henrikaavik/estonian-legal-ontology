"""KOV integration Layer 1 enrichment orchestrator.

Reads:
  - data/ehak/municipalities.json
  - data/ehak/municipality_wikidata.json (curated EHAK → Wikidata QIDs, #518)
  - data/ehak/issuers.json
  - data/ehak/counties.json (EHAK county codes, #712)
  - krr_outputs/regulations/kov/<issuer>/*_peep.json
  - krr_outputs/*_peep.json (laws)

Writes:
  - krr_outputs/municipalities_peep.json (new)
  - krr_outputs/issuers_kov_peep.json (new)
  - in-place updates to KOV act + provision files
  - in-place stamp of estleg:Law on existing law nodes

Idempotent: safe to re-run.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from estleg.estleg_common import CONTEXT, NS, compact_iri_local, mint_act_iri, save_json
from estleg.kov_registry import (
    HistoricalMunicipality,
    IssuerEntry,
    Municipality,
    extract_historical_municipalities,
    issuer_slug_from_iri,
    load_municipalities,
    municipality_status,
    normalize_title,
    parse_issuer_slug,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
KRR_DIR = REPO_ROOT / "krr_outputs"
EHAK_DIR = REPO_ROOT / "data" / "ehak"
KOV_DIR = KRR_DIR / "regulations" / "kov"

MUNICIPALITIES_OUT = KRR_DIR / "municipalities_peep.json"
MUNICIPALITY_WIKIDATA = EHAK_DIR / "municipality_wikidata.json"
ISSUERS_OUT = KRR_DIR / "issuers_kov_peep.json"
# Historical (pre-merger) municipality nodes — issue #130. Written under
# data/ehak/ (alongside municipalities.json / issuers.json, the source
# KOV registry files) rather than krr_outputs/ so it does not bump the
# krr_outputs/ file-count statistics validated against metadata.jsonld.
# It is added to the `kov` SHACL bucket in shacl_validate_all.py.
HISTORICAL_MUNICIPALITIES_OUT = EHAK_DIR / "historical_municipalities.jsonld"
# EHAK county (maakond) codes keyed by the county label used in
# municipalities.json (#712). Optional: a missing file means no countyCode.
COUNTIES_PATH = EHAK_DIR / "counties.json"
_COUNTY_CODE_RE = re.compile(r"^00[0-9]{2}$")
# Statistics Estonia public classifier; ``code`` is the 4-digit EHAK id.
EHAK_CLASSIFIER_IRI = (
    "https://metaweb.stat.ee/klassifikaator_avalik?id=EHAK&code={code}"
)
WIKIDATA_ENTITY_PREFIX = "http://www.wikidata.org/entity/"
_QID_RE = re.compile(r"^Q[1-9][0-9]*$")

# >5 missing law files in INDEX.json indicates real corpus drift,
# not the occasional stale entry. Hard-fail in that case so the
# orchestrator does not silently keep running on degraded input
# (Finding #4 in issue #182).
MISSING_LAW_FILES_FAIL_THRESHOLD = 5



def municipality_iri(ehak_code: str) -> str:
    return f"estleg:Municipality_EHAK_{ehak_code}"


def issuer_iri(slug: str) -> str:
    return f"estleg:Issuer_{slug}"


def historical_municipality_iri(former_ehak_code: str) -> str:
    return f"estleg:HistoricalMunicipality_{former_ehak_code}"


def ehak_classifier_iri(ehak_code: str) -> str:
    return EHAK_CLASSIFIER_IRI.format(code=ehak_code)


def wikidata_entity_iri(qid: str) -> str:
    if not qid.startswith("Q"):
        qid = f"Q{qid}"
    return f"{WIKIDATA_ENTITY_PREFIX}{qid}"


def load_municipality_wikidata(path: Path) -> dict[str, str]:
    """Load curated EHAK code → Wikidata QID. Missing file → empty map."""
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError(
            f"municipality_wikidata.json must be an object, got {type(payload).__name__}"
        )
    out: dict[str, str] = {}
    for code, value in payload.items():
        if code.startswith("_"):
            continue
        if not (len(code) == 4 and code.isdigit()):
            raise ValueError(f"EHAK code must be a 4-digit string, got {code!r}")
        if isinstance(value, str):
            qid = value
        elif isinstance(value, dict):
            qid = value.get("qid") or value.get("wikidata")
        else:
            raise ValueError(f"Invalid Wikidata entry for {code}: {value!r}")
        if not isinstance(qid, str) or not _QID_RE.fullmatch(qid):
            raise ValueError(f"Invalid Wikidata QID for {code}: {qid!r}")
        out[code] = qid
    return out


def load_county_codes(path: Path) -> dict[str, str]:
    """County label (``"Harju maakond"``) → 4-character EHAK county code.

    Missing file → empty map (the builder then omits ``estleg:countyCode``).
    Malformed entries raise rather than silently emitting a bad code.
    """
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    rows = payload.get("counties") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected an object with a 'counties' list")
    out: dict[str, str] = {}
    for row in rows:
        code = row.get("ehakCode") if isinstance(row, dict) else None
        name = row.get("name") if isinstance(row, dict) else None
        if not isinstance(code, str) or not _COUNTY_CODE_RE.fullmatch(code):
            raise ValueError(f"{path}: invalid EHAK county code {code!r}")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{path}: county {code} has no name")
        if name in out:
            raise ValueError(f"{path}: duplicate county name {name!r}")
        out[name] = code
    return out


def historical_municipality_by_issuer(
    historical: dict[str, HistoricalMunicipality],
) -> dict[str, str]:
    """Issuer slug → pre-merger EHAK code, inverted from ``issuerSlugs`` (#712).

    ``extract_historical_municipalities`` already collects the issuer slugs
    that map to each pre-merger unit; this is the lookup that lets the
    issuer and act nodes carry an IRI edge to the
    ``HistoricalMunicipality`` individual instead of only the slug-derived
    ``historicalMunicipalityName`` literal.
    """
    out: dict[str, str] = {}
    for code in sorted(historical):
        for slug in historical[code]["issuerSlugs"]:
            if slug in out and out[slug] != code:
                raise ValueError(
                    f"issuer {slug!r} maps to two historical municipalities "
                    f"({out[slug]} and {code})"
                )
            out[slug] = code
    return out


def municipality_identity_links(
    ehak_code: str, wikidata_qid: str | None = None
) -> dict:
    """EHAK ``rdfs:seeAlso`` always; Wikidata ``owl:sameAs`` only when curated."""
    links: dict = {
        "rdfs:seeAlso": {"@id": ehak_classifier_iri(ehak_code)},
    }
    if wikidata_qid:
        links["owl:sameAs"] = {"@id": wikidata_entity_iri(wikidata_qid)}
    return links


def build_municipality_doc(
    municipalities: dict[str, Municipality],
    wikidata_by_code: dict[str, str] | None = None,
    county_codes: dict[str, str] | None = None,
) -> dict:
    """Build the JSON-LD document containing all Municipality nodes.

    ``estleg:county`` keeps the county label; ``estleg:countyCode`` (#712)
    adds the EHAK county code when the label is in ``county_codes``.
    """
    if wikidata_by_code is None:
        wikidata_by_code = load_municipality_wikidata(
            EHAK_DIR / MUNICIPALITY_WIKIDATA.name
        )
    if county_codes is None:
        county_codes = load_county_codes(EHAK_DIR / COUNTIES_PATH.name)
    nodes = [
        {
            "@id": mint_act_iri("Municipalities"),
            "@type": ["owl:Ontology"],
            "rdfs:label": "Estonian Municipalities (current EHAK)",
            "dcterms:source": {"@id": "https://www.stat.ee/sites/default/files/2020-03/ehak.csv"},
        }
    ]
    for code in sorted(municipalities):
        mun = municipalities[code]
        node = {
            "@id": municipality_iri(code),
            "@type": ["owl:NamedIndividual", "estleg:Municipality"],
            "rdfs:label": mun["name"],
            "estleg:ehakCode": code,
            "estleg:county": mun["county"],
        }
        county_code = county_codes.get(mun["county"])
        if county_code:
            node["estleg:countyCode"] = county_code
        node.update(municipality_identity_links(code, wikidata_by_code.get(code)))
        nodes.append(node)
    return {"@context": CONTEXT, "@graph": nodes}


def build_issuer_doc(
    issuers: dict[str, IssuerEntry],
    historical_by_slug: dict[str, str] | None = None,
) -> dict:
    """Build the JSON-LD document containing all Issuer nodes.

    Issuers of an abolished (pre-merger) municipality also carry
    ``estleg:historicalMunicipality`` → the ``HistoricalMunicipality``
    individual (#712). ``historical_by_slug`` defaults to the mapping
    derived from ``issuers`` themselves.
    """
    if historical_by_slug is None:
        historical_by_slug = historical_municipality_by_issuer(
            extract_historical_municipalities(list(issuers.values()))
        )
    nodes: list[dict] = [
        {
            "@id": mint_act_iri("Issuers_Kov"),
            "@type": ["owl:Ontology"],
            "rdfs:label": "KOV Issuers (volikogu and valitsus bodies)",
        }
    ]
    for slug in sorted(issuers):
        entry = issuers[slug]
        node: dict = {
            "@id": issuer_iri(slug),
            "@type": ["owl:NamedIndividual", "estleg:Issuer"],
            "rdfs:label": entry["displayName"],
            "estleg:bodyType": entry["bodyType"],
            "estleg:currentMunicipality": {
                "@id": municipality_iri(entry["currentMunicipalityCode"])
            },
            "estleg:mappingSource": entry["mappingSource"],
            "estleg:municipalityStatus": municipality_status(
                entry["mappingSource"], entry["historicalMunicipalityName"]
            ),
        }
        if entry["mappingEvidence"]:
            node["estleg:mappingEvidence"] = entry["mappingEvidence"]
        if entry["historicalMunicipalityName"]:
            node["estleg:historicalMunicipalityName"] = entry["historicalMunicipalityName"]
        former_code = historical_by_slug.get(slug)
        if former_code:
            node["estleg:historicalMunicipality"] = {
                "@id": historical_municipality_iri(former_code)
            }
        nodes.append(node)
    return {"@context": CONTEXT, "@graph": nodes}


def issuer_status_map(issuers_doc: dict) -> dict[str, str]:
    """slug → ``current``/``abolished`` from an issuers JSON-LD doc."""
    out: dict[str, str] = {}
    for node in issuers_doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        slug = issuer_slug_from_iri(node.get("@id"))
        status = node.get("estleg:municipalityStatus")
        if slug and status in {"current", "abolished"}:
            out[slug] = status
    return out


def stamp_kov_act_municipality_status(act_node: dict, status_by_slug: dict[str, str]) -> bool:
    """Copy issuer status onto a MunicipalRegulation act node (#526)."""
    slug = issuer_slug_from_iri(act_node.get("estleg:enactedBy"))
    if not slug:
        return False
    status = status_by_slug.get(slug)
    if status is None:
        return False
    if act_node.get("estleg:municipalityStatus") == status:
        return False
    act_node["estleg:municipalityStatus"] = status
    return True


def stamp_kov_act_historical_municipality(
    act_node: dict, historical_by_slug: dict[str, str],
) -> bool:
    """Set ``estleg:enactedByHistoricalMunicipality`` on a KOV act (#712).

    Acts of a body of an abolished municipality point at the pre-merger
    ``HistoricalMunicipality``; ``estleg:enactedByMunicipality`` keeps
    pointing at the current successor. Acts of a current municipality get
    the property removed (strip-then-set, so re-runs are idempotent).
    Returns True iff the node changed.
    """
    prop = "estleg:enactedByHistoricalMunicipality"
    slug = issuer_slug_from_iri(act_node.get("estleg:enactedBy"))
    former_code = historical_by_slug.get(slug) if slug else None
    if former_code is None:
        return act_node.pop(prop, None) is not None
    ref = {"@id": historical_municipality_iri(former_code)}
    if act_node.get(prop) == ref:
        return False
    act_node[prop] = ref
    return True


def build_historical_municipality_doc(
    historical: dict[str, HistoricalMunicipality],
) -> dict:
    """Build the JSON-LD document containing all HistoricalMunicipality nodes.

    One node per distinct pre-merger EHAK code (deduped across the
    multiple issuers that map to it). Each node carries
    ``estleg:formerEhakCode`` / ``estleg:formerName`` /
    ``estleg:succeededBy`` (always), plus ``estleg:mergedAt`` /
    ``estleg:mergerEvidence`` / ``estleg:municipalityType`` where
    derivable. Nodes are emitted in ascending former-EHAK order for a
    stable on-disk diff. The ``issuerSlugs`` carried on the in-memory
    :class:`HistoricalMunicipality` records are used for coverage
    the inverse ``estleg:historicalMunicipality`` edge on issuer nodes and
    ``estleg:enactedByHistoricalMunicipality`` on acts (#712), so they are
    not repeated on the historical nodes themselves.
    """
    nodes: list[dict] = [
        {
            "@id": mint_act_iri("HistoricalMunicipalities"),
            "@type": ["owl:Ontology"],
            "rdfs:label": "Estonian Historical Municipalities (pre-merger KOV units)",
            "rdfs:comment": (
                "Former Estonian municipalities abolished by territorial "
                "reform (chiefly the 2017 haldusreform). Each node links "
                "to its surviving successor via estleg:succeededBy. "
                "Derived from the issuer registry's mappingEvidence "
                "citations; see issue #130."
            ),
            "dcterms:source": {
                "@id": f"{NS}{compact_iri_local(mint_act_iri('Issuers_Kov'))}"
            },
        }
    ]
    for code in sorted(historical):
        hist = historical[code]
        node: dict = {
            "@id": historical_municipality_iri(code),
            "@type": ["owl:NamedIndividual", "estleg:HistoricalMunicipality"],
            "rdfs:label": hist["formerName"],
            "estleg:formerEhakCode": code,
            "estleg:formerName": hist["formerName"],
            "estleg:succeededBy": {
                "@id": municipality_iri(hist["succeededByCode"])
            },
        }
        if hist.get("municipalityType"):
            node["estleg:municipalityType"] = hist["municipalityType"]
        if hist.get("mergedAt"):
            node["estleg:mergedAt"] = {
                "@value": hist["mergedAt"],
                "@type": "xsd:date",
            }
        if hist.get("mergerEvidence"):
            node["estleg:mergerEvidence"] = hist["mergerEvidence"]
        nodes.append(node)
    return {"@context": CONTEXT, "@graph": nodes}


_REGULATION_TYPES = {
    "estleg:NationalRegulation",
    "estleg:GovernmentRegulation",
    "estleg:MinisterialRegulation",
    "estleg:MunicipalRegulation",
}


def _add_type(node: dict, type_iri: str) -> bool:
    """Add type_iri to node["@type"] if missing. Returns True if changed."""
    types = node.get("@type")
    if isinstance(types, str):
        types = [types]
    elif types is None:
        types = []
    if type_iri in types:
        return False
    types.append(type_iri)
    node["@type"] = types
    return True


def is_stampable_law_node(types: list[str]) -> bool:
    """Return True iff a node should be stamped by `stamp_law_type`.

    A node is "applicable" for Law-stamping iff it carries
    `owl:Ontology` and is NOT already typed as a regulation subclass
    (those go through `stamp_act_type`). This helper exists so
    `verify_layer1.check_laws` and `stamp_law_type` share exactly one
    definition of "applicable" — without it, the two could drift and
    silently mask coverage gaps (Finding #1 in issue #182).
    """
    if "estleg:Act" not in types and "owl:Ontology" not in types:
        return False
    return not _REGULATION_TYPES.intersection(types)


def stamp_law_type(path: Path) -> bool:
    """Add `estleg:Law` and `estleg:Act` rdf:type to the act node of a
    law peep file.

    Skips files whose act node already has a regulation-specific type
    (NationalRegulation, MunicipalRegulation, etc.) — those go through
    `stamp_act_type` instead.

    Returns True if the file contained at least one applicable node
    (i.e. an owl:Ontology node that is not already a regulation
    subclass) and got stamped (or was already stamped). Returns False
    when no applicable node exists — sub-part files such as
    `*_osa6_peep.json` lack the parent ontology node and are filtered
    out by `_load_law_paths`/`verify_layer1.check_laws` via
    `is_stampable_law_node`. The orchestrator uses the return value
    to compare `stamped` against `expected_applicable` and surface a
    coverage gap loudly (Finding #1).

    Idempotent: re-running on an already-stamped file is a no-op (no
    write, so mtime does not churn — Finding #2).
    """
    with open(path, "r", encoding="utf-8") as fh:
        original_text = fh.read()
    doc = json.loads(original_text)

    applicable = False
    for node in doc.get("@graph", []):
        types = node.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if not is_stampable_law_node(types):
            continue
        applicable = True
        _add_type(node, "estleg:Law")
        _add_type(node, "estleg:Act")
        # Sort once at the end for stable on-disk order; do this even
        # when nothing was added so that the in-memory list mirrors
        # what we'd write — keeps the comparison consistent and the
        # idempotency contract explicit.
        node["@type"] = sorted(set(node["@type"]))

    new_text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    if new_text != original_text:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
    return applicable


def stamp_act_type(path: Path) -> None:
    """Add `estleg:Act` to act nodes already typed as a regulation
    subclass (NationalRegulation / GovernmentRegulation /
    MinisterialRegulation). For state regulation files under
    `regulations/riik/`. Idempotent.

    KOV act nodes are handled by `enrich_kov_act_file`; this function
    is for the state regulation files that Layer 1 otherwise leaves
    alone. It is an error to call this on a file containing a
    `MunicipalRegulation` node — the caller selected the wrong
    pipeline. We raise rather than silently skip so a wiring bug in
    the orchestrator surfaces here, not as silently mis-stamped state
    files (Finding #11).
    """
    with open(path, "r", encoding="utf-8") as fh:
        original_text = fh.read()
    doc = json.loads(original_text)

    for node in doc.get("@graph", []):
        types = node.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if "estleg:Act" not in types and "owl:Ontology" not in types:
            continue
        if "estleg:MunicipalRegulation" in types:
            raise ValueError(
                f"{path}: stamp_act_type called on a KOV act file "
                "(node has estleg:MunicipalRegulation). KOV acts are "
                "stamped by enrich_kov_act_file."
            )
        if not _REGULATION_TYPES.intersection(types):
            continue
        if _add_type(node, "estleg:Act"):
            node["@type"] = sorted(set(node["@type"]))

    new_text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    if new_text != original_text:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)


def _find_kov_act_node(doc: dict, path: Path) -> dict:
    """Return the single ``estleg:MunicipalRegulation`` node or raise."""
    act_nodes: list[dict] = []
    for node in doc.get("@graph", []):
        types = node.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if "estleg:MunicipalRegulation" in types:
            act_nodes.append(node)
    if len(act_nodes) == 0:
        raise ValueError(
            f"{path}: no estleg:MunicipalRegulation node in @graph; "
            "expected a KOV act file"
        )
    if len(act_nodes) > 1:
        raise ValueError(
            f"{path}: found {len(act_nodes)} estleg:MunicipalRegulation "
            "nodes; KOV files must contain exactly one"
        )
    return act_nodes[0]


def _build_enriched_act_doc(
    doc: dict,
    issuer: IssuerEntry,
    path: Path,
    historical_by_slug: dict[str, str] | None = None,
) -> dict:
    """Compute the enriched JSON-LD doc for a KOV act file.

    Mutates and returns `doc` (callers pass an independent in-memory
    copy when they need the original for comparison). Raises
    ValueError on malformed input — exactly the same conditions that
    `enrich_kov_act_file` reports.
    """
    issuer_ref = {"@id": issuer_iri(issuer["slug"])}
    municipality_ref = {"@id": municipality_iri(issuer["currentMunicipalityCode"])}

    # Pass 1: find the single MunicipalRegulation node, then enrich it.
    act_node = _find_kov_act_node(doc, path)
    act_iri = act_node.get("@id")
    if not isinstance(act_iri, str) or not act_iri:
        raise ValueError(f"{path}: MunicipalRegulation node has no @id")
    # Prefer dc:source over rdfs:label: the canonical KOV peep shape
    # has rdfs:label with the document-type suffix appended (e.g.
    # "Tallinna jäätmehoolduseeskiri (määrus)") while dc:source
    # carries the clean title ("Tallinna jäätmehoolduseeskiri"). The
    # clean title is what we want to feed normalize_title.
    title = act_node.get("dc:source") or act_node.get("rdfs:label") or ""
    # Stamp estleg:Act directly so SHACL constraints with
    # sh:class estleg:Act on partOfAct don't require RDFS inference
    # at validation time.
    _add_type(act_node, "estleg:Act")
    act_node["estleg:enactedBy"] = issuer_ref
    act_node["estleg:enactedByMunicipality"] = municipality_ref
    # Pass slug parts (not the display-name string) so normalize_title
    # can gate the alevi-genitive prefix on (municipalityType, root)
    # rather than blindly stripping it from any title that happens to
    # start with `<root> alevi `.
    act_node["estleg:titleNormalized"] = normalize_title(
        title, parse_issuer_slug(issuer["slug"]),
    )
    if historical_by_slug is not None:
        stamp_kov_act_historical_municipality(act_node, historical_by_slug)

    # Pass 2: enrich provisions.
    for node in doc.get("@graph", []):
        if "estleg:paragrahv" not in node:
            continue
        _add_type(node, "estleg:KovProvision")
        node["estleg:enactedBy"] = issuer_ref
        node["estleg:enactedByMunicipality"] = municipality_ref
        node["estleg:partOfAct"] = {"@id": act_iri}

    return doc


def enrich_kov_act_file(
    path: Path,
    issuer: IssuerEntry,
    historical_by_slug: dict[str, str] | None = None,
) -> bool:
    """Add Layer 1 properties to a single KOV act peep file in place.

    Raises ValueError if the file does not contain exactly one node
    typed as estleg:MunicipalRegulation. A KOV file under the corpus
    must have that node — silently skipping malformed files would let
    Gate A pass while leaving acts undecorated.

    Returns True iff the file was actually written. Idempotent — when
    the on-disk content already matches the enriched form, the file
    is NOT re-written so mtimes do not churn (Finding #2).
    """
    with open(path, "r", encoding="utf-8") as fh:
        original_text = fh.read()
    original_doc = json.loads(original_text)

    # Independent in-memory copy so the comparison below sees the
    # pre-enrichment shape. Round-tripping through JSON is fine here:
    # peep docs are pure data (no recursive refs, no datetimes).
    enriched_doc = _build_enriched_act_doc(
        json.loads(original_text), issuer, path, historical_by_slug,
    )

    if enriched_doc == original_doc:
        return False

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(enriched_doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return True


def stamp_kov_act_file_historical(
    path: Path, historical_by_slug: dict[str, str],
) -> bool:
    """Stamp ONLY ``enactedByHistoricalMunicipality`` on a KOV act file.

    The narrow entry point behind ``--historical-only``: unlike
    :func:`enrich_kov_act_file` it does not recompute ``titleNormalized``
    or the provision-level Layer 1 fields, so it cannot churn unrelated
    keys. Writes atomically (same formatting) only when the act changed.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not stamp_kov_act_historical_municipality(
        _find_kov_act_node(doc, path), historical_by_slug,
    ):
        return False
    save_json(path, doc)
    return True


def _load_issuers(path: Path) -> dict[str, IssuerEntry]:
    with open(path, "r", encoding="utf-8") as fh:
        rows = json.load(fh)
    return {row["slug"]: row for row in rows}


def _save_jsonld(path: Path, doc: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _load_law_paths(index_path: Path) -> tuple[list[Path], list[str]]:
    """Resolve law file paths from krr_outputs/INDEX.json.

    INDEX.json's `laws` is a list of {name, files: [...]}. A multi-part
    law (e.g. asjaoigusseadus_osa1, _osa2) has multiple files in one
    entry. Flatten and resolve to absolute paths under KRR_DIR.

    INDEX.json is the canonical source — using KRR_DIR.glob('*_peep.json')
    instead would conflate the 615 laws with other root-level files
    (combined ontology output, generated registry files, etc.) and would
    drift over time.

    Policy on missing entries: a small number of stale INDEX entries
    (typos, renames not propagated) is logged but tolerated. More than
    `MISSING_LAW_FILES_FAIL_THRESHOLD` missing files is treated as
    real corpus drift and the caller hard-fails (see `main()`,
    Finding #4).

    Mixed extensions: INDEX entries end in either `.json` or `.jsonld`.
    Both are accepted as-is; we do NOT silently substitute the
    alternate extension because that could mask real renames.

    Returns: (resolved_paths, missing_filenames)
    """
    with open(index_path, "r", encoding="utf-8") as fh:
        idx = json.load(fh)
    paths: list[Path] = []
    missing: list[str] = []
    for entry in idx.get("laws", []):
        for fname in entry.get("files", []):
            p = KRR_DIR / fname
            if p.exists():
                paths.append(p)
            else:
                missing.append(fname)
    return paths, missing


def _enrich_one_kov(
    args: tuple[Path, IssuerEntry, dict[str, str] | None],
) -> tuple[Path, str | None]:
    """Worker for parallel KOV enrichment.

    Returns (path, error_message_or_None). The serialised error
    message survives the cross-process boundary cleanly; raising
    inside the worker would lose argument fidelity in the parent.
    """
    path, issuer, historical_by_slug = args
    try:
        enrich_kov_act_file(path, issuer, historical_by_slug)
    except (ValueError, OSError) as exc:
        return path, str(exc)
    return path, None


def main(argv: list[str] | None = None) -> int:
    # Default to no arguments rather than `sys.argv[1:]` so callers
    # like the test suite — which run main() inside a pytest process
    # whose own argv contains test filters — don't accidentally pick
    # up unrelated flags. The CLI entry point passes sys.argv[1:]
    # explicitly; tests pass either nothing or e.g. ["--workers=2"].
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Number of parallel workers for the KOV enrichment loop "
             "(default: 1 = serial / deterministic). Use a higher "
             "value for the full 11k-file corpus run (Finding #3).",
    )
    parser.add_argument(
        "--historical-only", action="store_true",
        help="#712: regenerate the municipality / issuer / historical-"
             "municipality registry docs and stamp ONLY "
             "estleg:enactedByHistoricalMunicipality on KOV acts; skip the "
             "full act enrichment and the law / state-regulation stamps.",
    )
    args = parser.parse_args(argv if argv is not None else [])
    if args.workers < 1:
        parser.error("--workers must be >= 1")

    print("=" * 70)
    print("KOV Integration Layer 1 — Enrichment")
    print("=" * 70)

    municipalities = load_municipalities(EHAK_DIR / "municipalities.json")
    issuers = _load_issuers(EHAK_DIR / "issuers.json")
    print(f"Loaded {len(municipalities)} municipalities, {len(issuers)} issuers")

    # 1. Write Municipality JSON-LD
    _save_jsonld(MUNICIPALITIES_OUT, build_municipality_doc(municipalities))
    print(f"Wrote {MUNICIPALITIES_OUT.name}")

    historical = extract_historical_municipalities(
        list(issuers.values()), municipalities,
    )
    historical_by_slug = historical_municipality_by_issuer(historical)

    # 2. Write Issuer JSON-LD
    _save_jsonld(ISSUERS_OUT, build_issuer_doc(issuers, historical_by_slug))
    print(f"Wrote {ISSUERS_OUT.name}")

    # 2b. Write HistoricalMunicipality JSON-LD (issue #130). Derived
    #     from the issuer registry's mappingEvidence citations; one node
    #     per distinct pre-merger EHAK code, deduped across issuers.
    #     Path is resolved from the (monkeypatchable) EHAK_DIR so test
    #     trees that patch EHAK_DIR don't write into the real repo.
    historical_out = EHAK_DIR / HISTORICAL_MUNICIPALITIES_OUT.name
    _save_jsonld(
        historical_out,
        build_historical_municipality_doc(historical),
    )
    print(
        f"Wrote {historical_out.name} "
        f"({len(historical)} HistoricalMunicipality nodes)"
    )

    # 3. Enrich KOV act files
    kov_files = sorted(KOV_DIR.glob("**/*_peep.json"))
    # The KOV index file lives at the top of KOV_DIR — exclude it.
    kov_files = [f for f in kov_files if not f.name.startswith("REGULATIONS_KOV_INDEX")]
    print(f"\nEnriching {len(kov_files)} KOV act files (workers={args.workers})...")

    if args.historical_only:
        stamped = 0
        for f in kov_files:
            if f.parent.name not in issuers:
                print(f"  ERROR: no issuer entry for {f.parent.name}; skipping {f.name}")
                continue
            if stamp_kov_act_file_historical(f, historical_by_slug):
                stamped += 1
        print(
            f"Stamped enactedByHistoricalMunicipality changes on {stamped} "
            f"KOV act files ({len(historical_by_slug)} historical issuers)"
        )
        print("\nDone (--historical-only).")
        return 0

    work_items: list[tuple[Path, IssuerEntry, dict[str, str] | None]] = []
    missing_issuer = 0
    for f in kov_files:
        slug = f.parent.name
        issuer = issuers.get(slug)
        if issuer is None:
            missing_issuer += 1
            print(f"  ERROR: no issuer entry for {slug}; skipping {f.name}")
            continue
        work_items.append((f, issuer, historical_by_slug))

    enriched = 0
    errors: list[tuple[Path, str]] = []
    if args.workers == 1:
        # Serial path — keeps deterministic order and is the default
        # for tests.
        for item in work_items:
            path, err = _enrich_one_kov(item)
            if err is not None:
                errors.append((path, err))
            else:
                enriched += 1
    else:
        # Parallel path — order-insensitive results. The work is
        # naturally per-file so we don't need to pin a worker per
        # issuer slug.
        # Run through ``scripts/enrich_kov_layer1.py`` (runpy, run_name
        # "__main__") this module IS ``__main__``, and a spawned worker cannot
        # unpickle ``__main__._enrich_one_kov``. Hand the pool the function
        # of the importable module so it pickles by its qualified name.
        from estleg import enrich_kov_layer1 as _importable

        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for path, err in pool.map(_importable._enrich_one_kov, work_items):
                if err is not None:
                    errors.append((path, err))
                else:
                    enriched += 1

    print(
        f"Enriched {enriched} KOV act files "
        f"(missing-issuer: {missing_issuer}, errors: {len(errors)})"
    )
    for path, err in errors[:10]:
        print(f"  ERROR: {path}: {err}", file=sys.stderr)
    if len(errors) > 10:
        print(f"  ... and {len(errors) - 10} more errors", file=sys.stderr)
    if missing_issuer or errors:
        # Hard-fail rather than let Gate A pass with skipped files.
        if missing_issuer:
            print(
                f"FAIL: {missing_issuer} KOV files had no matching issuer entry. "
                "Re-run scripts/build_kov_registry.py to regenerate issuers.json.",
                file=sys.stderr,
            )
        if errors:
            print(
                f"FAIL: {len(errors)} KOV files raised during enrichment.",
                file=sys.stderr,
            )
        return 2

    # 4. Stamp estleg:Law + estleg:Act on the 615 laws (paths from INDEX.json)
    law_paths, missing_law_files = _load_law_paths(KRR_DIR / "INDEX.json")
    law_count = len({p.stem.replace("_peep", "").rsplit("_osa", 1)[0]
                     for p in law_paths})
    print(f"\nStamping estleg:Law on {len(law_paths)} law files "
          f"({law_count} unique laws)...")
    if missing_law_files:
        print(f"  WARN: INDEX.json references {len(missing_law_files)} "
              "missing law files (stale index — fix in a separate PR):")
        for f in missing_law_files[:10]:
            print(f"    {f}")
        if len(missing_law_files) > 10:
            print(f"    ... and {len(missing_law_files) - 10} more")
    if len(missing_law_files) > MISSING_LAW_FILES_FAIL_THRESHOLD:
        print(
            f"FAIL: {len(missing_law_files)} missing law files exceeds "
            f"the tolerance threshold of "
            f"{MISSING_LAW_FILES_FAIL_THRESHOLD}. Update INDEX.json or "
            "regenerate the law-file index before re-running Layer 1.",
            file=sys.stderr,
        )
        return 2

    stamped = 0
    skipped_no_ontology = 0
    for f in law_paths:
        if stamp_law_type(f):
            stamped += 1
        else:
            skipped_no_ontology += 1
    # Cross-check with verify_layer1.check_laws's "applicable" filter:
    # both helpers route through `is_stampable_law_node`, so `stamped`
    # is the count of files whose root ontology node was stampable.
    # If any applicable file ended up unstamped we'd have a real bug
    # — print a loud WARN so the gap is visible in CI logs.
    print(
        f"  Stamped {stamped} law files "
        f"({skipped_no_ontology} skipped: sub-part files without an "
        "owl:Ontology node)"
    )
    # #333: the previous `stamped < expected_stamped` comparison was
    # unreachable because `skipped_no_ontology` already counted every
    # False return. Cross-check with verify_layer1.check_laws instead.
    from estleg.verify_layer1 import check_laws

    laws_ok, laws_detail = check_laws()
    if not laws_ok:
        print(
            f"WARN: verify_layer1.check_laws failed after stamping "
            f"({laws_detail}); stamp_law_type / INDEX drift.",
            file=sys.stderr,
        )

    # 5. Stamp estleg:Act on state regulation acts under regulations/riik/
    riik_dir = KRR_DIR / "regulations" / "riik"
    riik_files = sorted(riik_dir.glob("*_peep.json")) if riik_dir.exists() else []
    print(f"\nStamping estleg:Act on {len(riik_files)} state regulation files...")
    for f in riik_files:
        stamp_act_type(f)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
