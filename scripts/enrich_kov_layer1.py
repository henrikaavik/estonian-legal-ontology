"""KOV integration Layer 1 enrichment orchestrator.

Reads:
  - data/ehak/municipalities.json
  - data/ehak/issuers.json
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

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kov_registry import (
    IssuerEntry,
    Municipality,
    load_municipalities,
    normalize_title,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
EHAK_DIR = REPO_ROOT / "data" / "ehak"
KOV_DIR = KRR_DIR / "regulations" / "kov"

MUNICIPALITIES_OUT = KRR_DIR / "municipalities_peep.json"
ISSUERS_OUT = KRR_DIR / "issuers_kov_peep.json"

CONTEXT = {
    "estleg": "https://data.riik.ee/ontology/estleg#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
}


def municipality_iri(ehak_code: str) -> str:
    return f"estleg:Municipality_EHAK_{ehak_code}"


def issuer_iri(slug: str) -> str:
    return f"estleg:Issuer_{slug}"


def build_municipality_doc(municipalities: dict[str, Municipality]) -> dict:
    """Build the JSON-LD document containing all Municipality nodes."""
    nodes = [
        {
            "@id": "estleg:Municipalities_Map_2026",
            "@type": ["owl:Ontology"],
            "rdfs:label": "Estonian Municipalities (current EHAK)",
            "dcterms:source": "https://www.stat.ee/sites/default/files/2020-03/ehak.csv",
        }
    ]
    for code in sorted(municipalities):
        mun = municipalities[code]
        nodes.append({
            "@id": municipality_iri(code),
            "@type": ["owl:NamedIndividual", "estleg:Municipality"],
            "rdfs:label": mun["name"],
            "estleg:ehakCode": code,
            "estleg:county": mun["county"],
        })
    return {"@context": CONTEXT, "@graph": nodes}


def build_issuer_doc(issuers: dict[str, IssuerEntry]) -> dict:
    """Build the JSON-LD document containing all Issuer nodes."""
    nodes: list[dict] = [
        {
            "@id": "estleg:Issuers_Kov_Map_2026",
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
        }
        if entry["mappingEvidence"]:
            node["estleg:mappingEvidence"] = entry["mappingEvidence"]
        if entry["historicalMunicipalityName"]:
            node["estleg:historicalMunicipalityName"] = entry["historicalMunicipalityName"]
        nodes.append(node)
    return {"@context": CONTEXT, "@graph": nodes}


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


def enrich_kov_act_file(path: Path, issuer: IssuerEntry) -> None:
    """Add Layer 1 properties to a single KOV act peep file in place.

    Raises ValueError if the file does not contain exactly one node
    typed as estleg:MunicipalRegulation. A KOV file under the corpus
    must have that node — silently skipping malformed files would let
    Gate A pass while leaving acts undecorated.

    Idempotent — running on an already-enriched file leaves it
    byte-identical.
    """
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)

    issuer_ref = {"@id": issuer_iri(issuer["slug"])}
    municipality_ref = {"@id": municipality_iri(issuer["currentMunicipalityCode"])}

    # Pass 1: collect every MunicipalRegulation node, enforce exactly one,
    # then enrich it.
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

    act_node = act_nodes[0]
    act_iri = act_node.get("@id")
    # Prefer dc:source over rdfs:label: the canonical KOV peep shape
    # has rdfs:label with the document-type suffix appended (e.g.
    # "Tallinna jäätmehoolduseeskiri (määrus)") while dc:source carries
    # the clean title ("Tallinna jäätmehoolduseeskiri"). The clean title
    # is what we want to feed normalize_title.
    title = act_node.get("dc:source") or act_node.get("rdfs:label") or ""
    # Stamp estleg:Act directly so SHACL constraints with
    # sh:class estleg:Act on partOfAct don't require RDFS inference
    # at validation time.
    _add_type(act_node, "estleg:Act")
    act_node["estleg:enactedBy"] = issuer_ref
    act_node["estleg:enactedByMunicipality"] = municipality_ref
    act_node["estleg:titleNormalized"] = normalize_title(
        title, issuer["displayName"]
    )

    # Pass 2: enrich provisions.
    for node in doc.get("@graph", []):
        if "estleg:paragrahv" not in node:
            continue
        _add_type(node, "estleg:KovProvision")
        node["estleg:enactedBy"] = issuer_ref
        node["estleg:enactedByMunicipality"] = municipality_ref
        node["estleg:partOfAct"] = {"@id": act_iri}

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def main() -> int:
    print("KOV layer 1 enrichment — TODO: full pipeline (see later tasks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
