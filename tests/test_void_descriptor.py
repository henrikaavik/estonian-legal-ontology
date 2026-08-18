"""Standalone VoID + DCAT dataset descriptor (#517).

``krr_outputs/void.ttl`` is the federation-facing descriptor: a
``void:Dataset`` / ``dcat:Dataset`` with license, publisher, uriSpace,
example resource, data dump, and at least one ``void:Linkset``. The
compilation layer is CC BY 4.0; this file must not advertise the
repository MIT software licence as the dataset licence.
"""

from __future__ import annotations

from pathlib import Path

from rdflib import RDF, Graph, Literal, URIRef
from rdflib.namespace import DCAT, DCTERMS, VOID

REPO = Path(__file__).resolve().parent.parent
VOID_TTL = REPO / "krr_outputs" / "void.ttl"

DATASET = URIRef("https://w3id.org/estleg/dataset/estonian-legal-ontology")
ESTLEG_NS = "https://w3id.org/estleg/"
CC_BY_40 = URIRef("https://creativecommons.org/licenses/by/4.0/")
PUBLISHER = URIRef("https://github.com/henrikaavik")
EXAMPLE = URIRef(f"{ESTLEG_NS}KarS_Par_1")
MIT_LICENSE = "opensource.org/licenses/MIT"


def _graph() -> Graph:
    graph = Graph()
    graph.parse(VOID_TTL, format="turtle")
    return graph


def test_void_file_exists() -> None:
    assert VOID_TTL.is_file()


def test_void_parses_as_turtle() -> None:
    graph = _graph()
    assert len(graph) > 0


def test_dataset_iri_present_as_void_dataset() -> None:
    graph = _graph()
    assert (DATASET, RDF.type, VOID.Dataset) in graph
    assert (DATASET, RDF.type, DCAT.Dataset) in graph


def test_required_dataset_properties() -> None:
    graph = _graph()
    assert (DATASET, DCTERMS.license, CC_BY_40) in graph
    assert (DATASET, DCTERMS.publisher, PUBLISHER) in graph
    assert (DATASET, VOID.exampleResource, EXAMPLE) in graph
    assert (DATASET, VOID.uriSpace, Literal(ESTLEG_NS)) in graph


def test_has_linkset() -> None:
    graph = _graph()
    linksets = list(graph.subjects(RDF.type, VOID.Linkset))
    assert linksets, "void.ttl must declare at least one void:Linkset"


def test_no_mit_as_dataset_license() -> None:
    text = VOID_TTL.read_text(encoding="utf-8")
    assert MIT_LICENSE not in text
    graph = _graph()
    licenses = {str(obj) for obj in graph.objects(DATASET, DCTERMS.license)}
    assert not any(MIT_LICENSE in value for value in licenses)
