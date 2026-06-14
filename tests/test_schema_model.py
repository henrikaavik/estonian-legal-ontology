"""Schema-model tests for the versioned legal-text model (#131) and the
practitioner annotation layer (#40).

Two things are checked:

1. The new reusable vocabulary terms are declared in
   ``krr_outputs/controlled_vocabulary.jsonld`` with the right OWL types
   (so the vocabulary-coverage gate will accept them once data uses them).
2. The new SHACL shapes (``estleg:ProvisionVersionShape``,
   ``estleg:AnnotationShape``) accept hand-built well-formed instances and
   reject instances that are missing required properties or violate the
   ``annotationType`` enum — and a ``estleg:LegalProvision`` carrying the
   optional ``estleg:hasVersion`` / ``estleg:currentVersion`` properties
   still conforms to ``estleg:LegalProvisionShape``.

Population is handled by separate ingestion scripts and data-completeness
issues referenced from ``docs/SCHEMA_REFERENCE.md``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

import validate_all

REPO_ROOT = Path(__file__).resolve().parent.parent
SHAPES = REPO_ROOT / "shacl" / "estonian_legal_shapes.ttl"
VOCAB = REPO_ROOT / "krr_outputs" / "controlled_vocabulary.jsonld"

CONTEXT = {
    "estleg": "https://data.riik.ee/ontology/estleg#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}


def _validate(graph_json: dict) -> tuple[bool, str]:
    pyshacl = pytest.importorskip("pyshacl")
    rdflib = pytest.importorskip("rdflib")
    g = rdflib.Graph()
    g.parse(data=json.dumps(graph_json), format="json-ld")
    shapes = rdflib.Graph().parse(str(SHAPES), format="turtle")
    conforms, _, msg = pyshacl.validate(g, shacl_graph=shapes, inference="rdfs")
    return conforms, str(msg)


def _vocab_index() -> dict[str, dict]:
    doc = json.loads(VOCAB.read_text(encoding="utf-8"))
    return {n["@id"]: n for n in doc["@graph"] if isinstance(n, dict) and n.get("@id")}


def _has_type(node: dict, owl_type: str) -> bool:
    t = node.get("@type")
    if isinstance(t, list):
        return owl_type in t
    return t == owl_type


# ---------------------------------------------------------------------------
# Vocabulary terms — issue #131
# ---------------------------------------------------------------------------

PROVISION_VERSION_CLASSES = ["estleg:ProvisionVersion"]
PROVISION_VERSION_OBJECT_PROPS = [
    "estleg:versionOf",
    "estleg:supersededByVersion",
    "estleg:hasVersion",
    "estleg:currentVersion",
]
PROVISION_VERSION_DATATYPE_PROPS = [
    "estleg:versionValidFrom",
    "estleg:versionValidTo",
    "estleg:versionText",
    "estleg:versionRedactionId",
]

# ---------------------------------------------------------------------------
# Vocabulary terms — issue #40
# ---------------------------------------------------------------------------

ANNOTATION_CLASSES = ["estleg:Annotation"]
ANNOTATION_OBJECT_PROPS = ["estleg:annotates"]
ANNOTATION_DATATYPE_PROPS = [
    "estleg:annotationText",
    "estleg:annotationSource",
    "estleg:annotationSourceUrl",
    "estleg:annotationDate",
    "estleg:annotationType",
]

# ---------------------------------------------------------------------------
# Vocabulary terms — issues #214 / #215
# ---------------------------------------------------------------------------

TARGET_GROUP_DATATYPE_PROPS = [
    "estleg:targetGroup",
    "estleg:competenceArea",
]
COMPETENCE_OBJECT_PROPS = ["estleg:grantedBy"]


@pytest.mark.parametrize("term", PROVISION_VERSION_CLASSES + ANNOTATION_CLASSES)
def test_new_classes_declared_as_owl_class(term):
    node = _vocab_index().get(term)
    assert node is not None, f"{term} missing from controlled_vocabulary.jsonld"
    assert _has_type(node, "owl:Class"), f"{term} is not an owl:Class"
    assert node.get("rdfs:label"), f"{term} lacks rdfs:label"
    assert node.get("rdfs:comment"), f"{term} lacks rdfs:comment"


@pytest.mark.parametrize(
    "term",
    PROVISION_VERSION_OBJECT_PROPS + ANNOTATION_OBJECT_PROPS + COMPETENCE_OBJECT_PROPS,
)
def test_new_object_properties_declared(term):
    node = _vocab_index().get(term)
    assert node is not None, f"{term} missing from controlled_vocabulary.jsonld"
    assert _has_type(node, "owl:ObjectProperty"), f"{term} is not an owl:ObjectProperty"
    assert node.get("rdfs:label"), f"{term} lacks rdfs:label"
    assert node.get("rdfs:comment"), f"{term} lacks rdfs:comment"


@pytest.mark.parametrize(
    "term",
    PROVISION_VERSION_DATATYPE_PROPS + ANNOTATION_DATATYPE_PROPS + TARGET_GROUP_DATATYPE_PROPS,
)
def test_new_datatype_properties_declared(term):
    node = _vocab_index().get(term)
    assert node is not None, f"{term} missing from controlled_vocabulary.jsonld"
    assert _has_type(node, "owl:DatatypeProperty"), f"{term} is not an owl:DatatypeProperty"
    assert node.get("rdfs:label"), f"{term} lacks rdfs:label"
    assert node.get("rdfs:comment"), f"{term} lacks rdfs:comment"


def test_hasversion_versionof_inverse_pair_declared():
    idx = _vocab_index()
    assert idx["estleg:hasVersion"].get("owl:inverseOf") == {"@id": "estleg:versionOf"}
    assert idx["estleg:versionOf"].get("owl:inverseOf") == {"@id": "estleg:hasVersion"}


def test_new_terms_are_collected_as_defined_vocabulary():
    """The vocabulary-coverage gate must recognise the new terms — they are
    declared in controlled_vocabulary.jsonld even though no data uses them
    yet (defined-but-unused is fine; used-but-undefined is the failure)."""
    defined = validate_all.collect_defined_vocabulary_terms()
    for term in (
        PROVISION_VERSION_CLASSES
        + PROVISION_VERSION_OBJECT_PROPS
        + PROVISION_VERSION_DATATYPE_PROPS
        + ANNOTATION_CLASSES
        + ANNOTATION_OBJECT_PROPS
        + ANNOTATION_DATATYPE_PROPS
    ):
        assert term in defined, f"{term} not seen by collect_defined_vocabulary_terms()"


# ---------------------------------------------------------------------------
# SHACL — estleg:ProvisionVersionShape (#131)
# ---------------------------------------------------------------------------

_PV_VALID = {
    "@context": CONTEXT,
    "@id": "estleg:LegalProvision_TEST_1_v1",
    "@type": "estleg:ProvisionVersion",
    "estleg:versionOf": {"@id": "estleg:LegalProvision_TEST_1"},
    "estleg:versionValidFrom": {"@type": "xsd:date", "@value": "2015-01-01"},
    "estleg:versionValidTo": {"@type": "xsd:date", "@value": "2019-12-31"},
    "estleg:versionText": "§ 1. Vana sõnastus.",
    "estleg:versionRedactionId": "123456789",
    "estleg:supersededByVersion": {"@id": "estleg:LegalProvision_TEST_1_v2"},
}


class TestProvisionVersionShape:
    def test_well_formed_conforms(self):
        ok, msg = _validate(dict(_PV_VALID))
        assert ok, msg

    def test_minimal_well_formed_conforms(self):
        # Only the required properties — validTo / redactionId / supersededBy omitted.
        ok, msg = _validate({
            "@context": CONTEXT,
            "@id": "estleg:LegalProvision_TEST_1_v2",
            "@type": "estleg:ProvisionVersion",
            "estleg:versionOf": {"@id": "estleg:LegalProvision_TEST_1"},
            "estleg:versionValidFrom": {"@type": "xsd:date", "@value": "2020-01-01"},
            "estleg:versionText": "§ 1. Uus sõnastus.",
        })
        assert ok, msg

    def test_missing_version_of_rejected(self):
        node = dict(_PV_VALID)
        del node["estleg:versionOf"]
        ok, _ = _validate(node)
        assert not ok

    def test_missing_version_valid_from_rejected(self):
        node = dict(_PV_VALID)
        del node["estleg:versionValidFrom"]
        ok, _ = _validate(node)
        assert not ok

    def test_missing_version_text_rejected(self):
        node = dict(_PV_VALID)
        del node["estleg:versionText"]
        ok, _ = _validate(node)
        assert not ok

    def test_version_of_literal_not_iri_rejected(self):
        node = dict(_PV_VALID)
        node["estleg:versionOf"] = "estleg:LegalProvision_TEST_1"
        ok, _ = _validate(node)
        assert not ok


# ---------------------------------------------------------------------------
# SHACL — estleg:AnnotationShape (#40)
# ---------------------------------------------------------------------------

_ANN_VALID = {
    "@context": CONTEXT,
    "@id": "estleg:Annotation_TEST_1",
    "@type": "estleg:Annotation",
    "estleg:annotates": {"@id": "estleg:KOKS_Par_22"},
    "estleg:annotationText": "Õiguskantsler on selgitanud, et seda sätet tuleb tõlgendada kitsalt.",
    "estleg:annotationType": "interpretation",
    "estleg:annotationSource": "Õiguskantsler",
    "estleg:annotationSourceUrl": {"@type": "xsd:anyURI", "@value": "https://www.oiguskantsler.ee/et/arvamus"},
    "estleg:annotationDate": {"@type": "xsd:date", "@value": "2020-03-15"},
}


class TestAnnotationShape:
    def test_well_formed_conforms(self):
        ok, msg = _validate(dict(_ANN_VALID))
        assert ok, msg

    def test_minimal_well_formed_conforms(self):
        ok, msg = _validate({
            "@context": CONTEXT,
            "@id": "estleg:Annotation_TEST_2",
            "@type": "estleg:Annotation",
            "estleg:annotates": {"@id": "estleg:KOKS_Par_22"},
            "estleg:annotationText": "Praktikas kohaldatakse seda paindlikult.",
            "estleg:annotationType": "practice_note",
        })
        assert ok, msg

    @pytest.mark.parametrize(
        "kind", ["guidance", "commentary", "practice_note", "warning", "interpretation"]
    )
    def test_all_enum_values_accepted(self, kind):
        node = dict(_ANN_VALID)
        node["estleg:annotationType"] = kind
        ok, msg = _validate(node)
        assert ok, msg

    def test_missing_annotates_rejected(self):
        node = dict(_ANN_VALID)
        del node["estleg:annotates"]
        ok, _ = _validate(node)
        assert not ok

    def test_missing_annotation_text_rejected(self):
        node = dict(_ANN_VALID)
        del node["estleg:annotationText"]
        ok, _ = _validate(node)
        assert not ok

    def test_missing_annotation_type_rejected(self):
        node = dict(_ANN_VALID)
        del node["estleg:annotationType"]
        ok, _ = _validate(node)
        assert not ok

    def test_out_of_enum_annotation_type_rejected(self):
        node = dict(_ANN_VALID)
        node["estleg:annotationType"] = "bogus_category"
        ok, _ = _validate(node)
        assert not ok

    def test_annotates_literal_not_iri_rejected(self):
        node = dict(_ANN_VALID)
        node["estleg:annotates"] = "estleg:KOKS_Par_22"
        ok, _ = _validate(node)
        assert not ok


# ---------------------------------------------------------------------------
# SHACL — estleg:LegalProvisionShape still accepts hasVersion / currentVersion
# ---------------------------------------------------------------------------


def test_legal_provision_with_version_links_conforms():
    ok, msg = _validate({
        "@context": CONTEXT,
        "@id": "estleg:LegalProvision_TEST_1",
        "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
        "estleg:paragrahv": "§ 1",
        "estleg:partOfAct": {"@id": "estleg:TEST_Map_2026"},
        "estleg:summary": "Test provision used in the schema-model test.",
        "estleg:hasVersion": [
            {"@id": "estleg:LegalProvision_TEST_1_v1"},
            {"@id": "estleg:LegalProvision_TEST_1_v2"},
        ],
        "estleg:currentVersion": {"@id": "estleg:LegalProvision_TEST_1_v2"},
    })
    assert ok, msg


def test_legal_provision_without_version_links_still_conforms():
    ok, msg = _validate({
        "@context": CONTEXT,
        "@id": "estleg:LegalProvision_TEST_2",
        "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
        "estleg:paragrahv": "§ 2",
        "estleg:partOfAct": {"@id": "estleg:TEST_Map_2026"},
        "estleg:summary": "Provision with no version history populated.",
    })
    assert ok, msg


# ---------------------------------------------------------------------------
# SHACL — estleg:targetGroup enum is optional and multi-valued
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target_group",
    ["citizen", "business", "public_body", "official", "ngo"],
)
def test_legal_provision_accepts_target_group_enum_values(target_group):
    ok, msg = _validate({
        "@context": CONTEXT,
        "@id": f"estleg:LegalProvision_TARGET_{target_group}",
        "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
        "estleg:paragrahv": "§ 10",
        "estleg:partOfAct": {"@id": "estleg:TEST_Map_2026"},
        "estleg:summary": "Provision with a classified target group.",
        "estleg:targetGroup": target_group,
    })
    assert ok, msg


def test_legal_provision_accepts_multiple_target_groups():
    ok, msg = _validate({
        "@context": CONTEXT,
        "@id": "estleg:LegalProvision_TARGET_MULTI",
        "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
        "estleg:paragrahv": "§ 11",
        "estleg:partOfAct": {"@id": "estleg:TEST_Map_2026"},
        "estleg:summary": "Provision affecting several target groups.",
        "estleg:targetGroup": ["citizen", "business", "public_body"],
    })
    assert ok, msg


def test_legal_provision_rejects_unknown_target_group():
    ok, _ = _validate({
        "@context": CONTEXT,
        "@id": "estleg:LegalProvision_TARGET_BAD",
        "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
        "estleg:paragrahv": "§ 12",
        "estleg:partOfAct": {"@id": "estleg:TEST_Map_2026"},
        "estleg:summary": "Provision with an invalid target group.",
        "estleg:targetGroup": "aliens",
    })
    assert not ok


# ---------------------------------------------------------------------------
# SHACL — estleg:CompetenceShape
# ---------------------------------------------------------------------------


def _competence_graph(extra: dict | None = None, remove: str | None = None) -> dict:
    competence = {
        "@id": "estleg:Institution_test_competence_general",
        "@type": ["owl:NamedIndividual", "estleg:Competence"],
        "estleg:institution": {"@id": "estleg:Institution_test"},
        "estleg:competenceType": "general",
        "estleg:appliesToProvision": {"@id": "estleg:TEST_Par_1"},
        "estleg:appliesToProvisionCount": {"@value": "1", "@type": "xsd:integer"},
        "estleg:grantedBy": {"@id": "estleg:TEST_Map_2026"},
        "estleg:competenceArea": "general_government",
    }
    if extra:
        competence.update(extra)
    if remove:
        competence.pop(remove, None)
    return {
        "@context": CONTEXT,
        "@graph": [
            {
                "@id": "estleg:Institution_test",
                "@type": ["owl:NamedIndividual", "estleg:Institution"],
                "rdfs:label": "Test Institution",
            },
            competence,
        ],
    }


def test_competence_shape_accepts_valid_node():
    ok, msg = _validate(_competence_graph())
    assert ok, msg


def test_competence_shape_requires_institution():
    ok, _ = _validate(_competence_graph(remove="estleg:institution"))
    assert not ok


def test_competence_shape_rejects_unknown_competence_type():
    ok, _ = _validate(_competence_graph({"estleg:competenceType": "mystery"}))
    assert not ok


def test_competence_shape_rejects_literal_granted_by():
    ok, _ = _validate(_competence_graph({"estleg:grantedBy": "estleg:TEST_Map_2026"}))
    assert not ok


def test_competence_shape_rejects_multiple_granted_by_values():
    ok, _ = _validate(_competence_graph({
        "estleg:grantedBy": [
            {"@id": "estleg:TEST_Map_2026"},
            {"@id": "estleg:OTHER_Map_2026"},
        ],
    }))
    assert not ok
