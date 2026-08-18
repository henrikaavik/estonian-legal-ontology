"""Smoke-test the new SHACL shapes against synthetic graphs.

Uses pyshacl. Build a tiny in-memory JSON-LD graph that should pass and
confirm the validator agrees, plus a graph missing a required field that
should fail.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHAPES = REPO_ROOT / "shacl" / "estonian_legal_shapes.ttl"


def _validate(graph_json: dict) -> tuple[bool, str]:
    pyshacl = pytest.importorskip("pyshacl")
    rdflib = pytest.importorskip("rdflib")
    g = rdflib.Graph()
    g.parse(data=json.dumps(graph_json), format="json-ld")
    shapes = rdflib.Graph().parse(str(SHAPES), format="turtle")
    conforms, _, msg = pyshacl.validate(g, shacl_graph=shapes, inference="rdfs")
    return conforms, str(msg)


CONTEXT = {
    "estleg": "https://w3id.org/estleg/",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}


class TestMunicipalityShape:
    def test_valid_passes(self):
        ok, msg = _validate({
            "@context": CONTEXT,
            "@id": "estleg:Municipality_EHAK_0784",
            "@type": "estleg:Municipality",
            "rdfs:label": "Tallinn",
            "estleg:ehakCode": "0784",
            "estleg:county": "Harju maakond",
        })
        assert ok, msg

    def test_missing_county_fails(self):
        ok, _ = _validate({
            "@context": CONTEXT,
            "@id": "estleg:Municipality_EHAK_0784",
            "@type": "estleg:Municipality",
            "estleg:ehakCode": "0784",
        })
        assert not ok

    def test_bad_iri_pattern_fails(self):
        ok, _ = _validate({
            "@context": CONTEXT,
            "@id": "estleg:Municipality_FOOBAR",  # not EHAK-coded
            "@type": "estleg:Municipality",
            "estleg:ehakCode": "0784",
            "estleg:county": "Harju maakond",
        })
        assert not ok


class TestIssuerShape:
    def test_valid_passes(self):
        ok, msg = _validate({
            "@context": CONTEXT,
            "@graph": [
                {
                    "@id": "estleg:Municipality_EHAK_0784",
                    "@type": "estleg:Municipality",
                    "rdfs:label": "Tallinn",
                    "estleg:ehakCode": "0784",
                    "estleg:county": "Harju maakond",
                },
                {
                    "@id": "estleg:Issuer_tallinna_linnavolikogu",
                    "@type": "estleg:Issuer",
                    "rdfs:label": "Tallinna Linnavolikogu",
                    "estleg:bodyType": "volikogu",
                    "estleg:currentMunicipality": {"@id": "estleg:Municipality_EHAK_0784"},
                    "estleg:mappingSource": "auto-match",
                },
            ],
        })
        assert ok, msg

    def test_missing_body_type_fails(self):
        ok, _ = _validate({
            "@context": CONTEXT,
            "@graph": [
                {
                    "@id": "estleg:Municipality_EHAK_0784",
                    "@type": "estleg:Municipality",
                    "rdfs:label": "Tallinn",
                    "estleg:ehakCode": "0784",
                    "estleg:county": "Harju maakond",
                },
                {
                    "@id": "estleg:Issuer_tallinna_linnavolikogu",
                    "@type": "estleg:Issuer",
                    "rdfs:label": "Tallinna Linnavolikogu",
                    "estleg:currentMunicipality": {"@id": "estleg:Municipality_EHAK_0784"},
                    "estleg:mappingSource": "auto-match",
                },
            ],
        })
        assert not ok


class TestKovProvisionShape:
    def test_missing_part_of_act_fails(self):
        ok, _ = _validate({
            "@context": CONTEXT,
            "@id": "estleg:Reg_1014955_Par_1",
            "@type": ["owl:NamedIndividual", "estleg:KovProvision"],
            "estleg:paragrahv": "§ 1.",
            "estleg:summary": "stub",
            "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavolikogu"},
            "estleg:enactedByMunicipality": {"@id": "estleg:Municipality_EHAK_0784"},
        })
        assert not ok


class TestEndToEndEnrichedKovGraph:
    """Validate a complete enriched KOV graph: Municipality + Issuer +
    MunicipalRegulation (typed Act) + KovProvision joined by partOfAct.

    Catches sh:class join failures that the negative-only tests miss —
    e.g. if `partOfAct` requires `sh:class estleg:Act` but the act node
    is only typed as `MunicipalRegulation`, this graph fails.
    """

    def test_full_enriched_graph_passes(self):
        ok, msg = _validate({
            "@context": CONTEXT,
            "@graph": [
                # Municipality
                {
                    "@id": "estleg:Municipality_EHAK_0784",
                    "@type": ["owl:NamedIndividual", "estleg:Municipality"],
                    "rdfs:label": "Tallinn",
                    "estleg:ehakCode": "0784",
                    "estleg:county": "Harju maakond",
                },
                # Issuer
                {
                    "@id": "estleg:Issuer_tallinna_linnavolikogu",
                    "@type": ["owl:NamedIndividual", "estleg:Issuer"],
                    "rdfs:label": "Tallinna Linnavolikogu",
                    "estleg:bodyType": "volikogu",
                    "estleg:currentMunicipality": {
                        "@id": "estleg:Municipality_EHAK_0784"
                    },
                    "estleg:mappingSource": "auto-match",
                },
                # MunicipalRegulation (act node, directly typed as Act)
                {
                    "@id": "estleg:Reg_1014955_Map_2026",
                    "@type": [
                        "owl:Ontology",
                        "estleg:Act",
                        "estleg:MunicipalRegulation",
                    ],
                    "rdfs:label": "Tallinna jäätmehoolduseeskiri",
                    "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavolikogu"},
                    "estleg:enactedByMunicipality": {
                        "@id": "estleg:Municipality_EHAK_0784"
                    },
                    "estleg:titleNormalized": "jaatmehoolduseeskiri",
                    # Required since NationalRegulationShape also targets
                    # MunicipalRegulation (#424 DomesticRegulation split).
                    "estleg:documentType": "määrus",
                    "estleg:terviktekstId": "1014955",
                },
                # KovProvision joined to act via partOfAct
                {
                    "@id": "estleg:Reg_1014955_Par_1",
                    "@type": [
                        "owl:NamedIndividual",
                        "estleg:KovProvision",
                    ],
                    "estleg:paragrahv": "§ 1.",
                    "estleg:summary": "stub",
                    "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavolikogu"},
                    "estleg:enactedByMunicipality": {
                        "@id": "estleg:Municipality_EHAK_0784"
                    },
                    "estleg:partOfAct": {"@id": "estleg:Reg_1014955_Map_2026"},
                },
            ],
        })
        assert ok, f"enriched graph failed SHACL: {msg}"


class TestCitationShape:
    def test_valid_citation_passes(self):
        # Minimal graph: Citation pointing to any IRI target. The shape
        # only enforces nodeKind sh:IRI on citationTarget — class-level
        # range is documented in metadata.jsonld via rdfs:range, not
        # SHACL (avoiding subclass-inference brittleness).
        ok, msg = _validate({
            "@context": CONTEXT,
            "@id": "estleg:Citation_1014955_Map_2026_1",
            "@type": ["owl:NamedIndividual", "estleg:Citation"],
            "estleg:citationTarget": {"@id": "estleg:Reg_1014955_Par_42"},
            "estleg:citationDetail": "lg 1",
            "estleg:citationText": "§ 42 lg 1 alusel",
        })
        assert ok, msg

    def test_citation_missing_target_fails(self):
        ok, _ = _validate({
            "@context": CONTEXT,
            "@id": "estleg:Citation_1014955_Map_2026_1",
            "@type": ["owl:NamedIndividual", "estleg:Citation"],
            "estleg:citationDetail": "lg 1",
        })
        assert not ok

    def test_citation_iri_pattern_enforced(self):
        # Provide a valid citationTarget so the failure can ONLY be the
        # pattern constraint (not the missing-target one).
        ok, _ = _validate({
            "@context": CONTEXT,
            "@id": "estleg:NotACitation",  # wrong IRI pattern
            "@type": ["owl:NamedIndividual", "estleg:Citation"],
            "estleg:citationTarget": {"@id": "estleg:Reg_1014955_Par_42"},
        })
        assert not ok

    def test_citation_target_must_be_iri(self):
        # citationTarget as a literal (not an IRI) must fail nodeKind.
        ok, _ = _validate({
            "@context": CONTEXT,
            "@id": "estleg:Citation_1014955_Map_2026_1",
            "@type": ["owl:NamedIndividual", "estleg:Citation"],
            "estleg:citationTarget": "literal-not-iri",
        })
        assert not ok
