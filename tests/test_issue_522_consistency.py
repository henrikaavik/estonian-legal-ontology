"""#522 T-Box axioms, consistencyChecked stamp, and local checker."""

from __future__ import annotations

import json
from pathlib import Path

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import XSD

from estleg import check_tbox_consistency as checker
from estleg import estleg_common

REPO = Path(__file__).resolve().parent.parent
VOCAB = REPO / "krr_outputs" / "controlled_vocabulary.jsonld"
VOID_TTL = REPO / "krr_outputs" / "void.ttl"
METADATA = REPO / "metadata.jsonld"
DATASET = URIRef("https://w3id.org/estleg/dataset/estonian-legal-ontology")
CONSISTENCY_CHECKED = URIRef("https://w3id.org/estleg/consistencyChecked")


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


def _as_ids(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        ids: set[str] = set()
        iri = value.get("@id")
        if isinstance(iri, str):
            ids.add(iri)
        for item in value.values():
            ids |= _as_ids(item)
        return ids
    if isinstance(value, list):
        ids = set()
        for item in value:
            ids |= _as_ids(item)
        return ids
    return set()


def test_partofact_is_functional_property() -> None:
    node = _vocab_index()["estleg:partOfAct"]
    assert _has_type(node, "owl:ObjectProperty")
    assert _has_type(node, "owl:FunctionalProperty")
    assert "estleg:Act" in _as_ids(node.get("rdfs:range"))
    domain_ids = _as_ids(node.get("rdfs:domain"))
    assert "estleg:LegalProvision" in domain_ids


def test_inforce_disjoint_with_repealed() -> None:
    idx = _vocab_index()
    in_force = idx["estleg:TemporalStatus_InForce"]
    repealed = idx["estleg:TemporalStatus_Repealed"]
    assert _has_type(in_force, "skos:Concept")
    assert _has_type(in_force, "estleg:TemporalStatus")
    assert _has_type(repealed, "skos:Concept")
    assert _has_type(repealed, "estleg:TemporalStatus")
    left = _as_ids(in_force.get("owl:disjointWith"))
    right = _as_ids(repealed.get("owl:disjointWith"))
    assert "estleg:TemporalStatus_Repealed" in left or "estleg:TemporalStatus_InForce" in right


def test_temporal_status_stays_datatype_property() -> None:
    node = _vocab_index()["estleg:temporalStatus"]
    assert _has_type(node, "owl:DatatypeProperty")
    assert not _has_type(node, "owl:ObjectProperty")
    comment = node.get("rdfs:comment", "")
    assert "inForce" in comment
    assert "repealed" in comment
    assert "unknown" in comment


def test_combined_ontology_header_includes_consistency_checked() -> None:
    header = estleg_common.combined_ontology_header()
    assert header["estleg:consistencyChecked"] == {
        "@value": True,
        "@type": "xsd:boolean",
    }


def test_metadata_dataset_includes_consistency_checked() -> None:
    doc = json.loads(METADATA.read_text(encoding="utf-8"))
    assert doc["estleg:consistencyChecked"] == {
        "@value": True,
        "@type": "xsd:boolean",
    }


def test_checker_flags_two_partofact_iris() -> None:
    doc = {
        "@graph": [
            {
                "@id": "estleg:X_Par_1",
                "@type": "estleg:LegalProvision",
                "estleg:partOfAct": [
                    {"@id": "estleg:A_Map_2026"},
                    {"@id": "estleg:B_Map_2026"},
                ],
            }
        ]
    }
    violations = checker.check_document(doc)
    assert len(violations) == 1
    hit = violations[0]
    assert hit.kind == checker.KIND_PARTOFACT_NOT_FUNCTIONAL
    assert hit.node_id == "estleg:X_Par_1"
    assert set(hit.values) == {"estleg:A_Map_2026", "estleg:B_Map_2026"}


def test_checker_flags_temporalstatus_inforce_and_repealed() -> None:
    doc = {
        "@graph": [
            {
                "@id": "estleg:X_Map_2026",
                "@type": "estleg:Act",
                "estleg:temporalStatus": ["inForce", "repealed"],
            }
        ]
    }
    violations = checker.check_document(doc)
    assert len(violations) == 1
    hit = violations[0]
    assert hit.kind == checker.KIND_TEMPORAL_CONFLICT
    assert hit.node_id == "estleg:X_Map_2026"
    assert checker.TOKEN_IN_FORCE in hit.values
    assert checker.TOKEN_REPEALED in hit.values


def test_checker_accepts_clean_fixture_and_duplicate_same_act() -> None:
    doc = {
        "@graph": [
            {
                "@id": "estleg:X_Par_1",
                "@type": "estleg:LegalProvision",
                "estleg:partOfAct": [
                    {"@id": "estleg:X_Map_2026"},
                    {"@id": "https://w3id.org/estleg/X_Map_2026"},
                ],
            },
            {
                "@id": "estleg:X_Map_2026",
                "@type": "estleg:Act",
                "estleg:temporalStatus": {"@value": "inForce", "@type": "xsd:string"},
            },
        ]
    }
    assert checker.check_document(doc) == []


def test_void_ttl_contains_consistency_checked() -> None:
    text = VOID_TTL.read_text(encoding="utf-8")
    assert "consistencyChecked" in text
    graph = Graph()
    graph.parse(VOID_TTL, format="turtle")
    values = set(graph.objects(DATASET, CONSISTENCY_CHECKED))
    assert Literal(True, datatype=XSD.boolean) in values


def test_checker_cli_and_sample_peeps_on_fixture(tmp_path: Path) -> None:
    peep = tmp_path / "perekonnaseadus_peep.json"
    peep.write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:PKS_Par_1",
                        "@type": "estleg:LegalProvision",
                        "estleg:partOfAct": {"@id": "estleg:PKS_Map_2026"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    bad = tmp_path / "bad.jsonld"
    bad.write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:Y_Par_1",
                        "@type": "estleg:LegalProvision",
                        "estleg:partOfAct": [
                            {"@id": "estleg:A_Map_2026"},
                            {"@id": "estleg:B_Map_2026"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert checker.check_sample_peeps(tmp_path) == []
    assert checker.main([str(peep)]) == 0
    assert checker.main([str(bad)]) == 1
