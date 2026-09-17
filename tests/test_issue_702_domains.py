"""Shared court/EU properties must not infer sibling classes on either load surface."""

import json
from pathlib import Path

import pytest
from owlrl import DeductiveClosure, RDFS_Semantics
from rdflib import Graph, Namespace, RDF

from estleg.consolidate_tbox import build_consolidated_graph

KRR = Path(__file__).resolve().parents[1] / "krr_outputs"
SHARED_DOMAINS = {
    "estleg:celexNumber": "estleg:EULegislation",
    "estleg:eurLexLink": "estleg:EULegislation",
    "estleg:documentDate": "estleg:EULegislation",
    "estleg:ecliIdentifier": "estleg:CourtDecision",
}


def _assert_no_phantom_types(context, nodes):
    decision = {
        "@id": "estleg:TestEUDecision",
        "@type": "estleg:EUCourtDecision",
        "estleg:celexNumber": "62026CJ0001",
        "estleg:eurLexLink": "https://eur-lex.europa.eu/",
        "estleg:documentDate": "2026-09-07",
        "estleg:ecliIdentifier": "ECLI:EU:C:2026:1",
    }
    graph = Graph().parse(
        data=json.dumps({"@context": context, "@graph": [*nodes, decision]}),
        format="json-ld",
    )
    DeductiveClosure(RDFS_Semantics).expand(graph)
    estleg = Namespace("https://w3id.org/estleg/")
    assert (estleg.TestEUDecision, RDF.type, estleg.EUCourtDecision) in graph
    assert (estleg.TestEUDecision, RDF.type, estleg.EULegislation) not in graph
    assert (estleg.TestEUDecision, RDF.type, estleg.CourtDecision) not in graph


def test_consolidator_replaces_shared_domains_only():
    old_domains = {**SHARED_DOMAINS, "estleg:decisionDate": "estleg:CourtDecision"}
    nodes, _ = build_consolidated_graph(
        {"@graph": [
            {"@id": prop, "@type": ["owl:DatatypeProperty"], "rdfs:domain": {"@id": domain}}
            for prop, domain in old_domains.items()
        ]},
        extra_sources=[],
    )
    by_id = {node["@id"]: node for node in nodes}
    for prop in SHARED_DOMAINS:
        assert by_id[prop]["rdfs:domain"] == {"@id": "owl:Thing"}
    assert by_id["estleg:decisionDate"]["rdfs:domain"] == {"@id": "estleg:CourtDecision"}


@pytest.mark.corpus
def test_committed_combined_preserves_shared_domains():
    """Load actual LFS data: a missing/pointer aggregate must fail this release gate."""
    projections = []
    for name in ("controlled_vocabulary.jsonld", "combined_ontology.jsonld"):
        doc = json.loads((KRR / name).read_bytes())
        nodes = {n["@id"]: n for n in doc["@graph"] if n.get("@id") in SHARED_DOMAINS}
        assert set(nodes) == set(SHARED_DOMAINS), name
        domains = {prop: node.get("rdfs:domain") for prop, node in nodes.items()}
        assert domains == dict.fromkeys(SHARED_DOMAINS, {"@id": "owl:Thing"}), name
        _assert_no_phantom_types(doc["@context"], list(nodes.values()))
        projections.append(domains)
        del doc
    assert projections[0] == projections[1]
