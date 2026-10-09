"""#703: the SHACL law-provenance shape for legal-text fidelity.

``estleg:LawActProvenanceShape`` lives in ``shacl/estonian_legal_shapes.ttl``,
with its canonical copy in ``tests/fixtures/text_fidelity_shapes.ttl``. It is
``sh:Warning`` and ships ``sh:deactivated``: it has current offenders, and both
SHACL gates fail on Warning results. The shrink-only Python baseline
(``check_text_fidelity.py --coverage``) enforces it until the baseline is
empty, at which point it may be activated.

There is no legalText shape. Joining a provision to its file head needs
``sh:sparql``, and the shapes stay core SHACL (shacl/README.md), so that rule
is enforced only by the Python baseline gate.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import rdflib
import rdflib.compare
from rdflib.namespace import RDF

from estleg import check_text_fidelity as ctf
from estleg.estleg_common import act_root_node, node_type_list

pyshacl = pytest.importorskip("pyshacl")

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "text_fidelity_shapes.ttl"
MAIN_SHAPES = REPO / "shacl" / "estonian_legal_shapes.ttl"
KRR = REPO / "krr_outputs"

SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
ESTLEG = rdflib.Namespace("https://w3id.org/estleg/")

LAW_SHAPE = ESTLEG.LawActProvenanceShape
TEXT_SHAPE = ESTLEG.StructuredBodyLegalTextShape  # must not exist (needs sh:sparql)
# Which coverage-baseline rules each shape mirrors.
SHAPE_RULES = {
    LAW_SHAPE: ("root_missing_source", "root_missing_kehtiv"),
}


def _shapes_graph() -> rdflib.Graph:
    main = rdflib.Graph().parse(MAIN_SHAPES, format="turtle")
    if (LAW_SHAPE, RDF.type, SH.NodeShape) in main:
        return main
    return rdflib.Graph().parse(FIXTURE, format="turtle")


def _activated(shapes: rdflib.Graph) -> rdflib.Graph:
    """Only the #703 shapes, with ``sh:deactivated`` removed."""
    out = rdflib.Graph()
    for shape in SHAPE_RULES:
        for triple in shapes.cbd(shape):
            out.add(triple)
        out.remove((shape, SH.deactivated, None))
    return out


def _validate(data: rdflib.Graph, shapes: rdflib.Graph, inference: str):
    _conforms, results, _text = pyshacl.validate(data, shacl_graph=shapes, inference=inference)
    rows = set()
    for result in results.subjects(RDF.type, SH.ValidationResult):
        rows.add(
            (
                str(results.value(result, SH.focusNode)),
                str(results.value(result, SH.resultPath) or ""),
                str(results.value(result, SH.resultSeverity)),
            )
        )
    return rows


DATA = """
@prefix estleg: <https://w3id.org/estleg/> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

estleg:Good_Map a estleg:Act, estleg:Law ;
    estleg:contentStatus "structuredBody" ;
    dcterms:source <https://www.riigiteataja.ee/akt/111.xml> ;
    estleg:kehtiv "2026-05-24"^^xsd:date .
estleg:Good_Par_1 a estleg:LegalProvision ; estleg:paragrahv "§ 1." ;
    estleg:partOfAct estleg:Good_Map ; estleg:legalText "(1) Tekst." .
estleg:Good_Par_2 a estleg:LegalProvision ; estleg:paragrahv "§ 2." ;
    estleg:partOfAct estleg:Good_Map .

estleg:Bare_Map a estleg:Act, estleg:Law ; estleg:contentStatus "noStructuredBody" .
estleg:Bare_Par_1 a estleg:LegalProvision ; estleg:paragrahv "§ 1." ;
    estleg:partOfAct estleg:Bare_Map .

estleg:Reg_1_Map a estleg:Act, estleg:Regulation ;
    estleg:contentStatus "structuredBody" ;
    dcterms:source <https://www.riigiteataja.ee/akt/222.xml> .
estleg:Reg_1_Par_1 a estleg:LegalProvision ; estleg:paragrahv "§ 1." ;
    estleg:partOfAct estleg:Reg_1_Map .
"""

WARNING = str(SH.Warning)
EXPECTED = {
    (str(ESTLEG.Bare_Map), "http://purl.org/dc/terms/source", WARNING),
    (str(ESTLEG.Bare_Map), str(ESTLEG.kehtiv), WARNING),
    # Good_Par_2 lacks legalText, but no SHACL shape covers that rule; and
    # regulations need no kehtiv (#703 scope trap).
}


