"""SHACL regressions for issues #450 and #393.

#450: LegalProvisionShape must target typed LegalProvision nodes, not only
subjects of estleg:paragrahv, so a provision missing paragrahv is in-scope.
#393: ProvisionVersionShape orders versionValidFrom < versionValidTo when both
dates are present; an open-ended current version (no versionValidTo) is valid.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHAPES = REPO_ROOT / "shacl" / "estonian_legal_shapes.ttl"

CONTEXT = {
    "estleg": "https://w3id.org/estleg/",
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


def _legal_provision_shape_graph():
    rdflib = pytest.importorskip("rdflib")
    return rdflib.Graph().parse(str(SHAPES), format="turtle")


def _provision_version(
    *,
    valid_from: str,
    valid_to: str | None = None,
) -> dict:
    node = {
        "@id": "estleg:TEST_Par_1_v1",
        "@type": ["owl:NamedIndividual", "estleg:ProvisionVersion"],
        "estleg:versionOf": {"@id": "estleg:TEST_Par_1"},
        "estleg:versionValidFrom": {"@value": valid_from, "@type": "xsd:date"},
        "estleg:versionText": "Sätte tekst sellel redaktsioonil.",
    }
    if valid_to is not None:
        node["estleg:versionValidTo"] = {"@value": valid_to, "@type": "xsd:date"}
    return node


class TestIssue450LegalProvisionTargetClass:
    def test_shape_declares_class_and_subjects_of_targets(self):
        rdflib = pytest.importorskip("rdflib")
        g = _legal_provision_shape_graph()
        shape = rdflib.URIRef("https://w3id.org/estleg/LegalProvisionShape")
        sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
        estleg = rdflib.Namespace("https://w3id.org/estleg/")
        assert (shape, sh.targetClass, estleg.LegalProvision) in g
        assert (shape, sh.targetSubjectsOf, estleg.paragrahv) in g

    def test_typed_legal_provision_missing_paragrahv_violates(self):
        ok, msg = _validate({
            "@context": CONTEXT,
            "@id": "estleg:TEST_Par_1",
            "@type": "estleg:LegalProvision",
            "estleg:summary": "Typed provision that never received a section reference.",
            "estleg:partOfAct": {"@id": "estleg:TEST_Map_2026"},
        })
        assert not ok, msg
        assert "MinCountConstraintComponent" in msg
        assert "paragrahv" in msg


class TestIssue393ProvisionVersionInterval:
    def test_version_valid_from_after_valid_to_violates(self):
        ok, msg = _validate({
            "@context": CONTEXT,
            "@graph": [_provision_version(valid_from="2020-01-02", valid_to="2020-01-01")],
        })
        assert not ok
        assert "ProvisionVersionShape" in msg or "LessThanConstraintComponent" in msg
        assert "versionValidFrom" in msg or "lessThan" in msg.lower()

    def test_open_ended_version_does_not_fail_less_than(self):
        ok, msg = _validate({
            "@context": CONTEXT,
            "@graph": [_provision_version(valid_from="2020-01-02")],
        })
        assert ok, msg
        assert "LessThanConstraintComponent" not in msg
