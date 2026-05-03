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


def main() -> int:
    print("KOV layer 1 enrichment — TODO: full pipeline (see later tasks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
