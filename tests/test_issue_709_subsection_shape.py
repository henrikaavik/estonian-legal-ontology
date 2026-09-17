"""#709: a § must carry paragrahv / summary / partOfAct; a lõige (estleg:Subsection) need not.

#519 made Subsection a subclass of LegalProvision and #450 gave LegalProvisionShape a class
target, which together held all 111,911 lõiked to the §-level minimums. Both validator
surfaces are exercised: the buckets run ``inference="rdfs"``, the Seadusloome gate none, and
pyshacl resolves class targets through rdfs:subClassOf in the data graph either way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pyshacl = pytest.importorskip("pyshacl")
rdflib = pytest.importorskip("rdflib")

SHAPES = Path(__file__).resolve().parent.parent / "shacl" / "estonian_legal_shapes.ttl"
SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
ESTLEG = rdflib.Namespace("https://w3id.org/estleg/")
CONTEXT = {
    "estleg": "https://w3id.org/estleg/",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
}
# The vocabulary rides along in every bucket and is embedded in combined.
SUBCLASS_AXIOM = {
    "@id": "estleg:Subsection",
    "@type": "owl:Class",
    "rdfs:subClassOf": {"@id": "estleg:LegalProvision"},
}
REQUIRED = ("Paragrahv", "Summary", "PartOfAct")
INFERENCE = pytest.mark.parametrize("inference", ["rdfs", "none"])


@pytest.fixture(scope="module")
def shapes():
    return rdflib.Graph().parse(str(SHAPES), format="turtle")


def _paragraph(**overrides) -> dict:
    node = {
        "@id": "estleg:TEST_Par_1",
        "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
        "estleg:paragrahv": "TEST § 1",
        "estleg:summary": "Sätestab seaduse reguleerimisala.",
        "estleg:partOfAct": {"@id": "estleg:TEST_Map"},
    }
    node.update(overrides)
    return {k: v for k, v in node.items() if v is not None}


def _subsection(*types: str, **extra) -> dict:
    return {
        "@id": "estleg:TEST_Par_1_Lg_1",
        "@type": ["owl:NamedIndividual", *types],
        "estleg:legalText": "(1) Käesolev seadus sätestab reguleerimisala.",
        "estleg:parentProvision": {"@id": "estleg:TEST_Par_1"},
        **extra,
    }


def _shape_name(shapes, shape) -> str:
    """A named shape by its local name; a property shape as ``OwningShape/path``."""
    if isinstance(shape, rdflib.URIRef):
        return str(shape).removeprefix(str(ESTLEG))
    owner = next(shapes.subjects(SH.property, shape))
    path = shapes.value(shape, SH.path)
    return f"{_shape_name(shapes, owner)}/{str(path).removeprefix(str(ESTLEG))}"


def _failing_shapes(shapes, inference: str, *nodes: dict) -> set[str]:
    data = rdflib.Graph().parse(
        data=json.dumps({"@context": CONTEXT, "@graph": [SUBCLASS_AXIOM, *nodes]}),
        format="json-ld",
    )
    _, report, _ = pyshacl.validate(data, shacl_graph=shapes, inference=inference)
    results = report.subjects(rdflib.RDF.type, SH.ValidationResult)
    return {_shape_name(shapes, report.value(result, SH.sourceShape)) for result in results}


@INFERENCE
def test_a_complete_paragraph_with_its_loige_conforms(shapes, inference):
    assert (
        _failing_shapes(shapes, inference, _paragraph(), _subsection("estleg:Subsection")) == set()
    )


@INFERENCE
def test_a_loige_as_combined_materialises_it_conforms(shapes, inference):
    """#519 adds the parent type, so in combined a lõige is asserted to be both."""
    loige = _subsection("estleg:Subsection", "estleg:LegalProvision")
    assert _failing_shapes(shapes, inference, _paragraph(), loige) == set()


