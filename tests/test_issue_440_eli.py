"""#440 remainder — ELI 1.5 class/property bridges in the canonical T-Box.

T-Box only: subclass/subproperty axioms in controlled_vocabulary.jsonld.
No instance remint.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VOCAB = REPO / "krr_outputs" / "controlled_vocabulary.jsonld"
SCHEMA_REF = REPO / "docs" / "SCHEMA_REFERENCE.md"

ELI_URI = "http://data.europa.eu/eli/ontology#"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _index(doc: dict) -> dict[str, dict]:
    return {
        node["@id"]: node
        for node in doc.get("@graph", [])
        if isinstance(node, dict) and isinstance(node.get("@id"), str)
    }


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
        ids: set[str] = set()
        for item in value:
            ids |= _as_ids(item)
        return ids
    return set()


def _literal_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        inner = value.get("@value")
        return inner if isinstance(inner, str) else ""
    if isinstance(value, list):
        return " ".join(_literal_text(item) for item in value)
    return ""


def test_cv_context_declares_official_eli_namespace() -> None:
    ctx = _load(VOCAB)["@context"]
    assert ctx["eli"] == ELI_URI


def test_act_subclass_of_eli_legal_resource() -> None:
    node = _index(_load(VOCAB))["estleg:Act"]
    assert "eli:LegalResource" in _as_ids(node.get("rdfs:subClassOf"))


def test_legal_provision_subclass_of_eli_legal_resource_subdivision() -> None:
    node = _index(_load(VOCAB))["estleg:LegalProvision"]
    assert "eli:LegalResourceSubdivision" in _as_ids(node.get("rdfs:subClassOf"))


def test_provision_version_aligns_to_eli_legal_expression() -> None:
    """ProvisionVersion is expression-level; subclass when the class node exists."""
    node = _index(_load(VOCAB))["estleg:ProvisionVersion"]
    parents = _as_ids(node.get("rdfs:subClassOf"))
    comment = _literal_text(node.get("rdfs:comment"))
    assert "eli:LegalExpression" in parents or "eli:LegalExpression" in comment


def test_entry_into_force_subproperty_of_eli_date_entry_in_force() -> None:
    node = _index(_load(VOCAB))["estleg:entryIntoForce"]
    assert "eli:date_entry_in_force" in _as_ids(node.get("rdfs:subPropertyOf"))


def test_repeal_date_subproperty_of_eli_date_no_longer_in_force() -> None:
    node = _index(_load(VOCAB))["estleg:repealDate"]
    assert "eli:date_no_longer_in_force" in _as_ids(node.get("rdfs:subPropertyOf"))


def test_temporal_status_not_mapped_to_eli_in_force() -> None:
    """String temporalStatus must not be a sub-property of IRI-valued eli:in_force."""
    node = _index(_load(VOCAB))["estleg:temporalStatus"]
    assert "eli:in_force" not in _as_ids(node.get("rdfs:subPropertyOf"))


def test_schema_reference_notes_eli_1_5_alignment() -> None:
    text = SCHEMA_REF.read_text(encoding="utf-8")
    assert "ELI 1.5" in text
    assert "#440" in text
    assert "eli:LegalResourceSubdivision" in text
