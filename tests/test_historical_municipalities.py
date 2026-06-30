"""Tests for the HistoricalMunicipality model — issue #130.

Covers:
  * ``build_historical_municipality_doc`` (the JSON-LD builder in
    ``enrich_kov_layer1.py``),
  * the new controlled-vocabulary terms,
  * the ``estleg:HistoricalMunicipalityShape`` SHACL shape,
  * regen-drift of the committed ``data/ehak/historical_municipalities.jsonld``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from enrich_kov_layer1 import (
    build_historical_municipality_doc,
    historical_municipality_iri,
)
from kov_registry import extract_historical_municipalities, load_municipalities

REPO_ROOT = Path(__file__).resolve().parent.parent
SHAPES = REPO_ROOT / "shacl" / "estonian_legal_shapes.ttl"
EHAK_DIR = REPO_ROOT / "data" / "ehak"
HISTORICAL_OUT = EHAK_DIR / "historical_municipalities.jsonld"
CONTROLLED_VOCAB = REPO_ROOT / "krr_outputs" / "controlled_vocabulary.jsonld"

CONTEXT = {
    "estleg": "https://w3id.org/estleg/",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dcterms": "http://purl.org/dc/terms/",
}


# A representative historical-municipality record (the in-memory shape
# `extract_historical_municipalities` produces).
def _hist_record(
    *,
    former_code: str = "0105",
    former_name: str = "Abja vald",
    municipality_type: str = "vald",
    succ_code: str = "0480",
    merged_at: str | None = "2017-10-15",
    evidence: str = (
        "Wikidata: Abja vald (EHAK 0105) -> Mulgi vald (EHAK 0480); "
        "per RT I 21.06.2017 1"
    ),
    issuer_slugs: list[str] | None = None,
) -> dict:
    return {
        "formerEhakCode": former_code,
        "formerName": former_name,
        "municipalityType": municipality_type,
        "succeededByCode": succ_code,
        "mergedAt": merged_at,
        "mergerEvidence": evidence,
        "issuerSlugs": issuer_slugs or ["abja_vallavolikogu"],
    }


class TestBuildHistoricalMunicipalityDoc:
    def test_builds_jsonld_doc_with_ontology_header(self):
        doc = build_historical_municipality_doc({"0105": _hist_record()})
        assert doc["@context"]["estleg"] == "https://w3id.org/estleg/"
        graph = doc["@graph"]
        # First node is the owl:Ontology header.
        assert graph[0]["@id"] == "estleg:HistoricalMunicipalities_Map_2026"
        assert "owl:Ontology" in graph[0]["@type"]

    def test_node_has_required_fields(self):
        doc = build_historical_municipality_doc({"0105": _hist_record()})
        node = next(
            n for n in doc["@graph"]
            if n["@id"] == "estleg:HistoricalMunicipality_0105"
        )
        assert "estleg:HistoricalMunicipality" in node["@type"]
        assert "owl:NamedIndividual" in node["@type"]
        assert node["rdfs:label"] == "Abja vald"
        assert node["estleg:formerEhakCode"] == "0105"
        assert node["estleg:formerName"] == "Abja vald"
        assert node["estleg:succeededBy"] == {"@id": "estleg:Municipality_EHAK_0480"}
        assert node["estleg:municipalityType"] == "vald"
        assert node["estleg:mergedAt"] == {"@value": "2017-10-15", "@type": "xsd:date"}
        assert "EHAK 0105" in node["estleg:mergerEvidence"]

    def test_iri_scheme(self):
        assert historical_municipality_iri("0105") == "estleg:HistoricalMunicipality_0105"

    def test_optional_fields_omitted_when_absent(self):
        rec = _hist_record(merged_at=None, evidence="", municipality_type="")
        doc = build_historical_municipality_doc({"0105": rec})
        node = next(
            n for n in doc["@graph"]
            if n["@id"] == "estleg:HistoricalMunicipality_0105"
        )
        assert "estleg:mergedAt" not in node
        assert "estleg:mergerEvidence" not in node
        assert "estleg:municipalityType" not in node
        # The always-present fields are still there.
        assert node["estleg:formerEhakCode"] == "0105"
        assert node["estleg:succeededBy"] == {"@id": "estleg:Municipality_EHAK_0480"}

    def test_issuer_slugs_not_serialised_onto_nodes(self):
        # issuerSlugs is coverage metadata only; it must not leak into
        # the JSON-LD (which would imply an Issuer-range predicate that
        # does not exist).
        doc = build_historical_municipality_doc({
            "0105": _hist_record(issuer_slugs=["a_vallavolikogu", "a_vallavalitsus"])
        })
        node = next(
            n for n in doc["@graph"]
            if n["@id"] == "estleg:HistoricalMunicipality_0105"
        )
        assert "estleg:enactedBy" not in node
        assert "issuerSlugs" not in node

    def test_nodes_emitted_in_ascending_former_code_order(self):
        doc = build_historical_municipality_doc({
            "0480": _hist_record(former_code="0480", former_name="Halliste vald",
                                 succ_code="0480"),
            "0105": _hist_record(former_code="0105"),
        })
        instance_ids = [
            n["@id"] for n in doc["@graph"]
            if n["@id"].startswith("estleg:HistoricalMunicipality_")
        ]
        assert instance_ids == [
            "estleg:HistoricalMunicipality_0105",
            "estleg:HistoricalMunicipality_0480",
        ]

    def test_succeededBy_targets_resolve_in_real_municipality_corpus(self):
        # Every estleg:succeededBy IRI emitted from the real registry
        # must correspond to a node that actually exists in the
        # municipality corpus (estleg:Municipality_EHAK_<code>).
        issuers = json.loads(
            (REPO_ROOT / "data" / "ehak" / "issuers.json").read_text(
                encoding="utf-8",
            )
        )
        muns = load_municipalities(EHAK_DIR / "municipalities.json")
        hist = extract_historical_municipalities(issuers, muns)
        doc = build_historical_municipality_doc(hist)
        # Build the set of municipality IRIs the same way enrich_kov
        # does (estleg:Municipality_EHAK_<code>).
        municipality_iris = {f"estleg:Municipality_EHAK_{c}" for c in muns}
        for node in doc["@graph"]:
            ref = node.get("estleg:succeededBy")
            if not ref:
                continue
            assert ref["@id"] in municipality_iris, (
                f"{node['@id']} succeededBy {ref['@id']} does not resolve "
                "to a real municipality node"
            )


class TestControlledVocabularyTerms:
    @pytest.fixture(scope="class")
    def vocab_ids(self) -> dict[str, dict]:
        doc = json.loads(CONTROLLED_VOCAB.read_text(encoding="utf-8"))
        return {n["@id"]: n for n in doc.get("@graph", []) if "@id" in n}

    def test_class_present(self, vocab_ids):
        node = vocab_ids.get("estleg:HistoricalMunicipality")
        assert node is not None, "estleg:HistoricalMunicipality not defined"
        assert "owl:Class" in node["@type"]
        # Deliberately NOT a subclass of estleg:Municipality (would pull
        # in the Municipality SHACL shape's IRI/ehakCode constraints).
        assert "rdfs:subClassOf" not in node
        assert node.get("rdfs:label")
        assert node.get("rdfs:comment")

    def test_datatype_properties_present(self, vocab_ids):
        for term, rng in (
            ("estleg:formerEhakCode", "xsd:string"),
            ("estleg:formerName", "xsd:string"),
            ("estleg:mergedAt", "xsd:date"),
            ("estleg:mergerEvidence", "xsd:string"),
            ("estleg:municipalityType", "xsd:string"),
        ):
            node = vocab_ids.get(term)
            assert node is not None, f"{term} not defined"
            assert "owl:DatatypeProperty" in node["@type"], term
            assert node["rdfs:range"] == {"@id": rng}, term
            assert node.get("rdfs:label") and node.get("rdfs:comment"), term

    def test_object_property_present(self, vocab_ids):
        node = vocab_ids.get("estleg:succeededBy")
        assert node is not None, "estleg:succeededBy not defined"
        assert "owl:ObjectProperty" in node["@type"]
        assert node["rdfs:range"] == {"@id": "estleg:Municipality"}
        assert node.get("rdfs:label") and node.get("rdfs:comment")


def _validate(graph_json: dict) -> tuple[bool, str]:
    pyshacl = pytest.importorskip("pyshacl")
    rdflib = pytest.importorskip("rdflib")
    g = rdflib.Graph()
    g.parse(data=json.dumps(graph_json), format="json-ld")
    shapes = rdflib.Graph().parse(str(SHAPES), format="turtle")
    conforms, _, msg = pyshacl.validate(g, shacl_graph=shapes, inference="rdfs")
    return conforms, str(msg)


def _well_formed_node(**overrides) -> dict:
    node = {
        "@context": CONTEXT,
        "@id": "estleg:HistoricalMunicipality_0105",
        "@type": ["owl:NamedIndividual", "estleg:HistoricalMunicipality"],
        "rdfs:label": "Abja vald",
        "estleg:formerEhakCode": "0105",
        "estleg:formerName": "Abja vald",
        "estleg:succeededBy": {"@id": "estleg:Municipality_EHAK_0480"},
        "estleg:municipalityType": "vald",
        "estleg:mergedAt": {"@value": "2017-10-15", "@type": "xsd:date"},
        "estleg:mergerEvidence": "Wikidata: Abja vald (EHAK 0105) -> Mulgi vald (EHAK 0480)",
    }
    node.update(overrides)
    return node


class TestHistoricalMunicipalityShape:
    def test_well_formed_node_passes(self):
        ok, msg = _validate(_well_formed_node())
        assert ok, msg

    def test_minimal_node_passes(self):
        # Only the required fields (no mergedAt / mergerEvidence /
        # municipalityType) — must still conform.
        node = {
            "@context": CONTEXT,
            "@id": "estleg:HistoricalMunicipality_0105",
            "@type": ["owl:NamedIndividual", "estleg:HistoricalMunicipality"],
            "rdfs:label": "Abja vald",
            "estleg:formerEhakCode": "0105",
            "estleg:formerName": "Abja vald",
            "estleg:succeededBy": {"@id": "estleg:Municipality_EHAK_0480"},
        }
        ok, msg = _validate(node)
        assert ok, msg

    def test_missing_former_ehak_code_fails(self):
        node = _well_formed_node()
        del node["estleg:formerEhakCode"]
        ok, _ = _validate(node)
        assert not ok

    def test_missing_former_name_fails(self):
        node = _well_formed_node()
        del node["estleg:formerName"]
        ok, _ = _validate(node)
        assert not ok

    def test_missing_succeeded_by_fails(self):
        node = _well_formed_node()
        del node["estleg:succeededBy"]
        ok, _ = _validate(node)
        assert not ok

    def test_non_four_digit_former_ehak_code_fails(self):
        ok, _ = _validate(_well_formed_node(**{"estleg:formerEhakCode": "105"}))
        assert not ok

    def test_out_of_enum_municipality_type_fails(self):
        ok, _ = _validate(_well_formed_node(**{"estleg:municipalityType": "alev"}))
        assert not ok

    def test_succeeded_by_literal_instead_of_iri_fails(self):
        ok, _ = _validate(_well_formed_node(**{"estleg:succeededBy": "0480"}))
        assert not ok

    def test_non_pattern_iri_fails(self):
        ok, _ = _validate(_well_formed_node(**{"@id": "estleg:HistoricalMunicipality_Abja"}))
        assert not ok

    def test_bad_merged_at_datatype_fails(self):
        ok, _ = _validate(_well_formed_node(**{
            "estleg:mergedAt": {"@value": "2017-10-15", "@type": "xsd:string"},
        }))
        assert not ok


class TestGeneratedFileIntegrity:
    """The committed ``data/ehak/historical_municipalities.jsonld`` must
    match what the builder produces from the current registry, and must
    be internally well-formed."""

    @pytest.fixture(scope="class")
    def committed_doc(self) -> dict:
        if not HISTORICAL_OUT.exists():
            pytest.fail(f"{HISTORICAL_OUT} not found — run enrich_kov_layer1.py")
        return json.loads(HISTORICAL_OUT.read_text(encoding="utf-8"))

    def test_matches_regenerated_doc(self, committed_doc):
        issuers = json.loads(
            (EHAK_DIR / "issuers.json").read_text(encoding="utf-8")
        )
        muns = load_municipalities(EHAK_DIR / "municipalities.json")
        hist = extract_historical_municipalities(issuers, muns)
        regenerated = build_historical_municipality_doc(hist)
        assert committed_doc == regenerated, (
            "data/ehak/historical_municipalities.jsonld is stale — "
            "re-run scripts/enrich_kov_layer1.py"
        )

    def test_one_node_per_distinct_former_code(self, committed_doc):
        instances = [
            n for n in committed_doc["@graph"]
            if "estleg:HistoricalMunicipality" in n.get("@type", [])
        ]
        codes = [n["estleg:formerEhakCode"] for n in instances]
        assert len(codes) == len(set(codes)), "duplicate former EHAK codes"
        assert len(instances) >= 130

    def test_ids_unique_and_match_former_code(self, committed_doc):
        ids = [n["@id"] for n in committed_doc["@graph"]]
        assert len(ids) == len(set(ids))
        for n in committed_doc["@graph"]:
            if "estleg:HistoricalMunicipality" in n.get("@type", []):
                assert n["@id"] == f"estleg:HistoricalMunicipality_{n['estleg:formerEhakCode']}"

    def test_no_self_succession(self, committed_doc):
        for n in committed_doc["@graph"]:
            if "estleg:HistoricalMunicipality" not in n.get("@type", []):
                continue
            succ = n["estleg:succeededBy"]["@id"]
            assert succ != f"estleg:Municipality_EHAK_{n['estleg:formerEhakCode']}"

    def test_uses_canonical_estleg_namespace(self, committed_doc):
        assert committed_doc["@context"]["estleg"] == "https://w3id.org/estleg/"