@INFERENCE
@pytest.mark.parametrize("field", ["paragrahv", "summary", "partOfAct"])
def test_a_paragraph_missing_a_required_field_fails_that_fields_shape(shapes, inference, field):
    name = f"ProvisionRequires{field[0].upper()}{field[1:]}Shape"
    assert _failing_shapes(shapes, inference, _paragraph(**{f"estleg:{field}": None})) == {name}


@INFERENCE
def test_a_bare_typed_provision_fails_all_three(shapes, inference):
    """#450: a node typed LegalProvision that never received paragrahv is still in scope."""
    bare = {"@id": "estleg:TEST_Par_9", "@type": "estleg:LegalProvision"}
    assert _failing_shapes(shapes, inference, bare) == {
        f"ProvisionRequires{f}Shape" for f in REQUIRED
    }


@INFERENCE
def test_a_loige_is_still_held_to_the_value_constraints(shapes, inference):
    """Excused from carrying the fields, not from carrying them wrongly."""
    loige = _subsection("estleg:Subsection", **{"estleg:partOfAct": "not an IRI"})
    assert _failing_shapes(shapes, inference, _paragraph(), loige) == {
        "LegalProvisionShape/partOfAct"
    }


@INFERENCE
def test_a_loige_without_its_parent_still_fails_subsection_shape(shapes, inference):
    loige = _subsection("estleg:Subsection")
    del loige["estleg:parentProvision"]
    assert _failing_shapes(shapes, inference, _paragraph(), loige) == {
        "SubsectionShape/parentProvision"
    }


# The vocabulary's own axiom; it rides along in every bucket.
PARENT_PROVISION_DOMAIN = {
    "@id": "estleg:parentProvision",
    "@type": "owl:ObjectProperty",
    "rdfs:domain": {"@id": "estleg:Subsection"},
}


def test_the_surfaces_diverge_on_a_paragraph_that_carries_a_loige_property(shapes):
    """sh:class also reads an RDFS-entailed type, and parentProvision entails Subsection.

    Pinned so the divergence is a known quantity: no shipped node is in this state, and
    the phantom-typing gate (next test) is what catches one in the inference surface.
    """
    stray = {
        "@id": "estleg:TEST_Par_2",
        "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
        "estleg:legalText": "Tekst.",
        "estleg:parentProvision": {"@id": "estleg:TEST_Par_1"},
    }
    trio = {f"ProvisionRequires{f}Shape" for f in REQUIRED}
    nodes = (PARENT_PROVISION_DOMAIN, _paragraph(), stray)
    assert _failing_shapes(shapes, "none", *nodes) == trio
    assert _failing_shapes(shapes, "rdfs", *nodes) == set()


def test_the_phantom_typing_gate_reports_that_paragraph():
    from estleg import check_phantom_typing as cpt

    stray = {
        "@id": "estleg:TEST_Par_2",
        "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
        "estleg:parentProvision": {"@id": "estleg:TEST_Par_1"},
    }
    tbox = cpt.TBox([SUBCLASS_AXIOM, PARENT_PROVISION_DOMAIN], cpt.shaped_classes())
    (finding,) = cpt.scan_documents("test", [{"@graph": [stray]}], tbox)
    assert (finding.prop, finding.cls, finding.nodes) == (
        "estleg:parentProvision",
        "estleg:Subsection",
        ("estleg:TEST_Par_2",),
    )


def test_the_trio_shares_legal_provision_shapes_targets(shapes):
    """Same nodes in scope as before the split: nothing is newly targeted, nothing dropped."""
    base = ESTLEG.LegalProvisionShape
    targets = {
        (p, o) for p in (SH.targetClass, SH.targetSubjectsOf) for o in shapes.objects(base, p)
    }
    assert targets == {
        (SH.targetClass, ESTLEG.LegalProvision),
        (SH.targetSubjectsOf, ESTLEG.paragrahv),
    }
    for field in REQUIRED:
        shape = ESTLEG[f"ProvisionRequires{field}Shape"]
        assert {
            (p, o)
            for p, o in shapes.predicate_objects(shape)
            if p in (SH.targetClass, SH.targetSubjectsOf)
        } == targets
        assert shapes.value(shape, SH.message) is not None
