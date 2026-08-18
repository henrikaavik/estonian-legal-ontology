"""#518 — Municipality EHAK seeAlso + curated Wikidata sameAs.

Honest slice: every current Municipality gets an EHAK classifier
``rdfs:seeAlso``; ``owl:sameAs`` Wikidata is emitted only for QIDs in
``data/ehak/municipality_wikidata.json``. No network at test time.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from enrich_kov_layer1 import (  # noqa: E402
    ehak_classifier_iri,
    municipality_identity_links,
    wikidata_entity_iri,
)

REPO = Path(__file__).resolve().parent.parent
MUNICIPALITIES_PEEP = REPO / "krr_outputs" / "municipalities_peep.json"
VOID_TTL = REPO / "krr_outputs" / "void.ttl"
WIKIDATA_ENTITY = "wikidata.org/entity/Q"


def _types(node: dict) -> list[str]:
    raw = node.get("@type", [])
    return [raw] if isinstance(raw, str) else list(raw or [])


def _id_values(value: object) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    iris: list[str] = []
    for item in items:
        if isinstance(item, dict):
            iri = item.get("@id")
            if isinstance(iri, str):
                iris.append(iri)
        elif isinstance(item, str):
            iris.append(item)
    return iris


def _municipality_nodes(doc: dict) -> list[dict]:
    return [
        node
        for node in doc.get("@graph", [])
        if isinstance(node, dict) and "estleg:Municipality" in _types(node)
    ]


def test_emitter_see_also_without_qid() -> None:
    links = municipality_identity_links("0784")
    assert links["rdfs:seeAlso"] == {"@id": ehak_classifier_iri("0784")}
    assert "owl:sameAs" not in links
    assert "code=0784" in links["rdfs:seeAlso"]["@id"]


def test_emitter_same_as_when_qid_given() -> None:
    links = municipality_identity_links("0784", "Q1770")
    assert links["rdfs:seeAlso"] == {"@id": ehak_classifier_iri("0784")}
    assert links["owl:sameAs"] == {"@id": wikidata_entity_iri("Q1770")}
    assert links["owl:sameAs"]["@id"] == "http://www.wikidata.org/entity/Q1770"


def test_every_municipality_has_seealso() -> None:
    doc = json.loads(MUNICIPALITIES_PEEP.read_text(encoding="utf-8"))
    nodes = _municipality_nodes(doc)
    assert nodes, "municipalities_peep.json must contain Municipality nodes"
    for node in nodes:
        seealso = _id_values(node.get("rdfs:seeAlso"))
        assert seealso, f"{node.get('@id')} missing rdfs:seeAlso"
        code = node["estleg:ehakCode"]
        assert ehak_classifier_iri(code) in seealso


def test_tallinn_sameas_q1770() -> None:
    doc = json.loads(MUNICIPALITIES_PEEP.read_text(encoding="utf-8"))
    tallinn = next(
        node for node in _municipality_nodes(doc) if node.get("rdfs:label") == "Tallinn"
    )
    sameas = _id_values(tallinn.get("owl:sameAs"))
    assert "http://www.wikidata.org/entity/Q1770" in sameas


def test_at_least_one_wikidata_entity_in_peep() -> None:
    text = MUNICIPALITIES_PEEP.read_text(encoding="utf-8")
    assert text.count(WIKIDATA_ENTITY) >= 1


def test_void_mentions_wikidata() -> None:
    text = VOID_TTL.read_text(encoding="utf-8")
    assert "wikidata" in text.lower()
