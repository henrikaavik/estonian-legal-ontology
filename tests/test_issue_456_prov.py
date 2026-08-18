"""PROV-O dataset activity + classifier assertionConfidence (#456).

Scraped ``estleg:legalText`` is not stamped. The RT law generator must
not write confidence. Classifier outputs on peeps do.
"""

from __future__ import annotations

import json
from pathlib import Path

from rdflib import RDF, Graph, URIRef
from rdflib.namespace import PROV, RDFS

from estleg import estleg_common

REPO = Path(__file__).resolve().parent.parent
VOID_TTL = REPO / "krr_outputs" / "void.ttl"
VOCAB = REPO / "krr_outputs" / "controlled_vocabulary.jsonld"
GENERATE_ALL_LAWS = REPO / "src" / "estleg" / "generate_all_laws.py"

DATASET = URIRef("https://w3id.org/estleg/dataset/estonian-legal-ontology")
ACTIVITY = URIRef("https://w3id.org/estleg/Activity_CorpusBuild_0_11_0")


def _void() -> Graph:
    graph = Graph()
    graph.parse(VOID_TTL, format="turtle")
    return graph


def _vocab() -> dict:
    return json.loads(VOCAB.read_text(encoding="utf-8"))


def _vocab_index() -> dict[str, dict]:
    return {
        node["@id"]: node
        for node in _vocab().get("@graph", [])
        if isinstance(node, dict) and node.get("@id")
    }


def _has_type(node: dict, owl_type: str) -> bool:
    value = node.get("@type")
    if isinstance(value, list):
        return owl_type in value
    return value == owl_type


def test_void_dataset_was_generated_by_corpus_build_activity() -> None:
    graph = _void()
    assert (DATASET, PROV.wasGeneratedBy, ACTIVITY) in graph


def test_void_declares_prov_activity() -> None:
    graph = _void()
    assert (ACTIVITY, RDF.type, PROV.Activity) in graph
    labels = {str(value) for value in graph.objects(ACTIVITY, RDFS.label)}
    assert any("0.11.0" in label for label in labels)
    started = list(graph.objects(ACTIVITY, PROV.startedAtTime))
    assert started
    assert any(estleg_common.PINNED_RUN_TIMESTAMP[:10] in str(value) for value in started)


def test_void_prefix_declares_prov() -> None:
    text = VOID_TTL.read_text(encoding="utf-8")
    assert "@prefix prov:" in text
    assert "http://www.w3.org/ns/prov#" in text


def test_cv_declares_assertion_confidence() -> None:
    ctx = _vocab()["@context"]
    assert ctx["prov"] == "http://www.w3.org/ns/prov#"
    node = _vocab_index()["estleg:assertionConfidence"]
    assert _has_type(node, "owl:DatatypeProperty")
    range_id = node.get("rdfs:range")
    if isinstance(range_id, dict):
        range_id = range_id.get("@id")
    assert range_id == "xsd:decimal"
    comment = node.get("rdfs:comment", "")
    assert "EuroVoc" in comment
    assert "deontic" in comment
    assert "targetGroup" in comment
    assert "legalText" in comment


def test_combined_ontology_header_emits_was_generated_by() -> None:
    header = estleg_common.combined_ontology_header()
    assert header["prov:wasGeneratedBy"] == {
        "@id": "estleg:Activity_CorpusBuild_0_11_0"
    }
    assert "prov:wasGeneratedBy" in estleg_common.COMBINED_CLOSURE_EXEMPT_PREDICATES


def test_schema_reference_documents_dataset_prov() -> None:
    text = (REPO / "docs" / "SCHEMA_REFERENCE.md").read_text(encoding="utf-8")
    assert "Provenance (dataset-level)" in text
    assert "prov:wasGeneratedBy" in text
    assert "estleg:assertionConfidence" in text


def test_law_generator_does_not_stamp_confidence() -> None:
    """Scraped statutory text is not a classifier output (#456)."""
    source = GENERATE_ALL_LAWS.read_text(encoding="utf-8")
    assert "wasGeneratedBy" not in source
    assert "assertionConfidence" not in source


def test_stamp_assertion_confidence_writes_typed_decimal() -> None:
    node = {"estleg:normativeType": {"@id": "estleg:NormType_Obligation"}}
    assert estleg_common.heuristic_confidence_for_node(node) == "0.70"
    assert estleg_common.stamp_assertion_confidence(node, "0.70") is True
    assert node["estleg:assertionConfidence"] == {
        "@value": "0.70",
        "@type": "xsd:decimal",
    }
    assert estleg_common.stamp_assertion_confidence(node, "0.70") is False


def test_legal_text_only_node_has_no_heuristic_confidence() -> None:
    node = {"estleg:legalText": "Tööandja peab pidama arvestust."}
    assert estleg_common.heuristic_confidence_for_node(node) is None


def test_committed_classified_peep_has_assertion_confidence() -> None:
    doc = json.loads(
        (REPO / "krr_outputs" / "abipolitseiniku_seadus_peep.json").read_text(
            encoding="utf-8"
        )
    )
    stamped = 0
    for node in doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        if "estleg:targetGroup" not in node and "estleg:normativeType" not in node:
            continue
        conf = node.get("estleg:assertionConfidence")
        assert isinstance(conf, dict), node.get("@id")
        assert conf.get("@type") == "xsd:decimal"
        value = float(conf["@value"])
        assert 0 < value <= 1
        stamped += 1
    assert stamped > 0