@pytest.mark.parametrize("inference", ["none", "rdfs"])
def test_activated_shapes_report_only_expected_warnings(inference: str) -> None:
    data = rdflib.Graph().parse(data=DATA, format="turtle")
    assert _validate(data, _activated(_shapes_graph()), inference) == EXPECTED


def test_every_constraint_is_warning_severity() -> None:
    shapes = _shapes_graph()
    for shape in SHAPE_RULES:
        assert (shape, SH.severity, SH.Warning) in shapes
        for prop in shapes.objects(shape, SH.property):
            assert (prop, SH.severity, SH.Warning) in shapes, prop


def test_shapes_stay_core_shacl() -> None:
    """No legalText SPARQL shape: both inference surfaces must agree."""
    for shapes in (_shapes_graph(), rdflib.Graph().parse(FIXTURE, format="turtle")):
        assert (TEXT_SHAPE, RDF.type, SH.NodeShape) not in shapes
        assert not list(shapes.subject_objects(SH.sparql))


def test_law_shape_targets_laws_only() -> None:
    shapes = _shapes_graph()
    assert set(shapes.objects(LAW_SHAPE, SH.targetClass)) == {ESTLEG.Law}


@pytest.mark.committed
def test_shapes_stay_deactivated_while_baseline_has_offenders() -> None:
    """A shape with offenders would turn both SHACL gates red."""
    shapes = _shapes_graph()
    baseline = ctf.load_baseline()
    for shape, rules in SHAPE_RULES.items():
        offenders = sum(len(baseline[rule]) for rule in rules)
        deactivated = (shape, SH.deactivated, rdflib.Literal(True)) in shapes
        if offenders:
            assert deactivated, f"{shape} is active but the baseline lists {offenders} offender(s)"


def test_shipped_shapes_conform_on_offending_data() -> None:
    data = rdflib.Graph().parse(data=DATA, format="turtle")
    shapes = rdflib.Graph()
    for shape in SHAPE_RULES:
        for triple in _shapes_graph().cbd(shape):
            shapes.add(triple)
    assert _validate(data, shapes, "none") == set()


def test_fixture_matches_merged_copy() -> None:
    """Once merged into the main shapes file, the two copies must agree."""
    main = rdflib.Graph().parse(MAIN_SHAPES, format="turtle")
    fixture = rdflib.Graph().parse(FIXTURE, format="turtle")
    for shape in SHAPE_RULES:
        if (shape, RDF.type, SH.NodeShape) not in main:
            continue
        assert rdflib.compare.isomorphic(main.cbd(shape), fixture.cbd(shape)), shape


CORPUS_PEEPS = (
    "karistusregistri_seadus_peep.json",  # clean structured law
    "volaoigusseadus_map_peep.json",  # Law root without source/kehtiv
    "elamuseadus_peep.json",  # provisions without legalText
)


@pytest.mark.committed
@pytest.mark.parametrize("inference", ["none", "rdfs"])
def test_corpus_peeps_agree_with_python_baseline(tmp_path: Path, inference: str) -> None:
    """SHACL on three real law peeps + the vocabulary flags exactly the Law
    roots ``measure_coverage`` flags for provenance; legalText gaps are the
    Python gate's alone."""
    data = rdflib.Graph()
    for name in CORPUS_PEEPS:
        shutil.copy(KRR / name, tmp_path / name)
        data.parse(KRR / name, format="json-ld")
    data.parse(KRR / "controlled_vocabulary.jsonld", format="json-ld")

    measured = ctf.measure_coverage(tmp_path)
    laws = set()
    for name in CORPUS_PEEPS:
        head = act_root_node(json.loads((KRR / name).read_text(encoding="utf-8")))
        if "estleg:Law" in node_type_list(head):
            laws.add(head["@id"])

    def iri(curie: str) -> str:
        return str(ESTLEG) + curie.removeprefix("estleg:")

    assert measured["missing_legal_text"], "elamuseadus should still lack legalText"
    expected = {
        (iri(i), "http://purl.org/dc/terms/source", WARNING)
        for i in measured["root_missing_source"]
        if i in laws
    }
    expected |= {
        (iri(i), str(ESTLEG.kehtiv), WARNING) for i in measured["root_missing_kehtiv"] if i in laws
    }
    assert expected, "fixture peeps no longer contain offenders; pick new ones"
    assert _validate(data, _activated(_shapes_graph()), inference) == expected
