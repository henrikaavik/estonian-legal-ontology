"""Tier 1 shape residues: three corpus values the shapes wrongly rejected.

* ``contentStatus "repealedBeforeSnapshot"`` on regulations repealed before the
  kehtiv snapshot (issue #374, ``generate_regulations``).
* ``institutionType "minister"``: ministers are not ministries (issue #457).
* ``ProvisionVersion`` with ``versionValidFrom == versionValidTo``: the end is the
  inclusive last day (day before the successor starts), so a one-day redaction is
  legal; only a reversed interval violates (issue #393).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pyshacl = pytest.importorskip("pyshacl")
rdflib = pytest.importorskip("rdflib")

SHAPES = Path(__file__).resolve().parent.parent / "shacl" / "estonian_legal_shapes.ttl"
SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
ESTLEG = rdflib.Namespace("https://w3id.org/estleg/")
PREFIXES = """
@prefix estleg: <https://w3id.org/estleg/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
"""


@pytest.fixture(scope="module")
def shapes():
    return rdflib.Graph().parse(str(SHAPES), format="turtle")


def _shape_name(shapes, shape) -> str:
    """A named shape by its local name; a property shape as ``OwningShape/path``."""
    if isinstance(shape, rdflib.URIRef):
        return str(shape).removeprefix(str(ESTLEG))
    owner = next(shapes.subjects(SH.property, shape))
    path = shapes.value(shape, SH.path)
    return f"{_shape_name(shapes, owner)}/{str(path).removeprefix(str(ESTLEG))}"


def _failing_shapes(shapes, turtle: str) -> set[str]:
    data = rdflib.Graph().parse(data=PREFIXES + turtle, format="turtle")
    _, report, _ = pyshacl.validate(data, shacl_graph=shapes, inference="none")
    results = report.subjects(rdflib.RDF.type, SH.ValidationResult)
    return {_shape_name(shapes, report.value(result, SH.sourceShape)) for result in results}


def _act(content_status: str) -> str:
    return f"""
estleg:TEST_Reg_Map a estleg:Act ;
    rdfs:label "Testmäärus" ;
    estleg:kehtiv "2025-01-01"^^xsd:date ;
    estleg:contentStatus "{content_status}" .
"""


def _institution(institution_type: str) -> str:
    return f"""
estleg:Institution_TEST a estleg:Institution ;
    rdfs:label "Testminister" ;
    estleg:institutionType "{institution_type}" .
"""


def _version(valid_from: str, valid_to: str | None) -> str:
    valid_to_triple = f'    estleg:versionValidTo "{valid_to}"^^xsd:date ;\n' if valid_to else ""
    return f"""
estleg:TEST_Par_1_v1 a estleg:ProvisionVersion ;
    estleg:versionOf estleg:TEST_Par_1 ;
    estleg:versionValidFrom "{valid_from}"^^xsd:date ;
{valid_to_triple}    estleg:versionText "Sätte tekst." .
"""


def test_act_repealed_before_snapshot_conforms(shapes):
    assert _failing_shapes(shapes, _act("repealedBeforeSnapshot")) == set()


def test_act_unknown_content_status_violates(shapes):
    assert _failing_shapes(shapes, _act("bogus")) == {"ActTemporalShape/contentStatus"}


def test_institution_minister_conforms(shapes):
    assert _failing_shapes(shapes, _institution("minister")) == set()


def test_institution_unknown_type_violates(shapes):
    assert _failing_shapes(shapes, _institution("bogus")) == {"InstitutionShape/institutionType"}


@pytest.mark.parametrize(
    ("valid_from", "valid_to"),
    [
        ("2024-03-01", "2024-03-01"),  # one-day redaction: inclusive end
        ("2024-03-01", "2024-06-30"),
        ("2024-03-01", None),  # open-ended current version
    ],
)
def test_provision_version_ordered_interval_conforms(shapes, valid_from, valid_to):
    assert _failing_shapes(shapes, _version(valid_from, valid_to)) == set()


def test_provision_version_reversed_interval_violates(shapes):
    assert _failing_shapes(shapes, _version("2024-03-02", "2024-03-01")) == {
        "ProvisionVersionShape/versionValidFrom"
    }
