"""#518 — Municipality EHAK seeAlso + curated Wikidata sameAs.

Honest slice: every current Municipality gets an EHAK classifier
``rdfs:seeAlso``; ``owl:sameAs`` Wikidata is emitted only for QIDs in
``data/ehak/municipality_wikidata.json``. Institutions and major acts
use the sitelink-justified maps in ``data/wikidata_institutions.json``
and ``data/wikidata_acts.json``. Riigikohus decisions with an ECLI
point at the e-Justice resolver. No network at test time.
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
from generate_court_decisions import ecli_see_also_iri  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
MUNICIPALITIES_PEEP = REPO / "krr_outputs" / "municipalities_peep.json"
VOID_TTL = REPO / "krr_outputs" / "void.ttl"
INSTITUTIONS_DIR = REPO / "krr_outputs" / "institutions"
PS_PEEP = REPO / "krr_outputs" / "eesti_vabariigi_pohiseadus_peep.json"
RK_2020 = REPO / "krr_outputs" / "riigikohus" / "riigikohus_2020_peep.json"
WIKIDATA_INSTITUTIONS = REPO / "data" / "wikidata_institutions.json"
WIKIDATA_ACTS = REPO / "data" / "wikidata_acts.json"
WIKIDATA_ENTITY = "wikidata.org/entity/Q"
PIN_2020_ID = "estleg:RK_3_18_1432_93"
# etwiki:Riigikohus / enwiki:Supreme Court of Estonia
WIKIDATA_RIIGIKOHUS = "http://www.wikidata.org/entity/Q1514159"
# etwiki:Eesti Vabariigi põhiseadus / enwiki:Constitution of Estonia
WIKIDATA_PS = "http://www.wikidata.org/entity/Q1370367"


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


def test_ecli_see_also_iri_uses_ejustice_resolver() -> None:
    ecli = "ECLI:EE:RK:2020:3.18.1432.93"
    iri = ecli_see_also_iri(ecli)
    assert iri == f"https://e-justice.europa.eu/ecli/{ecli}"
    assert ecli in iri


def test_institution_riigikohus_sameas() -> None:
    doc = json.loads(
        (INSTITUTIONS_DIR / "institution_riigikohus.json").read_text(encoding="utf-8")
    )
    node = next(
        n
        for n in doc.get("@graph", [])
        if isinstance(n, dict) and n.get("@id") == "estleg:Institution_riigikohus"
    )
    assert node.get("rdfs:label") == "Riigikohus"
    assert WIKIDATA_RIIGIKOHUS in _id_values(node.get("owl:sameAs"))


def test_mapped_institutions_match_labels_and_qids() -> None:
    payload = json.loads(WIKIDATA_INSTITUTIONS.read_text(encoding="utf-8"))
    mapped = {k: v for k, v in payload.items() if not k.startswith("_")}
    assert {"riigikohus", "riigikogu", "vabariigivalitsus", "vabariigipresident"} <= (
        set(mapped)
    )
    for slug, entry in mapped.items():
        qid = entry["qid"] if isinstance(entry, dict) else entry
        path = INSTITUTIONS_DIR / f"institution_{slug}.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        node = next(
            n
            for n in doc.get("@graph", [])
            if isinstance(n, dict) and n.get("@id") == f"estleg:Institution_{slug}"
        )
        if isinstance(entry, dict) and entry.get("label"):
            assert node.get("rdfs:label") == entry["label"]
        assert wikidata_entity_iri(qid) in _id_values(node.get("owl:sameAs"))


def test_ps_act_sameas_constitution() -> None:
    doc = json.loads(PS_PEEP.read_text(encoding="utf-8"))
    node = next(
        n
        for n in doc.get("@graph", [])
        if isinstance(n, dict)
        and n.get("@id") == "estleg:eesti_vabariigi_pohiseadus_Map_2026"
    )
    types = _types(node)
    assert "estleg:Act" in types and "owl:Ontology" in types
    assert WIKIDATA_PS in _id_values(node.get("owl:sameAs"))


def test_mapped_acts_have_wikidata_sameas() -> None:
    payload = json.loads(WIKIDATA_ACTS.read_text(encoding="utf-8"))
    mapped = {k: v for k, v in payload.items() if not k.startswith("_")}
    assert "eesti_vabariigi_pohiseadus" in mapped
    for _slug, entry in mapped.items():
        peep = REPO / "krr_outputs" / entry["peep"]
        doc = json.loads(peep.read_text(encoding="utf-8"))
        node = next(
            n
            for n in doc.get("@graph", [])
            if isinstance(n, dict) and n.get("@id") == entry["act_id"]
        )
        assert wikidata_entity_iri(entry["qid"]) in _id_values(node.get("owl:sameAs"))


def test_2020_pin_has_ecli_seealso() -> None:
    graph = json.loads(RK_2020.read_text(encoding="utf-8"))["@graph"]
    node = next(n for n in graph if isinstance(n, dict) and n.get("@id") == PIN_2020_ID)
    ecli = node["estleg:ecliIdentifier"]
    assert ecli == "ECLI:EE:RK:2020:3.18.1432.93"
    seealso = _id_values(node.get("rdfs:seeAlso"))
    assert any(ecli in iri for iri in seealso)
    assert ecli_see_also_iri(ecli) in seealso


def test_void_mentions_wikidata_institutions_or_ejustice() -> None:
    text = VOID_TTL.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "wikidata" in lowered
    assert "institution" in lowered or "e-justice" in lowered
    assert "linkset-wikidata-institutions" in text or "e-justice.europa.eu" in lowered
