"""SHACL regressions for issue #509 (langString titles/labels).

Human-language text properties accept either xsd:string or rdf:langString.
Title/label properties also enforce sh:uniqueLang so @et/@en can coexist.
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


def test_act_bilingual_labels_conform():
    ok, msg = _validate({
        "@context": CONTEXT,
        "@id": "estleg:KARIST_Map",
        "@type": ["estleg:Act", "owl:Ontology"],
        "rdfs:label": [
            {"@value": "Karistusseadustik", "@language": "et"},
            {"@value": "Penal Code", "@language": "en"},
        ],
    })
    assert ok, msg


def test_legal_provision_langstring_summary_conforms():
    ok, msg = _validate({
        "@context": CONTEXT,
        "@id": "estleg:TEST_Par_1",
        "@type": "estleg:LegalProvision",
        "estleg:paragrahv": "TEST § 1",
        "estleg:summary": {
            "@value": "Karistusseadustiku üldosa kohaldamisala.",
            "@language": "et",
        },
        "estleg:partOfAct": {"@id": "estleg:TEST_Map"},
    })
    assert ok, msg


def test_duplicate_et_labels_fail_unique_lang():
    ok, msg = _validate({
        "@context": CONTEXT,
        "@id": "estleg:KARIST_Map",
        "@type": ["estleg:Act", "owl:Ontology"],
        "rdfs:label": [
            {"@value": "Karistusseadustik", "@language": "et"},
            {"@value": "Karistusseadustik (lühend)", "@language": "et"},
        ],
    })
    assert not ok, msg
    assert "UniqueLangConstraintComponent" in msg


def test_bare_string_label_still_conforms():
    ok, msg = _validate({
        "@context": CONTEXT,
        "@id": "estleg:KARIST_Map",
        "@type": ["estleg:Act", "owl:Ontology"],
        "rdfs:label": "Karistusseadustik",
    })
    assert ok, msg


def test_shapes_file_declares_langstring_and_unique_lang():
    text = SHAPES.read_text(encoding="utf-8")
    assert "rdf:langString" in text
    assert "sh:uniqueLang" in text
