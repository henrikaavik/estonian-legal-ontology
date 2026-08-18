"""SHACL two-surface inference policy — issue #455.

The bucket gate (scripts/shacl_validate_all.py) runs pyshacl with
inference="rdfs"; the Seadusloome sync gate
(scripts/validate_seadusloome_sync.py) uses inference="none". Shapes must
pass identically under both modes for cross-bucket stub objects.

Negative control (PR #400 phantom-typing class): if estleg:hasVersion
carried rdfs:range estleg:ProvisionVersion, RDFS inference would type a
bare hasVersion object as ProvisionVersion. ProvisionVersionShape would
then fire minCount on versionValidFrom / versionOf / versionText and the
two modes would diverge (rdfs fails, none passes). hasVersion
deliberately has no rdfs:range, so a LegalProvision pointing at an
untyped VersionStub must conform under both inference modes.
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


def _shapes_graph():
    rdflib = pytest.importorskip("rdflib")
    return rdflib.Graph().parse(str(SHAPES), format="turtle")


def _validate(graph_json: dict, *, inference: str) -> tuple[bool, str]:
    pyshacl = pytest.importorskip("pyshacl")
    rdflib = pytest.importorskip("rdflib")
    g = rdflib.Graph()
    g.parse(data=json.dumps(graph_json), format="json-ld")
    conforms, _, msg = pyshacl.validate(
        g, shacl_graph=_shapes_graph(), inference=inference
    )
    return conforms, str(msg)


def _provision_with_bare_version_stub() -> dict:
    return {
        "@context": CONTEXT,
        "@id": "estleg:TEST_Par_1",
        "@type": "estleg:LegalProvision",
        "estleg:paragrahv": "TEST § 1",
        "estleg:summary": "Fixture provision whose hasVersion target is an untyped stub.",
        "estleg:partOfAct": {"@id": "estleg:TEST_Map_2026"},
        "estleg:hasVersion": {"@id": "estleg:VersionStub"},
    }


class TestIssue455ShaclSurfaces:
    def test_hasversion_has_no_range_or_class_in_shapes(self):
        rdflib = pytest.importorskip("rdflib")
        g = _shapes_graph()
        estleg = rdflib.Namespace("https://w3id.org/estleg/")
        rdfs = rdflib.namespace.RDFS
        sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
        assert not list(g.objects(estleg.hasVersion, rdfs.range))
        for shape in g.subjects(sh.path, estleg.hasVersion):
            assert not list(g.objects(shape, sh["class"]))

    def test_bare_hasversion_stub_conforms_under_both_inference_modes(self):
        graph = _provision_with_bare_version_stub()
        rdfs_ok, rdfs_msg = _validate(graph, inference="rdfs")
        none_ok, none_msg = _validate(graph, inference="none")
        assert rdfs_ok == none_ok
        assert rdfs_ok, f"inference=rdfs:\n{rdfs_msg}\ninference=none:\n{none_msg}"
        assert "versionValidFrom" not in rdfs_msg
        assert "ProvisionVersionShape" not in rdfs_msg
