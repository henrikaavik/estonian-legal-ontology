"""T-Box axioms for #439 and the #513 referenceType slice.

Loads ``krr_outputs/controlled_vocabulary.jsonld`` only — no citation-corpus
rewrite. #513 stays PARTIAL until a later emission pass stamps
``estleg:referenceType`` on data.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VOCAB = REPO / "krr_outputs" / "controlled_vocabulary.jsonld"

REF_TYPE_INDIVIDUALS = (
    "estleg:RefType_Citation",
    "estleg:RefType_Repeal",
    "estleg:RefType_LegalBasis",
    "estleg:RefType_Exception",
    "estleg:RefType_Derogation",
)


def _vocab() -> dict:
    return json.loads(VOCAB.read_text(encoding="utf-8"))


def _vocab_index() -> dict[str, dict]:
    return {
        n["@id"]: n
        for n in _vocab().get("@graph", [])
        if isinstance(n, dict) and n.get("@id")
    }


def _has_type(node: dict, owl_type: str) -> bool:
    t = node.get("@type")
    if isinstance(t, list):
        return owl_type in t
    return t == owl_type


def _as_ids(value) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        ids = set()
        if value.get("@id"):
            ids.add(value["@id"])
        for item in value.values():
            ids |= _as_ids(item)
        return ids
    if isinstance(value, list):
        ids: set[str] = set()
        for item in value:
            ids |= _as_ids(item)
        return ids
    return set()


def _assert_disjoint(idx: dict[str, dict], left: str, right: str) -> None:
    assert left in idx, f"{left} missing from controlled_vocabulary.jsonld"
    assert right in idx, f"{right} missing from controlled_vocabulary.jsonld"
    left_ids = _as_ids(idx[left].get("owl:disjointWith"))
    right_ids = _as_ids(idx[right].get("owl:disjointWith"))
    assert right in left_ids or left in right_ids, (
        f"expected {left} owl:disjointWith {right} (or the inverse) in the CV"
    )


def test_municipal_and_national_regulation_are_disjoint():
    """#439: sibling DomesticRegulation branches cannot overlap."""
    _assert_disjoint(
        _vocab_index(),
        "estleg:MunicipalRegulation",
        "estleg:NationalRegulation",
    )


def test_proposed_amendment_disjoint_from_amendment_event():
    """#439: a draft proposal is not an effected AmendmentEvent."""
    _assert_disjoint(
        _vocab_index(),
        "estleg:ProposedAmendment",
        "estleg:AmendmentEvent",
    )


def test_expression_of_is_functional_property():
    """#439: an ActExpression realises exactly one Act Work."""
    node = _vocab_index()["estleg:expressionOf"]
    assert _has_type(node, "owl:FunctionalProperty")


def test_partofact_functional_only_if_declared_in_cv():
    """#439: FunctionalProperty on partOfAct iff the property already lives in the CV.

    The predicate is used in instance data and declared in metadata.jsonld, but
    it is not a CV node. Do not invent it just to attach the axiom.
    """
    node = _vocab_index().get("estleg:partOfAct")
    if node is None:
        return
    assert _has_type(node, "owl:FunctionalProperty"), (
        "estleg:partOfAct is in the CV so it must be owl:FunctionalProperty (#439)"
    )


def test_partofact_declared_functional_in_controlled_vocabulary():
    """#522: partOfAct is now a CV FunctionalProperty (closes the #439 deferral)."""
    node = _vocab_index()["estleg:partOfAct"]
    assert _has_type(node, "owl:ObjectProperty")
    assert _has_type(node, "owl:FunctionalProperty")


def test_no_invented_versioning_iris_in_new_tbox_nodes():
    """#439: do not invent versioning IRIs while landing the minimal axioms."""
    idx = _vocab_index()
    for iri in (
        "estleg:MunicipalRegulation",
        "estleg:NationalRegulation",
        "estleg:ProposedAmendment",
        "estleg:AmendmentEvent",
        "estleg:referenceType",
    ):
        node = idx[iri]
        assert "owl:versionIRI" not in node, iri
        assert "owl:versionInfo" not in node, iri


def test_reference_type_is_object_property():
    """#513 T-Box: estleg:referenceType is an object property."""
    node = _vocab_index().get("estleg:referenceType")
    assert node is not None, "estleg:referenceType missing from controlled_vocabulary.jsonld"
    assert _has_type(node, "owl:ObjectProperty")
    domain_ids = _as_ids(node.get("rdfs:domain"))
    assert "estleg:Citation" in domain_ids or "rdf:Statement" in domain_ids, domain_ids
    range_ids = _as_ids(node.get("rdfs:range"))
    assert "estleg:ReferenceType" in range_ids or set(REF_TYPE_INDIVIDUALS) <= range_ids


def test_reftype_individuals_exist():
    """#513 T-Box: the five closed RefType individuals are in the CV."""
    idx = _vocab_index()
    assert "estleg:ReferenceType" in idx
    assert _has_type(idx["estleg:ReferenceType"], "owl:Class")
    for iri in REF_TYPE_INDIVIDUALS:
        node = idx.get(iri)
        assert node is not None, f"{iri} missing from controlled_vocabulary.jsonld"
        assert _has_type(node, "owl:NamedIndividual"), iri
        assert _has_type(node, "estleg:ReferenceType"), iri
        assert node.get("rdfs:label"), f"{iri} lacks rdfs:label"
